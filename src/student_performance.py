"""
student_performance.py
Detects performance-related queries and fetches real data from the SQLite database
to provide accurate, data-grounded answers via the AI coach.
"""

import re
import sqlite3
import datetime
from pathlib import Path
from src.users import get_db_connection

_DB_PATH = Path(__file__).resolve().parent.parent / "users.db"

# ─── Keywords that signal a data/performance question ───
_PERF_KEYWORDS = {
    "score", "scores", "grade", "grades", "mark", "marks", "performance",
    "progress", "result", "results", "doing", "report", "analytics",
    "statistic", "statistics", "stats", "percentage", "pass", "fail",
    "weak", "strength", "improve", "improvement", "rank", "average",
    "avg", "overview", "summary", "exam", "exams", "test", "quiz",
    "assignment", "assignments", "completed", "pending", "topic",
    "topics", "suggest", "recommendation", "recommend", "insight",
    "insights", "info", "detail", "details"
}

# Aggregate / admin signals — no specific person, refers to ALL trainees
_AGGREGATE_SIGNALS = {
    "all trainees", "all students", "all users", "trainees", "students",
    "every trainee", "every student", "the trainees", "the students",
    "each trainee", "each student", "trainee performance", "student performance",
    "trainee scores", "student scores", "class performance", "class scores",
    "overall performance", "team performance", "everyone", "everybody",
}


def _get_all_trainee_names_and_ids() -> list[tuple[str, str]]:
    """Return list of (full_name, employee_id) for all trainees."""
    try:
        conn = get_db_connection(_DB_PATH)
        rows = conn.execute(
            "SELECT full_name, employee_id FROM users WHERE role != 'admin'"
        ).fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception:
        return []


def detect_performance_query(query: str) -> str | None:
    """
    Detect whether the query is asking about performance data.
    Returns:
        'self'       – user is asking about their own performance
        'ALL'        – admin-level aggregate query about all trainees
        '<name/id>'  – asking about a specific trainee (name fragment or emp_id)
        None         – not a performance/data query
    """
    q = query.strip()
    q_lower = q.lower()

    # ── 1. Self-query ──
    self_patterns = [
        r"\b(my|own)\b.{0,40}\b(score|grade|mark|progress|performance|report|result|stat|analytic|average|exam|assignment)\b",
        r"\b(how am i|how i am|how have i been)\b.{0,30}\b(doing|performing|progressing|scoring)\b",
        r"\b(what is|what are|show me|tell me|get)\b.{0,20}\bmy\b.{0,20}\b(score|grade|mark|progress|report|result)\b",
        r"\bam i\b.{0,30}\b(passing|failing|doing|performing)\b",
    ]
    for p in self_patterns:
        if re.search(p, q_lower):
            return "self"

    # ── 2. Aggregate / all-trainee ──
    for signal in _AGGREGATE_SIGNALS:
        if signal in q_lower:
            # must also contain a performance keyword or "suggest" / "topic"
            words = set(q_lower.split())
            if words & _PERF_KEYWORDS:
                return "ALL"

    # ── 3. Specific trainee by name or ID ──
    # Check whether any real trainee name/id fragment appears in the query
    trainees = _get_all_trainee_names_and_ids()
    q_words = re.findall(r"\b\w+\b", q_lower)

    # Check employee IDs directly
    for full_name, emp_id in trainees:
        if emp_id.lower() in q_lower:
            return emp_id

    # Check name fragments (first name or full name)
    for full_name, emp_id in trainees:
        parts = [p.lower() for p in full_name.split() if len(p) > 1]
        for part in parts:
            if part in q_words:
                # Also require a perf keyword in the query
                if set(q_words) & _PERF_KEYWORDS:
                    return emp_id

    # ── 4. Regex fallback for explicit "of/for <name>" patterns ──
    pattern_specific = [
        r"\b(?:performance|score|scores|progress|grade|grades|result|results|report|stats|marks?|average|avg|insights?|analytics|info|details?)\b.{0,30}\b(?:of|for|about)\b\s+(?:student|trainee|user)?\s*([A-Za-z][A-Za-z0-9_\-]{1,})",
        r"\bhow\s+(?:is|was|has)\s+(?:student|trainee|user)?\s*([A-Za-z][A-Za-z0-9_\-]{1,})\s+(?:doing|performing|progressing|scoring)\b",
        r"\bshow\s+(?:me\s+)?(?:student|trainee|user)?\s*([A-Za-z][A-Za-z0-9_\-]{1,})['\u2019s]*\s*(?:performance|score|scores|progress|grade|report|stats|result)\b",
        r"\b(?:improve|improving|improvement)\b.{0,20}([A-Za-z][A-Za-z0-9_\-]{1,})['\u2019s]?\s*(?:score|performance|grade|marks?|result)\b",
        r"\b([A-Za-z][A-Za-z0-9_\-]{1,})['\u2019s]\s+(?:score|performance|grade|marks?|result|progress|stats|average)\b",
    ]
    IGNORE = {"the","a","an","this","my","your","their","our","its","each","every",
               "all","some","no","any","such","both","that","these","those","which",
               "who","whom","what","when","how","why","student","trainee","user",
               "to","of","for","in","on","at","by","with","from","or","and","but"}

    for pat in pattern_specific:
        m = re.search(pat, q_lower)
        if m:
            candidate = m.group(1).strip()
            if candidate not in IGNORE and len(candidate) > 1:
                return candidate

    return None


