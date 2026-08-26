# Phase 4: Dead Code & Integration Test Plan

This report identifies orphaned endpoints, dead code, and partially implemented features, followed by an end-to-end integration test plan for the voice and mock interview workflows.

## 1. Orphaned API Endpoints & Partially Implemented Features

The following Flask routes are defined in `app.py` but are **never referenced** in any of the UI templates (`*.html`) or client-side JavaScript (`*.js`). These represent either deprecated features, dead code, or backend logic for which the frontend UI was never built or wired up:

*   **Sprint & Study Plan Orchestration (Missing UI / Unwired)**
    *   `/assistant/sprint/status`
    *   `/assistant/sprint/task_complete`
    *   `/assistant/sprint/override`
    *   `/admin/sprint/ai_generate_plan`
    *   `/admin/sprint/save_plan`
    *   `/admin/sprint/upload_reference`
    *   `/sprint/reset`
*   **Mock Interviews (Unwired UI)**
    *   `/assistant/mock_interview/start`
    *   `/assistant/mock_interview/submit`
*   **Wizards & Settings (Abandoned Routes)**
    *   `/assistant/wizard/templates`
    *   `/announcements/settings/toggle`

*Impact:* These endpoints add maintenance overhead and increase the attack surface. If the Sprint and Mock Interview features are on the roadmap, their corresponding frontend HTML buttons and `fetch()` calls need to be implemented. Otherwise, this dead code should be pruned.

---

## 2. End-to-End Integration Test Plan: Voice & Mock Interviews

Because these workflows rely on external LLM services (Groq Whisper, Groq LLaMA) and stateful server-side sessions, the integration tests must mock the external boundaries while executing the full internal pipeline.

### Scenario A: Interactive Voice Workflow (Speech-to-Text & Response)
**Goal:** Verify that a recorded audio blob successfully transcribes via Whisper, processes through the LLM, and updates the chat history.

1.  **Test: Audio Transcription (Boundary Test)**
    *   *Action:* Send a mocked `audio/webm` Blob to the `/assistant/voice` (or equivalent Whisper transcription endpoint).
    *   *Mock:* Intercept the outbound `client.audio.transcriptions.create` call to Groq and return a hardcoded string: `"Hello AI coach."`
    *   *Assertion:* Verify the endpoint returns HTTP 200 with the exact parsed string.
2.  **Test: Voice Agent Chat Pipeline (Stateful Test)**
    *   *Action:* POST the transcribed text to `/assistant/voice_agent/chat` using a test client with an active session cookie.
    *   *Mock:* Intercept the LLM chat completion call to return a mock response.
    *   *Assertion:* Verify the Flask `session['voice_agent_state']` and `session['voice_agent_history']` are correctly updated with the new user prompt and assistant reply.

### Scenario B: Mock Interview Generation & Evaluation
**Goal:** Verify that the mock interview system correctly generates questions, stores them in the session, and accurately parses the user's submitted answers for grading.

1.  **Test: Initialize Mock Interview (Session State Check)**
    *   *Action:* POST to `/assistant/mock_interview/start` with a JSON payload `{"topic": "Python Generators"}`.
    *   *Mock:* Intercept the LLM call and return a JSON array of 5 sample questions.
    *   *Assertion:* Verify HTTP 200. Check the test client's session to ensure `session['mock_questions']` contains exactly 5 items and `session['mock_index']` is `0`.
2.  **Test: Submit Answers & Grade Calculation**
    *   *Action:* POST to `/assistant/mock_interview/submit` with a JSON payload containing answers to the generated questions.
    *   *Mock:* Intercept the LLM evaluation call and return a simulated grading rubric (e.g., `{"score": 85, "feedback": "Good job."}`).
    *   *Assertion:* Verify the endpoint returns the correctly parsed JSON rubric. Ensure that `session['mock_answers']` was updated prior to submission and that the session variables are cleared/reset after successful grading.
