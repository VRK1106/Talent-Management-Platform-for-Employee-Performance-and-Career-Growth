from pathlib import Path

txt = Path("app.py").read_text(encoding="utf-8")
lines = txt.splitlines()

for i, l in enumerate(lines, 1):
    if "get_collection" in l or "collection.get" in l or "coll.get" in l:
        print(f"L{i}: {l.strip()}")
