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
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pypdf import PdfReader

from grading_prompts import bulk_grader_prompt, _sum_rubric_marks
from llm_router import grade_text, verify_grade, gemini_ocr, detect_scope, \
    cluster_misconceptions, make_study_plan, generate_rubric_from_questions, \
    extract_paper_metadata
from pdf_writer import build_feedback_pdf, build_rubric_pdf
from nlp_polish import polish_feedback_dict
from agent_tools import verify_math
from agent_features import insights_chat, generate_practice, generate_class_plan
from transcript_export import transcripts_to_pdf, transcripts_to_docx
import rubric_store
import history_store
import exam_config_store
import copy_check
from grading_prompts import build_exam_constraints
from grade_profiles import get_profile, build_tier_label


app = FastAPI(title="AutoGrader — Bulk auto-grading")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5181", "http://127.0.0.1:5181"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

from collections import OrderedDict
GRADING_PROGRESS = OrderedDict()
GRADING_RESULTS = OrderedDict()
RUBRIC_PROGRESS = OrderedDict()
RUBRIC_RESULTS = OrderedDict()

@app.get("/api/grade/progress/{session_id}")
def get_grading_progress(session_id: str):
    return GRADING_PROGRESS.get(session_id, {"total": 0, "completed": 0, "failed": 0, "files": {}})

@app.get("/api/grade/results/{session_id}")
def get_grading_results(session_id: str):
    if session_id not in GRADING_RESULTS:
        raise HTTPException(404, "Results not found or not ready yet.")
    return GRADING_RESULTS[session_id]

@app.get("/api/rubric/progress/{session_id}")
def get_rubric_progress(session_id: str):
    return RUBRIC_PROGRESS.get(session_id, {"status": "unknown", "error": None})

@app.get("/api/rubric/results/{session_id}")
def get_rubric_results(session_id: str):
    if session_id not in RUBRIC_RESULTS:
        raise HTTPException(404, "Results not found or not ready yet.")
    return RUBRIC_RESULTS[session_id]



# ── Exam Config endpoints ────────────────────────────────────────────────────

@app.get("/api/exam-config")
def list_exam_configs():
    return exam_config_store.list_configs()


@app.post("/api/exam-config")
async def create_exam_config(payload: dict):
    cid = exam_config_store.save_config(
        name=payload.get("name", "Untitled"),
        board=payload.get("board", "Board"),
        grade=payload.get("grade", 1),
        subject=payload.get("subject", ""),
        chapter=payload.get("chapter", ""),
        exam_type=payload.get("exam_type", ""),
        paper_total=payload.get("paper_total", 100),
        questions=payload.get("questions", []),
        instructions=payload.get("instructions", ""),
        eval_order=payload.get("eval_order", ""),
        strictness=payload.get("strictness", "moderate"),
        rules=payload.get("rules", {}),
        feedback=payload.get("feedback", {}),
    )
    return {"id": cid}


@app.get("/api/exam-config/{cid}")
def get_exam_config(cid: int):
    cfg = exam_config_store.get_config(cid)
    if not cfg:
        raise HTTPException(404, "Config not found")
    return cfg


@app.delete("/api/exam-config/{cid}")
def delete_exam_config(cid: int):
    exam_config_store.delete_config(cid)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "tool": "AutoGrader",
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
    }



_MAX_ANSWER_CHARS = 30000  # ~7.5k tokens — fits comfortably in Gemini's context

import re as _re
import pypdfium2 as _pdfium

# ─── Fast subject detection (no LLM call) ────────────────────────────────────
# Each entry: list of (keyword, weight) tuples.
# High-weight keywords are very specific to that subject.
# Low-weight words may appear in multiple subjects — they need more count to win.
_SUBJ_KW: dict[str, list[tuple[str, int]]] = {
    "Mathematics":    [
        # Name variants (highest weight — appear in paper title)
        ("mathematics", 10), ("maths standard", 10), ("maths basic", 10),
        ("math standard", 10), ("math basic", 10),
        # Unique math terms
        ("theorem", 4), ("quadratic", 4), ("polynomial", 4), ("trigonometry", 4),
        ("algebra", 4), ("geometry", 4), ("matrix", 4), ("determinant", 4),
        ("derivative", 4), ("integral", 4), ("probability", 3), ("statistics", 3),
        ("triangle", 3), ("circle", 3), ("arithmetic progression", 4),
        ("hcf", 4), ("lcm", 4), ("prime factorisation", 4),
        # Moderate — also appear in science word problems
        ("equation", 2), ("calculate", 2), ("solve", 2), ("proof", 2),
    ],
    "English":        [
        ("comprehension", 5), ("grammar", 5), ("letter writing", 5),
        ("analytical paragraph", 5), ("unseen passage", 5), ("gap filling", 5),
        ("antonym", 4), ("synonym", 4), ("idiom", 4), ("phrase", 3),
        ("passage", 2), ("tone", 2), ("inference", 2), ("poem", 2),
    ],
    "Science":        [
        ("photosynthesis", 5), ("respiration", 5), ("ecosystem", 5),
        ("heredity", 5), ("evolution", 5), ("reproduction", 5),
        ("chemical reaction", 5), ("atom", 4), ("molecule", 4),
        ("magnetic field", 4), ("electric circuit", 4),
        ("cell division", 4), ("natural selection", 4),
        # Lower weight — can appear in math word problems
        ("electricity", 2), ("magnetic", 2), ("light", 1), ("force", 1), ("motion", 1),
    ],
    "Physics":        [
        ("velocity", 4), ("acceleration", 4), ("momentum", 4),
        ("thermodynamics", 5), ("electric field", 5), ("optics", 5),
        ("resistance", 3), ("newton", 3), ("wave", 3), ("current", 2),
    ],
    "Chemistry":      [
        ("periodic table", 5), ("oxidation", 5), ("reduction", 5),
        ("organic chemistry", 5), ("carbon compound", 5), ("titration", 5),
        ("acid", 3), ("base", 3), ("salt", 3), ("mole", 3),
    ],
    "Biology":        [
        ("nervous system", 5), ("excretion", 5), ("genetics", 5), ("dna", 5),
        ("biodiversity", 5), ("hormone", 4),
        ("photosynthesis", 3), ("respiration", 3),
    ],
    "Social Science": [
        ("parliament", 4), ("constitution", 4), ("democracy", 4),
        ("nationalism", 4), ("colonialism", 4), ("globalisation", 4),
        ("amendment", 4), ("revolution", 3), ("monsoon", 3), ("biosphere", 3),
        ("development", 2),
    ],
    "Hindi":          [
        ("गद्य", 5), ("पद्य", 5), ("कविता", 5), ("निबंध", 5),
        ("व्याकरण", 5), ("समास", 5), ("संधि", 5), ("रस", 4), ("अलंकार", 4),
    ],
}

_ROMAN_TO_INT = {"I":1,"II":2,"III":3,"IV":4,"V":5,"VI":6,"VII":7,"VIII":8,
                 "IX":9,"X":10,"XI":11,"XII":12}

