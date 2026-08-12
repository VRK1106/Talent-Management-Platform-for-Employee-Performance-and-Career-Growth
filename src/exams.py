"""SQLite-backed exams and announcements repository."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.users import get_db_connection

_DB_PATH = Path(__file__).resolve().parent.parent / "users.db"


def init_exams_db() -> None:
    """Initialize SQLite tables for exams, assignments, and announcements."""
    conn = get_db_connection(_DB_PATH)
    cursor = conn.cursor()
    
    # 1. Create exams table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS exams (
            exam_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            total_marks INTEGER NOT NULL,
            questions TEXT NOT NULL,  -- JSON serialized questions list
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    
    # 2. Create assignments table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            trainee_id TEXT NOT NULL,
            due_date TEXT,
            status TEXT DEFAULT 'assigned',  -- 'assigned', 'completed'
            score REAL,
            answers TEXT,  -- JSON serialized trainee answers
            ai_feedback TEXT,  -- AI grading response feedback
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (exam_id) REFERENCES exams (exam_id) ON DELETE CASCADE,
            FOREIGN KEY (trainee_id) REFERENCES users (employee_id) ON DELETE CASCADE
        )
        """
    )
    
    # 3. Create announcements table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS announcements (
            announcement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Migrations for new settings columns
    cursor.execute("PRAGMA table_info(exams)")
    exams_cols = [row[1] for row in cursor.fetchall()]
    if "settings" not in exams_cols:
        cursor.execute("ALTER TABLE exams ADD COLUMN settings TEXT")

    cursor.execute("PRAGMA table_info(assignments)")
    assignments_cols = [row[1] for row in cursor.fetchall()]
    if "settings" not in assignments_cols:
        cursor.execute("ALTER TABLE assignments ADD COLUMN settings TEXT")

    # 4. Create exam_templates table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_templates (
            template_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            config TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    
    # 5. Create email_logs table for tracking delivery status
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS email_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    
    # 6. Create system_settings table for feature toggles
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    # Insert default settings
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('email_notifications_enabled', 'true')")
    
    # 7. Create proctor_logs table for webcam proctoring violations
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS proctor_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL,
            trigger_reason TEXT NOT NULL,
            groq_label TEXT NOT NULL,
            snapshot_data TEXT NOT NULL,
            score REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (assignment_id) REFERENCES assignments (assignment_id) ON DELETE CASCADE
        )
        """
    )
    
    conn.commit()
    conn.close()


def sanitize_exam_questions(questions: list, exam_title: str = "") -> list:
    """Ensure all exam questions and option choices are formatted cleanly while preserving original domain technical questions and multi-section types."""
    if not isinstance(questions, list):
        return []

    seen_questions = set()
    cleaned = []

    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            continue

        q_text = str(q.get("question", "")).strip()
        q_type = str(q.get("type", "mcq")).lower()
        q_marks = q.get("marks", 10)
        q_section = str(q.get("section", "General Knowledge")).strip()

        if not q_text:
            continue

        norm_q = q_text.lower()
        if norm_q in seen_questions:
            q_text = f"{q_text} ({idx + 1})"
            norm_q = q_text.lower()

        seen_questions.add(norm_q)

        item = {
            "question": q_text,
            "type": q_type,
            "marks": q_marks,
            "section": q_section,
            "correct_answer": str(q.get("correct_answer", "")).strip()
        }

        if q_type == "mcq":
            opts = q.get("options", [])
            cleaned_opts = []
            if isinstance(opts, list):
                for opt in opts:
                    opt_str = str(opt).strip()
                    if opt_str and opt_str not in cleaned_opts:
                        cleaned_opts.append(opt_str)
            item["options"] = cleaned_opts
            if not item["correct_answer"] and cleaned_opts:
                item["correct_answer"] = cleaned_opts[0]
        elif q_type == "match":
            pairs = q.get("match_pairs", [])
            if isinstance(pairs, list):
                item["match_pairs"] = pairs

        cleaned.append(item)

    return cleaned


# --- Exams operations ---

def get_all_exams(include_gateway: bool = False) -> list[dict[str, Any]]:
    """Fetch all created exams. By default excludes private Day 5 Gateway exams created for weekly sprint plans."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT exam_id, title, description, total_marks, questions, created_at, settings FROM exams ORDER BY exam_id DESC")
        rows = cursor.fetchall()
        conn.close()
        
        result = []
        for r in rows:
            d = dict(r)
            try:
                raw_q = json.loads(d["questions"])
                d["questions"] = sanitize_exam_questions(raw_q, d.get("title", ""))
            except Exception:
                d["questions"] = []
            try:
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
            except Exception:
                d["settings"] = {}

            is_gateway = d["settings"].get("is_sprint_gateway") or d.get("title", "").startswith("Day 5 Gateway Exam")
            if not include_gateway and is_gateway:
                continue

            result.append(d)
        return result
    except Exception:
        return []


