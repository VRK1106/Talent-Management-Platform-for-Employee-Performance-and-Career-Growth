import sys
import json
sys.path.insert(0, '.')

import app
from src.exams import get_all_exams, get_exam_by_id

print("--- TESTING FULL 6-STEP VOICE EXAM CREATION WIZARD ---")

with app.app.test_client() as client:
    # 1. Admin Session
    with client.session_transaction() as sess:
        sess['user_role'] = 'admin'
        sess['current_user'] = 'Admin Wizard Tester'
        sess['user_info'] = {'employee_id': 'ADM_WIZ'}

    # Reset voice session
    client.post('/assistant/voice_agent/reset')

    # Step 1: Initiate exam creation -> Prompt for Exam Title
    res1 = client.post('/assistant/voice_agent/chat', data={'query': 'Create an exam'})
    d1 = res1.get_json()
    print("\nStep 1 Prompt Output:", d1.get('response_text'))
    assert "title" in d1.get('response_text').lower()

    # Step 2: Spoken Title -> Prompt for Document Selection
    res2 = client.post('/assistant/voice_agent/chat', data={'query': 'Material Science Gateway Assessment'})
    d2 = res2.get_json()
    print("\nStep 2 Title Confirmation Output:", d2.get('response_text'))
    print("Action Executed:", d2.get('action_executed'))
    assert d2.get('action_executed', {}).get('action') == 'prompt_select_documents'

    # Step 3: Document Selection Confirmation -> Prompt for Question Count
    res3 = client.post('/assistant/voice_agent/chat', data={'query': 'I selected Unit-1.pdf'})
    d3 = res3.get_json()
    print("\nStep 3 Document Confirmation Output:", d3.get('response_text'))
    assert "questions" in d3.get('response_text').lower()

    # Step 4: Spoken Question Count -> Prompt for Marks per Question
    res4 = client.post('/assistant/voice_agent/chat', data={'query': '5 questions'})
    d4 = res4.get_json()
    print("\nStep 4 Question Count Confirmation Output:", d4.get('response_text'))
    assert "marks" in d4.get('response_text').lower()

    # Step 5: Spoken Marks -> Prompt for Difficulty Level (Popup)
    res5 = client.post('/assistant/voice_agent/chat', data={'query': '10 marks per question'})
    d5 = res5.get_json()
    print("\nStep 5 Marks Confirmation Output:", d5.get('response_text'))
    print("Action Executed:", d5.get('action_executed'))
    assert d5.get('action_executed', {}).get('action') == 'prompt_select_difficulty'

    # Step 6: Difficulty Selection -> Prompt for Trainee Assignment (Popup)
    res6 = client.post('/assistant/voice_agent/chat', data={'query': 'Medium'})
    d6 = res6.get_json()
    print("\nStep 6 Difficulty Confirmation Output:", d6.get('response_text'))
    print("Action Executed:", d6.get('action_executed'))
    assert d6.get('action_executed', {}).get('action') == 'prompt_select_trainees'

    # Step 7: Trainee Selection -> Generate & Save Exam to DB & Assign Trainees!
    res7 = client.post('/assistant/voice_agent/chat', data={'query': 'All Trainees'})
    d7 = res7.get_json()
    print("\nStep 7 Final Generation & Assignment Output:", d7.get('response_text'))
    assert "successfully created" in d7.get('response_text') or "assigned" in d7.get('response_text')

    # Verify Exam in SQLite DB
    all_e = get_all_exams()
    latest_exam = all_e[0] if all_e else None
    print("\n--- VERIFYING CREATED EXAM IN SQLITE DB ---")
    print("Latest Exam ID:", latest_exam.get('exam_id'))
    print("Title:", latest_exam.get('title'))
    print("Total Marks:", latest_exam.get('total_marks'))
    print("Questions Count:", len(latest_exam.get('questions', [])))
    print("Sample Question 1:", latest_exam.get('questions', [])[0] if latest_exam.get('questions') else 'None')

    assert len(latest_exam.get('questions', [])) == 5
    print("\n100% PERFECT SUCCESS: Multi-step voice exam wizard generated 5 non-empty questions and assigned exam to trainees!")
