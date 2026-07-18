"""AutoGrader LLM router.

- grade_text(...)   → Groq llama for typed/OCR'd answers
- verify_grade(...) → second Groq call (Verifier Agent)
- gemini_ocr(...)   → Gemini Flash vision OCR for scanned answer sheets
"""
from __future__ import annotations

import json
import os
import re
import asyncio
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
    cfg_kwargs = dict(temperature=temperature, max_output_tokens=max(8192, max_tokens))
    if want_json:
        cfg_kwargs["response_mime_type"] = "application/json"
        cfg_kwargs["thinking_config"] = gtypes.ThinkingConfig(thinking_budget=0)

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
            msg = str(e)
            if _is_quota(msg):
                _block_gemini()
            print(f"[gemini-text-fallback {model}] failed with error ({e}), trying next Gemini model...")
            continue
    raise last_err or RuntimeError("Gemini is currently disabled due to quota exhaustion")


_groq_blocked_until = {}

def _is_groq_model_blocked(model_name: str) -> bool:
    import time
    return time.time() < _groq_blocked_until.get(model_name, 0.0)

def _block_groq_model(model_name: str):
    import time
    _groq_blocked_until[model_name] = time.time() + 60  # block for 1 minute
    print(f"[groq] Rate limit hit. Blocking model {model_name} for 1 minute to bypass retry delays.")


def _groq_chat_with_retry(model: str, messages, *, max_tokens: int,
                           temperature: float = 0.2,
                           response_format=None) -> str:
    """Run a Groq chat completion. On 429 rate-limit:
      - Retry up to 3× on the SAME (smartest) model using the exact wait time
        Groq returns in its error message
      - Fall through to other Groq models as a LAST resort (smaller models
        over-count Board marks — verify rubric carefully when this happens)
      - If ALL Groq models fail → fall back to Gemini 2.5 Flash (separate quota)"""
    chain = [model] + [m for m in _GROQ_TEXT_CHAIN if m != model]
    seen = set(); chain = [m for m in chain if not (m in seen or seen.add(m))]
    
    # Filter out blocked models unless all are blocked
    active_chain = [m for m in chain if not _is_groq_model_blocked(m)]
    if not active_chain:
        active_chain = chain
        
    last_err = None
    for ci, m in enumerate(active_chain):
        max_attempts = 3
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
                if "413" in msg or "Request too large" in msg:
                    print(f"[groq {m}] Request too large (413). Skipping retries.")
                    break
                if ("429" in msg or "rate limit" in msg.lower()
                        or "rate_limit" in msg.lower() or "tokens per minute" in msg.lower()):
                    if attempt + 1 < max_attempts:
                        wait = _parse_retry_after(msg)
                        print(f"[groq {m}] rate-limited (attempt {attempt+1}/{max_attempts}), waiting {wait:.1f}s then retrying same model...")
                        _time.sleep(wait)
                        continue
                    # Only block when we exhaust all attempts for this model
                    _block_groq_model(m)
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
    from dotenv import load_dotenv
    load_dotenv(override=True)
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key: raise RuntimeError("GROQ_API_KEY not set")
    if _groq_client is None or getattr(_groq_client, "api_key", None) != key:
        _groq_client = Groq(api_key=key)
    return _groq_client


def _gemini() -> genai.Client:
    global _gemini_client
    from dotenv import load_dotenv
    load_dotenv(override=True)
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key: raise RuntimeError("GEMINI_API_KEY not set")
    if _gemini_client is None or getattr(_gemini_client, "_api_key", None) != key:
        _gemini_client = genai.Client(
            api_key=key,
            http_options=gtypes.HttpOptions(
                retry_options=gtypes.HttpRetryOptions(attempts=1)
            )
        )
        _gemini_client._api_key = key
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
        print(f"[_extract_json] standard JSON load failed: {e}. Attempting to repair with json_repair...")
        try:
            import json_repair
            return json_repair.loads(content)
        except Exception as e_repair:
            print(f"[_extract_json] json_repair parse failed: {e_repair}. Falling back to manual quote repair...")
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


_RUBRIC_CHUNK_CHARS = 2000  # ~500 tokens — keeps output bounded and fits comfortably in Groq TPM limits


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


def _normalize_vertical_text(text: str) -> str:
    """Detects vertical character layout (one char per line) or horizontal
    glitched spacing (spaces between chars) and merges them to make search robust."""
    if not text:
        return ""
    # 1. Merge vertical character layout (one char per line)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        short_lines = sum(1 for line in lines if len(line) <= 2)
        if short_lines / len(lines) > 0.3:
            merged = []
            for line in lines:
                if len(line) == 1:
                    merged.append(line)
                else:
                    merged.append(" " + line + " ")
            text = "".join(merged)
            
    # 2. Merge horizontal space-separated character layout (M a x i m u m)
    words = text.split()
    if words:
        avg_word_len = sum(len(w) for w in words[:100]) / len(words[:100])
        if avg_word_len < 2.0:
            lines = text.splitlines()
            new_lines = []
            for line in lines:
                parts = re.split(r'\s{2,}', line)
                new_parts = [p.replace(' ', '') for p in parts]
                new_lines.append(' '.join(new_parts))
            text = '\n'.join(new_lines)
            
    # 3. Clean up whitespace
    return re.sub(r'\s+', ' ', text)


