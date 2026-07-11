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


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        val = val.strip()
        try:
            return int(float(val))
        except ValueError:
            match = re.search(r"\d+", val)
            if match:
                return int(match.group())
    return default


_GROQ_TEXT_CHAIN = [
    "llama-3.3-70b-versatile",   # smartest, lowest TPM cap — primary
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
    cfg_kwargs = dict(temperature=temperature, maxOutputTokens=max(8192, max_tokens))
    if want_json:
        cfg_kwargs["responseMimeType"] = "application/json"
        cfg_kwargs["thinkingConfig"] = gtypes.ThinkingConfig(thinkingBudget=0)

    last_err = None
    for model in _gemini_model_chain():
        try:
            rsp = _gemini().models.generate_content(
                model=model,
                contents=[prompt],
                config=gtypes.GenerateContentConfig(**cfg_kwargs),
            )
            return rsp.text or ""
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if _is_quota(msg):
                print(f"[gemini-text-fallback {model}] quota exhausted, trying next Gemini model...")
                continue
            raise e
    raise last_err


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
                          "Marks may be over-counted - verify the rubric carefully.")
                return rsp.choices[0].message.content or ""
            except Exception as e:
                msg = str(e); last_err = e
                if ("429" in msg or "413" in msg or "rate limit" in msg.lower()
                        or "rate_limit" in msg.lower() or "tokens per minute" in msg.lower()
                        or "Request too large" in msg):
                    if attempt + 1 < max_attempts:
                        wait = _parse_retry_after(msg)
                        print(f"[groq {m}] rate-limited (attempt {attempt+1}/{max_attempts}), waiting {wait:.1f}s then retrying same model...")
                        _time.sleep(wait)
                        continue
                    print(f"[groq {m}] still rate-limited after {max_attempts} tries - falling to next model")
                    break
                else:
                    print(f"[groq {m}] failed with error ({e}) - falling to next model")
                    break
    # All Groq models exhausted - fall back to Gemini (separate quota bucket)
    print(f"[groq->gemini] All Groq models rate-limited. Falling back to Gemini 2.5 Flash. Last Groq err: {last_err}")
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
        _gemini_client = genai.Client(
            api_key=key,
            http_options=gtypes.HttpOptions(
                retry_options=gtypes.HttpRetryOptions(attempts=1)
            )
        )
    return _gemini_client


def _repair_json_quotes(raw: str) -> str:
    n = len(raw)
    chars = list(raw)
    out = []
    
    for i in range(n):
        c = chars[i]
        if c == '"':
            # Count backslashes before it
            bs_count = 0
            k = i - 1
            while k >= 0 and chars[k] == '\\':
                bs_count += 1
                k -= 1
            if bs_count % 2 == 1:
                # It is escaped, just append it
                out.append('"')
                continue
                
            # Find previous non-whitespace char
            prev_char = ""
            for k in range(i - 1, -1, -1):
                if not chars[k].isspace():
                    prev_char = chars[k]
                    break
            # Find next non-whitespace char
            next_char = ""
            next_idx = -1
            for k in range(i + 1, n):
                if not chars[k].isspace():
                    next_char = chars[k]
                    next_idx = k
                    break
                    
            # Determine if structural
            is_structural = False
            if prev_char in ('{', '[', ':'):
                is_structural = True
            elif next_char in ('}', ']', ':'):
                is_structural = True
            elif next_char == ',':
                is_structural = True
            elif prev_char == ',':
                # Preceded by comma. Could be starting a key like: , "key":
                # Check if this quote is followed by a string key ending with a quote and then a colon
                has_colon = False
                for k in range(i + 1, n):
                    if chars[k] == '"' and chars[k-1] != '\\':
                        # Check if followed by colon
                        for m in range(k + 1, n):
                            if not chars[m].isspace():
                                if chars[m] == ':':
                                    has_colon = True
                                break
                        break
                if has_colon:
                    is_structural = True
                else:
                    # Could be an array element
                    # Let's check if there is a colon before the comma
                    is_array_element = True
                    for k in range(i - 1, -1, -1):
                        if chars[k] == ':':
                            is_array_element = False
                            break
                        elif chars[k] in ('{', '}'):
                            break
                    if is_array_element:
                        is_structural = True
            
            if is_structural:
                out.append('"')
            else:
                out.append('\\"')
        else:
            out.append(c)
            
    return "".join(out)


