from pathlib import Path

src_dir = Path("src")
total_replaced = 0

for p in src_dir.glob("*.py"):
    txt = p.read_text(encoding="utf-8")
    original = txt
    
    # Ensure get_db_connection is imported if _DB_PATH is imported from src.users
    if "from src.users import" in txt and "get_db_connection" not in txt:
        txt = txt.replace("from src.users import ", "from src.users import get_db_connection, ")
        
    c1 = txt.count("sqlite3.connect(str(_DB_PATH))")
    c2 = txt.count("sqlite3.connect(_DB_PATH)")
    
    if c1 > 0 or c2 > 0:
        txt = txt.replace("sqlite3.connect(str(_DB_PATH))", "get_db_connection(_DB_PATH)")
        txt = txt.replace("sqlite3.connect(_DB_PATH)", "get_db_connection(_DB_PATH)")
        p.write_text(txt, encoding="utf-8")
        print(f"Replaced {c1+c2} raw sqlite3.connect in {p.name}")
        total_replaced += (c1 + c2)

print(f"Total replaced across src/: {total_replaced}")
