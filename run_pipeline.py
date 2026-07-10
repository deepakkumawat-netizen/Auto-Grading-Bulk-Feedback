import requests
import os
import sys
import io
import json

# Fix Windows console UTF-8 printing
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Target URLs
backend_url = "http://127.0.0.1:8031"

# Sample files in the workspace
question_paper_path = "Math_Stand.pdf"          # 1. Question Paper (13MB PDF)
answer_key_path = "MathsStandard-MS.pdf"        # 2. Solution Key / Answer Key (867KB PDF)
student_sheet_path = "Math_Stand.pdf"           # 3. Student Answer Sheet

def check_files():
    for name, path in [("Question Paper", question_paper_path), 
                       ("Answer Key", answer_key_path), 
                       ("Student Sheet", student_sheet_path)]:
        if not os.path.exists(path):
            print(f"Error: {name} not found at '{path}'. Please ensure it exists.")
            sys.exit(1)

def run_pipeline():
    check_files()
    print("==================================================")
    print("STARTING GRADING PIPELINE")
    print("==================================================")

    # Step 1: Rubric Generation from Question Paper + Answer Key
    print(f"\n[Step 1] Generating Rubric from Question Paper and Solution Key...")
    print(f"  - Question Paper: {question_paper_path}")
    print(f"  - Solution Key: {answer_key_path}")
    
    with open(question_paper_path, "rb") as f_paper, open(answer_key_path, "rb") as f_sol:
        files = {
            "paper": (os.path.basename(question_paper_path), f_paper, "application/pdf"),
            "solution": (os.path.basename(answer_key_path), f_sol, "application/pdf")
        }
        response = requests.post(f"{backend_url}/api/rubric/from-paper", files=files)
        
    if response.status_code != 200:
        print(f"Error generating rubric: HTTP {response.status_code} - {response.text}")
        sys.exit(1)

    rubric_data = response.json()
    rubric_text = rubric_data.get("rubric", "")
    total_marks = rubric_data.get("total_marks", 80)
    questions_found = rubric_data.get("questions_found", 0)

    print(f"✅ Rubric successfully generated!")
    print(f"  - Questions found: {questions_found}")
    print(f"  - Total paper marks: {total_marks}")
    print("\n--- Extracted Rubric Preview ---")
    lines = rubric_text.splitlines()
    for line in lines[:10]:
        print(f"  {line}")
    if len(lines) > 10:
        print(f"  ... ({len(lines) - 10} more lines)")
    print("--------------------------------")

    # Step 2: Student Sheet Evaluation against the generated rubric
    print(f"\n[Step 2] Evaluating Student Answer Sheet...")
    print(f"  - Student Answer Sheet: {student_sheet_path}")

    with open(student_sheet_path, "rb") as f_student:
        files = [("files", (os.path.basename(student_sheet_path), f_student, "application/pdf"))]
        data = {
            "rubric": rubric_text,
            "verify": "true",
            "study_plan": "true",
            "total_marks": str(total_marks),
            "grade_override": "10",
            "subject_override": "Mathematics",
        }
        response = requests.post(f"{backend_url}/api/grade/bulk", files=files, data=data)

    if response.status_code != 200:
        print(f"Error grading student sheet: HTTP {response.status_code} - {response.text}")
        sys.exit(1)

    grade_results = response.json().get("results", [])
    if not grade_results:
        print("Error: No grading results returned.")
        sys.exit(1)

    result = grade_results[0]
    print(f"✅ Evaluation complete!")
    print(f"\n==================================================")
    print(f"EVALUATION RESULTS")
    print(f"==================================================")
    print(f"Student Name:      {result.get('student_name')}")
    print(f"Total Score:       {result.get('marks_awarded')} / {result.get('marks_total')} ({result.get('percentage')}%)")
    print(f"Primary Language:  {result.get('detected_language')}")
    print(f"Overall Suggestion:{result.get('suggestion')}")
    print(f"Math Check Status: {result.get('math_check', {}).get('verified', False)}")
    
    print(f"\nPer-Question Breakdowns:")
    for pq in result.get("per_question", [])[:15]:
        print(f"  - {pq.get('q')}: {pq.get('marks_awarded')} / {pq.get('marks_total')} (format: {pq.get('format')})")
        print(f"    Feedback: {pq.get('feedback')}")
    
    if len(result.get("per_question", [])) > 15:
        print(f"  ... and {len(result.get('per_question', [])) - 15} more questions.")

if __name__ == "__main__":
    run_pipeline()
