import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app

print("1. Testing GET /documents as admin...", flush=True)
with app.test_request_context('/documents'):
    from flask import session
    session['user_id'] = 'demo'
    session['employee_id'] = 'demo'
    session['user_role'] = 'admin'
    session['user_info'] = {'employee_id': 'demo', 'role': 'admin'}
    session['authenticated'] = True
    from app import documents
    t0 = time.time()
    res = documents()
    print(f"1. DONE in {time.time()-t0:.3f}s", flush=True)

print("2. Testing GET /documents as trainee...", flush=True)
with app.test_request_context('/documents'):
    session['user_id'] = 'demo'
    session['employee_id'] = 'demo'
    session['user_role'] = 'trainee'
    session['user_info'] = {'employee_id': 'demo', 'role': 'trainee'}
    session['authenticated'] = True
    t0 = time.time()
    res = documents()
    print(f"2. DONE in {time.time()-t0:.3f}s", flush=True)

print("3. Testing GET /sprint as trainee...", flush=True)
with app.test_request_context('/sprint'):
    session['user_id'] = 'demo'
    session['employee_id'] = 'demo'
    session['user_role'] = 'trainee'
    session['user_info'] = {'employee_id': 'demo', 'role': 'trainee'}
    session['authenticated'] = True
    from app import sprint_page
    t0 = time.time()
    res = sprint_page()
    print(f"3. DONE in {time.time()-t0:.3f}s", flush=True)
