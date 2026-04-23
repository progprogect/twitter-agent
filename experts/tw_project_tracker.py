# =============================================================================
# EXTELLA EXPERT: tw_project_tracker
# =============================================================================
# DESCRIPTION: Twitter Lead Agent project task tracker and execution orchestrator. Decomposes the full 10-expert build plan into trackable tasks with dependency resolution and sequential execution management. Parameters: action — init/list/next/start/done/fail/status/reset; task_id — task ID for state transitions (e.g. P1T1); notes — completion notes or error details; db_path — override default path to project SQLite DB
#
# KWARGS (default parameters):
# {
#   "action": "status",
#   "db_path": "",
#   "notes": "",
#   "task_id": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_project_tracker    # sync this file only
#   python sync_to_extella.py --all     # sync all experts
#
# RULES (see EXPERT_RULES.md):
# - $extens("include.py") must be the first code line
# - Dependencies via: include("import X", ["extella-pip install X"])
# - Function name = filename without .py
# - All params with type hints + default values -> dict
# - NO hardcoded credentials, paths, or personal data
# =============================================================================

$extens("include.py")
include("import sqlite3", [])

def tw_project_tracker(
    action: str = "status",
    task_id: str = "",
    notes: str = "",
    db_path: str = ""
) -> dict:
    import sqlite3
    import json
    from pathlib import Path
    from datetime import datetime

    print(f"[1/4] 🔄 tw_project_tracker: action={action}")

    if not db_path:
        db_path = str(Path.home() / "Documents" / "twitter_agent" / "project_tasks.db")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           TEXT PRIMARY KEY,
            phase        INTEGER NOT NULL,
            seq          INTEGER NOT NULL,
            name         TEXT NOT NULL,
            expert_name  TEXT NOT NULL,
            description  TEXT,
            dependencies TEXT DEFAULT '[]',
            status       TEXT DEFAULT 'todo',
            started_at   TEXT,
            completed_at TEXT,
            notes        TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT,
            action     TEXT,
            result     TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # Full task manifest — 10 experts, 5 phases
    TASKS = [
        # Phase 1 — Foundation
        ("P1T1", 1, 1, "Database Layer",
         "tw_data",
         "SQLite init, full schema (9 tables), WAL mode, migrations, backup, export_schema, KV path storage",
         "[]"),
        ("P1T2", 1, 2, "Auth & Account Manager",
         "tw_auth",
         "GoLogin+direct auth, KV-encrypted tokens, circuit breaker, health score, account pool",
         '["P1T1"]'),
        ("P1T3", 1, 3, "Web Server & SPA",
         "tw_server",
         "FastAPI server lifecycle, SPA HTML generation, lock file, browser open, SSE progress stream",
         '["P1T1","P1T2"]'),
        # Phase 2 — Discovery
        ("P2T1", 2, 1, "Profile Discovery Pipeline",
         "tw_discover",
         "twscrape search+enrich+KMeans tier — all 3 sub-phases in one pipeline expert",
         '["P1T1","P1T2"]'),
        ("P2T2", 2, 2, "Posts Fetcher",
         "tw_posts",
         "twscrape user_tweets, topic keyword filter, dedup check, store in posts table",
         '["P1T1","P1T2","P2T1"]'),
        # Phase 3 — AI & Queue
        ("P3T1", 3, 1, "AI Reply Generator",
         "tw_generate",
         "single+followup modes, langdetect, multi-provider fallback chain, structured prompt",
         '["P1T1"]'),
        ("P3T2", 3, 2, "Task Queue Manager",
         "tw_queue",
         "CRUD queue, FSM status transitions, duplicate protection, bulk approve/reject",
         '["P1T1","P3T1"]'),
        # Phase 4 — Posting
        ("P4T1", 4, 1, "Reply Poster",
         "tw_post",
         "Playwright + GoLogin CDP posting, human-like delays, rate limiting, dry-run screenshot",
         '["P1T1","P1T2","P3T2"]'),
        # Phase 5 — Monitoring & Orchestration
        ("P5T1", 5, 1, "Monitor & Analytics",
         "tw_monitor",
         "Conversation polling, followup trigger, analytics aggregation, export CSV",
         '["P1T1","P1T2","P4T1"]'),
        ("P5T2", 5, 2, "Master Agent Orchestrator",
         "tw_agent",
         "State Machine (9 states), resume-on-restart, background daemon, watchdog, onboarding wizard",
         '["P1T1","P1T2","P1T3","P2T1","P2T2","P3T1","P3T2","P4T1","P5T1"]'),
    ]

    if action == "init":
        cur.executemany("""
            INSERT OR IGNORE INTO tasks
              (id, phase, seq, name, expert_name, description, dependencies)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, TASKS)
        conn.commit()
        count = cur.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        print(f"[2/4] ✅ Initialized {count} tasks across 5 phases")
        print("[3/4] 📋 Task manifest loaded")
        print("[4/4] ✅ Ready to execute")
        return {
            "status": "success",
            "tasks_count": count,
            "phases": 5,
            "db_path": db_path,
            "message": f"Project initialized: {count} tasks ready"
        }

    elif action == "list":
        cur.execute("SELECT * FROM tasks ORDER BY phase, seq")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        by_phase = {}
        for r in rows:
            key = f"Phase {r['phase']}"
            by_phase.setdefault(key, []).append({
                "id": r["id"],
                "expert": r["expert_name"],
                "name": r["name"],
                "status": r["status"]
            })
        print(f"[2/4] 📋 {len(rows)} tasks found")
        print("[3/4] 🗂️  Grouped by phase")
        print("[4/4] ✅ Done")
        return {"status": "success", "total": len(rows), "by_phase": by_phase}

    elif action == "next":
        cur.execute("SELECT * FROM tasks WHERE status='todo' ORDER BY phase, seq")
        for row in cur.fetchall():
            deps = json.loads(row["dependencies"])
            if not deps:
                conn.close()
                print(f"[2/4] ➡️  Next task: {row['id']} — {row['name']}")
                print("[3/4] ✅ No blockers")
                print("[4/4] ✅ Ready")
                return {"status": "success", "next_task": dict(row)}
            unmet = cur.execute(
                f"SELECT COUNT(*) FROM tasks WHERE id IN ({','.join(['?']*len(deps))}) AND status != 'done'",
                deps
            ).fetchone()[0]
            if unmet == 0:
                conn.close()
                print(f"[2/4] ➡️  Next task: {row['id']} — {row['name']}")
                print(f"[3/4] ✅ All {len(deps)} dependencies done")
                print("[4/4] ✅ Ready to execute")
                return {"status": "success", "next_task": dict(row)}
        conn.close()
        print("[2/4] 🎉 No pending tasks!")
        print("[3/4] ✅ All done or blocked")
        print("[4/4] ✅ Done")
        return {"status": "success", "next_task": None, "message": "All tasks complete or blocked by unmet deps"}

    elif action == "start":
        if not task_id:
            conn.close()
            return {"status": "error", "message": "task_id required"}
        cur.execute("UPDATE tasks SET status='in_progress', started_at=datetime('now') WHERE id=?", (task_id,))
        conn.commit()
        conn.close()
        print(f"[2/4] 🔄 Task {task_id} → in_progress")
        print("[3/4] ⏱️  Timer started")
        print("[4/4] ✅ Done")
        return {"status": "success", "task_id": task_id, "new_status": "in_progress"}

    elif action == "done":
        if not task_id:
            conn.close()
            return {"status": "error", "message": "task_id required"}
        cur.execute(
            "UPDATE tasks SET status='done', completed_at=datetime('now'), notes=? WHERE id=?",
            (notes, task_id)
        )
        cur.execute("INSERT INTO runs (task_id, action, result) VALUES (?, 'done', ?)", (task_id, notes))
        conn.commit()
        remaining = cur.execute("SELECT COUNT(*) FROM tasks WHERE status != 'done'").fetchone()[0]
        conn.close()
        print(f"[2/4] ✅ Task {task_id} → done")
        print(f"[3/4] 📊 {remaining} tasks remaining")
        print("[4/4] ✅ Done")
        return {"status": "success", "task_id": task_id, "new_status": "done", "remaining": remaining}

    elif action == "fail":
        if not task_id:
            conn.close()
            return {"status": "error", "message": "task_id required"}
        cur.execute(
            "UPDATE tasks SET status='failed', notes=? WHERE id=?",
            (notes, task_id)
        )
        cur.execute("INSERT INTO runs (task_id, action, result) VALUES (?, 'fail', ?)", (task_id, notes))
        conn.commit()
        conn.close()
        print(f"[2/4] ❌ Task {task_id} → failed")
        print(f"[3/4] 📝 Notes: {notes[:80] if notes else 'none'}")
        print("[4/4] ✅ Status saved")
        return {"status": "success", "task_id": task_id, "new_status": "failed"}

    elif action == "status":
        cur.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status")
        breakdown = {r["status"]: r["cnt"] for r in cur.fetchall()}
        total = sum(breakdown.values())
        done = breakdown.get("done", 0)
        pct = round(done / total * 100) if total else 0
        cur.execute("SELECT * FROM tasks WHERE status IN ('in_progress','failed') ORDER BY phase, seq")
        active = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM tasks WHERE status='done' ORDER BY completed_at")
        completed = [{"id": r["id"], "name": r["name"], "expert": r["expert_name"]} for r in cur.fetchall()]
        conn.close()
        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        print(f"[2/4] 📊 Progress: [{bar}] {pct}% ({done}/{total})")
        print(f"[3/4] ✅ Done: {done} | 🔄 Active: {len(active)} | ❌ Failed: {breakdown.get('failed',0)}")
        print("[4/4] ✅ Status retrieved")
        return {
            "status": "success",
            "breakdown": breakdown,
            "progress_pct": pct,
            "progress_bar": f"[{bar}] {pct}%",
            "active_tasks": active,
            "completed_tasks": completed
        }

    elif action == "reset":
        cur.execute("UPDATE tasks SET status='todo', started_at=NULL, completed_at=NULL, notes=NULL")
        conn.commit()
        conn.close()
        print("[2/4] 🔄 All tasks reset to todo")
        print("[3/4] ⚠️  Progress cleared")
        print("[4/4] ✅ Done")
        return {"status": "success", "message": "All tasks reset to todo"}

    conn.close()
    return {"status": "error", "message": f"Unknown action: {action}. Use: init/list/next/start/done/fail/status/reset"}
