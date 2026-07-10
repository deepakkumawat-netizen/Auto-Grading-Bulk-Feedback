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
    do_ncert_check: bool
    do_study_plan: bool
    grade_override: int
    subject_override: str
    exam_config: Optional[Dict[str, Any]]

    # Intermediate fields
    grade_level: int
    subject: str
    chapter: str
    answer_text: str
    extraction_hint: str
    pre_flight_checks: Dict[str, Any]
    system_prompt: str
    grade_result: Dict[str, Any]
    math_check: Dict[str, Any]
    verifier_result: Dict[str, Any]
    study_plan_result: List[str]
    error: Optional[str]

    # Output
    final_output: Dict[str, Any]


def ocr_and_preflight_node(state: GradingState) -> Dict[str, Any]:
    from main import _read_sheet, _fast_scope, pre_flight_check
    
    filename = state["filename"]
    raw = state["raw_bytes"]
    rubric = state["rubric"]
    grade_override = state["grade_override"]
    subject_override = state["subject_override"]
    exam_config = state["exam_config"]
    
    # ── OCR Extraction ──────────────────────────────────────────────────────
    answer_text = _read_sheet(filename, raw)
    
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
            "error": error,
            "grade_level": grade_level,
            "subject": subject,
            "chapter": chapter,
            "extraction_hint": extraction_hint
        }
        
    checks = pre_flight_check(answer_text, filename, rubric)
    
    # Validate overrides
    grade_level = grade_override or grade_level or 10
    subject = subject_override or subject or "General"
    
    from grading_prompts import bulk_grader_prompt
    system_prompt = bulk_grader_prompt(grade_level, subject, chapter, rubric, exam_config=exam_config)
    
    return {
        "answer_text": answer_text,
        "grade_level": grade_level,
        "subject": subject,
        "chapter": chapter,
        "extraction_hint": extraction_hint,
        "pre_flight_checks": checks,
        "system_prompt": system_prompt
    }


async def grade_node(state: GradingState) -> Dict[str, Any]:
    if state.get("error"):
        return {}
        
    from main import grade_text, _sum_rubric_marks
    
    system_prompt = state["system_prompt"]
    answer_text = state["answer_text"]
    rubric = state["rubric"]
    declared_total = state["declared_total"]
    
    try:
        result = await asyncio.to_thread(grade_text, system_prompt, answer_text)
        result["detected_scope"] = {"grade": state["grade_level"], "subject": state["subject"], "chapter": state["chapter"]}
        
        # Recalculate and scale marks
        pq = result.get("per_question") or []
        rubric_total = _sum_rubric_marks(rubric)
        if pq:
            computed_total   = sum(float(q.get("marks_total",   0) or 0) for q in pq)
            computed_awarded = sum(float(q.get("marks_awarded", 0) or 0) for q in pq)
            
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
        return {"verifier_result": {"agrees": True, "comment": f"Verifier failed: {e}"}}


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
            state.get("answer_text", ""),
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
        "extracted_text": state["answer_text"],
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

# From ocr_and_preflight, run math_check and grade in parallel
workflow.add_edge("ocr_and_preflight", "math_check")
workflow.add_edge("ocr_and_preflight", "grade")

# From grade, run verifier and study_plan in parallel
workflow.add_edge("grade", "verifier")
workflow.add_edge("grade", "study_plan")

# Fan-in all parallel endpoints to polish
workflow.add_edge("math_check", "polish")
workflow.add_edge("verifier", "polish")
workflow.add_edge("study_plan", "polish")
workflow.add_edge("polish", END)

# Compile the Graph
grading_graph = workflow.compile()
