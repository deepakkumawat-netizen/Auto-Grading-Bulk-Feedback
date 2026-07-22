import os
import sys
import io
import asyncio
from typing import Dict, Any, List, Optional
from typing_extensions import TypedDict
from pypdf import PdfReader

# Add backend directory to path if needed for imports
sys.path.insert(0, os.path.dirname(__file__))

from langgraph.graph import StateGraph, START, END

class GradingState(TypedDict):
    # Inputs
    filename: str
    raw_bytes: bytes
    rubric: str
    verify: bool
    declared_total: int
    do_study_plan: bool
    grade_override: int
    subject_override: str
    exam_config: Optional[Dict[str, Any]]
    handwriting_audit: bool

    # Intermediate fields
    grade_level: int
    subject: str
    chapter: str
    answer_text: str
    full_answer_text: str
    extraction_hint: str
    pre_flight_checks: Dict[str, Any]
    system_prompt: str
    grade_result: Dict[str, Any]
    math_check: Dict[str, Any]
    verifier_result: Dict[str, Any]
    study_plan_result: List[str]
    images: List[tuple[bytes, str]]
    error: Optional[str]

    # Output
    final_output: Dict[str, Any]


async def ocr_and_preflight_node(state: GradingState) -> Dict[str, Any]:
    from main import _read_sheet, _fast_scope, pre_flight_check, _render_pdf_to_pngs, _MAX_ANSWER_CHARS
    
    filename = state["filename"]
    raw = state["raw_bytes"]
    rubric = state["rubric"]
    grade_override = state["grade_override"]
    subject_override = state["subject_override"]
    exam_config = state["exam_config"]
    handwriting_audit = state.get("handwriting_audit", False)
    
    # ── Image Pages Extraction for Handwriting Audit ────────────────────────
    images = []
    filename_lower = filename.lower()
    if filename_lower.endswith(".pdf"):
        try:
            # max_pages=15 previously silently dropped every page past 15 from
            # a scanned answer sheet — long board-exam papers (20-30+ pages)
            # lost their later questions entirely before OCR ever saw them,
            # grading them as "not attempted". 40 matches the other OCR paths
            # (_extract_question_paper_unified / _extract_solved_paper_unified).
            page_blobs = _render_pdf_to_pngs(raw, max_pages=40, dpi=110)
            images = [(b, "image/jpeg") for b in page_blobs]
        except Exception as e:
            print(f"[grading_graph] Failed to render PDF to images: {e}")
    elif filename_lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
        mime = "image/png" if filename_lower.endswith(".png") else "image/jpeg"
        images = [(raw, mime)]

    # ── OCR / Concurrent Transcription ──────────────────────────────────────
    answer_text = ""
    full_answer_text = ""
    if handwriting_audit and images:
        from llm_router import transcribe_all_pages_concurrently
        answer_text = await transcribe_all_pages_concurrently(images)
        full_answer_text = answer_text

    is_corrupted_transcription = (
        not answer_text or
        "Transcription Error" in answer_text or
        len(answer_text.strip()) < 50
    )
    if not handwriting_audit or not images or is_corrupted_transcription:
        native_text = await asyncio.to_thread(_read_sheet, filename, raw)
        if native_text and not native_text.startswith("["):
            # Keep the FULL extracted text (before any length cap) for consumers
            # that filter/slice it by a downstream criterion — e.g. copy_check
            # splits answer text by question number. Capping before that split
            # would silently drop content past the cutoff on just one side of a
            # comparison. The grading LLM calls below use a capped copy instead,
            # since Gemini needs a bounded context regardless of question
            # boundaries — this mirrors the cap _read_sheet used to apply
            # internally, just moved to after the full text is captured.
            full_answer_text = native_text
            if len(native_text) > _MAX_ANSWER_CHARS:
                answer_text = (native_text[:_MAX_ANSWER_CHARS] +
                                f"\n\n[note: truncated for grading — original answer was {len(native_text)} chars]")
            else:
                answer_text = native_text
        elif not answer_text:
            answer_text = native_text or "[ocr failed]"
            full_answer_text = answer_text

    extraction_hint = ""
    error = None
    
    # Define fallback scope variables in case of quick return
    temp_scope = _fast_scope(rubric, answer_text if not answer_text.startswith("[") else "")
    if grade_override > 0:
        temp_scope["grade"] = grade_override
    if subject_override:
        temp_scope["subject"] = subject_override
    
    grade_level = temp_scope["grade"] or 10
    subject = temp_scope["subject"] or "General"
    chapter = temp_scope["chapter"] or ""

    if not answer_text or (answer_text.startswith("[") and answer_text.startswith(
            ("[question_paper_only]", "[vision_failed]", "[pdf extract failed]", "[ocr failed]"))):
        if not answer_text:
            error = "File appears to be empty"
        elif answer_text.startswith("[question_paper_only]"):
            error = "Skipped: This file appears to be a question paper itself with NO handwritten student answers."
        elif answer_text.startswith("[vision_failed]"):
            error = f"Vision OCR failed: {answer_text[len('[vision_failed] '):]}"
        else:
            error = f"OCR extraction failed: {answer_text}"
            
    if error:
        return {
            "answer_text": answer_text,
            "full_answer_text": full_answer_text,
            "error": error,
            "grade_level": grade_level,
            "subject": subject,
            "chapter": chapter,
            "extraction_hint": extraction_hint,
            "images": images
        }
        
    checks = pre_flight_check(answer_text, filename, rubric)
    
    # Validate overrides
    grade_level = grade_override or grade_level or 10
    subject = subject_override or subject or "General"
    
    from grading_prompts import bulk_grader_prompt
    system_prompt = bulk_grader_prompt(grade_level, subject, chapter, rubric, exam_config=exam_config, handwriting_audit=handwriting_audit)
    
    return {
        "answer_text": answer_text,
        "full_answer_text": full_answer_text,
        "grade_level": grade_level,
        "subject": subject,
        "chapter": chapter,
        "extraction_hint": extraction_hint,
        "pre_flight_checks": checks,
        "system_prompt": system_prompt,
        "images": images
    }
 
 
