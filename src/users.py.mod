"""User management database operations using SQLite."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).resolve().parent.parent / "users.db"


def get_db_connection(db_path: Path | str = _DB_PATH, timeout: float = 15.0) -> sqlite3.Connection:
    """Return a WAL-enabled, lock-resistant SQLite database connection."""
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=10000;")
    except Exception:
        pass
    return conn


def _hash_password(password: str) -> str:
    """Return a SHA-256 hex digest of the password."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db() -> None:
    """Initialize the SQLite database and create users table if it does not exist."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
    
        # Create users table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                employee_id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                domain TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'trainee',
                password_plain TEXT
            )
            """
        )
    
        # Check if table needs password_plain column (migration for existing db)
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]
        if "password_plain" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN password_plain TEXT")
        if "last_active" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_active TIMESTAMP")
        if "face_descriptor" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN face_descriptor TEXT")
        if "accommodation_proctoring" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN accommodation_proctoring INTEGER DEFAULT 0")
        
        # Create activity_logs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    
        # Delete demo user if it exists (clean migration)
        cursor.execute("DELETE FROM users WHERE employee_id = 'demo'")
    
        # Check if table is empty, if so, seed default accounts
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            # Seed admin user only
            cursor.execute(
                """
                INSERT INTO users (employee_id, email, full_name, domain, password_hash, role, password_plain)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "admin",
                    "admin@company.com",
                    "Administrator",
                    "general",
                    _hash_password("admin123"),
                    "admin",
                    "admin123",
                ),
            )
    
        # Create sprint_schedules table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sprint_schedules (
                sprint_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                current_week INTEGER DEFAULT 1,
                current_day INTEGER DEFAULT 1,
                sprint_progress REAL DEFAULT 0.0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(employee_id)
            )
            """
        )

        # Create qa_errors table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS qa_errors (
                error_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                week_number INTEGER NOT NULL,
                incorrect_topic TEXT NOT NULL,
                exam_question_text TEXT NOT NULL,
                logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(employee_id)
            )
            """
        )

        # Create interview_evaluations table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS interview_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                week_number INTEGER NOT NULL,
                technical_score REAL,
                confidence_score REAL,
                filler_words_count INTEGER,
                words_per_minute REAL,
                feedback_report TEXT,
                graded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(employee_id)
            )
            """
        )
        # Create weekly_documents table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_documents (
                doc_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                week_number INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                filename TEXT NOT NULL,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(employee_id)
            )
            """
        )
        # Create weekly_study_plans table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_study_plans (
                plan_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                week_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                tasks_json TEXT NOT NULL,
                day5_exam_id TEXT,
                day6_interview_prompt TEXT,
                reference_files_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    
        # Ensure reference_files_json column exists in case DB was already created
        try:
            cursor.execute("ALTER TABLE weekly_study_plans ADD COLUMN reference_files_json TEXT")
        except sqlite3.OperationalError:
            pass
        
        conn.commit()


def add_user(
    employee_id: str,
    email: str,
    full_name: str,
    domain: str,
    password_plain: str,
    role: str = "trainee",
) -> tuple[bool, str]:
    """Add a new user to the database. Returns (success, message)."""
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            INSERT INTO users (employee_id, email, full_name, domain, password_hash, role, password_plain)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employee_id.strip(),
                email.strip().lower(),
                full_name.strip(),
                domain.strip().lower(),
                _hash_password(password_plain),
                role,
                password_plain.strip(),
            ),
        )
        conn.commit()
        conn.close()
        return True, "User created successfully."
    except sqlite3.IntegrityError as e:
        err_msg = str(e)
        if "employee_id" in err_msg or "PRIMARY KEY" in err_msg:
            return False, f"Employee ID '{employee_id}' is already registered."
        if "email" in err_msg:
            return False, f"Email '{email}' is already registered."
        return False, f"Registration failed: {err_msg}"
    except Exception as e:
        return False, f"Database error: {e}"


def delete_user(employee_id: str) -> bool:
    """Delete a user from the database by Employee ID."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE employee_id = ?", (employee_id,))
            rows_affected = cursor.rowcount
            conn.commit()
            return rows_affected > 0
        except Exception:
            return False


def get_all_users() -> list[dict[str, Any]]:
    """Return all users in the database (excluding administrators)."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT employee_id, email, full_name, domain, role, password_plain, last_active, accommodation_proctoring FROM users WHERE role != 'admin'")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


def verify_user(username_or_email: str, password_plain: str) -> dict[str, Any] | None:
    """Verify user credentials. Returns user dict if valid, else None."""
    try:
        init_db()  # Ensure DB is ready
        conn = get_db_connection(_DB_PATH)
    try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
        
            # Match against employee_id or email
            cursor.execute(
                """
                SELECT employee_id, email, full_name, domain, role, password_hash 
                FROM users 
                WHERE LOWER(employee_id) = ? OR LOWER(email) = ?
                """,
                (username_or_email.lower(), username_or_email.lower()),
            )
            row = cursor.fetchone()
        
            if row and row["password_hash"] == _hash_password(password_plain):
                user_data = dict(row)
                del user_data["password_hash"]
                # Update last_active on login verification
                update_user_activity(user_data["employee_id"])
                return user_data
        except Exception:
            pass
        return None


def update_user_activity(employee_id: str) -> None:
    """Update the last_active timestamp for a user to CURRENT_TIMESTAMP."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET last_active = datetime('now', 'localtime') WHERE employee_id = ?",
                (employee_id,)
            )
            conn.commit()
        except Exception:
            pass


