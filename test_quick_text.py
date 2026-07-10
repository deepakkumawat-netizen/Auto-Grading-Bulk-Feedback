import requests
import json

backend_url = "http://127.0.0.1:8031"

# A math question and answer sheet
rubric = """Q1 (5 marks): Step-by-step marking process:
- Formula: Write quadratic formula x = (-b +/- sqrt(b^2 - 4ac)) / 2a (1 mark)
- Substitution: substitute a=1, b=-5, c=6 to get x = (5 +/- sqrt(25 - 24)) / 2 (2 marks)
- Intermediate step: x = (5 +/- 1) / 2 (1 mark)
- Final answer: x = 3 or x = 2 (1 mark)"""

student_answer = """Student Name: Rohit Sharma
Subject: Mathematics

Q1 Answer:
We need to solve x^2 - 5x + 6 = 0.
Here, a = 1, b = -5, c = 6.
Using the quadratic formula:
x = (-b +/- sqrt(b^2 - 4ac)) / 2a
Substituting the values:
x = (5 +/- sqrt((-5)^2 - 4*1*6)) / (2*1)
x = (5 +/- sqrt(25 - 24)) / 2
x = (5 +/- 1) / 2
Therefore, x = (5 + 1) / 2 = 6 / 2 = 3.
Also, x = (5 - 1) / 2 = 4 / 2 = 2.
Final answer is x = 3 and x = 2."""

print("1. Sending text-based student copy for step-by-step grading...")
files = [("files", ("rohit_math.txt", student_answer.encode("utf-8"), "text/plain"))]
data = {
    "rubric": rubric,
    "verify": "true",
    "ncert_check": "true",
    "study_plan": "true",
    "total_marks": "5",
    "grade_override": "10",
    "subject_override": "Mathematics"
}

r = requests.post(f"{backend_url}/api/grade/bulk", files=files, data=data)
print("Status Code:", r.status_code)
if r.status_code == 200:
    res = r.json().get("results", [])[0]
    print("\n=== STEP-BY-STEP GRADING RESULT ===")
    print(f"Student: {res.get('student_name')}")
    print(f"Marks: {res.get('marks_awarded')} / {res.get('marks_total')} ({res.get('percentage')}%)")
    print("\nFeedback details:")
    for pq in res.get("per_question", []):
        print(f"  - {pq.get('q')}: {pq.get('marks_awarded')} / {pq.get('marks_total')}")
        print(f"    AI Examiner Note: {pq.get('feedback')}")
else:
    print("Error:", r.text)