async def grade_node(state: GradingState) -> Dict[str, Any]:
    if state.get("error"):
        return {}
        
    from main import grade_text, _sum_rubric_marks
    
    system_prompt = state["system_prompt"]
    answer_text = state["answer_text"]
    rubric = state["rubric"]
    declared_total = state["declared_total"]
    handwriting_audit = state.get("handwriting_audit", False)
    images = state.get("images", [])
    
    try:
        if handwriting_audit and images:
            from llm_router import grade_handwriting
            try:
                result = await asyncio.to_thread(grade_handwriting, system_prompt, images)
            except Exception as e:
                print(f"[grade_node] Vision grading failed ({e}). Falling back to text-based grading...")
                result = await asyncio.to_thread(grade_text, system_prompt, answer_text)
                if isinstance(result, dict):
                    if "handwriting_clarity" not in result:
                        result["handwriting_clarity"] = 0
                    if "handwriting_audit" not in result:
                        result["handwriting_audit"] = {
                            "spacing_score": 0, "clarity_score": 0, "alignment_score": 0, "completeness_score": 0,
                            "overall_comments": "Handwriting quality audit skipped (using text fallback)."
                        }
        else:
            result = await asyncio.to_thread(grade_text, system_prompt, answer_text)
        result["detected_scope"] = {"grade": state["grade_level"], "subject": state["subject"], "chapter": state["chapter"]}
        
        # Recalculate and scale marks
        pq = result.get("per_question") or []
        rubric_total = _sum_rubric_marks(rubric)
        if pq:
            import re
            
            parsed_items = []
            for item in pq:
                q_label = str(item.get("q") or "").strip()
                parts = []
                m_main = re.match(r'^(?:Q(?:uestion)?\s*(\d+)|\b\d+\b)', q_label, re.IGNORECASE)
                if m_main:
                    main_val = m_main.group(0).upper().replace("UESTION", "").replace(" ", "")
                    if not main_val.startswith("Q"):
                        main_val = "Q" + main_val
                    parts.append(main_val)
                    rest = q_label[m_main.end():]
                    sub_parts = re.findall(r'[\(\[]([^\]\)]+)[\)\]]|\b([a-zA-Z0-9]+)\b', rest)
                    for sp in sub_parts:
                        val = (sp[0] or sp[1] or "").strip()
                        if val:
                            parts.append(val)
                else:
                    parts.append(q_label.upper())
                
                parsed_items.append({
                    "parts": parts,
                    "marks_awarded": float(item.get("marks_awarded", 0) or 0),
                    "marks_total": float(item.get("marks_total", 0) or 0)
                })
                
            def aggregate_node(node_path, items):
                remaining_items = [it for it in items if len(it["parts"]) > len(node_path)]
                if not remaining_items:
                    return sum(it["marks_total"] for it in items), sum(it["marks_awarded"] for it in items)
                    
                groups = {}
                for it in remaining_items:
                    next_part = it["parts"][len(node_path)]
                    if next_part not in groups:
                        groups[next_part] = []
                    groups[next_part].append(it)
                    
                has_or = False
                if len(groups) > 1:
                    for key in groups.keys():
                        if key.upper() == "OR" or "OR" in key.upper():
                            has_or = True
                            break
                        if re.match(r'^[A-Z]$', key):
                            has_or = True
                            break
                            
                child_results = []
                for key, group_items in groups.items():
                    child_results.append(aggregate_node(node_path + [key], group_items))
                    
                if has_or:
                    block_total = max(r[0] for r in child_results) if child_results else 0
                    block_awarded = max(r[1] for r in child_results) if child_results else 0
                else:
                    block_total = sum(r[0] for r in child_results)
                    block_awarded = sum(r[1] for r in child_results)
                    
                return block_total, block_awarded

            main_groups = {}
            for it in parsed_items:
                if it["parts"]:
                    main_key = it["parts"][0]
                    if main_key not in main_groups:
                        main_groups[main_key] = []
                    main_groups[main_key].append(it)
                    
            computed_total = 0.0
            computed_awarded = 0.0
            for main_key, items in main_groups.items():
                q_total, q_awarded = aggregate_node([main_key], items)
                computed_total += q_total
                computed_awarded += q_awarded
            
            if computed_total > 0 or rubric_total > 0:
                if declared_total > 0:      final_total = declared_total
                elif rubric_total > 0:      final_total = rubric_total
                else:                       final_total = computed_total
                
                # Scale if computed total does not match final total
                if computed_total > 0 and abs(computed_total - final_total) > 0.01:
                    scaled_awarded = (computed_awarded / computed_total) * final_total
                    final_awarded = round(scaled_awarded * 2) / 2
                    if final_awarded.is_integer():
                        final_awarded = int(final_awarded)
                else:
                    final_awarded = min(computed_awarded, final_total)
                    if isinstance(final_awarded, float) and final_awarded.is_integer():
                        final_awarded = int(final_awarded)
                
                # Format final total as int if integer
                if isinstance(final_total, float) and final_total.is_integer():
                    final_total = int(final_total)

                result["marks_total"]   = final_total
                result["marks_awarded"] = final_awarded
                result["percentage"]    = round(final_awarded / final_total * 100, 1)
                
        return {"grade_result": result}
    except Exception as e:
        return {"error": f"LLM grading failed: {e}"}


