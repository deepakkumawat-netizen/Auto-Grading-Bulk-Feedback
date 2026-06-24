"""AutoGrader — Bulk auto-grading & per-student feedback PDF generator."""
from __future__ import annotations

import csv
import io
import os
import sys
import asyncio
import zipfile
from typing import Any

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pypdf import PdfReader

from cbse_kb import get_subjects, get_chapters, retrieve_context
from grading_prompts import bulk_grader_prompt
from llm_router import grade_text, verify_grade, gemini_ocr, detect_scope, \
    cluster_misconceptions, make_study_plan, generate_rubric_from_questions
from pdf_writer import build_feedback_pdf
from nlp_polish import polish_feedback_dict
from agent_tools import verify_math
from transcript_export import transcripts_to_pdf, transcripts_to_docx
import rubric_store
import history_store


app = FastAPI(title="AutoGrader — Bulk auto-grading")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5181", "http://127.0.0.1:5181"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "tool": "AutoGrader",
        "groq_configured":   bool(os.getenv("GROQ_API_KEY", "").strip()),
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "groq_model":   os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    }


@app.get("/api/curriculum/{grade}")
def curriculum(grade: int):
    subjects = get_subjects(grade)
    return {"grade": grade, "subjects": {s: get_chapters(grade, s) for s in subjects}}


_MAX_ANSWER_CHARS = 30000  # ~7.5k tokens — fits comfortably in Groq's context

import re as _re
import pypdfium2 as _pdfium


_SOLVED_PAPER_PROMPT_TEMPLATE = (
    "These {n} images are pages of a CBSE student's answer sheet. "
    "Each page may have:\n"
    "  • PRINTED text — the question paper (questions, passages, mark allocations)\n"
    "  • HANDWRITTEN text — the student's answers, in pen, between or below questions\n\n"
    "🎯 PRIMARY TASK: Find every handwritten answer you can read and pair it with its question.\n\n"
    "Be thorough — scan every line of every page for handwriting. Cursive script is allowed; "
    "do your best to read it. Look BETWEEN printed lines, in MARGINS, on RULED HORIZONTAL LINES, "
    "and under each question prompt.\n\n"
    "Output format (one block per question that has handwritten content):\n"
    "  Q<number><sub-letter>. <one-sentence summary of the printed question>\n"
    "  Answer: <verbatim transcription of the handwritten answer, including spelling errors>\n\n"
    "Special rules:\n"
    "  - Include sub-questions like Q1(i), Q1(ii), Q2(a) as separate blocks.\n"
    "  - If handwriting has unclear words, write your best guess and mark with [?].\n"
    "  - Preserve the student's exact spelling, grammar mistakes, and word choice.\n"
    "  - SKIP questions where no handwriting is visible — don't fabricate.\n"
    "  - If you see the student's name written on the sheet, put 'Name: <name>' on the FIRST line.\n"
    "  - If multiple choice answers are circled/ticked, record as 'Answer: (B)' etc.\n"
    "  - DO NOT add commentary or summaries — pure extraction only.\n\n"
    "Plain text output. No markdown, no JSON, no code blocks."
)


def _render_pdf_to_pngs(raw: bytes, max_pages: int = 16, dpi: int = 150) -> list[bytes]:
    pdf = _pdfium.PdfDocument(raw)
    out: list[bytes] = []
    scale = dpi / 72
    try:
        for i, page in enumerate(pdf):
            if i >= max_pages: break
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG", optimize=True)
            out.append(buf.getvalue())
    finally:
        pdf.close()
    return out


def _try_gemini_unified(image_blobs: list[bytes], prompt: str) -> str:
    """Single Gemini Vision call with all pages. Raises QuotaExceeded on 429."""
    from google.genai import types as gtypes
    from llm_router import _gemini, _gemini_model_chain, _is_overloaded, _is_quota
    import time

    parts = [gtypes.Part.from_bytes(data=b, mime_type="image/png") for b in image_blobs]
    parts.append(prompt)

    last_err = None
    for model in _gemini_model_chain():
        for attempt in range(2):
            if attempt > 0: time.sleep(3)
            try:
                rsp = _gemini().models.generate_content(model=model, contents=parts)
                return (rsp.text or "").strip()
            except Exception as e:
                msg = str(e); last_err = e
                if _is_quota(msg):
                    raise QuotaExceeded("Gemini daily quota exhausted")
                if not _is_overloaded(msg):
                    raise
        print(f"[gemini-unified] {model} overloaded, falling through")
    raise RuntimeError(f"Gemini vision failed: {last_err}")


