"""Grade-tier aware system prompt for the bulk auto-grader.

Tone, sentence length and vocabulary are now *explicitly bounded* per tier
so feedback is genuinely different for a Grade 2 vs a Grade 11 student.
"""
from __future__ import annotations
from cbse_languages import grading_language_block


def build_exam_constraints(config: dict, grade: int = 0) -> str:
    """Convert a teacher exam config dict into a prompt constraints block."""
    if not config:
        return ""

    lines = ["", "═══════════════════════════════════════════════"]
    lines.append("📋 TEACHER EXAM CONFIGURATION — follow these as HARD RULES")
    lines.append("═══════════════════════════════════════════════")

    board   = config.get("board", "")
    g       = config.get("grade") or grade
    subject = config.get("subject", "")
    chapter = config.get("chapter", "")
    etype   = config.get("exam_type", "")

    meta = " | ".join(filter(None, [
        f"Board: {board}" if board else "",
        f"Grade: {g}" if g else "",
        f"Subject: {subject}" if subject else "",
        f"Chapter: {chapter}" if chapter else "",
        f"Exam type: {etype}" if etype else "",
    ]))
    if meta:
        lines.append(meta)

    strictness = config.get("strictness", "")
    if strictness:
        labels = {
            "lenient": "Lenient — concept understood = marks",
            "moderate": "Moderate — key terms required",
            "strict": "Strict — exact steps required",
            "board": "Board exam — full precision required",
        }
        lines.append(f"Strictness: {labels.get(strictness, strictness)}")

    eval_order = config.get("eval_order", "")
    if eval_order:
        lines.append(f"Evaluation order: {eval_order}")

    instructions = config.get("instructions", "")
    if instructions and instructions.strip():
        lines.append(f'\nTeacher instruction: "{instructions.strip()}"')

    questions = config.get("questions", [])
    if questions:
        lines.append("\nQuestion marks (enforce exactly):")
        for q in questions:
            partial_label = {"yes": "partial credit: yes", "no": "partial credit: no",
                             "half": "partial credit: half only"}.get(q.get("partial", "yes"), "")
            lines.append(f"  {q.get('label','?')} ({q.get('type','')}) — "
                         f"{q.get('marks',0)} marks"
                         + (f", {partial_label}" if partial_label else ""))

    rules = config.get("rules", {})
    if rules:
        lines.append("\nGrading rules:")
        rule_map = {
            "step_marks":      ("Step-by-step marking", True),
            "partial_credit":  ("Partial credit allowed", True),
            "forgive_calc":    ("Forgive calculation errors if method correct", False),
            "diagram_marks":   ("Separate diagram / labeling marks", False),
            "grammar_check":   ("Grammar / language check", False),
            "carry_forward":   ("Carry-forward error protection (ECF)", True),
        }
        for key, (label, default) in rule_map.items():
            val = rules.get(key, default)
            lines.append(f"  {'✓' if val else '✗'} {label}")

    fb = config.get("feedback", {})
    if fb:
        fb_parts = []
        if fb.get("language"): fb_parts.append(fb["language"])
        if fb.get("tone"):     fb_parts.append(fb["tone"] + " tone")
        if fb.get("length"):   fb_parts.append(fb["length"] + " length")
        if fb.get("ncert_ref") is True: fb_parts.append("include NCERT reference")
        if fb_parts:
            lines.append(f"\nFeedback style: {', '.join(fb_parts)}")
        if fb.get("show_concepts") is True:
            lines.append("  ✓ Show missing concepts in feedback")
        if fb.get("revision_tips") is True:
            lines.append("  ✓ Include revision tips")

    lines.append("═══════════════════════════════════════════════\n")
    return "\n".join(lines)


def _tier(grade: int) -> str:
    if grade <= 4:  return "junior"
    if grade <= 8:  return "middle"
    return "senior"