async def math_check_node(state: GradingState) -> Dict[str, Any]:
    if state.get("error"):
        return {}
        
    from main import verify_math
    try:
        math_check = await asyncio.to_thread(verify_math, state["answer_text"])
        return {"math_check": math_check}
    except Exception:
        return {"math_check": {"expressions_found": 0, "errors": [], "passed": 0, "verified": True}}


async def verifier_node(state: GradingState) -> Dict[str, Any]:
    if state.get("error"):
        return {}
        
    if not state["verify"]:
        return {"verifier_result": {"agrees": True, "comment": "Verifier skipped"}}
        
    from main import verify_grade
    try:
        verifier_result = await asyncio.to_thread(
            verify_grade, state["answer_text"], state["rubric"], state["grade_result"]
        )
        return {"verifier_result": verifier_result}
    except Exception as e:
        print(f"[verifier] failed for '{state.get('filename')}': {e}")
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
            comment = "Verifier skipped — Gemini API quota was temporarily exceeded. The original grade stands unreviewed."
        else:
            comment = "Verifier could not run due to a temporary error. The original grade stands unreviewed."
        return {"verifier_result": {"agrees": True, "comment": comment}}


async def study_plan_node(state: GradingState) -> Dict[str, Any]:
    if state.get("error"):
        return {}
        
    pct = state["grade_result"].get("percentage", 100)
    has_mistakes = bool(state["grade_result"].get("mistakes"))
    
    if not state["do_study_plan"] or not has_mistakes or pct >= 80:
        return {"study_plan_result": []}
        
    from main import make_study_plan
    try:
        study_plan_result = await asyncio.to_thread(
            make_study_plan,
            state["grade_result"],
            state["grade_level"],
            state["subject"],
            state["chapter"]
        )
        return {"study_plan_result": study_plan_result}
    except Exception:
        return {"study_plan_result": []}


