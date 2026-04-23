# =============================================================================
# EXTELLA EXPERT: tw_agent
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Master Nested Expert Orchestrator. Single entry point that orchestrates 10 sub-experts via REST API. Resilient launch: checks DB file directly, doesn't fail if nested call returns empty. Token bootstrap: api_token param → .api_token file → KV Store. Modes: launch/run/resume/status/background. Sub-experts needed: tw_data, tw_auth, tw_server, tw_discover, tw_posts, tw_generate, tw_queue, tw_post, tw_monitor, tw_debug. Parameters: mode — launch/run/resume/status/background; background_action — start/stop/status; open_browser — auto-open UI (default true); api_token — explicit Extella API token; keywords — for run mode; topics/reply_intent/language/ai_provider/account_id/dry_run — campaign settings; autostart_background; db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "account_id": "",
#   "ai_provider": "extella",
#   "api_token": "",
#   "autostart_background": false,
#   "background_action": "start",
#   "db_path_key": "tw_db_path",
#   "dry_run": false,
#   "extella_token_key": "extella_api_token",
#   "keywords": "",
#   "language": "auto",
#   "mode": "status",
#   "open_browser": true,
#   "reply_intent": "helpful and curious, add genuine value",
#   "topics": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_agent    # sync this file only
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

def tw_agent(
    mode: str = "launch",
    background_action: str = "start",
    open_browser: bool = True,
    api_token: str = "",
    keywords: str = "",
    topics: str = "",
    reply_intent: str = "helpful and curious, add genuine value",
    language: str = "auto",
    ai_provider: str = "extella",
    account_id: str = "",
    dry_run: bool = False,
    autostart_background: bool = False,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3
    import json
    import os
    import sys
    import time
    import signal
    import subprocess
    import requests
    from pathlib import Path
    from datetime import datetime

    print(f"[1/8] 🐦 tw_agent: mode={mode}")

    BASE_URL   = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    APP_DIR    = Path.home() / "Documents" / "twitter_agent"
    BG_LOCK    = APP_DIR / ".background.lock"
    TOKEN_FILE = APP_DIR / ".api_token"
    DEFAULT_DB = APP_DIR / "data.db"

    # ── Token bootstrap ───────────────────────────────────────────
    # Resolution: 1) explicit param  2) .api_token file  3) KV Store
    def resolve_token() -> str:
        if api_token:
            return api_token
        if TOKEN_FILE.exists():
            try:
                v = TOKEN_FILE.read_text(encoding="utf-8").strip()
                if v: return v
            except Exception:
                pass
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get",
                              json={"key": extella_token_key}, timeout=10)
            if r.status_code == 200:
                v = r.json().get("value", "")
                if v: return v
        except Exception:
            pass
        return ""

    _TOKEN = resolve_token()
    print(f"[1/8] 🔑 Token: {'✅' if _TOKEN else '❌ not found — pass api_token param'}")

    # ── Helpers ───────────────────────────────────────────────────
    def kv_get_auth(key) -> str:
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get",
                              json={"key": key},
                              headers={"X-Auth-Token": _TOKEN}, timeout=10)
            if r.status_code == 200:
                return r.json().get("value", "")
        except Exception:
            pass
        return ""

    def kv_set(key, value, desc=""):
        try:
            requests.post(f"{BASE_URL}/api/kv/set",
                          json={"key": key, "value": value, "description": desc},
                          headers={"X-Auth-Token": _TOKEN}, timeout=10)
        except Exception:
            pass

    def run_expert(name, params, timeout=120):
        if not _TOKEN:
            return {"error": "No API token. Pass api_token=... to tw_agent."}
        try:
            r = requests.post(
                f"{BASE_URL}/api/expert/run",
                headers={"X-Auth-Token": _TOKEN, "Content-Type": "application/json"},
                json={"expert_name": name, "params": params},
                timeout=timeout
            )
            if r.status_code == 200:
                data = r.json()
                # Handle both response formats: {"result": {...}} and direct result
                result = data.get("result")
                if result is None:
                    result = data  # fallback: use full response
                return result if isinstance(result, dict) else {}
            return {"error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def get_db_path() -> str:
        # Try KV first (authenticated), fallback to default
        path = kv_get_auth(db_path_key)
        return path or str(DEFAULT_DB)

    def get_conn():
        c = sqlite3.connect(get_db_path())
        c.row_factory = sqlite3.Row
        return c

    def db_is_ready() -> bool:
        """Check if DB exists and has required tables — no expert call needed."""
        try:
            db_path = get_db_path()
            if not Path(db_path).exists():
                return False
            conn = sqlite3.connect(db_path)
            tables = {r[0] for r in
                      conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            required = {"accounts", "profiles", "posts", "reply_tasks",
                        "settings", "workflow_state"}
            return required.issubset(tables)
        except Exception:
            return False

    def get_setting(key, default=""):
        try:
            conn = get_conn()
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            conn.close()
            return row["value"] if row else default
        except Exception:
            return default

    def save_workflow_state(phase: str, payload: dict):
        try:
            conn = get_conn()
            conn.execute(
                "UPDATE workflow_state "
                "SET phase=?, payload=?, updated_at=datetime('now') "
                "WHERE id='current'",
                (phase, json.dumps(payload))
            )
            conn.commit()
            conn.close()
            print(f"[*] 📌 State → {phase}")
        except Exception as e:
            print(f"[*] ⚠️  State save failed: {e}")

    def load_workflow_state():
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT phase, payload FROM workflow_state WHERE id='current'"
            ).fetchone()
            conn.close()
            if row:
                return row["phase"], json.loads(row["payload"] or "{}")
        except Exception:
            pass
        return "idle", {}

    def is_pid_alive(pid):
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    def sub(action_or_params, **extra):
        """Build sub-expert params with routing keys always included."""
        if isinstance(action_or_params, str):
            return {"action": action_or_params,
                    "db_path_key": db_path_key,
                    "extella_token_key": extella_token_key,
                    **extra}
        return {"db_path_key": db_path_key,
                "extella_token_key": extella_token_key,
                **action_or_params,
                **extra}

    # ════════════════════════════════════════════════════════════
    # MODE: STATUS
    # ════════════════════════════════════════════════════════════
    if mode == "status":
        phase, payload = load_workflow_state()

        server_pid    = kv_get_auth("tw_server_pid")
        server_running = bool(server_pid) and is_pid_alive(int(server_pid))

        bg_running = False
        if BG_LOCK.exists():
            try:
                bg_data  = json.loads(BG_LOCK.read_text(encoding="utf-8"))
                bg_pid   = bg_data.get("pid")
                bg_running = bool(bg_pid) and is_pid_alive(int(bg_pid))
            except Exception:
                pass

        accounts_result = run_expert("tw_auth", sub("list"))
        accounts = accounts_result.get("accounts", [])

        try:
            conn = get_conn()
            profile_count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
            pending_tasks = conn.execute(
                "SELECT COUNT(*) FROM reply_tasks WHERE status='pending'"
            ).fetchone()[0]
            posted_count  = conn.execute(
                "SELECT COUNT(*) FROM reply_tasks WHERE status='posted'"
            ).fetchone()[0]
            conn.close()
        except Exception:
            profile_count = pending_tasks = posted_count = 0

        print(f"[2/8] 📊 Phase:{phase} | Accounts:{len(accounts)} | "
              f"Profiles:{profile_count} | Pending:{pending_tasks}")
        for i in range(6): print(f"[{i+3}/8] ✅")

        return {
            "status":               "success",
            "workflow_phase":       phase,
            "server_running":       server_running,
            "server_url":           "http://127.0.0.1:7842" if server_running else None,
            "background_running":   bg_running,
            "token_ok":             bool(_TOKEN),
            "accounts_count":       len(accounts),
            "accounts_with_session": sum(1 for a in accounts if a.get("has_session")),
            "profiles_in_db":       profile_count,
            "pending_tasks":        pending_tasks,
            "posted_total":         posted_count,
            "db_ready":             db_is_ready(),
            "payload":              payload,
            "sub_experts": [
                "tw_data", "tw_auth", "tw_server", "tw_discover",
                "tw_posts", "tw_generate", "tw_queue", "tw_post",
                "tw_monitor", "tw_debug"
            ]
        }

    # ════════════════════════════════════════════════════════════
    # MODE: LAUNCH
    # ════════════════════════════════════════════════════════════
    elif mode == "launch":
        if not _TOKEN:
            return {
                "status": "error",
                "message": ("No API token found. "
                            "Pass it explicitly: tw_agent(mode='launch', api_token='YOUR-TOKEN'). "
                            "Token is saved automatically when tw_server starts.")
            }

        # ── DB init: check file directly first ───────────────────
        print("[2/8] 🗄️  Checking database...")
        ready = db_is_ready()
        if not ready:
            print("[2/8] 📦 DB missing — initializing via tw_data...")
            db_result = run_expert("tw_data", sub("init"))
            # Accept success OR non-empty dict (nested call format variance)
            ready_after = (
                db_result.get("status") == "success" or
                db_result.get("tables_count", 0) > 0 or
                db_is_ready()  # final direct check
            )
            if not ready_after:
                return {
                    "status": "error",
                    "message": (f"DB init failed: {db_result}. "
                                "Try running tw_data(action='init') separately first.")
                }
        else:
            print("[2/8] ✅ DB already initialized")

        # ── Accounts ─────────────────────────────────────────────
        print("[3/8] 👤 Checking accounts...")
        auth_result = run_expert("tw_auth", sub("list"))
        accounts    = auth_result.get("accounts", [])
        first_run   = len(accounts) == 0

        # ── Web server ───────────────────────────────────────────
        print("[4/8] 🌐 Starting web UI...")
        server_result = run_expert("tw_server", sub({
            "action":       "start",
            "open_browser": open_browser,
        }))

        # ── Workflow state ────────────────────────────────────────
        print("[5/8] 💾 Setting workflow state...")
        initial_phase = "setup" if first_run else "idle"
        save_workflow_state(initial_phase, {"launched_at": datetime.utcnow().isoformat()})

        # ── Optional background daemon ────────────────────────────
        if autostart_background and accounts:
            print("[6/8] 🔄 Starting background daemon...")
            run_expert("tw_agent", sub({
                "mode":             "background",
                "background_action": "start",
                "api_token":        _TOKEN
            }))
        else:
            print("[6/8] ℹ️  Background not started (autostart=False or no accounts)")

        url = server_result.get("url", "http://127.0.0.1:7842")
        print(f"[7/8] ✅ {'First run — add account in Settings' if first_run else 'Welcome back!'}")
        print("[8/8] 🚀 tw_agent launched!")
        return {
            "status":    "success",
            "mode":      "launch",
            "ui_url":    url,
            "phase":     initial_phase,
            "first_run": first_run,
            "accounts":  len(accounts),
            "server":    server_result.get("message"),
            "next_step": ("Open Settings → add Twitter account"
                          if first_run else f"UI ready at {url}")
        }

    # ════════════════════════════════════════════════════════════
    # MODE: RUN
    # ════════════════════════════════════════════════════════════
    elif mode == "run":
        if not keywords:
            return {"status": "error", "message": "keywords required for run mode"}
        if not _TOKEN:
            return {"status": "error",
                    "message": "No API token. Pass api_token parameter."}

        campaign = {
            "keywords": keywords, "topics": topics,
            "reply_intent": reply_intent, "language": language,
            "ai_provider": ai_provider, "account_id": account_id,
            "dry_run": dry_run, "started_at": datetime.utcnow().isoformat()
        }

        # DISCOVER
        print("[2/8] 🔍 Phase: DISCOVERING...")
        save_workflow_state("discovering", campaign)
        disc = run_expert("tw_discover", sub({
            "keywords": keywords, "limit": 10,
            "lang": "en", "topics": topics, "dry_run": dry_run
        }), timeout=300)

        if disc.get("status") != "success":
            save_workflow_state("error", {"phase": "discovering", "error": str(disc)})
            return {"status": "error", "phase": "discovering",
                    "message": disc.get("message", str(disc))}

        profiles_found = disc.get("profiles_found", 0)
        print(f"[2/8] ✅ Found {profiles_found} profiles")
        if profiles_found == 0:
            save_workflow_state("idle", {})
            return {"status": "success", "message": "No profiles found.", "profiles_found": 0}

        # FETCH POSTS
        print("[3/8] 📥 Phase: FETCHING POSTS...")
        save_workflow_state("fetching_posts", campaign)
        posts_r = run_expert("tw_posts", sub({
            "posts_per_profile": 5, "topics": topics, "dry_run": dry_run
        }), timeout=300)
        posts_saved = posts_r.get("posts_saved", 0) if not dry_run else 0
        print(f"[3/8] ✅ Posts: {posts_saved}")

        if posts_saved == 0 and not dry_run:
            save_workflow_state("idle", {})
            return {"status": "success",
                    "message": "No posts matching topics.",
                    "profiles_found": profiles_found}

        # GENERATE
        print("[4/8] 🤖 Phase: GENERATING...")
        save_workflow_state("generating", campaign)
        try:
            conn = get_conn()
            post_rows = conn.execute("""
                SELECT p.id FROM posts p
                WHERE NOT EXISTS (SELECT 1 FROM reply_tasks rt WHERE rt.post_id=p.id)
                LIMIT 20
            """).fetchall()
            active_acc = account_id
            if not active_acc:
                row = conn.execute(
                    "SELECT id FROM accounts WHERE is_active=1 LIMIT 1"
                ).fetchone()
                if row: active_acc = row["id"]
            conn.close()
        except Exception as e:
            return {"status": "error", "message": f"DB query failed: {e}"}

        post_ids_str = ",".join(r["id"] for r in post_rows)
        tasks_created = 0
        if post_ids_str and active_acc:
            gen_r = run_expert("tw_queue", sub({
                "action": "create_batch",
                "post_ids": post_ids_str,
                "account_id": active_acc,
                "reply_intent": reply_intent
            }), timeout=300)
            tasks_created = gen_r.get("tasks_created", 0)

        print(f"[4/8] ✅ Tasks created: {tasks_created}")
        save_workflow_state("review_replies", {**campaign, "tasks_pending": tasks_created})
        print("[5/8] ⏸️  Awaiting human review in Reply Queue")
        run_expert("tw_monitor", sub({"action": "update_analytics"}))
        print("[6/8] 📊 Analytics updated")
        print("[7/8] ✅")
        print("[8/8] 🚀 Done — review tasks at http://127.0.0.1:7842")
        return {
            "status": "success", "mode": "run",
            "profiles_found": profiles_found, "posts_saved": posts_saved,
            "tasks_created": tasks_created,
            "current_phase": "review_replies",
            "next_action": "Review tasks in Reply Queue at http://127.0.0.1:7842",
            "dry_run": dry_run
        }

    # ════════════════════════════════════════════════════════════
    # MODE: RESUME
    # ════════════════════════════════════════════════════════════
    elif mode == "resume":
        phase, payload = load_workflow_state()
        print(f"[2/8] 📌 Resuming from: {phase}")

        if phase in ("idle", "setup"):
            for i in range(6): print(f"[{i+3}/8] ✅")
            return {"status": "success",
                    "message": f"No active workflow (phase={phase})",
                    "ui_url": "http://127.0.0.1:7842"}

        if phase == "review_replies":
            pending = run_expert("tw_queue", sub({"action": "list", "status_filter": "pending"}))
            count = len(pending.get("tasks", []))
            for i in range(6): print(f"[{i+3}/8] ✅")
            return {"status": "success", "resumed_phase": phase,
                    "pending_tasks": count,
                    "message": f"In review phase — {count} tasks await approval.",
                    "action": "Open Reply Queue tab"}

        saved_kw = payload.get("keywords", keywords)
        if saved_kw and _TOKEN:
            print(f"[3/8] 🔄 Re-running for: '{saved_kw}'")
            for i in range(5): print(f"[{i+4}/8] ✅")
            return run_expert("tw_agent", sub({
                "mode": "run", "keywords": saved_kw, "api_token": _TOKEN,
                "topics": payload.get("topics", topics),
                "reply_intent": payload.get("reply_intent", reply_intent)
            }))

        for i in range(6): print(f"[{i+3}/8] ✅")
        return {"status": "success", "resumed_phase": phase, "payload": payload}

    # ════════════════════════════════════════════════════════════
    # MODE: BACKGROUND
    # ════════════════════════════════════════════════════════════
    elif mode == "background":

        if background_action == "stop":
            stopped = False
            if BG_LOCK.exists():
                try:
                    bg_data = json.loads(BG_LOCK.read_text(encoding="utf-8"))
                    pid = bg_data.get("pid")
                    if pid and is_pid_alive(int(pid)):
                        os.kill(int(pid), signal.SIGTERM)
                        stopped = True
                        print(f"[2/8] 🛑 Daemon stopped (PID {pid})")
                    BG_LOCK.unlink(missing_ok=True)
                except Exception as e:
                    print(f"[2/8] ⚠️  Stop error: {e}")
            kv_set("tw_background_enabled", "false")
            for i in range(6): print(f"[{i+3}/8] ✅")
            return {"status": "success",
                    "message": "Stopped" if stopped else "No daemon running"}

        if background_action == "status":
            running, pid = False, None
            if BG_LOCK.exists():
                try:
                    bg_data = json.loads(BG_LOCK.read_text(encoding="utf-8"))
                    pid = bg_data.get("pid")
                    running = bool(pid) and is_pid_alive(int(pid))
                except Exception:
                    pass
            for i in range(7): print(f"[{i+2}/8] ✅")
            return {"status": "success", "running": running, "pid": pid}

        # START daemon
        if BG_LOCK.exists():
            try:
                old = json.loads(BG_LOCK.read_text(encoding="utf-8"))
                old_pid = old.get("pid")
                if old_pid and is_pid_alive(int(old_pid)):
                    os.kill(int(old_pid), signal.SIGTERM)
                    time.sleep(0.5)
            except Exception:
                pass
            BG_LOCK.unlink(missing_ok=True)

        interval = int(get_setting("monitor_interval_min", "15"))
        tok_for_daemon = _TOKEN or ""

        daemon_lines = [
            "#!/usr/bin/env python3",
            "import time,json,requests,signal,sys",
            "from pathlib import Path",
            "from datetime import datetime",
            "",
            f"BASE_URL  = '{BASE_URL}'",
            f"TOKEN     = '{tok_for_daemon}'",
            f"DB_KEY    = '{db_path_key}'",
            f"TOK_KEY   = '{extella_token_key}'",
            f"INTERVAL  = {interval * 60}",
            f"LOCK_FILE = Path('{BG_LOCK}')",
            "",
            "def run_expert(name, params):",
            "    try:",
            "        r=requests.post(f'{BASE_URL}/api/expert/run',",
            "            headers={'X-Auth-Token':TOKEN,'Content-Type':'application/json'},",
            "            json={'expert_name':name,'params':params},timeout=120)",
            "        if r.status_code==200:",
            "            d=r.json()",
            "            return d.get('result') or d",
            "    except: pass",
            "    return {}",
            "",
            "def kv_set(key,value):",
            "    try:",
            "        requests.post(f'{BASE_URL}/api/kv/set',",
            "            headers={'X-Auth-Token':TOKEN},",
            "            json={'key':key,'value':value},timeout=10)",
            "    except: pass",
            "",
            "def on_stop(sig,frame):",
            "    LOCK_FILE.unlink(missing_ok=True)",
            "    sys.exit(0)",
            "",
            "signal.signal(signal.SIGTERM,on_stop)",
            "signal.signal(signal.SIGINT,on_stop)",
            "",
            f"print(f'🔄 Background daemon started. Interval: {interval}min')",
            "",
            "while True:",
            "    try:",
            "        kv_set('tw_last_heartbeat',datetime.utcnow().isoformat())",
            "        ts=datetime.utcnow().strftime('%H:%M:%S')",
            "        print(f'[{ts}] Checking conversations...')",
            "        result=run_expert('tw_monitor',{",
            "            'action':'check',",
            "            'db_path_key':DB_KEY,",
            "            'extella_token_key':TOK_KEY",
            "        })",
            "        n=result.get('new_responses',0)",
            "        if n>0: print(f'  ✅ {n} new responses — followup tasks created')",
            "    except Exception as e:",
            "        print(f'  ⚠️ Error: {e}')",
            "    time.sleep(INTERVAL)",
        ]

        APP_DIR.mkdir(parents=True, exist_ok=True)
        daemon_path = APP_DIR / "daemon.py"
        daemon_path.write_text("\n".join(daemon_lines), encoding="utf-8")

        proc = subprocess.Popen(
            [sys.executable, str(daemon_path)],
            cwd=str(APP_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        BG_LOCK.write_text(
            json.dumps({"pid": proc.pid, "started": datetime.utcnow().isoformat()}),
            encoding="utf-8"
        )
        kv_set("tw_background_enabled", "true")
        kv_set("tw_background_pid", str(proc.pid))

        print(f"[2/8] 🔄 Background daemon started (PID {proc.pid})")
        print(f"[3/8] ⏱️  Interval: {interval} min")
        for i in range(5): print(f"[{i+4}/8] ✅")
        return {
            "status": "success", "mode": "background",
            "pid": proc.pid, "interval_minutes": interval,
            "message": f"Background monitoring active (PID {proc.pid})"
        }

    return {
        "status":  "error",
        "message": f"Unknown mode: '{mode}'. Valid: launch/run/resume/status/background"
    }
