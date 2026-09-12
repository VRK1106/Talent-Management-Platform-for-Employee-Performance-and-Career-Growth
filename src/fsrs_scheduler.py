import sqlite3
import uuid
import json
from datetime import datetime, timezone
import fsrs
from pathlib import Path
from typing import Dict, List, Any

_DB_PATH = Path(__file__).resolve().parent.parent / "users.db"

def get_db_connection(db_path: Path = _DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def init_fsrs_db():
    """Initialize the FSRS SQLite tables."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. fsrs_items: Flashcards/Questions extracted from content
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fsrs_items (
                item_id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 2. fsrs_cards: The FSRS state for each user + item pair
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fsrs_cards (
                card_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                state INTEGER NOT NULL,
                due TIMESTAMP NOT NULL,
                stability REAL NOT NULL,
                difficulty REAL NOT NULL,
                elapsed_days INTEGER NOT NULL,
                scheduled_days INTEGER NOT NULL,
                reps INTEGER NOT NULL,
                lapses INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (item_id) REFERENCES fsrs_items (item_id) ON DELETE CASCADE,
                UNIQUE(user_id, item_id)
            )
        ''')
        
        # 3. fsrs_review_logs: Log of all reviews
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fsrs_review_logs (
                log_id TEXT PRIMARY KEY,
                card_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                state INTEGER NOT NULL,
                due TIMESTAMP NOT NULL,
                stability REAL NOT NULL,
                difficulty REAL NOT NULL,
                elapsed_days INTEGER NOT NULL,
                last_elapsed_days INTEGER NOT NULL,
                scheduled_days INTEGER NOT NULL,
                review_time TIMESTAMP NOT NULL,
                FOREIGN KEY (card_id) REFERENCES fsrs_cards (card_id) ON DELETE CASCADE
            )
        ''')
        
        conn.commit()
    finally:
        conn.close()

def _card_to_fsrs_obj(row: dict) -> fsrs.Card:
    """Convert DB row to fsrs.Card"""
    card = fsrs.Card()
    if row:
        card.state = fsrs.State(row['state'])
        card.due = datetime.fromisoformat(row['due']).replace(tzinfo=timezone.utc)
        card.stability = float(row['stability'])
        card.difficulty = float(row['difficulty'])
        if 'step' in row and row['step'] is not None:
            card.step = int(row['step'])
        if 'last_review' in row and row['last_review'] is not None:
            card.last_review = datetime.fromisoformat(row['last_review']).replace(tzinfo=timezone.utc)
    return card

def populate_items_from_exams():
    """A helper to sync Day 5 Gateway Exam questions into fsrs_items."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Find exams that are sprint gateways
        cursor.execute("SELECT exam_id, title, questions FROM exams")
        rows = cursor.fetchall()
        for r in rows:
            d = dict(r)
            if "Day 5 Gateway Exam" in d.get("title", ""):
                try:
                    questions = json.loads(d["questions"])
                    domain = "general"
                    for q in questions:
                        q_text = str(q.get("question", "")).strip()
                        q_ans = str(q.get("correct_answer", "")).strip()
                        if not q_text or not q_ans: continue
                        
                        options = q.get("options", [])
                        if options:
                            opts_str = "\n".join([f"• {opt}" for opt in options])
                            q_text = f"{q_text}\n\n{opts_str}"
                        
                        # Generate ID based on original question so it matches existing
                        item_id = str(uuid.uuid5(uuid.NAMESPACE_OID, str(q.get("question", "")).strip().lower()))
                        
                        # Insert or update
                        cursor.execute("SELECT item_id FROM fsrs_items WHERE item_id = ?", (item_id,))
                        if cursor.fetchone():
                            cursor.execute("UPDATE fsrs_items SET question=?, answer=? WHERE item_id=?", (q_text, q_ans, item_id))
                        else:
                            cursor.execute('''
                                INSERT INTO fsrs_items (item_id, domain, question, answer)
                                VALUES (?, ?, ?, ?)
                            ''', (item_id, domain, q_text, q_ans))
                except Exception as e:
                    pass
        conn.commit()
    finally:
        conn.close()

def get_due_and_new_counts(user_id: str) -> dict:
    """Get counts of cards due today and completely new cards."""
    # Run sync just to be safe
    populate_items_from_exams()
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Count due cards
        cursor.execute('''
            SELECT COUNT(*) FROM fsrs_cards 
            WHERE user_id = ? AND due <= ?
        ''', (user_id, now_iso))
        due_count = cursor.fetchone()[0]
        
        # Count new items (items that don't have a card for this user)
        cursor.execute('''
            SELECT COUNT(*) FROM fsrs_items 
            WHERE item_id NOT IN (
                SELECT item_id FROM fsrs_cards WHERE user_id = ?
            )
        ''', (user_id,))
        new_count = cursor.fetchone()[0]
        
        return {"due": due_count, "new": new_count}
    finally:
        conn.close()

def get_next_card(user_id: str) -> dict:
    """Get the next card to review (due first, then new)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # 1. Look for due cards
        cursor.execute('''
            SELECT c.*, i.question, i.answer, i.domain 
            FROM fsrs_cards c
            JOIN fsrs_items i ON c.item_id = i.item_id
            WHERE c.user_id = ? AND c.due <= ?
            ORDER BY c.due ASC LIMIT 1
        ''', (user_id, now_iso))
        row = cursor.fetchone()
        
        if row:
            d = dict(row)
            d["is_new"] = False
            return d
            
        # 2. Look for new cards
        cursor.execute('''
            SELECT item_id, question, answer, domain 
            FROM fsrs_items 
            WHERE item_id NOT IN (
                SELECT item_id FROM fsrs_cards WHERE user_id = ?
            ) LIMIT 1
        ''', (user_id,))
        row = cursor.fetchone()
        
        if row:
            d = dict(row)
            d["is_new"] = True
            return d
            
        return None
    finally:
        conn.close()

def process_review(user_id: str, item_id: str, rating_val: int) -> bool:
    """
    Process a review. 
    rating_val: 1=Again, 2=Hard, 3=Good, 4=Easy
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM fsrs_cards WHERE user_id = ? AND item_id = ?", (user_id, item_id))
        row = cursor.fetchone()
        
        f = fsrs.Scheduler()
        card = fsrs.Card()
        card_id = str(uuid.uuid4())
        
        if row:
            d = dict(row)
            card = _card_to_fsrs_obj(d)
            card_id = d["card_id"]
        
        now = datetime.now(timezone.utc)
        rating = fsrs.Rating(rating_val)
        
        new_card, review_log = f.review_card(card, rating, now)
        
        if row:
            cursor.execute('''
                UPDATE fsrs_cards 
                SET state=?, due=?, stability=?, difficulty=?, elapsed_days=?, scheduled_days=?, reps=?, lapses=?
                WHERE card_id=?
            ''', (
                new_card.state.value, new_card.due.isoformat(), new_card.stability, new_card.difficulty,
                0, 0, getattr(new_card, 'step', None) or 0, 0,
                card_id
            ))
        else:
            cursor.execute('''
                INSERT INTO fsrs_cards (card_id, user_id, item_id, state, due, stability, difficulty, elapsed_days, scheduled_days, reps, lapses)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                card_id, user_id, item_id, new_card.state.value, new_card.due.isoformat(), new_card.stability, new_card.difficulty,
                0, 0, getattr(new_card, 'step', None) or 0, 0
            ))
            
        cursor.execute('''
            INSERT INTO fsrs_review_logs (log_id, card_id, rating, state, due, stability, difficulty, elapsed_days, last_elapsed_days, scheduled_days, review_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()), card_id, review_log.rating.value, new_card.state.value,
            new_card.due.isoformat(), new_card.stability, new_card.difficulty,
            getattr(review_log, 'review_duration', 0) or 0, 0, 0, now.isoformat()
        ))
        
        conn.commit()
        return True
    except Exception as e:
        import traceback
        with open("fsrs_error.log", "a") as f:
            f.write(traceback.format_exc())
            f.write(f"\nFSRS process_review error: {repr(e)}\n\n")
        print(f"FSRS process_review error: {e}")
        return False
    finally:
        conn.close()

def get_stats(user_id: str) -> dict:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT state, COUNT(*) FROM fsrs_cards WHERE user_id = ? GROUP BY state", (user_id,))
        rows = cursor.fetchall()
        
        counts = {0:0, 1:0, 2:0, 3:0}
        for r in rows:
            counts[r[0]] = r[1]
            
        return {
            "new": counts[0],
            "learning": counts[1],
            "review": counts[2],
            "relearning": counts[3],
            "total": sum(counts.values())
        }
    finally:
        conn.close()