def polish_node(state: GradingState) -> Dict[str, Any]:
    from main import _make_fallback_result, polish_feedback_dict, build_tier_label
    
    if state.get("error"):
        fallback = _make_fallback_result(
            state["filename"],
            state["error"],
            state.get("full_answer_text") or state.get("answer_text", ""),
            state.get("grade_level", 10),
            state.get("subject", "General"),
            state.get("chapter", ""),
            state["rubric"],
            state["exam_config"],
            state.get("pre_flight_checks")
        )
        return {"final_output": fallback}
        
    grade_level = state["grade_level"]
    grade_result = state["grade_result"]
    math_check = state.get("math_check", {})
    verifier_result = state.get("verifier_result", {"agrees": True, "comment": "Verifier skipped"})
    study_plan_result = state.get("study_plan_result", [])
    
    # Merge results
    grade_result["math_check"] = math_check
    grade_result["verifier"] = verifier_result
    
    if not verifier_result.get("agrees", True) and "suggested_marks" in verifier_result:
        grade_result["needs_review"] = True
        
    grade_result["study_plan"] = study_plan_result
    
    # Polish using existing utility
    polish_feedback_dict(grade_result, grade_level)
    
    out = {
        "file": state["filename"],
        "ok": True,
        "extracted_text": state.get("full_answer_text") or state["answer_text"],
        "grade_used": grade_level,
        "subject_used": state["subject"],
        "chapter_used": state["chapter"],
        "grade_tier": build_tier_label(grade_level),
        "exam_config_used": state["exam_config"],
        "pre_flight": state["pre_flight_checks"],
        **grade_result
    }
    
    if state.get("extraction_hint"):
        out["extraction_hint"] = state["extraction_hint"]
        
    return {"final_output": out}


# ── BUILD STATEGRAPH ────────────────────────────────────────────────────────
workflow = StateGraph(GradingState)

# Add nodes
workflow.add_node("ocr_and_preflight", ocr_and_preflight_node)
workflow.add_node("grade", grade_node)
workflow.add_node("math_check", math_check_node)
workflow.add_node("verifier", verifier_node)
workflow.add_node("study_plan", study_plan_node)
workflow.add_node("polish", polish_node)

# Add parallel edges
workflow.add_edge(START, "ocr_and_preflight")

# Run grade node after ocr_and_preflight
workflow.add_edge("ocr_and_preflight", "grade")

# From grade, run math_check, verifier, and study_plan in parallel
workflow.add_edge("grade", "math_check")
workflow.add_edge("grade", "verifier")
workflow.add_edge("grade", "study_plan")

# Fan-in all parallel endpoints to polish
workflow.add_edge("math_check", "polish")
workflow.add_edge("verifier", "polish")
workflow.add_edge("study_plan", "polish")
workflow.add_edge("polish", END)

# Compile the Graph
grading_graph = workflow.compile()
