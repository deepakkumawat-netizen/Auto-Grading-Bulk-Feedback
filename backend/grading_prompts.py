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

    lines = ["", "--------------------------------------------------"]
    lines.append("TEACHER EXAM CONFIGURATION - follow these as HARD RULES")
    lines.append("--------------------------------------------------")

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
            "lenient": "Lenient - concept understood = marks",
            "moderate": "Moderate - key terms required",
            "strict": "Strict - exact steps required",
            "board": "Board exam - full precision required",
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
            lines.append(f"  {q.get('label','?')} ({q.get('type','')}) - "
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
            lines.append(f"  [{'YES' if val else 'NO'}] {label}")

    fb = config.get("feedback", {})
    if fb:
        fb_parts = []
        if fb.get("language"): fb_parts.append(fb["language"])
        if fb.get("tone"):     fb_parts.append(fb["tone"] + " tone")
        if fb.get("length"):   fb_parts.append(fb["length"] + " length")
        if fb_parts:
            lines.append(f"\nFeedback style: {', '.join(fb_parts)}")
        if fb.get("show_concepts") is True:
            lines.append("  [YES] Show missing concepts in feedback")
        if fb.get("revision_tips") is True:
            lines.append("  [YES] Include revision tips")

    lines.append("--------------------------------------------------\n")
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
        "Use no emojis in the suggestion. Never use "
        "words like 'demonstrate', 'comprehend', 'utilize', 'subsequently'. Use 'show', "
        "'understand', 'use', 'then'. Always praise effort first. Phrase mistakes as "
        "'next time try...' not 'you got this wrong'. Example tone: 'Great try! "
        "Next time, try drawing the pattern. You almost had it!'"
    ),
    "middle": (
        "You are talking to a 10-13 year old. Use clear, supportive language with "
        "12-20 word sentences. No emojis. Be specific about the concept that needs "
        "work - name the chapter or formula. Acknowledge effort before pointing out "
        "errors. Avoid overly formal vocabulary but don't talk down. Example tone: "
        "'You set up the equation correctly. The next step needs the transposition "
        "rule from Chapter 5 - review section 5.2 and try again.'"
    ),
    "senior": (
        "You are talking to a 14-17 year old preparing for board exams. Use "
        "precise, exam-focused language. Sentences can be 18-28 words. No emojis. "
        "Use the correct "
        "technical vocabulary ('transposition', 'derivation', 'corollary'). Treat the "
        "student as an exam candidate. Example tone: 'The transposition in Step 3 "
        "neglects the sign change for the constant term. Rework the derivation paying attention to sign conventions.'"
    ),
}