def _try_groq_chunked(image_blobs: list[bytes], prompt: str, chunk_size: int = 5) -> str:
    """Groq Llama 4 Scout vision in chunks of 5 pages, running chunks IN PARALLEL.
    Used when Gemini is exhausted. Parallel calls cut wall-time ~3× on multi-chunk papers."""
    from llm_router import groq_vision_ocr
    from concurrent.futures import ThreadPoolExecutor, as_completed
    chunks = [image_blobs[i:i + chunk_size] for i in range(0, len(image_blobs), chunk_size)]

    def _run_chunk(idx: int, chunk):
        chunk_prompt = prompt + f"\n\n(These are pages {idx*chunk_size + 1}–{idx*chunk_size + len(chunk)} of the answer sheet.)"
        try:
            return idx, groq_vision_ocr(chunk, chunk_prompt, mime="image/png")
        except Exception as e:
            return idx, f"[groq vision chunk {idx + 1} failed: {e}]"

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
        futures = [pool.submit(_run_chunk, i, c) for i, c in enumerate(chunks)]
        for f in as_completed(futures):
            idx, text = f.result()
            results[idx] = text

    ordered = [results[i] for i in sorted(results.keys()) if results[i] and not results[i].startswith("[")]
    return "\n\n".join(ordered).strip()


class QuotaExceeded(Exception):
    pass


_QUESTION_PAPER_PROMPT_TEMPLATE = (
    "These {n} images are pages of a CBSE QUESTION PAPER (printed, no student answers). "
    "Your job: transcribe the ENTIRE paper VERBATIM into plain text so a teacher can use it "
    "to build a marking rubric.\n\n"
    "Requirements:\n"
    "  • Preserve question numbers exactly (Q1, Q.2, 3., Section A, etc.)\n"
    "  • Preserve mark allocations exactly as they appear: [5 marks], (3), 5M, Marks: 5\n"
    "  • Include EVERY question across ALL pages — do not skip, summarise, or stop early\n"
    "  • Preserve section headers (Section A, B, C, D, E) and instructions\n"
    "  • Keep passages, comprehension blocks, and case-study text complete\n"
    "  • Plain text only — no markdown, no JSON, no code blocks, no commentary\n\n"
    "Output: the full question paper as one continuous plain-text document."
)


def _extract_question_paper_unified(raw: bytes, max_pages: int = 16, dpi: int = 150) -> str:
    """Vision OCR for a scanned QUESTION PAPER (no handwriting expected).
    Transcribes the full paper verbatim across all pages so we can build a rubric.
    """
    image_blobs = _render_pdf_to_pngs(raw, max_pages=max_pages, dpi=dpi)
    if not image_blobs:
        return ""
    prompt = _QUESTION_PAPER_PROMPT_TEMPLATE.format(n=len(image_blobs))

    try:
        result = _try_gemini_unified(image_blobs, prompt)
        if result and len(result.strip()) >= 50:
            print(f"[vision/qpaper] Gemini extracted {len(result)} chars")
            return result
    except QuotaExceeded:
        print("[vision/qpaper] Gemini quota exhausted — falling back to Groq")
    except Exception as e:
        print(f"[vision/qpaper] Gemini failed ({e}) — falling back to Groq")

    try:
        result = _try_groq_chunked(image_blobs, prompt)
        if result and len(result.strip()) >= 50:
            print(f"[vision/qpaper] Groq extracted {len(result)} chars")
            return result
    except Exception as e:
        print(f"[vision/qpaper] Groq also failed: {e}")
        raise

    return ""


