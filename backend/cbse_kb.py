"""CBSE curriculum RAG for ClassroomAI — loaded from cbse_toc.json
(parsed from GRADE Wise TOC.xlsx: 12 grades, all subjects, ~769 chapters).

Used to ground every generator's output in the official CBSE curriculum.
"""
import json
import os
import re

_HERE = os.path.dirname(__file__)
_TOC = os.path.join(_HERE, "cbse_toc.json")

try:
    with open(_TOC, encoding="utf-8") as _f:
        CBSE_KB = json.load(_f)
except Exception:
    CBSE_KB = {}

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "as", "and", "or", "but", "if", "this",
    "that", "what", "how", "explain", "create", "make", "worksheet", "lesson",
    "questions", "quiz", "test", "grade", "students", "chapter", "topic",
}


def _grade_key(grade_level) -> str:
    """Normalize various grade inputs to 'Grade N'."""
    s = str(grade_level or "")
    m = re.search(r"\d+", s)
    return f"Grade {m.group()}" if m else ""


def _tokenize(text):
    text = (text or "").lower()
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in text).split()
    return {w for w in words if len(w) > 2 and w not in STOP_WORDS}


def get_subjects(grade_level) -> list:
    return list(CBSE_KB.get(_grade_key(grade_level), {}).keys())


def get_chapters(grade_level, subject) -> list:
    return CBSE_KB.get(_grade_key(grade_level), {}).get(subject, [])


def find_chapter(grade_level, subject, chapter_ref) -> dict:
    """Resolve a chapter reference ('Chapter 2', 'Human Reproduction',
    'Chapter 2. Human Reproduction') against the TOC. Returns the chapter
    dict or {}."""
    if not chapter_ref:
        return {}
    ref = str(chapter_ref).lower().strip()
    for ch in get_chapters(grade_level, subject):
        ch_label = (ch.get("ch") or "").lower().strip()
        ch_title = (ch.get("title") or "").lower().strip()
        if ref == ch_label or ref == ch_title:
            return ch
        if ch_label and ch_title and ref == f"{ch_label}. {ch_title}":
            return ch
        # also tolerate "chapter 2. human reproduction" vs "chapter 2: human..."
        if ch_label and ch_title and ref == f"{ch_label}: {ch_title}":
            return ch
    return {}


def concepts_to_topics(concepts) -> list:
    """Turn a chapter's `concepts` string into a clean list of student-facing
    subtopic chip labels.

    - Splits on commas AND semicolons at the outer level (so phrases like
      'gametogenesis (spermatogenesis & oogenesis)' stay intact).
    - Strips a leading 'Theme:' / 'Topic:' lead-in like
      'Teamwork Logistics:' that some literature entries carry.
    - Strips trailing punctuation, removes a leading 'and '.
    - Drops items longer than 90 characters — those are clearly whole
      sentences (often the case for Hindi/English-literature entries), not
      real subtopics, and returning them as chips produced absurd chip
      titles like 'Teamwork Logistics: Exploring insect biology (ants);
      building compound letters (Sanyukt Akshar) alongside diligence
      themes'.
    """
    if not concepts:
        return []
    text = str(concepts).strip()
    # drop a leading 'Theme: ' / 'Topic: ' lead-in (single colon, before any
    # paren) — keeps the actual subtopic listing.
    m = re.match(r"^[A-Za-z][A-Za-z &/'-]{2,40}:\s*(.+)$", text)
    if m:
        text = m.group(1)
    # split on commas or semicolons that are NOT inside parentheses
    parts = re.split(r"[,;]\s*(?![^()]*\))", text)
    out = []
    for p in parts:
        s = p.strip().rstrip(".").strip()
        if s.lower().startswith("and "):
            s = s[4:].strip()
        if not s:
            continue
        # drop chip-as-whole-sentence — those are not real subtopics
        if len(s) > 90:
            continue
        # title-case the first letter so chips read like proper labels
        out.append(s[0].upper() + s[1:] if len(s) > 1 else s.upper())
    return out


# Subjects taught IN a target language (the lesson itself must be written
# in that language). Maps subject name → script/language directive used in
# the LLM system prompt.
LANGUAGE_SUBJECTS = {
    "Hindi": "Hindi (Devanagari script)",
    "Hindi Course A": "Hindi (Devanagari script)",
    "Hindi Course B": "Hindi (Devanagari script)",
    "Sanskrit": "Sanskrit (Devanagari script)",
    "Urdu": "Urdu (Nastaliq script)",
    "Punjabi": "Punjabi (Gurmukhi script)",
    "Tamil": "Tamil",
    "Telugu": "Telugu",
    "Kannada": "Kannada",
    "Malayalam": "Malayalam",
    "Marathi": "Marathi",
    "Bengali": "Bengali",
    "Gujarati": "Gujarati",
}


def language_for_subject(subject) -> str:
    """Return the language a lesson should be written in for this subject,
    or '' for English-medium subjects (Maths, Science, EVS, English, etc).
    Match is case-insensitive and tolerant of trailing extras."""
    if not subject:
        return ""
    s = str(subject).strip()
    for key, lang in LANGUAGE_SUBJECTS.items():
        if s.lower() == key.lower():
            return lang
    # tolerant prefix match for things like 'Hindi A', 'Sanskrit Elective'
    for key, lang in LANGUAGE_SUBJECTS.items():
        if s.lower().startswith(key.lower() + " "):
            return lang
    return ""


def retrieve_context(query, grade_level, subject="", top_k=3) -> str:
    """Find the most relevant CBSE chapters for a topic/query and return a
    formatted context block to ground the generator prompt."""
    gk = _grade_key(grade_level)
    grade_data = CBSE_KB.get(gk, {})
    if not grade_data:
        return ""

    # Search within the chosen subject if given, else across all subjects of the grade
    candidates = []
    subjects = [subject] if subject and subject in grade_data else list(grade_data.keys())
    for subj in subjects:
        for ch in grade_data.get(subj, []):
            candidates.append((subj, ch))

    if not candidates:
        return ""

    q_tokens = _tokenize(query)
    if not q_tokens:
        return ""

    scored = []
    for subj, ch in candidates:
        text = (ch.get("title", "") + " " + ch.get("concepts", "")
                + " " + ch.get("unit", "") + " " + ch.get("stream", ""))
        overlap = len(q_tokens & _tokenize(text))
        if overlap > 0:
            scored.append((overlap, subj, ch))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]
    if not top:
        return ""

    lines = [f"OFFICIAL CBSE {gk} CURRICULUM CONTEXT (align content to this):"]
    for _, subj, ch in top:
        unit = ch.get("unit") or ch.get("stream")
        unit_str = f" [{unit}]" if unit else ""
        concepts = ch.get("concepts", "")
        lines.append(f"- {subj} · {ch.get('ch','')}: {ch.get('title','')}{unit_str}"
                     + (f" — {concepts}" if concepts else ""))
    return "\n".join(lines)
