import requests
import json
import os
import sys
import io

# Fix Windows console UTF-8 printing
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

backend_url = "http://127.0.0.1:8031"
paper_path = r"c:\Users\DEEPAK\Desktop\paper,answer sheet,schema\3-S-1_Hindi Course A.pdf"
solution_path = r"c:\Users\DEEPAK\Desktop\paper,answer sheet,schema\X_002_ms_unsigned_3S1,2,3-encrypted.pdf"
student_path = r"c:\Users\DEEPAK\Desktop\paper,answer sheet,schema\Hindi_A.pdf"

if not os.path.exists(paper_path) or not os.path.exists(solution_path) or not os.path.exists(student_path):
    print("Error: Files not found.")
    exit(1)

# Step 1: Generate Rubric
print("1. Generating Rubric...")
with open(paper_path, "rb") as f_paper, open(solution_path, "rb") as f_sol:
    files = {
        "paper": (paper_path, f_paper, "application/pdf"),
        "solution": (solution_path, f_sol, "application/pdf")
    }
    r = requests.post(f"{backend_url}/api/rubric/from-paper", files=files)

print("Rubric Gen Status Code:", r.status_code)
try:
    rubric_data = r.json()
    print("Rubric Gen Response:", json.dumps(rubric_data, indent=2))
except Exception as e:
    print("Rubric Gen Response Text:", r.text)
    sys.exit(1)

rubric_text = rubric_data.get("rubric", "")
total_marks = rubric_data.get("total_marks", 80)

# Step 2: Grade
print("2. Grading Student Sheet (Step-by-Step)...")
with open(student_path, "rb") as f_student:
    files = [("files", (student_path, f_student, "application/pdf"))]
    data = {
        "rubric": rubric_text,
        "verify": "true",
        "ncert_check": "false",  # Disabled!
        "study_plan": "true",
        "total_marks": str(total_marks),
        "grade_override": "10",
        "subject_override": "Mathematics",
    }
    r = requests.post(f"{backend_url}/api/grade/bulk", files=files, data=data)

print("Grading Status Code:", r.status_code)
try:
    grade_data = r.json()
    print("Full Grading Response:", json.dumps(grade_data, indent=2))
except Exception as e:
    print("Grading Response Text:", r.text)
    sys.exit(1)
print("\n=== GRADING RESULTS ===")
for res in grade_data.get("results", []):
    if res.get("ok"):
        print(f"Student: {res.get('student_name')}")
        print(f"Marks: {res.get('marks_awarded')} / {res.get('marks_total')}")
        print("\nStep-by-Step Marks:")
        # Show first 15 questions for brevity
        for pq in res.get("per_question", [])[:15]:
            print(f"  - {pq.get('q')}: {pq.get('marks_awarded')} / {pq.get('marks_total')} ({pq.get('format')})")
            print(f"    Feedback: {pq.get('feedback')}")
    else:
        print("Error:", res.get("error"))