def _extract_solved_paper_unified(raw: bytes, max_pages: int = 16, dpi: int = 150) -> str:
    """Multi-image Vision OCR for solved papers. Tries Gemini first; if Gemini's
    daily quota is exhausted, falls back to Groq Llama 4 Scout (in chunks of 5).
    """
    image_blobs = _render_pdf_to_pngs(raw, max_pages=max_pages, dpi=dpi)
    if not image_blobs:
        return ""
    prompt = _SOLVED_PAPER_PROMPT_TEMPLATE.format(n=len(image_blobs))

    # Primary: Gemini unified call
    try:
        result = _try_gemini_unified(image_blobs, prompt)
        if result and "Answer:" in result:
            print(f"[vision] Gemini extracted result ({len(result)} chars)")
            return result
    except QuotaExceeded:
        print("[vision] Gemini quota exhausted — falling back to Groq Llama 4 Scout vision")
    except Exception as e:
        print(f"[vision] Gemini failed ({e}) — falling back to Groq")

    # Fallback: Groq Llama 4 Scout vision
    try:
        result = _try_groq_chunked(image_blobs, prompt)
        if result and "Answer:" in result:
            print(f"[vision] Groq fallback extracted result ({len(result)} chars)")
            return result
        elif result:
            print(f"[vision] Groq returned {len(result)} chars but no 'Answer:' lines")
            return result
    except Exception as e:
        print(f"[vision] Groq fallback also failed: {e}")
        raise RuntimeError(f"Both Gemini and Groq vision failed. Last error: {e}")

    return ""

def looks_like_question_paper(text: str) -> tuple[bool, str]:
    """Heuristic: does this look like a question paper (no student answers)?
    Returns (is_paper, reason).

    We use DEFINITIVE exam-paper phrases as the strong signal, plus an
    "average post-Q content length" check. A student answer has long bodies
    after each Q marker; a question paper has short prompts.
    """
    if not text or len(text.strip()) < 30:
        return True, "Empty or near-empty file"

    t = text

    # 🟢 STRONG COUNTER-SIGNAL: structured 'Answer:' lines with real content =
    # this is an answer sheet (often Vision-extracted from a solved paper),
    # NOT a question paper. Short-circuit and let it through.
    answers_with_content = _re.findall(
        r"Answer\s*:\s*([^\[\n]{10,})", t, _re.IGNORECASE,
    )
    if len(answers_with_content) >= 2:
        return False, ""

    # Strong signal: phrases that only appear in question papers / exam papers
    paper_phrases = _re.findall(
        r"\b(?:maximum\s+marks|time\s+allowed|time\s*[:]\s*\d|"
        r"attempt\s+all\s+question|previous\s+year(?:'s)?\s+question|"
        r"general\s+instructions?|"
        r"read\s+the\s+(?:following\s+)?passage|"
        r"in\s+about\s+\d+\s+words|"
        r"write\s+(?:a\s+letter|an?\s+essay|a\s+paragraph)\s+(?:to|on|about))\b",
        t, _re.IGNORECASE,
    )

    # Bracketed mark allocations after Q markers (e.g. "Q3 [5 marks]" or "Q3 (5 marks)")
    bracketed_marks = _re.findall(r"[\[\(]\s*\d+\s*marks?\s*[\]\)]", t, _re.IGNORECASE)

    # Compute average length of text AFTER each Q-marker — long bodies = real answers
    chunks = _re.split(r"\bQ\s*\.?\s*\d+\.?\b|\bQuestion\s+\d+\b", t, flags=_re.IGNORECASE)
    bodies = [c.strip() for c in chunks[1:] if c.strip()]
    avg_body_len = (sum(len(b) for b in bodies) / len(bodies)) if bodies else 0
    q_count = len(bodies)

    # Strong signal A — explicit exam-paper preamble phrases (very specific)
    if len(paper_phrases) >= 1:
        return True, (f"Found exam-paper phrase: '{paper_phrases[0]}'. This looks like a "
                      "question paper, not a student answer.")

    # Strong signal B — many bracketed mark allocations + short bodies per question
    if len(bracketed_marks) >= 3 and avg_body_len < 100:
        return True, (f"{len(bracketed_marks)} bracketed mark allocations and only "
                      f"~{int(avg_body_len)} chars of text per question — looks like prompts, not answers.")

    # Strong signal C — lots of questions, every body is very short (just the question text)
    if q_count >= 4 and avg_body_len < 60:
        return True, (f"{q_count} question markers with only ~{int(avg_body_len)} chars per question. "
                      "No real answer content found.")

    return False, ""