def _extract_json(raw: str) -> dict[str, Any]:
    if not raw: raise ValueError("empty model output")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    else:
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        content = brace.group(0) if brace else raw
        
    try:
        return json.loads(content)
    except Exception as e:
        print(f"[_extract_json] standard JSON load failed: {e}. Attempting to repair quotes...")
        repaired = ""
        try:
            repaired = _repair_json_quotes(content)
            return json.loads(repaired)
        except Exception as e2:
            print(f"[_extract_json] JSON repair failed: {e2}")
            # Save debug files
            try:
                with open("failed_grader_response.json", "w", encoding="utf-8") as f:
                    f.write(content)
                if repaired:
                    with open("failed_repaired_response.json", "w", encoding="utf-8") as f:
                        f.write(repaired)
            except Exception as ef:
                print(f"Failed to write debug files: {ef}")
            # If repair fails, fall back to the original json.loads(content) to throw the clear error
            return json.loads(content)


_RUBRIC_CHUNK_CHARS = 3000  # ~750 tokens — keeps output bounded and fits comfortably in Groq TPM limits


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
        while b - cur_start > max_chars:
            chunks.append(text[cur_start:cur_start + max_chars])
            cur_start = cur_start + max_chars
        if b - cur_start >= max_chars * 0.3 or b == len(text):
            chunks.append(text[cur_start:b])
            cur_start = b
    return [c for c in chunks if c.strip()]


def extract_paper_metadata(paper_text: str) -> dict[str, Any]:
    """Extract board and total_marks from a CBSE question paper header.
    Subject and Grade detection is explicitly disabled and must be selected manually.

    Returns: { grade: None, subject: "", board: str, total_marks: int|None }
    """
    # Take first 800 chars — the header is always at the top
    header = paper_text[:800]

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
    tm = re.search(r'(?:maximum|total|max\.?|पूर्णांक|कुल\s*अंक|अधिकतम\s*अंक)\s*(?:marks?|अंक)?\s*[:\-]?\s*(\d{2,4})', paper_text[:2000], re.IGNORECASE)
    if tm:
        total_marks = int(tm.group(1))

    return {"grade": None, "subject": "", "board": board, "total_marks": total_marks}


