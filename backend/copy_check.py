"""Cross-student copy/plagiarism detection for a graded batch.

Pure-Python, no LLM calls: splits each student's OCR'd answer text into
per-question chunks and pairwise-compares them with difflib. Flags student
pairs whose shared questions are suspiciously similar — a signal for manual
review, not proof of copying (a common rubric naturally produces some
overlap in vocabulary/value-points).
"""
from __future__ import annotations

import difflib
import re
from typing import Any

_MIN_CHARS_TO_COMPARE = 40     # skip near-blank answers — nothing to compare
_SIMILARITY_THRESHOLD = 0.82   # per-question match ratio considered suspicious
_MIN_MATCHED_QUESTIONS = 2     # need 2+ matching questions to flag a pair
_MAX_PAIRS_RETURNED = 12


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_by_question(text: str) -> dict[str, str]:
    """Split OCR/extracted answer text into {question_number: normalized_text}."""
    chunks: dict[str, list[str]] = {}
    current_q = None
    for line in (text or "").splitlines():
        m = re.match(r'^[\s>*-]*Q(?:uestion)?\s*\.?\s*(\d+)', line, re.IGNORECASE)
        if m:
            current_q = m.group(1)
        if current_q:
            chunks.setdefault(current_q, []).append(line)
    return {q: _normalize(" ".join(lines)) for q, lines in chunks.items()}


def detect_possible_copying(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare every pair of successfully-graded students' answers.

    Returns a list of {student_a, student_b, avg_similarity, matched_questions}
    dicts, most-suspicious first, capped to the most significant matches.
    """
    students: list[tuple[str, dict[str, str]]] = []
    for r in results:
        if not r.get("ok"):
            continue
        text = (r.get("extracted_text") or "").strip()
        if not text:
            continue
        name = r.get("student_name") or r.get("file") or "Student"
        by_q = _split_by_question(text)
        by_q = {q: t for q, t in by_q.items() if len(t) >= _MIN_CHARS_TO_COMPARE}
        if by_q:
            students.append((name, by_q))

    flagged: list[dict[str, Any]] = []
    for i in range(len(students)):
        name_a, qa = students[i]
        for j in range(i + 1, len(students)):
            name_b, qb = students[j]
            if name_a == name_b:
                continue
            shared = sorted(set(qa) & set(qb), key=lambda x: int(x))
            if not shared:
                continue
            matches = []
            for q in shared:
                ratio = difflib.SequenceMatcher(None, qa[q], qb[q]).ratio()
                if ratio >= _SIMILARITY_THRESHOLD:
                    matches.append({"q": f"Q{q}", "similarity": round(ratio * 100, 1)})
            if len(matches) >= _MIN_MATCHED_QUESTIONS:
                avg = round(sum(m["similarity"] for m in matches) / len(matches), 1)
                flagged.append({
                    "student_a": name_a,
                    "student_b": name_b,
                    "matched_questions": matches,
                    "avg_similarity": avg,
                })

    flagged.sort(key=lambda x: -x["avg_similarity"])
    return flagged[:_MAX_PAIRS_RETURNED]
