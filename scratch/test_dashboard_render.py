import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, dashboard, login

print("Testing direct call to login() in test request context...", flush=True)
with app.test_request_context('/login'):
    t0 = time.time()
    try:
        res = login()
        print(f"login() completed in {time.time()-t0:.3f}s -> {type(res)}", flush=True)
    except Exception as e:
        print(f"login() error: {e}", flush=True)

print("Testing direct call to dashboard() in test request context...", flush=True)
with app.test_request_context('/'):
    from flask import session
    session['user_id'] = 'demo'
    session['employee_id'] = 'demo'
    session['user_role'] = 'admin'
    session['user_info'] = {'employee_id': 'demo', 'role': 'admin'}
    session['authenticated'] = True
    
    t0 = time.time()
    try:
        res = dashboard()
        print(f"dashboard() completed in {time.time()-t0:.3f}s -> status={getattr(res, 'status_code', 200)}", flush=True)
    except Exception as e:
        print(f"dashboard() error: {e}", flush=True)
