"""AutoGrader LLM router.

- grade_text(...)   → Groq llama for typed/OCR'd answers
- verify_grade(...) → second Groq call (Verifier Agent)
- gemini_ocr(...)   → Gemini Flash vision OCR for scanned answer sheets
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import time as _time

from groq import Groq
from google import genai
from google.genai import types as gtypes


_groq_client: Groq | None = None
_gemini_client: genai.Client | None = None


_GROQ_TEXT_CHAIN = [
    "llama-3.3-70b-versatile",   # smartest, lowest TPM cap — primary
    "mixtral-8x7b-32768",        # comparable quality, separate TPM bucket
    "llama-3.1-8b-instant",      # last-resort: 8× higher TPM but may over-count marks
]


def _parse_retry_after(msg: str) -> float:
    """Groq's 429 errors include 'Please try again in 12.345s' — pull that out.
    Honor the server's hint up to 60s (one TPM window). Default 15s if no hint."""
    m = re.search(r"try again in ([\d.]+)\s*s", msg, re.IGNORECASE)
    if m:
        try: return min(60.0, max(2.0, float(m.group(1)) + 0.5))
        except Exception: pass
    return 15.0


def _gemini_text_fallback(messages, *, max_tokens: int, temperature: float = 0.2,
                           response_format=None) -> str:
    """Last-resort text fallback when ALL Groq models are rate-limited.
    Uses Gemini 2.5 Flash — 1M context, generous free tier, separate quota bucket.
    Converts Groq-style messages list into a single Gemini prompt."""
    sys_parts = [m["content"] for m in messages if m.get("role") == "system"]
    usr_parts = [m["content"] for m in messages if m.get("role") == "user"]
    prompt = ""
    if sys_parts:
        prompt += "SYSTEM:\n" + "\n".join(sys_parts) + "\n\n"
    prompt += "\n".join(usr_parts)

    want_json = bool(response_format and response_format.get("type") == "json_object")
    cfg_kwargs = dict(temperature=temperature, max_output_tokens=max_tokens)
    if want_json:
        cfg_kwargs["response_mime_type"] = "application/json"

    rsp = _gemini().models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[prompt],
        config=gtypes.GenerateContentConfig(**cfg_kwargs),
    )
    return rsp.text or ""


def _groq_chat_with_retry(model: str, messages, *, max_tokens: int,
                           temperature: float = 0.2,
                           response_format=None) -> str:
    """Run a Groq chat completion. On 429 rate-limit:
      - Retry up to 3× on the SAME (smartest) model using the exact wait time
        Groq returns in its error message
      - Fall through to other Groq models as a LAST resort (smaller models
        over-count CBSE marks — verify rubric carefully when this happens)
      - If ALL Groq models fail → fall back to Gemini 2.5 Flash (separate quota)"""
    chain = [model] + [m for m in _GROQ_TEXT_CHAIN if m != model]
    seen = set(); chain = [m for m in chain if not (m in seen or seen.add(m))]
    last_err = None
    for ci, m in enumerate(chain):
        max_attempts = 3 if ci == 0 else 1
        for attempt in range(max_attempts):
            try:
                kwargs = dict(model=m, messages=messages, temperature=temperature,
                              max_tokens=max_tokens)
                if response_format is not None:
                    kwargs["response_format"] = response_format
                rsp = _groq().chat.completions.create(**kwargs)
                if m != model:
                    print(f"[groq] WARN used fallback model {m} (primary {model} was rate-limited). "
                          "Marks may be over-counted — verify the rubric carefully.")
                return rsp.choices[0].message.content or ""
            except Exception as e:
                msg = str(e); last_err = e
                if ("429" in msg or "413" in msg or "rate limit" in msg.lower()
                        or "rate_limit" in msg.lower() or "tokens per minute" in msg.lower()
                        or "Request too large" in msg):
                    if attempt + 1 < max_attempts:
                        wait = _parse_retry_after(msg)
                        print(f"[groq {m}] rate-limited (attempt {attempt+1}/{max_attempts}), waiting {wait:.1f}s then retrying same model…")
                        _time.sleep(wait)
                        continue
                    print(f"[groq {m}] still rate-limited after {max_attempts} tries — falling to next model")
                    break
                else:
                    print(f"[groq {m}] failed with error ({e}) — falling to next model")
                    break
    # All Groq models exhausted — fall back to Gemini (separate quota bucket)
    print(f"[groq→gemini] All Groq models rate-limited. Falling back to Gemini 2.5 Flash. Last Groq err: {last_err}")
    try:
        return _gemini_text_fallback(messages, max_tokens=max_tokens,
                                      temperature=temperature,
                                      response_format=response_format)
    except Exception as ge:
        raise RuntimeError(
            f"Both Groq AND Gemini fallback failed. Groq: {last_err}. Gemini: {ge}"
        )