def _extract_grade_from_text(text: str) -> int | None:
    """Pull grade from any text that mentions Class/Grade. Handles Roman numerals
    (Class X, Class XII) and Arabic (Class 10, Grade 9). Returns None if not found."""
    # Normalize multiple spaces and newlines
    text_normalized = _re.sub(r'\s+', ' ', text[:3000])

    # Arabic first - most reliable
    m = _re.search(r'\b(?:grade|class|std\.?)\s*[:\-]?\s*(\d{1,2})\b', text_normalized, _re.IGNORECASE)
    if m:
        g = int(m.group(1))
        if 1 <= g <= 12:
            return g
    # Roman numerals
    m = _re.search(r'\b(?:grade|class|std\.?)\s*[:\-]?\s*(XII|XI|IX|VIII|VII|VI|IV|III|II|X|V|I)\b',
                   text_normalized, _re.IGNORECASE)
    if m:
        return _ROMAN_TO_INT.get(m.group(1).upper())
    
    # Standalone Roman numeral representing class (X, XII, IX) in header context
    m = _re.search(r'\b(XII|XI|X|IX)\b', text_normalized)
    if m:
        return _ROMAN_TO_INT.get(m.group(1).upper())
    return None


def _fast_scope(rubric: str, answer_text: str = "") -> dict[str, Any]:
    """Extract chapter/topic from rubric + answer text (no LLM call).
    Subject and Grade detection from files is disabled.
    """
    chapter = ""
    cm = _re.search(r'chapter\s*[:\-]?\s*(\d+|[a-z][^,\n]{0,40})', rubric, _re.IGNORECASE)
    if cm:
        chapter = cm.group(1).strip()

    return {"grade": 0, "subject": "", "chapter": chapter,
            "confidence": 100, "reason": "Chapter extracted from rubric"}


_SOLVED_PAPER_PROMPT_TEMPLATE = (
    "These {n} images are pages of a student's answer sheet. "
    "Each page may have PRINTED questions and HANDWRITTEN student answers.\n\n"
    "PRIMARY TASK: Find EVERY handwritten answer and pair it with its question number. "
    "Be thorough - scan every line, margin, and ruled space. Cursive script is fine.\n\n"
    "Output one block per question that has handwritten content:\n"
    "  Q<number><sub-letter>. <one-sentence summary of the printed question>\n"
    "  Answer: <full extraction below>\n\n"
    "=== HOW TO EXTRACT EACH ANSWER TYPE ===\n\n"
    "TEXT answers: Transcribe verbatim - keep spelling errors, grammar mistakes, "
    "abbreviations exactly as the student wrote them.\n\n"
    "DIAGRAMS, FIGURES, FLOWCHARTS, MIND-MAPS: If the student drew ANYTHING visual, "
    "you MUST capture it. Write:\n"
    "  [DIAGRAM: <one-line description of what is drawn>. "
    "Labels: <every word/arrow label/annotation written on the diagram>]\n"
    "  Example: [DIAGRAM: cross-section of a leaf. Labels: cuticle, upper epidermis, "
    "palisade cells, spongy mesophyll, lower epidermis, stomata, guard cell]\n"
    "  A diagram is a COMPLETE answer - do not skip it or treat it as a blank.\n\n"
    "TABLES / COMPARISON CHARTS: Use | separators to preserve columns:\n"
    "  | Column 1 | Column 2 | Column 3 |\n"
    "  | value    | value    | value    |\n\n"
    "MATHEMATICAL EXPRESSIONS / EQUATIONS / PROOFS: Write clearly with:\n"
    "  ^ for powers (x^2), * for multiplication, / for division, sqrt() for roots.\n"
    "  Show each working step on its own line. Example:\n"
    "    s = ut + (1/2)at^2\n"
    "    s = 0*(5) + (1/2)*(10)*(5^2)\n"
    "    s = 0 + 125 = 125 m\n\n"
    "NUMBERED or BULLETED LISTS: Keep the numbering/bullets exactly:\n"
    "  1. First point\n  2. Second point\n\n"
    "HINGLISH / MIXED LANGUAGE: If the student wrote Hindi words in English script "
    "or mixed Hindi and English freely, transcribe exactly as written - do NOT translate.\n\n"
    "SPECIAL RULES:\n"
    "  - Sub-questions Q1(i), Q1(ii), Q2(a) -> separate blocks each.\n"
    "  - Unclear words -> best guess with [?].\n"
    "  - MCQ circled/ticked -> 'Answer: (B)' etc.\n"
    "  - Student name on sheet -> 'Name: <name>' on FIRST line of output.\n"
    "  - SKIP only if truly blank - no handwriting at all.\n"
    "  - DO NOT fabricate, summarise, or add commentary.\n\n"
    "Plain text output. No markdown, no JSON, no code blocks."
)


def _render_pdf_to_pngs(raw: bytes, max_pages: int = 40, dpi: int = 110) -> list[bytes]:
    pdf = _pdfium.PdfDocument(raw)
    out: list[bytes] = []
    scale = dpi / 72
    try:
        for i, page in enumerate(pdf):
            if i >= max_pages: break
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil().convert("RGB")
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=60)
            out.append(buf.getvalue())
    finally:
        pdf.close()
    return out


def _try_gemini_unified(image_blobs: list[bytes], prompt: str) -> str:
    """Single Gemini Vision call with all pages. Raises QuotaExceeded on 429."""
    from google.genai import types as gtypes
    from llm_router import _gemini, _gemini_model_chain, _is_overloaded, _is_quota
    import time

    parts = [gtypes.Part.from_bytes(data=b, mime_type="image/jpeg") for b in image_blobs]
    parts.append(prompt)

    last_err = None
    for model in _gemini_model_chain():
        for attempt in range(2):
            if attempt > 0: time.sleep(3)
            try:
                rsp = _gemini().models.generate_content(
                    model=model, contents=parts,
                    config=gtypes.GenerateContentConfig(
                        thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
                        # Long answer sheets (15-20+ pages) need a large output budget —
                        # without this the SDK's default cap truncates the transcript
                        # mid-paper, silently dropping later questions as "not attempted".
                        max_output_tokens=65536,
                    ),
                )
                try:
                    finish_reason = rsp.candidates[0].finish_reason
                    if finish_reason and str(finish_reason).upper().find("MAX_TOKENS") != -1:
                        print(f"[gemini-unified] WARNING: response hit MAX_TOKENS for {len(image_blobs)} pages — "
                              "transcript may be truncated. Consider chunking this paper.")
                except Exception:
                    pass
                return (rsp.text or "").strip()
            except Exception as e:
                msg = str(e); last_err = e
                if _is_quota(msg):
                    from llm_router import _block_gemini
                    _block_gemini()
                    raise QuotaExceeded("Gemini daily quota exhausted")
                if not _is_overloaded(msg):
                    raise
        print(f"[gemini-unified] {model} overloaded, falling through")
    raise RuntimeError(f"Gemini vision failed: {last_err}")


class QuotaExceeded(Exception):
    pass


