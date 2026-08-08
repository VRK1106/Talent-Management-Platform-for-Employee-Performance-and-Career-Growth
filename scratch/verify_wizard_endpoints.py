import sys
import json
sys.path.insert(0, '.')

import app
from src.exams import get_all_exams

print("--- TESTING INTERACTIVE EXAM WIZARD BACKEND ENDPOINTS ---")

with app.app.test_client() as client:
    with client.session_transaction() as sess:
        sess['user_role'] = 'admin'
        sess['current_user'] = 'Admin Tester'

    # 1. Test /assistant/wizard/generate
    gen_payload = {
        "docs": ["Unit-1.pdf"],
        "sections": [
            {"name": "Section A - MCQ", "type": "mcq", "count": 3, "marks": 10}
        ],
        "difficulty": {"easy": 50, "medium": 50, "hard": 0}
    }
    r_gen = client.post('/assistant/wizard/generate', json=gen_payload)
    d_gen = r_gen.get_json()
    print("\n1. /assistant/wizard/generate status:", r_gen.status_code)
    print("Generated Questions Count:", len(d_gen.get('questions', [])))
    print("Sample Q1:", d_gen.get('questions', [])[0] if d_gen.get('questions') else 'None')
    assert r_gen.status_code == 200
    assert len(d_gen.get('questions', [])) == 3

    # 2. Test /assistant/wizard/save
    save_payload = {
        "title": "Interactive Wizard Endpoint Assessment",
        "description": "Created via interactive wizard AJAX",
        "total_marks": 30,
        "questions": d_gen.get('questions', []),
        "settings": {
            "scheduling": {"assignee_id": "all", "end_date": "2026/12/31"}
        }
    }
    r_save = client.post('/assistant/wizard/save', json=save_payload)
    d_save = r_save.get_json()
    print("\n2. /assistant/wizard/save status:", r_save.status_code)
    print("Save Response:", d_save)
    assert r_save.status_code == 200
    assert d_save.get('status') == 'success'

    # Verify Exam in DB
    all_e = get_all_exams()
    latest = all_e[0]
    print("\n--- DB VERIFICATION ---")
    print("Latest Exam ID:", latest.get('exam_id'))
    print("Title:", latest.get('title'))
    print("Questions count in DB:", len(latest.get('questions', [])))
    assert len(latest.get('questions', [])) == 3

print("\n100% PERFECT SUCCESS: /assistant/wizard/generate and /assistant/wizard/save endpoints work flawlessly!")
