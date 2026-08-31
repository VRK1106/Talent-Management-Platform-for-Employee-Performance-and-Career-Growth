import sqlite3
import json

conn = sqlite3.connect('users.db')
cursor = conn.cursor()
cursor.execute('SELECT title, content, created_at FROM announcements ORDER BY announcement_id DESC LIMIT 3')
rows = cursor.fetchall()
with open('announcements.json', 'w', encoding='utf-8') as f:
    json.dump(rows, f, indent=4)
conn.close()