_QUESTION_PAPER_PROMPT_TEMPLATE = (
    "These {n} images are pages of a question paper (printed, no student answers). "
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


def _try_google_cloud_vision(image_blobs: list[bytes]) -> str:
    """Uses Google Cloud Vision API for visual OCR (supports handwriting and print).
    Requires 'google-cloud-vision' to be installed and GOOGLE_APPLICATION_CREDENTIALS in .env.
    """
    import os
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return ""
    try:
        from google.cloud import vision
    except ImportError:
        print("[gcv] google-cloud-vision library is not installed.")
        return ""

    print(f"[gcv] Sending {len(image_blobs)} pages to Google Cloud Vision API...")
    try:
        client = vision.ImageAnnotatorClient()
        results = []
        for idx, blob in enumerate(image_blobs):
            image = vision.Image(content=blob)
            response = client.document_text_detection(image=image)
            text = response.full_text_annotation.text.strip() if response.full_text_annotation else ""
            if text:
                results.append(text)
            if response.error.message:
                print(f"[gcv] Error on page {idx+1}: {response.error.message}")
        return "\n\n".join(results).strip()
    except Exception as e:
        print(f"[gcv] Failed to run Cloud Vision OCR: {e}")
        return ""


def _extract_question_paper_unified(raw: bytes, max_pages: int = 40, dpi: int = 110) -> str:
    """Vision OCR for a scanned QUESTION PAPER (no handwriting expected).
    Transcribes the full paper verbatim across all pages so we can build a rubric.
    """
    image_blobs = _render_pdf_to_pngs(raw, max_pages=max_pages, dpi=dpi)
    if not image_blobs:
        return ""

    # Primary: Google Cloud Vision OCR if configured
    gcv_text = _try_google_cloud_vision(image_blobs)
    if gcv_text and len(gcv_text.strip()) >= 50:
        print(f"[vision/qpaper] Google Cloud Vision OCR extracted {len(gcv_text)} chars")
        return gcv_text

    prompt = _QUESTION_PAPER_PROMPT_TEMPLATE.format(n=len(image_blobs))

    try:
        result = _try_gemini_unified(image_blobs, prompt)
        if result and len(result.strip()) >= 50:
            print(f"[vision/qpaper] Gemini extracted {len(result)} chars")
            return result
    except Exception as e:
        print(f"[vision/qpaper] Gemini failed ({e})")
        raise e

    return ""



def _extract_solved_paper_unified(raw: bytes, max_pages: int = 40, dpi: int = 110) -> str:
    """Multi-image Vision OCR for solved papers. Tries Google Cloud Vision first,
    then Gemini.
    """
    image_blobs = _render_pdf_to_pngs(raw, max_pages=max_pages, dpi=dpi)
    if not image_blobs:
        return ""

    # Primary: Google Cloud Vision OCR if configured
    gcv_text = _try_google_cloud_vision(image_blobs)
    if gcv_text and len(gcv_text.strip()) >= 50:
        print(f"[vision] Google Cloud Vision OCR extracted {len(gcv_text)} chars")
        return gcv_text

    prompt = _SOLVED_PAPER_PROMPT_TEMPLATE.format(n=len(image_blobs))

    # Primary: Gemini unified call
    try:
        result = _try_gemini_unified(image_blobs, prompt)
        if result and len(result.strip()) > 100:
            print(f"[vision] Gemini extracted result ({len(result)} chars)")
            return result
    except Exception as e:
        print(f"[vision] Gemini failed ({e})")
        raise RuntimeError(f"Gemini vision failed: {e}")

    return ""

def _de_space_text(text: str) -> str:
    """If the text has spaces between almost every letter (kerning/extraction glitch),
    merges characters separated by single spaces while keeping multiple spaces
    as word boundaries. Supports Indic and Latin scripts."""
    if not text:
        return text
    words = text.split()
    if not words:
        return text
    # Check average length of first 50 words to avoid scanning huge texts
    sample_words = words[:50]
    avg_len = sum(len(w) for w in sample_words) / len(sample_words)
    if avg_len < 2.0:
        lines = text.splitlines()
        new_lines = []
        for line in lines:
            parts = _re.split(r'\s{2,}', line)
            new_parts = [p.replace(' ', '') for p in parts]
            new_lines.append(' '.join(new_parts))
        return '\n'.join(new_lines)
    return text


def looks_like_question_paper(text: str) -> tuple[bool, str]:
    """Heuristic: does this look like a question paper (no student answers)?
    Returns (is_paper, reason).

    We use DEFINITIVE exam-paper phrases as the strong signal, plus an
    "average post-Q content length" check. A student answer has long bodies
    after each Q marker; a question paper has short prompts.
    """
    if not text or len(text.strip()) < 30:
        return True, "Empty or near-empty file"

    t = _de_space_text(text)

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

    # Check normalized text version (no spaces at all) for spacing-corrupted English terms
    norm = _re.sub(r'\s+', '', text).lower()
    for kw in ['maximummarks', 'timeallowed', 'generalinstruction', 'attemptallquestion']:
        if kw in norm:
            return True, f"Found normalized exam phrase: {kw}"

    return False, ""

def _read_pdf_text_fast(raw: bytes) -> str:
    """Pypdf-only PDF text extraction — fast, no Vision fallback.
    Converts PDF into a Markdown format by dividing pages with headers.
    Used by /api/rubric/from-paper where we KNOW the input is a question paper.
    Returns the FULL extracted text, untruncated — callers that need a length
    cap (e.g. after set-filtering a multi-set solution key) must truncate
    themselves, since truncating here would cut content before it's filtered."""
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = []
        for p in reader.pages:
            try:
                # Try layout mode first to preserve columns, tables, and alignment
                pages.append(p.extract_text(extraction_mode="layout") or "")
            except Exception:
                # Fallback to default extraction mode
                pages.append(p.extract_text() or "")
        markdown_pages = [f"## Page {i + 1}\n\n{text.strip()}" for i, text in enumerate(pages) if text.strip()]
        text = "\n\n".join(markdown_pages).strip()
        text = _de_space_text(text)
        return text
    except Exception as e:
        return f"[pdf extract failed: {e}]"


def _read_sheet_impl(name: str, raw: bytes) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(raw))
            pages = [p.extract_text() or "" for p in reader.pages]
            markdown_pages = [f"## Page {i + 1}\n\n{text.strip()}" for i, text in enumerate(pages) if text.strip()]
            text = "\n\n".join(markdown_pages).strip()
            text = _de_space_text(text)

            # Check for PUA corruption (Indic character encoding errors)
            pua_count = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0xFFFFD or 0x100000 <= ord(c) <= 0x10FFFD)
            has_pua_corruption = len(text) > 0 and (pua_count / len(text) > 0.05)

            # Check if the extracted text contains Devnagari (Hindi) characters
            devnagari_count = sum(1 for c in text if 0x0900 <= ord(c) <= 0x097F)
            has_hindi = devnagari_count > 50

            # 🔍 If the pypdf text looks like a question paper (printed-only), the
            # PDF may actually be a SOLVED paper with handwritten answers that
            # pypdf can't see. Single unified Vision call across all pages —
            # faster than per-page AND better cross-page Q/A matching.
            is_paper, paper_reason = looks_like_question_paper(text)

            # ALWAYS run Vision OCR on question-paper-looking, corrupted, or Hindi PDFs.
            if (is_paper or has_pua_corruption or has_hindi) and len(reader.pages) > 0:
                avg_per_page = len(text.strip()) / max(1, len(reader.pages))
                reason = "PUA encoding corruption" if has_pua_corruption else ("Devnagari (Hindi) detected" if has_hindi else "question paper heuristic match")
                print(f"[_read_sheet] '{name}': pypdf yield {avg_per_page:.0f} chars/page. "
                      f"Running Vision OCR (reason: {reason}) across {min(len(reader.pages), 40)} pages to "
                      "scan for any handwritten/annotated student answers…")
                try:
                    vision_text = _extract_solved_paper_unified(raw)
                    non_blank = 0
                    if vision_text and "Answer:" in vision_text:
                        non_blank = len(_re.findall(
                            r"Answer\s*:\s*(?!\s*\[BLANK\])[^\n]+", vision_text, _re.IGNORECASE))
                    print(f"[_read_sheet] '{name}': Vision extracted {non_blank} non-blank answers")
                    if non_blank >= 1 or (vision_text and len(vision_text.strip()) > 100):
                        # Return FULL text, untruncated — grading_graph applies the length
                        # cap only to the copy it sends to the grading LLM. Truncating here
                        # would cut content before copy_check's per-question split ever sees
                        # it, silently dropping late questions from plagiarism comparison.
                        return vision_text
                    # Vision ran but found NO handwritten answers → this is a pure
                    # question paper, not a solved sheet. Surface a clear rejection.
                    return (f"[question_paper_only] This PDF appears to be the question paper itself "
                            f"with NO handwritten student answers. Vision OCR scanned "
                            f"{min(len(reader.pages), 40)} pages and found 0 handwritten answers. "
                            "If you wanted to AUTO-GENERATE a rubric from this question paper, "
                            "use the '📑 Upload question paper' tab in Step 1 instead. "
                            "If this IS a solved paper, the handwriting may be too faint — "
                            "rescan at higher resolution and try again.")
                except Exception as e:
                    msg = str(e)
                    print(f"[_read_sheet] Vision OCR failed for '{name}': {msg}")
                    return (f"[vision_failed] Cannot read handwritten answers from this PDF. "
                            f"Vision OCR failed: {msg[:200]}. Either wait for "
                            "quota to reset, or update API keys in backend/.env")

            # Return FULL text, untruncated — see comment above. grading_graph
            # caps the LLM-facing copy after this; extracted_text (used by
            # copy_check's per-question split) stays untruncated.
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


