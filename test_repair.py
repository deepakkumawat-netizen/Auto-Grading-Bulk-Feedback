import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Import _repair_json_quotes from llm_router
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "backend")))
from llm_router import _repair_json_quotes

with open("raw_grader_response.json", "r", encoding="utf-8") as f:
    raw = f.read()

# Strip markdown
import re
fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
content = fenced.group(1) if fenced else raw
brace = re.search(r"\{.*\}", raw, re.DOTALL)
content = brace.group(0) if brace else raw

print("Raw length:", len(content))
try:
    parsed = json.loads(content)
    print("Parsed raw successfully!")
except Exception as e:
    print("Raw parse failed:", e)
    repaired = _repair_json_quotes(content)
    print("\nRepaired length:", len(repaired))
    try:
        parsed_rep = json.loads(repaired)
        print("Parsed repaired successfully!")
    except Exception as e2:
        print("Repaired parse failed:", e2)
        # Find where it failed in repaired
        lines = repaired.splitlines()
        for idx, l in enumerate(lines, 1):
            if idx >= 15 and idx <= 25:
                print(f"{idx}: {l}")