def _read_pdf_text_fast(raw: bytes) -> str:
    """Pypdf-only PDF text extraction — fast, no Vision fallback.
    Used by /api/rubric/from-paper where we KNOW the input is a question paper."""
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n".join(pages).strip()
        if len(text) > _MAX_ANSWER_CHARS:
            text = text[:_MAX_ANSWER_CHARS] + f"\n\n[truncated]"
        return text
    except Exception as e:
        return f"[pdf extract failed: {e}]"


def _read_sheet(name: str, raw: bytes) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(raw))
            pages = [p.extract_text() or "" for p in reader.pages]
            text = "\n".join(pages).strip()

            # 🔍 If the pypdf text looks like a question paper (printed-only), the
            # PDF may actually be a SOLVED paper with handwritten answers that
            # pypdf can't see. Single unified Vision call across all pages —
            # faster than per-page AND better cross-page Q/A matching.
            is_paper, paper_reason = looks_like_question_paper(text)

            # ALWAYS run Vision OCR on question-paper-looking PDFs. The user wants
            # handwriting detection even on PDFs that look "digital" — a teacher may
            # have annotated the PDF with student answers using Adobe/Foxit, and those
            # annotations only appear when we render pages to images and Vision-OCR them.
            if is_paper and len(reader.pages) > 0:
                avg_per_page = len(text.strip()) / max(1, len(reader.pages))
                print(f"[_read_sheet] '{name}': pypdf yield {avg_per_page:.0f} chars/page. "
                      f"Running Vision OCR across {min(len(reader.pages), 16)} pages to "
                      "scan for any handwritten/annotated student answers…")
                try:
                    vision_text = _extract_solved_paper_unified(raw)
                    non_blank = 0
                    if vision_text and "Answer:" in vision_text:
                        non_blank = len(_re.findall(
                            r"Answer\s*:\s*(?!\s*\[BLANK\])[^\n]+", vision_text, _re.IGNORECASE))
                    print(f"[_read_sheet] '{name}': Vision extracted {non_blank} non-blank answers")
                    if non_blank >= 1:
                        if len(vision_text) > _MAX_ANSWER_CHARS:
                            vision_text = vision_text[:_MAX_ANSWER_CHARS] + "\n\n[truncated]"
                        return vision_text
                    # Vision ran but found NO handwritten answers → this is a pure
                    # question paper, not a solved sheet. Surface a clear rejection.
                    return (f"[question_paper_only] This PDF appears to be the question paper itself "
                            f"with NO handwritten student answers. Vision OCR scanned "
                            f"{min(len(reader.pages), 16)} pages and found 0 handwritten answers. "
                            "If you wanted to AUTO-GENERATE a rubric from this question paper, "
                            "use the '📑 Upload question paper' tab in Step 1 instead. "
                            "If this IS a solved paper, the handwriting may be too faint — "
                            "rescan at higher resolution and try again.")
                except Exception as e:
                    msg = str(e)
                    print(f"[_read_sheet] Both Gemini and Groq vision failed for '{name}': {msg}")
                    return (f"[vision_failed] Cannot read handwritten answers from this PDF. "
                            f"Both Gemini and Groq vision OCR failed: {msg[:200]}. Either wait for "
                            "quota to reset, or update API keys in backend/.env")

            if len(text) > _MAX_ANSWER_CHARS:
                text = text[:_MAX_ANSWER_CHARS] + f"\n\n[note: truncated — original answer was {len(text)} chars across {len(pages)} pages]"
            return text
        except Exception as e:
            return f"[pdf extract failed: {e}]"
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
        mime = "image/png" if lower.endswith(".png") else "image/jpeg"
        try:
            return gemini_ocr(raw, mime=mime)
        except Exception as e:
            return f"[ocr failed: {e}]"
    try:
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def pre_flight_check(text: str, filename: str, rubric: str) -> dict[str, Any]:
    """Battery of validators that runs BEFORE Groq is called. Catches the
    common 'something's off' cases so we don't burn quota grading garbage.
    Returns { errors[], warnings[], info[] } — each is a list of {code, message}.
    """
    errors, warnings, info = [], [], []

    if not text or len(text.strip()) < 30:
        errors.append({"code": "too_short",
                       "message": f"Text extracted is {len(text.strip())} chars — too short to grade. "
                                  "File may be empty, corrupted, or a blank scan."})
        return {"errors": errors, "warnings": warnings, "info": info}

    # 1. Question paper instead of answer sheet
    is_paper, reason = looks_like_question_paper(text)
    if is_paper:
        errors.append({"code": "question_paper", "message": reason})

    # 2. Suspiciously short answer
    if len(text.strip()) < 120:
        warnings.append({"code": "very_short",
                         "message": f"Answer is very short ({len(text.strip())} chars). "
                                    "Student may have left most questions blank."})

    # 3. Encoding garbage (control chars)
    import unicodedata
    bad_chars = sum(1 for c in text if c not in "\n\r\t" and unicodedata.category(c).startswith("C"))
    if bad_chars > len(text) * 0.15:
        errors.append({"code": "garbage_text",
                       "message": f"{bad_chars} non-printable chars in extracted text "
                                  f"({bad_chars * 100 // max(1, len(text))}%). File may be corrupted "
                                  "or scanned in too low quality."})

    # 4. Excessive length / padding
    if len(text) > 25000:
        warnings.append({"code": "very_long",
                         "message": f"Answer is unusually long ({len(text)} chars). "
                                    "Will be auto-truncated for grading."})

    # 5. Missing student name
    if not _re.search(r"\b(?:name|naam|नाम)\s*[:\-]", text, _re.IGNORECASE):
        info.append({"code": "no_name",
                     "message": "No 'Name:' field detected — student name may be missing from the sheet."})

    # 6. Question-count mismatch between rubric and answer
    rubric_qs = len(_re.findall(r"\bQ\s*\.?\s*\d+", rubric, _re.IGNORECASE))
    answer_qs = len(_re.findall(r"\bQ\s*\.?\s*\d+", text, _re.IGNORECASE))
    if rubric_qs >= 2 and answer_qs >= 1 and abs(rubric_qs - answer_qs) >= 2:
        warnings.append({"code": "question_count_mismatch",
                         "message": f"Rubric mentions {rubric_qs} questions but student answered "
                                    f"~{answer_qs}. They may have skipped some — check the sheet."})

    # 7. Possible AI-generated answer (very polished, very long, unusual vocab for grade)
    formal_ratio = len(_re.findall(
        r"\b(?:furthermore|moreover|consequently|nevertheless|subsequently|notwithstanding)\b",
        text, _re.IGNORECASE,
    ))
    if formal_ratio >= 3 and len(text) > 1500:
        info.append({"code": "ai_suspect",
                     "message": f"Detected {formal_ratio} formal connectors — answer reads polished. "
                                "Verifier-agent + ai_cheat_suspicion score will weigh in."})

    return {"errors": errors, "warnings": warnings, "info": info}