# Each tier dictates: vocabulary level, sentence length, emoji use, examples.
_TONE = {
    "junior": (
        "You are talking to a 6-9 year old child. Use ONLY simple, everyday words "
        "(no more than 6 letters when possible). Keep every sentence under 12 words. "
        "Use exactly 1-2 friendly emojis in the suggestion (🌟 ✨ 👍 🌈). Never use "
        "words like 'demonstrate', 'comprehend', 'utilize', 'subsequently'. Use 'show', "
        "'understand', 'use', 'then'. Always praise effort first. Phrase mistakes as "
        "'next time try…' not 'you got this wrong'. Example tone: 'Great try! 🌟 "
        "Next time, try drawing the pattern. You almost had it!'"
    ),
    "middle": (
        "You are talking to a 10-13 year old. Use clear, supportive language with "
        "12-20 word sentences. No emojis. Be specific about the concept that needs "
        "work — name the chapter or formula. Acknowledge effort before pointing out "
        "errors. Avoid overly formal vocabulary but don't talk down. Example tone: "
        "'You set up the equation correctly. The next step needs the transposition "
        "rule from Chapter 5 — review section 5.2 and try again.'"
    ),
    "senior": (
        "You are talking to a 14-17 year old preparing for CBSE board exams. Use "
        "precise, exam-focused language. Sentences can be 18-28 words. No emojis. "
        "Reference NCERT chapters and section numbers where useful. Use the correct "
        "technical vocabulary ('transposition', 'derivation', 'corollary'). Treat the "
        "student as an exam candidate. Example tone: 'The transposition in Step 3 "
        "neglects the sign change for the constant term. Review NCERT Chapter 5, "
        "Section 5.4, and rework the derivation paying attention to sign conventions.'"
    ),
}


def _sum_rubric_marks(rubric: str) -> int:
    """Sum mark allocations from every rubric line.

    Handles all common formats the rubric generator produces:
      - Q1 (5 marks): ...       → 5
      - Q28.1 (1): ...          → 1   ← was being missed!
      - Q2 [3 marks] ...        → 3
      - Q3 - 2 marks ...        → 2
    Processes each line exactly once to avoid double-counting.
    """
    import re
    total = 0
    for line in rubric.splitlines():
        line = line.strip()
        if not line:
            continue
        # Pattern 1: (X marks) or (X mark)  — e.g. Q1 (5 marks):
        m = re.search(r"\((\d+)\s*marks?\)", line, re.IGNORECASE)
        if m:
            total += int(m.group(1))
            continue
        # Pattern 2: [X marks] or [X mark]  — e.g. Q1 [5 marks]
        m = re.search(r"\[(\d+)\s*marks?\]", line, re.IGNORECASE)
        if m:
            total += int(m.group(1))
            continue
        # Pattern 3: Q<id> (X):  with NO "marks" keyword — e.g. Q28.1 (1):
        m = re.search(r"^Q[\w.]*\s*\((\d+)\)\s*:", line, re.IGNORECASE)
        if m:
            total += int(m.group(1))
            continue
    return total


