"""Agentic AI features — multi-step reasoning over completed grading results.

Three agents:
  insights_chat()            — teacher Q&A co-pilot over batch results
  generate_practice()        — targeted practice questions per student
  generate_class_plan()      — intervention plan for next lesson
"""
from __future__ import annotations
import os
from typing import Any
from llm_router import _gemini_chat_with_retry, _extract_json


# ─── shared helpers ───────────────────────────────────────────────────────────

def _compact_results(results: list[dict]) -> str:
    """One bullet per student — name, score, top mistake, weak questions."""
    lines = []
    for r in results:
        if not r.get("ok"):
            continue
        name = (r.get("student_name") or r.get("file", "?")).split(".")[0]
        ma   = r.get("marks_awarded", 0)
        mt   = r.get("marks_total", 0)
        pct  = min(r.get("percentage", 0), 100)
        mistakes = [m.get("type", "") for m in (r.get("mistakes") or [])[:2] if m.get("type")]
        pq_wrong = [
            q.get("q", "?")[:25]
            for q in (r.get("per_question") or [])
            if (q.get("marks_awarded") or 0) < (q.get("marks_total") or 1)
        ][:3]
        line = f"• {name}: {ma}/{mt} ({pct:.0f}%)"
        if mistakes:
            line += f"  mistakes: {', '.join(mistakes)}"
        if pq_wrong:
            line += f"  weak: {'; '.join(pq_wrong)}"
        lines.append(line)
    return "\n".join(lines) if lines else "No graded results available."


def _class_stats(results: list[dict]) -> dict:
    graded = [r for r in results if r.get("ok")]
    if not graded:
        return {}
    pcts   = [min(r.get("percentage", 0), 100) for r in graded]
    avg    = sum(pcts) / len(pcts)
    tally: dict[str, int] = {}
    weak_q: dict[str, int] = {}
    for r in graded:
        for m in (r.get("mistakes") or []):
            t = m.get("type", "other")
            tally[t] = tally.get(t, 0) + 1
        for q in (r.get("per_question") or []):
            if (q.get("marks_awarded") or 0) < (q.get("marks_total") or 1):
                k = q.get("q", "?")[:30]
                weak_q[k] = weak_q.get(k, 0) + 1
    top_mistakes = sorted(tally.items(),  key=lambda x: -x[1])[:4]
    top_weak_qs  = sorted(weak_q.items(), key=lambda x: -x[1])[:4]
    return {
        "count": len(graded), "avg": round(avg, 1),
        "top_mistakes": top_mistakes, "top_weak_qs": top_weak_qs,
    }


# ─── Agent 1: insights chat ───────────────────────────────────────────────────

def insights_chat(
    message: str,
    results: list[dict],
    rubric: str,
    history: list[dict],
) -> str:
    """Answer a teacher's question about the batch grading results."""
    stats   = _class_stats(results)
    context = _compact_results(results)

    system = f"""You are an AI teaching co-pilot. A teacher just finished grading {stats.get('count', 0)} answer sheets.

CLASS RESULTS:
{context}

STATISTICS:
- Class average: {stats.get('avg', 0):.1f}%
- Top mistake types: {', '.join(f"{k} ({v} students)" for k,v in stats.get('top_mistakes',[]))}
- Most-missed questions: {', '.join(f'"{k}" ({v} students)' for k,v in stats.get('top_weak_qs',[]))}

RUBRIC (first 600 chars):
{rubric[:600]}

YOUR ROLE — answer anything the teacher asks:
• Explain a specific student's low/high score
• Identify which topics the class struggles with most
• Compare two students
• Suggest which topic to reteach and how
• Generate practice questions on request ("give 5 questions on Q3 topic")
• List students who need extra attention

Style: Direct. Bullet points for lists. Under 180 words unless generating questions.
If the teacher asks for practice questions, generate them numbered with answer keys."""

    msgs = [{"role": "system", "content": system}]
    msgs.extend(history[-8:])
    msgs.append({"role": "user", "content": message})
    return _gemini_chat_with_retry(messages=msgs, temperature=0.35, max_tokens=700)