async def _grade_one(filename: str, raw: bytes, rubric: str, verify: bool) -> dict[str, Any]:
    answer_text = await asyncio.to_thread(_read_sheet, filename, raw)

    # Handle extraction-stage errors (never hard-fail on "looks like question paper" —
    # always try to grade; just attach a hint so the teacher can see what happened)
    extraction_hint = ""
    if not answer_text or (answer_text.startswith("[") and answer_text.startswith(
            ("[question_paper_only]", "[vision_failed]", "[pdf extract failed]",
             "[ocr failed]"))):
        if not answer_text:
            return {"file": filename, "ok": False, "rejected_code": "empty_file",
                    "error": "File appears to be empty", "extracted_text": ""}
        # Vision found NO handwriting OR vision failed entirely. Still attempt
        # to grade against the printed text we did extract — but flag that no
        # handwritten content was found.
        if answer_text.startswith("[question_paper_only]"):
            extraction_hint = ("⚠ No handwritten answers detected in this PDF — Vision OCR "
                               "scanned every page but found no student handwriting. The "
                               "grade below is against the printed text only.")
            # Use the printed pypdf text as the answer_text so grading still runs
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(raw))
                answer_text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
            except Exception:
                answer_text = ""
        elif answer_text.startswith("[vision_failed]"):
            return {"file": filename, "ok": False, "rejected_code": "vision_failed",
                    "error": answer_text[len("[vision_failed] "):], "extracted_text": ""}
        else:
            return {"file": filename, "ok": False, "rejected_code": "extract_failed",
                    "error": answer_text, "extracted_text": ""}

    # 🛡 Pre-flight checks — warnings + info only; we do NOT hard-reject on
    # "question paper" anymore. The user wants the tool to ATTEMPT grading on
    # any file they upload, even edge cases.
    checks = pre_flight_check(answer_text, filename, rubric)

    # 🎯 Auto-detect grade/subject/chapter from the RUBRIC + answer text. The
    # rubric (what the teacher wrote) is the strongest signal — an English Reading
    # Comprehension passage ABOUT renewable energy is still English, not Science.
    try:
        scope = await asyncio.to_thread(detect_scope, answer_text, rubric)
    except Exception as e:
        scope = {"grade": 8, "subject": "General", "chapter": "", "confidence": 0,
                 "reason": f"detect_scope failed: {e}"}

    grade_level = scope.get("grade", 8)
    subject     = scope.get("subject", "General")
    chapter     = scope.get("chapter", "")

    # 📚 NCERT grounding — local semantic RAG over NCERT chapter index.
    # Uses Chroma + sentence-transformers (all local, no API). Falls back to
    # the legacy token-overlap retriever if Chroma fails for any reason.
    ctx = ""
    try:
        if subject and (chapter or subject != "General"):
            from ncert_rag import rag_retrieve
            ctx = rag_retrieve(chapter or subject, grade_level, subject, top_k=2) or ""
            if not ctx:
                ctx = retrieve_context(chapter or subject, grade_level, subject, top_k=2) or ""
            if ctx:
                print(f"[grade] NCERT context retrieved for G{grade_level} {subject} '{chapter}' ({len(ctx)} chars)")
    except Exception as e:
        print(f"[grade] NCERT retrieval failed: {e}")

    system_prompt = bulk_grader_prompt(grade_level, subject, chapter, rubric, ctx)

    try:
        result = await asyncio.to_thread(grade_text, system_prompt, answer_text)
        # Use whatever scope the grader inferred (if present)
        inferred = result.get("detected_scope") or {}
        if inferred.get("grade"):
            scope.update({k: inferred[k] for k in ("grade", "subject", "chapter") if k in inferred})
            grade_level = scope["grade"]; subject = scope["subject"]; chapter = scope["chapter"]
        result["detected_scope"] = scope

        # 🔧 Math verifier is instant — do it now
        try:
            result["math_check"] = verify_math(answer_text)
        except Exception:
            pass

        # 🚀 PARALLELIZE verifier + study plan (both depend only on grade_text result,
        # are independent of each other). Saves ~3s per student vs sequential.
        post_tasks = []
        if verify:
            post_tasks.append(("verifier",
                asyncio.create_task(asyncio.to_thread(verify_grade, answer_text, rubric, result))))
        if result.get("mistakes"):
            post_tasks.append(("study_plan",
                asyncio.create_task(asyncio.to_thread(
                    make_study_plan, result, grade_level, subject, chapter))))
        for name, task in post_tasks:
            try:
                out = await task
                if name == "verifier":
                    result["verifier"] = out
                    if not out.get("agrees", True) and "suggested_marks" in out:
                        result["needs_review"] = True
                elif name == "study_plan":
                    result["study_plan"] = out
            except Exception as e:
                if name == "verifier":
                    result["verifier"] = {"agrees": True, "comment": f"verifier failed: {e}"}

        # NLP polish — grade-adaptive readability + vocab simplification for junior grades
        polish_feedback_dict(result, grade_level)
        out = {"file": filename, "ok": True, "extracted_text": answer_text,
               "grade_used": grade_level, "subject_used": subject, "chapter_used": chapter,
               "pre_flight": checks,
               **result}
        if extraction_hint:
            out["extraction_hint"] = extraction_hint
        if ctx:
            out["ncert_context_used"] = True
        return out
    except Exception as e:
        return {"file": filename, "ok": False, "error": str(e),
                "extracted_text": answer_text}


