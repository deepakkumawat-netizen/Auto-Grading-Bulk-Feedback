"""CBSE-approved languages for answer writing and their scripts/states.

CBSE national policy: students write answers in the medium of instruction of
their school. All languages below are officially approved by CBSE for answer
writing in examinations.
"""
from __future__ import annotations

# Complete map: language name → { script, states, cbse_code }
CBSE_LANGUAGES: dict[str, dict] = {
    "Hindi":       {"script": "Devanagari",  "code": "002",
                    "states": ["All India"], "sample": "प्रकाश संश्लेषण"},
    "English":     {"script": "Latin",        "code": "101",
                    "states": ["All India"], "sample": "Photosynthesis"},
    "Bengali":     {"script": "Bengali",      "code": "005",
                    "states": ["West Bengal", "Tripura", "Assam"],
                    "sample": "সালোকসংশ্লেষণ"},
    "Telugu":      {"script": "Telugu",       "code": "042",
                    "states": ["Andhra Pradesh", "Telangana"],
                    "sample": "కిరణజన్య సంయోగక్రియ"},
    "Marathi":     {"script": "Devanagari",   "code": "019",
                    "states": ["Maharashtra", "Goa"],
                    "sample": "प्रकाशसंश्लेषण"},
    "Tamil":       {"script": "Tamil",        "code": "041",
                    "states": ["Tamil Nadu", "Puducherry"],
                    "sample": "ஒளிச்சேர்க்கை"},
    "Gujarati":    {"script": "Gujarati",     "code": "016",
                    "states": ["Gujarat"], "sample": "પ્રકાશસંશ્લેષણ"},
    "Kannada":     {"script": "Kannada",      "code": "017",
                    "states": ["Karnataka"], "sample": "ದ್ಯುತಿಸಂಶ್ಲೇಷಣೆ"},
    "Odia":        {"script": "Odia",         "code": "012",
                    "states": ["Odisha"], "sample": "ଆଲୋକ ସଂଶ୍ଳେଷଣ"},
    "Punjabi":     {"script": "Gurmukhi",     "code": "029",
                    "states": ["Punjab", "Haryana", "Chandigarh"],
                    "sample": "ਪ੍ਰਕਾਸ਼ ਸੰਸ਼ਲੇਸ਼ਣ"},
    "Urdu":        {"script": "Nastaliq",     "code": "303",
                    "states": ["Jammu", "UP", "Bihar", "Telangana"],
                    "sample": "روشنی ترکیب"},
    "Malayalam":   {"script": "Malayalam",    "code": "022",
                    "states": ["Kerala", "Lakshadweep"],
                    "sample": "പ്രകാശസംശ്ലേഷണം"},
    "Assamese":    {"script": "Bengali",      "code": "025",
                    "states": ["Assam"], "sample": "পোহৰ সংশ্লেষণ"},
    "Sanskrit":    {"script": "Devanagari",   "code": "122",
                    "states": ["All India"], "sample": "प्रकाशसंश्लेषणम्"},
    "Nepali":      {"script": "Devanagari",   "code": "038",
                    "states": ["Sikkim", "Darjeeling"],
                    "sample": "प्रकाश संश्लेषण"},
    "Manipuri":    {"script": "Meitei Mayek", "code": "014",
                    "states": ["Manipur"], "sample": "ꯄꯥꯎꯈꯨꯝ ꯁꯤꯡꯊꯦꯁꯤꯁ"},
    "Sindhi":      {"script": "Devanagari/Arabic", "code": "039",
                    "states": ["Gujarat", "Maharashtra", "Rajasthan"],
                    "sample": "روشني جوڙجڪ"},
    "Kashmiri":    {"script": "Nastaliq",     "code": "021",
                    "states": ["J&K", "Ladakh"], "sample": "روشنی ترکیب"},
    "Konkani":     {"script": "Devanagari",   "code": "040",
                    "states": ["Goa", "Kerala", "Karnataka"],
                    "sample": "प्रकाशसंश्लेषण"},
    "Bodo":        {"script": "Devanagari",   "code": "034",
                    "states": ["Assam", "West Bengal"],
                    "sample": "आलोगनि गोनांथि"},
    "Dogri":       {"script": "Devanagari",   "code": "007",
                    "states": ["J&K", "Himachal Pradesh"],
                    "sample": "प्रकाश संश्लेषण"},
    "Maithili":    {"script": "Devanagari",   "code": "057",
                    "states": ["Bihar", "Jharkhand"],
                    "sample": "प्रकाश संश्लेषण"},
    "Santali":     {"script": "Ol Chiki",     "code": "035",
                    "states": ["Jharkhand", "Odisha", "West Bengal"],
                    "sample": "ᱯᱷᱚᱴᱚᱥᱤᱱᱛᱷᱮᱥᱤᱥ"},
    "Mizo":        {"script": "Latin",        "code": "036",
                    "states": ["Mizoram"], "sample": "Chhanna inbuatsaihna"},
    "Arabic":      {"script": "Arabic",       "code": "201",
                    "states": ["Select areas"], "sample": "التمثيل الضوئي"},
}

