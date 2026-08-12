import time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

t0 = time.time()
print("Importing src.users...")
import src.users
print(f"src.users imported in {time.time()-t0:.3f}s")

t0 = time.time()
print("Importing src.exams...")
import src.exams
print(f"src.exams imported in {time.time()-t0:.3f}s")

t0 = time.time()
print("Importing src.chats...")
import src.chats
print(f"src.chats imported in {time.time()-t0:.3f}s")

t0 = time.time()
print("Importing src.vectorstore...")
import src.vectorstore
print(f"src.vectorstore imported in {time.time()-t0:.3f}s")