@app.post("/api/grade/bulk")
async def grade_bulk(
    rubric:  str  = Form(...),
    verify:  bool = Form(False),
    files:   list[UploadFile] = File(...),
):
    """Grade many answer sheets. AI auto-detects grade/subject/chapter per sheet —
    the teacher just provides a rubric and the files."""
    if not files: raise HTTPException(400, "No files uploaded")
    if not rubric.strip(): raise HTTPException(400, "Rubric is required")

    sem = asyncio.Semaphore(5)
    async def bounded(f: UploadFile):
        async with sem:
            raw = await f.read()
            return await _grade_one(f.filename or "untitled", raw, rubric, verify)

    results = await asyncio.gather(*(bounded(f) for f in files))
    graded = [r for r in results if r.get("ok")]
    total_pct = sum(r.get("percentage", 0) for r in graded) / len(graded) if graded else 0

    tally: dict[str, int] = {}
    for r in graded:
        for m in r.get("mistakes", []) or []:
            t = m.get("type", "other")
            tally[t] = tally.get(t, 0) + 1
    top = sorted(tally.items(), key=lambda kv: -kv[1])[:3]

    # 🧩 Misconception clustering (cross-student pattern detection)
    misconceptions = []
    if len(graded) >= 2:  # only worth clustering for class-sized batches
        try:
            mistakes_by_student = [
                {"student": r.get("student_name") or r.get("file","?"),
                 "mistakes": r.get("mistakes") or []}
                for r in graded if (r.get("mistakes") or [])
            ]
            if mistakes_by_student:
                misconceptions = await asyncio.to_thread(
                    cluster_misconceptions, mistakes_by_student)
        except Exception as e:
            print(f"[misconceptions] clustering failed: {e}")

    response = {
        "count": len(results), "graded": len(graded), "results": results,
        "class_analytics": {
            "average_percentage": round(total_pct, 1),
            "top_mistakes":       [{"type": k, "count": v} for k, v in top],
            "misconceptions":     misconceptions,
        },
    }

    # 🗂 Save to history (best-effort)
    try:
        subjects = sorted({(r.get("detected_scope") or {}).get("subject", "") for r in graded if r.get("detected_scope")})
        subjects_label = ", ".join(s for s in subjects if s)[:60] or "Mixed"
        title = f"{len(graded)} answer sheets — {subjects_label}"
        summary = (f"Class avg {response['class_analytics']['average_percentage']}% · "
                   f"Top mistake: {top[0][0] if top else '—'}")
        hid = await asyncio.to_thread(history_store.save_history, title, summary, rubric, response)
        response["history_id"] = hid
    except Exception as e:
        print(f"[history] save failed: {e}")

    return response


