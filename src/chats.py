"""SQLite-backed chat session and message persistence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.users import get_db_connection

_DB_PATH = Path(__file__).resolve().parent.parent / "users.db"


def init_chats_db() -> None:
    """Initialize persistent chat tables in the SQLite database."""
    conn = get_db_connection(_DB_PATH)
    cursor = conn.cursor()
    
    # 1. Create chat_sessions table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (employee_id) ON DELETE CASCADE
        )
        """
    )
    
    # Run migrations to add missing columns
    cursor.execute("PRAGMA table_info(chat_sessions)")
    session_columns = [row[1] for row in cursor.fetchall()]
    if "week_number" not in session_columns:
        cursor.execute("ALTER TABLE chat_sessions ADD COLUMN week_number INTEGER DEFAULT 1")

    # 2. Create chat_messages table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,  -- 'user' or 'assistant'
            content TEXT NOT NULL,
            sources TEXT,  -- JSON serialized list of sources cited
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES chat_sessions (session_id) ON DELETE CASCADE
        )
        """
    )
    
    # Run migration to add followups column if missing
    cursor.execute("PRAGMA table_info(chat_messages)")
    columns = [row[1] for row in cursor.fetchall()]
    if "followups" not in columns:
        cursor.execute("ALTER TABLE chat_messages ADD COLUMN followups TEXT")
        
    conn.commit()
    conn.close()


def create_chat_session(session_id: str, user_id: str, title: str, week_number: int = 1) -> bool:
    """Create a new chat session."""
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_sessions (session_id, user_id, title, week_number) VALUES (?, ?, ?, ?)",
            (session_id, user_id, title.strip(), week_number)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def add_chat_message(session_id: str, role: str, content: str, sources: list[dict[str, Any]] | None = None) -> bool:
    """Add a message to an active chat session."""
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        sources_str = json.dumps(sources) if sources else None
        cursor.execute(
            "INSERT INTO chat_messages (session_id, role, content, sources) VALUES (?, ?, ?, ?)",
            (session_id, role, content, sources_str)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_chat_sessions_for_user(user_id: str, week_number: int = None) -> list[dict[str, Any]]:
    """Retrieve all chat sessions created by a specific user, optionally filtered by week."""
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if week_number is not None:
            cursor.execute(
                "SELECT session_id, user_id, title, week_number, created_at FROM chat_sessions WHERE user_id = ? AND week_number = ? ORDER BY created_at DESC",
                (user_id, week_number)
            )
        else:
            cursor.execute(
                "SELECT session_id, user_id, title, week_number, created_at FROM chat_sessions WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_chat_messages(session_id: str) -> list[dict[str, Any]]:
    """Retrieve all messages in chronological order for a chat session."""
    init_chats_db()
    try:
        conn = get_db_connection(_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT message_id, session_id, role, content, sources, followups, created_at FROM chat_messages WHERE session_id = ? ORDER BY message_id ASC",
            (session_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d["sources"]) if d["sources"] else []
            except Exception:
                d["sources"] = []
            try:
                d["followups"] = json.loads(d["followups"]) if d["followups"] else []
            except Exception:
                d["followups"] = []
            result.append(d)
        return result
    except Exception:
        return []


def update_last_chat_message_followups(session_id: str, followups: list[str]) -> bool:
    """Update the followups column of the most recent message in the session."""
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        followups_str = json.dumps(followups) if followups else None
        cursor.execute(
            """
            UPDATE chat_messages 
            SET followups = ? 
            WHERE message_id = (
                SELECT MAX(message_id) 
                FROM chat_messages 
                WHERE session_id = ? AND role = 'assistant'
            )
            """,
            (followups_str, session_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating message followups: {e}")
        return False


def delete_chat_session(session_id: str) -> bool:
    """Delete a chat session and all cascading messages."""
    init_chats_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def rename_chat_session(session_id: str, new_title: str) -> bool:
    """Rename an existing chat session."""
    init_chats_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE chat_sessions SET title = ? WHERE session_id = ?",
            (new_title.strip(), session_id)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False



def get_global_chat_stats() -> dict[str, Any]:
    """Compile global statistics across all chat conversations (for Admin view)."""
    init_chats_db()
    try:
        conn = get_db_connection(_DB_PATH)
        cursor = conn.cursor()
        
        # 1. Total chat sessions
        cursor.execute("SELECT COUNT(*) FROM chat_sessions")
        total_sessions = cursor.fetchone()[0]
        
        # 2. Total messages
        cursor.execute("SELECT COUNT(*) FROM chat_messages")
        total_messages = cursor.fetchone()[0]
        
        # 3. Messages per day series (last 30 days)
        cursor.execute(
            """
            SELECT date(created_at) as msg_date, COUNT(*) as count 
            FROM chat_messages 
            GROUP BY msg_date 
            ORDER BY msg_date ASC
            """
        )
        msg_per_day = [{"date": r[0], "count": r[1]} for r in cursor.fetchall()]
        
        conn.close()
        return {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "messages_per_day": msg_per_day
        }
    except Exception:
        return {
            "total_sessions": 0,
            "total_messages": 0,
            "messages_per_day": []
        }
