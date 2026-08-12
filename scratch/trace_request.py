import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app

print("Testing Flask app request context for /dashboard...")
with app.test_client() as client:
    with client.session_transaction() as sess:
        sess['authenticated'] = True
        sess['user_role'] = 'admin'
        sess['current_user'] = 'Admin User'
        sess['user_info'] = {'employee_id': 'admin01'}
    
    print("Sending GET / request...")
    res = client.get('/')
    print("Response Status Code:", res.status_code)
    print("Response Data Length:", len(res.data))
    print("SUCCESS!")