@app.post("/api/export/csv")
async def export_csv(payload: dict):
    rows = payload.get("results") or []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["File", "Student", "Marks", "Total", "%", "Top Mistake", "Suggestion"])
    for r in rows:
        if not r.get("ok"):
            w.writerow([r.get("file", ""), "", "", "", "", "ERROR", r.get("error", "")])
            continue
        top = (r.get("mistakes") or [{}])[0].get("type", "")
        w.writerow([r.get("file", ""), r.get("student_name", ""),
                    r.get("marks_awarded", ""), r.get("marks_total", ""),
                    r.get("percentage", ""), top, r.get("suggestion", "")])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=grades.csv"})


# ─── Auto-generate rubric from a question paper ─────────────────────────────
@app.post("/api/rubric/from-paper")
async def rubric_from_paper(paper: UploadFile = File(...)):
    """Teacher uploads a question paper (PDF / image / txt). Backend extracts
    the text, then asks Groq to produce a marking rubric. Returns:
        { rubric: str, questions_found: int, total_marks: int, extracted_text: str }
    """
    raw = await paper.read()
    if not raw:
        raise HTTPException(400, "Empty file")

    name_l = (paper.filename or "").lower()
    if name_l.endswith(".pdf"):
        # Try pypdf first (fast for text-layer PDFs)
        text = await asyncio.to_thread(_read_pdf_text_fast, raw)
        # If pypdf yielded too little — scanned PDF with no text layer — Vision OCR all pages
        if not text or text.startswith("[") or len(text.strip()) < 200:
            print(f"[rubric/from-paper] pypdf yielded {len(text.strip()) if text else 0} chars — "
                  f"falling back to unified Vision OCR for scanned PDF")
            try:
                vision_text = await asyncio.to_thread(
                    _extract_question_paper_unified, raw, 30, 200,
                )
                if vision_text and len(vision_text.strip()) >= 50:
                    text = vision_text
            except Exception as e:
                print(f"[rubric/from-paper] Vision OCR fallback failed: {e}")
    else:
        # Image or txt — go through full _read_sheet (uses Gemini OCR for images)
        text = await asyncio.to_thread(_read_sheet, paper.filename or "paper", raw)

    if not text or text.startswith("["):
        raise HTTPException(400, f"Could not read question paper: {text or 'no text extracted'}")
    if len(text.strip()) < 30:
        raise HTTPException(400, "Too little text extracted — paper may be too low resolution.")

    try:
        result = await asyncio.to_thread(generate_rubric_from_questions, text)
    except Exception as e:
        raise HTTPException(500, f"Rubric generation failed: {e}")
    result["extracted_text"] = text[:2000]
    return result


