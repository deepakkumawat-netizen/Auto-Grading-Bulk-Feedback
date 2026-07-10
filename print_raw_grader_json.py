import requests
import json
import os
import sys
import io
from dotenv import load_dotenv

# Add backend to path so we can import local modules
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, backend_dir)

load_dotenv(override=True)

from groq import Groq
import re

backend_url = "http://127.0.0.1:8031"
paper_path = "C:/AutoGrader/MathsStandard-MS.pdf"
student_path = "C:/AutoGrader/Math_Stand.pdf"

if not os.path.exists(paper_path) or not os.path.exists(student_path):
    sys.stderr.write(f"Error: Files not found. Paper: {paper_path}, Student: {student_path}\n")
    sys.exit(1)

# Step 1: Generate Rubric
sys.stderr.write("1. Generating Rubric...\n")
with open(paper_path, "rb") as f_paper, open(paper_path, "rb") as f_sol:
    files = {
        "paper": (paper_path, f_paper, "application/pdf"),
        "solution": (paper_path, f_sol, "application/pdf")
    }
    r = requests.post(f"{backend_url}/api/rubric/from-paper", files=files)

rubric_data = r.json()
rubric_text = rubric_data.get("rubric", "")
total_marks = rubric_data.get("total_marks", 80)
sys.stderr.write(f"Rubric generated! total_marks={total_marks}\n")

# Step 2: Extract Student Text using the real backend Vision OCR
sys.stderr.write("2. Extracting Student Text via Vision OCR...\n")
from main import _read_sheet
with open(student_path, "rb") as f_student:
    student_answer = _read_sheet(student_path, f_student.read())
sys.stderr.write(f"OCR Complete! Extracted {len(student_answer)} chars of student answer.\n")

# Save OCR text so we don't have to run it again
with open("C:/AutoGrader/extracted_student_ocr.txt", "w", encoding="utf-8") as f:
    f.write(student_answer)

# Step 3: Build Prompt
from grading_prompts import bulk_grader_prompt
system_prompt = bulk_grader_prompt(10, "Mathematics", "", rubric_text)

# Step 4: Call Groq directly and get raw response
sys.stderr.write("3. Calling Groq directly to get raw response...\n")
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

kwargs = dict(
    model=model,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Student answer:\n\n{student_answer}"}
    ],
    temperature=0.2,
    max_tokens=4000,
    response_format={"type": "json_object"}
)

try:
    rsp = client.chat.completions.create(**kwargs)
    raw = rsp.choices[0].message.content or ""
    
    with open("C:/AutoGrader/raw_grader_response.json", "w", encoding="utf-8") as f:
        f.write(raw)
    sys.stderr.write("\nSaved raw response to raw_grader_response.json\n")
    
except Exception as e:
    sys.stderr.write(f"Error during Groq call: {e}\n")
