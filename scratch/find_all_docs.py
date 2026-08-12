from pathlib import Path

txt = Path("app.py").read_text(encoding="utf-8")
lines = txt.splitlines()

for i, l in enumerate(lines, 1):
    if "all_docs" in l or "source_names" in l:
        print(f"L{i}: {l.strip()}")
