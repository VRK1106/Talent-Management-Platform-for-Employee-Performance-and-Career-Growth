import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app
from flask import session

with app.test_request_context('/'):
    session['user_id'] = 'demo'
    session['employee_id'] = 'demo'
    session['user_role'] = 'admin'
    session['user_info'] = {'employee_id': 'demo', 'role': 'admin'}
    session['authenticated'] = True
    
    print("1. stats()...", flush=True)
    from src.vectorstore import stats
    t0 = time.time()
    s = stats()
    print(f"1. DONE in {time.time()-t0:.3f}s", flush=True)
    
    print("2. get_all_users()...", flush=True)
    from src.users import get_all_users, get_active_users_count
    t0 = time.time()
    users = get_all_users()
    trainees = [u for u in users if u["role"] == "trainee"]
    print(f"2. DONE in {time.time()-t0:.3f}s", flush=True)
    
    print("3. get_active_users_count()...", flush=True)
    t0 = time.time()
    active_now = get_active_users_count(hours=1)
    print(f"3. DONE in {time.time()-t0:.3f}s", flush=True)
    
    print("4. get_global_chat_stats()...", flush=True)
    from src.chats import get_global_chat_stats
    t0 = time.time()
    chat_stats = get_global_chat_stats()
    print(f"4. DONE in {time.time()-t0:.3f}s", flush=True)
    
    print("5. get_all_exams()...", flush=True)
    from src.exams import get_all_exams, get_assignments_for_exam
    t0 = time.time()
    exams_list = get_all_exams()
    all_submissions = []
    for e in exams_list:
        all_submissions.extend(get_assignments_for_exam(e["exam_id"]))
    print(f"5. DONE in {time.time()-t0:.3f}s", flush=True)

    print("6. log_stats...", flush=True)
    from app import get_log_analytics
    t0 = time.time()
    log_stats = get_log_analytics()
    print(f"6. DONE in {time.time()-t0:.3f}s", flush=True)

    print("7. get_system_setting...", flush=True)
    from src.exams import get_system_setting
    t0 = time.time()
    email_enabled = get_system_setting("email_notifications_enabled", "true")
    print(f"7. DONE in {time.time()-t0:.3f}s", flush=True)

    print("8. sprints & evaluations...", flush=True)
    from src.sprints import get_all_sprint_schedules, get_all_interview_evaluations
    t0 = time.time()
    sprints_list = get_all_sprint_schedules()
    evaluations_list = get_all_interview_evaluations()
    print(f"8. DONE in {time.time()-t0:.3f}s", flush=True)
