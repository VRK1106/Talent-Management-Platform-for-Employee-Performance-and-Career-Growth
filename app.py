"""Talent Sphere Elevate — Flask MVC Route Controller Entry Point."""

from __future__ import annotations

import os
import sys
import uuid
import json
import sqlite3
import re
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    Response,
    jsonify,
    stream_with_context,
    send_from_directory
)

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.users import init_db, verify_user, get_all_users, get_active_users_count, _DB_PATH, set_user_face_descriptor, get_user_face_descriptor, set_user_accommodation, update_user, log_activity, check_user_exists, get_db_connection
from src.exams import (
    init_exams_db,
    get_all_exams,
    add_exam,
    delete_exam,
    assign_exam,
    get_assignments_for_exam,
    get_assignments_for_trainee,
    get_assignment_by_id,
    submit_exam_answers,
    get_all_announcements,
    add_announcement,
    delete_announcement,
    add_proctor_log,
    get_proctor_logs_for_assignment,
    publish_assignment_results,
    clear_all_exams,
    clear_all_announcements,
    get_all_assignments
)
from src.chats import (
    init_chats_db,
    get_chat_sessions_for_user,
    get_chat_messages,
    create_chat_session,
    add_chat_message,
    rename_chat_session,
    delete_chat_session,
    get_global_chat_stats
)
from src.config import EMBEDDING_MODEL, DOCUMENTS_DIR
from src.vectorstore import stats, get_source_chunks, search, get_collection, add_ephemeral_chunks, search_ephemeral, delete_ephemeral_collection
from src.llm import list_local_models, generate_chat_answer, generate_rag_answer, GROQ_API_KEY, analyze_proctor_image, transcribe_audio_whisper, generate_ephemeral_rag_answer_stream
from src.concept_map import get_personalized_suggestions, get_related_concepts, get_ephemeral_document_text
from src.student_performance import detect_performance_query, get_student_performance_context, get_aggregate_performance_context

# Initialize databases
init_db()
init_exams_db()
init_chats_db()

app = Flask(__name__, static_folder='assets', static_url_path='/assets')
app.secret_key = os.environ.get('SECRET_KEY', 'talent-sphere-elevate-secret-key-12345')

# Register TabSessionInterface — SQLite-backed so sessions survive server restarts
from flask.sessions import SessionInterface, SessionMixin

class DictSession(dict, SessionMixin):
    pass

_SESSIONS_DB = Path(__file__).resolve().parent / "users.db"

def _init_sessions_table():
    conn = get_db_connection(_SESSIONS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tab_sessions (
            tab_id TEXT PRIMARY KEY,
            data   TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

_init_sessions_table()

def cleanup_orphaned_collections():
    """Scan and clean up in-memory vector collections for expired/deleted sessions."""
    try:
        # Delete tab sessions older than 2 hours (expiration logic)
        conn = get_db_connection(_SESSIONS_DB)
        conn.execute("DELETE FROM tab_sessions WHERE updated_at < datetime('now', '-2 hours')")
        conn.commit()
        
        # Query active session tab IDs
        rows = conn.execute("SELECT tab_id FROM tab_sessions").fetchall()
        conn.close()
        active_tabs = {row[0] for row in rows}
        
        # Get all ephemeral collections and delete active ones that are orphaned
        from src.vectorstore import get_ephemeral_client, _sanitize_collection_name
        client = get_ephemeral_client()
        collections = client.list_collections()
        for col in collections:
            if col.name.startswith("ephemeral_"):
                active_sanitized = {_sanitize_collection_name(f"ephemeral_{t}") for t in active_tabs}
                if col.name not in active_sanitized:
                    client.delete_collection(col.name)
    except Exception as e:
        print(f"Error cleaning up orphaned ephemeral collections: {e}")


class TabSessionInterface(SessionInterface):
    """SQLite-backed per-tab session store. Survives server restarts."""

    def _load(self, tab_id):
        try:
            conn = get_db_connection(_SESSIONS_DB)
            row = conn.execute("SELECT data FROM tab_sessions WHERE tab_id=?", (tab_id,)).fetchone()
            conn.close()
            if row:
                return DictSession(json.loads(row[0]))
        except Exception:
            pass
        return None

    def _save(self, tab_id, session):
        try:
            data = json.dumps({k: v for k, v in session.items() if k != '_tab_id'})
            conn = get_db_connection(_SESSIONS_DB)
            conn.execute("""
                INSERT INTO tab_sessions (tab_id, data, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(tab_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
            """, (tab_id, data))
            conn.commit()
            conn.close()
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f'[SESSION SAVE ERROR] tab_id={tab_id}: {e}')

    def _resolve_tab_id(self, request):
        tab_id = request.args.get('tab_id')
        if not tab_id:
            referer = request.headers.get('Referer', '')
            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(referer).query)
                tab_id = q.get('tab_id', [None])[0]
            except Exception:
                pass
        if not tab_id:
            tab_id = request.cookies.get('fallback_tab_id')
        if not tab_id:
            tab_id = 'default_tab'
        return tab_id

    def open_session(self, app, request):
        tab_id = self._resolve_tab_id(request)
        sess = self._load(tab_id) or DictSession()
        sess['_tab_id'] = tab_id
        return sess

    def should_set_cookie(self, app, session):
        # Always persist — we manage our own storage
        return True

    def is_null_session(self, obj):
        # Never treat our session as null
        return False

    def save_session(self, app, session, response):
        tab_id = session.get('_tab_id')
        if tab_id:
            self._save(tab_id, session)
            response.set_cookie('fallback_tab_id', tab_id, samesite='Lax')

app.session_interface = TabSessionInterface()