def _sum_rubric_marks(rubric: str) -> int:
    """Sum mark allocations from every rubric line hierarchically to avoid double-counting choices."""
    import re
    
    def parse_single_mark_value(text: str) -> float:
        text = text.strip().lower()
        # Replace unicode fractions with +decimal
        text = text.replace("½", "+0.5").replace("¼", "+0.25").replace("¾", "+0.75")
        
        # Split by whitespace, '+', or '-'
        parts = re.split(r'[\s\-+]+', text)
        val = 0.0
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if "/" in p:
                subparts = p.split("/")
                if len(subparts) == 2:
                    try:
                        val += float(subparts[0]) / float(subparts[1])
                    except ValueError:
                        pass
            else:
                try:
                    val += float(p)
                except ValueError:
                    pass
        return val

    pq = []
    for line in rubric.splitlines():
        line = line.strip()
        if not line:
            continue
        line_clean = re.sub(r"^[-*#\s]+", "", line)
        if not re.match(r"^(?:q\d+|question\s*\d+|q\s*\d+|\b\d+[\.\)])", line_clean, re.IGNORECASE):
            continue
            
        colon_idx = line_clean.find(":")
        header = line_clean[:colon_idx].strip() if colon_idx != -1 else line_clean
        
        # Parse marks total
        m_val = 0.0
        m = re.search(r"\(([\d\./\s\+½¼¾\-]+)\s*marks?\)", line_clean, re.IGNORECASE)
        if m:
            m_val = parse_single_mark_value(m.group(1))
        else:
            m = re.search(r"\[([\d\./\s\+½¼¾\-]+)\s*marks?\]", line_clean, re.IGNORECASE)
            if m:
                m_val = parse_single_mark_value(m.group(1))
            else:
                m = re.search(r"\(([\d\./\s\+½¼¾\-]+)\)", header)
                if m:
                    m_val = parse_single_mark_value(m.group(1))
                    
        if m_val > 0.0:
            clean_hdr = re.sub(r'[\(\[]\s*[\d\./\s\+½¼¾\-]+\s*(?:marks?)?\s*[\)\]]', '', header).strip()
            pq.append({
                "q": clean_hdr,
                "marks_total": m_val,
                "marks_awarded": 0.0,
                "full_line": line_clean
            })
            
    if not pq:
        return 0
        
    parsed_items = []
    for item in pq:
        q_label = str(item["q"]).strip()
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
            "marks_total": item["marks_total"],
            "full_line": item.get("full_line", "")
        })
        
    def aggregate_node(node_path, items):
        remaining_items = [it for it in items if len(it["parts"]) > len(node_path)]
        if not remaining_items:
            return sum(it["marks_total"] for it in items)
            
        groups = {}
        for it in remaining_items:
            next_part = it["parts"][len(node_path)]
            if next_part not in groups:
                groups[next_part] = []
            groups[next_part].append(it)
            
        has_or = False
        if len(groups) > 1:
            for key in groups.keys():
                k_upper = key.upper()
                if k_upper == "OR" or "OR" in k_upper or "अथवा" in key:
                    has_or = True
                    break
                if re.match(r'^[A-Z]$', key):
                    has_or = True
                    break
                    
            if not has_or:
                for it in items:
                    full_line = it.get("full_line", "")
                    colon_idx = full_line.find(":")
                    header = full_line[:colon_idx] if colon_idx != -1 else full_line
                    header_upper = header.upper()
                    if " OR " in header_upper or " OR:" in header_upper or "(OR)" in header_upper or "अथवा" in header or " या " in header:
                        has_or = True
                        break
                        
        child_totals = []
        for key, group_items in groups.items():
            child_totals.append(aggregate_node(node_path + [key], group_items))
            
        if has_or:
            return max(child_totals) if child_totals else 0
        else:
            return sum(child_totals)
            
    main_groups = {}
    for it in parsed_items:
        if it["parts"]:
            main_key = it["parts"][0]
            if main_key not in main_groups:
                main_groups[main_key] = []
            main_groups[main_key].append(it)
            
    total_marks = 0.0
    for main_key, items in main_groups.items():
        total_marks += aggregate_node([main_key], items)
        
    return int(round(total_marks))


