import sqlite3
import uuid
from pathlib import Path
from src.users import _DB_PATH

def init_sprint(user_id: str) -> dict:
    """Initialize sprint schedule for user if not exists."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM sprint_schedules WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if not row:
        sprint_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO sprint_schedules (sprint_id, user_id, current_week, current_day, sprint_progress)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sprint_id, user_id, 1, 1, 0.0)
        )
        conn.commit()
        cursor.execute("SELECT * FROM sprint_schedules WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
    res = dict(row)
    conn.close()
    return res

def get_sprint(user_id: str) -> dict:
    """Get active sprint details for user."""
    return init_sprint(user_id)

def update_sprint_day(user_id: str, day: int) -> bool:
    """Update sprint day (1-6) for user."""
    init_sprint(user_id)
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE sprint_schedules SET current_day = ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?",
            (day, user_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def update_sprint_week(user_id: str, week: int) -> bool:
    """Update active sprint week for user."""
    init_sprint(user_id)
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE sprint_schedules SET current_week = ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?",
            (week, user_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def update_sprint_progress(user_id: str, progress: float) -> bool:
    """Update sprint study progress percentage."""
    init_sprint(user_id)
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE sprint_schedules SET sprint_progress = ?, last_updated = CURRENT_TIMESTAMP WHERE user_id = ?",
            (min(max(progress, 0.0), 100.0), user_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def log_qa_error(user_id: str, week_number: int, topic: str, question_text: str) -> bool:
    """Log an incorrect topic/question from Day 5 QA Review."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        error_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO qa_errors (error_id, user_id, week_number, incorrect_topic, exam_question_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (error_id, user_id, week_number, topic, question_text)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def get_qa_errors(user_id: str, week_number: int) -> list[dict]:
    """Get qa errors logged for a user's week."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM qa_errors WHERE user_id = ? AND week_number = ? ORDER BY logged_at DESC",
        (user_id, week_number)
    )
    rows = cursor.fetchall()
    res = [dict(r) for r in rows]
    conn.close()
    return res

def clear_qa_errors(user_id: str, week_number: int) -> bool:
    """Clear qa errors for a user's week (e.g., on sprint reset)."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute("DELETE FROM qa_errors WHERE user_id = ? AND week_number = ?", (user_id, week_number))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def log_interview_evaluation(user_id: str, week_number: int, tech_score: float, conf_score: float, filler_count: int, wpm: float, feedback: str) -> bool:
    """Save Day 6 mock interview results."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        
        # Delete prior evaluation for this week if exists to allow clean re-takes
        cursor.execute("DELETE FROM interview_evaluations WHERE user_id = ? AND week_number = ?", (user_id, week_number))
        
        evaluation_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO interview_evaluations (evaluation_id, user_id, week_number, technical_score, confidence_score, filler_words_count, words_per_minute, feedback_report)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (evaluation_id, user_id, week_number, tech_score, conf_score, filler_count, wpm, feedback)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving interview evaluation: {e}")
        return False

def get_interview_evaluation(user_id: str, week_number: int) -> dict:
    """Get interview evaluation details."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM interview_evaluations WHERE user_id = ? AND week_number = ?",
        (user_id, week_number)
    )
    row = cursor.fetchone()
    res = dict(row) if row else None
    conn.close()
    return res