def _groq() -> Groq:
    global _groq_client
    if _groq_client is None:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key: raise RuntimeError("GROQ_API_KEY not set")
        _groq_client = Groq(api_key=key)
    return _groq_client


def _gemini() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key: raise RuntimeError("GEMINI_API_KEY not set")
        _gemini_client = genai.Client(api_key=key)
    return _gemini_client


def _extract_json(raw: str) -> dict[str, Any]:
    if not raw: raise ValueError("empty model output")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced: return json.loads(fenced.group(1))
    brace = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace: return json.loads(brace.group(0))
    return json.loads(raw)


_RUBRIC_CHUNK_CHARS = 6000  # ~1500 tokens — keeps output bounded


def _chunk_paper_by_questions(text: str, max_chars: int) -> list[str]:
    """Split a long question paper into chunks no larger than max_chars,
    keeping question boundaries intact where possible."""
    if len(text) <= max_chars:
        return [text]
    # Try to split on Section / Q markers so we don't cut a question in half
    boundaries = [0]
    for m in re.finditer(r"\n\s*(?:SECTION|Section|Q\.?\s*\d+|Question\s+\d+)\b", text):
        if m.start() - boundaries[-1] >= max_chars * 0.5:
            boundaries.append(m.start())
    boundaries.append(len(text))
    chunks, cur_start = [], 0
    for b in boundaries[1:]:
        if b - cur_start > max_chars:
            chunks.append(text[cur_start:cur_start + max_chars])
            cur_start = cur_start + max_chars
        if b - cur_start >= max_chars * 0.5 or b == len(text):
            chunks.append(text[cur_start:b])
            cur_start = b
    return [c for c in chunks if c.strip()]


def extract_paper_metadata(paper_text: str) -> dict[str, Any]:
    """Extract grade, subject, board, and total_marks from a CBSE question paper header.
    Uses regex — no LLM call needed. The paper header always has this info printed.

    Returns: { grade: int|None, subject: str, board: str, total_marks: int|None }
    """
    # Take first 800 chars — the header is always at the top
    header = paper_text[:800]

    # Grade: "Class X", "Class 10", "Std XII", "Grade 10"
    grade = None
    gm = re.search(
        r'(?:class|std\.?|grade)\s*[:\-]?\s*([IVXLCDM]+|\d{1,2})\b',
        header, re.IGNORECASE,
    )
    if gm:
        raw_g = gm.group(1).strip().upper()
        roman = {"I":1,"II":2,"III":3,"IV":4,"V":5,"VI":6,"VII":7,"VIII":8,
                 "IX":9,"X":10,"XI":11,"XII":12}
        grade = roman.get(raw_g) or (int(raw_g) if raw_g.isdigit() else None)
        if grade:
            grade = max(1, min(12, grade))

    # Subject: check paper text (first 2000 chars) — not just 800 — for more reliable detection.
    # Step 1: explicit "Subject: ..." label (most reliable)
    scan_zone = paper_text[:2000].lower()
    subject = ""
    sm = re.search(r'subject\s*[:\-]\s*([A-Za-z ()]{3,50})', paper_text[:2000], re.IGNORECASE)
    if sm:
        raw_sub = sm.group(1).strip().title()
        # Normalize CBSE variants like "Mathematics (Standard)" → "Mathematics"
        for canonical in ["Mathematics","Science","Physics","Chemistry","Biology",
                          "English","Hindi","Social Science","Sanskrit","Computer Science"]:
            if canonical.lower() in raw_sub.lower():
                subject = canonical
                break
        if not subject:
            subject = raw_sub

    if not subject:
        # Score-based keyword match across the top section of the paper.
        # Multi-word phrases checked before single words to avoid false positives.
        _SUBJ_SCORED = [
            ("Mathematics", [("mathematics standard",10),("mathematics basic",10),
                             ("mathematics",8),("maths",8),("math",6)]),
            ("Social Science", [("social science",8),("sst",6),("history",4),
                                ("geography",4),("civics",4)]),
            ("Computer Science", [("computer science",8),("informatics",6)]),
            ("Science",      [("general science",8),("science",6)]),
            ("Physics",      [("physics",8)]),
            ("Chemistry",    [("chemistry",8)]),
            ("Biology",      [("biology",8)]),
            ("English",      [("english",8)]),
            ("Hindi",        [("hindi",8)]),
            ("Sanskrit",     [("sanskrit",8)]),
        ]
        scores: dict[str, int] = {}
        for subj, kw_list in _SUBJ_SCORED:
            total = sum(w for kw, w in kw_list if kw in scan_zone)
            if total:
                scores[subj] = total
        if scores:
            subject = max(scores, key=scores.__getitem__)

    # Board
    board = ""
    if re.search(r'\bCBSE\b', header, re.IGNORECASE):
        board = "CBSE"
    elif re.search(r'\bICSE\b|\bISC\b', header, re.IGNORECASE):
        board = "ICSE"
    elif re.search(r'maharashtra|msbshse', header, re.IGNORECASE):
        board = "Maharashtra State Board"
    elif re.search(r'UP\s*board|UPMSP', header, re.IGNORECASE):
        board = "UP Board"

    # Total marks (scan full header section)
    total_marks = None
    tm = re.search(r'(?:maximum|total|max\.?)\s*marks?\s*[:\-]?\s*(\d{2,4})', paper_text[:2000], re.IGNORECASE)
    if tm:
        total_marks = int(tm.group(1))

    return {"grade": grade, "subject": subject, "board": board, "total_marks": total_marks}


