import time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.users import get_all_users, get_active_users_count
from src.exams import get_all_exams, get_assignments_for_exam
from src.chats import get_global_chat_stats
from src.vectorstore import stats

print("[1] get_all_users...")
t0 = time.time()
u = get_all_users()
print(f"[1 DONE] in {time.time()-t0:.3f}s -> count={len(u)}")

print("[2] get_active_users_count...")
t0 = time.time()
ac = get_active_users_count()
print(f"[2 DONE] in {time.time()-t0:.3f}s -> {ac}")

print("[3] get_global_chat_stats...")
t0 = time.time()
cs = get_global_chat_stats()
print(f"[3 DONE] in {time.time()-t0:.3f}s -> {cs}")

print("[4] get_all_exams...")
t0 = time.time()
ex = get_all_exams()
print(f"[4 DONE] in {time.time()-t0:.3f}s -> count={len(ex)}")

print("[5] stats()...")
t0 = time.time()
st = stats()
print(f"[5 DONE] in {time.time()-t0:.3f}s -> {st}")
