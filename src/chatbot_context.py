import sqlite3
import datetime
from typing import Tuple, Optional
from src.users import _DB_PATH, get_all_users
from src.exams import get_all_announcements, get_all_exams, get_assignments_for_trainee, get_all_assignments
from src.sprints import get_sprint, get_all_study_plans

def detect_query_intent(query: str, role: str) -> Tuple[str, Optional[str]]:
    """
    Detects the intent of the user's query using keyword matching.
    Returns:
        (intent_name, privilege_error_message)
    """
    q = query.lower()
    
    # 1. Logs / Activity Query
    if any(k in q for k in ["log", "activity", "activities", "usage", "active"]):
        return "LOGS_QUERY", None
        
    # 2. Announcements Query
    if any(k in q for k in ["announcement", "notice", "news", "update"]):
        return "ANNOUNCEMENTS_QUERY", None
        
    # 3. Study Plans / Sprints Query
    if any(k in q for k in ["study plan", "sprint", "roadmap"]):
        return "STUDY_PLANS_QUERY", None
        
    # 4. Users / Trainees Query (Admin only)
    if any(k in q for k in ["user ", "users", "trainee", "student"]):
        if role != "admin" and not any(k in q for k in ["my", "i am", "me"]):
            return "USERS_QUERY", "You have no privilege to access global user data."
        return "USERS_QUERY", None
        
    # 5. Documents / Ingested Files Query
    if any(k in q for k in ["document", "pdf", "file", "ingest"]):
        return "DOCUMENTS_QUERY", None
        
    # 6. Exams / Assessments Query
    # Exclude "create exam" (handled by wizard)
    if any(k in q for k in ["exam", "test", "quiz", "assessment", "score"]):
        if role != "admin" and ("all exams" in q or "every exam" in q):
            return "EXAMS_QUERY", "You have no privilege to access global exams data."
        return "EXAMS_QUERY", None
        
    return "GENERAL_RAG", None

def get_feature_context(intent: str, role: str, emp_id: str) -> str:
    """
    Fetches real-time database context based on the detected intent.
    Limits logs to the last 2 weeks (14 days).
    """
    lines = [f"=== {intent.replace('_', ' ')} DATA ==="]
    
    try:
        # 1. LOGS
        if intent == "LOGS_QUERY":
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            two_weeks_ago = (datetime.datetime.now() - datetime.timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            
            if role == "admin":
                c.execute("SELECT employee_id, method, path, timestamp FROM activity_logs WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 100", (two_weeks_ago,))
                rows = c.fetchall()
                lines.append(f"Recent Global Platform Activity (Last 14 days, top 100):")
                lines.append("Emp ID | Method | Path | Timestamp")
                lines.append("---|---|---|---")
                for r in rows:
                    lines.append(f"{r['employee_id']} | {r['method']} | {r['path']} | {r['timestamp']}")
            else:
                c.execute("SELECT method, path, timestamp FROM activity_logs WHERE employee_id = ? AND timestamp >= ? ORDER BY timestamp DESC LIMIT 50", (emp_id, two_weeks_ago))
                rows = c.fetchall()
                lines.append(f"Your Recent Activity (Last 14 days):")
                lines.append("Method | Path | Timestamp")
                lines.append("---|---|---")
                for r in rows:
                    lines.append(f"{r['method']} | {r['path']} | {r['timestamp']}")
            conn.close()

        # 2. ANNOUNCEMENTS
        elif intent == "ANNOUNCEMENTS_QUERY":
            announcements = get_all_announcements()
            if not announcements:
                lines.append("No announcements currently available.")
            else:
                lines.append("Recent Announcements:")
                lines.append("ID | Title | Date")
                lines.append("---|---|---")
                for a in announcements[:10]:
                    lines.append(f"{a.get('id', '')} | {a.get('title', '')} | {a.get('date', '')}")
                    
        # 3. STUDY PLANS
        elif intent == "STUDY_PLANS_QUERY":
            if role == "admin":
                plans = get_all_study_plans()
                lines.append(f"Global Study Plans ({len(plans)} total):")
                lines.append("Domain | Week | Title")
                lines.append("---|---|---")
                for p in plans:
                    lines.append(f"{p.get('domain', '')} | Week {p.get('week_number', '')} | {p.get('title', '')}")
            else:
                sprint = get_sprint(emp_id)
                lines.append(f"Your Current Sprint/Study Plan:")
                lines.append(f"Domain: {sprint.get('domain', 'General')}")
                lines.append(f"Current Week: {sprint.get('current_week', 1)}")
                lines.append(f"Current Day: {sprint.get('current_day', 1)}")
                lines.append(f"Progress: {sprint.get('progress', 0.0)*100:.1f}%")

        # 4. EXAMS
        elif intent == "EXAMS_QUERY":
            if role == "admin":
                exams = get_all_exams()
                lines.append(f"Global Exams ({len(exams)} total):")
                lines.append("Exam ID | Title | Marks | Duration (m)")
                lines.append("---|---|---|---")
                for e in exams[:20]:
                    lines.append(f"{e.get('exam_id')} | {e.get('title')} | {e.get('total_marks')} | {e.get('duration_minutes')}")
                
                assignments = get_all_assignments()
                completed = [a for a in assignments if a.get('status') == 'completed']
                lines.append(f"\nTotal Exam Assignments: {len(assignments)}")
                lines.append(f"Total Completed: {len(completed)}")
            else:
                assignments = get_assignments_for_trainee(emp_id)
                lines.append(f"Your Assigned Exams ({len(assignments)} total):")
                lines.append("Exam Title | Status | Score / Total | Date Completed")
                lines.append("---|---|---|---")
                for a in assignments:
                    score = a.get('score', '-') if a.get('score') is not None else '-'
                    total = a.get('total_marks', '-')
                    lines.append(f"{a.get('title')} | {a.get('status')} | {score}/{total} | {a.get('completed_at', '-')}")

        # 5. USERS
        elif intent == "USERS_QUERY":
            if role == "admin":
                users = get_all_users()
                lines.append(f"Registered Users ({len(users)} total):")
                lines.append("Emp ID | Name | Role | Domain | Last Active")
                lines.append("---|---|---|---|---")
                for u in users:
                    lines.append(f"{u.get('employee_id')} | {u.get('full_name')} | {u.get('role')} | {u.get('domain')} | {u.get('last_active')}")
            else:
                lines.append("Your Profile:")
                lines.append(f"Employee ID: {emp_id}")

        # 6. DOCUMENTS
        elif intent == "DOCUMENTS_QUERY":
            def get_all_available_documents():
                import os
                from pathlib import Path
                docs_set = set()
                try:
                    from src.config import DOCUMENTS_DIR
                    doc_dir = Path(DOCUMENTS_DIR)
                    if doc_dir.exists():
                        for f in doc_dir.iterdir():
                            if f.is_file() and not f.name.startswith('.') and not f.name.startswith('Custom_'):
                                docs_set.add(f.name)
                except Exception as ex:
                    pass
                return sorted(list(docs_set))
                
            docs = get_all_available_documents()
            lines.append(f"Ingested Documents ({len(docs)} total):")
            for idx, d in enumerate(docs[:30]):
                lines.append(f"{idx+1}. {d}")
            if len(docs) > 30:
                lines.append("... and more.")

    except Exception as e:
        lines.append(f"Error retrieving data: {str(e)}")
        
    return "\n".join(lines)
