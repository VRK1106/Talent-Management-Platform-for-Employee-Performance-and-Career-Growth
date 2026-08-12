from pathlib import Path

path = Path("src/sprints.py")
content = path.read_text(encoding="utf-8")

old = "sqlite3.connect(str(_DB_PATH))"
new = "get_db_connection(_DB_PATH)"

count = content.count(old)
print(f"Found {count} occurrences of '{old}' in src/sprints.py")

content_updated = content.replace(old, new)
path.write_text(content_updated, encoding="utf-8")
print("Updated src/sprints.py successfully!")