def extract_paper_metadata(paper_text: str) -> dict[str, Any]:
    """Extract board and total_marks from a question paper header.
    Subject and Grade detection is explicitly disabled and must be selected manually.

    Returns: { grade: None, subject: "", board: str, total_marks: int|None }
    """
    normalized_text = _normalize_vertical_text(paper_text[:4000])
    # Take first 800 chars — the header is always at the top
    header = normalized_text[:800]

    # Board
    board = ""
    if re.search(r'\bCBSE\b', header, re.IGNORECASE):
        board = "Board"
    elif re.search(r'\bICSE\b|\bISC\b', header, re.IGNORECASE):
        board = "ICSE"
    elif re.search(r'maharashtra|msbshse', header, re.IGNORECASE):
        board = "Maharashtra State Board"
    elif re.search(r'UP\s*board|UPMSP', header, re.IGNORECASE):
        board = "UP Board"

    # Total marks (scan full header section)
    total_marks = None
    tm = re.search(
        r"(?:maximum\s*marks?|max\.?\s*marks?|m\.?\s*m\.?\s*|total\s*marks?|पूर्णांक|कुल\s*अंक|अधिकतम\s*अंक)\s*[:\-]?\s*(\d{2,3})\b",
        normalized_text[:2000], re.IGNORECASE
    )
    if tm:
        total_marks = int(tm.group(1))

    return {"grade": None, "subject": "", "board": board, "total_marks": total_marks}