def get_exam_by_id(exam_id: int) -> dict[str, Any] | None:
    """Fetch a single exam by ID."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT exam_id, title, description, total_marks, questions, created_at, settings FROM exams WHERE exam_id = ?", (exam_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            d = dict(row)
            try:
                raw_q = json.loads(d["questions"])
                d["questions"] = sanitize_exam_questions(raw_q, d.get("title", ""))
            except Exception:
                d["questions"] = []
            try:
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
            except Exception:
                d["settings"] = {}
            return d
        return None
    except Exception:
        return None
    return None


def add_exam(title: str, description: str, total_marks: int, questions: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> bool:
    """Add a new exam containing a list of questions."""
    res = add_exam_and_get_id(title, description, total_marks, questions, settings)
    return res is not None

def add_exam_and_get_id(title: str, description: str, total_marks: int, questions: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> int | None:
    """Add a new exam containing a list of questions and return its created exam_id."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO exams (title, description, total_marks, questions, settings)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title.strip(), description.strip(), total_marks, json.dumps(questions), json.dumps(settings or {})),
        )
        exam_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return exam_id
    except Exception as e:
        print(f"Error adding exam: {e}")
        return None

def assign_exam_to_all_students(exam_id: int) -> int:
    """Assign an exam to all trainee users in SQLite."""
    init_exams_db()
    count = 0
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT employee_id FROM users WHERE role = 'trainee'")
        trainees = [r['employee_id'] for r in cursor.fetchall()]
        conn.close()
        for t_id in trainees:
            if assign_exam(exam_id, t_id, None):
                count += 1
    except Exception as e:
        print(f"Error assigning exam to all: {e}")
    return count

