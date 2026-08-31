from dotenv import load_dotenv
load_dotenv()
import time
import concurrent.futures
import chromadb
import os
from groq import Groq

from src.config import CHROMA_HOST, CHROMA_PORT

# 1. Global Initialization Test
start_init = time.time()
try:
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    collection = chroma_client.get_or_create_collection(name="diagnostic_test")
    groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
except Exception as e:
    print(f"Initialization Failed: {e}")
init_time = time.time() - start_init
print(f"[Metrics] Global Initialization: {init_time:.4f} seconds")

def probe_chroma(query_text="test query"):
    start = time.time()
    try:
        collection.query(query_texts=[query_text], n_results=1)
    except Exception as e:
        print(f"[Fatal] Groq Error: {e}")
        return float('inf')
    return time.time() - start

def probe_groq(prompt="Reply with a single word."):
    start = time.time()
    try:
        groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant", 
            max_tokens=10
        )
    except Exception as e:
        return float('inf')
    return time.time() - start

def run_diagnostics():
    print("\n--- Running Isolated Probes ---")
    
    # Test ChromaDB I/O
    chroma_time = probe_chroma()
    print(f"[Metrics] ChromaDB Single Query: {chroma_time:.4f} seconds")
    if chroma_time > 0.5:
        print("[Alert] ChromaDB is operating too slowly for local disk I/O. Check SQLite locks or disk health.")

    # Test Groq Network Latency
    groq_time = probe_groq()
    print(f"[Metrics] Groq Single Inference: {groq_time:.4f} seconds")
    if groq_time > 1.5:
        print("[Alert] Groq API latency is high. You are severely blocking the main thread.")

    print("\n--- Running Concurrency Stress Test ---")
    # Simulating 5 simultaneous Flask requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(probe_chroma, f"query {i}") for i in range(5)]
        start_concurrent = time.time()
        concurrent.futures.wait(futures)
        total_time = time.time() - start_concurrent
        
    print(f"[Metrics] 5 Concurrent ChromaDB Reads: {total_time:.4f} seconds")
    if total_time > (chroma_time * 2.5):
        print("[Alert] Concurrency collision detected. Your database is locking under simultaneous access.")

if __name__ == "__main__":
    run_diagnostics()