# ─── Agent 2: practice question generator ────────────────────────────────────

def generate_practice(
    result: dict,
    grade_level: int,
    subject: str,
    chapter: str,
    count: int = 5,
) -> list[dict]:
    """Generate targeted practice questions based on one student's mistakes."""
    pq     = result.get("per_question") or []
    wrong  = [q for q in pq if (q.get("marks_awarded") or 0) < (q.get("marks_total") or 1)]
    mist   = result.get("mistakes") or []

    weak_detail = "\n".join(
        f"- {q.get('q','?')}: {q.get('feedback','')[:100]}"
        for q in wrong[:4]
    ) or "General revision"
    mistake_types = ", ".join({m.get("type","") for m in mist[:4]} - {""}) or "conceptual"

    prompt = (
        f"Generate exactly {count} practice questions for a Grade {grade_level} {subject} student.\n\n"
        f"Student's weak areas:\n{weak_detail}\n"
        f"Mistake pattern: {mistake_types}\n"
        f"Chapter: {chapter or 'as detected'}\n\n"
        f"Rules:\n"
        f"- Target the SPECIFIC topics the student got wrong\n"
        f"- 2 easy (recall), 2 medium (apply), 1 hard (analyse/evaluate)\n"
        f"- Match exam format and mark weightage for Grade {grade_level}\n"
        f"- Include a concise answer key for each\n\n"
        "Return STRICT JSON only:\n"
        '{"questions":[{"number":1,"question":"...","marks":1,"difficulty":"easy",'
        '"answer_key":"key points"}]}'
    )
    raw = _gemini_chat_with_retry(
        messages=[
            {"role": "system", "content": "You return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3, max_tokens=1200,
        response_format={"type": "json_object"},
    )
    return (_extract_json(raw) or {}).get("questions") or []


# ─── Agent 3: class intervention planner ─────────────────────────────────────

def generate_class_plan(results: list[dict], rubric: str) -> dict:
    """Generate a focused next-lesson intervention plan from batch results."""
    stats  = _class_stats(results)
    ctx    = _compact_results(results)
    graded = [r for r in results if r.get("ok")]

    # Names of students scoring < 50%
    struggling = [
        (r.get("student_name") or r.get("file","?")).split(".")[0]
        for r in graded
        if min(r.get("percentage", 0), 100) < 50
    ][:6]

    prompt = (
        f"You are a curriculum expert. Analyze these Grade-level results and create a precise intervention plan.\n\n"
        f"CLASS RESULTS:\n{ctx}\n\n"
        f"Top mistake types: {', '.join(f'{k}({v})' for k,v in stats.get('top_mistakes',[]))}\n"
        f"Most-missed questions: {', '.join(f'\"{k}\"({v})' for k,v in stats.get('top_weak_qs',[]))}\n"
        f"Students scoring <50%: {', '.join(struggling) or 'none'}\n"
        f"Class average: {stats.get('avg',0):.1f}%\n\n"
        "Create a 30-40 minute intervention lesson. Be very specific — name actual core concepts, activities, and questions.\n\n"
        "Return STRICT JSON only:\n"
        '{"class_health":"strong|average|needs_help","summary":"one sentence",'
        '"priority_topics":["topic1","topic2","topic3"],'
        '"steps":[{"step":1,"title":"...","duration_mins":5,"what_teacher_does":"...","what_students_do":"..."}],'
        '"quick_check_questions":[{"q":"...","answer":"..."}],'
        '"students_needing_attention":["name1","name2"],'
        '"homework_suggestion":"string"}'
    )
    raw = _gemini_chat_with_retry(
        messages=[
            {"role": "system", "content": "You return only valid JSON."},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3, max_tokens=1600,
        response_format={"type": "json_object"},
    )
    return _extract_json(raw) or {}
