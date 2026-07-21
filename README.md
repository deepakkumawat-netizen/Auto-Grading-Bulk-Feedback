# Auto Grade
 
AI-powered bulk answer sheet grader for CBSE schools. Upload 1–50 answer sheets (PDF/JPG/PNG), get per-student marks, step-by-step feedback, and downloadable feedback PDFs — in seconds.
 
## Features
 
- **Bulk grading** — grade up to 50 sheets in a single upload
- **Teacher Customization Panel** — set board, grade, subject, question-wise marks, grading rules, and feedback style
- **Grade-adaptive evaluation** — 5 grade tiers (1–2, 3–5, 6–8, 9–10, 11–12) with tier-appropriate strictness and feedback tone
- **Hard LLM constraints** — teacher's grading instructions become hard rules in the AI prompt, not hints
- **Auto-rubric from question paper** — upload a question paper PDF (with or without an answer key); AI generates the full rubric and auto-detects board and total marks from the header. If no answer key is provided, AI writes the expected answers itself (flagged for teacher review). Grade/subject are always manually selected by the teacher.
- **Rubric PDF export** — download the generated marking scheme as a PDF (question + expected answer + step-wise marks)
- **Save & reload configs** — exam configurations saved to SQLite, reloadable for the next exam
- **Verifier agent** — second AI reviews every grade and flags over-generous marks
- **Cross-student copy-check** — flags pairs of students with suspiciously similar answers (pure text-similarity, no LLM call)
- **Personalised study plan** — per-student next-steps plan for struggling students
- **Math verifier** — sympy-based arithmetic check catches LLM calculation errors
- **Class analytics** — score distribution, common misconceptions, top mistakes, possible copying
- **Export** — CSV grades, feedback PDFs (per student, with per-question verdict + student answer), rubric PDF, full transcript .txt

## Tech Stack

| Layer | Tech |
|-------|------|
| Frontend | React 18 + Vite, custom component library |
| Backend | FastAPI (Python), Uvicorn |
| Vision OCR | Gemini 2.5 Flash (primary), pypdfium2 for PDF pages |
| LLM grading | Gemini 2.5 Flash (multi-key rotation supported) |
| Storage | SQLite (history, rubric library, exam configs) |
| Ports | Backend: 8031 · Frontend: 5181 |

## Setup

### 1. Backend
```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env         # add your API keys
uvicorn main:app --port 8031 --reload
```

### 2. Frontend
```bash
cd frontend
npm install
npm run dev                  # http://localhost:5181
```

### 3. Quick start (Windows)
```bash
start.bat
```

### Environment variables (`backend/.env`)
```
GEMINI_API_KEY=your_gemini_key
# Optional: comma-separated list of multiple Gemini keys, round-robin rotated
# on every call. Takes priority over GEMINI_API_KEY when set.
GEMINI_API_KEYS=key_one,key_two,key_three
GEMINI_MODEL=gemini-2.5-flash
GRADE_CONCURRENCY=5
```
`GRADE_CONCURRENCY` controls how many answer sheets are graded in parallel per
bulk-grading batch (default 5). Raise it if your Gemini quota tier allows more
concurrent requests; lower it if you see 429 rate-limit errors.

`GEMINI_API_KEYS` lets you register several Gemini API keys (e.g. from
separate free-tier projects); every Gemini call — OCR, grading, verifier,
rubric generation — round-robins across them, multiplying your effective
RPM/quota ceiling roughly by the number of keys. Falls back to the single
`GEMINI_API_KEY` if unset.

## How It Works

```
Teacher uploads question paper (+ optional answer key)
        ↓
AI generates rubric (grounded on the key, or self-written if none given)
        ↓
Teacher selects grade/subject, configures exam (marks, rules, instructions)
        ↓
Teacher uploads answer sheets → clicks Grade
        ↓
Each sheet: OCR → grade-adaptive prompt
           → LLM grading with hard teacher constraints
           → verifier agent → study plan
        ↓
Results: per-student marks + verdicts, feedback PDF, class analytics, copy-check
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/grade/bulk` | Grade multiple answer sheets |
| POST | `/api/rubric/from-paper` | Generate rubric from question paper (answer key optional) |
| POST | `/api/rubric/pdf` | Download the current rubric as a marking-scheme PDF |
| GET/POST | `/api/exam-config` | List / save exam configurations |
| GET/DELETE | `/api/exam-config/{id}` | Fetch / delete one config |
| GET | `/api/health` | Service health check |
| POST | `/api/export/csv` | Export results as CSV |
| POST | `/api/feedback/zip` | Download all feedback PDFs as ZIP |

## Project Structure

```
AutoGrade/
├── backend/
│   ├── main.py                  # FastAPI app, all endpoints
│   ├── grading_prompts.py       # Grade-adaptive prompt + exam constraints
│   ├── llm_router.py            # Gemini calls (multi-key rotation) + rubric generation
│   ├── grading_graph.py         # LangGraph grading pipeline (OCR → grade → verify/study-plan)
│   ├── grade_profiles.py        # GRADE_PROFILES + SUBJECT_GRADE_RULES
│   ├── exam_config_store.py     # SQLite CRUD for exam configs
│   ├── rubric_store.py          # Rubric library
│   ├── history_store.py         # Grading session history
│   ├── agent_tools.py           # Math verifier (sympy)
│   ├── agent_features.py        # Insights chat, practice gen, class plan
│   ├── copy_check.py            # Cross-student copy/plagiarism detection
│   ├── cbse_languages.py        # Approved answer-writing languages + grading rules
│   ├── nlp_polish.py            # Feedback readability polish
│   ├── pdf_writer.py            # Feedback + rubric PDF generation
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/Grader.jsx     # Main grading UI
│   │   ├── components/
│   │   │   ├── ExamConfigPanel.jsx   # Teacher customization panel
│   │   │   ├── ClassAnalytics.jsx    # Score distribution charts
│   │   │   ├── AgentPanel.jsx        # AI insights panel
│   │   │   └── RubricLibrary.jsx     # Saved rubric browser
│   │   └── styles/app.css
│   └── package.json
└── start.bat
```

---

Built for teachers at CBSE schools. Powered by [Codevidhya](https://codevidhya.com).
