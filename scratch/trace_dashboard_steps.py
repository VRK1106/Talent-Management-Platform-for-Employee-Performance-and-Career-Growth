import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("[STEP 1] Importing src modules...")
t0 = time.time()
from src.users import get_all_users, get_active_users_count, get_db_connection, _DB_PATH
from src.exams import get_all_exams, get_assignments_for_exam
from src.chats import get_global_chat_stats
from src.vectorstore import stats
print(f"[STEP 1 DONE] in {time.time()-t0:.3f}s")

print("[STEP 2] Testing stats()...")
t0 = time.time()
s = stats()
print(f"[STEP 2 DONE] in {time.time()-t0:.3f}s -> {s}")

print("[STEP 3] Testing get_all_users()...")
t0 = time.time()
u = get_all_users()
print(f"[STEP 3 DONE] in {time.time()-t0:.3f}s -> count={len(u)}")

print("[STEP 4] Testing get_active_users_count()...")
t0 = time.time()
ac = get_active_users_count()
print(f"[STEP 4 DONE] in {time.time()-t0:.3f}s -> {ac}")

print("[STEP 5] Testing get_global_chat_stats()...")
t0 = time.time()
cs = get_global_chat_stats()
print(f"[STEP 5 DONE] in {time.time()-t0:.3f}s -> {cs}")

print("[STEP 6] Testing get_all_exams()...")
t0 = time.time()
ex = get_all_exams()
print(f"[STEP 6 DONE] in {time.time()-t0:.3f}s -> count={len(ex)}")

print("[ALL DASHBOARD STEPS COMPLETE!]")
