# QA Automation Diagnostic Matrix

This report details the findings from analyzing the Flask routing controller (`app.py`) and database managers for security, concurrency, and stability issues.

## 1. Missing Authentication on Sensitive Routes

The following routes are missing the `@login_required` decorator or equivalent session validation, potentially exposing sensitive operations and PII to unauthenticated users.

| Endpoint | Methods | Risk Level | Description |
| :--- | :--- | :--- | :--- |
| `/api/get_face_descriptor` | GET | **Critical** | Exposes raw biometric face descriptors (arrays of floats) for any user, enabling biometric spoofing or theft. |
| `/api/enroll_face` | POST | **Critical** | Allows unauthenticated requests to overwrite a user's face descriptor, potentially allowing an attacker to bypass proctored exams. |
| `/api/log_proctoring_event` | POST | **High** | Allows forging proctoring logs (e.g., clearing suspicious flags or injecting false cheating logs) without authentication. |
| `/api/check_user` | GET | **Medium** | Could be used for user enumeration to discover valid `employee_id`s in the system. |

*(Note: `/login` and `/logout` are intentionally unauthenticated).*

## 2. Race Conditions & Global State Vulnerabilities

| Component / State Variable | Issue | Impact |
| :--- | :--- | :--- |
| `_in_memory_tab_sessions` (`app.py`) | **Multi-Process Inconsistency:** Storing user session data in a global dictionary works only if the application runs as a single thread/process. If run under multiple Gunicorn/Uvicorn workers, requests will hit different processes and sessions will randomly appear missing or outdated. | Users will randomly get logged out or lose state when load balancers route requests to different worker processes. |
| `_session_lock` (`app.py`) | **Thread Contention:** A single global `threading.Lock()` wraps the session dictionary for *every* request. | High concurrency will cause thread starvation. Requests will queue up waiting for the lock, drastically reducing throughput. |
| DB connection caching (`src/vectorstore.py` / `chroma`) | **Non-thread-safe clients:** Relying on globally initialized database clients or embedding models (`_model_instance` in `embeddings.py`) can cause runtime crashes if the underlying libraries aren't thread-safe. | Inference crashes or segmentation faults under load. |

## 3. Database Connection Leaks

Most database managers in the `src/` directory (and several routes in `app.py`) utilize a pattern of assigning `conn = get_db_connection()` and calling `conn.close()` sequentially.

**Vulnerability:** 
Because there are no `try...finally` blocks or `with closing(conn):` context managers, if *any* `conn.execute()` command raises a database exception (e.g., `sqlite3.IntegrityError`), the execution flow will abort, bypassing the `.close()` statement. This results in **file descriptor leaks** and eventually `database is locked` or `too many open files` errors.

**Affected Files & Functions (Sample):**
- `src/users.py`: `init_db`, `add_user`, `verify_user`, `update_user_activity`, `set_user_face_descriptor`, `check_user_exists`
- `src/exams.py`: `init_exams_db`, `add_exam_and_get_id`, `assign_exam`, `submit_exam_answers`, `get_all_assignments`
- `src/chats.py`: `init_chats_db`, `create_chat_session`, `add_chat_message`
- `app.py`: `_init_sessions_table`, `cleanup_orphaned_collections`, `admin_logs`

## 4. Stress-Test `curl` Commands (Malformed JSON)

Use the following raw `curl` commands to stress-test the POST endpoints against malformed payloads, missing keys, and empty bodies. These test how gracefully the endpoints handle unexpected input.

```bash
# 1. /api/enroll_face (Empty Body)
curl -X POST http://localhost:5000/api/enroll_face \
     -H "Content-Type: application/json" \
     -d ""

# 2. /api/log_proctoring_event (Malformed JSON)
curl -X POST http://localhost:5000/api/log_proctoring_event \
     -H "Content-Type: application/json" \
     -d '{"emp_id": "101", "assignment_id": 5, "event_type": "face_missing", "details": '

# 3. /user_management/create (Missing Required Keys)
curl -X POST http://localhost:5000/user_management/create \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "employee_name=John"

# 4. /exams/create/generate_ai (Missing Content-Type / Invalid JSON)
curl -X POST http://localhost:5000/exams/create/generate_ai \
     -d "{'topic': 'Machine Learning'}"

# 5. /assistant/chat_stream (Incorrect Types)
curl -X POST http://localhost:5000/assistant/chat_stream \
     -H "Content-Type: application/json" \
     -d '{"message": 12345, "session_id": null}'

# 6. /ingest (Empty Multipart Form)
curl -X POST http://localhost:5000/ingest \
     -H "Content-Type: multipart/form-data"

# 7. /assistant/mock_interview/start (Missing JSON body)
curl -X POST http://localhost:5000/assistant/mock_interview/start \
     -H "Content-Type: application/json" \
     -d '{}'

# 8. /exams/submit (Array instead of Dict)
curl -X POST http://localhost:5000/exams/submit \
     -H "Content-Type: application/json" \
     -d '["answer1", "answer2"]'
```
