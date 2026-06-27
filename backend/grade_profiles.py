GRADE_PROFILES = {
    "1-2": {
        "tier": "primary_lower",
        "semantic_threshold": 0.60,
        "partial_credit": False,
        "keyword_strictness": "loose",
        "feedback_max_words": 20,
        "feedback_vocab_level": "grade2",
        "emoji_allowed": True,
        "tone": "very encouraging",
        "ncert_reference": False,
    },
    "3-5": {
        "tier": "primary_upper",
        "semantic_threshold": 0.65,
        "partial_credit": True,
        "keyword_strictness": "moderate",
        "feedback_max_words": 40,
        "feedback_vocab_level": "grade5",
        "emoji_allowed": False,
        "tone": "encouraging",
        "ncert_reference": False,
    },
    "6-8": {
        "tier": "middle",
        "semantic_threshold": 0.70,
        "partial_credit": True,
        "keyword_strictness": "moderate",
        "feedback_max_words": 60,
        "feedback_vocab_level": "grade8",
        "emoji_allowed": False,
        "tone": "supportive",
        "ncert_reference": True,
    },
    "9-10": {
        "tier": "secondary",
        "semantic_threshold": 0.75,
        "partial_credit": True,
        "keyword_strictness": "strict",
        "feedback_max_words": 80,
        "feedback_vocab_level": "grade10",
        "emoji_allowed": False,
        "tone": "exam-focused",
        "ncert_reference": True,
    },
    "11-12": {
        "tier": "senior",
        "semantic_threshold": 0.80,
        "partial_credit": True,
        "keyword_strictness": "very_strict",
        "feedback_max_words": 100,
        "feedback_vocab_level": "technical",
        "emoji_allowed": False,
        "tone": "precise and technical",
        "ncert_reference": True,
    },
}

SUBJECT_GRADE_RULES = {
    ("Mathematics", "primary_lower"): {
        "check_steps": False, "accept_any_valid_method": True,
        "verify_final_answer_only": True, "formula_required": False,
        "unit_required": False,
    },
    ("Mathematics", "primary_upper"): {
        "check_steps": True, "formula_required": False,
        "unit_required": False, "step_marks": True,
    },
    ("Mathematics", "middle"): {
        "check_steps": True, "formula_required": True,
        "unit_required": False, "step_marks": True,
    },
    ("Mathematics", "secondary"): {
        "check_steps": True, "formula_required": True,
        "unit_required": True, "step_marks": True, "proof_format_check": False,
    },
    ("Mathematics", "senior"): {
        "check_steps": True, "formula_required": True,
        "unit_required": True, "step_marks": True, "proof_format_check": True,
    },
    ("English", "primary_lower"): {
        "check_grammar": False, "check_content": True, "spelling_tolerance": "high",
    },
    ("English", "primary_upper"): {
        "check_grammar": False, "check_content": True, "spelling_tolerance": "medium",
    },
    ("English", "middle"): {
        "check_grammar": True, "grammar_weight": 0.20,
        "content_weight": 0.50, "vocabulary_weight": 0.15, "structure_weight": 0.15,
    },
    ("English", "secondary"): {
        "check_grammar": True, "grammar_weight": 0.30,
        "content_weight": 0.40, "vocabulary_weight": 0.15, "structure_weight": 0.15,
    },
    ("English", "senior"): {
        "check_grammar": True, "grammar_weight": 0.30,
        "content_weight": 0.35, "vocabulary_weight": 0.20, "structure_weight": 0.15,
    },
    ("Science", "middle"): {
        "diagram_marks": True, "labeling_required": False, "equation_verification": False,
    },
    ("Science", "secondary"): {
        "diagram_marks": True, "labeling_required": True, "equation_verification": True,
    },
    ("Physics", "secondary"): {
        "diagram_marks": True, "labeling_required": True, "equation_verification": True,
        "unit_required": True,
    },
    ("Physics", "senior"): {
        "diagram_marks": True, "labeling_required": True, "equation_verification": True,
        "unit_required": True, "derivation_steps": True,
    },
    ("Chemistry", "secondary"): {
        "equation_verification": True, "labeling_required": False, "diagram_marks": True,
    },
    ("Chemistry", "senior"): {
        "equation_verification": True, "labeling_required": True, "diagram_marks": True,
    },
    ("Biology", "secondary"): {
        "diagram_marks": True, "labeling_required": True, "equation_verification": False,
    },
    ("Biology", "senior"): {
        "diagram_marks": True, "labeling_required": True, "equation_verification": False,
    },
}


def get_profile(grade: int) -> dict:
    if grade <= 2:
        key = "1-2"
    elif grade <= 5:
        key = "3-5"
    elif grade <= 8:
        key = "6-8"
    elif grade <= 10:
        key = "9-10"
    else:
        key = "11-12"
    return {"grade_key": key, **GRADE_PROFILES[key]}


def get_subject_rules(subject: str, grade: int) -> dict:
    profile = get_profile(grade)
    tier = profile["tier"]
    return SUBJECT_GRADE_RULES.get((subject, tier), {})


def build_tier_label(grade: int) -> str:
    p = get_profile(grade)
    labels = {
        "primary_lower": "Primary (Grades 1-2)",
        "primary_upper": "Primary (Grades 3-5)",
        "middle":        "Middle School (Grades 6-8)",
        "secondary":     "Secondary (Grades 9-10)",
        "senior":        "Senior Secondary (Grades 11-12)",
    }
    return labels.get(p["tier"], f"Grade {grade}")