def bulk_grader_prompt(grade: int, subject: str, chapter: str, rubric: str,
                       ncert_context: str = "", exam_config: dict = None) -> str:
    tone = _TONE[_tier(grade)]
    ctx = f"\n\nNCERT chapter context (use to ground feedback):\n{ncert_context}\n" if ncert_context else ""
    declared_total = _sum_rubric_marks(rubric)
    lang_block = grading_language_block()
    exam_block = build_exam_constraints(exam_config or {}, grade)
    total_constraint = (
        f"\n🎯 MARKS TOTAL RULE: The rubric above has marks that sum to {declared_total}. "
        f"Your `marks_total` field MUST equal {declared_total}. "
        "Your `per_question` marks_total values must also sum to exactly this number. "
        "Do NOT invent extra marks or count parent questions AND sub-questions separately.\n"
    ) if declared_total > 0 else ""
    return f"""{exam_block}You are a CBSE Grade {grade} {subject} examiner grading the chapter \
\"{chapter}\".

TONE RULES (must follow strictly):
{tone}{ctx}

Marking rubric the teacher provided:
\"\"\"
{rubric}
\"\"\"
{total_constraint}
Grade the student's answer FAIRLY against this rubric. Every feedback string \
(suggestion, mistake descriptions, per-question feedback) MUST follow the TONE RULES above.

═══════════════════════════════════════════════
📋 ANSWER FORMAT RULES — grade the CONCEPT, not the format
═══════════════════════════════════════════════

🖊  PLAIN TEXT — grade normally.

📊  [DIAGRAM: ...] tags — the student drew a diagram/figure/flowchart.
    This is a COMPLETE visual answer. Grade as follows:
    • All key labels present and correct → full marks.
    • Most labels correct, a few missing → proportional part marks.
    • Labels present but mostly wrong concept → 0-1 marks.
    • Never penalise for diagram neatness or artistic quality.

📋  TABLES (rows with | separators) — valid and often ideal for comparison questions.
    Grade on whether the correct facts are in the right cells, not on table style.

🔢  MATHEMATICAL WORKING (equations / steps / proofs):
    • 🔀 ERROR CARRIED FORWARD (ECF): If a student makes an arithmetic error in an early step, but uses that incorrect result correctly in subsequent steps, do NOT penalize them again. Award full method/calculation marks for all subsequent steps.
    • 📏 FORMULA CREDIT: Always award partial marks (e.g., 0.5 to 1 mark) for writing the correct formula/identity, even if the calculations or substituted values are wrong.
    • Minor arithmetic slip (e.g. 4*4=15 instead of 16) → deduct ≤1 mark only.
    • Different but valid derivation path → full marks.

📌  BULLET POINTS / NUMBERED LISTS — equivalent to prose.
    All expected points present → full marks regardless of writing style.

🗣  ANY CBSE-APPROVED LANGUAGE (see full rules below) — grade conceptual content only.

📝  ABBREVIATIONS / SHORTHAND — if the key facts are present, award full marks.

🔄  COMBINATION ANSWERS (text + diagram + formula + bullets together):
    • Shows deeper understanding. Award full marks if the concept is demonstrated.
    • Do NOT double-penalise when the student used multiple formats.

═══════════════════════════════════════════════
{lang_block}

═══════════════════════════════════════════════
📚 NCERT & CBSE GRADING RULES
═══════════════════════════════════════════════
  - Grade against what NCERT Grade {grade} {subject} books say.
  - 📌 VALUE POINTS: Look for key terms (value points) in the student's answer. If the core concepts (value points) from the rubric are present, award full credit regardless of sentence structure.
  - 🔠 NO SPELLING/GRAMMAR PENALTY (Non-Language Subjects): If the subject is NOT a language paper (e.g. Science, Mathematics, Physics, Chemistry, Biology, Social Science), you must NOT deduct any marks for spelling mistakes, grammatical errors, or poor sentence structure. Only check for conceptual correctness.
  - Correct NCERT terminology and concepts → full marks.
  - Correct but non-NCERT phrasing → deduct 1 mark max, note "Not as per NCERT phrasing".
  - Directly contradicts NCERT content → 0 for that question.
  - Cite NCERT concept in per_question feedback only when answer is non-blank.

🚫 ANTI-HALLUCINATION RULES:
  - BLANK/MISSING answer → feedback: "No answer was given for this question."
    and mistake type "blank" with description "No attempt made."
  - Do NOT reference unrelated subjects.

Return STRICT JSON only (no prose, no markdown fences):

{{
  \"student_name\": string,
  \"detected_language\": string (primary language/script detected, e.g. "Telugu", "Hindi+English", "Bengali"),
  \"marks_awarded\": number,
  \"marks_total\": number,
  \"percentage\": number,
  \"answer_formats_used\": [string],
  \"per_question\": [
    {{ \"q\": string, \"marks_awarded\": number, \"marks_total\": number,
       \"feedback\": string, \"ncert_aligned\": true|false,
       \"format\": \"text\"|\"diagram\"|\"table\"|\"math\"|\"bullets\"|\"hinglish\"|\"mixed\" }}
  ],
  \"mistakes\": [
    {{ \"type\": \"conceptual\"|\"calculation\"|\"step_skipped\"|\"wrong_formula\"|\"spelling\"|\"language\"|\"not_ncert\"|\"blank\", \"description\": string }}
  ],
  \"strengths\": [string],
  \"suggestion\": string,
  \"ai_cheat_suspicion\": number (0-100)
}}

Be honest but kind. If the answer is blank, give 0 and a short note."""


def ncert_validate_prompt(grade: int, subject: str, chapter: str) -> str:
    """Prompt for the standalone NCERT content validation call."""
    return f"""You are a CBSE curriculum expert with complete knowledge of NCERT textbooks.

Check whether this student answer sheet is based on NCERT Grade {grade} {subject} \
curriculum (chapter: \"{chapter}\").

For EACH question answer you see, decide:
  1. Is the question type from NCERT/CBSE exam pattern for this grade/subject? (yes/no)
  2. Is the student's answer content aligned with what NCERT books say? (yes/partial/no)
  3. Is anything in the answer factually wrong compared to NCERT? (describe briefly)

Return STRICT JSON only:
{{
  "is_ncert_paper": true|false,
  "ncert_alignment_score": number (0-100, how well answers match NCERT content),
  "syllabus_match": "full"|"partial"|"none",
  "questions_from_ncert": number (count of questions that follow NCERT/CBSE pattern),
  "questions_outside_ncert": number,
  "non_ncert_topics": [string] (topics asked that are NOT in NCERT syllabus for this grade),
  "ncert_issues": [
    {{ "question": string, "issue": string, "ncert_says": string }}
  ],
  "overall_comment": string (one sentence summary)
}}"""