def bulk_grader_prompt(grade: int, subject: str, chapter: str, rubric: str,
                       exam_config: dict = None, handwriting_audit: bool = False) -> str:
    tone = _TONE[_tier(grade)]
    ctx = ""
    declared_total = _sum_rubric_marks(rubric)
    lang_block = grading_language_block()
    exam_block = build_exam_constraints(exam_config or {}, grade)
    total_constraint = (
        f"\nMARKS TOTAL RULE: The rubric above has marks that sum to {declared_total}. "
        f"Your `marks_total` field MUST equal {declared_total}. "
        f"Your `per_question` marks_total values must also sum to exactly this number. "
        "Do NOT invent extra marks or count parent questions AND sub-questions separately.\n"
    ) if declared_total > 0 else ""

    tone_rules_block = f"TONE RULES (must follow strictly for all feedback/suggestions/mistakes):\n{tone}{ctx}"

    if handwriting_audit:
        # Append HandwritingEval specialized guidelines and tone guidelines
        handwriting_guidelines = """
═══════════════════════════════════════════════
📋 HANDWRITING, GRAMMAR & QUALITY AUDIT INSTRUCTIONS
═══════════════════════════════════════════════
You must perform the following audit steps on the provided handwritten answer sheet images:

Step 1 - Transcribe all content: You MUST transcribe the ENTIRE student answer sheet page-by-page, word-for-word. Do NOT summarize and do NOT omit any text, sentences, calculations, or steps. Transcribe everything verbatim (keeping misspelled words or shorthand in this verbatim transcript). If there are multiple pages, include page headers like '--- PAGE 1 ---', etc., and transcribe the complete content of each page. Every single line of written text on the sheet must be present in the transcript.
Step 1.1 - Create a polished, corrected, and highly readable version of the verbatim transcript in clear, standard language (named "cleaned_transcript"). You MUST polish the ENTIRE verbatim transcript word-for-word, correcting all spelling mistakes, typos, awkward phrasing, grammatical issues, and formatting all math equations/tables cleanly. The goal is to provide a complete, clean, readable copy of the student's text that the teacher can easily read and understand. Do NOT summarize or abbreviate.
Step 2 - Break the answer into logical steps (lines of working / parts of the argument / diagram labels / table rows).
Step 3 - For each step decide: correct / wrong / partial.
Step 4 - Identify the FIRST conceptual mistake (the one that derails everything after). If the student got everything right, set first_mistake to null.
Step 5 - Determine if the text is HANDWRITTEN or TYPED/PRINTED:
   - Set `is_typed: true` if letters look uniform (same width/height, same font), perfectly aligned on the baseline, no ink variation, no slant inconsistency.
   - Set `is_typed: false` only if you can see clear evidence of handwriting: irregular letter sizes, varying slant, ink-pressure variation, hand-drawn baseline drift, pen smudges, or non-uniform spacing.
   If is_typed = true: set `handwriting_clarity` to 0.
   If is_typed = false: rate STRICTLY using these criteria — DO NOT BE GENEROUS:
     ★ (1)     Mostly illegible. Many letters unreadable. Heavy strikethroughs or scribbles.
     ★★ (2)    Very messy. Reader must guess most words. Inconsistent letter sizes, wandering baseline.
     ★★★ (3)   Readable with effort. Several letters ambiguous; some words need context to decode.
     ★★★★ (4) Clear handwriting with minor inconsistencies.
     ★★★★★ (5) Exceptional — uniform letter sizes, clean baseline, no smudges, every letter instantly readable.
Step 6 - Score effort (`effort_score`, 0-100) — INDEPENDENT of correctness:
   - 0:     blank / no attempt visible
   - 1-30:  minimal attempt, almost no working
   - 31-60: some steps shown but incomplete
   - 61-85: clear working shown for most of the answer (even if final result wrong)
   - 86-100: thorough, complete working with all reasoning visible
Step 7 - Analyze handwriting quality (clarity, alignment, spacing, readability comment) and map to a 0-100 handwriting quality rating score.
Step 8 - Check grammar and spelling. Find all spelling, punctuation, and grammar errors, extract their details, and calculate grammar/spelling quality scores out of 100.
Step 9 - Evaluate visual elements. Identify and evaluate any graphs, diagrams, sketches, arrows, maps, dots, tables, and shapes. For each: set detected (true/false), score correctness (0-100 or null if not detected), and write a comment.
Step 10 - Check homework completeness (score 0-100, status: complete/incomplete/partial, comment on missing parts).
Step 11 - Calculate category-wise scores out of 100: handwriting_quality, grammar_and_spelling, math_and_equations, diagrams_and_visuals, completeness. These are quality rating percentages, not exam marks.
Step 12 - Write feedback. If is_typed = true, note in the feedback that the answer appears typed/printed so handwriting could not be assessed.
"""
        json_schema = """Return STRICT JSON only (no prose, no markdown fences):

{
  "student_name": string,
  "detected_language": string (primary language/script detected, e.g. "Telugu", "Hindi+English", "Bengali"),
  "marks_awarded": number,
  "marks_total": number,
  "percentage": number,
  "answer_formats_used": [string],
  "per_question": [
    { "q": string, "marks_awarded": number, "marks_total": number,
       "feedback": string,
       "format": "text"|"diagram"|"table"|"math"|"bullets"|"hinglish"|"mixed" }
  ],
  "mistakes": [
    { "type": "conceptual"|"calculation"|"step_skipped"|"wrong_formula"|"spelling"|"language"|"blank", "description": string }
  ],
  "strengths": [string],
  "suggestion": string,
  "ai_cheat_suspicion": number (0-100),
  "transcript": string (verbatim, page-by-page, line-by-line transcription of all text written by the student),
  "cleaned_transcript": string (polished, corrected, highly readable version of the transcript above),
  "steps": [
    { "index": number, "text": string, "verdict": "correct"|"wrong"|"partial",
       "comment": string,
       "format": "text"|"diagram"|"table"|"math"|"bullets"|"hinglish"|"mixed" }
  ],
  "first_mistake": null | { "step_index": number, "why": string, "correction": string },
  "is_typed": boolean,
  "handwriting_clarity": number,
  "handwriting_analysis": {
    "clarity_score": number,
    "alignment": "good"|"wandering"|"poor",
    "spacing": "good"|"uneven"|"cramped",
    "readability_comment": string
  },
  "grammar_spelling": {
    "grammar_score": number,
    "spelling_score": number,
    "errors": [
      { "original": string, "correction": string, "type": "spelling"|"grammar", "explanation": string }
    ]
  },
  "visual_elements": {
    "graphs": { "detected": boolean, "correctness_score": number | null, "comment": string },
    "diagrams": { "detected": boolean, "correctness_score": number | null, "comment": string },
    "sketches": { "detected": boolean, "correctness_score": number | null, "comment": string },
    "arrows": { "detected": boolean, "correctness_score": number | null, "comment": string },
    "maps": { "detected": boolean, "correctness_score": number | null, "comment": string },
    "dots": { "detected": boolean, "correctness_score": number | null, "comment": string },
    "tables": { "detected": boolean, "correctness_score": number | null, "comment": string },
    "shapes": { "detected": boolean, "correctness_score": number | null, "comment": string }
  },
  "homework_completeness": {
    "score": number (0-100),
    "status": "complete"|"incomplete"|"partial",
    "missing_parts_comment": string
  },
  "category_scores": {
    "handwriting_quality": number (0-100),
    "grammar_and_spelling": number (0-100),
    "math_and_equations": number (0-100),
    "diagrams_and_visuals": number (0-100),
    "completeness": number (0-100)
  },
  "effort_score": number (0-100)
}"""
    else:
        handwriting_guidelines = ""
        json_schema = """Return STRICT JSON only (no prose, no markdown fences):

{{
  \"student_name\": string,
  \"detected_language\": string (primary language/script detected, e.g. \"Telugu\", \"Hindi+English\", \"Bengali\"),
  \"marks_awarded\": number,
  \"marks_total\": number,
  \"percentage\": number,
  \"answer_formats_used\": [string],
  \"per_question\": [
    {{ \"q\": string, \"marks_awarded\": number, \"marks_total\": number,
       \"feedback\": string,
       \"format\": \"text\"|\"diagram\"|\"table\"|\"math\"|\"bullets\"|\"hinglish\"|\"mixed\" }}
  ],
  \"mistakes\": [
    {{ \"type\": \"conceptual\"|\"calculation\"|\"step_skipped\"|\"wrong_formula\"|\"spelling\"|\"language\"|\"blank\", \"description\": string }}
  ],
  \"strengths\": [string],
  \"suggestion\": string,
  \"ai_cheat_suspicion\": number (0-100)
}}"""

    return f"""{exam_block}You are a Grade {grade} {subject} examiner grading the chapter \
\"{chapter}\".

CRITICAL DIRECTIVES:
1. PROCESS THE TEACHER'S UPLOADED ANSWER KEY & RUBRIC ONLY:
   - You MUST evaluate and grade the student's answers strictly and solely based on the Marking Rubric/Solution Key uploaded by the teacher below. Do NOT use your own external assumptions, general book knowledge, or default standards.
   - Note: The teacher's answer key/rubric is uploaded in Markdown (.md) format. Carefully parse the Markdown headers, bold titles, bullet points, step descriptions, and sub-marks allocations to perform step-wise grading.
   - Do NOT generate or assume ideal answers. Follow ONLY the teacher-provided answer key and marking instructions. This is the sole authority for grading.

2. STRICT EVALUATION:
   - Do not infer missing information. Do not assume alternative answers unless explicitly provided in the teacher's marking scheme.
   - Grade strictly according to the uploaded marking scheme. If a student's answer is missing required information or steps, deduct marks accordingly.

3. MATCH QUESTIONS:
   - Match each student answer with the correct question number. Ensure you are evaluating the student's response against the matching question in the rubric.

4. STEP-WISE MARKING:
   - Evaluate each step separately if step-wise marks or criteria are provided in the rubric.
   - Award marks only for correct steps. Do NOT award marks for missing or incorrect steps.
   - If a student's intermediate steps are correct but the final calculation has an error, award the intermediate step marks strictly as defined.

5. UNANSWERED/MISSING QUESTIONS:
   - If a question is unanswered, blank, or missing in the student's response, you MUST:
     a. Award 0 marks (marks_awarded = 0) for that question.
     b. Set the `feedback` for that question to EXACTLY: "Question not attempted."
     c. Create a mistake entry in the `mistakes` array with type "blank" and description "Question not attempted."

6. GRADE-LEVEL APPROPRIATE FEEDBACK:
   - Generate feedback according to the student’s grade level.
   - Use simple language for lower grades. Use detailed academic language for higher grades.
   - Base feedback only on the teacher's rubric and student response. Do NOT generate generic AI feedback.
   - Mention missing or incorrect steps using grade-appropriate language.

7. DETAILED & SPECIFIC FEEDBACK (DONE/MISSING FORMAT):
   - For every question in `per_question`, provide a clear, proper, and specific step-by-step breakdown in the `feedback` field.
   - You MUST format the feedback string exactly using this structure to show which part of the process was completed by the student and which part was not:
     "Done: [Briefly describe the correct steps, formulas, or value points completed by the student] | Missing/Incorrect: [Briefly describe the specific steps, calculations, or value points from the teacher's rubric that the student omitted or got wrong]"
   - If the student got full marks, write: "Done: All steps completed correctly | Missing/Incorrect: None"
   - Do NOT output vague, unhelpful feedback such as "Correct", "Partially correct", "Incorrect", or "Incorrect option". You must explicitly show what was done and what was not.
   - Keep all `mistakes` descriptions and the overall `suggestion` helpful, actionable, and clear.

8. FEEDBACK LANGUAGE:
   - Write all feedback, suggestions, mistake descriptions, and overall suggestions in the language of the subject being examined (e.g. write in Hindi for a Hindi paper, and write in English for English/Science/Maths).
   - CRITICAL: When writing in Hindi (Devanagari) or any other regional language, ignore English-specific constraints (such as "no more than 6 letters" or specific English word bounds). Write in natural, grammatically correct, and fluent Hindi suited for the student's grade level. Avoid literal translations from English structure (e.g., never write "हो है" - use correct phrasing like "होता है", "होना चाहिए", or "है").

{tone_rules_block}
 
Marking rubric the teacher provided:
\"\"\"
{rubric}
\"\"\"
{total_constraint}
Grade the student's answer FAIRLY against this rubric. Every feedback string \
(suggestion, mistake descriptions, per-question feedback) MUST follow the TONE RULES and CRITICAL DIRECTIVES above.

--------------------------------------------------
ANSWER FORMAT RULES - grade the CONCEPT, not the format
--------------------------------------------------

PLAIN TEXT - grade normally.

[DIAGRAM] [DIAGRAM: ...] tags - the student drew a diagram/figure/flowchart.
    This is a COMPLETE visual answer. Grade as follows:
    - All key labels present and correct -> full marks.
    - Most labels correct, a few missing -> proportional part marks.
    - Labels present but mostly wrong concept -> 0-1 marks.
    - Never penalise for diagram neatness or artistic quality.

[TABLES] TABLES (rows with | separators) - valid and often ideal for comparison questions.
    Grade on whether the correct facts are in the right cells, not on table style.

[MATHEMATICAL WORKING] MATHEMATICAL WORKING (equations / steps / proofs):
    - [ECF] ERROR CARRIED FORWARD (ECF): If a student makes an arithmetic error in an early step, but uses that incorrect result correctly in subsequent steps, do NOT penalize them again. Award full method/calculation marks for all subsequent steps.
    - [FORMULA] FORMULA CREDIT: Always award partial marks (e.g., 0.5 to 1 mark) for writing the correct formula/identity, even if the calculations or substituted values are wrong.
    - Minor arithmetic slip (e.g. 4*4=15 instead of 16) -> deduct <= 1 mark only.
    - Different but valid derivation path -> full marks.

[BULLET POINTS] BULLET POINTS / NUMBERED LISTS - equivalent to prose.
    All expected points present -> full marks regardless of writing style.

[LANGUAGE] ANY APPROVED LANGUAGE (see full rules below) - grade conceptual content only.

[ABBREVIATIONS] ABBREVIATIONS / SHORTHAND - if the key facts are present, award full marks.

[CONTAINER] COMBINATION ANSWERS (text + diagram + formula + bullets together):
    - Shows deeper understanding. Award full marks if the concept is demonstrated.
    - Do NOT double-penalise when the student used multiple formats.

--------------------------------------------------
{lang_block}

--------------------------------------------------
GENERAL EVALUATION RULES
--------------------------------------------------
  - VALUE POINTS: Look for key terms (value points) in the student's answer. If the core concepts (value points) from the rubric are present, award full credit regardless of sentence structure.
  - NO SPELLING/GRAMMAR PENALTY (Non-Language Subjects): If the subject is NOT a language paper, you must NOT deduct any marks for spelling mistakes, grammatical errors, or poor sentence structure. Grade solely based on conceptual understanding.
  - Grade strictly and solely against the teacher's provided answer key and rubric. Do not evaluate against external standards or books.

🚫 ANTI-HALLUCINATION RULES:
  - BLANK/MISSING answer -> feedback: "Question not attempted."
    and mistake type "blank" with description "Question not attempted."
  - Do NOT reference unrelated subjects.

{handwriting_guidelines}

{json_schema}

Be honest but kind. If the answer is blank, give 0 and a short note."""