def clear_interview_evaluations(user_id: str, week_number: int) -> bool:
    """Clear interview evaluations for a user."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute("DELETE FROM interview_evaluations WHERE user_id = ? AND week_number = ?", (user_id, week_number))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def get_all_sprint_schedules() -> list[dict]:
    """Retrieve sprint details for trainees who have active sprint schedules."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 
            u.employee_id as user_id, 
            u.full_name, 
            u.email, 
            u.domain,
            s.current_week,
            s.current_day,
            s.sprint_progress,
            s.last_updated
        FROM users u
        JOIN sprint_schedules s ON u.employee_id = s.user_id
        WHERE u.role = 'trainee'
        """
    )
    rows = cursor.fetchall()
    res = [dict(r) for r in rows]
    conn.close()
    return res

def add_weekly_document(user_id: str, week_number: int, day_number: int, filename: str) -> bool:
    """Log an uploaded reference document for a user's specific sprint week and day."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        doc_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO weekly_documents (doc_id, user_id, week_number, day_number, filename)
            VALUES (?, ?, ?, ?, ?)
            """,
            (doc_id, user_id, week_number, day_number, filename)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error adding weekly document: {e}")
        return False

def get_weekly_documents(user_id: str, week_number: int) -> list[str]:
    """Retrieve filenames of uploaded documents for a trainee's specific week."""
    conn = sqlite3.connect(str(_DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT filename FROM weekly_documents WHERE user_id = ? AND week_number = ?",
        (user_id, week_number)
    )
    rows = cursor.fetchall()
    res = [r[0] for r in rows]
    conn.close()
    return res

def delete_weekly_document(user_id: str, week_number: int, filename: str) -> bool:
    """Delete a reference document record for a trainee's specific week."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM weekly_documents WHERE user_id = ? AND week_number = ? AND filename = ?",
            (user_id, week_number, filename)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def get_all_interview_evaluations() -> list[dict]:
    """Retrieve all mock interview evaluations (for admin review)."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM interview_evaluations")
    rows = cursor.fetchall()
    res = [dict(r) for r in rows]
    conn.close()
    return res

def save_study_plan(domain: str, week_number: int, title: str, tasks_json: str, day5_exam_id: str, day6_interview_prompt: str, reference_files_json: str = "[]") -> bool:
    """Create or update a weekly study plan for a domain."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        
        # Check if already exists
        cursor.execute(
            "SELECT plan_id FROM weekly_study_plans WHERE domain = ? AND week_number = ?",
            (domain.lower(), week_number)
        )
        row = cursor.fetchone()
        
        if row:
            cursor.execute(
                """
                UPDATE weekly_study_plans 
                SET title = ?, tasks_json = ?, day5_exam_id = ?, day6_interview_prompt = ?, reference_files_json = ?
                WHERE domain = ? AND week_number = ?
                """,
                (title, tasks_json, day5_exam_id, day6_interview_prompt, reference_files_json, domain.lower(), week_number)
            )
        else:
            plan_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO weekly_study_plans (plan_id, domain, week_number, title, tasks_json, day5_exam_id, day6_interview_prompt, reference_files_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (plan_id, domain.lower(), week_number, title, tasks_json, day5_exam_id, day6_interview_prompt, reference_files_json)
            )
            
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving study plan: {e}")
        return False

def get_study_plan(domain: str = "general", week_number: int = 1, plan_id: str = None) -> dict:
    """Retrieve study plan by plan_id if provided, or latest plan for domain/week, or latest overall plan."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    row = None
    if plan_id:
        cursor.execute("SELECT * FROM weekly_study_plans WHERE plan_id = ?", (plan_id,))
        row = cursor.fetchone()
        
    if not row:
        cursor.execute(
            "SELECT * FROM weekly_study_plans WHERE domain = ? AND week_number = ? ORDER BY rowid DESC",
            (domain.lower(), week_number)
        )
        row = cursor.fetchone()

    if not row:
        cursor.execute("SELECT * FROM weekly_study_plans ORDER BY rowid DESC")
        row = cursor.fetchone()

    conn.close()
    
    if row:
        return dict(row)
        
    # Provide a default study plan if not configured yet
    import json
    default_tasks = {
        "day1": [
            "Upload reference documentation / specifications",
            "Generate AI structured concept roadmap"
        ],
        "day2": [
            "Initiate interactive Q&A session with AI Coach",
            "Review architecture design guidelines"
        ],
        "day3": [
            "Run dynamic training sandbox quiz",
            "Review and analyze quiz explanation notes"
        ],
        "day4": [
            "Complete final code verification checkpoints",
            "Confirm all checklists are 100% completed"
        ]
    }
    
    return {
        "plan_id": "default",
        "domain": domain,
        "week_number": week_number,
        "title": f"{domain.capitalize()} Study Plan",
        "tasks_json": json.dumps(default_tasks),
        "day5_exam_id": "",
        "day6_interview_prompt": f"Defend the core architectural patterns and choices you made during Week {week_number}.",
        "reference_files_json": "[]"
    }