def generate_rubric_from_questions(question_paper_text: str, solution_key_text: str = "") -> dict[str, Any]:
    """Read a question paper (extracted text) and produce a teacher-ready rubric.

    🚀 Tries Gemini 2.5 Flash FIRST — 1M context, generous free tier, handles
    the full paper in a single fast call. Falls back to Groq (chunked) only
    if Gemini is unavailable.

    Returns { "rubric": str (multi-line), "questions_found": int, "total_marks": int }.
    """
    # Try Gemini first — fastest path for long papers
    try:
        out = _generate_rubric_gemini(question_paper_text, solution_key_text)
        out = _recalculate_rubric_stats(out)
        return _correct_total_marks(out, question_paper_text)
    except Exception as e:
        print(f"[rubric] Gemini path failed ({e}) - falling back to Groq chunked pipeline")

    # Fallback: Groq with chunking
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    combined_len = len(question_paper_text) + len(solution_key_text)
    
    paper_chunks = _chunk_paper_by_questions(question_paper_text, _RUBRIC_CHUNK_CHARS)
    sol_chunks = _chunk_paper_by_questions(solution_key_text, _RUBRIC_CHUNK_CHARS) if solution_key_text else []
    
    num_chunks = max(len(paper_chunks), len(sol_chunks))
    
    if num_chunks > 1 or combined_len > 12000:
        # Pad chunks to match count so we send corresponding parts of paper and solution
        paper_chunks_padded = paper_chunks + [""] * (num_chunks - len(paper_chunks))
        sol_chunks_padded = sol_chunks + [""] * (num_chunks - len(sol_chunks))
        print(f"[rubric] Groq fallback - splitting {combined_len} chars into {num_chunks} chunks to fit TPM window.")
        return _generate_rubric_chunked(question_paper_text, paper_chunks_padded, model, sol_chunks_padded)
        
    paper = paper_chunks[0] if paper_chunks else question_paper_text
    truncated_note = ""
    prompt = (
        "You are a CBSE senior examiner writing a DETAILED marking rubric from a question paper.\n\n"
        "🎯 TOTAL MARKS RULE:\n"
        "Find 'Maximum Marks : NN' at the top. Your `total_marks` MUST equal that value EXACTLY.\n\n"
        "🚫 ANTI-DOUBLE-COUNT: Sub-questions (Q1.1, Q1.2) already sum to the parent (Q1). "
        "List ONLY the sub-questions OR the parent — NEVER both.\n\n"
        f"{_RUBRIC_QUALITY_RULES}\n"
    )
    if solution_key_text:
        prompt += (
            "🔑 TEACHER'S SOLUTION KEY / ANSWER KEY:\n"
            "You have been provided with the teacher's official solution key / answer key below. "
            "You MUST use the solutions, steps, and correct answers from this key to write the "
            "detailed marking rubric criteria. Map each solution back to its corresponding question.\n"
            "\"\"\"\n"
            f"{solution_key_text}\n"
            "\"\"\"\n\n"
        )
    prompt += (
        "Output ONE line per question:\n"
        "    Q<num> (<X> marks): <detailed mark-point criteria>\n\n"
        "VERIFY: sum of marks in rubric must equal total_marks.\n\n"
        "Return STRICT JSON only:\n"
        '{ "rubric": string, "questions_found": int, "total_marks": int }\n\n'
        f"Question paper text:{truncated_note}\n\"\"\"\n{paper}\n\"\"\""
    )
    
    content = ""
    try:
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
    except Exception as je:
        print(f"[rubric] Single-call JSON parsing failed: {je}. Attempting regex salvage...")
        rubric_m = re.search(r'"rubric"\s*:\s*"((?:[^"\\]|\\.)*)', content, re.DOTALL) if content else None
        total_m = re.search(r'"total_marks"\s*:\s*(\d+)', content) if content else None
        qf_m = re.search(r'"questions_found"\s*:\s*(\d+)', content) if content else None
        if rubric_m:
            rubric_raw = rubric_m.group(1)
            rubric_text = rubric_raw.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")
            out = {
                "rubric": rubric_text,
                "questions_found": _safe_int(qf_m.group(1)) if qf_m else 0,
                "total_marks": _safe_int(total_m.group(1)) if total_m else 0,
            }
        else:
            lines = re.findall(r"^\s*(Q\d+[\w\.]*\s*\(.*?\)\s*:.*?)$", content, re.MULTILINE)
            out = {
                "rubric": "\n".join(lines),
                "questions_found": len(lines),
                "total_marks": 0
            }
            
    out["rubric"] = str(out.get("rubric", "")).strip()
    out["questions_found"] = _safe_int(out.get("questions_found") or 0)
    out["total_marks"] = _safe_int(out.get("total_marks") or 0)
    # Fallback: count Q-lines in rubric when model returns 0
    if out["questions_found"] == 0 and out["rubric"]:
        counted = len(re.findall(r"^\s*Q\d+[\.\(]", out["rubric"], re.MULTILINE))
        if counted:
            out["questions_found"] = counted
    out = _recalculate_rubric_stats(out)
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
HOW TO WRITE THE RUBRIC LINES:
=====================================================
You MUST structure the rubric lines based on the questions and marks found in the Question Paper and the teacher's official Solution Key / Answer Key.

Rules:
1. STRUCTURE FROM QUESTION PAPER: Formulate the list of questions, question numbers (e.g. Q1, Q2, Q3), and their mark allocations strictly from the Question Paper. Do NOT omit questions present in the Question Paper.
   - ⚠️ IMPORTANT: If a question has sub-parts (e.g. Question 1 has parts (i), (ii) or (a), (b)), you MUST prefix the sub-part with the parent question number. Write "Q1(i)" or "Q1(a)", NOT "Q(i)" or "Q(a)". Always maintain the correct question prefix.
