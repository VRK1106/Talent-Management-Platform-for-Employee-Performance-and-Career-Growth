import time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("[1] get_all_users...", flush=True)
from src.users import get_all_users
u = get_all_users()
print(f"[1 DONE] -> count={len(u)}", flush=True)

print("[2] get_active_users_count...", flush=True)
from src.users import get_active_users_count
ac = get_active_users_count()
print(f"[2 DONE] -> {ac}", flush=True)

print("[3] get_global_chat_stats...", flush=True)
from src.chats import get_global_chat_stats
cs = get_global_chat_stats()
print(f"[3 DONE] -> {cs}", flush=True)

print("[4] get_all_exams...", flush=True)
from src.exams import get_all_exams
ex = get_all_exams()
print(f"[4 DONE] -> count={len(ex)}", flush=True)

print("[5] stats()...", flush=True)
from src.vectorstore import stats
st = stats()
print(f"[5 DONE] -> {st}", flush=True)