def _read_sheet(name: str, raw: bytes) -> str:
    import hashlib
    file_hash = hashlib.sha256(raw).hexdigest()
    cache_dir = os.path.join(os.path.dirname(__file__), "data", "ocr_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{file_hash}.md")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                print(f"[ocr_cache] Cache HIT for file '{name}' (Hash: {file_hash[:10]})")
                return text
        except Exception as e:
            print(f"[ocr_cache] Failed to read cache: {e}")

    text = _read_sheet_impl(name, raw)

    # Cache successful extractions (skip errors)
    if text and not text.startswith(("[question_paper_only]", "[vision_failed]", "[pdf extract failed]", "[ocr failed]")):
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[ocr_cache] Cached new transcript for file '{name}' (Hash: {file_hash[:10]})")
        except Exception as e:
            print(f"[ocr_cache] Failed to write cache: {e}")

    return text


def pre_flight_check(text: str, filename: str, rubric: str) -> dict[str, Any]:
    """Battery of validators that runs before the grading call. Catches the
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

    # 6. Question-count mismatch between rubric and answer (unique question count)
    rubric_unique_qs = set(_re.findall(r"\bQ\s*\.?\s*(\d+)", rubric, _re.IGNORECASE))
    answer_unique_qs = set(_re.findall(r"\bQ\s*\.?\s*(\d+)", text, _re.IGNORECASE))
    rubric_qs = len(rubric_unique_qs)
    answer_qs = len(answer_unique_qs)
    if rubric_qs >= 2 and answer_qs >= 1 and abs(rubric_qs - answer_qs) >= 2:
        warnings.append({"code": "question_count_mismatch",
                         "message": f"Rubric has {rubric_qs} questions but student answered "
                                    f"~{answer_qs}. They may have skipped some — check the sheet."})

    # 7. Paper set mismatch detection (keyword topic mismatch per question number)
    # Build rubric map
    rubric_map = {}
    for line in rubric.splitlines():
        line = line.strip()
        m = _re.match(r'^[-*#\s]*Q(?:uestion)?\s*(\d+)', line, _re.IGNORECASE)
        if m:
            q_num = m.group(1)
            rubric_map[q_num] = rubric_map.get(q_num, "") + " " + line

    # Build answer map
    answer_map = {}
    current_q = None
    for line in text.splitlines():
        line = line.strip()
        m = _re.match(r'^[-*#\s]*Q(?:uestion)?\s*\.?\s*(\d+)', line, _re.IGNORECASE)
        if m:
            current_q = m.group(1)
        if current_q:
            answer_map[current_q] = answer_map.get(current_q, "") + " " + line

    def get_kw(s):
        words = _re.findall(r'\b[a-zA-Z]{4,}\b', s.lower())
        stopwords = {'question', 'marks', 'answer', 'student', 'correct', 'incorrect', 'option', 'should', 'would', 'could', 'about', 'their', 'there', 'given'}
        return {w for w in words if w not in stopwords}

    mismatches = []
    for q in rubric_map:
        if q in answer_map:
            kw_r = get_kw(rubric_map[q])
            kw_a = get_kw(answer_map[q])
            if len(kw_r) >= 3 and len(kw_a) >= 3:
                overlap = kw_r.intersection(kw_a)
                if not overlap:
                    mismatches.append((q, list(kw_r)[:3], list(kw_a)[:3]))

    if len(mismatches) >= 3:
        m_list = ", ".join(f"Q{q} ({'/'.join(r_s)} vs {'/'.join(a_s)})" for q, r_s, a_s in mismatches[:3])
        warnings.append({
            "code": "paper_set_mismatch",
            "message": f"Set Mismatch Warning: The student's answers do not seem to match this rubric. "
                       f"For example: {m_list}. Please verify if you uploaded the correct Question Paper set."
        })

    # 8. Possible AI-generated answer (very polished, very long, unusual vocab for grade)
    formal_ratio = len(_re.findall(
        r"\b(?:furthermore|moreover|consequently|nevertheless|subsequently|notwithstanding)\b",
        text, _re.IGNORECASE,
    ))
    if formal_ratio >= 3 and len(text) > 1500:
        info.append({"code": "ai_suspect",
                     "message": f"Detected {formal_ratio} formal connectors — answer reads polished. "
                                "Verifier-agent + ai_cheat_suspicion score will weigh in."})

    return {"errors": errors, "warnings": warnings, "info": info}


def _make_fallback_result(filename: str, error_msg: str, answer_text: str,
                          grade_level: int, subject: str, chapter: str,
                          rubric: str, exam_config: dict = None, checks: dict = None) -> dict[str, Any]:
    print(f"[grade] AutoGrader fallback applied for {filename}: {error_msg}")
    rubric_total = _sum_rubric_marks(rubric) or 80
    return {
        "file": filename,
        "ok": True,
        "student_name": f"Student ({filename})",
        "detected_language": "English",
        "detected_scope": {"grade": grade_level, "subject": subject, "chapter": chapter},
        "marks_awarded": 0,
        "marks_total": rubric_total,
        "percentage": 0.0,
        "answer_formats_used": ["text"],
        "per_question": [
            {
                "q": "Q1",
                "marks_awarded": 0,
                "marks_total": rubric_total,
                "feedback": f"Auto-grading fallback applied. Error: {error_msg}. Raw transcribed answers: {answer_text[:200] or 'No transcription available'}...",
                "format": "text"
            }
        ],
        "mistakes": [
            {"type": "conceptual", "description": f"Pipeline fallback: {error_msg}"}
        ],
        "strengths": ["Attempted paper"],
        "suggestion": f"Manual grading required. Pipeline error: {error_msg}",
        "ai_cheat_suspicion": 0,
        "extracted_text": answer_text,
        "grade_used": grade_level,
        "subject_used": subject,
        "chapter_used": chapter,
        "grade_tier": build_tier_label(grade_level),
        "exam_config_used": exam_config,
        "pre_flight": checks or {"errors": [], "warnings": [], "info": []},
        "verifier": {"agrees": True, "comment": "Verifier skipped due to fallback"},
        "study_plan": ["Review the topic manually"],
        "is_typed": False,
        "handwriting_clarity": 0,
        "handwriting_analysis": None,
        "grammar_spelling": None,
        "visual_elements": None,
        "homework_completeness": None,
        "category_scores": None,
        "effort_score": 0,
        "transcript": "",
        "cleaned_transcript": "",
        "steps": [],
        "first_mistake": None
    }

async def _grade_one(filename: str, raw: bytes, rubric: str, verify: bool,
                     declared_total: int = 0,
                     do_study_plan: bool = False,
                     grade_override: int = 0, subject_override: str = "",
                     exam_config: dict = None,
                     handwriting_audit: bool = False) -> dict[str, Any]:
    from grading_graph import grading_graph
    
    inputs = {
        "filename": filename,
        "raw_bytes": raw,
        "rubric": rubric,
        "verify": verify,
        "declared_total": declared_total,
        "do_study_plan": do_study_plan,
        "grade_override": grade_override,
        "subject_override": subject_override,
        "exam_config": exam_config,
        "handwriting_audit": handwriting_audit,
    }
    
    final_state = await grading_graph.ainvoke(inputs)
    return final_state["final_output"]


async def run_grade_bulk_in_background(
    session_id: str,
    files_data: list[tuple[str, bytes]],
    rubric: str,
    parsed_verify: bool,
    parsed_total_marks: int,
    parsed_study_plan: bool,
    eff_grade: int,
    eff_subject: str,
    resolved_config: dict | None,
    parsed_handwriting_audit: bool,
):
    # Gemini-only: default raised from the Groq-era 5 to 10. Each concurrently-graded
    # sheet issues a handful of Gemini calls (OCR + 1-3 grading chunks) over its grading
    # window; concurrency=10 keeps peak in-flight requests with real margin under Gemini's
    # RPM limits (even conservative Tier-1 estimates), while roughly halving bulk-batch wall time.
    sem = asyncio.Semaphore(max(1, int(os.getenv("GRADE_CONCURRENCY", "10"))))
    async def bounded(filename: str, raw: bytes):
        if session_id and session_id in GRADING_PROGRESS:
            GRADING_PROGRESS[session_id]["files"][filename] = "grading"
            GRADING_PROGRESS.move_to_end(session_id)
        async with sem:
            filename_lower = filename.lower()
            import re
            _MS_KEYWORDS = re.compile(
                r"[-_](ms|marking|solution|key|rubric|scheme)\b"
                r"|\b(answer[-_]key|solution[-_]key|marking[-_]scheme|question[-_]paper)\b",
                re.IGNORECASE
            )
            if _MS_KEYWORDS.search(filename_lower):
                if session_id and session_id in GRADING_PROGRESS:
                    GRADING_PROGRESS[session_id]["files"][filename] = "skipped"
                    GRADING_PROGRESS[session_id]["completed"] += 1
                    GRADING_PROGRESS.move_to_end(session_id)
                return {
                    "file": filename,
                    "ok": False,
                    "error": "Skipped: This file appears to be a marking scheme, answer key, or question paper.",
                    "student_name": "Skipped (Answer Key)",
                    "marks_awarded": 0,
                    "marks_total": parsed_total_marks or 80,
                    "percentage": 0,
                    "per_question": [],
                    "mistakes": [],
                    "strengths": [],
                    "suggestion": f"Ignored file '{filename}' because its name matches marking scheme patterns.",
                    "grade_used": eff_grade,
                    "subject_used": eff_subject,
                    "chapter_used": "",
                    "extracted_text": "[skipped]"
                }
            try:
                res = await _grade_one(filename, raw, rubric, parsed_verify,
                                        parsed_total_marks,
                                        do_study_plan=parsed_study_plan,
                                        grade_override=eff_grade,
                                        subject_override=eff_subject,
                                        exam_config=resolved_config,
                                        handwriting_audit=parsed_handwriting_audit)
                if session_id and session_id in GRADING_PROGRESS:
                    GRADING_PROGRESS[session_id]["files"][filename] = "completed"
                    GRADING_PROGRESS[session_id]["completed"] += 1
                    GRADING_PROGRESS.move_to_end(session_id)
                return res
            except Exception as e:
                import traceback
                traceback.print_exc()
                if session_id and session_id in GRADING_PROGRESS:
                    GRADING_PROGRESS[session_id]["files"][filename] = "failed"
                    GRADING_PROGRESS[session_id]["failed"] += 1
                    GRADING_PROGRESS.move_to_end(session_id)
                return {
                    "file": filename,
                    "ok": False,
                    "error": f"Error: {e}",
                    "student_name": "Error",
                    "marks_awarded": 0,
                    "marks_total": parsed_total_marks or 80,
                    "percentage": 0,
                    "per_question": [],
                    "mistakes": [],
                    "strengths": [],
                    "suggestion": f"Failed during grading: {e}",
                    "grade_used": eff_grade,
                    "subject_used": eff_subject,
                    "chapter_used": "",
                    "extracted_text": ""
                }

    results = await asyncio.gather(*(bounded(name, raw) for name, raw in files_data))
    graded = [r for r in results if r.get("ok")]
    total_pct = sum(min(r.get("percentage", 0), 100) for r in graded) / len(graded) if graded else 0

    tally: dict[str, int] = {}
    for r in graded:
        for m in r.get("mistakes", []) or []:
            t = m.get("type", "other")
            tally[t] = tally.get(t, 0) + 1
    top = sorted(tally.items(), key=lambda kv: -kv[1])[:3]

    # Misconception clustering (cross-student pattern detection)
    misconceptions = []
    if len(graded) >= 2:
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

    # Cross-student copy/plagiarism detection (pure-Python, no LLM call)
    copy_check_pairs = []
    if len(graded) >= 2:
        try:
            copy_check_pairs = await asyncio.to_thread(copy_check.detect_possible_copying, results)
        except Exception as e:
            print(f"[copy_check] detection failed: {e}")

    response = {
        "count": len(results), "graded": len(graded), "results": results,
        "class_analytics": {
            "average_percentage": round(total_pct, 1),
            "top_mistakes":       [{"type": k, "count": v} for k, v in top],
            "misconceptions":     misconceptions,
            "copy_check":         copy_check_pairs,
        },
    }

    # Save to history (best-effort)
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

    GRADING_RESULTS[session_id] = response


@app.post("/api/grade/bulk")
async def grade_bulk(
    background_tasks: BackgroundTasks,
    rubric:           str  = Form(...),
    verify:           Any  = Form(False),
    total_marks:      Any  = Form(0),
    study_plan:       Any  = Form(False),
    grade_override:   Any  = Form(None),
    subject_override: str  = Form(""),
    exam_config:      str  = Form(""),
    exam_config_id:   Any  = Form(None),
    session_id:       str  = Form(None),
    handwriting_audit: Any = Form(False),
    files:            list[UploadFile] = File(...),
):
    """Grade many answer sheets. Grade and subject must be manually selected by the teacher."""
    import json as _json
    if not files: raise HTTPException(400, "No files uploaded")
    if not rubric.strip(): raise HTTPException(400, "Rubric is required")

    # Safe parsing helpers
    def _bool(val) -> bool:
        if val is None: return False
        if isinstance(val, bool): return val
        return str(val).lower() in ("true", "1", "yes")

    def _int(val) -> int:
        if val is None or val == "": return 0
        try: return int(float(val))
        except Exception: return 0

    parsed_verify = _bool(verify)
    parsed_study_plan = _bool(study_plan)
    parsed_handwriting_audit = _bool(handwriting_audit)
    
    # Resolve exam config: inline JSON > saved DB record > none
    resolved_config = None
    if exam_config:
        try: resolved_config = _json.loads(exam_config)
        except Exception: pass
    elif exam_config_id:
        parsed_config_id = _int(exam_config_id)
        if parsed_config_id:
            resolved_config = exam_config_store.get_config(parsed_config_id)

    parsed_total_marks = _int(total_marks) or _int((resolved_config or {}).get("paper_total"))

    # Prefer grade/subject from exam config if not overridden by form fields
    eff_grade   = _int(grade_override) or _int((resolved_config or {}).get("grade"))
    eff_subject = subject_override or (resolved_config or {}).get("subject")

    if not eff_grade:
        raise HTTPException(400, "Class / Grade must be manually selected by the teacher.")
    if not eff_subject or not str(eff_subject).strip():
        raise HTTPException(400, "Subject must be manually selected by the teacher.")

    if not session_id:
        import uuid
        session_id = str(uuid.uuid4())

    GRADING_PROGRESS[session_id] = {
        "total": len(files),
        "completed": 0,
        "failed": 0,
        "files": {f.filename or "untitled": "queued" for f in files}
    }
    if len(GRADING_PROGRESS) > 100:
        GRADING_PROGRESS.popitem(last=False)

    # Read files bytes synchronously before request finishes
    files_data = []
    for f in files:
        raw = await f.read()
        files_data.append((f.filename or "untitled", raw))

    # Add task to background tasks
    background_tasks.add_task(
        run_grade_bulk_in_background,
        session_id,
        files_data,
        rubric,
        parsed_verify,
        parsed_total_marks,
        parsed_study_plan,
        eff_grade,
        eff_subject,
        resolved_config,
        parsed_handwriting_audit
    )

    return {"status": "started", "session_id": session_id}


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
async def _extract_doc_text_impl(filename: str, raw: bytes) -> str:
    name_l = filename.lower()
    if name_l.endswith(".pdf"):
        text = await asyncio.to_thread(_read_pdf_text_fast, raw)
        
        # Check for PUA corruption or loose/corrupted Hindi matras
        pua_count = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF or 0xF0000 <= ord(c) <= 0xFFFFD or 0x100000 <= ord(c) <= 0x10FFFD)
        matras = set("ािीुूृेैोौ")
        loose_matras = sum(1 for idx, c in enumerate(text) if c in matras and (idx == 0 or text[idx-1] in " \n\t" or idx == len(text)-1 or text[idx+1] in " \n\t"))
        has_pua_corruption = len(text) > 0 and (pua_count / len(text) > 0.05)
        has_hindi_corruption = len(text) > 0 and (loose_matras / len(text) > 0.15)

        # Check if the extracted text contains Devnagari (Hindi) characters
        devnagari_count = sum(1 for c in text if 0x0900 <= ord(c) <= 0x097F)
        has_hindi = devnagari_count > 50

        # Check for vertical text layout corruption (one or two characters per line)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        has_vertical_corruption = False
        if lines:
            short_lines = sum(1 for line in lines if len(line) <= 2)
            if short_lines / len(lines) > 0.3:
                has_vertical_corruption = True

        if not text or text.startswith("[") or len(text.strip()) < 200 or has_pua_corruption or has_hindi_corruption or has_hindi or has_vertical_corruption:
            if has_pua_corruption or has_hindi_corruption or has_hindi or has_vertical_corruption:
                reason = (
                    "PUA encoding corruption" if has_pua_corruption
                    else (f"corrupted Hindi matras ({loose_matras} standalone matras)" if has_hindi_corruption
                    else ("vertical layout corruption" if has_vertical_corruption else "Devnagari (Hindi) detected"))
                )
                print(f"[extract_doc_text] '{filename}' text extraction corrupted or layout vertical ({reason}). Falling back to Vision OCR...")
            try:
                # Extract declared maximum marks from fast PDF text before falling back to Vision OCR
                declared_total = None
                if text and not text.startswith("["):
                    import re
                    m = re.search(
                        r"(?:maximum\s*marks?|max\.?\s*marks?|m\.?\s*m\.?\s*|total\s*marks?|पूर्णांक|कुल\s*अंक|अधिकतम\s*अंक)\s*[:\-]?\s*(\d{2,3})\b",
                        text[:4000], re.IGNORECASE
                    )
                    if m:
                        declared_total = int(m.group(1))
                        print(f"[extract_doc_text] Pre-extracted total marks {declared_total} from PDF text.")

                vision_text = await asyncio.to_thread(
                    _extract_question_paper_unified, raw, 15, 120,
                )
                if vision_text and len(vision_text.strip()) >= 50:
                    if declared_total:
                        return f"Maximum Marks: {declared_total}\n\n{vision_text}"
                    return vision_text
            except Exception as e:
                print(f"[extract_doc_text] Vision OCR failed: {e}")
        return text
    elif name_l.endswith((".png", ".jpg", ".jpeg", ".webp")):
        mime = "image/png" if name_l.endswith(".png") else "image/jpeg"
        try:
            return await asyncio.to_thread(gemini_ocr, raw, mime=mime)
        except Exception as e:
            print(f"[extract_doc_text] OCR failed: {e}")
            return ""
    try:
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


async def _extract_doc_text(filename: str, raw: bytes) -> str:
    import hashlib
    file_hash = hashlib.sha256(raw).hexdigest()
    cache_dir = os.path.join(os.path.dirname(__file__), "data", "ocr_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{file_hash}.md")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                print(f"[ocr_cache/doc] Cache HIT for doc '{filename}' (Hash: {file_hash[:10]})")
                # Self-healing: if the file is a PDF and the cache does not start with prepended Maximum Marks,
                # check the PDF's text layer for any declared maximum marks and prepend it if found.
                if filename.lower().endswith(".pdf") and not text.startswith("Maximum Marks:"):
                    pdf_text = await asyncio.to_thread(_read_pdf_text_fast, raw)
                    if pdf_text and not pdf_text.startswith("["):
                        import re
                        m = re.search(
                            r"(?:maximum\s*marks?|max\.?\s*marks?|m\.?\s*m\.?\s*|total\s*marks?|पूर्णांक|कुल\s*अंक|अधिकतम\s*अंक)\s*[:\-]?\s*(\d{2,3})\b",
                            pdf_text[:4000], re.IGNORECASE
                        )
                        if m:
                            declared_total = int(m.group(1))
                            text = f"Maximum Marks: {declared_total}\n\n{text}"
                            try:
                                with open(cache_path, "w", encoding="utf-8") as f_out:
                                    f_out.write(text)
                                print(f"[ocr_cache/doc] Self-healed cache file: prepended Maximum Marks: {declared_total}")
                            except Exception as write_err:
                                print(f"[ocr_cache/doc] Failed to write self-healed cache: {write_err}")
                return text
        except Exception as e:
            print(f"[ocr_cache/doc] Failed to read cache: {e}")

    text = await _extract_doc_text_impl(filename, raw)

    # Cache successful extractions (skip errors)
    if text and not text.startswith(("[pdf extract failed]", "[ocr failed]", "[vision_failed]")):
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[ocr_cache/doc] Cached new transcript for doc '{filename}' (Hash: {file_hash[:10]})")
        except Exception as e:
            print(f"[ocr_cache/doc] Failed to write cache: {e}")

    return text


def _rubric_cache_path(paper_text: str, solution_text: str) -> str:
    """Rubric generation is a single LLM call, but re-uploading the exact same
    question paper + solution key (e.g. regenerating, or a teacher re-testing)
    used to re-spend that call every time even though the output would be
    identical. Cache the synthesized rubric by a hash of the two source texts
    so an exact repeat costs zero LLM calls; any different paper/solution
    (different subject, different set, even a single edited character) hashes
    differently and always regenerates fresh."""
    import hashlib
    key = hashlib.sha256((paper_text + "\n---\n" + solution_text).encode("utf-8")).hexdigest()
    cache_dir = os.path.join(os.path.dirname(__file__), "data", "rubric_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{key}.json")


async def run_generate_rubric_in_background(
    session_id: str,
    paper_text: str,
    solution_text: str,
):
    import json

    RUBRIC_PROGRESS[session_id] = {"status": "running", "error": None}
    RUBRIC_PROGRESS.move_to_end(session_id)
    try:
        cache_path = _rubric_cache_path(paper_text, solution_text)
        result = None
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
                print(f"[rubric_cache] Cache HIT for session {session_id} — skipping LLM call")
            except Exception as e:
                print(f"[rubric_cache] Failed to read cache: {e}")
                result = None

        if result is None:
            result = await asyncio.to_thread(generate_rubric_from_questions, paper_text, solution_text)
            if (result.get("rubric") or "").strip():
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False)
                    print(f"[rubric_cache] Cached new rubric for session {session_id}")
                except Exception as e:
                    print(f"[rubric_cache] Failed to write cache: {e}")

        # Validate result. If empty rubric, raise error.
        rubric_text = (result.get("rubric") or "").strip()
        if not rubric_text:
            raise ValueError(
                "Question paper was read successfully but no questions with marks could be extracted. "
                "Make sure the file is a question paper with question numbers and mark allocations."
            )

        result["extracted_text"] = paper_text[:2000]
        result["solution_text"] = solution_text
        RUBRIC_RESULTS[session_id] = result
        RUBRIC_PROGRESS[session_id] = {"status": "completed", "error": None}
        RUBRIC_PROGRESS.move_to_end(session_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        RUBRIC_PROGRESS[session_id] = {"status": "failed", "error": str(e)}
        RUBRIC_PROGRESS.move_to_end(session_id)


def filter_solution_by_set(paper_filename: str, paper_text: str, solution_text: str) -> str:
    """If the solution key contains multiple sets, slice it to match the set of the question paper."""
    import re
    
    # Try detecting set from the filename first - highly reliable for CBSE papers!
    detected_set = None
    series_prefix = None
    
    if paper_filename:
        # Match e.g. "31-5-1" -> Series 31/5, Set 1
        m = re.search(r'\b(\d+)[-_/](\d+)[-_/]([123])\b', paper_filename)
        if m:
            series_prefix = f"{m.group(1)}/{m.group(2)}"
            detected_set = m.group(3)
            print(f"[set_filter] Detected Series {series_prefix}, Set {detected_set} from filename '{paper_filename}'")
        else:
            # Match e.g. "31-S-1" -> Series 31/S, Set 1
            m_s = re.search(r'\b(\d+)[-_/]([sS])[-_/]([123])\b', paper_filename)
            if m_s:
                series_prefix = f"{m_s.group(1)}/{m_s.group(2).upper()}"
                detected_set = m_s.group(3)
                print(f"[set_filter] Detected Series {series_prefix}, Set {detected_set} from filename '{paper_filename}'")
                
    if not detected_set:
        # Fallback to paper_text detection
        paper_sets = re.findall(r'\b\d+/\d+/([123])\b|\b\d+/([123])-\d+\b|\bSeries\s*[A-Z0-9\-/]+\s*Set\s*([123])\b', paper_text, re.IGNORECASE)
        for p in paper_sets:
            val = p[0] or p[1] or p[2]
            if val:
                detected_set = val
                break
        if not detected_set:
            m = re.search(r'\bSet[- ]*([123])\b', paper_text, re.IGNORECASE)
            if m:
                detected_set = m.group(1)
        if detected_set:
            m_series = re.search(r'\b(\d+/\d+)/[123]\b', paper_text)
            if m_series:
                series_prefix = m_series.group(1)
                
    if not detected_set:
        print("[set_filter] No set detected in question paper filename or text. Slicing skipped.")
        return solution_text
        
    print(f"[set_filter] Final Detected Set: {detected_set}, Series: {series_prefix}")
    
    # Locate Set starts in the solution text
    lines = solution_text.splitlines()
    set_boundaries = {}
    
    for idx, line in enumerate(lines):
        if series_prefix:
            normalized_line = line.replace("-", "/")
            pattern = rf'{re.escape(series_prefix)}/([123])\b'
            m = re.search(pattern, normalized_line, re.IGNORECASE)
            if m:
                s_val = m.group(1)
                if s_val not in set_boundaries:
                    set_boundaries[s_val] = idx
                    
        m_gen = re.search(r'\b\d+/[5sS]/([123])\b', line.replace("-", "/"), re.IGNORECASE)
        if m_gen:
            s_val = m_gen.group(1)
            if s_val not in set_boundaries:
                set_boundaries[s_val] = idx
                
    if not set_boundaries:
        for idx, line in enumerate(lines):
            m_loose = re.search(r'\b31/[5sS]/([123])\b', line.replace("-", "/"), re.IGNORECASE)
            if m_loose:
                s_val = m_loose.group(1)
                if s_val not in set_boundaries:
                    set_boundaries[s_val] = idx

    if not set_boundaries:
        print("[set_filter] No set boundaries found in solution text. Slicing skipped.")
        return solution_text
        
    print(f"[set_filter] Found set boundaries in solution text: {set_boundaries}")
    sorted_boundaries = sorted(set_boundaries.items(), key=lambda x: x[1])
    
    start_idx = None
    end_idx = None
    for i, (s_val, line_idx) in enumerate(sorted_boundaries):
        if str(s_val) == str(detected_set):
            start_idx = line_idx
            if i + 1 < len(sorted_boundaries):
                end_idx = sorted_boundaries[i+1][1]
            break
            
    if start_idx is not None:
        sliced_lines = lines[start_idx:end_idx] if end_idx is not None else lines[start_idx:]
        sliced_text = "\n".join(sliced_lines)
        print(f"[set_filter] Sliced solution text to Set {detected_set} (lines {start_idx} to {end_idx or 'end'}).")
        return sliced_text
        
    return solution_text


@app.post("/api/rubric/from-paper")
async def rubric_from_paper(
    background_tasks: BackgroundTasks,
    paper:            UploadFile = File(...),
    solution:         UploadFile = File(None),
    session_id:       str        = Form(None),
):
    """Teacher uploads a question paper (PDF / image / txt) and, optionally, a
    solution/answer key. Backend extracts the text, then asks Gemini to produce
    a marking rubric. If no solution key is given, Gemini generates the
    expected answers itself from the question paper alone (auto_generated_answers
    will be true in the response so the teacher knows to double-check it). Returns:
        { rubric: str, questions_found: int, total_marks: int, extracted_text: str,
          auto_generated_answers: bool }
    """
    raw_paper = await paper.read()
    if not raw_paper:
        raise HTTPException(400, "Empty question paper file")

    paper_text = await _extract_doc_text(paper.filename or "paper", raw_paper)
    if not paper_text or paper_text.startswith("["):
        raise HTTPException(400, f"Could not read question paper: {paper_text or 'no text extracted'}")
    if len(paper_text.strip()) < 30:
        raise HTTPException(400, "Too little text extracted from question paper — paper may be too low resolution.")
    if len(paper_text) > _MAX_ANSWER_CHARS:
        paper_text = paper_text[:_MAX_ANSWER_CHARS] + "\n\n[truncated]"

    solution_text = ""
    if solution is not None:
        raw_sol = await solution.read()
        if raw_sol:
            solution_text = await _extract_doc_text(solution.filename or "solution", raw_sol)
            if not solution_text or solution_text.startswith("["):
                raise HTTPException(400, f"Could not read solution key: {solution_text or 'no text extracted'}")
            if len(solution_text.strip()) < 30:
                raise HTTPException(400, "Too little text extracted from solution key — key may be too low resolution.")
            # Slice solution key to match the question paper's set
            solution_text = filter_solution_by_set(paper.filename or "paper", paper_text, solution_text)
            # Strip the generic "General Instructions" preamble (boilerplate evaluator
            # instructions, identical across every CBSE marking scheme) before truncating —
            # it has zero rubric-relevant content but can eat ~7-8k chars of the char budget
            # before the real "MARKING SCHEME" / question content ever appears.
            marker_idx = solution_text.find("MARKING SCHEME")
            if marker_idx == -1:
                marker_idx = solution_text.lower().find("marking scheme")
            if marker_idx > 0:
                solution_text = solution_text[marker_idx:]
            # Truncate AFTER set-filtering so the cap applies to the relevant
            # set only, not to the full multi-set PDF (which could cut off
            # this set's later questions before filtering ever sees them).
            if len(solution_text) > _MAX_ANSWER_CHARS:
                solution_text = solution_text[:_MAX_ANSWER_CHARS] + "\n\n[truncated]"

    if not session_id:
        import uuid
        session_id = str(uuid.uuid4())

    RUBRIC_PROGRESS[session_id] = {"status": "pending", "error": None}
    if len(RUBRIC_PROGRESS) > 100:
        RUBRIC_PROGRESS.popitem(last=False)

    background_tasks.add_task(
        run_generate_rubric_in_background,
        session_id,
        paper_text,
        solution_text
    )

    return {"status": "started", "session_id": session_id}


@app.post("/api/rubric/pdf")
async def rubric_pdf(payload: dict):
    """Body: { rubric: str, meta?: {board, grade, subject, chapter, total_marks,
    questions_found} }. Renders the current rubric (question + expected answer +
    step-wise marking criteria, per question) as a downloadable PDF marking scheme."""
    rubric = (payload.get("rubric") or "").strip()
    meta   = payload.get("meta") or {}
    if not rubric:
        raise HTTPException(400, "Rubric is required")
    pdf = await asyncio.to_thread(build_rubric_pdf, rubric, meta)
    return StreamingResponse(iter([pdf]), media_type="application/pdf",
                             headers={"Content-Disposition": 'attachment; filename="rubric.pdf"'})


@app.post("/api/feedback/pdf")
async def feedback_pdf(payload: dict):
    import urllib.parse
    result = payload.get("result") or {}
    meta   = payload.get("meta") or {}
    if not result: raise HTTPException(400, "Missing 'result'")
    pdf = await asyncio.to_thread(build_feedback_pdf, result, meta)
    student = result.get("student_name") or meta.get("file", "student")
    
    # Filter non-printable/control chars first
    cleaned_student = "".join(c for c in student if c.isprintable())
    safe = "".join(c for c in cleaned_student if c.isalnum() or c in " _-").strip() or "student"
    
    # Strictly ASCII safe name for fallback
    ascii_safe = "".join(c for c in safe if ord(c) < 128).strip() or "student"
    encoded_safe = urllib.parse.quote(safe)
    
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_safe}.pdf"; filename*=UTF-8\'\'{encoded_safe}.pdf'
    }
    return StreamingResponse(iter([pdf]), media_type="application/pdf", headers=headers)


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
                cleaned_student = "".join(c for c in student if c.isprintable())
                safe = "".join(c for c in cleaned_student if c.isalnum() or c in " _-").strip() or f"student_{i}"
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
    grade: Any
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


# ─── Agentic AI endpoints ────────────────────────────────────────────────────

class ChatPayload(BaseModel):
    message: str
    results: list[dict]
    rubric:  str = ""
    history: list[dict] = []

@app.post("/api/agent/chat")
async def agent_chat(payload: ChatPayload):
    if not payload.message.strip():
        raise HTTPException(400, "message is required")
    try:
        reply = await asyncio.to_thread(
            insights_chat, payload.message, payload.results,
            payload.rubric, payload.history,
        )
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(500, f"Agent chat failed: {e}")


class PracticePayload(BaseModel):
    result:    dict
    grade:     Any  = 8
    subject:   str  = "General"
    chapter:   str  = ""
    count:     int  = 5

@app.post("/api/agent/practice")
async def agent_practice(payload: PracticePayload):
    try:
        questions = await asyncio.to_thread(
            generate_practice, payload.result, payload.grade,
            payload.subject, payload.chapter, min(payload.count, 10),
        )
        return {"questions": questions}
    except Exception as e:
        raise HTTPException(500, f"Practice generation failed: {e}")


class ClassPlanPayload(BaseModel):
    results: list[dict]
    rubric:  str = ""

@app.post("/api/agent/class-plan")
async def agent_class_plan(payload: ClassPlanPayload):
    if not payload.results:
        raise HTTPException(400, "results are required")
    try:
        plan = await asyncio.to_thread(
            generate_class_plan, payload.results, payload.rubric,
        )
        return plan
    except Exception as e:
        raise HTTPException(500, f"Class plan generation failed: {e}")