def generate_rubric_from_questions(question_paper_text: str) -> dict[str, Any]:
    """Read a question paper (extracted text) and produce a teacher-ready rubric.

    🚀 Tries Gemini 2.5 Flash FIRST — 1M context, generous free tier, handles
    the full paper in a single fast call. Falls back to Groq (chunked) only
    if Gemini is unavailable.

    Returns { "rubric": str (multi-line), "questions_found": int, "total_marks": int }.
    """
    # Try Gemini first — fastest path for long papers
    try:
        out = _generate_rubric_gemini(question_paper_text)
        return _correct_total_marks(out, question_paper_text)
    except Exception as e:
        print(f"[rubric] Gemini path failed ({e}) — falling back to Groq chunked pipeline")

    # Fallback: Groq with chunking
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    chunks = _chunk_paper_by_questions(question_paper_text, _RUBRIC_CHUNK_CHARS)
    if len(chunks) > 1:
        print(f"[rubric] Groq fallback — splitting {len(question_paper_text)} chars "
              f"into {len(chunks)} chunks to fit TPM window.")
        return _generate_rubric_chunked(question_paper_text, chunks, model)
    paper = chunks[0]
    truncated_note = ""
    prompt = (
        "You are a CBSE senior examiner writing a DETAILED marking rubric from a question paper.\n\n"
        "🎯 TOTAL MARKS RULE:\n"
        "Find 'Maximum Marks : NN' at the top. Your `total_marks` MUST equal that value EXACTLY.\n\n"
        "🚫 ANTI-DOUBLE-COUNT: Sub-questions (Q1.1, Q1.2) already sum to the parent (Q1). "
        "List ONLY the sub-questions OR the parent — NEVER both.\n\n"
        f"{_RUBRIC_QUALITY_RULES}\n"
        "Output ONE line per question:\n"
        "    Q<num> (<X> marks): <detailed mark-point criteria>\n\n"
        "VERIFY: sum of marks in rubric must equal total_marks.\n\n"
        "Return STRICT JSON only:\n"
        '{ "rubric": string, "questions_found": int, "total_marks": int }\n\n'
        f"Question paper text:{truncated_note}\n\"\"\"\n{paper}\n\"\"\""
    )
    content = _groq_chat_with_retry(
        model,
        messages=[
            {"role": "system", "content": "You return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,
        max_tokens=3500,
        response_format={"type": "json_object"},
    )
    out = _extract_json(content)
    out["rubric"] = str(out.get("rubric", "")).strip()
    out["questions_found"] = int(out.get("questions_found") or 0)
    out["total_marks"] = int(out.get("total_marks") or 0)
    # Fallback: count Q-lines in rubric when model returns 0
    if out["questions_found"] == 0 and out["rubric"]:
        counted = len(re.findall(r"^\s*Q\d+[\.\(]", out["rubric"], re.MULTILINE))
        if counted:
            out["questions_found"] = counted
    out = _correct_total_marks(out, question_paper_text)
    # Always include paper metadata extracted from header
    meta = extract_paper_metadata(question_paper_text)
    out["paper_grade"]   = meta["grade"]
    out["paper_subject"] = meta["subject"]
    out["paper_board"]   = meta["board"]
    # If total_marks was corrected by declared value, meta may also have it
    if not out.get("total_marks") and meta.get("total_marks"):
        out["total_marks"] = meta["total_marks"]
    return out


_RUBRIC_QUALITY_RULES = """\
HOW TO WRITE EACH RUBRIC LINE — QUALITY IS CRITICAL:
=====================================================
A bad rubric line:  Q1 (5 marks): explain photosynthesis
A good rubric line: Q1 (5 marks): define photosynthesis (1m) + light/dark reactions named (1m) + role of chlorophyll (1m) + ATP/glucose as products (1m) + balanced equation or diagram with labels (1m)

Rules for EVERY question:
1. MARK-POINT BREAKDOWN: For questions ≥ 3 marks, split into individual mark points using (1m) notation.
   Example: Q3 (4 marks): two laws of motion stated correctly (2m) + one numerical example with working (1m) + SI units correct (1m)
2. SPECIFIC CONTENT: Name the actual terms, formulas, facts, examples, or steps the student must include.
   BAD: "explain the process"  GOOD: "steps: prophase → metaphase → anaphase → telophase named (4m)"
3. MCQ/OBJECTIVE: For 1-mark questions, state the correct answer directly.
   Q2 (1 mark): correct answer — photosynthesis occurs in chloroplast
4. DIAGRAMS: If a question asks for a diagram, specify: "labelled diagram with at least 4 parts named (Xm)"
5. LONG ANSWERS: List ALL the expected points, sub-headings, or steps. Do not summarise into one phrase.
6. COMPREHENSION PASSAGES: List the specific facts or inferences needed from the passage.
Do NOT write vague rubric lines — a vague rubric produces poor grading.
"""


def _generate_rubric_gemini(paper_text: str) -> dict[str, Any]:
    """Single-call rubric generation via Gemini 2.5 Flash (1M context, fast).
    No chunking needed — feeds the entire paper in one prompt."""
    prompt = (
        "You are a CBSE senior examiner writing a DETAILED marking rubric from this question paper.\n\n"
        "🎯 TOTAL MARKS RULE: Find 'Maximum Marks: NN' at the top — your total_marks "
        "MUST equal that value EXACTLY (could be 30, 50, 80, 100, 120, 150 — use the "
        "declared number, not a guess).\n\n"
        "🚫 ANTI-DOUBLE-COUNT: If you see 'Q1 [10 marks]' broken into 'Q1.1, Q1.2, …', "
        "list ONLY the sub-questions OR only the parent — never both.\n\n"
        f"{_RUBRIC_QUALITY_RULES}\n"
        "Output format — ONE line per question:\n"
        "    Q<num> (<X> marks): <detailed mark-point-by-mark-point criteria>\n\n"
        "Cover ALL questions across ALL sections (A, B, C, D, E). Do not skip any.\n\n"
        "Return STRICT JSON only:\n"
        '{ "rubric": "multi-line string", "questions_found": int, "total_marks": int }\n\n'
        f"Question paper text:\n\"\"\"\n{paper_text[:200000]}\n\"\"\""
    )
    rsp = _gemini().models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[prompt],
        config=gtypes.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=32768,
        ),
    )
    raw = rsp.text or ""
    try:
        out = _extract_json(raw)
    except Exception:
        # Truncated JSON — salvage what we can by extracting the rubric field
        # via regex even if JSON parsing fails
        rubric_m = re.search(r'"rubric"\s*:\s*"((?:[^"\\]|\\.)*)', raw, re.DOTALL)
        total_m = re.search(r'"total_marks"\s*:\s*(\d+)', raw)
        qf_m = re.search(r'"questions_found"\s*:\s*(\d+)', raw)
        if rubric_m:
            rubric_raw = rubric_m.group(1)
            # Unescape \n, \t — avoid full unicode_escape which breaks on non-ASCII
            rubric_text = rubric_raw.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
            out = {
                "rubric": rubric_text,
                "questions_found": int(qf_m.group(1)) if qf_m else 0,
                "total_marks": int(total_m.group(1)) if total_m else 0,
            }
        else:
            raise
    out["rubric"] = str(out.get("rubric", "")).strip()
    out["questions_found"] = int(out.get("questions_found") or 0)
    out["total_marks"] = int(out.get("total_marks") or 0)
    # Fallback: if model said 0 questions but rubric has Q-lines, count them
    if out["questions_found"] == 0 and out["rubric"]:
        counted = len(re.findall(r"^\s*Q\d+[\.\(]", out["rubric"], re.MULTILINE))
        if counted:
            out["questions_found"] = counted
    return out


