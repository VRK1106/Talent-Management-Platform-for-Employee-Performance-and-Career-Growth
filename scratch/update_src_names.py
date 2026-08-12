from pathlib import Path

src_dir = Path("src")
new_name = "Talent Management Platform for Employee Performance and Career Growth"

for p in src_dir.glob("*.py"):
    txt = p.read_text(encoding="utf-8")
    txt_updated = txt.replace("Talent Sphere Elevate", new_name).replace("Talent Sphere", "Talent Management Platform")
    if txt_updated != txt:
        p.write_text(txt_updated, encoding="utf-8")
        print(f"Updated platform title in src/{p.name}")

print("All src python files updated!")