def get_all_study_plans() -> list[dict]:
    """Retrieve all custom weekly study plans."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM weekly_study_plans ORDER BY domain ASC, week_number ASC")
    rows = cursor.fetchall()
    res = [dict(r) for r in rows]
    conn.close()
    return res

def run_sprint_orchestrator(state_payload: dict, model_name: str = "llama-3.3-70b-versatile") -> dict:
    """Executes the AI Learning Orchestrator specification from gemini-code-1785693472882.md.
    
    Accepts state_payload dict containing Current_State (phase 1, 2, 3, or 4) and associated materials/answers.
    Returns structured JSON output dictionary.
    """
    import json
    import re
    from pathlib import Path
    from src.llm import generate_chat_answer, clean_json_response

    prompt_path = Path(__file__).resolve().parent.parent / "gemini-code-1785693472882.md"
    if not prompt_path.exists():
        return {"error": "gemini-code-1785693472882.md prompt specification file missing."}

    system_instruction = prompt_path.read_text(encoding="utf-8")
    user_prompt = json.dumps(state_payload, indent=2)

    try:
        raw_resp = generate_chat_answer(
            prompt=user_prompt,
            model_name=model_name,
            system_instruction=system_instruction
        )

        cleaned = clean_json_response(raw_resp)
        try:
            return json.loads(cleaned)
        except Exception:
            match = re.search(r'(\{[\s\S]*\})', cleaned)
            if match:
                return json.loads(match.group(1))
            return {"raw_response": raw_resp}
    except Exception as e:
        return {"error": f"Failed to execute orchestrator: {str(e)}"}

def delete_study_plan(plan_id: str) -> bool:
    """Delete a study plan by ID and wipe all associated trainee sprint schedules so it is deleted on user side too."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute("DELETE FROM weekly_study_plans WHERE plan_id = ?", (plan_id,))
        cursor.execute("DELETE FROM sprint_schedules WHERE assigned_plan_id = ?", (plan_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error deleting study plan: {e}")
        return False

def assign_study_plan_to_user(user_id: str, plan_id: str) -> bool:
    """Assign a specific study plan to a trainee user, creating schedule row if needed."""
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(sprint_schedules)")
        cols = [r[1] for r in cursor.fetchall()]
        if "assigned_plan_id" not in cols:
            cursor.execute("ALTER TABLE sprint_schedules ADD COLUMN assigned_plan_id TEXT")
        
        cursor.execute("SELECT user_id FROM sprint_schedules WHERE user_id = ?", (user_id,))
        if cursor.fetchone():
            cursor.execute(
                """
                UPDATE sprint_schedules 
                SET assigned_plan_id = ?, current_day = 1, sprint_progress = 0.0, last_updated = CURRENT_TIMESTAMP 
                WHERE user_id = ?
                """,
                (plan_id, user_id)
            )
        else:
            cursor.execute(
                "INSERT INTO sprint_schedules (sprint_id, user_id, current_week, current_day, sprint_progress, assigned_plan_id) VALUES (?, ?, 1, 1, 0.0, ?)",
                (str(uuid.uuid4()), user_id, plan_id)
            )
        
        # Clear prior QA errors and interview evaluations so trainee starts clean on new plan
        cursor.execute("DELETE FROM qa_errors WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM interview_evaluations WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error assigning study plan: {e}")
        return False