# All unique scripts used across CBSE-approved languages
ALL_SCRIPTS: list[str] = sorted({v["script"] for v in CBSE_LANGUAGES.values()})

# OCR instruction block for Gemini — tells it every script it may encounter
def ocr_language_block() -> str:
    script_examples = "\n".join(
        f"  • {lang} ({info['script']} script, {', '.join(info['states'][:2])}): "
        f"e.g. \"{info['sample']}\""
        for lang, info in CBSE_LANGUAGES.items()
        if lang not in ("English",)  # English is obvious; skip
    )
    return (
        "🌐 MULTI-SCRIPT TRANSCRIPTION — CBSE students write in their state language:\n"
        f"{script_examples}\n"
        "Rules:\n"
        "  - Transcribe every script exactly as written — DO NOT translate.\n"
        "  - For Devanagari, Bengali, Telugu, Tamil, Gujarati, Kannada, Odia, "
        "Gurmukhi, Malayalam, Nastaliq, Meitei Mayek, Ol Chiki: copy the characters faithfully.\n"
        "  - Mixed-language answers (e.g. Telugu + English technical terms, "
        "Hindi + English equations) are very common — keep both scripts.\n"
        "  - If a script is unclear or a word is illegible, write it as [?] — never substitute "
        "a different language."
    )


# Grading instruction block — tells the AI to grade concepts in all CBSE languages
def grading_language_block() -> str:
    lang_list = ", ".join(CBSE_LANGUAGES.keys())
    return f"""🌐 MULTI-LANGUAGE GRADING — CBSE National Policy
CBSE officially permits answer writing in the school's medium of instruction.
Approved languages: {lang_list}.

Rules — apply to every question:
  ✅ A correct concept explained in Telugu, Bengali, Tamil, Marathi, Gujarati, Kannada,
     Odia, Punjabi, Urdu, Malayalam, Assamese, Hindi, Nepali, or any other CBSE language
     earns EXACTLY the same marks as the same concept in English.
  ✅ Mixed-language answers (e.g. Kannada sentences + English technical terms like
     "photosynthesis" or chemical formulas) are standard practice — award full marks
     if the concept is correct.
  ✅ Regional terms for NCERT concepts are valid:
     Tamil "ஒளிச்சேர்க்கை" = Hindi "प्रकाश संश्लेषण" = English "photosynthesis" — all correct.
  ❌ Do NOT deduct marks for writing in a regional language instead of English.
  ❌ Do NOT penalise transliteration (e.g. "prakaash sanshleshan" written in English script
     for the Hindi term).
  ⚠  Only flag "language" mistake type when the language choice creates genuine AMBIGUITY
     about whether the concept is correct — not simply because it's non-English.

DETECTED LANGUAGE: Identify the primary language/script the student used and set
  \"detected_language\" in your JSON response (e.g. "Telugu", "Hindi", "Bengali", "Tamil",
  "English", "Hindi+English", "Telugu+English", etc.)."""
