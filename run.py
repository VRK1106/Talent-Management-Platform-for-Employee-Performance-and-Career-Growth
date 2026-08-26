import subprocess
import sys
import time
import socket
import signal

def is_port_open(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0

def main():
    print("[Launcher] Booting ChromaDB server on 127.0.0.1:8001...")
    
    chroma_proc = subprocess.Popen(
        ["chroma", "run", "--path", "./chroma_db", "--host", "127.0.0.1", "--port", "8001"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )

    # Update the while loop port check
    attempts = 0
    while not is_port_open(8001):
        time.sleep(1.0)
        attempts += 1
        if attempts > 20:
            print("[Fatal] ChromaDB failed to bind to 127.0.0.1:8001 after 20 seconds.")
            chroma_proc.send_signal(signal.CTRL_BREAK_EVENT)
            sys.exit(1)

    print("[Launcher] ChromaDB is verified ready. Starting Flask...")

    # 3. Start Flask App
    # 3. Start Flask App with unbuffered logs
    try:
        flask_proc = subprocess.Popen([sys.executable, "-u", "app.py"])
        flask_proc.wait()
    except KeyboardInterrupt:
        print("\n[Launcher] Shutting down services...")
    finally:
        # Forcefully terminate the Flask process first
        print("[Launcher] Terminating Flask...")
        if 'flask_proc' in locals():
            flask_proc.terminate()
            flask_proc.wait()

        # Send break signal to the isolated ChromaDB process group
        print("[Launcher] Terminating ChromaDB...")
        if 'chroma_proc' in locals():
            chroma_proc.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                chroma_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                chroma_proc.kill()
                
        print("[Launcher] All processes terminated cleanly.")

if __name__ == "__main__":
    main()