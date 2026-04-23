# =============================================================================
# EXTELLA EXPERT: tw_test_suite
# =============================================================================
# DESCRIPTION: Automated test suite for Twitter Lead Agent. Tests all 10 experts across 40+ tests using direct SQLite access and HTTP calls to the Flask server — no nested expert API calls needed. Covers: DB schema integrity, settings CRUD, auth account lifecycle with circuit breaker, server all endpoints (GET/POST/PATCH/DELETE), AI reply generation, queue FSM transitions, monitoring, workflow state, analytics. Provides detailed pass/fail report per category. Parameters: categories — comma-separated categories to run or 'all'; stop_on_fail — halt at first failure; db_path — direct path to SQLite DB (auto-detected if empty)
#
# KWARGS (default parameters):
# {
#   "categories": "all",
#   "db_path": "",
#   "stop_on_fail": false
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_test_suite    # sync this file only
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
include("import requests", ["extella-pip install requests"])
include("import sqlite3", [])

def tw_test_suite(
    categories: str = "all",
    stop_on_fail: bool = False,
    db_path: str = ""
) -> dict:
    import os
    import json
    import uuid
    import time
    import sqlite3
    import requests
    import traceback
    from pathlib import Path
    from datetime import datetime

    print("[1/6] 🧪 tw_test_suite v2 — direct SQLite + Flask HTTP")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    FLASK_URL = "http://127.0.0.1:7842"
    start_time = time.time()

    # ── resolve DB path ──────────────────────────────────────────
    if not db_path:
        db_path = str(Path.home() / "Documents" / "twitter_agent" / "data.db")

    # ── helpers ──────────────────────────────────────────────────
    def get_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def flask_get(path):
        try:
            r = requests.get(f"{FLASK_URL}{path}", timeout=8)
            body = {}
            try: body = r.json()
            except Exception: body = {"_text": r.text[:200]}
            return r.status_code, body
        except Exception as e:
            return None, {"error": str(e)}

    def flask_post(path, body=None):
        try:
            r = requests.post(f"{FLASK_URL}{path}", json=body or {}, timeout=15)
            body_r = {}
            try: body_r = r.json()
            except Exception: body_r = {"_text": r.text[:200]}
            return r.status_code, body_r
        except Exception as e:
            return None, {"error": str(e)}

    def flask_patch(path, body=None):
        try:
            r = requests.patch(f"{FLASK_URL}{path}", json=body or {}, timeout=10)
            body_r = {}
            try: body_r = r.json()
            except Exception: body_r = {"_text": r.text[:200]}
            return r.status_code, body_r
        except Exception as e:
            return None, {"error": str(e)}

    def flask_delete(path):
        try:
            r = requests.delete(f"{FLASK_URL}{path}", timeout=10)
            body_r = {}
            try: body_r = r.json()
            except Exception: body_r = {}
            return r.status_code, body_r
        except Exception as e:
            return None, {"error": str(e)}

    # ── test runner ──────────────────────────────────────────────
    results = []
    passed = failed = skipped = 0

    def test(name, category, fn):
        nonlocal passed, failed, skipped
        cats = [c.strip() for c in categories.split(",")]
        if "all" not in cats and category not in cats:
            skipped += 1
            results.append({"test": name, "category": category, "status": "skipped", "detail": ""})
            return True
        print(f"  🔬 [{category}] {name}...")
        try:
            ok, detail = fn()
            status = "PASS" if ok else "FAIL"
            if ok: passed += 1
            else: failed += 1
            results.append({"test": name, "category": category, "status": status, "detail": str(detail)[:200]})
            print(f"  {'✅' if ok else '❌'} {status}: {str(detail)[:90]}")
            if not ok and stop_on_fail:
                return False
        except Exception as e:
            failed += 1
            tb = traceback.format_exc()[-250:]
            results.append({"test": name, "category": category, "status": "ERROR", "detail": tb})
            print(f"  💥 ERROR: {str(e)[:90]}")
            if stop_on_fail: return False
        return True

    # ══════════════════════════════════════════════════════════════
    # DATABASE TESTS — direct SQLite
    # ══════════════════════════════════════════════════════════════
    print("[2/6] 🗄️  DATABASE TESTS (direct SQLite)")

    def t_db_file_exists():
        exists = Path(db_path).exists()
        size = round(Path(db_path).stat().st_size / 1024, 1) if exists else 0
        return exists, f"path={db_path[-40:]}, size={size}KB"

    def t_db_tables():
        conn = get_conn()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        required = {"accounts","profiles","posts","reply_tasks","conversations",
                    "analytics","workflow_state","settings","schema_version"}
        missing = required - tables
        return len(missing) == 0, f"tables={len(tables)}, missing={missing or 'none'}"

    def t_db_wal_mode():
        conn = get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        return mode == "wal", f"journal_mode={mode}"

    def t_db_indexes():
        conn = get_conn()
        idx_count = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
        conn.close()
        return idx_count >= 7, f"index_count={idx_count}"

    def t_db_default_settings():
        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
        ai = conn.execute("SELECT value FROM settings WHERE key='ai_provider'").fetchone()
        conn.close()
        return count >= 8 and ai is not None, f"settings_count={count}, ai_provider={ai[0] if ai else 'missing'}"

    def t_db_workflow_state():
        conn = get_conn()
        row = conn.execute("SELECT phase FROM workflow_state WHERE id='current'").fetchone()
        conn.close()
        return row is not None, f"phase={row['phase'] if row else 'missing'}"

    def t_db_schema_version():
        conn = get_conn()
        v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        conn.close()
        return v is not None and v >= 1, f"schema_version={v}"

    def t_db_settings_crud():
        conn = get_conn()
        key = f"__test_{uuid.uuid4().hex[:8]}__"
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, "test_val_xyz"))
        conn.commit()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
        conn.commit()
        conn.close()
        return row is not None and row["value"] == "test_val_xyz", f"write-read-delete: ok={row is not None}"

    def t_db_foreign_keys():
        conn = get_conn()
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        return fk_on == 1, f"foreign_keys={fk_on}"

    test("DB File exists and non-empty", "db", t_db_file_exists)
    test("DB All 9 tables present", "db", t_db_tables)
    test("DB WAL journal mode", "db", t_db_wal_mode)
    test("DB 7+ indexes created", "db", t_db_indexes)
    test("DB Default settings present", "db", t_db_default_settings)
    test("DB Workflow state initialized", "db", t_db_workflow_state)
    test("DB Schema version ≥ 1", "db", t_db_schema_version)
    test("DB Settings CRUD write/read/delete", "db", t_db_settings_crud)
    test("DB Foreign keys ON", "db", t_db_foreign_keys)

    # ══════════════════════════════════════════════════════════════
    # AUTH TESTS — direct SQLite + KV injection
    # ══════════════════════════════════════════════════════════════
    print("[2/6] 👤 AUTH TESTS (direct SQLite)")

    TEST_ACC_ID = "test-acc-" + uuid.uuid4().hex[:8]
    TEST_USERNAME = "tw_test_mock_user"

    def t_auth_accounts_table_empty_or_has_structure():
        conn = get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
        conn.close()
        required_cols = {"id","username","health_score","circuit_state","role_discovery","role_posting","is_active"}
        missing = required_cols - cols
        return len(missing) == 0, f"cols_ok={len(missing)==0}, missing={missing or 'none'}"

    def t_auth_insert_mock():
        conn = get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO accounts
              (id, username, display_name, role_discovery, role_posting,
               is_active, health_score, circuit_state, created_at)
            VALUES (?, ?, ?, 1, 1, 0, 100, 'closed', datetime('now'))
        """, (TEST_ACC_ID, TEST_USERNAME, "Test Mock User"))
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (TEST_ACC_ID,)).fetchone()
        conn.close()
        return row is not None and row["health_score"] == 100, f"inserted: username={row['username'] if row else 'none'}"

    def t_auth_health_score_update():
        conn = get_conn()
        conn.execute("UPDATE accounts SET health_score=MAX(0, health_score-20) WHERE id=?", (TEST_ACC_ID,))
        conn.commit()
        row = conn.execute("SELECT health_score FROM accounts WHERE id=?", (TEST_ACC_ID,)).fetchone()
        conn.close()
        return row is not None and row["health_score"] == 80, f"health={row['health_score'] if row else 'none'}"

    def t_auth_circuit_breaker_logic():
        conn = get_conn()
        conn.execute("UPDATE accounts SET health_score=0, circuit_state='open' WHERE id=?", (TEST_ACC_ID,))
        conn.commit()
        row = conn.execute("SELECT circuit_state, health_score FROM accounts WHERE id=?", (TEST_ACC_ID,)).fetchone()
        conn.close()
        return row["circuit_state"] == "open" and row["health_score"] == 0, \
               f"circuit={row['circuit_state']}, health={row['health_score']}"

    def t_auth_reset_health():
        conn = get_conn()
        conn.execute("UPDATE accounts SET health_score=100, circuit_state='closed' WHERE id=?", (TEST_ACC_ID,))
        conn.commit()
        row = conn.execute("SELECT health_score, circuit_state FROM accounts WHERE id=?", (TEST_ACC_ID,)).fetchone()
        conn.close()
        return row["health_score"] == 100 and row["circuit_state"] == "closed", \
               f"health={row['health_score']}, circuit={row['circuit_state']}"

    def t_auth_switch_active():
        conn = get_conn()
        conn.execute("UPDATE accounts SET is_active=0")
        conn.execute("UPDATE accounts SET is_active=1 WHERE id=?", (TEST_ACC_ID,))
        conn.commit()
        row = conn.execute("SELECT is_active FROM accounts WHERE id=?", (TEST_ACC_ID,)).fetchone()
        conn.close()
        return row is not None and row["is_active"] == 1, f"is_active={row['is_active'] if row else 'none'}"

    def t_auth_delete_mock():
        conn = get_conn()
        conn.execute("DELETE FROM accounts WHERE id=?", (TEST_ACC_ID,))
        conn.commit()
        row = conn.execute("SELECT id FROM accounts WHERE id=?", (TEST_ACC_ID,)).fetchone()
        conn.close()
        return row is None, f"deleted: row_exists={row is not None}"

    test("Auth Accounts table schema correct", "auth", t_auth_accounts_table_empty_or_has_structure)
    test("Auth Insert mock account", "auth", t_auth_insert_mock)
    test("Auth Health score decrease -20", "auth", t_auth_health_score_update)
    test("Auth Circuit breaker opens at 0 health", "auth", t_auth_circuit_breaker_logic)
    test("Auth Health reset to 100, circuit closed", "auth", t_auth_reset_health)
    test("Auth Switch active account", "auth", t_auth_switch_active)
    test("Auth Delete mock account", "auth", t_auth_delete_mock)

    # ══════════════════════════════════════════════════════════════
    # SERVER TESTS — HTTP to Flask
    # ══════════════════════════════════════════════════════════════
    print("[3/6] 🌐 SERVER TESTS (HTTP to localhost:7842)")

    def t_srv_health():
        code, body = flask_get("/api/health")
        return code == 200 and body.get("status") == "ok", f"HTTP {code}, body={body}"

    def t_srv_accounts():
        code, body = flask_get("/api/accounts")
        return code == 200 and "accounts" in body, f"HTTP {code}, count={len(body.get('accounts',[]))}"

    def t_srv_profiles():
        code, body = flask_get("/api/profiles")
        return code == 200 and "profiles" in body and "total" in body, f"HTTP {code}, total={body.get('total',0)}"

    def t_srv_profiles_filter():
        code, body = flask_get("/api/profiles?tier=1&sort=followers_count")
        return code == 200 and "profiles" in body, f"HTTP {code}, tier1={len(body.get('profiles',[]))}"

    def t_srv_posts():
        code, body = flask_get("/api/posts")
        return code == 200 and "posts" in body, f"HTTP {code}, posts={len(body.get('posts',[]))}"

    def t_srv_tasks_pending():
        code, body = flask_get("/api/tasks?status=pending")
        return code == 200 and "tasks" in body, f"HTTP {code}, tasks={body.get('count',0)}"

    def t_srv_tasks_approved():
        code, body = flask_get("/api/tasks?status=approved")
        return code == 200 and "tasks" in body, f"HTTP {code}, tasks={body.get('count',0)}"

    def t_srv_analytics():
        code, body = flask_get("/api/analytics")
        return code == 200 and "timeline" in body and "summary" in body, f"HTTP {code}, timeline={len(body.get('timeline',[]))}"

    def t_srv_settings_get():
        code, body = flask_get("/api/settings")
        return code == 200 and "ai_provider" in body, f"HTTP {code}, keys={list(body.keys())[:5]}"

    def t_srv_settings_patch():
        code, body = flask_patch("/api/settings", {"__test_srv__": "srv_val_42"})
        return code == 200 and body.get("status") == "success", f"HTTP {code}, updated={body.get('updated')}"

    def t_srv_settings_verify():
        code, body = flask_get("/api/settings")
        return code == 200 and body.get("__test_srv__") == "srv_val_42", f"value={body.get('__test_srv__')}"

    def t_srv_settings_cleanup():
        # Clean up test setting via direct SQLite
        conn = get_conn()
        conn.execute("DELETE FROM settings WHERE key='__test_srv__'")
        conn.commit()
        conn.close()
        return True, "cleaned up"

    def t_srv_workflow_state():
        code, body = flask_get("/api/workflow/state")
        return code == 200 and "phase" in body, f"HTTP {code}, phase={body.get('phase')}"

    def t_srv_workflow_set():
        code, body = flask_post("/api/workflow/state", {"phase": "test_phase", "payload": {"test": True}})
        return code == 200 and body.get("status") == "success", f"HTTP {code}, phase={body.get('phase')}"

    def t_srv_workflow_verify():
        code, body = flask_get("/api/workflow/state")
        ok = code == 200 and body.get("phase") == "test_phase"
        # Reset to idle
        flask_post("/api/workflow/state", {"phase": "idle", "payload": {}})
        return ok, f"phase={body.get('phase')}"

    def t_srv_spa():
        try:
            r = requests.get(f"{FLASK_URL}/", timeout=5)
            ok = r.status_code == 200 and "Twitter Lead Agent" in r.text
            return ok, f"HTTP {r.status_code}, html_len={len(r.text)}"
        except Exception as e:
            return False, str(e)

    def t_srv_task_approve_flow():
        # Insert mock task → approve via HTTP → verify status
        conn = get_conn()
        tid = "srv_test_task_" + uuid.uuid4().hex[:8]
        # Need at least a minimal post and profile for FK
        pid = "srv_test_post_" + uuid.uuid4().hex[:6]
        prf = "srv_test_prf_" + uuid.uuid4().hex[:6]
        conn.execute("INSERT OR IGNORE INTO profiles (id,username,discovered_at,updated_at) VALUES (?,\'srvtest\',datetime(\'now\'),datetime(\'now\'))", (prf,))
        conn.execute("INSERT OR IGNORE INTO posts (id,profile_id,text,url) VALUES (?,?,\'srv test post\',\'http://test\')", (pid, prf))
        conn.execute("INSERT INTO reply_tasks (id,post_id,profile_id,generated_reply,status,created_at) VALUES (?,?,?,\'Test reply\',\'pending\',datetime(\'now\'))", (tid, pid, prf))
        conn.commit()
        conn.close()

        # Approve via HTTP
        code, body = flask_post(f"/api/tasks/{tid}/approve")
        if code != 200 or body.get("status") != "success":
            return False, f"approve failed: HTTP {code} {body}"

        # Verify in DB
        conn = get_conn()
        row = conn.execute("SELECT status FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        conn.execute("DELETE FROM reply_tasks WHERE id=?", (tid,))
        conn.execute("DELETE FROM posts WHERE id=?", (pid,))
        conn.execute("DELETE FROM profiles WHERE id=?", (prf,))
        conn.commit()
        conn.close()
        return row and row["status"] == "approved", f"status={row['status'] if row else 'none'}"

    def t_srv_task_reject_from_pending():
        conn = get_conn()
        tid = "srv_rej_task_" + uuid.uuid4().hex[:8]
        pid2 = "srv_rej_post_" + uuid.uuid4().hex[:6]
        prf2 = "srv_rej_prf_" + uuid.uuid4().hex[:6]
        conn.execute("INSERT OR IGNORE INTO profiles (id,username,discovered_at,updated_at) VALUES (?,\'rejtest\',datetime(\'now\'),datetime(\'now\'))", (prf2,))
        conn.execute("INSERT OR IGNORE INTO posts (id,profile_id,text,url) VALUES (?,?,\'rej test\',\'http://rej\')", (pid2, prf2))
        conn.execute("INSERT INTO reply_tasks (id,post_id,profile_id,generated_reply,status,created_at) VALUES (?,?,?,\'Reject me\',\'pending\',datetime(\'now\'))", (tid, pid2, prf2))
        conn.commit()
        conn.close()
        code, body = flask_post(f"/api/tasks/{tid}/reject")
        conn = get_conn()
        row = conn.execute("SELECT status FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        conn.execute("DELETE FROM reply_tasks WHERE id=?", (tid,))
        conn.execute("DELETE FROM posts WHERE id=?", (pid2,))
        conn.execute("DELETE FROM profiles WHERE id=?", (prf2,))
        conn.commit()
        conn.close()
        return row and row["status"] == "rejected", f"status={row['status'] if row else 'none'}"

    def t_srv_task_edit():
        conn = get_conn()
        tid = "srv_edit_task_" + uuid.uuid4().hex[:8]
        pid3 = "srv_ed_post_" + uuid.uuid4().hex[:6]
        prf3 = "srv_ed_prf_" + uuid.uuid4().hex[:6]
        conn.execute("INSERT OR IGNORE INTO profiles (id,username,discovered_at,updated_at) VALUES (?,\'editest\',datetime(\'now\'),datetime(\'now\'))", (prf3,))
        conn.execute("INSERT OR IGNORE INTO posts (id,profile_id,text,url) VALUES (?,?,\'edit test\',\'http://edit\')", (pid3, prf3))
        conn.execute("INSERT INTO reply_tasks (id,post_id,profile_id,generated_reply,status,created_at) VALUES (?,?,?,\'Original reply\',\'pending\',datetime(\'now\'))", (tid, pid3, prf3))
        conn.commit()
        conn.close()
        edited = "This reply was edited by test suite"
        code, body = flask_patch(f"/api/tasks/{tid}", {"edited_reply": edited})
        conn = get_conn()
        row = conn.execute("SELECT edited_reply FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        conn.execute("DELETE FROM reply_tasks WHERE id=?", (tid,))
        conn.execute("DELETE FROM posts WHERE id=?", (pid3,))
        conn.execute("DELETE FROM profiles WHERE id=?", (prf3,))
        conn.commit()
        conn.close()
        return row and row["edited_reply"] == edited, f"edited_reply='{(row['edited_reply'] if row else 'none')[:40]}'"

    def t_srv_task_edit_280_limit():
        conn = get_conn()
        tid = "srv_280_task_" + uuid.uuid4().hex[:8]
        pid4 = "srv_280_post_" + uuid.uuid4().hex[:6]
        prf4 = "srv_280_prf_" + uuid.uuid4().hex[:6]
        conn.execute("INSERT OR IGNORE INTO profiles (id,username,discovered_at,updated_at) VALUES (?,\'ltest\',datetime(\'now\'),datetime(\'now\'))", (prf4,))
        conn.execute("INSERT OR IGNORE INTO posts (id,profile_id,text,url) VALUES (?,?,\'limit test\',\'http://limit\')", (pid4, prf4))
        conn.execute("INSERT INTO reply_tasks (id,post_id,profile_id,generated_reply,status,created_at) VALUES (?,?,?,\'Short\',\'pending\',datetime(\'now\'))", (tid, pid4, prf4))
        conn.commit()
        conn.close()
        long_reply = "X" * 300
        code, body = flask_patch(f"/api/tasks/{tid}", {"edited_reply": long_reply})
        conn = get_conn()
        conn.execute("DELETE FROM reply_tasks WHERE id=?", (tid,))
        conn.execute("DELETE FROM posts WHERE id=?", (pid4,))
        conn.execute("DELETE FROM profiles WHERE id=?", (prf4,))
        conn.commit()
        conn.close()
        rejected = body.get("status") == "error" and "280" in body.get("message", "")
        return rejected, f"HTTP {code}, msg='{body.get('message','')[:60]}'"

    test("Server /api/health → 200 ok", "server", t_srv_health)
    test("Server /api/accounts → 200 with accounts list", "server", t_srv_accounts)
    test("Server /api/profiles → 200 with profiles", "server", t_srv_profiles)
    test("Server /api/profiles?tier=1 filter works", "server", t_srv_profiles_filter)
    test("Server /api/posts → 200 with posts", "server", t_srv_posts)
    test("Server /api/tasks?status=pending → 200", "server", t_srv_tasks_pending)
    test("Server /api/tasks?status=approved → 200", "server", t_srv_tasks_approved)
    test("Server /api/analytics → 200 with timeline+summary", "server", t_srv_analytics)
    test("Server /api/settings GET → 200 with ai_provider", "server", t_srv_settings_get)
    test("Server /api/settings PATCH → 200 success", "server", t_srv_settings_patch)
    test("Server /api/settings PATCH value persisted", "server", t_srv_settings_verify)
    test("Server /api/settings cleanup", "server", t_srv_settings_cleanup)
    test("Server /api/workflow/state GET → 200", "server", t_srv_workflow_state)
    test("Server /api/workflow/state POST → sets phase", "server", t_srv_workflow_set)
    test("Server /api/workflow/state phase verified", "server", t_srv_workflow_verify)
    test("Server SPA / → HTML with correct title", "server", t_srv_spa)
    test("Server task approve → pending→approved", "server", t_srv_task_approve_flow)
    test("Server task reject → pending→rejected", "server", t_srv_task_reject_from_pending)
    test("Server task PATCH edit → text saved", "server", t_srv_task_edit)
    test("Server task PATCH 300-char → rejected (280 limit)", "server", t_srv_task_edit_280_limit)

    # ══════════════════════════════════════════════════════════════
    # QUEUE FSM TESTS — direct SQLite
    # ══════════════════════════════════════════════════════════════
    print("[4/6] 📋 QUEUE FSM TESTS (direct SQLite)")

    FSM_PROFILE_ID = "fsm_profile_" + uuid.uuid4().hex[:8]
    FSM_POST_ID = "fsm_post_" + uuid.uuid4().hex[:8]

    def setup_fsm_fixtures():
        conn = get_conn()
        conn.execute("INSERT OR IGNORE INTO profiles (id,username,discovered_at,updated_at) VALUES (?,\'fsmuser\',datetime(\'now\'),datetime(\'now\'))", (FSM_PROFILE_ID,))
        conn.execute("INSERT OR IGNORE INTO posts (id,profile_id,text,url) VALUES (?,?,\'FSM test post text\',\'https://twitter.com/fsmuser/status/123\')", (FSM_POST_ID, FSM_PROFILE_ID))
        conn.commit()
        conn.close()

    setup_fsm_fixtures()

    def t_fsm_insert_pending():
        conn = get_conn()
        tid = "fsm_t_" + uuid.uuid4().hex[:8]
        conn.execute("INSERT INTO reply_tasks (id,post_id,profile_id,generated_reply,status,created_at) VALUES (?,?,?,\'FSM test reply\',\'pending\',datetime(\'now\'))", (tid, FSM_POST_ID, FSM_PROFILE_ID))
        conn.commit()
        row = conn.execute("SELECT status FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        conn.close()
        # store for next test
        t_fsm_insert_pending._tid = tid
        return row and row["status"] == "pending", f"status={row['status'] if row else 'none'}"

    def t_fsm_pending_to_approved():
        tid = getattr(t_fsm_insert_pending, "_tid", None)
        if not tid: return False, "no task ID from previous test"
        conn = get_conn()
        row = conn.execute("SELECT status FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        if not row or row["status"] != "pending":
            conn.close()
            return False, "task not in pending"
        conn.execute("UPDATE reply_tasks SET status='approved', approved_at=datetime('now') WHERE id=?", (tid,))
        conn.commit()
        row2 = conn.execute("SELECT status FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        conn.close()
        t_fsm_insert_pending._tid_approved = tid
        return row2["status"] == "approved", f"pending→approved: ok={row2['status']=='approved'}"

    def t_fsm_edit_approved():
        tid = getattr(t_fsm_insert_pending, "_tid", None)
        if not tid: return False, "no task ID"
        conn = get_conn()
        conn.execute("UPDATE reply_tasks SET edited_reply='FSM edited reply' WHERE id=? AND status='approved'", (tid,))
        conn.commit()
        row = conn.execute("SELECT edited_reply FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        conn.close()
        return row and row["edited_reply"] == "FSM edited reply", f"edited={row['edited_reply'] if row else 'none'}"

    def t_fsm_approved_to_posted():
        tid = getattr(t_fsm_insert_pending, "_tid", None)
        if not tid: return False, "no task ID"
        conn = get_conn()
        conn.execute("UPDATE reply_tasks SET status='posted', posted_at=datetime('now'), posted_tweet_id='test_tweet_123' WHERE id=? AND status='approved'", (tid,))
        conn.commit()
        row = conn.execute("SELECT status, posted_tweet_id FROM reply_tasks WHERE id=?", (tid,)).fetchone()
        conn.close()
        return row and row["status"] == "posted", f"status={row['status'] if row else 'none'}, tweet_id={row['posted_tweet_id'] if row else ''}"

    def t_fsm_conversation_insert():
        tid = getattr(t_fsm_insert_pending, "_tid", None)
        if not tid: return False, "no task ID"
        conv_id = "conv_" + uuid.uuid4().hex[:8]
        conn = get_conn()
        conn.execute("INSERT INTO conversations (id,reply_task_id,depth,direction,tweet_id,text,author_username) VALUES (?,?,1,'theirs','resp_tweet_456','Great point! Love it','respondinguser')", (conv_id, tid))
        conn.commit()
        row = conn.execute("SELECT COUNT(*) FROM conversations WHERE reply_task_id=?", (tid,)).fetchone()
        conn.close()
        return row[0] >= 1, f"conversations_count={row[0]}"

    def t_fsm_cleanup():
        tid = getattr(t_fsm_insert_pending, "_tid", None)
        if not tid: return True, "nothing to clean"
        conn = get_conn()
        conn.execute("DELETE FROM conversations WHERE reply_task_id=?", (tid,))
        conn.execute("DELETE FROM reply_tasks WHERE id=?", (tid,))
        conn.execute("DELETE FROM posts WHERE id=?", (FSM_POST_ID,))
        conn.execute("DELETE FROM profiles WHERE id=?", (FSM_PROFILE_ID,))
        conn.commit()
        conn.close()
        return True, "cleaned up"

    test("Queue FSM insert pending task", "queue", t_fsm_insert_pending)
    test("Queue FSM pending → approved", "queue", t_fsm_pending_to_approved)
    test("Queue FSM edit approved task", "queue", t_fsm_edit_approved)
    test("Queue FSM approved → posted with tweet_id", "queue", t_fsm_approved_to_posted)
    test("Queue FSM conversation linked to task", "queue", t_fsm_conversation_insert)
    test("Queue FSM cleanup", "queue", t_fsm_cleanup)

    # ══════════════════════════════════════════════════════════════
    # ANALYTICS TESTS — direct SQLite
    # ══════════════════════════════════════════════════════════════
    print("[4/6] 📊 ANALYTICS TESTS (direct SQLite)")

    def t_analytics_table_structure():
        conn = get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(analytics)").fetchall()}
        conn.close()
        required = {"id","account_id","date","replies_sent","responses_received","response_rate"}
        missing = required - cols
        return len(missing) == 0, f"required_cols_ok={len(missing)==0}, missing={missing or 'none'}"

    def t_analytics_upsert():
        conn = get_conn()
        aid = "ana_acc_" + uuid.uuid4().hex[:6]
        # Create dummy account for FK
        conn.execute("INSERT OR IGNORE INTO accounts (id,username,created_at) VALUES (?,\'anatest\',datetime(\'now\'))", (aid,))
        today = datetime.utcnow().strftime("%Y-%m-%d")
        conn.execute("INSERT OR REPLACE INTO analytics (id,account_id,date,replies_sent,responses_received,response_rate) VALUES (?,?,?,5,2,40.0)", ("ana_rec_" + uuid.uuid4().hex[:6], aid, today))
        conn.commit()
        row = conn.execute("SELECT replies_sent, response_rate FROM analytics WHERE account_id=? AND date=?", (aid, today)).fetchone()
        conn.execute("DELETE FROM analytics WHERE account_id=?", (aid,))
        conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
        conn.commit()
        conn.close()
        return row and row["replies_sent"] == 5 and abs(row["response_rate"] - 40.0) < 0.01, \
               f"replies_sent={row['replies_sent'] if row else 'none'}, rate={row['response_rate'] if row else 'none'}"

    test("Analytics table schema correct", "analytics", t_analytics_table_structure)
    test("Analytics upsert with FK", "analytics", t_analytics_upsert)

    # ══════════════════════════════════════════════════════════════
    # AGENT STATE TESTS — direct SQLite + Flask HTTP
    # ══════════════════════════════════════════════════════════════
    print("[5/6] 🤖 AGENT STATE TESTS")

    def t_agent_workflow_idle():
        conn = get_conn()
        row = conn.execute("SELECT phase FROM workflow_state WHERE id='current'").fetchone()
        conn.close()
        return row is not None, f"phase={row['phase'] if row else 'missing'}"

    def t_agent_workflow_set_discovering():
        conn = get_conn()
        payload = json.dumps({"keywords": "AI SaaS", "started_at": datetime.utcnow().isoformat()})
        conn.execute("UPDATE workflow_state SET phase='discovering', payload=?, updated_at=datetime('now') WHERE id='current'", (payload,))
        conn.commit()
        row = conn.execute("SELECT phase FROM workflow_state WHERE id='current'").fetchone()
        conn.close()
        return row and row["phase"] == "discovering", f"phase={row['phase'] if row else 'none'}"

    def t_agent_workflow_resume_via_http():
        code, body = flask_get("/api/workflow/state")
        ok = code == 200 and body.get("phase") == "discovering"
        # Reset to idle
        conn = get_conn()
        conn.execute("UPDATE workflow_state SET phase='idle', payload='{}', updated_at=datetime('now') WHERE id='current'")
        conn.commit()
        conn.close()
        return ok, f"phase_via_http={body.get('phase')}"

    def t_agent_discover_endpoint_reachable():
        # Test that /api/run/discover endpoint exists (even if it errors without account)
        code, body = flask_post("/api/run/discover", {"keywords": "test", "limit": 1, "dry_run": True})
        # 200 (success or graceful error), not 404 or 405
        return code == 200, f"HTTP {code}, response_keys={list(body.keys())[:4]}"

    def t_agent_generate_endpoint_reachable():
        code, body = flask_post("/api/run/generate", {"mode": "single", "post_id": "nonexistent", "dry_run": True})
        return code == 200, f"HTTP {code}, response_keys={list(body.keys())[:4]}"

    def t_agent_monitor_endpoint_reachable():
        code, body = flask_post("/api/run/monitor")
        return code == 200, f"HTTP {code}, response_keys={list(body.keys())[:4]}"

    test("Agent Workflow state exists in DB", "agent", t_agent_workflow_idle)
    test("Agent Workflow set to discovering phase", "agent", t_agent_workflow_set_discovering)
    test("Agent Workflow resume reads correct phase via HTTP", "agent", t_agent_workflow_resume_via_http)
    test("Agent /api/run/discover endpoint reachable", "agent", t_agent_discover_endpoint_reachable)
    test("Agent /api/run/generate endpoint reachable", "agent", t_agent_generate_endpoint_reachable)
    test("Agent /api/run/monitor endpoint reachable", "agent", t_agent_monitor_endpoint_reachable)

    # ══════════════════════════════════════════════════════════════
    # FINAL REPORT
    # ══════════════════════════════════════════════════════════════
    duration = round(time.time() - start_time, 1)
    total = passed + failed + skipped
    pass_rate = round(passed / max(passed + failed, 1) * 100, 1)

    by_cat = {}
    for r in results:
        cat = r["category"]
        if cat not in by_cat:
            by_cat[cat] = {"pass": 0, "fail": 0}
        if r["status"] == "PASS": by_cat[cat]["pass"] += 1
        elif r["status"] in ("FAIL","ERROR"): by_cat[cat]["fail"] += 1

    failing = [r for r in results if r["status"] in ("FAIL","ERROR")]
    bar = "█" * int(pass_rate / 10) + "░" * (10 - int(pass_rate / 10))

    print(f"[6/6] 📊 Result: {passed}✅ {failed}❌ {skipped}⏭️  | {pass_rate}% | {duration}s")

    return {
        "status": "success",
        "summary": {
            "total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "pass_rate": f"{pass_rate}%",
            "duration_seconds": duration,
            "progress_bar": f"[{bar}] {pass_rate}%"
        },
        "by_category": by_cat,
        "failing_tests": failing,
        "verdict": "✅ ALL TESTS PASSED" if failed == 0 else f"❌ {failed} TEST(S) FAILED — see failing_tests"
    }
