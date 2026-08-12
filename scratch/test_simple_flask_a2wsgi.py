import time, urllib.request, threading, uvicorn
from flask import Flask
from a2wsgi import WSGIMiddleware

simple_app = Flask(__name__)

@simple_app.route('/login')
def login():
    return "LOGIN PAGE OK"

asgi = WSGIMiddleware(simple_app)

def run():
    uvicorn.run(asgi, host="127.0.0.1", port=9999, log_level="warning")

t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(1.5)

print("Testing simple Flask app via a2wsgi...", flush=True)
res = urllib.request.urlopen("http://127.0.0.1:9999/login")
print("Response:", res.read().decode('utf-8'), flush=True)
