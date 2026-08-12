from pathlib import Path

p = Path("app.py")
txt = p.read_text(encoding="utf-8")

c1 = txt.count("sqlite3.connect(str(_DB_PATH))")
c2 = txt.count("sqlite3.connect(_DB_PATH)")

print(f"Found {c1+c2} raw sqlite3.connect in app.py")

txt = txt.replace("sqlite3.connect(str(_DB_PATH))", "get_db_connection(_DB_PATH)")
txt = txt.replace("sqlite3.connect(_DB_PATH)", "get_db_connection(_DB_PATH)")

p.write_text(txt, encoding="utf-8")
print("Updated app.py successfully!")
