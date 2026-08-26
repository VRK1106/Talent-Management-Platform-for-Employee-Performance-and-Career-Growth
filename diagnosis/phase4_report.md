# Phase 4 Diagnosis Report

## Dead Code Analysis
The following functions are defined in the backend source code but appear to be unused (orphaned) based on a codebase-wide symbol search:
- `load_css`
- `metric_tile`
- `get_user_face_descriptor`
- `delete_assignment`
- `generate_text`
- `pill_row`
- `run_sprint_orchestrator`
- `get_study_plans_by_domain`
- `render_sidebar`
- `result_card`
- `info_banner`
- `section_header`
- `get_related_concepts`

**Note:** Several of these functions (`load_css`, `metric_tile`, `pill_row`, `render_sidebar`, `result_card`, `info_banner`, `section_header`) appear to be related to a Streamlit or UI component library that is either orphaned or was replaced by the current HTML templates.

## Integration Test Plan for Interactive Voice Workflows
To ensure the interactive voice workflows and mock interview generation are robust, the following end-to-end integration test plan should be implemented:

### 1. Voice Input and Transcription Pipeline
*   **Test Case:** Audio capture and encoding.
    *   *Action:* Simulate microphone input using a mock `MediaRecorder` in the browser, generate a dummy Blob, and send it to the backend transcription endpoint.
    *   *Expected Result:* Backend correctly decodes the Blob and interfaces with the STT (Speech-to-Text) provider (e.g., Groq Whisper).
*   **Test Case:** STT Fallbacks.
    *   *Action:* Trigger a 429 Too Many Requests or 500 error from the STT provider.
    *   *Expected Result:* Backend gracefully catches the error and the frontend UI indicates a temporary transcription failure without freezing the recording state.

### 2. Mock Interview Generation
*   **Test Case:** Prompt context injection.
    *   *Action:* Submit a request for a mock interview specifying a domain (e.g., "Python Developer") and week number.
    *   *Expected Result:* The LLM prompt correctly incorporates the domain, retrieving the appropriate curriculum topics from the database.
*   **Test Case:** Malicious or hallucinated JSON formats.
    *   *Action:* Mock the LLM provider to return malformed JSON or markdown instead of the requested JSON structure.
    *   *Expected Result:* The backend validation layer catches the parsing error, retries the prompt, or safely falls back to a generic default response.

### 3. Voice Output (TTS) and UI Synchronization
*   **Test Case:** TTS Streaming.
    *   *Action:* Trigger the Text-to-Speech synthesis for the generated interview question.
    *   *Expected Result:* The audio stream plays seamlessly in the browser.
*   **Test Case:** Interruptions and AbortController.
    *   *Action:* User clicks "Stop" or navigates away while the TTS is playing or fetching.
    *   *Expected Result:* The frontend AbortController cancels the network fetch, stops the audio context, and resets the UI button state to prevent state locks.
