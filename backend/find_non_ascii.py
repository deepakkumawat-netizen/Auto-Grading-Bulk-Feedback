import os

files = ["grading_prompts.py", "llm_router.py", "main.py", "cbse_languages.py"]
output = []
for f in files:
    path = os.path.join(os.path.dirname(__file__), f)
    if not os.path.exists(path):
        continue
    output.append(f"\n=== Non-ASCII in {f} ===\n")
    with open(path, "r", encoding="utf-8") as file:
        for idx, line in enumerate(file, 1):
            non_ascii = [(c, ord(c)) for c in line if ord(c) > 127]
            if non_ascii:
                chars_str = ''.join(c[0] for c in non_ascii)
                output.append(f"Line {idx}: {chars_str} (ords: {[c[1] for c in non_ascii]})\n")

with open(os.path.join(os.path.dirname(__file__), "non_ascii_results.txt"), "w", encoding="utf-8") as out_file:
    out_file.writelines(output)
print("Done! Saved to non_ascii_results.txt")
