"""NLP polish layer for grade-adaptive feedback.

Uses `textstat` (Flesch-Kincaid) to score readability of feedback text
and a small vocabulary-swap dictionary to simplify wording for junior grades.

Public API:
    polish_feedback_dict(d: dict, grade: int) -> dict
        Walks a grading result and rewrites string fields in-place so they
        match the target reading level. Adds `_readability` metadata.

    grade_target_fk(grade: int) -> int
        Returns the target Flesch-Kincaid grade level for a given CBSE grade.
"""
from __future__ import annotations

import re
from typing import Any
import textstat


# Vocab simplification map (for junior grades 1-4)
_VOCAB_SIMPLE = [
    (r"\bdemonstrate(s|d)?\b",    "show"),
    (r"\bcomprehend(s|ed|ing)?\b", "understand"),
    (r"\bcomprehension\b",         "understanding"),
    (r"\butili[sz]e(s|d)?\b",      "use"),
    (r"\butili[sz]ation\b",        "use"),
    (r"\bsubsequently\b",          "then"),
    (r"\bconsequently\b",          "so"),
    (r"\bfurthermore\b",           "also"),
    (r"\bmoreover\b",              "also"),
    (r"\bnevertheless\b",          "but"),
    (r"\bapproximately\b",         "about"),
    (r"\bsufficient(ly)?\b",       "enough"),
    (r"\bcommence(s|d|ment)?\b",   "start"),
    (r"\bobtain(s|ed|ing)?\b",     "get"),
    (r"\bobserve(s|d)?\b",         "see"),
    (r"\belaborate(s|d)?\b",       "explain more"),
    (r"\bsignificant(ly)?\b",      "big"),
    (r"\bnumerous\b",              "many"),
    (r"\bappropriate(ly)?\b",      "right"),
    (r"\baccompli(sh|shed|shes)\b","do"),
    (r"\bregarding\b",             "about"),
    (r"\binitial(ly)?\b",          "first"),
    (r"\bidentify\b",              "find"),
    (r"\bensure\b",                "make sure"),
    (r"\battempt(s|ed|ing)?\b",    "try"),
]
_VOCAB_SIMPLE = [(re.compile(p, re.IGNORECASE), r) for p, r in _VOCAB_SIMPLE]


def grade_target_fk(grade: int) -> int:
    """Return target Flesch-Kincaid grade level for a CBSE grade."""
    if grade <= 4:  return 3   # very simple (FK 2-4)
    if grade <= 8:  return 7   # standard middle-school (FK 6-8)
    return 10                  # board-exam appropriate (FK 9-12)


def _simplify_vocab(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    out = text
    for pat, repl in _VOCAB_SIMPLE:
        out = pat.sub(repl, out)
    # Preserve capitalization at sentence starts
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


def _score(text: str) -> float:
    if not text or len(text.strip()) < 10:
        return 0.0
    try:
        return float(textstat.flesch_kincaid_grade(text))
    except Exception:
        return 0.0


def polish_text(text: str, grade: int) -> tuple[str, float, bool]:
    """Return (polished, fk_score, was_simplified)."""
    if not text or not isinstance(text, str):
        return text, 0.0, False
    target = grade_target_fk(grade)
    original_fk = _score(text)
    # Junior grades: aggressively simplify if too complex
    if grade <= 4 and original_fk > target + 1:
        simplified = _simplify_vocab(text)
        new_fk = _score(simplified)
        return simplified, new_fk, simplified != text
    # Middle: only swap if very complex (FK > 9)
    if 5 <= grade <= 8 and original_fk > target + 2:
        simplified = _simplify_vocab(text)
        return simplified, _score(simplified), simplified != text
    return text, original_fk, False


def _walk_strings(obj: Any, grade: int, stats: dict):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in {"feedback", "suggestion", "description",
                                            "comment", "why", "correction"}:
                polished, fk, changed = polish_text(v, grade)
                obj[k] = polished
                stats["scores"].append(fk)
                if changed:
                    stats["simplified"] += 1
            else:
                _walk_strings(v, grade, stats)
    elif isinstance(obj, list):
        for x in obj:
            _walk_strings(x, grade, stats)


def polish_feedback_dict(d: dict, grade: int) -> dict:
    """Walk the grading result and rewrite string fields. Mutates `d`.
    Adds a top-level `_readability` block with {average_fk, target_fk, simplified_count, tier}."""
    if not isinstance(d, dict):
        return d
    stats = {"scores": [], "simplified": 0}
    _walk_strings(d, grade, stats)
    avg_fk = round(sum(stats["scores"]) / len(stats["scores"]), 1) if stats["scores"] else 0.0
    tier = "junior" if grade <= 4 else "middle" if grade <= 8 else "senior"
    d["_readability"] = {
        "average_fk":       avg_fk,
        "target_fk":        grade_target_fk(grade),
        "simplified_count": stats["simplified"],
        "tier":             tier,
    }
    return d
