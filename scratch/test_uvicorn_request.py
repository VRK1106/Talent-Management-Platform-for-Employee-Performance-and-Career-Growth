import sys, time, urllib.request, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app
from a2wsgi import WSGIMiddleware
import uvicorn

asgi_app = WSGIMiddleware(app)

def run_server():
    uvicorn.run(asgi_app, host="127.0.0.1", port=8999, log_level="warning")

t = threading.Thread(target=run_server, daemon=True)
t.start()

time.sleep(2)

print("Testing HTTP request to http://127.0.0.1:8999/login...", flush=True)
t0 = time.time()
try:
    resp = urllib.request.urlopen("http://127.0.0.1:8999/login", timeout=5)
    print(f"Status Code: {resp.status}, Time: {time.time()-t0:.3f}s", flush=True)
    print("100% PERFECT A2WSGI + UVICORN SUCCESS!", flush=True)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
