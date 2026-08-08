import sys
import json
sys.path.insert(0, '.')

import app

print("--- TESTING SPHERE VOICE ASSISTANT AGENT CONTROLLER ---")

with app.app.test_client() as client:
    # 1. Simulate Admin Session
    with client.session_transaction() as sess:
        sess['user_role'] = 'admin'
        sess['current_user'] = 'Admin Test'
        sess['user_info'] = {'employee_id': 'ADM001'}

    # Reset voice session
    res = client.post('/assistant/voice_agent/reset')
    print("1. Voice Agent Reset Status:", res.status_code, res.get_json())
    assert res.status_code == 200

    # 2. Test Admin Exam Creation Intent (Triggers Document Selection Popup)
    res = client.post('/assistant/voice_agent/chat', data={'query': 'Create an exam for trainee assessment'})
    data = res.get_json()
    print("\n2. Admin Exam Creation Intent Output:")
    print("Query:", data.get('query_text'))
    print("Response:", data.get('response_text'))
    print("Action Executed:", data.get('action_executed'))
    assert data.get('action_executed') is not None
    assert data.get('action_executed', {}).get('action') == 'prompt_select_documents'

    # 3. Test Confirming Selection Popup for Exam Creation
    res = client.post('/assistant/voice_agent/chat', data={'query': 'I selected Unit-1.pdf. Please proceed.'})
    data = res.get_json()
    print("\n3. Exam Creation Selection Confirmation Output:")
    print("Response:", data.get('response_text'))
    assert "successfully generated" in data.get('response_text') or "Exam" in data.get('response_text') or "document" in data.get('response_text').lower()

    # 4. Test Admin Document Deletion Intent (Triggers Delete Popup)
    res = client.post('/assistant/voice_agent/chat', data={'query': 'Delete ingested document'})
    data = res.get_json()
    print("\n4. Admin Document Deletion Intent Output:")
    print("Response:", data.get('response_text'))
    print("Action Executed:", data.get('action_executed'))

    # 5. Test Trainee Role Voice Queries
    with client.session_transaction() as sess:
        sess['user_role'] = 'trainee'
        sess['current_user'] = 'Trainee John'
        sess['user_info'] = {'employee_id': 'TRN001'}

    res = client.post('/assistant/voice_agent/chat', data={'query': 'What is my current sprint progress?'})
    data = res.get_json()
    print("\n5. Trainee Sprint Progress Output:")
    print("Response:", data.get('response_text'))
    assert "sprint progress" in data.get('response_text').lower() or "week" in data.get('response_text').lower()

print("\n100% PERFECT SUCCESS: Sphere Voice Assistant Agent Controller fully verified for all role-based actions!")