# ─────────────────────────────────────────────────────────────────────────────

def get_trainee_log_analytics(emp_id: str) -> dict:
    """Query activity logs for a student and calculate estimated study hours."""
    conn = get_db_connection(_DB_PATH)
    analytics = {"total_activities": 0, "study_hours": 0.0, "avg_session_mins": 0.0}
    try:
        analytics["total_activities"] = conn.execute(
            "SELECT COUNT(*) FROM activity_logs WHERE employee_id = ?", (emp_id,)
        ).fetchone()[0]

        rows = conn.execute(
            "SELECT timestamp FROM activity_logs WHERE employee_id = ? ORDER BY timestamp ASC",
            (emp_id,)
        ).fetchall()
        timestamps = []
        for (ts_str,) in rows:
            if ts_str:
                try:
                    timestamps.append(datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    pass

        if timestamps:
            sessions, cur = [], [timestamps[0]]
            for ts in timestamps[1:]:
                if (ts - cur[-1]).total_seconds() / 60.0 <= 30.0:
                    cur.append(ts)
                else:
                    sessions.append(cur); cur = [ts]
            sessions.append(cur)
            total_mins = sum(
                max((s[-1] - s[0]).total_seconds() / 60.0, 5.0) for s in sessions
            )
            analytics["study_hours"] = round(total_mins / 60.0, 1)
            analytics["avg_session_mins"] = round(total_mins / len(sessions), 1)
    except Exception as e:
        print(f"[perf] log analytics error: {e}")
    finally:
        conn.close()
    return analytics


def get_student_performance_context(identifier: str, requester_role: str, requester_id: str) -> str:
    """
    Fetch individual trainee profile + exams + analytics.
    Returns a formatted context string for the LLM.
    """
    conn = get_db_connection(_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    try:
        # ── Resolve employee_id ──
        target_emp_id = None
        if identifier == "self":
            target_emp_id = requester_id
        else:
            # Direct ID match
            row = c.execute("SELECT employee_id FROM users WHERE employee_id = ?", (identifier,)).fetchone()
            if row:
                target_emp_id = row["employee_id"]
            else:
                # Name fragment (case-insensitive)
                row = c.execute(
                    "SELECT employee_id FROM users WHERE LOWER(full_name) LIKE ?",
                    (f"%{identifier.lower()}%",)
                ).fetchone()
                if row:
                    target_emp_id = row["employee_id"]

        if not target_emp_id:
            conn.close()
            return f"No trainee matching '{identifier}' was found in the platform database."

        # ── Authorization ──
        if requester_role != "admin" and target_emp_id != requester_id:
            conn.close()
            return "Unauthorized. You can only view your own performance data."

        # ── Profile ──
        profile = c.execute(
            "SELECT employee_id, full_name, email, domain, last_active, role FROM users WHERE employee_id = ?",
            (target_emp_id,)
        ).fetchone()
        if not profile or profile["role"] == "admin":
            conn.close()
            return "The requested user is either not registered or is an administrator."

        # ── Assignments / Exams ──
        assignments = [
            dict(r) for r in c.execute(
                """SELECT a.score, a.status, a.ai_feedback, a.completed_at,
                          e.title, e.total_marks
                   FROM assignments a
                   JOIN exams e ON a.exam_id = e.exam_id
                   WHERE a.trainee_id = ?""",
                (target_emp_id,)
            ).fetchall()
        ]

        # ── Proctoring (admin only) ──
        proctor_logs = []
        if requester_role == "admin":
            proctor_logs = [
                dict(r) for r in c.execute(
                    """SELECT pl.trigger_reason, pl.groq_label, pl.timestamp, e.title
                       FROM proctor_logs pl
                       JOIN assignments a ON pl.assignment_id = a.assignment_id
                       JOIN exams e ON a.exam_id = e.exam_id
                       WHERE a.trainee_id = ?""",
                    (target_emp_id,)
                ).fetchall()
            ]

        log_stats = get_trainee_log_analytics(target_emp_id)

        # ── Build context ──
        lines = [
            f"=== TRAINEE PROFILE: {profile['full_name']} (ID: {profile['employee_id']}) ===",
            f"Name          : {profile['full_name']}",
            f"Email         : {profile['email']}",
            f"Specialization: {(profile['domain'] or 'General').upper()}",
            f"Last Active   : {profile['last_active'] or 'Never'}",
            "",
            "=== STUDY HABIT ANALYTICS ===",
            f"Total Platform Actions       : {log_stats['total_activities']}",
            f"Estimated Total Study Hours  : {log_stats['study_hours']} hrs",
            f"Avg Session Duration         : {log_stats['avg_session_mins']} min",
            "",
            "=== EXAM & ASSIGNMENT RECORD ===",
        ]

        completed = [a for a in assignments if a["status"] == "completed"]
        pending   = [a for a in assignments if a["status"] != "completed"]
        lines += [
            f"Total Assigned: {len(assignments)}  |  Completed: {len(completed)}  |  Pending: {len(pending)}",
        ]

        if completed:
            scores = [a["score"] for a in completed if a["score"] is not None]
            totals = [a["total_marks"] for a in completed if a["total_marks"]]
            overall_avg = (sum(scores) / len(scores)) if scores else 0
            lines.append(f"Overall Average Score: {overall_avg:.1f} / {totals[0] if totals else '?'}"
                         f"  ({overall_avg / totals[0] * 100:.1f}%)" if totals else "")
            lines.append("\nCompleted Exams:")
            for a in completed:
                pct = (a["score"] / a["total_marks"] * 100) if a["total_marks"] else 0
                status_emoji = "✅" if pct >= 60 else "⚠️"
                lines.append(
                    f"  {status_emoji} {a['title']}: {a['score']}/{a['total_marks']} ({pct:.1f}%)"
                    f"  [Completed: {a['completed_at'] or 'N/A'}]"
                )
                if a.get("ai_feedback"):
                    lines.append(f"     AI Feedback: {a['ai_feedback'][:200]}")
        else:
            lines.append("No completed exams yet.")

        if pending:
            lines.append("\nPending Exams:")
            for a in pending:
                lines.append(f"  🕐 {a['title']} (not yet completed)")

        if requester_role == "admin":
            lines.append("\n=== INTEGRITY & PROCTORING LOGS ===")
            if proctor_logs:
                lines.append(f"Total Flagged Events: {len(proctor_logs)}")
                for log in proctor_logs:
                    lines.append(
                        f"  ⚑ [{log['timestamp']}] Exam '{log['title']}': "
                        f"{log['trigger_reason']} (AI label: {log['groq_label']})"
                    )
            else:
                lines.append("No integrity violations recorded.")

        conn.close()
        return "\n".join(lines)

    except Exception as e:
        if conn:
            conn.close()
        return f"Error retrieving performance data: {e}"


def get_aggregate_performance_context(requester_role: str) -> str:
    """
    Returns an aggregate summary of ALL trainees — for admin queries like
    'suggest topics based on trainee exam scores' or 'who is struggling'.
    Only accessible by admins.
    """
    if requester_role != "admin":
        return "Unauthorized. Aggregate performance data is only available to administrators."

    conn = get_db_connection(_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    try:
        trainees = c.execute(
            "SELECT employee_id, full_name, domain FROM users WHERE role != 'admin'"
        ).fetchall()

        lines = [
            "=== PLATFORM-WIDE AGGREGATE PERFORMANCE REPORT ===",
            f"Total Registered Trainees: {len(trainees)}",
            "",
        ]

        # Per-trainee summary
        trainee_rows = []
        exam_topic_scores: dict[str, list[float]] = {}  # exam_title -> list of pct scores

        for t in trainees:
            emp_id   = t["employee_id"]
            name     = t["full_name"]
            domain   = (t["domain"] or "General").upper()

            assignments = c.execute(
                """SELECT a.score, a.status, e.title, e.total_marks
                   FROM assignments a
                   JOIN exams e ON a.exam_id = e.exam_id
                   WHERE a.trainee_id = ?""",
                (emp_id,)
            ).fetchall()

            completed   = [a for a in assignments if a["status"] == "completed" and a["score"] is not None]
            total_count = len(assignments)
            done_count  = len(completed)

            if completed:
                pcts = [(a["score"] / a["total_marks"] * 100) for a in completed if a["total_marks"]]
                avg_pct = sum(pcts) / len(pcts) if pcts else 0
                # accumulate per-topic
                for a in completed:
                    if a["total_marks"]:
                        pct = a["score"] / a["total_marks"] * 100
                        exam_topic_scores.setdefault(a["title"], []).append(pct)
            else:
                avg_pct = None

            trainee_rows.append({
                "name"      : name,
                "domain"    : domain,
                "total"     : total_count,
                "done"      : done_count,
                "avg_pct"   : avg_pct,
            })

        # Sort by avg_pct ascending (struggling first)
        trainee_rows.sort(key=lambda r: (r["avg_pct"] is None, r["avg_pct"] if r["avg_pct"] is not None else -1))

        lines.append("=== INDIVIDUAL TRAINEE PERFORMANCE SUMMARY ===")
        lines.append(f"{'Trainee':<25} {'Domain':<15} {'Assigned':>8} {'Completed':>9} {'Avg Score':>9}")
        lines.append("-" * 72)
        for r in trainee_rows:
            avg_str = f"{r['avg_pct']:.1f}%" if r["avg_pct"] is not None else "N/A"
            emoji   = "🔴" if r["avg_pct"] is not None and r["avg_pct"] < 60 else (
                      "🟡" if r["avg_pct"] is not None and r["avg_pct"] < 75 else "🟢")
            if r["avg_pct"] is None:
                emoji = "⚪"
            lines.append(f"  {emoji} {r['name']:<23} {r['domain']:<15} {r['total']:>8} {r['done']:>9} {avg_str:>9}")

        # Per-exam topic averages
        if exam_topic_scores:
            lines.append("")
            lines.append("=== PER-EXAM TOPIC AVERAGE SCORES ===")
            topic_avgs = {
                title: sum(scores) / len(scores)
                for title, scores in exam_topic_scores.items()
            }
            for title, avg in sorted(topic_avgs.items(), key=lambda x: x[1]):
                bar_len  = int(avg / 5)
                bar      = "█" * bar_len + "░" * (20 - bar_len)
                flag     = "⚠️ WEAK" if avg < 60 else ("OK" if avg < 75 else "✅ STRONG")
                lines.append(f"  {title:<30} {avg:5.1f}%  [{bar}]  {flag}")

        # Struggling trainees (< 60%)
        struggling = [r for r in trainee_rows if r["avg_pct"] is not None and r["avg_pct"] < 60]
        if struggling:
            lines.append("")
            lines.append("=== TRAINEES NEEDING IMMEDIATE ATTENTION (avg < 60%) ===")
            for r in struggling:
                lines.append(f"  🔴 {r['name']} ({r['domain']}) — avg {r['avg_pct']:.1f}%")

        # Topic recommendations
        weak_topics = [t for t, a in topic_avgs.items() if a < 70] if exam_topic_scores else []
        if weak_topics:
            lines.append("")
            lines.append("=== RECOMMENDED TOPICS FOR REMEDIAL EXAMS ===")
            for topic in weak_topics:
                avg = topic_avgs[topic]
                lines.append(f"  📌 '{topic}' — class avg {avg:.1f}% (below 70% threshold)")

        conn.close()
        return "\n".join(lines)

    except Exception as e:
        if conn:
            conn.close()
        return f"Error generating aggregate performance report: {e}"
