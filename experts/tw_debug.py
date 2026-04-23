# =============================================================================
# EXTELLA EXPERT: tw_debug
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Debug and Diagnostics Expert. Reads server logs, checks account sessions, analyzes DB state, and returns full diagnostics. Can be called by the agent directly to inspect system state without user copy-pasting. Parameters: action — logs/session_check/diagnostics/db_info/relink_direct; lines — number of log lines to return (default 200); account_id — specific account for session_check; server_url — Flask server URL (default localhost:7842); db_path_key — KV key for SQLite path
#
# KWARGS (default parameters):
# {
#   "account_id": "",
#   "action": "diagnostics",
#   "db_path_key": "tw_db_path",
#   "lines": 200,
#   "server_url": "http://127.0.0.1:7842"
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_debug    # sync this file only
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

def tw_debug(
    action: str = "diagnostics",
    lines: int = 200,
    account_id: str = "",
    server_url: str = "http://127.0.0.1:7842",
    db_path_key: str = "tw_db_path"
) -> dict:
    import requests
    import sqlite3
    import json
    import os
    from pathlib import Path

    print(f"[1/4] 🔍 tw_debug: action={action}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    DB_PATH  = str(Path.home() / "Documents" / "twitter_agent" / "data.db")
    LOG_FILE = str(Path.home() / "Documents" / "twitter_agent" / "logs" / "server.log")

    def flask(path, method="GET", body=None, timeout=10):
        try:
            url = f"{server_url}{path}"
            if method == "GET":
                r = requests.get(url, timeout=timeout)
            else:
                r = requests.post(url, json=body or {}, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return {"error": f"HTTP {r.status_code}", "body": r.text[:200]}
        except Exception as e:
            return {"error": str(e), "note": "Server offline? Run tw_server(action='start')"}

    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200:
                return r.json().get("value", "")
        except Exception:
            pass
        return ""

    # ════════════════════════════════════════════════════════════
    if action == "logs":
        print(f"[2/4] 📋 Fetching {lines} log lines from Flask...")
        r = flask(f"/api/logs?lines={lines}")
        logs = r.get("logs", [])
        errors = [l for l in logs if "[ERROR]" in l]
        warnings = [l for l in logs if "[WARNING]" in l]
        print(f"[3/4] {'✅' if not errors else '❌'} {len(logs)} lines | {len(errors)} errors | {len(warnings)} warnings")
        print("[4/4] ✅ Done")
        return {
            "status": "success",
            "total_lines": r.get("total", 0),
            "returned_lines": len(logs),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "recent_errors": errors[-10:],
            "recent_warnings": warnings[-5:],
            "logs": logs
        }

    elif action == "session_check":
        print("[2/4] 👤 Checking account sessions via Flask...")
        accounts_r = flask("/api/accounts")
        accounts = accounts_r.get("accounts", [])

        # Also check KV directly for each account
        session_details = []
        for a in accounts:
            kv_raw = kv_get(f"tw_session_{a['id']}")
            has_kv = bool(kv_raw)
            kv_parsed = {}
            if kv_raw:
                try:
                    kv_parsed = json.loads(kv_raw)
                    # Mask tokens for security
                    if "auth_token" in kv_parsed:
                        kv_parsed["auth_token"] = kv_parsed["auth_token"][:8] + "..."
                    if "ct0" in kv_parsed:
                        kv_parsed["ct0"] = kv_parsed["ct0"][:8] + "..."
                except Exception:
                    pass

            session_details.append({
                "username": a["username"],
                "account_id": a["id"],
                "has_session_flask": a.get("has_session", False),  # from Flask kv_get_auth
                "has_session_kv": has_kv,                          # from agent kv_get (MCP)
                "session_fields": list(kv_parsed.keys()) if kv_parsed else [],
                "health": a.get("health_score", 0),
                "circuit": a.get("circuit_state", "unknown"),
                "is_active": a.get("is_active", False)
            })

        no_session = [a for a in session_details if not a["has_session_flask"]]
        discrepancy = [a for a in session_details if a["has_session_kv"] != a["has_session_flask"]]

        print(f"[3/4] 👤 {len(session_details)} accounts | No session: {len(no_session)} | Discrepancy: {len(discrepancy)}")
        print("[4/4] ✅ Done")
        return {
            "status": "success",
            "accounts": session_details,
            "no_session_count": len(no_session),
            "no_session_accounts": [a["username"] for a in no_session],
            "discrepancy_count": len(discrepancy),
            "discrepancy_note": (
                "Discrepancy means agent KV (MCP) and Flask KV (REST without token) disagree. "
                "This usually means Flask doesn't have a valid API token injected."
            ) if discrepancy else None
        }

    elif action == "diagnostics":
        print("[2/4] 🔍 Running full system diagnostics...")
        result = {}

        # Server health
        h = flask("/api/health")
        result["server"] = {
            "online": "status" in h and not h.get("error"),
            "url": server_url,
            "response": h
        }

        # Accounts + sessions (both checks)
        ar = flask("/api/accounts")
        accounts = ar.get("accounts", [])
        session_states = []
        for a in accounts:
            kv_raw = kv_get(f"tw_session_{a['id']}")
            session_states.append({
                "username": a["username"],
                "id_short": a["id"][:8] + "...",
                "has_session_flask": a.get("has_session", False),
                "has_session_agent_kv": bool(kv_raw),
                "health": a.get("health_score", 0),
                "is_active": a.get("is_active", False),
                "circuit": a.get("circuit_state", "closed")
            })

        result["accounts"] = {
            "count": len(accounts),
            "active": next((a["username"] for a in accounts if a.get("is_active")), None),
            "issues": [a["username"] for a in session_states if not a["has_session_flask"]],
            "details": session_states
        }

        # KV token check
        token = kv_get("extella_api_token")
        result["kv_token"] = {
            "present_in_agent": bool(token),
            "token_prefix": token[:12] + "..." if token else None,
            "note": "Token needed for Flask's kv_set_auth calls"
        }

        # DB stats
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            result["database"] = {
                "file": DB_PATH,
                "profiles": conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0],
                "posts": conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
                "pending_tasks": conn.execute(
                    "SELECT COUNT(*) FROM reply_tasks WHERE status='pending'"
                ).fetchone()[0],
                "total_tasks": conn.execute("SELECT COUNT(*) FROM reply_tasks").fetchone()[0],
                "workflow_phase": (conn.execute(
                    "SELECT phase FROM workflow_state WHERE id='current'"
                ).fetchone() or {})["phase"] if True else "unknown"
            }
            conn.close()
        except Exception as e:
            result["database"] = {"error": str(e)}

        # Recent logs
        lr = flask("/api/logs?lines=50")
        logs = lr.get("logs", [])
        errors = [l for l in logs if "[ERROR]" in l]
        result["logs"] = {
            "recent_error_count": len(errors),
            "recent_errors": errors[-5:],
            "last_5_lines": logs[-5:]
        }

        # Diagnosis
        issues = []
        if not result["server"]["online"]:
            issues.append("⛔ Flask server is offline — run tw_server(action='start')")
        for a in session_states:
            if not a["has_session_flask"] and a["has_session_agent_kv"]:
                issues.append(
                    f"⚠️ @{a['username']}: Session in KV but Flask can't read it — "
                    "Flask token might not be injected. Restart server with api_token parameter."
                )
            elif not a["has_session_flask"]:
                issues.append(
                    f"❌ @{a['username']}: No session. Click Re-link in Settings to fix."
                )
        if not result["kv_token"]["present_in_agent"]:
            issues.append("⚠️ extella_api_token not found in KV — Flask can't authenticate KV calls")

        result["issues"] = issues
        result["status"] = "healthy" if not issues else "needs_attention"

        print(f"[3/4] {'✅' if not issues else '⚠️'} Issues found: {len(issues)}")
        print("[4/4] ✅ Diagnostics complete")
        return {"status": "success", "diagnostics": result}

    elif action == "db_info":
        print("[2/4] 🗄️  Reading DB directly...")
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            info = {}
            for tbl in ["accounts", "profiles", "posts", "reply_tasks",
                        "conversations", "analytics", "settings"]:
                try:
                    info[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                except Exception:
                    info[tbl] = -1
            phase = conn.execute(
                "SELECT phase, payload FROM workflow_state WHERE id='current'"
            ).fetchone()
            settings_rows = conn.execute("SELECT key, value FROM settings").fetchall()
            conn.close()
            print(f"[3/4] 📊 {sum(v for v in info.values() if v >= 0)} total records")
            print("[4/4] ✅ Done")
            return {
                "status": "success",
                "db_path": DB_PATH,
                "row_counts": info,
                "workflow_phase": dict(phase) if phase else {},
                "settings": {r["key"]: r["value"] for r in settings_rows}
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    elif action == "relink_direct":
        """Emergency: directly write session to KV for an account using agent's KV context."""
        if not account_id:
            return {"status": "error", "message": "account_id required for relink_direct"}

        # Check account exists
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT username FROM accounts WHERE id=?", (account_id,)).fetchone()
            conn.close()
        except Exception as e:
            return {"status": "error", "message": f"DB error: {e}"}

        if not row:
            return {"status": "error", "message": f"Account {account_id} not found in DB"}

        # Check if there's already a session from the agent's perspective
        existing = kv_get(f"tw_session_{account_id}")
        print(f"[2/4] 🔍 Account: @{row['username']}, existing KV session: {bool(existing)}")
        print(f"[3/4] ℹ️  Cannot directly inject new tokens — use browser relink flow in UI")
        print("[4/4] ✅ Done")
        return {
            "status": "info",
            "username": row["username"],
            "account_id": account_id,
            "has_existing_session": bool(existing),
            "message": (
                "Use the Re-link button in Settings → Twitter Accounts to fix the session. "
                "This will open a browser, capture fresh cookies, and store them via Flask."
            )
        }

    return {
        "status": "error",
        "message": f"Unknown action: '{action}'. Valid: logs/session_check/diagnostics/db_info/relink_direct"
    }