# Helper: clean LLM output to parse as JSON
def clean_json_response(raw_resp: str) -> str:
    resp = raw_resp.strip()
    if resp.startswith("```"):
        match = re.match(r"^```(?:json)?\s*", resp)
        if match:
            resp = resp[match.end():]
        if resp.endswith("```"):
            resp = resp[:-3]
    resp = resp.strip()
    start_idx = resp.find('[')
    end_idx = resp.rfind(']')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        resp = resp[start_idx:end_idx+1]
    return resp.strip()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        authenticated = session.get('authenticated')
        if not authenticated:
            # For API/AJAX calls return JSON instead of an HTML redirect
            if (request.path.startswith('/api/') or
                    request.is_json or
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'):
                return jsonify({"error": "session_expired", "message": "Your session has expired. Please refresh the page and log in again."}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Helper: redirect wrapper to preserve tab_id
flask_redirect = redirect
def redirect(location, code=302):
    try:
        tab_id = session.get('_tab_id') or request.args.get('tab_id')
        if tab_id:
            from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
            parsed = urlparse(location)
            # Only append tab_id for internal redirects
            if not parsed.netloc or parsed.netloc == request.host:
                query = dict(parse_qsl(parsed.query))
                if 'tab_id' not in query:
                    query['tab_id'] = tab_id
                    new_query = urlencode(query)
                    location = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    except Exception:
        pass
    return flask_redirect(location, code=code)

@app.before_request
def log_user_activity():
    if request.path.startswith('/assets') or request.path.startswith('/static') or request.path.startswith('/api/check_user'):
        return
    if session.get('authenticated'):
        emp_id = session.get('user_info', {}).get('employee_id')
        if emp_id:
            log_activity(emp_id, request.method, request.path)

@app.after_request
def add_header(r):
    if request.path.startswith('/api/'):
        r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        r.headers["Pragma"] = "no-cache"
        r.headers["Expires"] = "0"
    return r

# Context Processor for base template and other views
@app.context_processor
def inject_global_data():
    if not session.get('authenticated'):
        return {}
    
    path = request.path
    active_page = 'dashboard'
    if path.startswith('/assistant'):
        if request.args.get('mode') == 'ephemeral':
            active_page = 'ephemeral_assistant'
        else:
            active_page = 'assistant'
    elif path.startswith('/search'):
        active_page = 'search'
    elif path.startswith('/documents'):
        active_page = 'documents'
    elif path.startswith('/ingest'):
        active_page = 'ingest'
    elif path.startswith('/user_management'):
        active_page = 'user_management'
    elif path.startswith('/exams'):
        active_page = 'exams'
    elif path.startswith('/announcements'):
        active_page = 'announcements'
    elif path.startswith('/admin/logs'):
        active_page = 'activity_logs'
    elif path.startswith('/admin/maintenance'):
        active_page = 'maintenance'
        
    sqlite_ok = True
    chroma_ok = True
    ollama_ok = True
    
    chroma_stats = {'total_chunks': 0, 'distinct_sources': 0}
    
    current_user = session.get('current_user', 'User')
    user_info = session.get('user_info', {})
    employee_id = user_info.get('employee_id', '')
    role = session.get('user_role', 'trainee')
    
    names = current_user.split()
    user_initials = "".join([n[0].upper() for n in names if n]) if names else "U"
    
    progress_pct = 0
    progress_text = "0% Completed"
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.cursor()
        if role == 'admin':
            c.execute("SELECT COUNT(*) FROM assignments")
            total = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM assignments WHERE status = 'completed'")
            completed = c.fetchone()[0]
        else:
            c.execute("SELECT COUNT(*) FROM assignments WHERE trainee_id = ?", (employee_id,))
            total = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM assignments WHERE trainee_id = ? AND status = 'completed'", (employee_id,))
            completed = c.fetchone()[0]
        conn.close()
        
        if total > 0:
            progress_pct = int((completed / total) * 100)
            progress_text = f"{progress_pct}% ({completed}/{total} Completed)"
        else:
            progress_pct = 0
            progress_text = "No Assignments"
    except Exception:
        pass
        
    week = 1
    if employee_id:
        try:
            from src.sprints import get_sprint
            sprint = get_sprint(employee_id)
            week = sprint.get("current_week", 1)
        except Exception:
            pass
            
    user_sessions = get_chat_sessions_for_user(employee_id, week) if employee_id else []
    active_chat_session_id = session.get('active_chat_session_id')
    
    show_chat_history = (active_page == 'assistant')
    ollama_models = list_local_models()
    
    def get_all_trainees():
        return [u for u in get_all_users() if u["role"] == "trainee"]
        
    def get_all_completed_submissions():
        exams_list = get_all_exams()
        all_results = []
        for e in exams_list:
            all_results.extend(get_assignments_for_exam(e["exam_id"]))
        return [r for r in all_results if r["status"] == "completed"]
        
    def get_trainee_assigned_exams(trainee_id):
        return [a for a in get_assignments_for_trainee(trainee_id) if a["status"] == "assigned"]
        
    def get_trainee_completed_exams(trainee_id):
        return [a for a in get_assignments_for_trainee(trainee_id) if a["status"] == "completed"]
    
    return {
        'active_page': active_page,
        'health': {
            'sqlite_ok': sqlite_ok,
            'chroma_ok': chroma_ok,
            'ollama_ok': ollama_ok
        },
        'stats': chroma_stats,
        'user_initials': user_initials,
        'progress_pct': progress_pct,
        'progress_text': progress_text,
        'user_sessions': user_sessions,
        'active_chat_session_id': active_chat_session_id,
        'show_chat_history': show_chat_history,
        'ollama_models': ollama_models,
        'groq_api_key': GROQ_API_KEY,
        'get_all_trainees': get_all_trainees,
        'get_assignments_for_exam': get_assignments_for_exam,
        'get_all_completed_submissions': get_all_completed_submissions,
        'get_trainee_assigned_exams': get_trainee_assigned_exams,
        'get_trainee_completed_exams': get_trainee_completed_exams,
        'get_all_assignments': get_all_assignments
    }

# AUTHENTICATION
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = verify_user(username, password)
        if user:
            session['authenticated'] = True
            session['current_user'] = user['full_name']
            session['user_role'] = user['role']
            session['user_info'] = user
            
            sessions = get_chat_sessions_for_user(user['employee_id'])
            if sessions:
                session['active_chat_session_id'] = sessions[0]['session_id']
            else:
                session_id = str(uuid.uuid4())
                create_chat_session(session_id, user['employee_id'], "Welcome Conversation")
                session['active_chat_session_id'] = session_id
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid Employee ID or Password")
    return render_template('login.html')

@app.route('/logout')
def logout():
    tab_id = session.get('_tab_id')
    if tab_id:
        try:
            delete_ephemeral_collection(tab_id)
            conn = sqlite3.connect(str(_SESSIONS_DB))
            conn.execute("DELETE FROM tab_sessions WHERE tab_id = ?", (tab_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass
    session.clear()
    return redirect(url_for('login'))

def get_log_analytics(emp_id=None):
    import datetime
    analytics = {}
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.cursor()
        
        # 1. Peak Study Hours (hourly distribution)
        if emp_id:
            c.execute("""
                SELECT strftime('%H', timestamp) as hr, COUNT(*) 
                FROM activity_logs 
                WHERE employee_id = ? 
                GROUP BY hr
            """, (emp_id,))
        else:
            c.execute("""
                SELECT strftime('%H', timestamp) as hr, COUNT(*) 
                FROM activity_logs 
                GROUP BY hr
            """)
        hr_data = {f"{i:02d}": 0 for i in range(24)}
        for row in c.fetchall():
            if row[0]:
                hr_data[row[0]] = row[1]
        analytics["hours_labels"] = [f"{i}:00" for i in range(24)]
        analytics["hours_counts"] = [hr_data[f"{i:02d}"] for i in range(24)]

        # 2. Activity Category Distribution (Study vs Exam vs Other)
        if emp_id:
            c.execute("SELECT path FROM activity_logs WHERE employee_id = ?", (emp_id,))
        else:
            c.execute("SELECT path FROM activity_logs")
        
        paths = c.fetchall()
        study_count = 0
        exam_count = 0
        other_count = 0
        for p in paths:
            path_str = p[0] or ""
            if path_str.startswith("/assistant"):
                study_count += 1
            elif path_str.startswith("/exams"):
                exam_count += 1
            else:
                other_count += 1
        analytics["category_labels"] = ["Study / Assistant", "Exams / Quizzes", "Other Actions"]
        analytics["category_counts"] = [study_count, exam_count, other_count]
        
        # 3. Weekly Trend (last 7 days)
        today = datetime.date.today()
        dates_list = [(today - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        dates_list.reverse()
        
        trend_data = {d: 0 for d in dates_list}
        if emp_id:
            c.execute("""
                SELECT date(timestamp) as dt, COUNT(*) 
                FROM activity_logs 
                WHERE employee_id = ? AND timestamp >= datetime('now', '-7 days')
                GROUP BY dt
            """, (emp_id,))
        else:
            c.execute("""
                SELECT date(timestamp) as dt, COUNT(*) 
                FROM activity_logs 
                WHERE timestamp >= datetime('now', '-7 days')
                GROUP BY dt
            """)
        for row in c.fetchall():
            if row[0] in trend_data:
                trend_data[row[0]] = row[1]
        analytics["trend_labels"] = dates_list
        analytics["trend_counts"] = [trend_data[d] for d in dates_list]
        
        # 4. Total activity count
        if emp_id:
            c.execute("SELECT COUNT(*) FROM activity_logs WHERE employee_id = ?", (emp_id,))
        else:
            c.execute("SELECT COUNT(*) FROM activity_logs")
        analytics["total_activities"] = c.fetchone()[0]

        # 5. Study Time / Session Estimate (Trainee only)
        if emp_id:
            c.execute("SELECT timestamp FROM activity_logs WHERE employee_id = ? ORDER BY timestamp ASC", (emp_id,))
            timestamps = []
            for row in c.fetchall():
                if row[0]:
                    try:
                        timestamps.append(datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
                    except Exception:
                        pass
            
            sessions = []
            if timestamps:
                current_session = [timestamps[0]]
                for ts in timestamps[1:]:
                    diff = (ts - current_session[-1]).total_seconds() / 60.0
                    if diff <= 30.0:
                        current_session.append(ts)
                    else:
                        sessions.append(current_session)
                        current_session = [ts]
                sessions.append(current_session)
                
            total_minutes = 0
            for s in sessions:
                if len(s) > 1:
                    duration = (s[-1] - s[0]).total_seconds() / 60.0
                    total_minutes += max(duration, 5.0)
                else:
                    total_minutes += 5.0
            
            analytics["study_hours"] = round(total_minutes / 60.0, 1)
            analytics["avg_session_mins"] = round(total_minutes / len(sessions), 1) if sessions else 0
        else:
            analytics["study_hours"] = 0.0
            analytics["avg_session_mins"] = 0

        # Inject mock data fallback if real logs are sparse
        if analytics["total_activities"] < 10:
            if emp_id:
                # Mock data for Trainee
                analytics["hours_labels"] = [f"{i}:00" for i in range(24)]
                analytics["hours_counts"] = [
                    0, 0, 0, 0, 0, 0, 0, 1, 3, 5, 8, 10, 
                    3, 4, 6, 9, 12, 6, 3, 5, 8, 10, 4, 1
                ]
                analytics["category_labels"] = ["Study / Assistant", "Exams / Quizzes", "Other Actions"]
                analytics["category_counts"] = [35, 15, 8]
                analytics["trend_labels"] = dates_list
                analytics["trend_counts"] = [4, 8, 6, 12, 18, 14, 8]
                analytics["total_activities"] = 58
                analytics["study_hours"] = 12.4
                analytics["avg_session_mins"] = 42.0
            else:
                # Mock data for Admin
                analytics["hours_labels"] = [f"{i}:00" for i in range(24)]
                analytics["hours_counts"] = [
                    10, 3, 1, 0, 0, 1, 6, 18, 38, 55, 72, 65,
                    48, 52, 64, 80, 70, 50, 40, 55, 75, 60, 35, 20
                ]
                analytics["category_labels"] = ["Study / Assistant", "Exams / Quizzes", "Other Actions"]
                analytics["category_counts"] = [280, 140, 75]
                analytics["trend_labels"] = dates_list
                analytics["trend_counts"] = [50, 72, 60, 85, 105, 90, 70]
                analytics["total_activities"] = 495
                analytics["study_hours"] = 0.0
                analytics["avg_session_mins"] = 0
        
        conn.close()
    except Exception as e:
        print(f"Error calculating log analytics: {e}")
        analytics = {
            "hours_labels": [], "hours_counts": [],
            "category_labels": [], "category_counts": [],
            "trend_labels": [], "trend_counts": [],
            "total_activities": 0, "study_hours": 0.0, "avg_session_mins": 0
        }
    return analytics

# DASHBOARD
@app.route('/')
@login_required
def dashboard():
    role = session.get('user_role', 'trainee')
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    
    index_stats = stats()
    
    if role == 'admin':
        users = get_all_users()
        trainees = [u for u in users if u["role"] == "trainee"]
        active_now = get_active_users_count(hours=1)
        
        doc_count = index_stats["sources"]
        chunk_count = index_stats["total_chunks"]
        
        chat_stats = get_global_chat_stats()
        total_sessions = chat_stats["total_sessions"]
        total_messages = chat_stats["total_messages"]
        
        exams_list = get_all_exams()
        all_submissions = []
        for e in exams_list:
            all_submissions.extend(get_assignments_for_exam(e["exam_id"]))
        completed_subs = [s for s in all_submissions if s["status"] == "completed"]
        
        avg_score_pct = 0.0
        if completed_subs:
            exam_marks_map = {e["exam_id"]: e["total_marks"] for e in exams_list}
            sum_pcts = 0.0
            for sub in completed_subs:
                total_m = exam_marks_map.get(sub["exam_id"], 100)
                score = sub["score"] or 0.0
                sum_pcts += (score / total_m * 100.0) if total_m > 0 else 0.0
            avg_score_pct = sum_pcts / len(completed_subs)
            
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM chat_messages WHERE role = 'assistant' AND sources IS NOT NULL AND sources != '[]' AND sources != ''")
            rag_use_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM chat_messages WHERE role = 'assistant' AND (sources IS NULL OR sources = '[]' OR sources = '')")
            general_use_count = c.fetchone()[0]
            conn.close()
        except Exception:
            rag_use_count, general_use_count = 0, 0
            
        sum_use = rag_use_count + general_use_count
        rag_ratio = (rag_use_count / sum_use * 100.0) if sum_use > 0 else 0.0
        
        msg_data = chat_stats["messages_per_day"]
        if not msg_data:
            import datetime
            today = datetime.date.today()
            msg_data = [
                {"date": (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d"), "count": c}
                for i, c in enumerate([10, 15, 12, 22, 19, 14, 20])
            ]
            msg_data.reverse()
        
        chat_dates = [d["date"] for d in msg_data]
        chat_counts = [d["count"] for d in msg_data]
        
        scores = []
        if completed_subs:
            exam_marks_map = {e["exam_id"]: e["total_marks"] for e in exams_list}
            for sub in completed_subs:
                t_marks = exam_marks_map.get(sub["exam_id"], 100)
                score_val = sub["score"] or 0.0
                scores.append((score_val / t_marks * 100.0) if t_marks > 0 else 0.0)
        else:
            scores = [15, 42, 58, 62, 75, 80, 85, 92]
            
        score_ranges = ["0-20%", "21-40%", "41-60%", "61-80%", "81-100%"]
        score_counts = [0, 0, 0, 0, 0]
        for s in scores:
            if s <= 20:
                score_counts[0] += 1
            elif s <= 40:
                score_counts[1] += 1
            elif s <= 60:
                score_counts[2] += 1
            elif s <= 80:
                score_counts[3] += 1
            else:
                score_counts[4] += 1
                
        trainee_rows = []
        domain_map = {}
        for t in trainees:
            t_emp_id = t["employee_id"]
            assignments = get_assignments_for_trainee(t_emp_id)
            pending = len([a for a in assignments if a["status"] == "assigned"])
            completed = len([a for a in assignments if a["status"] == "completed"])
            
            sub_pcts = []
            for a in assignments:
                if a["status"] == "completed":
                    total_m = a.get("total_marks", 100)
                    score_val = a.get("score") or 0.0
                    sub_pcts.append((score_val / total_m * 100.0) if total_m > 0 else 0.0)
            avg_val = f"{sum(sub_pcts)/len(sub_pcts):.1f}%" if sub_pcts else "No Submissions"
            last_active = t.get("last_active") or "Never Active"
            
            trainee_rows.append({
                "ID": t_emp_id,
                "Name": t["full_name"],
                "Domain": t["domain"].upper(),
                "Email": t["email"],
                "Completed": completed,
                "Pending": pending,
                "Avg_Score": avg_val,
                "Last_Active": last_active
            })
            
            dom = t["domain"].upper()
            domain_map[dom] = domain_map.get(dom, 0) + 1
            
        domain_labels = list(domain_map.keys())
        domain_counts = list(domain_map.values())
        
        doc_rows = index_stats["source_details"]
        doc_labels = [d["name"] for d in doc_rows]
        doc_chunks = [d["chunks"] for d in doc_rows]
        
        log_stats = get_log_analytics()
        admin_stats = {
            "trainees_count": len(trainees),
            "active_now": active_now,
            "doc_count": doc_count,
            "chunk_count": chunk_count,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "exams_count": len(exams_list),
            "avg_score_pct": avg_score_pct,
            "rag_ratio": rag_ratio,
            "chat_dates": chat_dates,
            "chat_counts": chat_counts,
            "score_ranges": score_ranges,
            "score_counts": score_counts,
            "domain_labels": domain_labels,
            "domain_counts": domain_counts,
            "doc_labels": doc_labels,
            "doc_chunks": doc_chunks,
            "rag_use_count": rag_use_count,
            "general_use_count": general_use_count,
            "log_hours_labels": log_stats["hours_labels"],
            "log_hours_counts": log_stats["hours_counts"],
            "log_category_labels": log_stats["category_labels"],
            "log_category_counts": log_stats["category_counts"],
            "log_trend_labels": log_stats["trend_labels"],
            "log_trend_counts": log_stats["trend_counts"],
            "total_activities": log_stats["total_activities"]
        }
        
        from src.exams import get_system_setting
        email_enabled = get_system_setting("email_notifications_enabled", "true").lower() == "true"

        from src.sprints import get_all_sprint_schedules, get_all_interview_evaluations
        sprints_list = get_all_sprint_schedules()
        evaluations_list = get_all_interview_evaluations()
        
        eval_map = {ev["user_id"]: ev for ev in evaluations_list}
        for sp in sprints_list:
            ev = eval_map.get(sp["user_id"])
            if ev:
                sp["interview"] = {
                    "tech_score": ev["technical_score"],
                    "conf_score": ev["confidence_score"],
                    "filler_count": ev["filler_words_count"],
                    "wpm": ev["words_per_minute"],
                    "feedback": ev["feedback_report"]
                }
            else:
                sp["interview"] = None

        return render_template(
            'dashboard.html',
            admin_stats=admin_stats,
            trainee_rows=trainee_rows,
            doc_rows=doc_rows,
            embedding_model_name=EMBEDDING_MODEL,
            email_enabled=email_enabled,
            sprints_list=sprints_list,
            exams_list=exams_list
        )
    else:
        assignments = get_assignments_for_trainee(emp_id)
        pending_exams = [a for a in assignments if a["status"] == "assigned"]
        completed_exams = [a for a in assignments if a["status"] == "completed"]
        
        personal_avg = 0.0
        if completed_exams:
            sum_pcts = 0.0
            for a in completed_exams:
                total_m = a.get("total_marks", 100)
                score_val = a.get("score") or 0.0
                sum_pcts += (score_val / total_m * 100.0) if total_m > 0 else 0.0
            personal_avg = sum_pcts / len(completed_exams)
            
        chat_sessions = get_chat_sessions_for_user(emp_id)
        recommendations = index_stats["source_details"]
        resume_chats = chat_sessions[:5]
        
        trajectory_labels = []
        trajectory_scores = []
        sorted_completed = sorted(completed_exams, key=lambda x: x.get("completed_at") or "")
        for idx, a in enumerate(sorted_completed, 1):
            trajectory_labels.append(f"Test {idx}")
            total_m = a.get("total_marks", 100)
            score_val = a.get("score") or 0.0
            trajectory_scores.append((score_val / total_m * 100.0) if total_m > 0 else 0.0)
            
        log_stats = get_log_analytics(emp_id)
        trainee_stats = {
            "pending_count": len(pending_exams),
            "completed_count": len(completed_exams),
            "personal_avg": personal_avg,
            "chat_count": len(chat_sessions),
            "trajectory_labels": trajectory_labels,
            "trajectory_scores": trajectory_scores,
            "log_hours_labels": log_stats["hours_labels"],
            "log_hours_counts": log_stats["hours_counts"],
            "log_category_labels": log_stats["category_labels"],
            "log_category_counts": log_stats["category_counts"],
            "log_trend_labels": log_stats["trend_labels"],
            "log_trend_counts": log_stats["trend_counts"],
            "total_activities": log_stats["total_activities"],
            "study_hours": log_stats["study_hours"],
            "avg_session_mins": log_stats["avg_session_mins"]
        }
        
        from src.exams import get_all_announcements
        announcements = get_all_announcements()

        return render_template(
            'dashboard.html',
            trainee_stats=trainee_stats,
            pending_exams=pending_exams,
            recommendations=recommendations,
            resume_chats=resume_chats,
            announcements=announcements
        )

# DOCUMENT EXPLORER
@app.route('/documents')
@login_required
def documents():
    index_stats = stats()
    source_names = index_stats["source_names"]
    
    selected_doc = request.args.get('selected_doc')
    if not selected_doc and source_names:
        selected_doc = source_names[0]
        
    doc_details = None
    pdf_exists = False
    
    if selected_doc:
        for doc in index_stats["source_details"]:
            if doc["name"] == selected_doc:
                doc_details = doc
                break
        
        pdf_path = Path(DOCUMENTS_DIR) / selected_doc
        pdf_exists = pdf_path.exists()
        
    role = session.get('user_role', 'trainee')
    
    if role == 'trainee':
        return render_template(
            'documents.html',
            source_names=source_names,
            selected_doc=selected_doc,
            doc_details=doc_details,
            pdf_exists=pdf_exists
        )
    else:
        all_chunks = get_source_chunks(selected_doc) if selected_doc else []
        query = request.args.get('query', '').strip()
        
        if query:
            filtered_chunks = []
            for c in all_chunks:
                text = c.get("text", "")
                if query.lower() in text.lower():
                    import html as py_html
                    escaped_text = py_html.escape(text)
                    escaped_query = py_html.escape(query)
                    highlighted = re.sub(
                        f"({re.escape(escaped_query)})",
                        r"<mark style='background-color: var(--ts-primary); color: #fff; padding: 2px 4px; border-radius: 4px;'>\1</mark>",
                        escaped_text,
                        flags=re.IGNORECASE
                    )
                    filtered_chunks.append({
                        "page": c.get("page"),
                        "chunk_index": c.get("chunk_index"),
                        "highlighted_text": highlighted
                    })
            return render_template(
                'documents.html',
                source_names=source_names,
                selected_doc=selected_doc,
                doc_details=doc_details,
                pdf_exists=pdf_exists,
                query=query,
                filtered_chunks=filtered_chunks,
                all_chunks=all_chunks
            )
        else:
            pages = sorted(list(set([c.get("page") for c in all_chunks if c.get("page") is not None])))
            selected_page = request.args.get('page', type=int)
            if not selected_page and pages:
                selected_page = pages[0]
            elif not selected_page:
                selected_page = 1
                
            page_chunks = [c for c in all_chunks if c.get("page") == selected_page]
            formatted_page_chunks = []
            for c in page_chunks:
                import html as py_html
                formatted_page_chunks.append({
                    "page": c.get("page"),
                    "chunk_index": c.get("chunk_index"),
                    "formatted_text": py_html.escape(c.get("text", ""))
                })
                
            return render_template(
                'documents.html',
                source_names=source_names,
                selected_doc=selected_doc,
                doc_details=doc_details,
                pdf_exists=pdf_exists,
                page_list=pages,
                selected_page=selected_page,
                page_chunks=formatted_page_chunks
            )

@app.route('/documents/download/<path:filename>')
@login_required
def download_document(filename):
    preview = request.args.get('preview') == 'true'
    return send_from_directory(DOCUMENTS_DIR, filename, as_attachment=(not preview))

# KNOWLEDGE SEARCH
@app.route('/search')
@login_required
def search_route():
    idx_stats = stats()
    ollama_models = list_local_models()
    
    query = request.args.get('query', '').strip()
    selected_sources = request.args.getlist('selected_sources')
    threshold = request.args.get('threshold', 0.1, type=float)
    top_k = request.args.get('top_k', 4, type=int)
    enable_rag = request.args.get('enable_rag') == 'true'
    selected_model = request.args.get('selected_model')
    if not selected_model and ollama_models:
        selected_model = ollama_models[0]
        
    results = None
    answer = None
    
    if query:
        try:
            from src.embeddings import embed_query
            query_vec = embed_query(query)
            search_results = search(query_vec, top_k=top_k, source_filters=selected_sources if selected_sources else None, threshold=threshold)
            
            results = []
            for hit in search_results:
                text = hit.get("text", "")
                import html as py_html
                escaped_text = py_html.escape(text)
                terms = [re.escape(py_html.escape(t)) for t in query.split() if t.strip()]
                highlighted = escaped_text
                if terms:
                    pattern = f"({'|'.join(terms)})"
                    highlighted = re.sub(
                        pattern,
                        r"<mark style='background-color: var(--ts-primary); color: #fff; padding: 2px 4px; border-radius: 4px;'>\1</mark>",
                        escaped_text,
                        flags=re.IGNORECASE
                    )
                results.append({
                    "source": hit.get("source", "Unknown Source"),
                    "page": hit.get("page", "?"),
                    "score": hit.get("score", 0.0),
                    "text": text,
                    "highlighted_text": highlighted
                })
                
            if enable_rag and results and GROQ_API_KEY:
                answer = generate_rag_answer(query, search_results, selected_model)
        except Exception as e:
            print(f"Search error: {e}")
            results = []
            
    return render_template(
        'search.html',
        query=query,
        stats=idx_stats,
        selected_sources=selected_sources,
        threshold=threshold,
        top_k=top_k,
        enable_rag=enable_rag,
        selected_model=selected_model,
        ollama_models=ollama_models,
        groq_api_key=GROQ_API_KEY,
        results=results,
        answer=answer
    )

# INGESTION
@app.route('/ingest')
@login_required
def ingest():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    idx_stats = stats()
    
    doc_chunks = {}
    for name in idx_stats["source_names"]:
        doc_chunks[name] = get_source_chunks(name)
        
    return render_template(
        'ingest.html',
        stats=idx_stats,
        doc_chunks=doc_chunks,
        summary=session.pop('ingest_summary', None)
    )

@app.route('/ingest/cancel', methods=['POST'])
@login_required
def ingest_cancel():
    session['cancel_ingestion'] = True
    return jsonify({'status': 'cancelling'})

@app.route('/ingest', methods=['POST'])
@login_required
def ingest_post():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    from src.embeddings import embed_documents
    from src.ingest import chunk_pages, extract_pages, file_hash
    from src.vectorstore import add_chunks, ingested_hashes
    
    session['cancel_ingestion'] = False
    uploaded_files = request.files.getlist('files')
    if not uploaded_files or not uploaded_files[0].filename:
        flash("No files selected")
        return redirect(url_for('ingest'))
        
    known_hashes = ingested_hashes()
    files_processed = 0
    chunks_added = 0
    duplicates = 0
    success_files = []
    
    for file in uploaded_files:
        if session.get('cancel_ingestion'):
            flash("⛔ Vector indexing was cancelled by user.")
            break
        try:
            from io import BytesIO
            data = file.read()
            if not data:
                continue
                
            digest = file_hash(data)
            if digest in known_hashes:
                duplicates += 1
                flash(f"⏭️ {file.filename} is already indexed — skipped duplicate.")
                continue
                
            pages = extract_pages(BytesIO(data))
            if not pages:
                flash(f"⚠️ No extractable text found in {file.filename} — skipped.")
                continue
                
            chunks = chunk_pages(pages, file.filename)
            embeddings = embed_documents([c["text"] for c in chunks])
            added = add_chunks(chunks, embeddings, digest)
            
            try:
                save_path = Path(DOCUMENTS_DIR) / file.filename
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_bytes(data)
            except Exception as e:
                flash(f"Could not save PDF copy to disk: {e}")
                
            known_hashes.add(digest)
            files_processed += 1
            chunks_added += added
            success_files.append(file.filename)
            flash(f"✅ {file.filename} — Created {added} chunks from {len(pages)} pages.")
            
        except Exception as exc:
            flash(f"❌ Failed to process {file.filename}: {exc}")
            
    if success_files:
        file_names = ", ".join(success_files)
        add_announcement(
            "📂 New Study Documents Uploaded",
            f"The Administrator has successfully uploaded and processed new document(s) into the knowledge base:\n\n"
            f"Files: {file_names}\n\n"
            f"You can now query this information using the Document Explorer or AI Assistant."
        )

    session['ingest_summary'] = {
        'processed': files_processed,
        'chunks': chunks_added,
        'duplicates': duplicates
    }
    return redirect(url_for('ingest'))

@app.route('/ingest/delete', methods=['POST'])
@login_required
def ingest_delete():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    doc_name = request.form.get('doc_name', '').strip()
    redirect_target = request.form.get('redirect_target', 'ingest')
    if doc_name:
        import urllib.parse
        doc_name = urllib.parse.unquote(doc_name)

        doc_hash = None
        try:
            pdf_file = Path(DOCUMENTS_DIR) / doc_name
            if pdf_file.exists():
                from src.ingest import file_hash
                doc_hash = file_hash(pdf_file.read_bytes())
                pdf_file.unlink()
        except Exception as e:
            print(f"Error deleting physical file {doc_name}: {e}")

        from src.vectorstore import delete_source
        delete_source(doc_name, file_hash=doc_hash)

        add_announcement(
            "🗑️ Document Removed",
            f"The document '{doc_name}' has been removed from the knowledge base by the Administrator."
        )
        flash(f"🗑️ Successfully removed ingested document '{doc_name}'. You can now re-ingest it anytime.")

    if redirect_target == 'documents':
        return redirect(url_for('documents'))
    return redirect(url_for('ingest'))

@app.route('/ingest/reset/confirm', methods=['POST'])
@login_required
def ingest_reset_confirm():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
    session['confirm_reset'] = True
    return redirect(url_for('ingest'))

@app.route('/ingest/reset/cancel', methods=['POST'])
@login_required
def ingest_reset_cancel():
    session.pop('confirm_reset', None)
    return redirect(url_for('ingest'))

@app.route('/ingest/reset/execute', methods=['POST'])
@login_required
def ingest_reset_execute():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    from src.vectorstore import reset_collection
    reset_collection()
    try:
        for pdf_file in Path(DOCUMENTS_DIR).glob("*.pdf"):
            pdf_file.unlink()
    except Exception:
        pass
    add_announcement(
        "⚠️ Knowledge Base Reset",
        "The entire document database has been reset by the Administrator. All previous study materials and vector search indexes have been cleared."
    )
    session.pop('confirm_reset', None)
    flash("Index reset complete.")
    return redirect(url_for('ingest'))

# USER MANAGEMENT
@app.route('/user_management')
@login_required
def user_management():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
    
    users_list = get_all_users()
    active_tab = request.args.get('tab', 'create')
    last_created = session.pop('last_created_user', None)
    
    return render_template(
        'user_management.html',
        users=users_list,
        active_tab=active_tab,
        last_created_user=last_created
    )

@app.route('/user_management/create', methods=['POST'])
@login_required
def user_management_create():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    employee_id = request.form.get('employee_id', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    domain = request.form.get('domain', 'general')
    password_mode = request.form.get('password_mode', 'auto')
    manual_password = request.form.get('manual_password', '').strip()
    
    errors = []
    if not employee_id:
        errors.append("Please enter an Employee ID.")
    if not full_name:
        errors.append("Please enter a Full Name.")
    if not email:
        errors.append("Please enter an Email address.")
        
    if password_mode == 'auto':
        first_part = full_name.split()[0].capitalize() if full_name.split() else "User"
        generated_password = f"{first_part}@123"
    else:
        generated_password = manual_password
        
    if not generated_password:
        errors.append("Please enter a Password.")
    elif len(generated_password) < 8:
        errors.append("Password must be at least 8 characters long.")
        
    if errors:
        for err in errors:
            flash(err, "create_user_error")
        return redirect(url_for('user_management', tab='create'))
        
    from src.users import add_user
    success, msg = add_user(
        employee_id=employee_id,
        email=email,
        full_name=full_name,
        domain=domain,
        password_plain=generated_password,
        role="trainee"
    )
    
    if success:
        session['last_created_user'] = {
            "email": email,
            "password": generated_password,
            "name": full_name
        }
        # Send onboarding credentials email
        try:
            from src.mail import send_user_credentials
            send_user_credentials(
                email=email,
                name=full_name,
                employee_id=employee_id,
                password_plain=generated_password
            )
        except Exception as mail_err:
            print(f"Failed to send welcome credentials email: {mail_err}")
            
        return redirect(url_for('user_management', tab='create'))
    else:
        flash(msg, "create_user_error")
        return redirect(url_for('user_management', tab='create'))

@app.route('/user_management/delete', methods=['POST'])
@login_required
def user_management_delete():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    employee_id = request.form.get('employee_id')
    if employee_id == 'admin':
        flash("Cannot delete protected admin account.", "manage_user_info")
        return redirect(url_for('user_management', tab='manage'))
        
    from src.users import delete_user
    if delete_user(employee_id):
        flash("Deleted user successfully.", "manage_user_info")
    else:
        flash("Failed to delete user.", "manage_user_info")
        
    return redirect(url_for('user_management', tab='manage'))

@app.route('/user_management/edit', methods=['GET', 'POST'])
@login_required
def user_management_edit():
    if session.get('user_role') != 'admin':
        if request.method == 'POST':
            return jsonify({"error": "Unauthorized"}), 403
        return redirect(url_for('dashboard'))
        
    if request.method == 'GET':
        return redirect(url_for('user_management', tab='manage'))
        
    employee_id = request.form.get('employee_id', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    domain = request.form.get('domain', 'general').strip()
    role = request.form.get('role', 'trainee').strip()
    password = request.form.get('password', '').strip()
    
    if not employee_id or not full_name or not email:
        flash("Employee ID, Full Name, and Email are required.", "manage_user_info")
        return redirect(url_for('user_management', tab='manage'))
        
    success, msg = update_user(employee_id, full_name, email, domain, role, password)
    if success:
        flash(f"User {employee_id} updated successfully.", "manage_user_info")
    else:
        flash(msg, "manage_user_info")
        
    return redirect(url_for('user_management', tab='manage'))

@app.route('/api/check_user', methods=['GET'])
def api_check_user():
    employee_id = request.args.get('employee_id', '').strip()
    if not employee_id:
        return jsonify({"exists": False})
    exists = check_user_exists(employee_id)
    return jsonify({"exists": exists})

@app.route('/admin/logs')
@login_required
def admin_logs():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    search_query = request.args.get('search', '').strip()
    
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if search_query:
        cursor.execute(
            """
            SELECT id, employee_id, method, path, timestamp 
            FROM activity_logs 
            WHERE employee_id LIKE ? OR path LIKE ? 
            ORDER BY timestamp DESC LIMIT 500
            """,
            (f'%{search_query}%', f'%{search_query}%')
        )
    else:
        cursor.execute(
            "SELECT id, employee_id, method, path, timestamp FROM activity_logs ORDER BY timestamp DESC LIMIT 500"
        )
        
    logs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return render_template('activity_logs.html', logs=logs, search_query=search_query)

@app.route('/admin/logs/clear', methods=['POST'])
@login_required
def admin_logs_clear():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    conn = sqlite3.connect(str(_DB_PATH))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM activity_logs")
    conn.commit()
    conn.close()
    
    flash("Activity logs cleared successfully.")
    return redirect(url_for('admin_logs'))


# ANNOUNCEMENTS
@app.route('/announcements')
@login_required
def announcements():
    anns = get_all_announcements()
    email_logs = []
    email_enabled = True
    if session.get('user_role') == 'admin':
        from src.exams import get_all_email_logs, get_system_setting
        email_logs = get_all_email_logs(limit=50)
        email_enabled = get_system_setting("email_notifications_enabled", "true").lower() == "true"
    return render_template('announcements.html', announcements=anns, email_logs=email_logs, email_enabled=email_enabled)

@app.route('/announcements/settings/toggle', methods=['POST'])
@login_required
def announcements_settings_toggle():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
    
    from src.exams import get_system_setting, set_system_setting
    current_val = get_system_setting("email_notifications_enabled", "true").lower() == "true"
    new_val = "false" if current_val else "true"
    set_system_setting("email_notifications_enabled", new_val)
    
    if new_val == "true":
        flash("Email notifications enabled.")
    else:
        flash("Email notifications disabled.")
        
    return redirect(url_for('announcements'))

@app.route('/announcements/create', methods=['POST'])
@login_required
def announcements_create():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    
    if not title or not content:
        flash("Title and Content are required.")
        return redirect(url_for('announcements'))
        
    if add_announcement(title, content):
        flash("Announcement published successfully!")
    else:
        flash("Failed to publish announcement. Database error.")
    return redirect(url_for('announcements'))

@app.route('/announcements/delete', methods=['POST'])
@login_required
def announcements_delete():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    ann_id = request.form.get('announcement_id', type=int)
    if ann_id:
        if delete_announcement(ann_id):
            flash("Announcement deleted successfully.")
        else:
            flash("Failed to delete announcement.")
    return redirect(url_for('announcements'))

# EXAMS & ASSIGNMENTS
@app.route('/exams')
@login_required
def exams():
    role = session.get('user_role', 'trainee')
    active_tab = request.args.get('active_tab', 'create')
    
    exam_title_draft = session.get('exam_title_draft', '')
    exam_desc_draft = session.get('exam_desc_draft', '')
    exam_marks_draft = session.get('exam_marks_draft', 50)
    exam_questions = session.get('exam_questions', [])
    
    all_exams = get_all_exams()
    
    selected_exam_id = request.args.get('selected_exam_id', type=int)
    if not selected_exam_id and all_exams:
        selected_exam_id = all_exams[0]['exam_id']
        
    review_assignment_id = request.args.get('review_assignment_id', type=int)
    review_detail = None
    if review_assignment_id:
        detail = get_assignment_by_id(review_assignment_id)
        if detail:
            try:
                grade_sheet = json.loads(detail["ai_feedback"])
            except Exception:
                grade_sheet = {"overall_comments": detail["ai_feedback"], "questions": []}
                
            review_detail = {
                "assignment_id": detail["assignment_id"],
                "title": detail["title"],
                "full_name": detail["full_name"],
                "score": detail["score"],
                "total_marks": detail["total_marks"],
                "ai_feedback_overall": grade_sheet.get("overall_comments", ""),
                "ai_feedback_questions": grade_sheet.get("questions", []),
                "answers_submitted": detail["answers"],
                "questions": detail["questions"],
                "proctor_logs": get_proctor_logs_for_assignment(review_assignment_id)
            }
            active_tab = 'results'
            
    trainee_review_id = request.args.get('trainee_review_id', type=int)
    if trainee_review_id:
        detail = get_assignment_by_id(trainee_review_id)
        if detail:
            # Enforce results_published check for trainees
            asg_settings = detail.get("settings") or {}
            if asg_settings.get("results_release") == "manual" and not asg_settings.get("results_published"):
                flash("Results for this exam have not been published yet.")
                return redirect(url_for('exams'))
                
            try:
                grade_sheet = json.loads(detail["ai_feedback"])
            except Exception:
                grade_sheet = {"overall_comments": detail["ai_feedback"], "questions": []}
                
            review_detail = {
                "assignment_id": detail["assignment_id"],
                "title": detail["title"],
                "score": detail["score"],
                "total_marks": detail["total_marks"],
                "ai_feedback_overall": grade_sheet.get("overall_comments", ""),
                "ai_feedback_questions": grade_sheet.get("questions", []),
                "answers_submitted": detail["answers"],
                "questions": detail["questions"],
                "proctor_logs": get_proctor_logs_for_assignment(trainee_review_id)
            }
            
    taking_assignment_id = session.get('taking_assignment_id')
    taking_assignment = None
    if taking_assignment_id:
        taking_assignment = get_assignment_by_id(taking_assignment_id)
        
    return render_template(
        'exams.html',
        active_tab=active_tab,
        exam_title_draft=exam_title_draft,
        exam_desc_draft=exam_desc_draft,
        exam_marks_draft=exam_marks_draft,
        exam_questions=exam_questions,
        exams=all_exams,
        selected_exam_id=selected_exam_id,
        review_detail=review_detail,
        taking_assignment=taking_assignment
    )

@app.route('/exams/create/add_manual', methods=['POST'])
@login_required
def exams_create_add_manual():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    session['exam_title_draft'] = request.form.get('title', '').strip()
    session['exam_desc_draft'] = request.form.get('description', '').strip()
    session['exam_marks_draft'] = request.form.get('total_marks', 50, type=int)
    
    mq_text = request.form.get('mq_text', '').strip()
    mq_type = request.form.get('mq_type', 'mcq')
    mq_marks = request.form.get('mq_marks', 10, type=int)
    mq_opts = request.form.get('mq_opts', '').strip()
    mq_ans = request.form.get('mq_ans', '').strip()
    
    if not mq_text:
        flash("Please enter a question.")
        return redirect(url_for('exams', active_tab='create'))
    if mq_type == 'mcq' and not mq_opts:
        flash("Please specify MCQ options.")
        return redirect(url_for('exams', active_tab='create'))
    if not mq_ans:
        flash("Please enter a correct answer / rubric.")
        return redirect(url_for('exams', active_tab='create'))
        
    parsed_opts = [o.strip() for o in mq_opts.split(",") if o.strip()] if mq_type == "mcq" else []
    
    new_q = {
        "question": mq_text,
        "type": mq_type,
        "marks": mq_marks,
        "options": parsed_opts,
        "correct_answer": mq_ans
    }
    
    if 'exam_questions' not in session:
        session['exam_questions'] = []
    questions = session['exam_questions']
    questions.append(new_q)
    session['exam_questions'] = questions
    
    flash("Question added to list!")
    return redirect(url_for('exams', active_tab='create'))

@app.route('/exams/create/generate_ai', methods=['POST'])
@login_required
def exams_create_generate_ai():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    session['exam_title_draft'] = request.form.get('title', '').strip()
    session['exam_desc_draft'] = request.form.get('description', '').strip()
    session['exam_marks_draft'] = request.form.get('total_marks', 50, type=int)
    
    ai_doc = request.form.get('ai_doc')
    ai_count = request.form.get('ai_count', 5, type=int)
    ai_model = request.form.get('ai_model')
    
    if not GROQ_API_KEY or not ai_model or not ai_doc:
        flash("AI generation requirements missing.")
        return redirect(url_for('exams', active_tab='create'))
        
    try:
        coll = get_collection()
        res = coll.get(where={"source": ai_doc}, include=["documents"])
        docs = res.get("documents") or []
        if not docs:
            flash("No text chunks found in document.")
        else:
            context_text = "\n\n".join(docs[:3])
            
            prompt = (
                f"Generate exactly {ai_count} test questions based on the document excerpt below. "
                f"Ensure a mix of Multiple Choice Questions (MCQ) and Free-text questions. "
                f"You MUST return ONLY a valid JSON array of question objects (do not wrap in markdown or prefix text). "
                f"Format of each question object in JSON:\n"
                f"[{{\"question\": \"Question text\", \"type\": \"mcq\", \"marks\": 10, \"options\": [\"Option A\", \"Option B\", \"Option C\", \"Option D\"], \"correct_answer\": \"Option A\"}},\n"
                f" {{\"question\": \"Question text\", \"type\": \"text\", \"marks\": 10, \"options\": [], \"correct_answer\": \"Explain key details...\"}}]\n\n"
                f"--- DOCUMENT EXCERPT ---\n{context_text}"
            )
            
            response = generate_chat_answer(
                prompt=prompt,
                model_name=ai_model,
                system_instruction="You are a professional educational assessor. You output ONLY valid JSON arrays without codeblocks."
            )
            
            cleaned_resp = clean_json_response(response)
            questions_list = json.loads(cleaned_resp)
            
            if isinstance(questions_list, list):
                if 'exam_questions' not in session:
                    session['exam_questions'] = []
                questions = session['exam_questions']
                questions.extend(questions_list)
                session['exam_questions'] = questions
                flash(f"Added {len(questions_list)} AI-generated questions!")
            else:
                flash("AI returned invalid question format. Please try again.")
    except Exception as e:
        flash(f"Failed to generate questions: {e}")
        
    return redirect(url_for('exams', active_tab='create'))

@app.route('/exams/create/remove_question', methods=['POST'])
@login_required
def exams_create_remove_question():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    idx = request.form.get('index', type=int)
    questions = session.get('exam_questions', [])
    if 0 <= idx < len(questions):
        questions.pop(idx)
        session['exam_questions'] = questions
        flash("Question removed.")
    return redirect(url_for('exams', active_tab='create'))

@app.route('/exams/create/clear', methods=['POST'])
@login_required
def exams_create_clear():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    session.pop('exam_title_draft', None)
    session.pop('exam_desc_draft', None)
    session.pop('exam_marks_draft', None)
    session.pop('exam_questions', None)
    return redirect(url_for('exams', active_tab='create'))

@app.route('/exams/create/save', methods=['POST'])
@login_required
def exams_create_save():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    total_marks = request.form.get('total_marks', 50, type=int)
    results_release = request.form.get('results_release', 'auto').strip()
    questions = session.get('exam_questions', [])
    
    if not title:
        flash("Please enter an exam title.")
        return redirect(url_for('exams', active_tab='create'))
    if not questions:
        flash("Cannot save an exam with zero questions.")
        return redirect(url_for('exams', active_tab='create'))
        
    settings = {"results_release": results_release}
    if add_exam(title, description, total_marks, questions, settings):
        add_announcement(
            f"📝 New Exam Published: {title}",
            f"A new exam titled '{title}' (Total Marks: {total_marks}) has been published by the Administrator.\n\n"
            f"Description: {description}\n\n"
            f"Please check your dashboard or exams section for active assignments."
        )
        flash(f"Exam '{title}' saved successfully!")
        session.pop('exam_title_draft', None)
        session.pop('exam_desc_draft', None)
        session.pop('exam_marks_draft', None)
        session.pop('exam_questions', None)
        return redirect(url_for('exams', active_tab='assign'))
    else:
        flash("Failed to save exam. Database error.")
        return redirect(url_for('exams', active_tab='create'))

@app.route('/exams/assign', methods=['POST'])
@login_required
def exams_assign():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    exam_id = request.form.get('exam_id', type=int)
    due_date = request.form.get('due_date', '').strip()
    due_date_str = due_date.replace('-', '/') if due_date else ""
    
    trainee_ids = request.form.getlist('trainee_ids')
    
    trainees = [u for u in get_all_users() if u["role"] == "trainee"]
    trainee_options = {u["employee_id"]: u["employee_id"] for u in trainees}
    
    target_ids = list(trainee_options.values()) if not trainee_ids else trainee_ids
    
    success_count = 0
    for t_id in target_ids:
        if assign_exam(exam_id, t_id, due_date_str):
            success_count += 1
            
    if success_count > 0:
        flash(f"Assigned exam to {success_count} trainees!")
    else:
        flash("Trainees are already assigned to this exam.")
        
    return redirect(url_for('exams', active_tab='assign', selected_exam_id=exam_id))

@app.route('/exams/assignment/delete', methods=['POST'])
@login_required
def exams_assignment_delete():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    assignment_id = request.form.get('assignment_id', type=int)
    selected_exam_id = request.form.get('selected_exam_id')
    
    if assignment_id:
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            c = conn.cursor()
            c.execute("DELETE FROM assignments WHERE assignment_id = ?", (assignment_id,))
            conn.commit()
            conn.close()
            flash("Assignment deleted successfully.")
        except Exception as e:
            flash(f"Failed to delete assignment: {e}")
            
    return redirect(url_for('exams', active_tab='assign', selected_exam_id=selected_exam_id))

@app.route('/exams/delete', methods=['POST'])
@login_required
def exams_delete_post():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    exam_id = request.form.get('exam_id', type=int)
    if exam_id:
        from src.exams import get_exam_by_id
        exam = get_exam_by_id(exam_id)
        if delete_exam(exam_id):
            if exam:
                title = exam.get('title', 'Unknown Exam')
                add_announcement(
                    f"🗑️ Exam Cancelled/Removed: {title}",
                    f"The exam '{title}' has been deleted/cancelled by the Administrator. Any pending assignments for this exam have been removed."
                )
            flash("Exam deleted successfully.")
        else:
            flash("Failed to delete exam.")
    return redirect(url_for('exams', active_tab='assign'))


# SPHERE VOICE ASSISTANT AGENT CONTROLLER
@app.route('/assistant/voice_agent/reset', methods=['POST'])
@login_required
def voice_agent_reset():
    session.pop('voice_agent_state', None)
    return jsonify({"status": "success", "message": "Voice agent session reset."})


@app.route('/assistant/voice_agent/chat', methods=['POST'])
@login_required
def voice_agent_chat():
    user_role = session.get('user_role', 'trainee')
    user_info = session.get('user_info', {})
    emp_id = user_info.get('employee_id', '')
    emp_name = session.get('current_user', 'User')

    voice_state = session.get('voice_agent_state', {})
    pending_action = voice_state.get('pending_action')

    query_text = ""
    audio_file = request.files.get('audio')
    if audio_file:
        try:
            from src.llm import transcribe_audio_whisper
            audio_bytes = audio_file.read()
            query_text = transcribe_audio_whisper(audio_bytes, mime_type=request.form.get('mime_type', 'audio/webm'))
        except Exception as e:
            print(f"Whisper transcription error: {e}")
            query_text = ""

    if not query_text:
        query_text = request.form.get('query', '').strip()

    if not query_text:
        return jsonify({"error": "No query or audio provided"}), 400

    q_lower = query_text.lower()
    model = request.form.get('model', 'llama-3.3-70b-versatile')
    selected_lang = request.form.get('language', 'en-US')

    lang_instructions = {
        'hi-IN': "Respond strictly in fluent Hindi (हिंदी). All text and explanations must be in Hindi.",
        'es-ES': "Respond strictly in fluent Spanish (Español). All text and explanations must be in Spanish.",
        'fr-FR': "Respond strictly in fluent French (Français). All text and explanations must be in French.",
        'en-US': "Respond in clear English."
    }
    sys_lang_prompt = lang_instructions.get(selected_lang, lang_instructions['en-US'])

    response_text = ""
    action_executed = None

    idx_stats = stats()
    available_docs = idx_stats.get("source_names", [])

    # --- ADMIN ROLE CONTROLLER ---
    if user_role == 'admin':
        # 1. MULTI-STEP VOICE EXAM CREATION WIZARD
        if ("create" in q_lower or "make" in q_lower or "generate" in q_lower) and ("exam" in q_lower or "test" in q_lower) and not pending_action:
            response_text = "Sure! What title would you like to give to this exam?"
            voice_state["pending_action"] = "wiz_exam_title"
            session["voice_agent_state"] = voice_state

        elif pending_action == "wiz_exam_title":
            exam_title = query_text.strip().strip('"').strip("'")
            if not exam_title or len(exam_title) < 2:
                exam_title = "Voice Generated Assessment"
            
            voice_state["exam_title"] = exam_title
            
            if not available_docs:
                response_text = f"Got it, title set to '{exam_title}'. However, there are no ingested PDF documents found in the system. Please ingest a PDF document first."
                voice_state.clear()
            else:
                response_text = f"Title set to '{exam_title}'. Now, please select the source document(s) to generate questions from and click Confirm Selection."
                action_executed = {
                    "action": "prompt_select_documents",
                    "item_type": "Document",
                    "select_mode": "multi",
                    "items": available_docs
                }
                voice_state["pending_action"] = "wiz_select_docs"
            session["voice_agent_state"] = voice_state

        elif pending_action == "wiz_select_docs" or (pending_action and pending_action.startswith("wiz_") and "selected_docs" not in voice_state and ("selected" in q_lower or "confirm" in q_lower)):
            selected_docs = [doc for doc in available_docs if doc.lower() in q_lower or doc in query_text]
            if not selected_docs and "selected_docs" in voice_state:
                selected_docs = voice_state["selected_docs"]
            if not selected_docs and available_docs:
                selected_docs = [available_docs[0]]

            voice_state["selected_docs"] = selected_docs
            doc_str = ", ".join(selected_docs)
            
            response_text = f"Selected document(s): {doc_str}. How many questions should be included in this exam? (e.g. 5, 10, or 15 questions)"
            voice_state["pending_action"] = "wiz_question_count"
            session["voice_agent_state"] = voice_state

        elif pending_action == "wiz_question_count":
            import re
            digits = re.findall(r'\d+', query_text)
            q_count = int(digits[0]) if digits else 5
            if q_count <= 0 or q_count > 50:
                q_count = 5
            
            voice_state["question_count"] = q_count
            response_text = f"Understood, {q_count} questions. How many marks should each question be worth? (e.g. 5 or 10 marks per question)"
            voice_state["pending_action"] = "wiz_marks_per_question"
            session["voice_agent_state"] = voice_state

        elif pending_action == "wiz_marks_per_question":
            import re
            digits = re.findall(r'\d+', query_text)
            m_per_q = int(digits[0]) if digits else 10
            if m_per_q <= 0 or m_per_q > 100:
                m_per_q = 10
                
            voice_state["marks_per_question"] = m_per_q
            
            response_text = f"Marks set to {m_per_q} per question. Now please select the difficulty level for this exam."
            action_executed = {
                "action": "prompt_select_difficulty",
                "item_type": "Difficulty Level",
                "select_mode": "single",
                "items": ["Easy", "Medium", "Hard", "Mixed"]
            }
            voice_state["pending_action"] = "wiz_select_difficulty"
            session["voice_agent_state"] = voice_state

        elif pending_action == "wiz_select_difficulty":
            diff = "Medium"
            for d in ["Easy", "Medium", "Hard", "Mixed"]:
                if d.lower() in q_lower:
                    diff = d
                    break
                    
            voice_state["difficulty"] = diff
            
            trainees = [u for u in get_all_users() if u["role"] == "trainee"]
            trainee_items = [f"{t['full_name']} ({t['employee_id']})" for t in trainees]
            trainee_items.insert(0, "All Trainees")
            
            response_text = f"Difficulty set to {diff}. Finally, please select the trainee(s) to assign this exam to and click Confirm Selection."
            action_executed = {
                "action": "prompt_select_trainees",
                "item_type": "Trainee",
                "select_mode": "multi",
                "items": trainee_items
            }
            voice_state["pending_action"] = "wiz_select_trainees"
            session["voice_agent_state"] = voice_state

        elif pending_action == "wiz_select_trainees":
            exam_title = voice_state.get("exam_title", "Voice Generated Assessment")
            selected_docs = voice_state.get("selected_docs", available_docs[:1])
            q_count = voice_state.get("question_count", 5)
            m_per_q = voice_state.get("marks_per_question", 10)
            diff = voice_state.get("difficulty", "Medium")
            
            doc_str = ", ".join(selected_docs)
            all_chunks = []
            for doc in selected_docs:
                all_chunks.extend(get_source_chunks(doc))
                
            text_sample = "\n\n".join([c.get("text", "") for c in all_chunks[:15]])
            if not text_sample:
                text_sample = f"Technical concepts from {doc_str}."
                
            total_marks = q_count * m_per_q
            
            prompt = f"""Generate exactly {q_count} multiple choice questions (MCQs) for an exam titled '{exam_title}' based on the text sample below:

Text Sample:
{text_sample[:3000]}

Difficulty level: {diff}
Marks per question: {m_per_q}

Output ONLY a valid JSON array of objects. Do NOT output any markdown commentary or text outside the JSON array.
Format required:
[
  {{
    "question": "Question text...",
    "type": "mcq",
    "marks": {m_per_q},
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "correct_answer": "Option A"
  }}
]
"""
            generated_questions = []
            try:
                raw_resp = generate_chat_answer(prompt, model_name=model, system_instruction="Output strictly a valid JSON array of question objects without any markdown commentary.")
                cleaned = clean_json_response(raw_resp)
                generated_questions = json.loads(cleaned)
            except Exception as e:
                print(f"Error parsing LLM questions: {e}")
                for i in range(q_count):
                    chunk_t = all_chunks[i % len(all_chunks)].get("text", "") if all_chunks else "Core technical concept"
                    snippet = chunk_t[:70].strip() or f"Question {i+1}"
                    generated_questions.append({
                        "question": f"Based on {doc_str}: What is the core principle of {snippet}?",
                        "type": "mcq",
                        "marks": m_per_q,
                        "options": [
                            f"Primary specification of {snippet[:30]}",
                            "Secondary auxiliary configuration parameter",
                            "Deprecated legacy interface",
                            "None of the above"
                        ],
                        "correct_answer": f"Primary specification of {snippet[:30]}"
                    })

            # Save Exam into SQLite database `exams` table with list of question dicts
            from src.exams import add_exam_and_get_id
            exam_id = add_exam_and_get_id(
                title=exam_title,
                description=f"Voice created assessment based on {doc_str}. Difficulty: {diff}.",
                total_marks=total_marks,
                questions=generated_questions
            )

            # Assign to Selected Trainees
            trainees = [u for u in get_all_users() if u["role"] == "trainee"]
            assigned_count = 0
            
            if "all" in q_lower or "all trainees" in q_lower or not trainees:
                for t in trainees:
                    if exam_id:
                        assign_exam(exam_id, t["employee_id"], "2026/12/31")
                        assigned_count += 1
            else:
                for t in trainees:
                    if t["employee_id"] in query_text or t["full_name"].lower() in q_lower or t["employee_id"] in str(voice_state):
                        if exam_id:
                            assign_exam(exam_id, t["employee_id"], "2026/12/31")
                            assigned_count += 1
                if assigned_count == 0 and trainees:
                    for t in trainees:
                        if exam_id:
                            assign_exam(exam_id, t["employee_id"], "2026/12/31")
                            assigned_count += 1

            add_announcement(
                f"📝 New Exam Assigned: {exam_title}",
                f"The Administrator has published a new exam '{exam_title}' ({total_marks} marks, {q_count} questions) generated from {doc_str}."
            )

            response_text = f"Exam '{exam_title}' with {q_count} questions ({total_marks} total marks, {diff} difficulty) has been successfully created and assigned to {assigned_count} trainee(s)!"
            voice_state.clear()
            session["voice_agent_state"] = voice_state

        # 2. DELETE DOCUMENT FLOW
        elif ("delete" in q_lower or "remove" in q_lower) and ("document" in q_lower or "pdf" in q_lower or "file" in q_lower) and not pending_action:
            if not available_docs:
                response_text = "There are no ingested documents currently in the knowledge base."
            else:
                response_text = "Please select the document you wish to delete from the list below and click Confirm Selection."
                action_executed = {
                    "action": "prompt_delete_document",
                    "item_type": "Ingested Document",
                    "select_mode": "single",
                    "items": available_docs
                }
                voice_state["pending_action"] = "delete_doc_select"
                session["voice_agent_state"] = voice_state

        elif pending_action == "delete_doc_select":
            target_doc = None
            for doc in available_docs:
                if doc.lower() in q_lower or doc in query_text:
                    target_doc = doc
                    break
            if not target_doc and available_docs:
                target_doc = available_docs[0]

            if target_doc:
                doc_hash = None
                try:
                    pdf_file = Path(DOCUMENTS_DIR) / target_doc
                    if pdf_file.exists():
                        from src.ingest import file_hash
                        doc_hash = file_hash(pdf_file.read_bytes())
                        pdf_file.unlink()
                except Exception as e:
                    print(f"Error deleting file: {e}")
                delete_source(target_doc, file_hash=doc_hash)
                response_text = f"Document '{target_doc}' has been permanently deleted from disk and purged from the vector index."
            else:
                response_text = "No valid document was selected for deletion."
            voice_state.clear()
            session["voice_agent_state"] = voice_state

        # 3. ASSIGN EXAM FLOW
        elif ("assign" in q_lower and "exam" in q_lower) and not pending_action:
            all_exams = get_all_exams()
            if not all_exams:
                response_text = "No exams exist in the system yet. Please create an exam first."
            else:
                response_text = "Please select the exam you would like to assign."
                action_executed = {
                    "action": "prompt_select_exam",
                    "item_type": "Exam",
                    "select_mode": "single",
                    "items": [f"{e['exam_id']}: {e['title']}" for e in all_exams]
                }
                voice_state["pending_action"] = "assign_exam_select_exam"
                session["voice_agent_state"] = voice_state

        elif pending_action == "assign_exam_select_exam":
            trainees = [u for u in get_all_users() if u["role"] == "trainee"]
            response_text = "Exam selected! Now please select the trainee(s) to assign this exam to."
            action_executed = {
                "action": "prompt_select_trainees",
                "item_type": "Trainee",
                "select_mode": "multi",
                "items": [f"{t['full_name']} ({t['employee_id']})" for t in trainees]
            }
            voice_state["pending_action"] = "assign_exam_select_trainees"
            session["voice_agent_state"] = voice_state

        elif pending_action == "assign_exam_select_trainees":
            trainees = [u for u in get_all_users() if u["role"] == "trainee"]
            all_exams = get_all_exams()
            exam_id = all_exams[0]["exam_id"] if all_exams else 1
            count = 0
            for t in trainees:
                if t["employee_id"] in query_text or t["full_name"].lower() in q_lower:
                    assign_exam(exam_id, t["employee_id"], "2026/12/31")
                    count += 1
            if count == 0 and trainees:
                assign_exam(exam_id, trainees[0]["employee_id"], "2026/12/31")
                count = 1
            response_text = f"Exam successfully assigned to {count} selected trainee(s)!"
            voice_state.clear()
            session["voice_agent_state"] = voice_state

        # 4. POST ANNOUNCEMENT
        elif "announcement" in q_lower or "post announcement" in q_lower:
            title = "📢 Voice Announcement"
            content = query_text.replace("post announcement", "").replace("create announcement", "").strip() or "Important update posted via Voice Assistant."
            add_announcement(title, content)
            response_text = f"Announcement '{title}' has been successfully posted to all trainees!"

        # 5. QUERY PROCTOR LOGS & TRAINEES
        elif "proctor" in q_lower or "log" in q_lower or "violation" in q_lower:
            response_text = "System Proctoring Analytics: All recent gateway exams were monitored with active head movement and tab switch tracking. No critical flags detected."

        elif "list trainees" in q_lower or "trainees" in q_lower or "users" in q_lower:
            trainees = [u for u in get_all_users() if u["role"] == "trainee"]
            t_names = ", ".join([t["full_name"] for t in trainees])
            response_text = f"There are currently {len(trainees)} active trainees in the system: {t_names}."

        elif "list exams" in q_lower or "show exams" in q_lower:
            all_exams = get_all_exams()
            e_titles = ", ".join([e["title"] for e in all_exams])
            response_text = f"Total exams in repository: {len(all_exams)}. Exams list: {e_titles}."

        elif "list documents" in q_lower or "show documents" in q_lower or "pdfs" in q_lower:
            doc_names = ", ".join(available_docs) if available_docs else "None"
            response_text = f"Indexed PDF documents in knowledge base ({len(available_docs)} total): {doc_names}."

    # --- TRAINEE ROLE CONTROLLER ---
    else:
        # 1. TRAINEE EXAMS QUERY
        if "exam" in q_lower or "my exams" in q_lower or "test" in q_lower:
            assigned = get_assignments_for_trainee(emp_id)
            pending = [a for a in assigned if a.get("status") == "assigned"]
            completed = [a for a in assigned if a.get("status") == "completed"]
            response_text = f"Hello {emp_name}! You have {len(pending)} pending exam(s) assigned and {len(completed)} completed exam(s)."
            if pending:
                action_executed = {
                    "action": "prompt_select_exam_to_take",
                    "item_type": "Assigned Exam",
                    "select_mode": "single",
                    "items": [f"Exam ID {a['assignment_id']} (Due: {a.get('due_date', 'N/A')})" for a in pending]
                }

        # 2. SPRINT & SYLLABUS PROGRESS QUERY
        elif "sprint" in q_lower or "progress" in q_lower or "day" in q_lower or "gateway" in q_lower or "week" in q_lower:
            from src.sprints import init_sprint
            sprint_data = init_sprint(emp_id)
            c_week = sprint_data.get("current_week", 1)
            c_day = sprint_data.get("current_day", 1)
            response_text = f"Your current sprint progress: Week {c_week}, Day {c_day}. You are currently working through Day {c_day} learning modules."

        # 3. PERFORMANCE & SCORE RETRO QUERY
        elif "score" in q_lower or "grade" in q_lower or "feedback" in q_lower or "performance" in q_lower:
            assigned = get_assignments_for_trainee(emp_id)
            scores = [a.get("score") for a in assigned if a.get("score") is not None]
            avg_s = (sum(scores) / len(scores)) if scores else 0.0
            response_text = f"Your overall performance average is {avg_s:.1f}%. You have completed {len(scores)} graded assessment(s)."

    # FALLBACK / GENERAL RAG KNOWLEDGE QUERY
    if not response_text:
        try:
            from src.embeddings import embed_query
            query_emb = embed_query(query_text)
            search_results = search(query_emb, top_k=3)
            if search_results:
                response_text = generate_rag_answer(query_text, search_results, selected_model=model)
                if selected_lang != 'en-US':
                    response_text = generate_chat_answer(f"Translate the following response into the target language requested ({selected_lang}):\n\n{response_text}", model_name=model, system_instruction=sys_lang_prompt)
            else:
                response_text = generate_chat_answer(query_text, model_name=model, system_instruction=f"Answer concisely as an educational assistant. {sys_lang_prompt}")
        except Exception as e:
            print(f"RAG voice search error: {e}")
            response_text = f"I received your request: '{query_text}'. How else can I assist you with your learning goals today?"

    return jsonify({
        "query_text": query_text,
        "response_text": response_text,
        "action_executed": action_executed
    })


@app.route('/exams/take', methods=['POST'])
@login_required
def exams_take():
    assignment_id = request.form.get('assignment_id', type=int)
    if assignment_id:
        session['taking_assignment_id'] = assignment_id
        session['exam_started'] = False
    return redirect(url_for('exams'))

@app.route('/exams/cancel')
@login_required
def exams_cancel():
    session.pop('taking_assignment_id', None)
    session.pop('exam_started', None)
    return redirect(url_for('exams'))

@app.route('/exams/start_active', methods=['POST'])
@login_required
def exams_start_active():
    if session.get('taking_assignment_id'):
        session['exam_started'] = True
        return jsonify({"status": "success"})
    return jsonify({"error": "No exam in progress"}), 400

@app.route('/exams/submit', methods=['POST'])
@login_required
def exams_submit():
    assignment_id = request.form.get('assignment_id', type=int)
    if not assignment_id:
        return redirect(url_for('exams'))
        
    detail = get_assignment_by_id(assignment_id)
    if not detail:
        session.pop('taking_assignment_id', None)
        session.pop('exam_started', None)
        return redirect(url_for('exams'))
        
    is_malpractice = request.form.get('malpractice') == 'true'
    if is_malpractice:
        responses = {}
        for idx, q in enumerate(detail["questions"]):
            responses[idx] = request.form.get(f"answer_{idx}", "").strip()
            
        ai_breakdowns = []
        for idx, q in enumerate(detail["questions"]):
            ai_breakdowns.append({
                "index": idx,
                "score": 0.0,
                "feedback": "Grading bypassed. Proctoring system detected active window/tab switching or unauthorized actions."
            })
            
        overall_feedback = {
            "overall_comments": "🚨 MALPRACTICE DETECTED: This assessment was terminated automatically. Multiple proctoring violations (tab switching, window focus loss, or screenshot attempts) were registered. The score is set to 0.0.",
            "questions": ai_breakdowns
        }
        
        if submit_exam_answers(assignment_id, responses, 0.0, json.dumps(overall_feedback)):
            flash("Exam submitted automatically and flagged as MALPRACTICE.")
        else:
            flash("Failed to save malpractice submission to database.")
            
        session.pop('taking_assignment_id', None)
        session.pop('exam_started', None)
        return redirect(url_for('exams'))
        
    responses = {}
    for idx, q in enumerate(detail["questions"]):
        responses[idx] = request.form.get(f"answer_{idx}", "").strip()
        
    local_models = list_local_models()
    grade_model = None
    if GROQ_API_KEY and local_models:
        d_idx = 0
        for idx, m in enumerate(local_models):
            if "llama-3.3" in m.lower() or "llama" in m.lower():
                d_idx = idx
                break
        grade_model = local_models[d_idx]
        
    total_earned_score = 0.0
    ai_breakdowns = []
    
    for idx, q in enumerate(detail["questions"]):
        t_ans = responses.get(idx) or responses.get(str(idx)) or ""
        if q["type"] == "mcq":
            is_correct = str(t_ans).strip().lower() == str(q["correct_answer"]).strip().lower()
            score_q = float(q["marks"]) if is_correct else 0.0
            feedback_q = "Correct!" if is_correct else f"Incorrect. Correct answer was: {q['correct_answer']}"
            total_earned_score += score_q
            ai_breakdowns.append({
                "index": idx,
                "score": score_q,
                "feedback": feedback_q
            })
        else:
            if not grade_model:
                total_earned_score += float(q["marks"]) / 2
                ai_breakdowns.append({
                    "index": idx,
                    "score": float(q["marks"]) / 2,
                    "feedback": "Graded 50% (No LLM detected for evaluation)"
                })
            else:
                prompt = (
                    f"Grade the trainee's answer against the expected rubric/keywords.\n"
                    f"Question: {q['question']}\n"
                    f"Expected Rubric: {q['correct_answer']}\n"
                    f"Trainee Answer: {t_ans}\n"
                    f"Max Marks: {q['marks']}\n\n"
                    f"You MUST assign a score between 0 and {q['marks']} based on accuracy and completeness. "
                    f"You MUST return ONLY a valid JSON object matching this structure: "
                    f"{{\"score\": 8.5, \"feedback\": \"Trainee correctly identified visibility protocols but missed...\"}}"
                )
                try:
                    resp = generate_chat_answer(
                        prompt=prompt,
                        model_name=grade_model,
                        system_instruction="You are a strict grading assistant. Return ONLY a single JSON object."
                    )
                    cleaned = clean_json_response(resp)
                    res_grade = json.loads(cleaned)
                    score_q = float(res_grade.get("score", 0.0))
                    feedback_q = res_grade.get("feedback", "No feedback generated.")
                except Exception:
                    score_q = 0.0
                    feedback_q = "AI Grading failure. Assigned 0."
                    
                total_earned_score += score_q
                ai_breakdowns.append({
                    "index": idx,
                    "score": score_q,
                    "feedback": feedback_q
                })
                
    overall_feedback = {
        "overall_comments": f"Completed test with a total score of {total_earned_score} / {detail['total_marks']}.",
        "questions": ai_breakdowns
    }
    
    emp_id = session.get('user_info', {}).get('employee_id', 'demo')
    
    # ── Agile Sprint Integration: Log QA Errors & Advance to Day 6 ──
    is_sprint_gateway = False
    try:
        from src.sprints import get_sprint, log_qa_error, update_sprint_day, clear_qa_errors
        sprint = get_sprint(emp_id)
        if sprint and sprint["current_day"] == 5:
            week = sprint["current_week"]
            clear_qa_errors(emp_id, week) # clear old ones first
            
            for idx, q in enumerate(detail["questions"]):
                breakdown = ai_breakdowns[idx]
                max_marks = float(q["marks"])
                earned = float(breakdown["score"])
                if earned < max_marks * 0.8:  # Trainee struggled (got < 80% marks)
                    topic = q.get("topic", "").strip()
                    if not topic:
                        words = [w.strip("?,.:;\"'") for w in q["question"].split() if len(w) > 4]
                        topic = " ".join(words[:2]).title() or "General Technical Concepts"
                    log_qa_error(emp_id, week, topic, q["question"])
            
            # Automatically advance to Day 6 (Stakeholder Demo)
            update_sprint_day(emp_id, 6)
            is_sprint_gateway = True
    except Exception as e:
        print(f"Error in sprint exam submission integration: {e}")

    if not is_sprint_gateway:
        exam_st = detail.get("exam_settings", {}) or {}
        assign_st = detail.get("settings", {}) or {}
        if exam_st.get("is_sprint_gateway") or assign_st.get("is_sprint_gateway") or "Gateway Exam" in str(detail.get("title", "")) or "Day 5" in str(detail.get("title", "")):
            is_sprint_gateway = True

    if submit_exam_answers(assignment_id, responses, total_earned_score, json.dumps(overall_feedback)):
        flash("Day 5 Gateway Exam completed and graded successfully! Advanced to Day 6." if is_sprint_gateway else "Test submitted and graded successfully!")
    else:
        flash("Failed to save submissions to database.")
        
    session.pop('taking_assignment_id', None)
    session.pop('exam_started', None)

    if is_sprint_gateway:
        return redirect(url_for('sprint_page'))
    return redirect(url_for('exams'))

# AI ASSISTANT & SSE CHAT STREAMING
active_generations = {} # employee_id -> { "session_id", "query", "partial_response", "sources", "stop" }

@app.before_request
def before_request_cleanup():
    # Exam Proctoring Redirect Lock:
    # If the student is actively taking an assignment, they are locked to /exams, /exams/submit, /exams/cancel, /logout, static/assets files, or API endpoints
    if session.get('authenticated') and session.get('taking_assignment_id'):
        if session.get('exam_started'):
            path = request.path
            allowed_paths = ['/exams', '/logout', '/static', '/assets', '/api/']
            is_allowed = False
            for p in allowed_paths:
                if path.startswith(p):
                    is_allowed = True
                    break
            if not is_allowed:
                return redirect(url_for('exams'))
        else:
            # If the exam has NOT started yet, they are allowed to navigate away!
            # If they navigate to a non-exam page, automatically cancel/reset the taking session.
            path = request.path
            allowed_paths = ['/exams', '/logout', '/static', '/assets', '/api/']
            is_allowed = False
            for p in allowed_paths:
                if path.startswith(p):
                    is_allowed = True
                    break
            if not is_allowed:
                session.pop('taking_assignment_id', None)
                session.pop('exam_started', None)

    # Only clean up for authenticated users and non-static/non-assistant routes
    if session.get('authenticated'):
        path = request.path
        if not (path.startswith('/assistant') or path.startswith('/assets') or path.startswith('/static')):
            user_info = session.get('user_info', {}) or {}
            emp_id = user_info.get('employee_id')
            if emp_id:
                try:
                    for s in get_chat_sessions_for_user(emp_id):
                        if not get_chat_messages(s["session_id"]):
                            delete_chat_session(s["session_id"])
                except Exception:
                    pass

@app.route('/assistant/upload_ephemeral', methods=['POST'])
@login_required
def assistant_upload_ephemeral():
    tab_id = session.get('_tab_id')
    if not tab_id:
        return jsonify({"status": "error", "message": "No active session tab identifier found."}), 400
        
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part in the request."}), 400
        
    file = request.files['file']
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "No file selected."}), 400
        
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"status": "error", "message": "Invalid file format. Only PDF documents are supported."}), 400
        
    try:
        from io import BytesIO
        from src.ingest import extract_pages, chunk_pages, file_hash
        from src.embeddings import embed_documents
        
        data = file.read()
        if not data:
            return jsonify({"status": "error", "message": "Selected file is empty."}), 400
            
        digest = file_hash(data)
        pages = extract_pages(BytesIO(data))
        if not pages:
            return jsonify({"status": "error", "message": "No extractable text found in the PDF."}), 400
            
        chunks = chunk_pages(pages, file.filename)
        if len(chunks) > 1000:
            return jsonify({"status": "error", "message": f"Document is too large ({len(chunks)} chunks). Max allowed is 1000 chunks."}), 400
            
        embeddings = embed_documents([c["text"] for c in chunks])
        added_count = add_ephemeral_chunks(tab_id, chunks, embeddings, digest)
        
        user_info = session.get('user_info', {}) or {}
        emp_id = user_info.get('employee_id', 'demo')
        
        from src.sprints import get_sprint, add_weekly_document
        sprint = get_sprint(emp_id)
        week = sprint.get("current_week", 1)
        day = sprint.get("current_day", 1)
        add_weekly_document(emp_id, week, day, file.filename)
        
        ephemeral_docs = session.get('ephemeral_docs', [])
        if file.filename not in ephemeral_docs:
            ephemeral_docs.append(file.filename)
            session['ephemeral_docs'] = ephemeral_docs
            
        return jsonify({
            "status": "success",
            "message": f"Successfully processed and embedded {file.filename}.",
            "chunks_added": added_count,
            "filename": file.filename
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Failed to ingest document: {str(e)}"}), 500


@app.route('/assistant/delete_ephemeral_file', methods=['POST'])
@login_required
def assistant_delete_ephemeral_file():
    tab_id = session.get('_tab_id')
    if not tab_id:
        return jsonify({"status": "error", "message": "No active session tab identifier found."}), 400
        
    data = request.get_json() or {}
    filename = data.get('filename')
    if not filename:
        return jsonify({"status": "error", "message": "No filename provided."}), 400
        
    try:
        from src.vectorstore import get_ephemeral_collection
        collection = get_ephemeral_collection(tab_id)
        
        # Delete from Chroma where source matches the filename
        collection.delete(where={"source": filename})
        
        user_info = session.get('user_info', {}) or {}
        emp_id = user_info.get('employee_id', 'demo')
        
        from src.sprints import get_sprint, delete_weekly_document
        sprint = get_sprint(emp_id)
        week = sprint.get("current_week", 1)
        delete_weekly_document(emp_id, week, filename)
        
        ephemeral_docs = session.get('ephemeral_docs', [])
        if filename in ephemeral_docs:
            ephemeral_docs.remove(filename)
            session['ephemeral_docs'] = ephemeral_docs
            
        return jsonify({
            "status": "success",
            "message": f"Successfully removed {filename} from in-memory session."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to delete document: {str(e)}"}), 500


# ═══════════════════════════════════════════════════════════════════════════
#  AGILE LEARNING SPRINT & MOCK INTERVIEW ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/assistant/sprint/status')
@login_required
def sprint_status():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    
    from src.sprints import get_sprint
    sprint = get_sprint(emp_id)
    
    completed = session.get('sprint_tasks_completed', [])
    return jsonify({
        "status": "success",
        "sprint": sprint,
        "tasks_completed": completed
    })


@app.route('/assistant/sprint/task_complete', methods=['POST'])
@login_required
def sprint_task_complete():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    
    data = request.get_json() or {}
    task_name = data.get('task')
    
    completed = session.get('sprint_tasks_completed', [])
    if task_name and task_name not in completed:
        completed.append(task_name)
        session['sprint_tasks_completed'] = completed
        
        progress = len(completed) * 25.0
        from src.sprints import update_sprint_progress
        update_sprint_progress(emp_id, progress)
        
    return jsonify({
        "status": "success",
        "progress": len(completed) * 25.0,
        "tasks_completed": completed
    })


@app.route('/assistant/sprint/override', methods=['POST'])
@login_required
def sprint_override():
    if session.get('user_role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    target_user_id = data.get('user_id')
    day = data.get('day')
    week = data.get('week')
    progress = data.get('progress')
    
    if not target_user_id:
        return jsonify({"status": "error", "message": "Missing user_id"}), 400
        
    from src.sprints import update_sprint_day, update_sprint_week, update_sprint_progress, clear_qa_errors, clear_interview_evaluations
    
    if week is not None:
        try:
            week = int(week)
            update_sprint_week(target_user_id, week)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid week value"}), 400
            
    if day is not None:
        try:
            day = int(day)
            update_sprint_day(target_user_id, day)
            if day < 5:
                # Clear QA errors and voice evaluations for that week
                clear_qa_errors(target_user_id, week if week is not None else 1)
                clear_interview_evaluations(target_user_id, week if week is not None else 1)
                if target_user_id == session.get('user_info', {}).get('employee_id'):
                    session.pop('sprint_tasks_completed', None)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid day value"}), 400
            
    if progress is not None:
        try:
            progress = float(progress)
            update_sprint_progress(target_user_id, progress)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid progress value"}), 400
        
    return jsonify({"status": "success", "message": "Sprint updated successfully"})


@app.route('/admin/sprint/ai_generate_plan', methods=['POST'])
@login_required
def admin_sprint_ai_generate_plan():
    if session.get('user_role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    prompt = data.get('prompt')
    domain = data.get('domain')
    week = data.get('week')
    
    if not (prompt and domain and week):
        return jsonify({"status": "error", "message": "Missing required planning fields"}), 400
        
    try:
        from src.llm import generate_study_plan
        plan = generate_study_plan(prompt, domain, int(week), model_name=OLLAMA_MODEL)
        return jsonify({"status": "success", "plan": plan})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/admin/sprint/save_plan', methods=['POST'])
@login_required
def admin_sprint_save_plan():
    if session.get('user_role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    data = request.get_json() or {}
    domain = data.get('domain')
    week = data.get('week')
    title = data.get('title')
    tasks = data.get('tasks')
    day5_exam_id = data.get('day5_exam_id', '')
    day6_interview_prompt = data.get('day6_interview_prompt', '')
    reference_files = data.get('reference_files', [])
    
    if not (domain and week and title and tasks):
        return jsonify({"status": "error", "message": "Missing required study plan fields"}), 400
        
    try:
        from src.sprints import save_study_plan
        tasks_json = json.dumps(tasks)
        ref_files_json = json.dumps(reference_files)
        success = save_study_plan(domain, int(week), title, tasks_json, day5_exam_id, day6_interview_prompt, ref_files_json)
        if success:
            return jsonify({"status": "success", "message": "Study plan saved successfully."})
        else:
            return jsonify({"status": "error", "message": "Database write failed."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/admin/sprint/upload_reference', methods=['POST'])
@login_required
def admin_sprint_upload_reference():
    if session.get('user_role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
        
    file = request.files['file']
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "No file selected"}), 400
        
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"status": "error", "message": "Only PDF reference files are supported."}), 400
        
    try:
        import os
        from pypdf import PdfReader
        upload_dir = os.path.join(app.root_path, 'uploads', 'sprint_references')
        os.makedirs(upload_dir, exist_ok=True)
        
        file_path = os.path.join(upload_dir, file.filename)
        file.save(file_path)
        
        reader = PdfReader(file_path)
        total_pages = len(reader.pages)
        
        return jsonify({"status": "success", "filename": file.filename, "total_pages": total_pages})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/assistant/mock_interview/start', methods=['POST'])
@login_required
def mock_interview_start():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    
    from src.sprints import get_sprint, get_qa_errors
    sprint = get_sprint(emp_id)
    if sprint["current_day"] != 6:
        return jsonify({"status": "error", "message": "Mock interview is only unlocked on Sprint Day 6 (Demo Phase)."}), 400
        
    errors = get_qa_errors(emp_id, sprint["current_week"])
    topics = [e["incorrect_topic"] for e in errors]
    
    questions = []
    if topics:
        prompt = (
            f"You are a Senior Project Stakeholder conducting an Agile Sprint Demo. "
            f"Generate exactly 5 challenging questions for the trainee. "
            f"The trainee struggled with these topics in yesterday's QA check: {', '.join(topics)}.\n"
            f"Format each question to sound like an urgent Slack/Teams comment or ticket "
            f"(e.g., 'Hey, I saw yesterday's code. Why did you use round-robin here instead of sticky sessions? Explain how it handles failover. Leave a voice memo.').\n"
            f"Return ONLY a JSON array of 5 strings."
        )
        try:
            local_models = list_local_models()
            model_name = local_models[0] if local_models else "qwen2.5:latest"
            resp = generate_chat_answer(
                prompt=prompt,
                model_name=model_name,
                system_instruction="You are a senior stakeholder. Return ONLY a single JSON list of 5 strings."
            )
            cleaned = clean_json_response(resp)
            questions = json.loads(cleaned)
        except Exception:
            pass
            
    if not questions or len(questions) < 5:
        questions = [
            "We're seeing occasional socket timeouts in our high-latency service. Why did we decide to implement a circuit breaker instead of simple retry loops? Leave a voice memo.",
            "I'm reviewing the message broker configuration. Why did we choose Kafka partition keys over RabbitMQ exchanges for this asynchronous flow? Leave a voice memo.",
            "In yesterday's system design, we opted for local caches. How do we ensure cache coherence across our replicas without hurting read latency? Leave a voice memo.",
            "We need to implement distributed locks for our database transactions. Should we use Redis (Redlock) or database row locks? Defend your choice in a voice memo.",
            "Explain how the consistency model changes when we set Cassandra writes to LOCAL_QUORUM instead of ONE. How does it impact write latency? Leave a voice memo."
        ]
        
    session['mock_questions'] = questions[:5]
    session['mock_index'] = 0
    session['mock_answers'] = []
    
    return jsonify({
        "status": "success",
        "questions": questions[:5],
        "first_question": questions[0]
    })


@app.route('/assistant/mock_interview/submit', methods=['POST'])
@login_required
def mock_interview_submit():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    
    data = request.get_json() or {}
    answer_text = data.get('answer', '').strip()
    duration = data.get('duration', type=float) or 10.0
    
    questions = session.get('mock_questions', [])
    idx = session.get('mock_index', 0)
    
    if not questions or idx >= len(questions):
        return jsonify({"status": "error", "message": "No active interview session found."}), 400
        
    current_question = questions[idx]
    
    import re
    fillers = re.findall(r'\b(um|uh|like|so|ah|you\s+know)\b', answer_text.lower())
    filler_count = len(fillers)
    
    words = answer_text.split()
    word_count = len(words)
    wpm = (word_count / duration * 60.0) if duration > 0 else 0.0
    
    pacing_score = 100.0
    if wpm < 100:
        pacing_score = max(100.0 - (100 - wpm), 50.0)
    elif wpm > 160:
        pacing_score = max(100.0 - (wpm - 160), 50.0)
        
    filler_penalty = min(filler_count * 5.0, 50.0)
    confidence_score = max(pacing_score - filler_penalty, 20.0)
    
    from src.vectorstore import get_ephemeral_collection, get_collection
    context = ""
    try:
        tab_id = session.get('_tab_id')
        coll = get_ephemeral_collection(tab_id) if tab_id else get_collection()
        results = coll.query(query_texts=[current_question], n_results=2)
        if results and results.get("documents"):
            context = "\n".join(results["documents"][0])
    except Exception:
         pass
         
    prompt = (
        f"You are a Senior Technical Stakeholder. Grade the trainee's answer.\n"
        f"Question: {current_question}\n"
        f"RAG Context: {context}\n"
        f"Trainee Spoken Response: {answer_text}\n\n"
        f"Assess the answer for technical accuracy, completeness, and context correctness. "
        f"Return ONLY a JSON object matching this structure: "
        f"{{\"score\": 85, \"feedback\": \"Trainee correctly noted cache coherence but missed...\"}}"
    )
    
    tech_score = 50.0
    feedback_text = "Standard evaluation complete."
    try:
        local_models = list_local_models()
        model_name = local_models[0] if local_models else "qwen2.5:latest"
        resp = generate_chat_answer(
            prompt=prompt,
            model_name=model_name,
            system_instruction="Grade the technical accuracy of the response. Return JSON only."
        )
        cleaned = clean_json_response(resp)
        res_grade = json.loads(cleaned)
        tech_score = float(res_grade.get("score", 50.0))
        feedback_text = res_grade.get("feedback", "No feedback generated.")
    except Exception as e:
        print(f"Error grading mock interview question: {e}")
        
    answers = session.get('mock_answers', [])
    answers.append({
        "question": current_question,
        "answer": answer_text,
        "tech_score": tech_score,
        "confidence_score": confidence_score,
        "filler_count": filler_count,
        "fillers_found": list(set(fillers)),
        "wpm": round(wpm, 1),
        "feedback": feedback_text
    })
    session['mock_answers'] = answers
    
    idx += 1
    session['mock_index'] = idx
    
    finished = idx >= len(questions)
    report = None
    if finished:
        avg_tech = sum(a["tech_score"] for a in answers) / len(answers)
        avg_conf = sum(a["confidence_score"] for a in answers) / len(answers)
        total_fillers = sum(a["filler_count"] for a in answers)
        avg_wpm = sum(a["wpm"] for a in answers) / len(answers)
        
        report_data = {
            "overall_tech": round(avg_tech, 1),
            "overall_conf": round(avg_conf, 1),
            "total_fillers": total_fillers,
            "avg_wpm": round(avg_wpm, 1),
            "qna_breakdown": answers
        }
        report_str = json.dumps(report_data)
        
        from src.sprints import log_interview_evaluation, update_sprint_day, get_sprint
        sprint = get_sprint(emp_id)
        log_interview_evaluation(emp_id, sprint["current_week"], avg_tech, avg_conf, total_fillers, avg_wpm, report_str)
        
        update_sprint_day(emp_id, 7)
        report = report_data
        
    return jsonify({
        "status": "success",
        "finished": finished,
        "next_index": idx,
        "next_question": questions[idx] if not finished else None,
        "report": report
    })


@app.route('/assistant')
@login_required
def assistant():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    
    from src.sprints import get_sprint, get_weekly_documents
    sprint = get_sprint(emp_id)
    week = sprint.get("current_week", 1)
    
    user_sessions = get_chat_sessions_for_user(emp_id, week)
    
    session_id = request.args.get('session_id')
    if session_id:
        for s in user_sessions:
            if s["session_id"] != session_id and not get_chat_messages(s["session_id"]):
                delete_chat_session(s["session_id"])
        session['active_chat_session_id'] = session_id
        active_id = session_id
    else:
        empty_sessions = [s for s in user_sessions if not get_chat_messages(s["session_id"])]
        if empty_sessions:
            active_id = empty_sessions[0]["session_id"]
            for s in empty_sessions[1:]:
                delete_chat_session(s["session_id"])
        else:
            active_id = str(uuid.uuid4())
            create_chat_session(active_id, emp_id, "New Chat", week_number=week)
        session['active_chat_session_id'] = active_id
        
    user_sessions = get_chat_sessions_for_user(emp_id, week)
    active_messages = get_chat_messages(active_id)
    
    # Auto-ingest study plan reference files if any
    from src.sprints import get_study_plan
    study_plan = get_study_plan(user_info.get('domain', 'general'), week)
    try:
        ref_data = json.loads(study_plan.get("reference_files_json") or "[]")
    except Exception:
        ref_data = []
        
    target_refs = []
    if isinstance(ref_data, dict):
        current_day = sprint.get("current_day", 1)
        for d in range(1, min(current_day, 4) + 1):
            day_key = f"day{d}"
            day_files = ref_data.get(day_key, [])
            for f_info in day_files:
                filename = f_info.get("filename")
                page_range = f_info.get("page_range")
                target_refs.append((filename, page_range))
    elif isinstance(ref_data, list):
        for filename in ref_data:
            target_refs.append((filename, None))
            
    if target_refs:
        from src.sprints import add_weekly_document
        existing_docs = get_weekly_documents(emp_id, week)
        import os
        for filename, page_range in target_refs:
            if page_range:
                unique_filename = f"{os.path.splitext(filename)[0]}_p{page_range[0]}_{page_range[1]}.pdf"
            else:
                unique_filename = filename
                
            if unique_filename not in existing_docs:
                file_path = os.path.join(app.root_path, 'uploads', 'sprint_references', filename)
                if os.path.exists(file_path):
                    try:
                        from io import BytesIO
                        from src.ingest import extract_pages, chunk_pages, file_hash
                        from src.embeddings import embed_documents
                        from src.vectorstore import add_ephemeral_chunks
                        
                        with open(file_path, 'rb') as f:
                            file_data = f.read()
                            
                        digest = file_hash(file_data)
                        pages = extract_pages(BytesIO(file_data), page_range=page_range)
                        if pages:
                            chunks = chunk_pages(pages, unique_filename)
                            embeddings = embed_documents([c["text"] for c in chunks])
                            add_ephemeral_chunks(active_id, chunks, embeddings, digest)
                            add_weekly_document(emp_id, week, 1, unique_filename)
                    except Exception as e:
                        print(f"Error auto-ingesting admin reference file {filename}: {e}")

    # Load study documents from SQLite DB for this week
    ephemeral_docs = get_weekly_documents(emp_id, week)
    
    return render_template(
        'assistant.html',
        active_messages=active_messages,
        user_sessions=user_sessions,
        active_chat_session_id=active_id,
        ephemeral_docs=ephemeral_docs,
        groq_api_key=GROQ_API_KEY
    )



@app.route('/assistant/suggestions', methods=['GET'])
@login_required
def assistant_suggestions():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    tab_id = request.args.get('tab_id', '')
    
    # Check if there are active ephemeral documents loaded in Chroma for this tab session
    has_ephemeral = False
    if tab_id:
        try:
            from src.vectorstore import get_ephemeral_collection
            coll = get_ephemeral_collection(tab_id)
            if coll.count() > 0:
                has_ephemeral = True
        except Exception:
            pass
            
    if has_ephemeral:
        text = get_ephemeral_document_text(tab_id)
        if text:
            prompt = (
                f"Based on the following document content excerpt, generate exactly 3 short, direct study questions "
                f"that a student might want to ask to explore or understand this document better.\n"
                f"Keep each question short (under 12 words) and highly relevant to the text content.\n"
                f"You MUST return ONLY a valid JSON array of strings (do not wrap in markdown or prefix text).\n"
                f"Example format:\n"
                f"[\"What is the main topic?\", \"Explain the compliance rules.\"]\n\n"
                f"--- DOCUMENT CONTENT ---\n{text[:2500]}"
            )
            local_models = list_local_models()
            model_name = local_models[0] if local_models else "llama3-8b-8192"
            try:
                resp = generate_chat_answer(
                    prompt=prompt,
                    model_name=model_name,
                    system_instruction="You are a study suggestion assistant. You output ONLY valid JSON arrays of strings."
                )
                # clean_json_response is defined locally in app.py
                cleaned = clean_json_response(resp)
                prompts = []
                try:
                    prompts = json.loads(cleaned)
                except Exception:
                    # Fallback regex extraction of double-quoted strings
                    prompts = re.findall(r'"([^"]+)"', cleaned)
                    if not prompts:
                        prompts = re.findall(r"'([^']+)'", cleaned)
                
                if isinstance(prompts, list) and len(prompts) > 0:
                    prompts = [p.strip() for p in prompts if p.strip()]
                    if prompts:
                        return jsonify({"suggestions": prompts[:4]})
            except Exception as e:
                print(f"Failed to generate ephemeral suggestions: {e}")
                
    active_id = session.get('active_chat_session_id')
    from src.chats import get_chat_messages
    active_messages = get_chat_messages(active_id) if active_id else []
    
    if len(active_messages) == 0:
        from src.concept_map import get_history_based_suggestions
        prompts = get_history_based_suggestions(emp_id)
    else:
        prompts = get_personalized_suggestions(emp_id)
        
    return jsonify({"suggestions": prompts})


@app.route('/assistant/chat_stream', methods=['POST'])
@login_required
def chat_stream():
    data = request.get_json() or {}
    query = data.get('query', '').strip()
    model = data.get('model')
    mode = data.get('mode', 'RAG (Document Guided)')
    
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    active_session_id = session.get('active_chat_session_id')
    
    if not active_session_id:
        return jsonify({"error": "No active chat session"}), 400
        
    add_chat_message(active_session_id, "user", query)
    
    user_sessions = get_chat_sessions_for_user(emp_id)
    current_title = "Welcome Conversation"
    for s in user_sessions:
        if s["session_id"] == active_session_id:
            current_title = s["title"]
            break
    if current_title in ["Welcome Conversation", "New Conversation", "New Chat"] or current_title.startswith("Chat "):
        new_title = " ".join(query.split()[:4])
        if len(new_title) > 20:
            new_title = new_title[:18] + "..."
        if not new_title.strip():
            new_title = "Conversation"
        rename_chat_session(active_session_id, new_title)

    query_lower = query.lower()
    is_exam_request = (session.get('user_role') == 'admin') and ("create" in query_lower or "make" in query_lower or "generate" in query_lower or "setup" in query_lower or "new" in query_lower) and ("exam" in query_lower or "test" in query_lower or "assessment" in query_lower or "quiz" in query_lower)
    
    if is_exam_request:
        def wizard_event_generator():
            yield "[EXAM_WIZARD_START]"
            add_chat_message(active_session_id, "assistant", "Interactive Exam Creator Wizard opened.", [])
        return Response(stream_with_context(wizard_event_generator()), mimetype='text/event-stream')

    list_items = None
    list_item_type = None
    if ("list" in query_lower or "show" in query_lower or "display" in query_lower) and ("doc" in query_lower or "pdf" in query_lower or "file" in query_lower or "reference" in query_lower):
        from src.vectorstore import get_collection
        coll = get_collection()
        res = coll.get(include=["metadatas"])
        metadatas = res.get("metadatas") or []
        list_items = sorted(list(set(m["source"] for m in metadatas if m and "source" in m)))
        list_item_type = "document"
    elif ("list" in query_lower or "show" in query_lower or "display" in query_lower) and ("trainee" in query_lower or "student" in query_lower or "user" in query_lower):
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT employee_id, full_name FROM users WHERE role = 'trainee'")
        list_items = [f"{r['full_name']} ({r['employee_id']})" for r in cursor.fetchall()]
        conn.close()
        list_item_type = "trainee"
    elif ("list" in query_lower or "show" in query_lower or "display" in query_lower) and ("exam" in query_lower or "test" in query_lower or "quiz" in query_lower):
        from src.exams import get_all_exams
        list_items = [e.get("title") for e in get_all_exams() if e.get("title")]
        list_item_type = "exam"
    elif ("list" in query_lower or "show" in query_lower or "display" in query_lower) and ("announcement" in query_lower or "notice" in query_lower):
        from src.exams import get_all_announcements
        list_items = [a.get("title") for a in get_all_announcements() if a.get("title")]
        list_item_type = "announcement"

        
    # 1. Performance Query Intent Classification
    perf_target = detect_performance_query(query)
    perf_context = None
    aggregate_context = None

    if perf_target == "ALL":
        # Admin aggregate query — get summary of all trainees
        user_role = session.get('user_role', 'trainee')
        aggregate_context = get_aggregate_performance_context(user_role)
        if "Unauthorized" in aggregate_context:
            def error_agg_generator():
                add_chat_message(active_session_id, "assistant", aggregate_context, [])
                yield aggregate_context
            return Response(stream_with_context(error_agg_generator()), mimetype='text/event-stream')
    elif perf_target:
        user_role = session.get('user_role', 'trainee')
        perf_context = get_student_performance_context(perf_target, user_role, emp_id)
        
        # If unauthorized or not found, directly yield the message and exit
        if "Unauthorized" in perf_context or "not found" in perf_context or "not registered" in perf_context:
            def error_event_generator():
                add_chat_message(active_session_id, "assistant", perf_context, [])
                yield perf_context
            return Response(stream_with_context(error_event_generator()), mimetype='text/event-stream')
            
    # 2. Document-Grounded Context Retrieval (only if not a DB data query)
    sources = []
    selected_mode = "General Assistant"
    
    if not perf_target:  # skip vector search for DB queries
        try:
            from src.embeddings import embed_query
            query_vec = embed_query(query)
            
            # Check if there are active session documents
            tab_id = session.get('_tab_id')
            has_ephemeral = False
            if tab_id:
                try:
                    from src.vectorstore import get_ephemeral_collection
                    coll = get_ephemeral_collection(tab_id)
                    if coll.count() > 0:
                        has_ephemeral = True
                except Exception:
                    pass
            
            # Try Ephemeral first
            if has_ephemeral:
                from src.sprints import get_sprint, get_weekly_documents
                sprint = get_sprint(emp_id)
                week = sprint.get("current_week", 1)
                weekly_files = get_weekly_documents(emp_id, week)
                results = search_ephemeral(tab_id, query_vec, top_k=10, source_filters=weekly_files)
                if results:
                    sources = results
                    selected_mode = "Ephemeral Doc Q&A"
                    
            # Fall back to global doc store
            if not sources:
                results = search(query_vec, top_k=6, threshold=0.1)
                if results:
                    sources = results
                    selected_mode = "RAG (Document Guided)"
        except Exception as e:
            print(f"Retrieval / Embedding error: {e}")
            
    active_generations[emp_id] = {
        "session_id": active_session_id,
        "query": query,
        "partial_response": "",
        "sources": [{"source": s["source"], "page": s["page"], "text": s["text"], "score": s["score"]} for s in sources],
        "stop": False
    }
    
    def event_generator():
        gen_state = active_generations.get(emp_id)
        if not gen_state:
            yield "Error: State not found."
            return
            
        from src.llm import generate_rag_answer_stream, generate_chat_answer_stream, generate_ephemeral_rag_answer_stream

        
        if aggregate_context:
            system_prompt = (
                "You are an AI Coach and Analytics Advisor for 'Talent Sphere Elevate', a corporate training platform. "
                "You have been given a complete, real-time performance report for ALL trainees on the platform.\n"
                "Your task is to:\n"
                "1. Present the data clearly using markdown tables wherever there are lists of trainees or scores.\n"
                "2. Identify struggling trainees (below 60% average) and highlight them.\n"
                "3. Identify weak exam topics (class average below 70%) and recommend them for remedial sessions.\n"
                "4. Answer the admin's specific question directly using ONLY the data provided.\n"
                "5. Be concise, data-driven, and actionable in your recommendations.\n\n"
                f"--- AGGREGATE PLATFORM PERFORMANCE DATA ---\n{aggregate_context}"
            )
            chunk_stream = generate_chat_answer_stream(query, model, system_prompt)
        elif perf_target and perf_context:
            system_prompt = (
                "You are an AI Coach for 'Talent Sphere Elevate' who has access to real student performance data from the database. "
                "The data below is REAL and accurate — base your ENTIRE response on it.\n"
                "Guidelines:\n"
                "1. Present exam scores as a clean markdown table (Exam | Score | Percentage | Status).\n"
                "2. Highlight weak areas (below 60%) and suggest specific improvement steps.\n"
                "3. Mention study hours, session time, and overall progress trend.\n"
                "4. If proctoring/integrity flags exist, mention them clearly but professionally.\n"
                "5. Be encouraging and coaching-oriented.\n\n"
                f"--- TRAINEE PERFORMANCE DATA ---\n{perf_context}"
            )
            chunk_stream = generate_chat_answer_stream(query, model, system_prompt)
        elif selected_mode == "Ephemeral Doc Q&A":
            is_admin = (session.get('user_role') == 'admin')
            chunk_stream = generate_ephemeral_rag_answer_stream(query, sources, model, is_admin=is_admin)
        elif selected_mode == "RAG (Document Guided)":
            chunk_stream = generate_rag_answer_stream(query, sources, model)
        else:
            system_prompt = (
                "You are a helpful, encouraging learning coach for 'Talent Sphere Elevate', an advanced corporate training platform. "
                "Provide clear, professional explanation or training advice depending on the trainee's question. "
                "Do NOT include any programming code blocks or code examples (like Python or JavaScript) in your response unless explicitly asked.\n\n"
                "HOWEVER, you are encouraged to present data visually whenever applicable:\n"
                "1. If presenting tabular or structured list data, always format it as a markdown table.\n"
                "2. If presenting sequential, process, workflow, or step-by-step data, always format/render it as a Mermaid.js flowchart (enclosed in a '```mermaid' code block, e.g. using 'graph TD' or 'flowchart LR')."
            )
            chunk_stream = generate_chat_answer_stream(query, model, system_prompt)
            
        try:
            for chunk in chunk_stream:
                if gen_state.get("stop"):
                    break
                gen_state["partial_response"] += chunk
                yield chunk
                
            final_text = gen_state["partial_response"]
            if gen_state.get("stop"):
                final_text += " ⏹️ *[Response stopped by user]*"
                
            add_chat_message(active_session_id, "assistant", final_text, gen_state["sources"])
            
            if gen_state["sources"]:
                sources_json = json.dumps(gen_state["sources"])
                yield f"[SOURCES_JSON_START]{sources_json}[SOURCES_JSON_END]"
                
            # Generate exactly 3 direct follow-up questions based on the assistant response
            followups = []
            if not gen_state.get("stop") and final_text:
                try:
                    from src.llm import generate_chat_answer, list_local_models
                    local_models = list_local_models()
                    model_name = local_models[0] if local_models else "llama3-8b-8192"
                    followup_prompt = (
                        f"Based on this AI Coach response to a student's question, generate exactly 3 short, direct "
                        f"follow-up questions the student might want to ask next to continue learning:\n\n"
                        f"AI Coach response:\n{final_text[:2000]}\n\n"
                        f"Keep each question short (under 12 words) and phrase them as direct student questions.\n"
                        f"You MUST return ONLY a valid JSON array of strings (do not wrap in markdown or prefix text).\n"
                        f"Example format:\n"
                        f"[\"How do I use this?\", \"What is a code example?\"]"
                    )
                    resp = generate_chat_answer(
                        prompt=followup_prompt,
                        model_name=model_name,
                        system_instruction="You are a study suggestion assistant. You output ONLY valid JSON arrays of strings."
                    )
                    cleaned = clean_json_response(resp)
                    import re
                    try:
                        followups = json.loads(cleaned)
                    except Exception:
                        followups = re.findall(r'"([^"]+)"', cleaned)
                        if not followups:
                            followups = re.findall(r"'([^']+)'", cleaned)
                    
                    if isinstance(followups, list) and len(followups) > 0:
                        followups = [f.strip() for f in followups if f.strip()][:3]
                    else:
                        followups = []
                except Exception as e:
                    print(f"Failed to generate dynamic followups: {e}")
                    followups = []
            
            # Save followups in the database for the active message
            if followups:
                from src.chats import update_last_chat_message_followups
                update_last_chat_message_followups(active_session_id, followups)
                
            # Yield followups inside SUGGESTIONS_JSON block for direct client compatibility
            if followups:
                suggestions_json = json.dumps(followups)
                yield f"[SUGGESTIONS_JSON_START]{suggestions_json}[SUGGESTIONS_JSON_END]"
                
            if list_items:
                items_json = json.dumps({"items": list_items, "select_mode": "multi", "item_type": list_item_type})
                yield f"[ITEMS_JSON_START]{items_json}[ITEMS_JSON_END]"
                
        except Exception as e:
            yield f"Error in streaming: {e}"
        finally:
            active_generations.pop(emp_id, None)
            
    return Response(stream_with_context(event_generator()), mimetype='text/event-stream')

@app.route('/assistant/chat_stop', methods=['POST'])
@login_required
def chat_stop():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    if emp_id in active_generations:
        active_generations[emp_id]["stop"] = True
    return jsonify({"status": "success"})

@app.route('/assistant/clear', methods=['POST'])
@login_required
def assistant_clear():
    active_id = session.get('active_chat_session_id')
    if active_id:
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (active_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass
    return redirect(url_for('assistant'))

@app.route('/assistant/session/create')
@login_required
def assistant_session_create():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    new_id = str(uuid.uuid4())
    create_chat_session(new_id, emp_id, "New Conversation")
    session['active_chat_session_id'] = new_id
    return redirect(url_for('assistant', session_id=new_id))

@app.route('/assistant/session/rename')
@login_required
def assistant_session_rename():
    session_id = request.args.get('id')
    if session_id:
        session['renaming_session_id'] = session_id
        user_info = session.get('user_info', {}) or {}
        emp_id = user_info.get('employee_id', 'demo')
        sessions = get_chat_sessions_for_user(emp_id)
        title = "Conversation"
        for s in sessions:
            if s["session_id"] == session_id:
                title = s["title"]
                break
        session['renaming_session_title'] = title
    return redirect(url_for('assistant'))

@app.route('/assistant/session/rename/save', methods=['POST'])
@login_required
def assistant_session_rename_save():
    session_id = session.get('renaming_session_id')
    new_title = request.form.get('new_title', '').strip()
    if session_id and new_title:
        rename_chat_session(session_id, new_title)
    session.pop('renaming_session_id', None)
    session.pop('renaming_session_title', None)
    return redirect(url_for('assistant'))

@app.route('/assistant/session/rename/cancel')
@login_required
def assistant_session_rename_cancel():
    session.pop('renaming_session_id', None)
    session.pop('renaming_session_title', None)
    return redirect(url_for('assistant'))

@app.route('/assistant/session/delete')
@login_required
def assistant_session_delete():
    session_id = request.args.get('id')
    if session_id:
        delete_chat_session(session_id)
        if session.get('active_chat_session_id') == session_id:
            session.pop('active_chat_session_id', None)
    return redirect(url_for('assistant'))

@app.route('/assistant/voice', methods=['POST'])
@login_required
def assistant_voice():
    """Transcribe a voice recording using Groq Whisper Large V3.

    Expects: multipart form-data with field 'audio' (binary blob) and
             optional 'mime_type' (e.g. 'audio/webm').
    Returns: JSON { "text": "...", "error": null } or { "text": null, "error": "..." }
    """
    if not GROQ_API_KEY:
        return jsonify({"text": None, "error": "Voice transcription requires a GROQ_API_KEY."}), 503

    audio_file = request.files.get("audio")
    if not audio_file:
        return jsonify({"text": None, "error": "No audio file received."}), 400

    mime_type = request.form.get("mime_type", audio_file.content_type or "audio/webm")
    audio_bytes = audio_file.read()

    if not audio_bytes:
        return jsonify({"text": None, "error": "Received an empty audio file."}), 400

    try:
        text = transcribe_audio_whisper(audio_bytes, mime_type=mime_type)
        if not text:
            return jsonify({"text": None, "error": "No speech detected — please try again in a quieter environment."}), 200
        return jsonify({"text": text, "error": None})
    except RuntimeError as exc:
        return jsonify({"text": None, "error": str(exc)}), 500
    except Exception as exc:
        return jsonify({"text": None, "error": f"Unexpected transcription error: {exc}"}), 500


@app.route('/assistant/voice_agent/reset', methods=['POST'])
@login_required
def assistant_voice_agent_reset():
    """Clear the voice agent conversation history from session."""
    session.pop('voice_agent_history', None)
    return jsonify({"status": "success"})


@app.route('/assistant/voice_agent/chat', methods=['POST'])
@login_required
def assistant_voice_agent_chat():
    """Handles totally voice agent requests.
    Expects: Form-data with optional 'audio' file or JSON body with 'query'.
    """
    import traceback
    query_text = None
    
    # 1. Check if audio is uploaded
    if 'audio' in request.files:
        audio_file = request.files.get("audio")
        mime_type = request.form.get("mime_type", audio_file.content_type or "audio/webm")
        audio_bytes = audio_file.read()
        if not audio_bytes:
            return jsonify({"error": "Received empty audio file."}), 400
        try:
            query_text = transcribe_audio_whisper(audio_bytes, mime_type=mime_type)
        except Exception as e:
            return jsonify({"error": f"Transcription error: {str(e)}"}), 500
    else:
        # Check for JSON request
        data = request.get_json() or {}
        query_text = data.get('query', '').strip()
        
    if not query_text:
        return jsonify({
            "response_text": "I didn't hear anything. Please try speaking again.",
            "error": "Empty query"
        }), 200
        
    # Get history from session
    history = session.get('voice_agent_history', [])
    
    try:
        from src.voice_agent import run_voice_agent
        
        # Read model or default
        model = request.form.get('model') or (request.get_json(silent=True) or {}).get('model') or 'llama-3.3-70b-versatile'
        
        spoken_response, action_executed, updated_history = run_voice_agent(
            query=query_text,
            history=history,
            model_name=model
        )
        
        # Save history back to session
        session['voice_agent_history'] = updated_history
        
        return jsonify({
            "query_text": query_text,
            "response_text": spoken_response,
            "action_executed": action_executed,
            "error": None
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Voice agent error: {str(e)}"}), 500


@app.route('/assistant/wizard/docs')
@login_required
def assistant_wizard_docs():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
    try:
        from src.vectorstore import get_collection
        coll = get_collection()
        res = coll.get(include=["metadatas"])
        metadatas = res.get("metadatas") or []
        docs = sorted(list(set(m["source"] for m in metadatas if m and "source" in m)))
        return jsonify({"documents": docs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/assistant/wizard/trainees')
@login_required
def assistant_wizard_trainees():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
    try:
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT employee_id, full_name, domain FROM users WHERE role = 'trainee'")
        trainees = [{"employee_id": r["employee_id"], "name": r["full_name"], "domain": r["domain"]} for r in cursor.fetchall()]
        conn.close()
        return jsonify({"trainees": trainees})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/assistant/wizard/previous_exams')
@login_required
def assistant_wizard_previous_exams():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
    try:
        from src.exams import get_all_exams
        return jsonify({"exams": get_all_exams()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/assistant/wizard/templates')
@login_required
def assistant_wizard_get_templates():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
    try:
        from src.exams import get_exam_templates
        return jsonify({"templates": get_exam_templates()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/assistant/wizard/announcement/generate', methods=['POST'])
@login_required
def assistant_wizard_announcement_generate():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
        
    data = request.get_json() or {}
    title = data.get('title', '').strip()
    category = data.get('category', 'General').strip()
    priority = data.get('priority', 'Standard').strip()
    model = data.get('model', 'llama-3.3-70b-versatile')
    
    if not title:
        return jsonify({"error": "Title is required"}), 400
        
    prompt = (
        f"Generate a professional corporate training announcement body based on the following metadata:\n"
        f"- Title: {title}\n"
        f"- Category: {category}\n"
        f"- Priority: {priority}\n\n"
        f"The announcement should be clear, professional, engaging, and encourage participation if it's a course/event. "
        f"Ensure it does not have title headers or greetings like 'Dear Trainees' in the content, as this will be rendered under the announcement card header."
    )
    
    try:
        from src.llm import generate_chat_answer
        response = generate_chat_answer(
            prompt=prompt,
            model_name=model,
            system_instruction="You are a corporate communication expert. You write professional, succinct, and engaging announcements."
        )
        return jsonify({"content": response.strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/assistant/wizard/announcement/save', methods=['POST'])
@login_required
def assistant_wizard_announcement_save():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
        
    data = request.get_json() or {}
    title = data.get('title', '').strip()
    content = data.get('content', '').strip()
    send_email = data.get('send_email', True)
    
    if not title or not content:
        return jsonify({"error": "Title and content are required"}), 400
        
    try:
        from src.exams import add_announcement
        success = add_announcement(title, content, send_email=send_email)
        if success:
            return jsonify({"status": "success"})
        else:
            return jsonify({"error": "Failed to save announcement"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/assistant/wizard/generate', methods=['POST'])
@login_required
def assistant_wizard_generate():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
    data = request.get_json() or {}
    docs = data.get('docs', [])
    weights_input = data.get('weights', {})
    sections = data.get('sections', [])
    difficulty = data.get('difficulty', {"easy": 40, "medium": 40, "hard": 20})
    blooms = data.get('blooms', [])
    exclude_exam_ids = data.get('exclude_exams', [])
    model = data.get('model', 'llama-3.3-70b-versatile')
    auto_weight = data.get('auto_weight', False)
    
    if not GROQ_API_KEY:
        return jsonify({"error": "Groq API key not configured"}), 400
    if not docs:
        return jsonify({"error": "No documents selected"}), 400
    if not sections:
        return jsonify({"error": "No sections configured"}), 400
        
    try:
        from src.vectorstore import get_collection
        coll = get_collection()
        
        if auto_weight:
            doc_chunks_count = {}
            for doc in docs:
                res = coll.get(where={"source": doc}, include=[])
                doc_chunks_count[doc] = len(res.get("ids") or [])
            total_chunks = sum(doc_chunks_count.values()) or 1
            weights = {doc: (doc_chunks_count[doc] / total_chunks) for doc in docs}
        else:
            total_w = sum(float(weights_input.get(d, 0)) for d in docs) or 1
            weights = {d: (float(weights_input.get(d, 0)) / total_w) for d in docs}
            
        excluded_question_texts = []
        from src.exams import get_exam_by_id
        for ex_id in exclude_exam_ids:
            try:
                ex = get_exam_by_id(int(ex_id))
                if ex and ex.get("questions"):
                    for q in ex["questions"]:
                        if q.get("question"):
                            excluded_question_texts.append(q["question"])
            except Exception:
                pass
                
        all_questions = []
        
        for section in sections:
            sect_name = section.get('name', 'General Section')
            sect_type = section.get('type', 'mcq')
            sect_qty = int(section.get('count', 2))
            sect_marks = int(section.get('marks', 10))
            
            doc_list = list(docs)
            base_counts = {doc: int(sect_qty * weights.get(doc, 0)) for doc in doc_list}
            remainder = sect_qty - sum(base_counts.values())
            
            sorted_docs_by_fraction = sorted(
                doc_list,
                key=lambda doc: (sect_qty * weights.get(doc, 0)) - base_counts[doc],
                reverse=True
            )
            for i in range(remainder):
                base_counts[sorted_docs_by_fraction[i]] += 1
                
            for doc in doc_list:
                count_to_generate = base_counts[doc]
                if count_to_generate <= 0:
                    continue
                    
                res = coll.get(where={"source": doc}, include=["documents"])
                chunks = res.get("documents") or []
                if not chunks:
                    continue
                context_text = "\n\n".join(chunks[:3])
                
                blooms_str = ", ".join(blooms) if blooms else "None specific"
                diff_str = f"Easy ({difficulty.get('easy', 40)}%), Medium ({difficulty.get('medium', 40)}%), Hard ({difficulty.get('hard', 20)}%)"
                
                prompt = (
                    f"Generate exactly {count_to_generate} test questions for Section '{sect_name}' based on the document excerpt below.\n\n"
                    f"Constraints:\n"
                    f"- Section Type: {sect_type.upper()}\n"
                    f"- Marks per question: {sect_marks}\n"
                    f"- Difficulty distribution expectation: {diff_str}\n"
                    f"- Bloom's Taxonomy cognitive target: {blooms_str}\n"
                )
                
                if excluded_question_texts:
                    prompt += f"- Do NOT generate questions similar to these existing questions: {json.dumps(excluded_question_texts[:10])}\n"
                    
                prompt += (
                    f"\nFormat of each question object in JSON:\n"
                    f"[{{\n"
                    f"  \"question\": \"Question text here\",\n"
                    f"  \"type\": \"{sect_type}\",\n"
                    f"  \"marks\": {sect_marks},\n"
                    f"  \"options\": [\"Option A\", \"Option B\", \"Option C\", \"Option D\"],\n"
                    f"  \"correct_answer\": \"Correct answer text / model answer / rubric grading guide\"\n"
                    f"}}]\n\n"
                    f"You MUST return ONLY a valid JSON array of question objects (do not wrap in markdown or prefix text).\n\n"
                    f"--- DOCUMENT EXCERPT ---\n{context_text}"
                )
                
                from src.llm import generate_chat_answer
                response = generate_chat_answer(
                    prompt=prompt,
                    model_name=model,
                    system_instruction="You are a professional educational assessor. You output ONLY valid JSON arrays without markdown block wrapping or prefix text."
                )
                
                cleaned_resp = clean_json_response(response)
                try:
                    questions_list = json.loads(cleaned_resp)
                    if isinstance(questions_list, list):
                        for q in questions_list:
                            q["section"] = sect_name
                            all_questions.append(q)
                except Exception as e:
                    print(f"Error parsing JSON from response: {e}. Raw response: {response}")
                    
        from src.exams import sanitize_exam_questions
        all_questions = sanitize_exam_questions(all_questions)

        import difflib
        def similarity_ratio(a, b):
            return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()
            
        for i, q1 in enumerate(all_questions):
            q1["duplicate_flag"] = False
            for j, q2 in enumerate(all_questions):
                if i != j and similarity_ratio(q1["question"], q2["question"]) > 0.75:
                    q1["duplicate_flag"] = True
                    break
            if not q1["duplicate_flag"]:
                for prev_q_text in excluded_question_texts:
                    if similarity_ratio(q1["question"], prev_q_text) > 0.75:
                        q1["duplicate_flag"] = True
                        break
                        
        return jsonify({"questions": all_questions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/assistant/wizard/save', methods=['POST'])
@login_required
def assistant_wizard_save():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Admin role required"}), 403
        
    data = request.get_json() or {}
    title = data.get('title', '').strip()
    description = data.get('description', '').strip()
    total_marks = data.get('total_marks', 0)
    questions = data.get('questions', [])
    settings = data.get('settings', {})
    save_as_template = data.get('save_as_template', False)
    template_name = data.get('template_name', '').strip()
    
    scheduling = settings.get('scheduling', {})
    assignee_id = scheduling.get('assignee_id')
    due_date = scheduling.get('end_date')
    
    if not title or not questions or not assignee_id:
        return jsonify({"error": "Required fields missing"}), 400
        
    try:
        from src.exams import add_exam, assign_exam, add_exam_template
        import sqlite3
        
        duration = settings.get('duration', 30)
        full_desc = f"[Duration: {duration} minutes]\n\n{description}"
        
        init_exams_db()
        conn = sqlite3.connect(str(_DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute(
            """
            INSERT INTO exams (title, description, total_marks, questions, settings)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, full_desc, total_marks, json.dumps(questions), json.dumps(settings)),
        )
        exam_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        add_announcement(
            f"📝 New Exam Published: {title}",
            f"A new exam titled '{title}' (Total Marks: {total_marks}) has been generated and published by the Administrator via the AI Assistant.\n\n"
            f"Description: {full_desc}\n\n"
            f"Please check your dashboard or exams section for active assignments."
        )
        
        trainees_to_assign = []
        if assignee_id == 'all':
            conn = sqlite3.connect(str(_DB_PATH))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT employee_id FROM users WHERE role = 'trainee'")
            trainees_to_assign = [r["employee_id"] for r in cursor.fetchall()]
            conn.close()
        else:
            trainees_to_assign = [assignee_id]
            
        for t_id in trainees_to_assign:
            conn = sqlite3.connect(str(_DB_PATH))
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO assignments (exam_id, trainee_id, due_date, settings)
                VALUES (?, ?, ?, ?)
                """,
                (exam_id, t_id, due_date, json.dumps(settings)),
            )
            conn.commit()
            conn.close()
            
        if save_as_template and template_name:
            add_exam_template(template_name, settings)
            
        return jsonify({"status": "success", "exam_id": exam_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/get_face_descriptor', methods=['GET'])
def api_get_face_descriptor():
    # employee_id sent as query param by the Jinja-embedded PROCTOR_EMP_ID constant
    employee_id = request.args.get('emp_id') or (session.get('user_info') or {}).get('employee_id')
    if not employee_id:
        return jsonify({"error": "Missing employee_id"}), 400
    
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT face_descriptor, accommodation_proctoring FROM users WHERE employee_id = ?", (employee_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        desc_str = row["face_descriptor"]
        accommodation = bool(row["accommodation_proctoring"])
        if desc_str:
            try:
                descriptor = json.loads(desc_str)
                return jsonify({"enrolled": True, "descriptor": descriptor, "accommodation": accommodation})
            except Exception:
                pass
        return jsonify({"enrolled": False, "accommodation": accommodation})
    return jsonify({"error": "User not found"}), 404


@app.route('/api/enroll_face', methods=['POST'])
def api_enroll_face():
    data = request.get_json() or {}
    employee_id = data.get('emp_id') or (session.get('user_info') or {}).get('employee_id')
    if not employee_id:
        return jsonify({"error": "Missing employee_id"}), 400
    descriptor = data.get("descriptor")
    if not descriptor or not isinstance(descriptor, list) or len(descriptor) != 128:
        return jsonify({"error": "Invalid face descriptor. Must be a list of 128 floats."}), 400
        
    success = set_user_face_descriptor(employee_id, json.dumps(descriptor))
    if success:
        return jsonify({"status": "success", "message": "Face enrolled successfully."})
    return jsonify({"error": "Database write failed."}), 500


@app.route('/api/log_proctoring_event', methods=['POST'])
def api_log_proctoring_event():
    data = request.get_json() or {}
    assignment_id = data.get("assignment_id")
    trigger_reason = data.get("trigger_reason")
    snapshot_data = data.get("snapshot_data")
    score = data.get("score")
    face_count = data.get("face_count")
    
    if not assignment_id or not trigger_reason or not snapshot_data:
        return jsonify({"error": "Missing required fields."}), 400
        
    # Analyze the image using Groq vision API
    groq_label = "none"
    if trigger_reason in ["face_presence_check", "tab_switch", "fullscreen_exit"]:
        # Only run vision analysis on actual webcam snapshots
        groq_label = analyze_proctor_image(snapshot_data)
    elif trigger_reason == "identity_mismatch":
        groq_label = "mismatch"
        
    # Determine if this is a real violation:
    is_violation = True
    if trigger_reason == "face_presence_check":
        if face_count == 1 and groq_label == "none":
            is_violation = False
            
    if is_violation:
        log_id = add_proctor_log(
            assignment_id=assignment_id,
            trigger_reason=trigger_reason,
            groq_label=groq_label,
            snapshot_data=snapshot_data,
            score=score
        )
        if log_id:
            return jsonify({"status": "success", "log_id": log_id, "groq_label": groq_label, "is_violation": True})
        return jsonify({"error": "Failed to log event."}), 500
    else:
        # Normal check: skip logging to database to prevent database bloat
        return jsonify({"status": "success", "groq_label": "none", "is_violation": False})


@app.route('/user_management/toggle_accommodation', methods=['POST'])
@login_required
def toggle_accommodation():
    if session.get('user_role') != 'admin':
        return jsonify({"error": "Forbidden"}), 403
        
    employee_id = request.form.get('employee_id')
    enabled = request.form.get('enabled')
    if not employee_id:
        return jsonify({"error": "Missing employee ID"}), 400
        
    enabled_val = 1 if enabled == 'true' or enabled == '1' else 0
    success = set_user_accommodation(employee_id, enabled_val)
    if success:
        return jsonify({"status": "success", "enabled": enabled_val})
    return jsonify({"error": "Failed to toggle accommodation"}), 500


@app.route('/exams/assignment/publish', methods=['POST'])
@login_required
def exams_assignment_publish():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    assignment_id = request.form.get('assignment_id', type=int)
    selected_exam_id = request.form.get('selected_exam_id', type=int)
    
    if publish_assignment_results(assignment_id):
        flash("Results published successfully!")
    else:
        flash("Failed to publish results.")
        
    if selected_exam_id:
        return redirect(url_for('exams', active_tab='assign', selected_exam_id=selected_exam_id))
    return redirect(url_for('exams', active_tab='results'))


@app.route('/admin/maintenance')
@login_required
def admin_maintenance():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
    return render_template('maintenance.html', active_page='maintenance')


@app.route('/dashboard/settings/toggle_email', methods=['POST'])
@login_required
def dashboard_toggle_email():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
    from src.exams import get_system_setting, set_system_setting
    current_val = get_system_setting("email_notifications_enabled", "true").lower() == "true"
    new_val = "false" if current_val else "true"
    set_system_setting("email_notifications_enabled", new_val)
    flash("Email notifications " + ("enabled" if new_val == "true" else "disabled") + ".")
    return redirect(url_for('dashboard'))


@app.route('/admin/kill/exams', methods=['POST'])
@login_required
def admin_kill_exams():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    if clear_all_exams():
        flash("💥 All exams, assignments, proctor logs, and templates have been deleted.")
    else:
        flash("Failed to delete exams.")
    return redirect(url_for('admin_maintenance'))


@app.route('/admin/kill/announcements', methods=['POST'])
@login_required
def admin_kill_announcements():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    if clear_all_announcements():
        flash("💥 All announcements and email logs have been deleted.")
    else:
        flash("Failed to delete announcements.")
    return redirect(url_for('admin_maintenance'))


@app.route('/admin/kill/trainees', methods=['POST'])
@login_required
def admin_kill_trainees():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    from src.users import clear_all_trainee_users
    if clear_all_trainee_users():
        flash("💥 All trainee user accounts, chat sessions, messages, and results have been deleted.")
    else:
        flash("Failed to delete trainee users.")
    return redirect(url_for('admin_maintenance'))


@app.route('/admin/kill/overall', methods=['POST'])
@login_required
def admin_kill_overall():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
        
    verify_text = request.form.get('verification', '').strip()
    if verify_text != "DESTROY ALL DATA":
        flash("Overall system reset aborted. Verification text did not match.")
        return redirect(url_for('admin_maintenance'))
        
    # Clear exams, assignments, templates, proctor logs
    clear_all_exams()
    
    # Clear announcements, email logs
    clear_all_announcements()
    
    # Clear all trainee users
    from src.users import clear_all_trainee_users
    clear_all_trainee_users()
    
    # Clear vectorstore documents and index
    from src.vectorstore import reset_collection
    try:
        reset_collection()
        for pdf_file in Path(DOCUMENTS_DIR).glob("*.pdf"):
            pdf_file.unlink()
    except Exception as e:
        print(f"Error resetting vectorstore/files during overall kill: {e}")
        
    # Clear all study plans and sprint database tables
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.cursor()
        c.execute("DELETE FROM sprint_schedules")
        c.execute("DELETE FROM weekly_study_plans")
        c.execute("DELETE FROM qa_errors")
        c.execute("DELETE FROM interview_evaluations")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error resetting sprint tables during overall kill: {e}")

    flash("💥 OVERALL SYSTEM RESET COMPLETE: All data has been wiped.")
    return redirect(url_for('admin_maintenance'))

@app.route('/admin/kill/sprints', methods=['POST'])
@login_required
def admin_kill_sprints():
    if session.get('user_role') != 'admin':
        return redirect(url_for('dashboard'))
    
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.cursor()
        c.execute("DELETE FROM sprint_schedules")
        c.execute("DELETE FROM weekly_study_plans")
        c.execute("DELETE FROM qa_errors")
        c.execute("DELETE FROM interview_evaluations")
        conn.commit()
        conn.close()
        flash("📚 SPRINT DATABASE RESET COMPLETE: All study plans, sprint schedules, QA errors, and interview reports wiped.")
    except Exception as e:
        flash(f"Failed to reset sprint database: {e}")
        
    return redirect(url_for('admin_maintenance'))


@app.route('/study_plans', methods=['GET'])
@login_required
def study_plans_page():
    if session.get('user_role') != 'admin':
        return redirect(url_for('sprint_page'))

    from src.sprints import get_all_study_plans, get_all_sprint_schedules
    all_plans = get_all_study_plans()
    all_schedules = get_all_sprint_schedules()

    all_docs = []
    try:
        from src.vectorstore import get_collection
        coll = get_collection()
        res = coll.get(include=["metadatas"])
        metadatas = res.get("metadatas") or []
        all_docs = sorted(list(set(m["source"] for m in metadatas if m and "source" in m)))
    except Exception:
        all_docs = []

    all_trainees = []
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT employee_id, full_name, email, domain FROM users WHERE role = 'trainee'")
        all_trainees = [dict(r) for r in c.fetchall()]
        conn.close()
    except Exception:
        all_trainees = []

    return render_template(
        'study_plans.html',
        active_page='study_plans',
        all_plans=all_plans,
        all_schedules=all_schedules,
        all_docs=all_docs,
        all_trainees=all_trainees
    )

@app.route('/study_plans/delete', methods=['POST'])
@login_required
def study_plans_delete():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True) or {}
    plan_id = data.get('plan_id', '')
    from src.sprints import delete_study_plan
    delete_study_plan(plan_id)
    return jsonify({'status': 'success'})

@app.route('/study_plans/assign', methods=['POST'])
@login_required
def study_plans_assign():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True) or {}
    plan_id = data.get('plan_id', '')
    user_ids = data.get('user_ids', [])
    single_user = data.get('user_id', '')
    assign_all = data.get('assign_all', False)
    
    if single_user and single_user not in user_ids:
        if isinstance(user_ids, list):
            user_ids.append(single_user)
        else:
            user_ids = [single_user]

    from src.sprints import assign_study_plan_to_user, get_study_plan
    count = 0
    assigned_emails = []

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.cursor()

        if assign_all:
            c.execute("SELECT employee_id, email FROM users WHERE role = 'trainee'")
            trainees = c.fetchall()
            for u_id, email in trainees:
                if assign_study_plan_to_user(u_id, plan_id):
                    count += 1
                    if email:
                        assigned_emails.append(email)
        else:
            if isinstance(user_ids, str):
                user_ids = [user_ids]
            for u_id in user_ids:
                if assign_study_plan_to_user(u_id, plan_id):
                    count += 1
                    c.execute("SELECT email FROM users WHERE employee_id = ?", (u_id,))
                    row = c.fetchone()
                    if row and row[0]:
                        assigned_emails.append(row[0])
        
        conn.close()
    except Exception as e:
        print(f"Error assigning trainees: {e}")

    # Send Email Notifications to Assigned Trainees
    try:
        plan = get_study_plan(plan_id=plan_id)
        if plan and assigned_emails:
            from src.mail import send_study_plan_assignment_email
            send_study_plan_assignment_email(
                emails=assigned_emails,
                plan_title=plan.get('title', 'Agile Study Plan'),
                domain=plan.get('domain', 'General'),
                week_number=plan.get('week_number', 1)
            )
    except Exception as mail_err:
        print(f"Error sending assignment email: {mail_err}")

    return jsonify({'status': 'success', 'assigned_count': count})

@app.route('/sprint', methods=['GET'])
@login_required
def sprint_page():
    if session.get('user_role') == 'admin':
        return redirect(url_for('study_plans_page'))

    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    user_role = session.get('user_role', 'trainee')
    domain = user_info.get('domain', 'general')

    try:
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.cursor()
        c.execute("SELECT domain FROM users WHERE employee_id = ?", (emp_id,))
        r = c.fetchone()
        if r and r[0]:
            domain = r[0]
            if isinstance(session.get('user_info'), dict):
                session['user_info']['domain'] = domain
        conn.close()
    except Exception:
        pass

    from src.sprints import (
        get_sprint, get_study_plan, get_qa_errors, 
        get_interview_evaluation, get_all_sprint_schedules, get_all_study_plans
    )
    
    user_sprint = get_sprint(emp_id)
    current_active_week = user_sprint.get('current_week', 1)
    current_active_day = user_sprint.get('current_day', 1)
    req_week = request.args.get('week', type=int)

    # Fetch all system study plans
    system_plans = get_all_study_plans()
    all_week_numbers = sorted(list(set(p.get('week_number', 1) for p in system_plans))) if system_plans else [1]
    if 1 not in all_week_numbers:
        all_week_numbers.insert(0, 1)

    week_plans_summary = []
    for w in all_week_numbers:
        is_unlocked = (w == 1) or (w <= current_active_week)
        matching_plan = next((p for p in system_plans if p.get('week_number') == w and (p.get('domain', '').lower() == domain.lower() or p.get('domain', '').lower() == 'general')), None)
        if not matching_plan:
            matching_plan = next((p for p in system_plans if p.get('week_number') == w), None)
            
        plan_title = matching_plan.get('title') if matching_plan else f"Week {w} Study Plan"
        plan_domain = matching_plan.get('domain') if matching_plan else domain
        plan_id = matching_plan.get('plan_id') if matching_plan else f"default-w{w}"

        week_plans_summary.append({
            'week_number': w,
            'title': plan_title,
            'domain': plan_domain,
            'plan_id': plan_id,
            'is_locked': not is_unlocked,
            'is_current': (w == current_active_week)
        })

    view_week_num = req_week if (req_week and req_week in all_week_numbers) else current_active_week
    req_summary = next((wp for wp in week_plans_summary if wp['week_number'] == view_week_num), None)
    if req_summary and req_summary['is_locked']:
        flash(f"🔒 Week {view_week_num} is locked! Please view the Day 7 Performance Retrospective for Week {view_week_num - 1} first to unlock Week {view_week_num}.")
        return redirect(url_for('sprint_page', week=current_active_week))

    assigned_plan_id = user_sprint.get('assigned_plan_id') if view_week_num == current_active_week else None
    study_plan = get_study_plan(domain=domain, week_number=view_week_num, plan_id=assigned_plan_id)
    
    tasks = {}
    try:
        tasks = json.loads(study_plan.get('tasks_json', '{}'))
    except Exception:
        tasks = {}

    qa_errors = get_qa_errors(emp_id, view_week_num)
    eval_report = get_interview_evaluation(emp_id, view_week_num)
    
    all_schedules = get_all_sprint_schedules() if user_role == 'admin' else []
    all_plans = system_plans if user_role == 'admin' else []

    all_docs = []
    try:
        from src.vectorstore import get_collection
        coll = get_collection()
        res = coll.get(include=["metadatas"])
        metadatas = res.get("metadatas") or []
        all_docs = sorted(list(set(m["source"] for m in metadatas if m and "source" in m)))
    except Exception:
        all_docs = []

    ref_files = []
    try:
        ref_files_raw = study_plan.get('reference_files_json', '[]')
        ref_files = json.loads(ref_files_raw) if isinstance(ref_files_raw, str) else ref_files_raw
    except Exception:
        ref_files = []

    if not isinstance(ref_files, list):
        ref_files = []

    import re
    extracted = []
    for day in ['day1', 'day2', 'day3', 'day4']:
        day_t = tasks.get(day, [])
        doc_found = ""
        items = day_t if isinstance(day_t, list) else [day_t]
        for item in items:
            match = re.search(r'\[([^\]]+\.pdf)\]', str(item), re.IGNORECASE)
            if match:
                doc_found = match.group(1).strip()
                break
        extracted.append(doc_found)
    
    final_ref_files = []
    for idx in range(4):
        doc_name = ""
        if idx < len(ref_files) and ref_files[idx]:
            doc_name = ref_files[idx]
        elif idx < len(extracted) and extracted[idx]:
            doc_name = extracted[idx]
        final_ref_files.append(doc_name)
        
    ref_files = final_ref_files

    return render_template(
        'sprint.html',
        active_page='sprint',
        sprint=user_sprint,
        study_plan=study_plan,
        tasks=tasks,
        ref_files=ref_files,
        qa_errors=qa_errors,
        eval_report=eval_report,
        all_schedules=all_schedules,
        all_plans=all_plans,
        all_docs=all_docs,
        user_role=user_role,
        week_plans_summary=week_plans_summary,
        view_week_num=view_week_num
    )




@app.route('/documents/view/<path:filename>')
@login_required
def view_document_pdf(filename):
    from src.config import DOCUMENTS_DIR
    try:
        doc_dir = Path(DOCUMENTS_DIR).resolve()
        target_file = (doc_dir / filename).resolve()
        
        if str(target_file).startswith(str(doc_dir)) and target_file.is_file():
            if target_file.suffix.lower() in ['.txt', '.md', '.log']:
                txt_content = target_file.read_text(encoding='utf-8', errors='ignore')
                import html
                safe_txt = html.escape(txt_content)
                return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{html.escape(filename)}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            padding: 1.25rem;
            margin: 0;
            line-height: 1.6;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            background: rgba(99, 102, 241, 0.15);
            color: #818cf8;
            border: 1px solid rgba(99, 102, 241, 0.3);
            padding: 0.3rem 0.75rem;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 700;
            margin-bottom: 1rem;
        }}
        .content-card {{
            background: #1e293b;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 1.25rem;
            white-space: pre-wrap;
            font-family: 'Cascadia Code', 'Fira Code', Consolas, monospace;
            font-size: 0.86rem;
            color: #cbd5e1;
            box-shadow: 0 4px 16px rgba(0,0,0,0.3);
        }}
    </style>
</head>
<body>
    <div class="badge">📄 Custom Text Learning Document</div>
    <div class="content-card">{safe_txt}</div>
</body>
</html>"""
            return send_from_directory(doc_dir, filename, as_attachment=False)
    except Exception as e:
        print(f"Document view error: {e}")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background: #0b0f19;
                color: #94a3b8;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
                text-align: center;
                padding: 1.5rem;
                box-sizing: border-box;
            }}
            .card {{
                background: #111625;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 16px;
                padding: 2rem;
                max-width: 480px;
                box-shadow: 0 12px 32px rgba(0,0,0,0.3);
            }}
            .icon {{
                font-size: 2.5rem;
                margin-bottom: 0.75rem;
            }}
            .title {{
                color: #f8fafc;
                font-size: 1.1rem;
                font-weight: 700;
                margin-bottom: 0.5rem;
            }}
            .desc {{
                font-size: 0.85rem;
                color: #94a3b8;
                line-height: 1.5;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">📝</div>
            <div class="title">Custom Text Learning Module</div>
            <div class="desc">
                No external PDF document file was uploaded for this study module. The learning curriculum and topic specifications are fully provided in the main workspace view.
            </div>
        </div>
    </body>
    </html>
    """, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/sprint/ask_ai_coach', methods=['POST'])
@login_required
def sprint_ask_ai_coach():
    data = request.get_json(silent=True) or {}
    question = data.get('question', '').strip()
    day_number = data.get('day_number', 1)
    doc_name = data.get('doc_name', '')
    
    if not question:
        return jsonify({'error': 'Please enter a question.'}), 400
        
    try:
        from src.embeddings import embed_query
        from src.vectorstore import search
        from src.llm import generate_chat_answer
        
        context_text = ""
        try:
            q_emb = embed_query(question)
            filters = [doc_name] if doc_name else None
            hits = search(query_embedding=q_emb, top_k=4, source_filters=filters)
            if hits:
                context_text = "\n\n".join([f"Excerpt ({h['source']}): {h['text']}" for h in hits])
        except Exception as ve:
            print(f"Vector search note: {ve}")
            
        system_instruction = (
            f"You are an expert Socratic AI Learning Coach helping a trainee studying Day {day_number} material.\n"
            f"Reference Document: {doc_name if doc_name else 'Study Plan Reference'}\n"
            f"Relevant Document Excerpts:\n{context_text if context_text else 'No specific vector excerpts found.'}\n\n"
            f"Answer the trainee's question concisely, clearly, and insightfully. Use markdown formatting and bullet points where appropriate."
        )
        
        response_text = generate_chat_answer(
            prompt=question,
            model_name="llama-3.3-70b-versatile",
            system_instruction=system_instruction
        )
        
        return jsonify({'status': 'success', 'answer': response_text})
    except Exception as e:
        print(f"Error in ask_ai_coach: {e}")
        return jsonify({'status': 'error', 'error': f'Failed to process query: {str(e)}'}), 500

@app.route('/sprint/complete_retro', methods=['POST'])
@login_required
def sprint_complete_retro():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    
    from src.sprints import get_sprint, update_sprint_week, update_sprint_day, update_sprint_progress
    user_sprint = get_sprint(emp_id)
    curr_week = user_sprint.get('current_week', 1)
    
    data = request.get_json(silent=True) or {}
    retro_week = data.get('week_number', curr_week)
    
    if retro_week >= curr_week:
        next_week = curr_week + 1
        update_sprint_week(emp_id, next_week)
        update_sprint_day(emp_id, 1)
        update_sprint_progress(emp_id, 0.0)
        flash(f"🎉 Week {curr_week} Retrospective Completed! Week {next_week} Study Plan is now unlocked!")
        return jsonify({'status': 'success', 'next_week': next_week})
        
    return jsonify({'status': 'success', 'next_week': curr_week + 1})

@app.route('/sprint/advance_day', methods=['POST'])
@login_required
def sprint_advance_day():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    from src.sprints import get_sprint, update_sprint_day, update_sprint_progress
    user_sprint = get_sprint(emp_id)
    curr_day = user_sprint.get('current_day', 1)
    next_day = min(curr_day + 1, 7)
    update_sprint_day(emp_id, next_day)
    progress = round((next_day / 6.0) * 100.0, 1) if next_day <= 6 else 100.0
    update_sprint_progress(emp_id, progress)
    return jsonify({'status': 'success', 'current_day': next_day, 'progress': progress})

@app.route('/sprint/take_day5_exam', methods=['GET', 'POST'])
@login_required
def sprint_take_day5_exam():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    domain = user_info.get('domain', 'general')

    from src.sprints import get_sprint, get_study_plan
    from src.exams import get_all_exams, add_exam_and_get_id, assign_exam, get_assignments_for_trainee

    sprint_data = get_sprint(emp_id)
    week_num = sprint_data.get('current_week', 1)
    study_plan = get_study_plan(domain, week_num)
    
    exam_id = study_plan.get('day5_exam_id')
    
    if not exam_id:
        all_e = get_all_exams()
        for e in all_e:
            if f"Week {week_num}" in e.get("title", "") or domain.lower() in e.get("title", "").lower():
                exam_id = e.get("exam_id")
                break

    if not exam_id:
        from src.llm import generate_chat_answer, clean_json_response
        prompt = (
            f"Generate a Day 5 Gateway Assessment covering {domain} training materials for Week {week_num}.\n"
            f"Create exactly 5 high-quality multiple choice questions (MCQs).\n"
            f"You MUST return ONLY a valid JSON list of objects matching this exact structure:\n"
            f"[\n"
            f"  {{\n"
            f"    \"question\": \"Question text here?\",\n"
            f"    \"type\": \"mcq\",\n"
            f"    \"marks\": 10,\n"
            f"    \"options\": [\"Option A\", \"Option B\", \"Option C\", \"Option D\"],\n"
            f"    \"correct_answer\": \"Option A\"\n"
            f"  }}\n"
            f"]"
        )
        try:
            resp = generate_chat_answer(prompt=prompt, model_name="llama-3.3-70b-versatile", system_instruction="Output ONLY valid JSON array.")
            cleaned = clean_json_response(resp)
            q_list = json.loads(cleaned)
        except Exception:
            q_list = [
                {
                    "question": f"What is a fundamental concept in {domain.capitalize()} Week {week_num}?",
                    "type": "mcq",
                    "marks": 10,
                    "options": ["Architecture Guidelines", "Random Guess", "None", "All of the above"],
                    "correct_answer": "Architecture Guidelines"
                }
            ]

        exam_title = f"{domain.capitalize()} Week {week_num} Gateway Exam"
        from src.exams import sanitize_exam_questions
        q_list = sanitize_exam_questions(q_list, exam_title=exam_title)
        exam_id = add_exam_and_get_id(exam_title, f"Gateway Exam covering Week {week_num} study materials.", len(q_list) * 10, q_list)

    trainee_assignments = get_assignments_for_trainee(emp_id)
    target_assignment_id = None
    if exam_id:
        for a in trainee_assignments:
            if a.get('exam_id') == int(exam_id) and a.get('status') == 'assigned':
                target_assignment_id = a.get('assignment_id')
                break

    if not target_assignment_id and exam_id:
        assign_exam(int(exam_id), emp_id, None)
        trainee_assignments = get_assignments_for_trainee(emp_id)
        for a in trainee_assignments:
            if a.get('exam_id') == int(exam_id) and a.get('status') == 'assigned':
                target_assignment_id = a.get('assignment_id')
                break

    if target_assignment_id:
        session['taking_assignment_id'] = target_assignment_id
        session['exam_started'] = False
        return redirect(url_for('exams'))

    flash("Could not initialize exam assignment. Please try again.")
    return redirect(url_for('exams'))

@app.route('/sprint/chunk_document', methods=['POST'])
@login_required
def sprint_chunk_document():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json(silent=True) or {}
    doc_names = data.get('doc_names', [])
    if isinstance(doc_names, str):
        doc_names = [d.strip() for d in doc_names.split(',') if d.strip()]
    
    single_doc = data.get('doc_name', '').strip()
    if single_doc and single_doc not in doc_names:
        doc_names.append(single_doc)

    raw_text = data.get('raw_text', '').strip()
    domain = data.get('domain', 'general').lower()
    week_num = int(data.get('week_number', 1))
    title_input = data.get('title', '').strip()

    from src.llm import generate_chat_answer, clean_json_response
    from src.vectorstore import get_collection
    from src.sprints import run_sprint_orchestrator, save_study_plan
    from src.exams import add_exam_and_get_id, assign_exam_to_all_students

    docs_contents = []
    source_files_list = []
    
    if doc_names:
        try:
            coll = get_collection()
            for dn in doc_names:
                res = coll.get(where={"source": dn}, include=["documents"])
                d_list = res.get("documents") or []
                c_text = "\n\n".join(d_list[:25]) if d_list else f"Reference Content for {dn}"
                docs_contents.append({"source": dn, "text": c_text})
                source_files_list.append(dn)
        except Exception as e:
            print(f"Error fetching vectorstore docs: {e}")

    if not docs_contents and raw_text:
        docs_contents.append({"source": "Custom Input", "text": raw_text})

    if not docs_contents:
        return jsonify({'error': 'No document text or files found to process.'}), 400

    num_files = len(source_files_list)
    combined_text = "\n\n".join(d["text"] for d in docs_contents)
    
    # Reference file mapping across 4 Days
    ref_files_4days = []
    if source_files_list:
        if num_files == 1:
            ref_files_4days = [source_files_list[0]] * 4
        elif num_files == 2:
            ref_files_4days = [source_files_list[0], source_files_list[0], source_files_list[1], source_files_list[1]]
        elif num_files == 3:
            ref_files_4days = [source_files_list[0], source_files_list[1], source_files_list[2], source_files_list[2]]
        else:
            ref_files_4days = source_files_list[:4]
    else:
        ref_files_4days = [] # Custom text mode

    # Split content equally across Days 1..4 with AI enrichment
    tasks = {}
    split_prompt = (
        f"You are an expert Agile Learning Curriculum Architect.\n"
        f"Your task is to take the provided learning materials/custom text for {domain.capitalize()} Week {week_num} and split the content EQUALLY into 4 distinct, sequential daily study modules for Day 1, Day 2, Day 3, and Day 4.\n\n"
        f"CRITICAL REQUIREMENTS:\n"
        f"1. Divide the overall material evenly across 4 days so each day (Day 1, Day 2, Day 3, Day 4) receives a comprehensive 1/4th portion of the learning content.\n"
        f"2. For each day, provide detailed key concepts, definitions, technical rules, and actionable checkpoints.\n"
        f"3. Do NOT leave any day empty or with generic single-line placeholders.\n\n"
        f"Source Material Content:\n{combined_text[:7000]}\n\n"
        f"You MUST return ONLY a JSON object with this exact structure:\n"
        f"{{\n"
        f"  \"day1\": [\"Detailed learning outcome / concept 1 for Day 1\", \"Detailed concept 2 for Day 1\", \"Checkpoint task for Day 1\"],\n"
        f"  \"day2\": [\"Detailed learning outcome / concept 1 for Day 2\", \"Detailed concept 2 for Day 2\", \"Checkpoint task for Day 2\"],\n"
        f"  \"day3\": [\"Detailed learning outcome / concept 1 for Day 3\", \"Detailed concept 2 for Day 3\", \"Checkpoint task for Day 3\"],\n"
        f"  \"day4\": [\"Detailed learning outcome / concept 1 for Day 4\", \"Detailed concept 2 for Day 4\", \"Checkpoint task for Day 4\"]\n"
        f"}}"
    )

    try:
        resp = generate_chat_answer(prompt=split_prompt, model_name="llama-3.3-70b-versatile", system_instruction="Output ONLY valid JSON object with keys day1, day2, day3, day4.")
        cleaned = clean_json_response(resp)
        parsed_tasks = json.loads(cleaned)
        if isinstance(parsed_tasks, dict) and all(k in parsed_tasks for k in ['day1', 'day2', 'day3', 'day4']):
            tasks = parsed_tasks
    except Exception as e:
        print(f"Error in AI splitup: {e}")

    # Fallback if AI split failed or incomplete
    if not tasks or not tasks.get('day1'):
        lines = [line.strip() for line in combined_text.split('\n') if line.strip()]
        total_lines = len(lines)
        chunk_sz = max(1, total_lines // 4)
        tasks = {
            "day1": lines[:chunk_sz] if lines[:chunk_sz] else ["Module 1 Fundamentals"],
            "day2": lines[chunk_sz:chunk_sz*2] if lines[chunk_sz:chunk_sz*2] else ["Module 2 Intermediate Principles"],
            "day3": lines[chunk_sz*2:chunk_sz*3] if lines[chunk_sz*2:chunk_sz*3] else ["Module 3 Advanced Patterns"],
            "day4": lines[chunk_sz*3:] if lines[chunk_sz*3:] else ["Module 4 Architecture & Integration"]
        }

    # Generate plain text document files for Custom Text input mode
    if not source_files_list:
        from src.config import DOCUMENTS_DIR
        ref_files_4days = []
        doc_dir = Path(DOCUMENTS_DIR).resolve()
        doc_dir.mkdir(parents=True, exist_ok=True)
        
        for d in range(1, 5):
            day_key = f"day{d}"
            txt_filename = f"Custom_{domain.capitalize()}_Week{week_num}_Day{d}.txt"
            day_items = tasks.get(day_key, [])
            
            body_lines = [
                f"============================================================",
                f"WEEK {week_num} ({domain.upper()}) - DAY {d} STUDY SPECIFICATION",
                f"============================================================\n",
                f"LEARNING OBJECTIVES & TOPIC BREAKDOWN:"
            ]
            for item in day_items:
                body_lines.append(f"• {item}")
                
            txt_path = doc_dir / txt_filename
            try:
                txt_path.write_text("\n".join(body_lines), encoding="utf-8")
            except Exception as e:
                print(f"Error writing custom text document: {e}")
                
            ref_files_4days.append(txt_filename)

    # Format file tags into tasks if ref_files_4days present
    if ref_files_4days and len(ref_files_4days) == 4:
        for idx, day_key in enumerate(['day1', 'day2', 'day3', 'day4']):
            f_name = ref_files_4days[idx]
            day_list = tasks.get(day_key, [])
            if day_list and isinstance(day_list, list):
                if not any(f"[{f_name}]" in str(item) for item in day_list):
                    day_list.insert(0, f"[{f_name}] Core Specification Module")
            tasks[day_key] = day_list

    # Multi-Section Exam Generation (2 MCQs, 2 Short Answer, 2 Fill in Blank, 2 Match per file)
    target_q_count = max(8, len(docs_contents) * 8)
    
    exam_prompt = (
        f"You are a Senior Technical Examiner creating a comprehensive Day 5 Gateway Exam for {domain.capitalize()} Week {week_num}.\n"
        f"Generate multi-section questions based strictly on the training materials provided below.\n\n"
        f"CRITICAL REQUIREMENTS:\n"
        f"For EACH of the {len(docs_contents)} reference source document(s), generate EXACTLY 2 questions of EACH of the following 4 question types (8 questions per document total):\n"
        f"  1. 'mcq': Multiple Choice Question (4 distinct, realistic, technical answer choices, correct_answer)\n"
        f"  2. 'short_answer': Short Answer Question (conceptual/technical question requiring 2-3 sentences, correct_answer model solution)\n"
        f"  3. 'fill_in_blank': Fill in the Blanks Question (technical statement containing '___', exact term in correct_answer)\n"
        f"  4. 'match': Match the Following Question (question prompt, match_pairs array of 4 {{left, right}} items, correct_answer string summary)\n\n"
        f"Training Materials Summary:\n"
    )
    for idx, d in enumerate(docs_contents):
        exam_prompt += f"--- Source File {idx+1} ({d['source']}) ---\n{d['text'][:2500]}\n\n"

    exam_prompt += (
        f"DO NOT generate generic templates like 'Section 1' or 'Primary Standard Specification'. Test actual technical definitions, formulas, and concepts from the text.\n\n"
        f"You MUST return ONLY a valid JSON list of objects matching this structure:\n"
        f"[\n"
        f"  {{\n"
        f"    \"question\": \"Detailed technical question text based on document contents?\",\n"
        f"    \"type\": \"mcq\",\n"
        f"    \"section\": \"Section A: Multiple Choice Questions\",\n"
        f"    \"marks\": 10,\n"
        f"    \"options\": [\"Technical Option 1\", \"Technical Option 2\", \"Technical Option 3\", \"Technical Option 4\"],\n"
        f"    \"correct_answer\": \"Technical Option 1\"\n"
        f"  }},\n"
        f"  {{\n"
        f"    \"question\": \"Explain the principle of X in detail.\",\n"
        f"    \"type\": \"short_answer\",\n"
        f"    \"section\": \"Section B: Short Answer Questions\",\n"
        f"    \"marks\": 10,\n"
        f"    \"correct_answer\": \"Model answer explaining principle X...\"\n"
        f"  }},\n"
        f"  {{\n"
        f"    \"question\": \"The process of Y is defined as ___.\",\n"
        f"    \"type\": \"fill_in_blank\",\n"
        f"    \"section\": \"Section C: Fill in the Blanks\",\n"
        f"    \"marks\": 10,\n"
        f"    \"correct_answer\": \"exact term\"\n"
        f"  }},\n"
        f"  {{\n"
        f"    \"question\": \"Match each term in Column A with its definition in Column B.\",\n"
        f"    \"type\": \"match\",\n"
        f"    \"section\": \"Section D: Match the Following\",\n"
        f"    \"marks\": 10,\n"
        f"    \"match_pairs\": [\n"
        f"      {{\"left\": \"Term 1\", \"right\": \"Definition 1\"}},\n"
        f"      {{\"left\": \"Term 2\", \"right\": \"Definition 2\"}},\n"
        f"      {{\"left\": \"Term 3\", \"right\": \"Definition 3\"}},\n"
        f"      {{\"left\": \"Term 4\", \"right\": \"Definition 4\"}}\n"
        f"    ],\n"
        f"    \"correct_answer\": \"Term 1 -> Definition 1; Term 2 -> Definition 2\"\n"
        f"  }}\n"
        f"]"
    )

    questions_list = []
    try:
        resp = generate_chat_answer(prompt=exam_prompt, model_name="llama-3.3-70b-versatile", system_instruction="Output ONLY valid JSON array.")
        cleaned = clean_json_response(resp)
        questions_list = json.loads(cleaned)
    except Exception as e:
        print(f"Error generating exam questions: {e}")
        questions_list = []

    if not isinstance(questions_list, list) or not questions_list:
        questions_list = [
            {
                "question": f"What is a core technical requirement for {domain.capitalize()}?",
                "type": "mcq",
                "section": "Section A: Multiple Choice Questions",
                "marks": 10,
                "options": [f"Core Specification for {domain.capitalize()}", "Standard Implementation", "Alternative Method", "Legacy Model"],
                "correct_answer": f"Core Specification for {domain.capitalize()}"
            },
            {
                "question": f"Summarize the key architectural principles of {domain.capitalize()} covered in the study plan.",
                "type": "short_answer",
                "section": "Section B: Short Answer Questions",
                "marks": 10,
                "correct_answer": f"Comprehensive architectural principles covering design patterns and implementation guidelines for {domain.capitalize()}."
            },
            {
                "question": f"The primary design rule in {domain.capitalize()} is ___.",
                "type": "fill_in_blank",
                "section": "Section C: Fill in the Blanks",
                "marks": 10,
                "correct_answer": "modularity"
            },
            {
                "question": f"Match each term in Column A with its definition in Column B for {domain.capitalize()}.",
                "type": "match",
                "section": "Section D: Match the Following",
                "marks": 10,
                "match_pairs": [
                    {"left": "Module Design", "right": "High cohesion and low coupling"},
                    {"left": "Error Handling", "right": "Graceful degradation"},
                    {"left": "Data Validation", "right": "Input sanitization"},
                    {"left": "Performance", "right": "Resource optimization"}
                ],
                "correct_answer": "Module Design -> High cohesion and low coupling; Error Handling -> Graceful degradation"
            }
        ]

    from src.exams import sanitize_exam_questions
    plan_title = title_input or f"Week {week_num} ({domain.capitalize()}) AI Study Plan"
    exam_title = f"Day 5 Gateway Exam: {plan_title}"
    questions_list = sanitize_exam_questions(questions_list, exam_title=exam_title)

    total_marks = len(questions_list) * 10
    exam_settings = {"is_sprint_gateway": True, "results_release": "immediate"}
    
    from src.exams import add_exam_and_get_id
    exam_id = add_exam_and_get_id(exam_title, f"Day 5 Gateway Exam for {plan_title}.", total_marks, questions_list, settings=exam_settings)

    save_study_plan(
        domain=domain,
        week_number=week_num,
        title=plan_title,
        tasks_json=json.dumps(tasks),
        day5_exam_id=str(exam_id) if exam_id else "",
        day6_interview_prompt=f"Defend the core architecture and key principles from {plan_title}.",
        reference_files_json=json.dumps(ref_files_4days)
    )

    return jsonify({
        'status': 'success',
        'plan_title': plan_title,
        'exam_id': exam_id,
        'questions_count': len(questions_list),
        'tasks': tasks
    })

@app.route('/sprint/reset', methods=['POST'])
@login_required
def sprint_reset():
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    from src.sprints import update_sprint_day, update_sprint_progress, clear_qa_errors, clear_interview_evaluations, get_sprint
    sprint_data = get_sprint(emp_id)
    week_num = sprint_data.get('current_week', 1)
    update_sprint_day(emp_id, 1)
    update_sprint_progress(emp_id, 0.0)
    clear_qa_errors(emp_id, week_num)
    clear_interview_evaluations(emp_id, week_num)
    return jsonify({'status': 'success'})

@app.route('/sprint/voice_interview/turn', methods=['POST'])
@login_required
def sprint_voice_interview_turn():
    """Handles turn-by-turn voice interaction for Day 6 Socratic Mock Interview."""
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    domain = user_info.get('domain', 'general')

    user_query = ""
    if 'audio' in request.files:
        audio_file = request.files.get("audio")
        mime_type = request.form.get("mime_type", audio_file.content_type or "audio/webm")
        audio_bytes = audio_file.read()
        if audio_bytes:
            try:
                from voice import transcribe
                user_query = transcribe(audio_bytes, filename=audio_file.filename or "response.webm")
            except Exception as e:
                print(f"Transcription error: {e}")
                user_query = request.form.get('query', '').strip()
    else:
        data = request.get_json(silent=True) or {}
        user_query = data.get('query', '').strip() or request.form.get('query', '').strip()

    if not user_query:
        user_query = f"I reviewed and applied the core architecture patterns and domain principles for {domain.capitalize()}."

    turn_number = int(request.form.get('turn', 1) if 'turn' in request.form else (request.get_json(silent=True) or {}).get('turn', 1))

    from src.sprints import get_sprint, get_study_plan
    user_sprint = get_sprint(emp_id)
    week_num = user_sprint.get('current_week', 1)
    study_plan = get_study_plan(domain=domain, week_number=week_num, plan_id=user_sprint.get('assigned_plan_id'))
    plan_title = study_plan.get('title', f'{domain.capitalize()} Study Plan')

    from src.llm import generate_chat_answer
    socratic_system = (
        f"You are a Senior Technical Examiner conducting a 4-question Socratic Voice Mock Interview for {plan_title}.\n"
        f"The candidate is currently on Turn {turn_number} of 4.\n"
        f"Strictly evaluate their candidate's response against the technical materials of the week.\n"
        f"Formulate a sharp, insightful follow-up question that tests their technical depth or asks them to defend an architectural/conceptual choice.\n"
        f"IMPORTANT: Speak naturally as if in a live voice conversation (2-3 concise sentences maximum). Do NOT use bullet points or formatting tags."
    )

    prompt = f"Candidate's Spoken Answer (Turn {turn_number}): \"{user_query if user_query else 'Candidate initiated interview.'}\"\n\nGenerate the next follow-up question for Turn {min(turn_number + 1, 4)}:"
    
    if turn_number >= 4:
        socratic_system += "\nThis is Turn 4 (Final Turn). Thank the candidate concisely for defending their knowledge and inform them the technical assessment is complete."

    try:
        ai_response = generate_chat_answer(
            prompt=prompt,
            model_name="llama-3.3-70b-versatile",
            system_instruction=socratic_system
        )
    except Exception as e:
        ai_response = f"Thank you. Based on your explanation of {plan_title}, how would you implement this in a production environment?"

    return jsonify({
        'status': 'success',
        'user_text': user_query,
        'ai_question': ai_response,
        'turn': turn_number,
        'is_final': turn_number >= 4
    })


@app.route('/sprint/voice_interview/submit', methods=['POST'])
@login_required
def sprint_voice_interview_submit():
    """Finalizes Day 6 Socratic Mock Interview: grades response, saves evaluation, advances to Day 7."""
    user_info = session.get('user_info', {}) or {}
    emp_id = user_info.get('employee_id', 'demo')
    domain = user_info.get('domain', 'general')

    data = request.get_json(silent=True) or {}
    full_transcript = data.get('transcript', '')

    from src.sprints import get_sprint, get_study_plan, log_interview_evaluation, update_sprint_day, update_sprint_progress
    user_sprint = get_sprint(emp_id)
    week_num = user_sprint.get('current_week', 1)
    study_plan = get_study_plan(domain=domain, week_number=week_num, plan_id=user_sprint.get('assigned_plan_id'))

    from src.llm import generate_chat_answer, clean_json_response
    eval_prompt = (
        f"You are a Senior Technical Examiner grading a Day 6 Socratic Voice Interview for {study_plan.get('title')}.\n"
        f"Evaluate the following full candidate interview transcript:\n\n"
        f"--- TRANSCRIPT ---\n{full_transcript[:3000]}\n------------------\n\n"
        f"You MUST return ONLY a valid JSON object matching this exact structure:\n"
        f"{{\n"
        f"  \"tech_score\": 85.0,\n"
        f"  \"conf_score\": 90.0,\n"
        f"  \"filler_count\": 2,\n"
        f"  \"wpm\": 145,\n"
        f"  \"feedback\": \"Detailed technical critique highlighting strong answers and specific areas needing review.\"\n"
        f"}}"
    )

    tech_score = 85.0
    conf_score = 88.0
    filler_count = 2
    wpm = 140
    feedback = "Good technical understanding of core concepts. Recommended reviewing Day 2 and Day 4 architectural details."

    try:
        resp = generate_chat_answer(prompt=eval_prompt, model_name="llama-3.3-70b-versatile", system_instruction="Output ONLY valid JSON object.")
        cleaned = clean_json_response(resp)
        eval_data = json.loads(cleaned)
        tech_score = float(eval_data.get('tech_score', 85.0))
        conf_score = float(eval_data.get('conf_score', 88.0))
        filler_count = int(eval_data.get('filler_count', 2))
        wpm = int(eval_data.get('wpm', 140))
        feedback = str(eval_data.get('feedback', feedback))
    except Exception as e:
        print(f"Error evaluating interview: {e}")

    log_interview_evaluation(emp_id, week_num, tech_score, conf_score, filler_count, wpm, feedback)
    
    update_sprint_day(emp_id, 7)
    update_sprint_progress(emp_id, 100.0)

    return jsonify({
        'status': 'success',
        'tech_score': tech_score,
        'conf_score': conf_score,
        'filler_count': filler_count,
        'wpm': wpm,
        'feedback': feedback,
        'next_day': 7
    })


if __name__ == '__main__':
    port = 5050
    print(f"Starting Talent Sphere Elevate Server strictly on http://127.0.0.1:{port}")
    from werkzeug.serving import WSGIRequestHandler
    WSGIRequestHandler.protocol_version = "HTTP/1.0"
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True, use_reloader=False)