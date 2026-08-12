from pathlib import Path

txt = Path("app.py").read_text(encoding="utf-8")
lines = txt.splitlines()

for i, line in enumerate(lines, 1):
    if "@app.route" in line:
        print(f"L{i}: {line.strip()}")
