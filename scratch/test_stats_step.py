import time, chromadb, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import CHROMA_COLLECTION, CHROMA_DB_PATH

print("1. Creating PersistentClient...", flush=True)
t0 = time.time()
client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
print(f"1. DONE in {time.time()-t0:.3f}s", flush=True)

print("2. Getting collection...", flush=True)
t0 = time.time()
coll = client.get_or_create_collection(name=CHROMA_COLLECTION, metadata={"hnsw:space": "cosine"})
print(f"2. DONE in {time.time()-t0:.3f}s", flush=True)

print("3. Counting collection...", flush=True)
t0 = time.time()
cnt = coll.count()
print(f"3. DONE in {time.time()-t0:.3f}s -> count={cnt}", flush=True)

print("4. Fetching metadatas...", flush=True)
t0 = time.time()
res = coll.get(include=["metadatas"])
print(f"4. DONE in {time.time()-t0:.3f}s -> count={len(res.get('ids', []))}", flush=True)