def _correct_total_marks(out: dict[str, Any], paper_text: str) -> dict[str, Any]:
    """If the paper declares a Maximum/Total Marks value and the model returned a
    different total, trust the declared value. Supports any value 10-500."""
    declared_match = re.search(
        r"(?:Maximum|Total)\s*Marks?\s*[:\-]?\s*(\d{2,4})\b",
        paper_text, re.IGNORECASE,
    )
    if declared_match:
        declared_total = int(declared_match.group(1))
        if 10 <= declared_total <= 500 and out.get("total_marks") != declared_total:
            model_said = int(out.get("total_marks") or 0)
            print(f"[rubric] Total mismatch: model said {model_said}, paper declared "
                  f"{declared_total}. Trusting the paper's declared value.")
            out["total_marks"] = declared_total
            out["_total_marks_corrected"] = True
            out["_total_marks_model_said"] = model_said
    return out


def _generate_rubric_chunked(full_text: str, chunks: list[str], model: str) -> dict[str, Any]:
    """Generate a rubric for a long paper by processing one chunk at a time
    (with a small wait between chunks to respect Groq's TPM rate window)."""
    all_lines: list[str] = []
    total_q, total_marks = 0, 0
    for i, chunk in enumerate(chunks):
        chunk_prompt = (
            f"You are reading PART {i+1}/{len(chunks)} of a multi-page CBSE question paper.\n\n"
            "Write a DETAILED marking rubric for every question in this part.\n\n"
            f"{_RUBRIC_QUALITY_RULES}\n"
            "Rules:\n"
            "  - Use the mark allocation visible in the text ([5 marks], (3), 5M).\n"
            "  - Sub-questions only — never list both parent and sub-questions.\n"
            "  - Skip section headers/instructions; only emit Q lines for actual questions.\n\n"
            "Output ONE line per question:\n"
            "    Q<num> (<X> marks): <detailed mark-point criteria>\n\n"
            "Return STRICT JSON only:\n"
            '{ "rubric_lines": [string], "questions_found": int, "chunk_marks": int }\n\n'
            f"Paper part {i+1}/{len(chunks)}:\n\"\"\"\n{chunk}\n\"\"\""
        )
        content = _groq_chat_with_retry(
            model,
            messages=[
                {"role": "system", "content": "You return only valid JSON."},
                {"role": "user",   "content": chunk_prompt},
            ],
            temperature=0.2,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
        part = _extract_json(content)
        lines = [str(l).strip() for l in (part.get("rubric_lines") or []) if str(l).strip()]
        all_lines.extend(lines)
        total_q += int(part.get("questions_found") or len(lines))
        total_marks += int(part.get("chunk_marks") or 0)
        print(f"[rubric chunk {i+1}/{len(chunks)}] {len(lines)} Q lines, {part.get('chunk_marks')} marks")
        # Pace chunks so TPM window has time to clear between calls
        # (Groq llama-3.3-70b free tier: 6000 TPM, each chunk uses ~3500 tokens,
        # so 18-20s wait keeps us safely under the limit)
        if i + 1 < len(chunks):
            _time.sleep(20)
    out = {
        "rubric": "\n".join(all_lines),
        "questions_found": total_q,
        "total_marks": total_marks,
    }
    return _correct_total_marks(out, full_text)


def detect_scope(student_answer: str, rubric: str = "") -> dict[str, Any]:
    """Auto-detect CBSE grade + subject + chapter from a student answer + rubric.
    Cheap Groq call (~0.5s). Returns {grade, subject, chapter, confidence, reason}.

    The RUBRIC is the strongest signal — it's what the teacher wrote. Question
    PATTERNS matter more than topic words (a passage ABOUT renewable energy in
    an English paper is still ENGLISH, not Science).
    """
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    prompt = (
        "You are a CBSE classifier. Infer the paper's grade, subject, and chapter.\n\n"
        "🎯 PRIORITY RULE — use the RUBRIC and question PATTERNS, not the topic words:\n"
        "  • Rubric/questions mention 'antonyms', 'reading comprehension', 'tone of writer', "
        "'letter writing', 'analytical paragraph', 'inference', 'passage', 'message conveyed', "
        "'phrase substitution' → SUBJECT IS ENGLISH (even if the passage is ABOUT renewable "
        "energy, mangoes, climate, or any non-English topic).\n"
        "  • Rubric mentions 'derive', 'equation', 'numerical', 'diagram of cell', "
        "'experiment', 'Newton's laws', 'photosynthesis as a process' → Science / Physics / "
        "Chemistry / Biology.\n"
        "  • Rubric mentions 'solve', 'equation', 'theorem', 'prove', 'calculate', 'find x' "
        "→ Maths.\n"
        "  • Rubric mentions 'cause of war', 'historical event', 'amendment', 'biosphere', "
        "'monsoon', 'parliament' → Social Science.\n"
        "  • Rubric in Hindi script → Hindi. In Sanskrit → Sanskrit.\n\n"
        "An English READING COMPREHENSION can be about ANY topic (science, sports, history). "
        "The SUBJECT depends on what the TEACHER is testing (comprehension/language/grammar) "
        "NOT what the passage is about.\n\n"
        "Return STRICT JSON only:\n"
        "{ \"grade\": int (1-12), \"subject\": string, \"chapter\": string, "
        "\"confidence\": int (0-100), \"reason\": string (one sentence) }\n\n"
        f"RUBRIC (strongest signal):\n\"\"\"\n{rubric[:1500] or '(no rubric provided)'}\n\"\"\"\n\n"
        f"Student answer:\n\"\"\"\n{student_answer[:2500]}\n\"\"\""
    )
    content = _groq_chat_with_retry(
        model,
        messages=[
            {"role": "system", "content": "You return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.1,
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    out = _extract_json(content)
    try:
        out["grade"] = max(1, min(12, int(out.get("grade", 6))))
    except Exception:
        out["grade"] = 6
    out["subject"] = str(out.get("subject", "") or "").strip() or "General"
    out["chapter"] = str(out.get("chapter", "") or "").strip()
    return out


def grade_text(system_prompt: str, student_answer: str) -> dict[str, Any]:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    # Estimate tokens needed: ~80 tokens per question in per_question array.
    # Count questions in rubric (lines starting with Q) and set a floor of 2000.
    q_count = len(re.findall(r"^\s*Q\d", system_prompt, re.MULTILINE))
    max_tokens = max(2000, q_count * 100 + 1000)
    content = _groq_chat_with_retry(
        model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": f"Student answer:\n\n{student_answer}"},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return _extract_json(content)


def cluster_misconceptions(mistakes_by_student: list[dict]) -> list[dict[str, Any]]:
    """Second Groq call: take all per-student mistakes across the class and
    cluster them into top common misconceptions."""
    if not mistakes_by_student:
        return []
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    sample = mistakes_by_student[:30]  # cap to keep prompt small
    prompt = (
        "You are a CBSE teacher reviewing common mistakes across a class. "
        "Below is a list of mistakes each student made (one item per student). "
        "Identify the TOP 3-5 SHARED misconceptions — patterns that affect "
        "multiple students. Return STRICT JSON:\n"
        "{\n"
        '  "misconceptions": [\n'
        '    { "label": "<short title>",\n'
        '      "description": "<one sentence>",\n'
        '      "count": <int how many students>,\n'
        '      "students": [<student name strings>],\n'
        '      "remedy": "<one-line teaching tip>" }\n'
        "  ]\n"
        "}\n\n"
        "Student mistakes:\n" +
        "\n".join(
            f"- {m.get('student','?')}: " +
            "; ".join(f"{x.get('type','')}: {x.get('description','')[:120]}"
                       for x in (m.get('mistakes') or [])[:3])
            for m in sample
        )
    )
    content = _groq_chat_with_retry(
        model,
        messages=[
            {"role": "system", "content": "You return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2, max_tokens=900,
        response_format={"type": "json_object"},
    )
    out = _extract_json(content)
    return out.get("misconceptions") or []


def make_study_plan(grade_result: dict[str, Any], grade_level: int,
                    subject: str, chapter: str) -> list[str]:
    """Generate a 2-3 bullet personalised next-steps plan for one student."""
    mistakes = grade_result.get("mistakes") or []
    if not mistakes:
        return []
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    tier = "junior" if grade_level <= 4 else "middle" if grade_level <= 8 else "senior"
    prompt = (
        f"For a CBSE Grade {grade_level} ({tier}) student studying \"{subject}\" — "
        f"chapter \"{chapter}\", generate a SHORT personalised study plan of "
        "EXACTLY 2-3 specific next steps based on these mistakes:\n"
        + "\n".join(f"- {m.get('type','')}: {m.get('description','')}" for m in mistakes[:5])
        + "\n\nEach step must:\n"
        "  - Be ONE short sentence (max 20 words)\n"
        "  - Name a SPECIFIC action (re-read which section, practise which problem type)\n"
        "  - Be in tier-appropriate language (junior=simple+emoji, senior=board-exam precise)\n\n"
        'Return STRICT JSON: { "plan": [string, string, string] }'
    )
    content = _groq_chat_with_retry(
        model,
        messages=[
            {"role": "system", "content": "You return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3, max_tokens=400,
        response_format={"type": "json_object"},
    )
    out = _extract_json(content)
    plan = out.get("plan") or []
    return [str(p) for p in plan][:3]


def ncert_validate(student_answer: str, grade: int, subject: str, chapter: str,
                   system_prompt: str) -> dict[str, Any]:
    """Check if the student's answer content aligns with NCERT books for the
    detected grade/subject/chapter. Uses the AI's built-in NCERT knowledge.
    Returns a structured report with ncert_alignment_score, issues, etc."""
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    content = _groq_chat_with_retry(
        model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": f"Student answer sheet (Grade {grade} {subject}):\n\n{student_answer[:4000]}"},
        ],
        temperature=0.1,
        max_tokens=1200,
        response_format={"type": "json_object"},
    )
    out = _extract_json(content)
    # Ensure required fields exist
    out.setdefault("ncert_alignment_score", 0)
    out.setdefault("syllabus_match", "unknown")
    out.setdefault("is_ncert_paper", False)
    out.setdefault("ncert_issues", [])
    out.setdefault("overall_comment", "")
    return out


def verify_grade(student_answer: str, rubric: str, grade_result: dict[str, Any]) -> dict[str, Any]:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    critic = (
        "You are a skeptical senior CBSE examiner reviewing another examiner's "
        "grading. Your job is to catch over-generous or unfair marks. Return STRICT JSON:\n"
        "{ \"agrees\": boolean, \"confidence\": number (0-100), "
        "\"suggested_marks\": number, \"comment\": string }\n\n"
        f"Rubric:\n\"\"\"\n{rubric}\n\"\"\"\n\n"
        f"Student answer:\n\"\"\"\n{student_answer[:4000]}\n\"\"\"\n\n"
        f"First grader's verdict:\n"
        f"- marks: {grade_result.get('marks_awarded')}/{grade_result.get('marks_total')}\n"
        f"- suggestion: {grade_result.get('suggestion','')}\n"
        f"- mistakes: {grade_result.get('mistakes', [])}\n"
    )
    content = _groq_chat_with_retry(
        model,
        messages=[
            {"role": "system", "content": "You return only valid JSON."},
            {"role": "user",   "content": critic},
        ],
        temperature=0.1,
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    return _extract_json(content)


_GEMINI_FALLBACK_CHAIN = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


def _gemini_model_chain() -> list[str]:
    primary = os.getenv("GEMINI_MODEL", "").strip() or _GEMINI_FALLBACK_CHAIN[0]
    chain = [primary] + [m for m in _GEMINI_FALLBACK_CHAIN if m != primary]
    seen, out = set(), []
    for m in chain:
        if m and m not in seen:
            seen.add(m); out.append(m)
    return out


def _is_overloaded(msg: str) -> bool:
    return ("503" in msg or "UNAVAILABLE" in msg
            or "overloaded" in msg.lower() or "high demand" in msg.lower())


def _is_quota(msg: str) -> bool:
    return ("429" in msg or "RESOURCE_EXHAUSTED" in msg
            or "exceeded your current quota" in msg.lower())


def groq_vision_ocr(image_bytes_list: list[bytes], prompt: str,
                    mime: str = "image/png") -> str:
    """Vision OCR via Groq's Llama 3.2 vision (multimodal). Up to 5 images per call.
    Used as fallback when Gemini quota is exhausted."""
    import base64

    model = os.getenv("GROQ_VISION_MODEL", "llama-3.2-11b-vision-preview")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in image_bytes_list[:5]:  # Groq vision caps at ~5 images per request
        b64 = base64.b64encode(img).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })

    rsp = _groq().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.2,
        max_tokens=4000,
    )
    return (rsp.choices[0].message.content or "").strip()


def gemini_ocr(image_bytes: bytes, mime: str = "image/jpeg",
               prompt: str | None = None) -> str:
    """Run Gemini Vision OCR on an image with fallback chain.

    If `prompt` is None, uses the default 'transcribe verbatim' prompt.
    Pass a custom prompt to extract solved papers, math-only content, etc.
    """
    import time

    if prompt is None:
        prompt = (
            "You are reading a student's handwritten answer sheet. Extract ALL content accurately.\n\n"
            "TEXT: Transcribe handwritten text verbatim — keep spelling errors, grammar mistakes, "
            "abbreviations exactly as written.\n\n"
            "DIAGRAMS / DRAWINGS / FLOWCHARTS: If the student drew a diagram, figure, flowchart "
            "or mind-map, write:\n"
            "  [DIAGRAM: <describe what is drawn in one line>. Labels visible: <list every word, "
            "arrow label, or annotation written on the diagram>]\n"
            "  This is very important — a diagram IS a valid answer and must not be skipped.\n\n"
            "TABLES: If the student drew a table or comparison chart, preserve it using | column "
            "separators. Example:\n"
            "  | Feature | Plant Cell | Animal Cell |\n"
            "  | Cell wall | Present | Absent |\n\n"
            "MATHEMATICAL EXPRESSIONS: Write equations and formulas clearly. Use ^ for powers, "
            "* for multiplication, / for division, sqrt() for roots. Example: a^2 + b^2 = c^2, "
            "F = ma, v = u + at.\n\n"
            "NUMBERED/BULLET LISTS: Preserve the numbering or bullets. If a student wrote 1. 2. 3. "
            "or used dashes, keep that structure.\n\n"
            "MIXED LANGUAGE (Hinglish): If the student wrote a mix of Hindi and English, transcribe "
            "exactly as written. Do NOT translate. Hindi words written in English script "
            "(e.g. 'photosynthesis ko prakaash sangleshan kehte hain') are valid answers.\n\n"
            "If a student name is at the top, put 'Name: <name>' on the first line.\n"
            "Plain text output only — no commentary, no markdown fences."
        )
    parts = [gtypes.Part.from_bytes(data=image_bytes, mime_type=mime), prompt]

    last_err = None
    for model in _gemini_model_chain():
        for attempt in range(3):  # 1 try + 2 retries
            if attempt > 0:
                time.sleep(2 * (2 ** (attempt - 1)))
            try:
                rsp = _gemini().models.generate_content(model=model, contents=parts)
                return (rsp.text or "").strip()
            except Exception as e:
                msg = str(e); last_err = e
                if _is_quota(msg):
                    raise RuntimeError(
                        "Gemini daily quota exhausted. Wait until reset or use a new key."
                    )
                if not _is_overloaded(msg):
                    raise
                print(f"[gemini-ocr {model}] overloaded (attempt {attempt + 1}/3), backing off…")
        print(f"[gemini-ocr] falling through from {model} -> next model")

    raise RuntimeError(f"All Gemini models overloaded for OCR. Last error: {last_err}")
