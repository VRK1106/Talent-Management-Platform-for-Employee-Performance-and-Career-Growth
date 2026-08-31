import os
import sys
from pathlib import Path

# Add workspace to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, _in_memory_tab_sessions, get_db_connection, _SESSIONS_DB

def test_logout():
    with app.test_client() as client:
        # Simulate active tab session
        tab_id = "test_tab_logout_123"
        _in_memory_tab_sessions[tab_id] = {"authenticated": True, "user_role": "admin", "current_user": "Admin User"}
        
        conn = get_db_connection(_SESSIONS_DB)
        conn.execute("INSERT OR REPLACE INTO tab_sessions (tab_id, data) VALUES (?, ?)", (tab_id, '{"authenticated": true}'))
        conn.commit()
        conn.close()

        print(f"Before Logout: in_memory keys count = {len(_in_memory_tab_sessions)}")
        assert tab_id in _in_memory_tab_sessions, "Test tab_id should be in memory"

        # Hit /logout with tab_id
        res = client.get(f'/logout?tab_id={tab_id}')
        
        print(f"Logout Status Code: {res.status_code}")
        print(f"Logout Location Header: {res.headers.get('Location')}")
        
        # Verify in memory is cleared
        assert tab_id not in _in_memory_tab_sessions, "Test tab_id should be removed from in-memory cache"
        
        # Verify DB is cleared
        conn = get_db_connection(_SESSIONS_DB)
        row = conn.execute("SELECT data FROM tab_sessions WHERE tab_id = ?", (tab_id,)).fetchone()
        conn.close()
        assert row is None, "Test tab_id should be deleted from SQLite tab_sessions table"
        
        print("[SUCCESS] Logout test passed completely!")

if __name__ == '__main__':
    test_logout()