@app.post("/api/feedback/pdf")
async def feedback_pdf(payload: dict):
    result = payload.get("result") or {}
    meta   = payload.get("meta") or {}
    if not result: raise HTTPException(400, "Missing 'result'")
    pdf = await asyncio.to_thread(build_feedback_pdf, result, meta)
    student = result.get("student_name") or meta.get("file", "student")
    safe = "".join(c for c in student if c.isalnum() or c in " _-").strip() or "student"
    return StreamingResponse(iter([pdf]), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'})


@app.post("/api/feedback/zip")
async def feedback_zip(payload: dict):
    results = payload.get("results") or []
    meta    = payload.get("meta") or {}
    if not results: raise HTTPException(400, "No results")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, r in enumerate(results):
            if not r.get("ok"): continue
            file_meta = {**meta, "file": r.get("file", f"student_{i}")}
            try:
                pdf = await asyncio.to_thread(build_feedback_pdf, r, file_meta)
                student = r.get("student_name") or r.get("file", f"student_{i}")
                safe = "".join(c for c in student if c.isalnum() or c in " _-").strip() or f"student_{i}"
                zf.writestr(f"{safe}.pdf", pdf)
            except Exception as e:
                zf.writestr(f"_error_{i}.txt", f"Failed to build PDF: {e}")
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="feedback.zip"'})


# ─── Transcript export (PDF / DOCX) ─────────────────────────────────────────
@app.post("/api/transcript/{fmt}")
async def transcript_export(fmt: str, payload: dict):
    """Body: { items: [{text, meta:{title, student, file, grade, subject, chapter, marks, marks_total}}],
               filename?: str }.  Returns binary blob in `fmt` (pdf|docx)."""
    fmt = (fmt or "").lower()
    if fmt not in {"pdf", "docx"}:
        raise HTTPException(400, "Format must be pdf or docx")
    items = payload.get("items") or []
    if not items: raise HTTPException(400, "No items")
    filename = payload.get("filename") or f"transcripts.{fmt}"

    if fmt == "pdf":
        blob = await asyncio.to_thread(transcripts_to_pdf, items)
        mime = "application/pdf"
    else:
        blob = await asyncio.to_thread(transcripts_to_docx, items)
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return StreamingResponse(iter([blob]), media_type=mime,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ─── History (SQLite) ───────────────────────────────────────────────────────
@app.get("/api/history")
def history_list():
    return {"items": history_store.list_history(limit=50)}


@app.get("/api/history/{hid}")
def history_get(hid: int):
    h = history_store.get_history(hid)
    if not h:
        raise HTTPException(404, "Not found")
    return h


@app.delete("/api/history/{hid}")
def history_delete(hid: int):
    history_store.delete_history(hid)
    return {"ok": True}


@app.delete("/api/history")
def history_clear():
    history_store.clear_history()
    return {"ok": True}


class RubricIn(BaseModel):
    name: str
    grade: int
    subject: str
    chapter: str = ""
    rubric: str

@app.get("/api/rubric")
def rubric_list():
    return {"rubrics": rubric_store.list_rubrics()}

@app.post("/api/rubric")
def rubric_create(body: RubricIn):
    rid = rubric_store.save_rubric(body.name, body.grade, body.subject, body.chapter, body.rubric)
    return {"id": rid}

@app.get("/api/rubric/{rid}")
def rubric_get(rid: int):
    r = rubric_store.get_rubric(rid)
    if not r: raise HTTPException(404, "Not found")
    return r

@app.delete("/api/rubric/{rid}")
def rubric_delete(rid: int):
    rubric_store.delete_rubric(rid)
    return {"ok": True}