def delete_exam(exam_id: int) -> bool:
    """Delete an exam and cascade delete its assignments."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        # Enable FK cascade support explicitly in SQLite
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("DELETE FROM exams WHERE exam_id = ?", (exam_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# --- Assignments operations ---

def assign_exam(exam_id: int, trainee_id: str, due_date: str | None) -> bool:
    """Assign an exam to a trainee."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        
        # Check if already assigned
        cursor.execute(
            "SELECT count(*) FROM assignments WHERE exam_id = ? AND trainee_id = ? AND status = 'assigned'",
            (exam_id, trainee_id)
        )
        if cursor.fetchone()[0] > 0:
            conn.close()
            return False  # Already assigned and active
            
        # Get exam settings
        cursor.execute("SELECT settings FROM exams WHERE exam_id = ?", (exam_id,))
        exam_row = cursor.fetchone()
        exam_settings = {}
        if exam_row and exam_row[0]:
            try:
                exam_settings = json.loads(exam_row[0])
            except Exception:
                pass
                
        results_release = exam_settings.get("results_release", "auto")
        assignment_settings = {
            "results_release": results_release,
            "results_published": results_release == "auto"
        }
            
        cursor.execute(
            """
            INSERT INTO assignments (exam_id, trainee_id, due_date, settings)
            VALUES (?, ?, ?, ?)
            """,
            (exam_id, trainee_id, due_date, json.dumps(assignment_settings)),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_assignments_for_exam(exam_id: int) -> list[dict[str, Any]]:
    """Get all trainee assignments for a specific exam."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.assignment_id, a.exam_id, a.trainee_id, a.due_date, a.status, a.score, 
                   a.answers, a.ai_feedback, a.assigned_at, a.completed_at, a.settings, u.full_name, u.email
            FROM assignments a
            JOIN users u ON a.trainee_id = u.employee_id
            WHERE a.exam_id = ?
            ORDER BY a.assignment_id DESC
            """,
            (exam_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["answers"] = json.loads(d["answers"]) if d["answers"] else {}
            except Exception:
                d["answers"] = {}
            try:
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
            except Exception:
                d["settings"] = {}
            result.append(d)
        return result
    except Exception:
        return []


def get_assignments_for_trainee(trainee_id: str) -> list[dict[str, Any]]:
    """Get all exam assignments for a specific trainee."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.assignment_id, a.exam_id, a.trainee_id, a.due_date, a.status, a.score, 
                   a.answers, a.ai_feedback, a.assigned_at, a.completed_at, a.settings, e.title, e.description, e.total_marks
            FROM assignments a
            JOIN exams e ON a.exam_id = e.exam_id
            WHERE a.trainee_id = ?
            ORDER BY a.assignment_id DESC
            """,
            (trainee_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["answers"] = json.loads(d["answers"]) if d["answers"] else {}
            except Exception:
                d["answers"] = {}
            try:
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
            except Exception:
                d["settings"] = {}
            result.append(d)
        return result
    except Exception:
        return []


def get_assignment_by_id(assignment_id: int) -> dict[str, Any] | None:
    """Fetch a single assignment details."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.assignment_id, a.exam_id, a.trainee_id, a.due_date, a.status, a.score, 
                   a.answers, a.ai_feedback, a.assigned_at, a.completed_at, a.settings, e.title, e.description, e.questions, e.total_marks, e.settings AS exam_settings, u.full_name
            FROM assignments a
            JOIN exams e ON a.exam_id = e.exam_id
            LEFT JOIN users u ON a.trainee_id = u.employee_id
            WHERE a.assignment_id = ?
            """,
            (assignment_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            d = dict(row)
            d["description"] = d.get("description") or ""
            try:
                d["questions"] = json.loads(d["questions"])
            except Exception:
                d["questions"] = []
            try:
                d["answers"] = json.loads(d["answers"]) if d["answers"] else {}
            except Exception:
                d["answers"] = {}
            try:
                d["settings"] = json.loads(d["settings"]) if d.get("settings") else {}
            except Exception:
                d["settings"] = {}
            try:
                d["exam_settings"] = json.loads(d["exam_settings"]) if d.get("exam_settings") else {}
            except Exception:
                d["exam_settings"] = {}
            return d
    except Exception:
        pass
    return None


def submit_exam_answers(assignment_id: int, answers: dict[str, Any], score: float, ai_feedback: str) -> bool:
    """Submit trainee exam responses and save grade results."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE assignments
            SET status = 'completed',
                answers = ?,
                score = ?,
                ai_feedback = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE assignment_id = ?
            """,
            (json.dumps(answers), score, ai_feedback, assignment_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_assignment(assignment_id: int) -> bool:
    """Delete a trainee assignment by ID."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM assignments WHERE assignment_id = ?", (assignment_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# --- Announcements operations ---

def get_all_announcements() -> list[dict[str, Any]]:
    """Fetch all announcements."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT announcement_id, title, content, created_at FROM announcements ORDER BY announcement_id DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def add_announcement(title: str, content: str, send_email: bool = True) -> bool:
    """Add a new system announcement."""
    init_exams_db()
    try:
        import re
        import datetime
        
        title = (title or "").strip()
        content = (content or "").strip()
        
        now = datetime.datetime.now()
        current_dt_str = now.strftime("%B %d, %Y, %I:%M %p")
        
        pat = r'\[\s*(?:insert|enter)?\s*(?:dates?|time|date\s*(?:/|&|and)\s*time)\s*(?:here)?\s*\]'
        title = re.sub(pat, current_dt_str, title, flags=re.IGNORECASE)
        content = re.sub(pat, current_dt_str, content, flags=re.IGNORECASE)

        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO announcements (title, content)
            VALUES (?, ?)
            """,
            (title, content),
        )
        conn.commit()
        conn.close()

        # Broadcast via email
        if send_email:
            try:
                from src.mail import broadcast_announcement
                broadcast_announcement(title, content)
            except Exception as e:
                print(f"Failed to trigger email broadcast: {e}")

        return True
    except Exception:
        return False


def delete_announcement(announcement_id: int) -> bool:
    """Delete an announcement by ID."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM announcements WHERE announcement_id = ?", (announcement_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def add_exam_template(name: str, config: dict) -> bool:
    """Add or replace an exam template configuration."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO exam_templates (name, config)
            VALUES (?, ?)
            """,
            (name.strip(), json.dumps(config)),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_exam_templates() -> list[dict[str, Any]]:
    """Fetch all saved exam templates."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT template_id, name, config, created_at FROM exam_templates ORDER BY template_id DESC")
        rows = cursor.fetchall()
        conn.close()
        
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["config"] = json.loads(d["config"])
            except Exception:
                d["config"] = {}
            result.append(d)
        return result
    except Exception:
        return []


def add_email_log(recipient: str, subject: str, status: str, error_message: str | None = None) -> int | None:
    """Insert a new email delivery log and return the row ID."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO email_logs (recipient, subject, status, error_message)
            VALUES (?, ?, ?, ?)
            """,
            (recipient, subject, status, error_message),
        )
        log_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return log_id
    except Exception as e:
        print(f"Failed to write email log: {e}")
        return None


def update_email_log(log_id: int, status: str, error_message: str | None = None) -> bool:
    """Update the status of an existing email delivery log."""
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE email_logs
            SET status = ?, error_message = ?
            WHERE log_id = ?
            """,
            (status, error_message, log_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Failed to update email log: {e}")
        return False


def get_all_email_logs(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve the most recent email delivery logs."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT log_id, recipient, subject, status, error_message, datetime(sent_at, 'localtime') as sent_at FROM email_logs ORDER BY log_id DESC LIMIT ?",
            (limit,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_system_setting(key: str, default: str = "") -> str:
    """Retrieve a system setting value by key."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def set_system_setting(key: str, value: str) -> bool:
    """Set/update a system setting value."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO system_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Failed to set system setting: {e}")
        return False


def add_proctor_log(assignment_id: int, trigger_reason: str, groq_label: str, snapshot_data: str, score: float | None = None) -> int | None:
    """Insert a new proctoring violation log."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO proctor_logs (assignment_id, trigger_reason, groq_label, snapshot_data, score)
            VALUES (?, ?, ?, ?, ?)
            """,
            (assignment_id, trigger_reason, groq_label, snapshot_data, score)
        )
        log_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return log_id
    except Exception as e:
        print(f"Failed to add proctor log: {e}")
        return None


def get_proctor_logs_for_assignment(assignment_id: int) -> list[dict[str, Any]]:
    """Fetch all proctoring logs for a given assignment."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT log_id, assignment_id, trigger_reason, groq_label, snapshot_data, score, timestamp FROM proctor_logs WHERE assignment_id = ? ORDER BY timestamp ASC", (assignment_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def publish_assignment_results(assignment_id: int) -> bool:
    """Publish results for a completed manual-release assignment."""
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM assignments WHERE assignment_id = ?", (assignment_id,))
        row = cursor.fetchone()
        if row:
            try:
                settings = json.loads(row[0]) if row[0] else {}
            except Exception:
                settings = {}
            settings["results_published"] = True
            cursor.execute(
                "UPDATE assignments SET settings = ? WHERE assignment_id = ?",
                (json.dumps(settings), assignment_id)
            )
            conn.commit()
            conn.close()
            return True
        conn.close()
    except Exception as e:
        print(f"Error publishing results: {e}")
    return False


def get_all_assignments() -> list[dict[str, Any]]:
    """Get all trainee assignments across every exam, ordered newest first."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.assignment_id, a.exam_id, a.trainee_id, a.due_date, a.status, a.score,
                   a.assigned_at, a.completed_at, a.settings, a.ai_feedback,
                   u.full_name, u.email,
                   e.title, e.total_marks
            FROM assignments a
            JOIN users u ON a.trainee_id = u.employee_id
            JOIN exams e ON a.exam_id = e.exam_id
            ORDER BY a.assignment_id DESC
            """
        )
        rows = cursor.fetchall()
        assignments = []
        for r in rows:
            asg = dict(r)
            try:
                asg["settings"] = json.loads(r["settings"]) if r["settings"] else {}
            except Exception:
                asg["settings"] = {}
            assignments.append(asg)
        conn.close()
        return assignments
    except Exception as e:
        print(f"Error in get_all_assignments: {e}")
        return []


def clear_all_exams() -> bool:
    """Delete all exams, templates, assignments, and proctor logs."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        # Enable foreign key cascading
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("DELETE FROM exams")
        cursor.execute("DELETE FROM exam_templates")
        cursor.execute("DELETE FROM assignments")
        cursor.execute("DELETE FROM proctor_logs")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error clearing exams: {e}")
        return False


def clear_all_announcements() -> bool:
    """Delete all announcements and email logs."""
    init_exams_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM announcements")
        cursor.execute("DELETE FROM email_logs")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error clearing announcements: {e}")
        return False