def get_active_users_count(hours: int = 1) -> int:
    """Count the number of users who have been active within the last N hours."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM users WHERE last_active >= datetime('now', 'localtime', ?)",
                (f"-{hours} hours",)
            )
            count = cursor.fetchone()[0]
            # Always return at least 1 if an active session exists
            return max(count, 1)
        except Exception:
            return 1


def set_user_face_descriptor(employee_id: str, descriptor_json: str) -> bool:
    """Set the 128-float face descriptor for a user."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET face_descriptor = ? WHERE employee_id = ?", (descriptor_json, employee_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"Failed to set face descriptor: {e}")
            return False


def get_user_face_descriptor(employee_id: str) -> str | None:
    """Get the 128-float face descriptor for a user."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute("SELECT face_descriptor FROM users WHERE employee_id = ?", (employee_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception:
            return None


def set_user_accommodation(employee_id: str, enabled: int) -> bool:
    """Set the proctoring accommodation flag (0 or 1) for a user."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET accommodation_proctoring = ? WHERE employee_id = ?", (enabled, employee_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"Failed to set user accommodation: {e}")
            return False


def clear_all_trainee_users() -> bool:
    """Delete all users in the database where role == 'trainee'."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
        
            # Get list of trainee employee IDs to delete their chat sessions/messages too
            cursor.execute("SELECT employee_id FROM users WHERE role = 'trainee'")
            trainee_ids = [row[0] for row in cursor.fetchall()]
        
            # Delete from users
            cursor.execute("DELETE FROM users WHERE role = 'trainee'")
        
            # Also clean up assignments associated with deleted trainees
            cursor.execute("DELETE FROM assignments WHERE trainee_id IN (SELECT employee_id FROM users WHERE role = 'trainee')")
        
            # Clear chat sessions & messages associated with trainees
            for t_id in trainee_ids:
                cursor.execute("SELECT session_id FROM chat_sessions WHERE user_id = ?", (t_id,))
                sess_ids = [r[0] for r in cursor.fetchall()]
                for s_id in sess_ids:
                    cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (s_id,))
                cursor.execute("DELETE FROM chat_sessions WHERE user_id = ?", (t_id,))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"Error clearing trainees: {e}")
            return False


def check_user_exists(employee_id: str) -> bool:
    """Check if a user with the given employee_id exists."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE LOWER(employee_id) = ?", (employee_id.strip().lower(),))
            exists = cursor.fetchone() is not None
            return exists
        except Exception:
            return False


def log_activity(employee_id: str, method: str, path: str) -> None:
    """Log an activity request for an authenticated user."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO activity_logs (employee_id, method, path) VALUES (?, ?, ?)",
                (employee_id, method, path)
            )
            conn.commit()
        except Exception as e:
            print(f"Failed to log activity: {e}")


def update_user(employee_id: str, full_name: str, email: str, domain: str, role: str, password_plain: str = None) -> tuple[bool, str]:
    """Update an existing user's details, including optional password change."""
    try:
        conn = get_db_connection(_DB_PATH)
    try:
            cursor = conn.cursor()
        
            if password_plain and password_plain.strip():
                cursor.execute(
                    """
                    UPDATE users 
                    SET full_name = ?, email = ?, domain = ?, role = ?, password_hash = ?, password_plain = ?
                    WHERE employee_id = ?
                    """,
                    (full_name.strip(), email.strip().lower(), domain.strip().lower(), role, _hash_password(password_plain), password_plain.strip(), employee_id)
                )
            else:
                cursor.execute(
                    """
                    UPDATE users 
                    SET full_name = ?, email = ?, domain = ?, role = ?
                    WHERE employee_id = ?
                    """,
                    (full_name.strip(), email.strip().lower(), domain.strip().lower(), role, employee_id)
                )
            
            rows_affected = cursor.rowcount
            conn.commit()
        
            if rows_affected > 0:
                return True, "User updated successfully."
            else:
                return False, "User not found."
        except sqlite3.IntegrityError as e:
            if "email" in str(e).lower():
                return False, f"Email '{email}' is already registered to another user."
            return False, f"Update failed: {e}"
        except Exception as e:
            return False, f"Database error: {e}"
    finally:
        conn.close()
