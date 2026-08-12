import sys, time, urllib.request, threading, uvicorn
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app
from a2wsgi import WSGIMiddleware

asgi = WSGIMiddleware(app)

def run():
    uvicorn.run(asgi, host="127.0.0.1", port=9998, log_level="debug")

t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(2)

print("Sending GET /login...", flush=True)
try:
    res = urllib.request.urlopen("http://127.0.0.1:9998/login", timeout=5)
    print("Status:", res.status)
except Exception as e:
    print("Error:", e)