def _filter_solution_key_for_chunk(paper_chunk: str, solution_text: str) -> str:
    """Finds and extracts only the relevant parts of the solution key
    that match the question numbers present in the paper chunk."""
    if not solution_text:
        return ""
    q_nums = set()
    for m in re.finditer(r"\b(?:q|question)?\s*(\d+)\b", paper_chunk, re.IGNORECASE):
        num = m.group(1)
        try:
            if 1 <= int(num) <= 100:
                q_nums.add(num)
        except ValueError:
            pass
            
    if not q_nums:
        return solution_text[:4000]
        
    paragraphs = []
    lines = solution_text.splitlines()
    current_para = []
    for line in lines:
        if not line.strip():
            if current_para:
                para_text = "\n".join(current_para)
                words = re.findall(r"\b\d+\b", para_text)
                if any(w in q_nums for w in words):
                    paragraphs.append(para_text)
                current_para = []
        else:
            current_para.append(line)
    if current_para:
        para_text = "\n".join(current_para)
        words = re.findall(r"\b\d+\b", para_text)
        if any(w in q_nums for w in words):
            paragraphs.append(para_text)
            
    filtered = "\n\n".join(paragraphs)
    if len(filtered) < 200:
        return solution_text[:3000]
    return filtered[:4000]


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
    num_chunks = len(paper_chunks)
    
    if num_chunks > 1 or combined_len > 12000:
        sol_chunks = []
        for chunk in paper_chunks:
            sol_chunk = _filter_solution_key_for_chunk(chunk, solution_key_text)
            sol_chunks.append(sol_chunk)
            
        print(f"[rubric] Groq fallback - splitting into {num_chunks} chunks to fit TPM window.")
        return _generate_rubric_chunked(question_paper_text, paper_chunks, model, sol_chunks)
        
    paper = paper_chunks[0] if paper_chunks else question_paper_text
    truncated_note = ""
    prompt = (
        "You are a senior examiner writing a DETAILED marking rubric from a question paper.\n\n"
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
        counted = len(re.findall(r"^\s*[-*#\s]*(?:Q\d+|Question\s*\d+)[\.\(\[]", out["rubric"], re.MULTILINE | re.IGNORECASE))
        if counted:
            out["questions_found"] = counted
    out["rubric"] = _correct_rubric_marks(str(out.get("rubric", "")), question_paper_text)
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
   - ⚠️ IMPORTANT: Do NOT treat general instructions, exam rules, timings, candidate guidelines, or notes (even if numbered like (i), (ii), (iii), (iv), (v) or (I), (II), (III), (IV), (V)) as questions! Only generate rubric entries for actual exam questions starting from SECTION A.
   - ⚠️ IMPORTANT: If a question has sub-parts (e.g. Question 1 has parts (i), (ii) or (a), (b)), you MUST prefix the sub-part with the parent question number. Write "Q1(i)" or "Q1(a)", NOT "Q(i)" or "Q(a)". Always maintain the correct question prefix.
2. ANSWER KEY INTEGRATION: For each question, look up the correct answers, steps, and key points from the provided teacher's Solution Key / Answer Key. Do NOT invent your own correct answers, and do not assume ideal answers.
3. STRICT ADHERENCE: Use only the teacher's answer key as the grading reference. Do not generate or assume ideal answers. Never create your own answer format or invent rubric points. Follow only the teacher-provided answer key and marking instructions. If the Answer Key lacks details or contains placeholder text, you must still only grade against what is explicitly specified by the teacher.
4. SPECIFICITY: Every rubric line MUST specify the exact question content (e.g., "Evaluate sin 60 cos 30 + sin 30 cos 60" or "Prove that 2 - sqrt(3) is irrational") and the step-by-step mark breakdown, not just generic guidelines.
5. LANGUAGE CONSISTENCY: You MUST write the rubric description, questions, and marking criteria in the SAME language as the original question from the Question Paper. If a question is written in Hindi, the rubric description and marking criteria for that question MUST be written in Hindi. Do NOT translate Hindi questions or criteria to English.
6. INTERNAL CHOICES / ALTERNATIVES: For questions with internal choices (e.g. Q22 has an option A and option B where only one is required, or Q12(क) अथवा Q12(ख) in Hindi), you MUST append 'OR' (or 'अथवा' for Hindi papers) to the question number header of the alternative choice (e.g. 'Q22(b) OR' or 'Q12(ख) OR') so the system knows it is an alternative choice and does not double-count the marks.
"""



def _generate_rubric_gemini(paper_text: str, solution_key_text: str = "") -> dict[str, Any]:
    """Single-call rubric generation via Gemini 2.5 Flash (1M context, fast).
    No chunking needed — feeds the entire paper in one prompt."""
    prompt = (
        "You are a senior examiner writing a DETAILED marking rubric from this question paper.\n\n"
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
                    response_mime_type="application/json",
                    temperature=0.2,
                    max_output_tokens=32768,
                    thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
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
            msg = str(e)
            if _is_quota(msg):
                _block_gemini()
            print(f"[rubric-gemini {model}] failed with error ({e}), trying next Gemini model...")
            continue
    raise last_err or RuntimeError("Gemini is currently disabled due to quota exhaustion")
    out["rubric"] = str(out.get("rubric", "")).strip()
    out["questions_found"] = _safe_int(out.get("questions_found") or 0)
    out["total_marks"] = _safe_int(out.get("total_marks") or 0)
    # Fallback: if model said 0 questions but rubric has Q-lines, count them
    if out["questions_found"] == 0 and out["rubric"]:
        counted = len(re.findall(r"^\s*[-*#\s]*(?:Q\d+|Question\s*\d+)[\.\(\[]", out["rubric"], re.MULTILINE | re.IGNORECASE))
        if counted:
            out["questions_found"] = counted
    return out


def _correct_rubric_marks(rubric_text: str, paper_text: str) -> str:
    """Parses and corrects inflated/incorrect marks in the rubric lines
    to match the standard question paper mark distribution."""
    if not rubric_text:
        return rubric_text
        
    # Extract marks map from paper
    marks_map = {}
    sections_info = re.findall(
        r"Section\s+([A-E])\s*–?\s*Questions?\s*(?:No\.)?\s*(\d+)\s*to\s*(\d+).*?carries?\s*(\d+)\s*marks?",
        paper_text, re.IGNORECASE
    )
    for sec, start, end, marks in sections_info:
        for q in range(int(start), int(end) + 1):
            marks_map[str(q)] = float(marks)
            
    sec_format2 = re.findall(
        r"Section\s+([A-E]).*?Q\s*(\d+)\s*-\s*Q\s*(\d+).*?(\d+)\s*marks?",
        paper_text, re.IGNORECASE
    )
    for sec, start, end, marks in sec_format2:
        for q in range(int(start), int(end) + 1):
            marks_map[str(q)] = float(marks)

    # Fallback to standard Board Class 10 Science / general pattern if not found
    if not marks_map:
        for q in range(1, 21): marks_map[str(q)] = 1.0
        for q in range(21, 27): marks_map[str(q)] = 2.0
        for q in range(27, 34): marks_map[str(q)] = 3.0
        for q in range(34, 37): marks_map[str(q)] = 5.0
        for q in range(37, 40): marks_map[str(q)] = 4.0
        
    new_lines = []
    for line in rubric_text.splitlines():
        # Match Q1 (5 marks): ... or Q22(a)(i) (3 marks): ...
        m = re.match(r"^([-\*#\s]*Q\d+)([\w\.\(\)]*)\s*\(([\d\.]+)\s*(?:marks?|अंक)\)\s*:(.*)$", line.strip(), re.IGNORECASE)
        if m:
            q_prefix = m.group(1).strip()
            sub_part = m.group(2).strip()
            old_marks = m.group(3)
            criteria = m.group(4)
            
            # Extract main question number
            q_num_match = re.search(r"\d+", q_prefix)
            if q_num_match:
                q_num = q_num_match.group(0)
                expected_marks = marks_map.get(q_num)
                if expected_marks is None:
                    continue
                
                if int(q_num) <= 20:
                    corrected_marks = 1.0
                else:
                    try:
                        om = float(old_marks)
                        if om == 5.0 or om > expected_marks:
                            corrected_marks = expected_marks
                        else:
                            corrected_marks = om
                    except ValueError:
                        corrected_marks = expected_marks
                        
                # Format float to int if whole number
                m_val = int(corrected_marks) if corrected_marks.is_integer() else corrected_marks
                line = f"{q_prefix}{sub_part} ({m_val} marks):{criteria}"
        new_lines.append(line)
        
    return "\n".join(new_lines)


def _correct_total_marks(out: dict[str, Any], paper_text: str) -> dict[str, Any]:
    """If the paper declares a Maximum/Total Marks value and the model returned a
    different total, trust the declared value. Supports any value 10-500."""
    normalized_text = _normalize_vertical_text(paper_text[:4000])
    declared_match = re.search(
        r"(?:maximum\s*marks?|max\.?\s*marks?|m\.?\s*m\.?\s*|total\s*marks?|पूर्णांक|कुल\s*अंक|अधिकतम\s*अंक)\s*[:\-]?\s*(\d{2,3})\b",
        normalized_text, re.IGNORECASE,
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


def _normalize_header_for_compare(h: str) -> str:
    # Remove any parenthetical/bracketed mark specifications from the header first
    h_clean = re.sub(r'[\(\[]\s*[\d\.\+\s=]*(?:mark|अंक|marks|अंकों)[\s\d\.\+\s=]*[\)\]]', '', h, flags=re.IGNORECASE)
    h_clean = re.sub(r'[\(\[]\s*\d+\s*[\)\]]', '', h_clean)
    
    # Strip any leading Q or QUESTION or Question (case-insensitive) prefix
    h_clean = re.sub(r'^(?:question|q)\s*', '', h_clean, flags=re.IGNORECASE)
    
    # Remove all spaces, parentheses, brackets, colons, dots, dashes
    h_clean = re.sub(r'[\s\(\)\[\]\:\.\-]', '', h_clean).upper()
    return h_clean


def _recalculate_rubric_stats(out: dict[str, Any]) -> dict[str, Any]:
    """Recalculate questions_found and total_marks programmatically by parsing the final compiled rubric.
    Avoids double-counting when both the parent question (e.g. Q37) and its subparts (e.g. Q37(a))
    are present in the rubric."""
    rubric = out.get("rubric") or ""
    if not rubric:
        return out

    # If the rubric is a string but contains serialized dictionaries, parse and merge them
    if isinstance(rubric, str) and "{" in rubric:
        import ast
        import json
        parsed_dicts = []
        start_idx = 0
        while True:
            start_idx = rubric.find("{", start_idx)
            if start_idx == -1:
                break
            brace_count = 0
            end_idx = -1
            for i in range(start_idx, len(rubric)):
                if rubric[i] == "{":
                    brace_count += 1
                elif rubric[i] == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i
                        break
            if end_idx != -1:
                dict_str = rubric[start_idx:end_idx+1]
                try:
                    d = json.loads(dict_str)
                    parsed_dicts.append(d)
                except Exception:
                    try:
                        d = ast.literal_eval(dict_str)
                        if isinstance(d, dict):
                            parsed_dicts.append(d)
                    except Exception:
                        pass
                start_idx = end_idx + 1
            else:
                start_idx += 1
        if parsed_dicts:
            merged_dict = {}
            for d in parsed_dicts:
                if isinstance(d, dict):
                    merged_dict.update(d)
            if merged_dict:
                rubric = merged_dict

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
    question_groups = {} # parent_q_key -> {"parent_marks": float, "subparts_marks": [float]}

    for line in lines:
        line = line.strip()
        # Clean markdown symbols like "-", "*", "#" and spaces from the start of the line
        line_clean = re.sub(r"^[-*#\s]+", "", line)
        # Check if it starts with Q/Question
        is_q = bool(re.match(r"^(?:q\d+|question\s*\d+|q\s*\d+)", line_clean, re.IGNORECASE))
        # Check if it starts with a number followed by an optional subpart and mark specification, e.g. "37(a) (1 mark)"
        is_num_q = bool(re.match(r'^\d+[\w\.\(\)\[\]\s\-]*[\(\[]\s*\d+\s*(?:marks?|अंक)', line_clean, re.IGNORECASE))
        
        if not (is_q or is_num_q):
            continue

        colon_idx = line_clean.find(":")
        header = line_clean[:colon_idx].strip() if colon_idx != -1 else line_clean
        desc = line_clean[colon_idx+1:].strip() if colon_idx != -1 else ""
        
        # Skip alternative/choice questions to avoid double-counting.
        # Check both header and the start of the description for choice keywords (OR, अथवा, या).
        is_choice = False
        if re.search(r'\bOR\b|\bअथवा\b|^\s*या\s*|\s+या\s+', header, re.IGNORECASE):
            is_choice = True
        elif desc and re.match(r'^(?:OR|अथवा|या)\b', desc, re.IGNORECASE):
            is_choice = True
            
        if is_choice:
            continue
        
        # Extract parent question number, e.g. "Q1" from "Q1(i)" or "Question 1"
        m_main = re.match(r'^(?:q\d+|question\s*\d+|q\s*\d+)', header, re.IGNORECASE)
        m_num = re.match(r'^(\d+)', header)
        
        if m_main:
            # Normalize parent question to upper case (e.g. Q1)
            parent_q_key = m_main.group(0).upper().replace("UESTION", "").replace(" ", "")
            if not parent_q_key.startswith("Q"):
                parent_q_key = "Q" + re.sub(r'\D', '', parent_q_key)
        elif m_num:
            parent_q_key = f"Q{m_num.group(1)}"
        else:
            parent_q_key = header.upper().replace(" ", "")

        main_questions.add(parent_q_key)

        # Parse marks for this line
        parsed_marks = 0.0
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
                            parsed_marks = float(m_eq.group(1))
                            break
                        except ValueError:
                            pass
                
                # Match digit followed by mark word: "1 mark", "2 अंक", etc.
                m_num_val = re.search(r'([\d\.]+)\s*(?:mark|अंक)', content)
                if m_num_val:
                    try:
                        parsed_marks = float(m_num_val.group(1))
                        break
                    except ValueError:
                        pass
                
                # Match subparts sum: "1+1 marks" or similar
                numbers = re.findall(r'[\d\.]+', content)
                if numbers and "+" in content:
                    try:
                        parsed_marks = sum(float(n) for n in numbers)
                        break
                    except ValueError:
                        pass
            else:
                # Fallback: check if the content is purely a number (e.g. [2] or (2)), ignoring Roman/alpha labels
                if re.match(r'^[\d\.]+$', content):
                    try:
                        parsed_marks = float(content)
                        break
                    except ValueError:
                        pass

        # Determine if this line is the parent question itself or a subpart
        h_norm = _normalize_header_for_compare(header)
        p_norm = _normalize_header_for_compare(parent_q_key)
        
        if parent_q_key not in question_groups:
            question_groups[parent_q_key] = {"parent_marks": 0.0, "subparts_marks": [], "subparts_lines": []}
            
        if h_norm == p_norm:
            question_groups[parent_q_key]["parent_marks"] = parsed_marks
        else:
            question_groups[parent_q_key]["subparts_marks"].append(parsed_marks)
            question_groups[parent_q_key]["subparts_lines"].append(line_clean)

    from grading_prompts import _sum_rubric_marks
    calculated_total_marks = _sum_rubric_marks(rubric)

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
            f"You are reading PART {i+1}/{len(chunks)} of a multi-page question paper.\n\n"
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
            "  - Default to 1 mark for any question if mark allocation is not explicitly visible (such as Multiple Choice Questions (MCQs)).\n"
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
                    {"role": "system", "content": "You are a professional exam grader. You must output a JSON object containing the rubric lines. Never include any conversational preamble, explanation, notes, or code blocks. Output STRICT JSON conforming to the schema requested."},
                    {"role": "user",   "content": chunk_prompt},
                ],
                temperature=0.2,
                max_tokens=1000,
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
            _time.sleep(8)
    out = {
        "rubric": "\n".join(all_lines),
        "questions_found": total_q,
        "total_marks": total_marks,
    }
    out = _recalculate_rubric_stats(out)
    return _correct_total_marks(out, full_text)


def detect_scope(student_answer: str, rubric: str = "") -> dict[str, Any]:
    """Auto-detect Grade + subject + chapter from a student answer + rubric.
    Cheap Groq call (~0.5s). Returns {grade, subject, chapter, confidence, reason}.

    The RUBRIC is the strongest signal — it's what the teacher wrote. Question
    PATTERNS matter more than topic words (a passage ABOUT renewable energy in
    an English paper is still ENGLISH, not Science).
    """
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    prompt = (
        "You are a classifier. Infer the paper's grade, subject, and chapter.\n\n"
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


def _compress_system_prompt(prompt: str) -> str:
    # 1. Strip the long format rules
    format_rules_start = prompt.find("ANSWER FORMAT RULES")
    if format_rules_start != -1:
        brief_format = (
            "ANSWER FORMAT RULES:\n"
            "- Plain text / Bullets: Grade normally on concepts.\n"
            "- Diagrams: Grade labels, not artistic quality.\n"
            "- Tables: Grade cells contents, ignore style.\n"
            "- Math: Award ECF (Error Carried Forward) and Formula credit.\n"
            "- Spelling/Grammar: Do NOT penalize spelling/grammar for non-language subjects.\n"
        )
        resume_idx = prompt.find("GENERAL EVALUATION RULES")
        if resume_idx == -1:
            resume_idx = prompt.find("ANTI-HALLUCINATION RULES")
        if resume_idx != -1:
            prompt = prompt[:format_rules_start] + brief_format + "\n" + prompt[resume_idx:]
            
    # 2. Strip language rules if present
    lang_start = prompt.find("MULTI-LANGUAGE GRADING")
    if lang_start != -1:
        resume_idx = prompt.find("GENERAL EVALUATION RULES", lang_start)
        if resume_idx == -1:
            resume_idx = prompt.find("ANTI-HALLUCINATION RULES", lang_start)
        if resume_idx != -1:
            prompt = prompt[:lang_start] + "LANGUAGES: Grade content/concepts, ignore dialect/translation slips.\n\n" + prompt[resume_idx:]
            
    # 3. Strip tone rules if present
    tone_start = prompt.find("TONE RULES")
    if tone_start != -1:
        resume_idx = prompt.find("Marking rubric the teacher provided:", tone_start)
        if resume_idx != -1:
            prompt = prompt[:tone_start] + "TONE RULES: Be supportive and grade-appropriate. No emojis.\n\n" + prompt[resume_idx:]
            
    return prompt


def _filter_student_answer_for_chunk(chunk_q_nums: set[str], student_answer: str) -> str:
    """Extracts only the paragraphs/sections from the student answer sheet
    that correspond to the question numbers in the current grading chunk."""
    if not student_answer or not chunk_q_nums:
        return student_answer
        
    paragraphs = []
    lines = student_answer.splitlines()
    current_para = []
    
    for line in lines:
        if not line.strip():
            if current_para:
                para_text = "\n".join(current_para)
                words = re.findall(r"\b\d+\b", para_text)
                if any(w in chunk_q_nums for w in words):
                    paragraphs.append(para_text)
                current_para = []
        else:
            current_para.append(line)
            
    if current_para:
        para_text = "\n".join(current_para)
        words = re.findall(r"\b\d+\b", para_text)
        if any(w in chunk_q_nums for w in words):
            paragraphs.append(para_text)
            
    filtered = "\n\n".join(paragraphs)
    if len(filtered) < 200:
        return student_answer[:8000]
    return filtered[:12000]


def _normalize_q_label(label: str) -> str:
    lbl = re.sub(r'\s+', '', str(label)).lower()
    lbl = lbl.strip().rstrip('.:')
    return re.sub(r'^(?:question|q\.?)(?=\d)', '', lbl)

def grade_text(system_prompt: str, student_answer: str) -> dict[str, Any]:
    from grading_prompts import _sum_rubric_marks
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    # Extract the rubric from the system prompt
    rubric = ""
    rubric_match = re.search(r'Marking rubric the teacher provided:\s*"""(.*?)"""', system_prompt, re.DOTALL)
    if rubric_match:
        rubric = rubric_match.group(1).strip()
        
    # Check if we should chunk
    if rubric:
        q_count = len(re.findall(r"^\s*(?:[Qq]\d+|question\s*\d+|[Qq]\s*\d+|\b\d+[\.\)])", rubric, re.MULTILINE))
    else:
        q_count = len(re.findall(r"^\s*Q\d", system_prompt, re.MULTILINE))
    
    # If the number of questions is small (<= 8), we can run a single call
    if q_count <= 8:
        run_prompt = system_prompt
        estimated_total = (len(system_prompt) + len(student_answer)) // 4
        if estimated_total > 5000:
            print(f"[grader] Compressing single-call system prompt (estimated total tokens: {estimated_total}).")
            run_prompt = _compress_system_prompt(system_prompt)
            
        max_tokens = max(1500, q_count * 200 + 800)
        content = _groq_chat_with_retry(
            model,
            messages=[
                {"role": "system", "content": run_prompt},
                {"role": "user",   "content": f"Student answer:\n\n{student_answer}"},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return _extract_json(content)
        
    student_tokens = len(student_answer) // 4
    # Set an optimal chunk size of 20 questions.
    chunk_size = 20
        
    # Otherwise, chunk the grading question-by-question to avoid TPM rate limits (413)
    print(f"[grader] Splitting {q_count} questions into chunks of {chunk_size} to fit Groq TPM limit (estimated answer tokens: {student_tokens}).")
    
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
    
    # Split the rubric into chunks
    rubric_lines = rubric.splitlines()
    chunks = []
    current_chunk = []
    chunk_q_count = 0
    for line in rubric_lines:
        current_chunk.append(line)
        line_clean = re.sub(r"^[-*#\s]+", "", line)
        if re.match(r"^(?:q\d+|question\s*\d+|q\s*\d+|\b\d+[\.\)])", line_clean, re.IGNORECASE):
            chunk_q_count += 1
            if chunk_q_count >= chunk_size:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                chunk_q_count = 0
    if current_chunk:
        chunks.append("\n".join(current_chunk))
        
    from concurrent.futures import ThreadPoolExecutor
    
    def process_chunk(idx, r_chunk):
        print(f"[grader] Starting chunk {idx+1}/{len(chunks)} in parallel...")
        # Reconstruct the system prompt for this chunk
        chunk_system_prompt = system_prompt.replace(rubric, r_chunk)
        
        # 1. Update the MARKS TOTAL RULE constraint to match the chunk total
        chunk_total = _sum_rubric_marks(r_chunk)
        if chunk_total > 0:
            pattern = r"(MARKS TOTAL RULE: The rubric above has marks that sum to )\d+(\..*?MUST equal )\d+(\..*?sum to exactly this number\.)"
            replacement = rf"\g<1>{chunk_total}\g<2>{chunk_total}\g<3>"
            chunk_system_prompt = re.sub(pattern, replacement, chunk_system_prompt)
            
        # 2. Filter the "Question marks (enforce exactly):" block to only include current chunk questions
        chunk_q_labels = set()
        for line in r_chunk.splitlines():
            line_clean = re.sub(r"^[-*#\s]+", "", line).strip()
            m = re.match(r"^((?:q\d+|question\s*\d+|q\s*\d+|\b\d+)[\w\.\(\)]*)\b", line_clean, re.IGNORECASE)
            if m:
                chunk_q_labels.add(_normalize_q_label(m.group(1)))
                
        lines_sp = chunk_system_prompt.splitlines()
        new_lines_sp = []
        in_q_marks_block = False
        for line in lines_sp:
            if "Question marks (enforce exactly):" in line:
                in_q_marks_block = True
                new_lines_sp.append(line)
                continue
            if in_q_marks_block:
                if line.strip() == "" or not line.startswith("  "):
                    in_q_marks_block = False
                    new_lines_sp.append(line)
                else:
                    m = re.match(r"^\s+((?:q\d+|question\s*\d+|q\s*\d+|\b\d+)[\w\.\(\)]*)\b", line, re.IGNORECASE)
                    if m and _normalize_q_label(m.group(1)) in chunk_q_labels:
                        new_lines_sp.append(line)
            else:
                new_lines_sp.append(line)
        chunk_system_prompt = "\n".join(new_lines_sp)

        chunk_system_prompt += (
            "\n\nCRITICAL CHUNKING RULE:\n"
            "You are grading a subset of the exam. You MUST ONLY grade and return JSON entries "
            "for the questions that are explicitly listed in the Marking Rubric above. "
            "Do NOT include any other questions in the `per_question` list. "
            "Do NOT add mistakes or feedback for any questions not in the rubric above."
        )
        
        # Compress system prompt to fit Groq limit
        compressed_system_prompt = _compress_system_prompt(chunk_system_prompt)
        
        # Enforce chunk-specific marks total rule
        chunk_marks = len(re.findall(r"^\s*Q\d", r_chunk, re.MULTILINE))
        max_tokens = max(1500, chunk_marks * 200 + 800)
        
        # Dynamically extract question numbers from this rubric chunk
        chunk_q_nums = set()
        for m in re.finditer(r"\b(?:q|question)?\s*(\d+)\b", r_chunk, re.IGNORECASE):
            chunk_q_nums.add(m.group(1))
            
        chunk_student_answer = _filter_student_answer_for_chunk(chunk_q_nums, student_answer)
        
        # Pacing start of requests slightly to prevent concurrent 429 RPM limit triggers
        _time.sleep(idx * 1.5)
        
        try:
            content = _groq_chat_with_retry(
                model,
                messages=[
                    {"role": "system", "content": compressed_system_prompt},
                    {"role": "user",   "content": f"Student answer:\n\n{chunk_student_answer}"},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            res = _extract_json(content)
            
            # Filter chunk results to only include questions present in this chunk's rubric
            if res and "per_question" in res:
                filtered_pq = []
                for pq_item in res["per_question"]:
                    q_label = str(pq_item.get("q") or "").strip()
                    q_norm = _normalize_q_label(q_label)
                    parent_q = re.match(r"^(\d+)", q_norm).group(1) if re.match(r"^(\d+)", q_norm) else q_norm
                    if q_norm in chunk_q_labels or parent_q in chunk_q_labels:
                        filtered_pq.append(pq_item)
                res["per_question"] = filtered_pq
                
            print(f"[grader] Chunk {idx+1}/{len(chunks)} completed successfully.")
            return res
        except Exception as e:
            print(f"[grader] Chunk {idx+1} failed: {e}")
            # Add a dummy fallback result for this chunk so we don't crash
            dummy_questions = []
            for line in r_chunk.splitlines():
                line_clean = re.sub(r"^[-*#\s]+", "", line)
                m = re.match(r"^((?:q\d+|question\s*\d+|q\s*\d+|\b\d+)[\w\.]*)\b", line_clean, re.IGNORECASE)
                if m:
                    dummy_questions.append({
                        "q": m.group(1),
                        "marks_awarded": 0,
                        "marks_total": 1,
                        "feedback": f"Grading failed for this question chunk: {e}",
                        "format": "text"
                    })
            return {
                "per_question": dummy_questions,
                "mistakes": [{"type": "other", "description": f"Grading chunk {idx+1} failed: {e}"}]
            }

    with ThreadPoolExecutor(max_workers=min(5, len(chunks))) as executor:
        results = list(executor.map(lambda pair: process_chunk(pair[0], pair[1]), enumerate(chunks)))
            
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
    
    # Deduplicate strengths
    seen_strengths = set()
    unique_strengths = []
    for s in list(all_strengths):
        s_clean = s.strip().lower()
        if s_clean not in seen_strengths:
            seen_strengths.add(s_clean)
            unique_strengths.append(s)
    merged["strengths"] = unique_strengths
    
    # Deduplicate suggestions
    if suggestions:
        unique_sugs = []
        for s in suggestions:
            s_clean = s.strip()
            if s_clean and s_clean not in unique_sugs:
                unique_sugs.append(s_clean)
        merged["suggestion"] = " ".join(unique_sugs)
        
    if cheat_suspicions:
        merged["ai_cheat_suspicion"] = int(sum(cheat_suspicions) / len(cheat_suspicions))
        
    # Deduplicate mistakes
    seen_mistakes = set()
    unique_mistakes = []
    for m in merged["mistakes"]:
        key = (m.get("type", ""), m.get("description", "").strip().lower())
        if key not in seen_mistakes:
            seen_mistakes.add(key)
            unique_mistakes.append(m)
    merged["mistakes"] = unique_mistakes
        
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
        "You are a teacher reviewing common mistakes across a class. "
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
        f"For a Grade {grade_level} ({tier}) student studying \"{subject}\" — "
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



def verify_grade(student_answer: str, rubric: str, grade_result: dict[str, Any]) -> dict[str, Any]:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    critic = (
        "You are a skeptical senior examiner reviewing another examiner's "
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
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-pro",
]


_gemini_quota_blocked_until = 0.0

def _is_gemini_blocked() -> bool:
    global _gemini_quota_blocked_until
    import time
    return time.time() < _gemini_quota_blocked_until

def _block_gemini():
    global _gemini_quota_blocked_until
    import time
    _gemini_quota_blocked_until = time.time() + 1800  # Block Gemini for 30 minutes
    print("[gemini] Quota exhausted (429). Disabling Gemini for the next 30 minutes to save quota and speed up fallback.")

def _gemini_model_chain() -> list[str]:
    if _is_gemini_blocked():
        return []
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

    model = os.getenv("GROQ_VISION_MODEL", "").strip() or "llama-3.2-11b-vision-preview"
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
                last_err = e
                print(f"[gemini-ocr {model}] failed with error ({e}), trying next model...")
                break
        print(f"[gemini-ocr] falling through from {model} -> next model")

    raise RuntimeError(f"All Gemini models overloaded for OCR. Last error: {last_err}")


class QuotaExceeded(Exception):
    """Raised when daily Gemini quota is exhausted (429)."""


async def transcribe_chunk_async(images_chunk: list[tuple[bytes, str]], start_page: int) -> str:
    """Asynchronously transcribe a chunk of pages using Gemini 2.5 Flash."""
    parts = []
    for idx, (b, m) in enumerate(images_chunk):
        parts.append(gtypes.Part.from_bytes(data=b, mime_type=m))
        parts.append(f"Above is page {start_page + idx} of the student answer sheet.")
    
    prompt = (
        "You are an expert OCR transcription engine.\n"
        "Your task is to transcribe all handwritten and printed text from the provided page images.\n"
        "Follow these rules strictly:\n"
        "1. Transcribe EVERYTHING verbatim, word-for-word. Do NOT summarize, paraphrase, or omit any text, questions, formula, or calculations.\n"
        "2. For each page, start with a clear header like '--- PAGE X ---' (matching the page number).\n"
        "3. If a page has diagrams, represent them as [DIAGRAM: brief description. Labels: ...].\n"
        "4. Output the transcript inside a JSON block with this exact schema:\n"
        "{\n"
        "  \"transcript\": \"...\"\n"
        "}\n"
    )
    parts.append(prompt)
    
    def call():
        client = _gemini()
        model_str = os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.0-flash"
        rsp = client.models.generate_content(
            model=model_str,
            contents=parts,
            config=gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=4000,
            ),
        )
        return _extract_json(rsp.text or "")

    try:
        res = await asyncio.to_thread(call)
        return res.get("transcript", "")
    except Exception as e:
        print(f"Error transcribing chunk starting at page {start_page}: {e}")
        return f"\n--- PAGE {start_page} (Transcription Error) ---\n"


async def transcribe_all_pages_concurrently(images: list[tuple[bytes, str]], chunk_size: int = 4) -> str:
    """Run concurrent transcription for all pages in small chunks."""
    tasks = []
    for i in range(0, len(images), chunk_size):
        chunk = images[i:i+chunk_size]
        start_page = i + 1
        tasks.append(transcribe_chunk_async(chunk, start_page))
    
    transcripts = await asyncio.gather(*tasks)
    return "\n\n".join(transcripts)


def _try_gemini_vision_model(model: str, parts, retries: int = 1, base_wait: float = 1.0):
    """Call one vision model with at most 1 quick retry. On overload,
    fall through to the next model immediately."""
    import time
    last_err = None
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(base_wait)
        try:
            rsp = _gemini().models.generate_content(
                model=model,
                contents=parts,
                config=gtypes.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                    max_output_tokens=8192,
                ),
            )
            return True, _extract_json(rsp.text or "")
        except Exception as e:
            msg = str(e)
            last_err = e
            if _is_quota(msg):
                _block_gemini()
                raise QuotaExceeded(
                    "Gemini daily quota exhausted for this API key. "
                    "Either wait until quota resets or use a fresh key."
                )
            if not _is_overloaded(msg):
                return False, e
            print(f"[gemini-vision {model}] overloaded (attempt {attempt + 1}/{retries + 1}), backing off…")
    return False, last_err


def _grade_with_groq_vision_fallback(system_prompt: str,
                                    images: list[tuple[bytes, str]]) -> dict[str, Any]:
    """Fallback grading via Groq Llama-4-Scout vision when all Gemini models fail.
    Up to 5 images per call. Returns the same JSON schema as Gemini path."""
    import base64

    model = os.getenv("GROQ_VISION_MODEL", "").strip() or "llama-3.2-11b-vision-preview"
    content: list[dict[str, Any]] = [{"type": "text", "text": system_prompt}]
    for img_bytes, mime in images[:5]:  # Groq caps at ~5 images per call
        b64 = base64.b64encode(img_bytes).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime or 'image/png'};base64,{b64}"},
        })

    # Estimate tokens needed based on question count in prompt (~100 tokens per question)
    q_count = len(re.findall(r"Q\d+", system_prompt))
    groq_max_tokens = max(3000, q_count * 100 + 1500)
    rsp = _groq().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.2,
        max_tokens=groq_max_tokens,
        response_format={"type": "json_object"},
    )
    return _extract_json(rsp.choices[0].message.content or "")


def grade_handwriting(system_prompt: str,
                      images: list[tuple[bytes, str]]) -> dict[str, Any]:
    """Evaluate a handwritten answer with retries + fallback model chain.

    Tries primary model with 2 retries (2s, 4s). If still overloaded, falls
    through to the next model in the chain. Quota errors (429) surface immediately.
    """
    if not images:
        raise ValueError("No images provided")

    parts = [gtypes.Part.from_bytes(data=b, mime_type=m) for b, m in images]
    if len(images) > 1:
        parts.append(f"NOTE: The image(s) above are {len(images)} pages of one answer sheet, in order. Treat them as a single submission.")
    parts.append(system_prompt)

    last_err = None
    quota_hit = False
    skip_gemini = _is_gemini_blocked()
    if skip_gemini:
        print(f"[gemini] quota block active — skipping straight to Groq")
        
    for model in (() if skip_gemini else _gemini_model_chain()):
        try:
            ok, result = _try_gemini_vision_model(model, parts)
        except QuotaExceeded as e:
            quota_hit = True
            last_err = e
            break
        if ok:
            if model != _gemini_model_chain()[0]:
                if isinstance(result, dict):
                    result["_fallback_model_used"] = model
            return result
        last_err = result
        print(f"[gemini] falling through from {model} -> next in chain")

    print(f"[gemini] {'skipped (quota block active)' if skip_gemini else ('quota exhausted' if quota_hit else 'all Gemini models failed')} — using Groq Llama-4-Scout vision")
    groq_err = None
    try:
        result = _grade_with_groq_vision_fallback(system_prompt, images)
        if isinstance(result, dict):
            result["_fallback_model_used"] = os.getenv(
                "GROQ_VISION_MODEL", "").strip() or "llama-3.2-11b-vision-preview"
        return result
    except Exception as e:
        groq_err = e
        print(f"[groq vision] failed ({e}) — trying Gemini one more time as final fallback")

    # Final fallback: Gemini again (in case its quota cleared or earlier failure was transient).
    try:
        last_model = _gemini_model_chain()[-1]
        ok, result = _try_gemini_vision_model(last_model, parts, retries=0)
        if ok:
            if isinstance(result, dict):
                result["_fallback_model_used"] = f"{last_model} (retry after Groq failure)"
            global _gemini_quota_blocked_until
            _gemini_quota_blocked_until = 0.0  # clear circuit-breaker since it worked
            return result
        raise result if isinstance(result, Exception) else RuntimeError(str(result))
    except Exception as ge:
        raise RuntimeError(
            f"All providers failed. Gemini: {last_err}. Groq vision: {groq_err}. Gemini retry: {ge}"
        )