2. ANSWER KEY INTEGRATION: For each question, look up the correct answers, steps, and key points from the provided teacher's Solution Key / Answer Key. Do NOT invent your own correct answers, and do not assume ideal answers.
3. STRICT ADHERENCE: Use only the teacher's answer key as the grading reference. Do not generate or assume ideal answers. Never create your own answer format or invent rubric points. Follow only the teacher-provided answer key and marking instructions. If the Answer Key lacks details or contains placeholder text, you must still only grade against what is explicitly specified by the teacher.
4. SPECIFICITY: Every rubric line MUST specify the exact question content (e.g., "Evaluate sin 60 cos 30 + sin 30 cos 60" or "Prove that 2 - sqrt(3) is irrational") and the step-by-step mark breakdown, not just generic guidelines.
5. LANGUAGE CONSISTENCY: You MUST write the rubric description, questions, and marking criteria in the SAME language as the original question from the Question Paper. If a question is written in Hindi, the rubric description and marking criteria for that question MUST be written in Hindi. Do NOT translate Hindi questions or criteria to English.
"""



def _generate_rubric_gemini(paper_text: str, solution_key_text: str = "") -> dict[str, Any]:
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
    )
    if solution_key_text:
        prompt += (
            "🔑 TEACHER'S SOLUTION KEY / ANSWER KEY:\n"
            "You have been provided with the teacher's official solution key / answer key below. "
            "You MUST use the solutions, steps, and correct answers from this key to write the "
            "detailed marking rubric criteria. Map each solution back to its corresponding question.\n"
            "\"\"\"\n"
            f"{solution_key_text}\n"
            "\"\"\"\n\n"
        )
    prompt += (
        "Output format — ONE line per question:\n"
        "    Q<num> (<X> marks): <detailed mark-point-by-mark-point criteria>\n\n"
        "Cover ALL questions across ALL sections (A, B, C, D, E). Do not skip any.\n\n"
        "Return STRICT JSON only:\n"
        '{ "rubric": "multi-line string", "questions_found": int, "total_marks": int }\n\n'
        f"Question paper text:\n\"\"\"\n{paper_text[:200000]}\n\"\"\""
    )
    last_err = None
    for model in _gemini_model_chain():
        try:
            rsp = _gemini().models.generate_content(
                model=model,
                contents=[prompt],
                config=gtypes.GenerateContentConfig(
                    responseMimeType="application/json",
                    temperature=0.2,
                    maxOutputTokens=32768,
                    thinkingConfig=gtypes.ThinkingConfig(thinkingBudget=0),
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
                        "questions_found": _safe_int(qf_m.group(1)) if qf_m else 0,
                        "total_marks": _safe_int(total_m.group(1)) if total_m else 0,
                    }
                else:
                    raise
            return out
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if _is_quota(msg):
                print(f"[rubric-gemini {model}] quota exhausted, trying next Gemini model...")
                continue
            raise e
    raise last_err
    out["rubric"] = str(out.get("rubric", "")).strip()
    out["questions_found"] = _safe_int(out.get("questions_found") or 0)
    out["total_marks"] = _safe_int(out.get("total_marks") or 0)
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
        r"(?:maximum|total|max\.?|पूर्णांक|कुल\s*अंक|अधिकतम\s*अंक)\s*(?:marks?|अंक)?\s*[:\-]?\s*(\d{2,4})\b",
        paper_text, re.IGNORECASE,
    )
    if declared_match:
        declared_total = int(declared_match.group(1))
        if 10 <= declared_total <= 500 and out.get("total_marks") != declared_total:
            model_said = _safe_int(out.get("total_marks") or 0)
            print(f"[rubric] Total mismatch: model said {model_said}, paper declared "
                  f"{declared_total}. Trusting the paper's declared value.")
            out["total_marks"] = declared_total
            out["_total_marks_corrected"] = True
            out["_total_marks_model_said"] = model_said
    return out


def _recalculate_rubric_stats(out: dict[str, Any]) -> dict[str, Any]:
    """Recalculate questions_found and total_marks programmatically by parsing the final compiled rubric."""
    rubric = out.get("rubric") or ""
    if not rubric:
        return out

    if isinstance(rubric, dict):
        # Convert dictionary format to standard multi-line string
        dict_lines = []
        for q_num, criteria in rubric.items():
            if isinstance(criteria, dict):
                marks = criteria.get("marks") or criteria.get("max_marks") or ""
                desc = criteria.get("desc") or criteria.get("criteria") or criteria.get("answer") or str(criteria)
                marks_str = f" ({marks} marks)" if marks else ""
                dict_lines.append(f"{q_num}{marks_str}: {desc}")
            else:
                dict_lines.append(f"{q_num}: {criteria}")
        rubric = "\n".join(dict_lines)
        out["rubric"] = rubric

    lines = rubric.splitlines()
    main_questions = set()
    calculated_total_marks = 0.0

    for line in lines:
        line = line.strip()
        if not line.startswith("Q"):
            continue

        colon_idx = line.find(":")
        if colon_idx != -1:
            header = line[:colon_idx].strip()
            
            # Extract parent question number, e.g. "Q1" from "Q1(i)"
            m_main = re.match(r'^(Q\d+)', header)
            if m_main:
                main_questions.add(m_main.group(1))
            else:
                main_questions.add(header)
            
            # Find all contents inside parentheses (...) or brackets [...] in the header
            matches = re.findall(r'[\(\[]([^\]\)]+)[\)\]]', header)
            for content in matches:
                content = content.lower().strip()
                
                # Check for indicators of mark specifications: "mark", "marks", "अंक", "अंकों"
                if any(k in content for k in ["mark", "अंक"]):
                    # Parse math expressions like "1+1=2" or "=2"
                    if "=" in content:
                        m_eq = re.search(r'=\s*([\d\.]+)', content)
                        if m_eq:
                            try:
                                calculated_total_marks += float(m_eq.group(1))
                                break
                            except ValueError:
                                pass
                    
                    # Match digit followed by mark word: "1 mark", "2 अंक", etc.
                    m_num = re.search(r'([\d\.]+)\s*(?:mark|अंक)', content)
                    if m_num:
                        try:
                            calculated_total_marks += float(m_num.group(1))
                            break
                        except ValueError:
                            pass
                    
                    # Match subparts sum: "1+1 marks" or similar
                    numbers = re.findall(r'[\d\.]+', content)
                    if numbers and "+" in content:
                        try:
                            calculated_total_marks += sum(float(n) for n in numbers)
                            break
                        except ValueError:
                            pass
                else:
                    # Fallback: check if the content is purely a number (e.g. [2] or (2)), ignoring Roman/alpha labels
                    if re.match(r'^[\d\.]+$', content):
                        try:
                            calculated_total_marks += float(content)
                            break
                        except ValueError:
                            pass

    out["questions_found"] = len(main_questions)
    if calculated_total_marks > 0:
        out["total_marks"] = int(calculated_total_marks)
    return out


def _generate_rubric_chunked(full_text: str, chunks: list[str], model: str, solution_key_chunks: list[str] = None) -> dict[str, Any]:
    """Generate a rubric for a long paper by processing one chunk at a time
    (with a small wait between chunks to respect Groq's TPM rate window)."""
    all_lines: list[str] = []
    total_q, total_marks = 0, 0
    for i, chunk in enumerate(chunks):
        chunk_prompt = (
            f"You are reading PART {i+1}/{len(chunks)} of a multi-page CBSE question paper.\n\n"
            "Write a DETAILED marking rubric for every question in this part.\n\n"
            f"{_RUBRIC_QUALITY_RULES}\n"
        )
        if solution_key_chunks and i < len(solution_key_chunks) and solution_key_chunks[i].strip():
            chunk_prompt += (
                "🔑 TEACHER'S SOLUTION KEY / ANSWER KEY (Part):\n"
                "Use the solutions and correct answers from this key to ground the detailed rubric criteria:\n"
                "\"\"\"\n"
                f"{solution_key_chunks[i]}\n"
                "\"\"\"\n\n"
            )
        chunk_prompt += (
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
        content = ""
        try:
            content = _groq_chat_with_retry(
                model,
                messages=[
                    {"role": "system", "content": "You return only valid JSON."},
                    {"role": "user",   "content": chunk_prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
            part = _extract_json(content)
            lines = [str(l).strip() for l in (part.get("rubric_lines") or []) if str(l).strip()]
            if not lines and part.get("rubric"):
                lines = [l.strip() for l in str(part.get("rubric")).splitlines() if l.strip()]
        except Exception as je:
            print(f"[rubric chunk {i+1}/{len(chunks)}] JSON parsing failed: {je}. Attempting regex salvage...")
            lines = []
            if content:
                lines = [l.strip() for l in re.findall(r'(Q\d+[\w\.]*\s*\(.*?\)\s*:[^"\n]+)', content)]
            part = {"chunk_marks": 0}
            
        all_lines.extend(lines)
        total_q += _safe_int(part.get("questions_found") or len(lines))
        total_marks += _safe_int(part.get("chunk_marks") or 0)
        print(f"[rubric chunk {i+1}/{len(chunks)}] {len(lines)} Q lines, {part.get('chunk_marks')} marks")
        # Pace chunks so TPM window has time to clear between calls
        if i + 1 < len(chunks):
            _time.sleep(20)
    out = {
        "rubric": "\n".join(all_lines),
        "questions_found": total_q,
        "total_marks": total_marks,
    }
    out = _recalculate_rubric_stats(out)
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
        f"RUBRIC (strongest signal):\n\"\"\"\n{rubric or '(no rubric provided)'}\n\"\"\"\n\n"
        f"Student answer:\n\"\"\"\n{student_answer}\n\"\"\""
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
        out["grade"] = max(1, min(12, _safe_int(out.get("grade", 6))))
    except Exception:
        out["grade"] = 6
    out["subject"] = str(out.get("subject", "") or "").strip() or "General"
    out["chapter"] = str(out.get("chapter", "") or "").strip()
    return out


def grade_text(system_prompt: str, student_answer: str) -> dict[str, Any]:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Check if we should chunk
    q_count = len(re.findall(r"^\s*Q\d", system_prompt, re.MULTILINE))
    
    # If the number of questions is small (<= 8), we can run a single call
    if q_count <= 8:
        max_tokens = max(1500, q_count * 200 + 800)
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
        
    # Otherwise, chunk the grading question-by-question to avoid TPM rate limits (413)
    print(f"[grader] Splitting {q_count} questions into chunks of 8 to fit Groq TPM limit.")
    
    # Extract the rubric from the system prompt
    rubric_match = re.search(r'Marking rubric the teacher provided:\s*"""(.*?)"""', system_prompt, re.DOTALL)
    if not rubric_match:
        # Fallback if regex doesn't match: just run single call
        max_tokens = max(4000, q_count * 180 + 1500)
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
        
    rubric = rubric_match.group(1).strip()
    
    # Split the rubric into chunks of 8 questions
    rubric_lines = rubric.splitlines()
    chunks = []
    current_chunk = []
    chunk_q_count = 0
    for line in rubric_lines:
        current_chunk.append(line)
        line_clean = re.sub(r"^[-*#\s]+", "", line)
        if re.match(r"^(?:q\d+|question\s*\d+|q\s*\d+)", line_clean, re.IGNORECASE):
            chunk_q_count += 1
            if chunk_q_count >= 8:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                chunk_q_count = 0
    if current_chunk:
        chunks.append("\n".join(current_chunk))
        
    results = []
    for idx, r_chunk in enumerate(chunks):
        print(f"[grader] Processing chunk {idx+1}/{len(chunks)}...")
        # Reconstruct the system prompt for this chunk
        chunk_system_prompt = system_prompt.replace(rubric, r_chunk)
        
        # Enforce chunk-specific marks total rule
        chunk_marks = len(re.findall(r"^\s*Q\d", r_chunk, re.MULTILINE))
        max_tokens = max(1500, chunk_marks * 200 + 800)
        
        try:
            content = _groq_chat_with_retry(
                model,
                messages=[
                    {"role": "system", "content": chunk_system_prompt},
                    {"role": "user",   "content": f"Student answer:\n\n{student_answer}"},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            res = _extract_json(content)
            results.append(res)
        except Exception as e:
            print(f"[grader] Chunk {idx+1} failed: {e}")
            # Add a dummy fallback result for this chunk so we don't crash
            dummy_questions = []
            for line in r_chunk.splitlines():
                line_clean = re.sub(r"^[-*#\s]+", "", line)
                m = re.match(r"^((?:q\d+|question\s*\d+|q\s*\d+)[\w\.]*)\b", line_clean, re.IGNORECASE)
                if m:
                    dummy_questions.append({
                        "q": m.group(1),
                        "marks_awarded": 0,
                        "marks_total": 1,
                        "feedback": f"Grading failed for this question chunk: {e}",
                        "format": "text"
                    })
            results.append({
                "per_question": dummy_questions,
                "mistakes": [{"type": "other", "description": f"Grading chunk {idx+1} failed: {e}"}]
            })
            
        # Pace calls to respect RPM/TPM limits
        if idx + 1 < len(chunks):
            _time.sleep(10)
            
    # Merge results
    merged = {
        "student_name": "Student",
        "detected_language": "English",
        "marks_awarded": 0,
        "marks_total": 0,
        "percentage": 0.0,
        "answer_formats_used": [],
        "per_question": [],
        "mistakes": [],
        "strengths": [],
        "suggestion": "",
        "ai_cheat_suspicion": 0
    }
    
    all_formats = set()
    all_strengths = set()
    suggestions = []
    cheat_suspicions = []
    
    for res in results:
        if not res:
            continue
        if "student_name" in res and res["student_name"] and res["student_name"] != "Student":
            merged["student_name"] = res["student_name"]
        if "detected_language" in res and res["detected_language"]:
            merged["detected_language"] = res["detected_language"]
            
        merged["per_question"].extend(res.get("per_question") or [])
        merged["mistakes"].extend(res.get("mistakes") or [])
        
        all_formats.update(res.get("answer_formats_used") or [])
        all_strengths.update(res.get("strengths") or [])
        
        if res.get("suggestion"):
            suggestions.append(res["suggestion"])
        if res.get("ai_cheat_suspicion") is not None:
            cheat_suspicions.append(res["ai_cheat_suspicion"])
            
    merged["answer_formats_used"] = list(all_formats)
    merged["strengths"] = list(all_strengths)
    if suggestions:
        merged["suggestion"] = " ".join(suggestions)
    if cheat_suspicions:
        merged["ai_cheat_suspicion"] = int(sum(cheat_suspicions) / len(cheat_suspicions))
        
    # Recalculate totals
    pq = merged["per_question"]
    if pq:
        computed_total = sum(float(q.get("marks_total", 0) or 0) for q in pq)
        computed_awarded = sum(float(q.get("marks_awarded", 0) or 0) for q in pq)
        merged["marks_total"] = int(computed_total) if computed_total.is_integer() else computed_total
        merged["marks_awarded"] = int(computed_awarded) if computed_awarded.is_integer() else computed_awarded
        if merged["marks_total"] > 0:
            merged["percentage"] = round(merged["marks_awarded"] / merged["marks_total"] * 100, 1)
            
    return merged


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
            {"role": "user",   "content": f"Student answer sheet (Grade {grade} {subject}):\n\n{student_answer}"},
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
        "grading. Your job is to catch over-generous or unfair marks. "
        "Your evaluation MUST be based strictly and solely on the provided Rubric/Marking Scheme. "
        "Do not invent external standards or grade according to your own custom criteria. "
        "Verify that step-by-step answers were evaluated and step-by-step marks were awarded correctly as defined in the rubric. "
        "Return STRICT JSON:\n"
        "{ \"agrees\": boolean, \"confidence\": number (0-100), "
        "\"suggested_marks\": number, \"comment\": string }\n\n"
        f"Rubric:\n\"\"\"\n{rubric}\n\"\"\"\n\n"
        f"Student answer:\n\"\"\"\n{student_answer}\n\"\"\"\n\n"
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
    "gemini-1.5-flash",
    "gemini-1.5-pro",
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

    model = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    max_imgs = 3 if "qwen" in model.lower() else 5
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in image_bytes_list[:max_imgs]:  # Groq vision limits depend on model (Qwen supports up to 3)
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
                    print(f"[gemini-ocr {model}] quota exhausted, falling through to next model...")
                    break
                if not _is_overloaded(msg):
                    raise
                print(f"[gemini-ocr {model}] overloaded (attempt {attempt + 1}/3), backing off...")
        print(f"[gemini-ocr] falling through from {model} -> next model")

    raise RuntimeError(f"All Gemini models overloaded for OCR. Last error: {last_err}")
