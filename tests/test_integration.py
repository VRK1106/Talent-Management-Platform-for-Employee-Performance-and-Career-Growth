import os
import io
import json
import pytest
from unittest.mock import patch, MagicMock

# Import the Flask app
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test_secret_key'
    with app.test_client() as client:
        yield client

def test_voice_workflow_boundary(client):
    """Voice Workflow Boundary Test:
    Send a mocked audio/webm blob to /assistant/voice_agent/chat,
    intercept transcription, vectorstore, and LLM calls, and verify response.
    """
    with client.session_transaction() as sess:
        sess['authenticated'] = True
        sess['current_user'] = 'test_trainee'
        sess['user_role'] = 'trainee'
        sess['user_info'] = {'employee_id': 'EMP123', 'domain': 'Engineering'}
        sess['voice_agent_state'] = {}

    query_text = "Tell me about the system architecture design"
    mock_llm_response = "The architecture uses a microservices design with background task processing."

    with patch('src.llm.transcribe_audio_whisper', return_value=query_text), \
         patch('src.embeddings.embed_query', return_value=[0.1]*384), \
         patch('app.search', return_value=[{"text": "System architecture overview", "metadata": {"source": "doc.pdf"}}]), \
         patch('app.generate_rag_answer', return_value=mock_llm_response), \
         patch('src.llm.generate_rag_answer', return_value=mock_llm_response), \
         patch('app.generate_chat_answer', return_value=mock_llm_response), \
         patch('src.llm.generate_chat_answer', return_value=mock_llm_response):
        
        audio_data = io.BytesIO(b"fake audio webm stream content")
        data = {
            'audio': (audio_data, 'sample.webm'),
            'mime_type': 'audio/webm'
        }
        
        response = client.post(
            '/assistant/voice_agent/chat',
            data=data,
            content_type='multipart/form-data'
        )
        
        assert response.status_code == 200
        res_json = response.get_json()
        assert res_json is not None
        assert "response_text" in res_json
        assert "microservices design" in res_json["response_text"]

def test_mock_interview_session(client):
    """Mock Interview Session Test:
    Send a POST request to /assistant/mock_interview/start,
    mock LLM response to return an array of 5 questions,
    and assert session['mock_questions'] contains exactly 5 items with index set to 0.
    """
    with client.session_transaction() as sess:
        sess['authenticated'] = True
        sess['current_user'] = 'test_trainee'
        sess['user_role'] = 'trainee'
        sess['user_info'] = {'employee_id': 'EMP123', 'domain': 'Engineering'}

    mock_5_questions = [
        "How do you handle database concurrency in high throughput services?",
        "Explain the difference between optimistic and pessimistic locking.",
        "What strategies do you use for cache invalidation?",
        "How do you approach zero-downtime database migrations?",
        "Describe your experience with circuit breaker design patterns."
    ]

    with patch('src.sprints.get_sprint', return_value={"current_day": 6, "current_week": 1}), \
         patch('src.sprints.get_qa_errors', return_value=[{"incorrect_topic": "Concurrency"}]), \
         patch('app.list_local_models', return_value=[]), \
         patch('app.generate_chat_answer', return_value=json.dumps(mock_5_questions)), \
         patch('src.llm.generate_chat_answer', return_value=json.dumps(mock_5_questions)):

        response = client.post('/assistant/mock_interview/start')
        
        assert response.status_code == 200
        res_json = response.get_json()
        assert res_json["status"] == "success"
        assert len(res_json["questions"]) == 5

        with client.session_transaction() as sess:
            assert 'mock_questions' in sess
            assert len(sess['mock_questions']) == 5
            assert sess['mock_index'] == 0
            assert sess['mock_questions'] == mock_5_questions
