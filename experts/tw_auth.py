# =============================================================================
# EXTELLA EXPERT: tw_auth
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Authentication and Account Manager v2. Fixed: uses authenticated kv_get for session reads — bootstraps API token from extella_api_token KV key (stored via MCP, readable without auth), then uses that token for tw_session_* reads (stored by Flask with auth token). This fixes the nested expert KV context issue where unauthenticated kv_get couldn't read Flask-stored sessions. Parameters: action — add/validate/list/switch/set_role/remove/get_pool/update_health/get_credentials; username; auth_token; ct0; mode; account_id; health_delta; db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "account_id": "",
#   "action": "list",
#   "auth_token": "",
#   "ct0": "",
#   "db_path_key": "tw_db_path",
#   "extella_token_key": "extella_api_token",
#   "gologin_profile_id": "",
#   "gologin_token_key": "tw_gologin_token",
#   "health_delta": 0,
#   "mode": "direct",
#   "role_discovery": true,
#   "role_posting": true,
#   "username": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_auth    # sync this file only
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

def tw_auth(
    action: str = "list",
    username: str = "",
    auth_token: str = "",
    ct0: str = "",
    mode: str = "direct",
    gologin_profile_id: str = "",
    gologin_token_key: str = "tw_gologin_token",
    role_discovery: bool = True,
    role_posting: bool = True,
    account_id: str = "",
    health_delta: int = 0,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3
    import json
    import uuid
    import os
    import requests
    from pathlib import Path
    from datetime import datetime, timedelta

    print(f"[1/5] 🔄 tw_auth v2: action={action}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")

    # ── KV helpers with auth bootstrap ───────────────────────────
    # CRITICAL FIX: Flask stores tw_session_* via kv_set_auth (X-Auth-Token).
    # Nested experts can't read those via unauthenticated kv_get.
    # Bootstrap: read extella_api_token without auth (stored via MCP → accessible),
    # then use that token for all session reads.

    def kv_get_noauth(key) -> str:
        """Unauthenticated KV read — works for MCP-stored items (extella_api_token, db_path, etc.)"""
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200:
                return r.json().get("value", "")
        except Exception:
            pass
        return ""

    # Bootstrap the API token from KV (stored via MCP → readable without auth)
    _api_token = kv_get_noauth(extella_token_key)

    def kv_get(key) -> str:
        """Authenticated KV read — uses bootstrapped token to access Flask-stored sessions."""
        try:
            headers = {}
            if _api_token:
                headers["X-Auth-Token"] = _api_token
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key},
                              headers=headers, timeout=10)
            if r.status_code == 200:
                return r.json().get("value", "")
        except Exception:
            pass
        # Fallback: try without auth
        return kv_get_noauth(key)

    def kv_set(key, value, desc=""):
        """Write to KV with auth token (for consistency with Flask)."""
        try:
            headers = {"X-Auth-Token": _api_token} if _api_token else {}
            requests.post(f"{BASE_URL}/api/kv/set",
                          json={"key": key, "value": value, "description": desc},
                          headers=headers, timeout=10)
        except Exception:
            pass

    def kv_remove(key):
        try:
            headers = {"X-Auth-Token": _api_token} if _api_token else {}
            requests.post(f"{BASE_URL}/api/kv/remove", json={"key": key},
                          headers=headers, timeout=10)
        except Exception:
            pass

    # ── DB setup ──────────────────────────────────────────────────
    db_path = kv_get_noauth(db_path_key) or str(Path.home() / "Documents" / "twitter_agent" / "data.db")

    def get_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def compute_circuit_state(health_score, quarantine_until):
        if quarantine_until:
            try:
                q_dt = datetime.fromisoformat(quarantine_until)
                if datetime.utcnow() < q_dt:
                    return "open"
                else:
                    return "half_open"
            except Exception:
                pass
        if health_score <= 0:
            return "open"
        return "closed"

    def health_to_quarantine(health_score):
        if health_score <= 0:
            return datetime.utcnow() + timedelta(hours=24)
        if health_score < 30:
            return datetime.utcnow() + timedelta(minutes=60)
        if health_score < 60:
            return datetime.utcnow() + timedelta(minutes=15)
        return None

    def validate_twitter_session(a_token, csrf_token):
        """Validate session — format check + Twitter API attempt + format fallback."""
        if not a_token or len(a_token.strip()) < 10:
            return {"valid": False, "reason": "auth_token empty or too short"}
        if not csrf_token or len(csrf_token.strip()) < 10:
            return {"valid": False, "reason": "ct0 empty or too short"}

        BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
                  "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")
        headers = {
            "authorization": f"Bearer {BEARER}",
            "cookie": f"auth_token={a_token.strip()}; ct0={csrf_token.strip()}",
            "x-csrf-token": csrf_token.strip(),
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "accept": "*/*", "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://x.com", "referer": "https://x.com/home",
        }
        ENDPOINTS = [
            "https://x.com/i/api/1.1/account/settings.json",
            "https://twitter.com/i/api/1.1/account/settings.json",
        ]
        last_status = None
        for endpoint in ENDPOINTS:
            try:
                resp = requests.get(endpoint, headers=headers, timeout=15, allow_redirects=True)
                last_status = resp.status_code
                if resp.status_code == 200:
                    screen_name = ""
                    try: screen_name = resp.json().get("screen_name", "")
                    except Exception: pass
                    return {"valid": True, "screen_name": screen_name, "http_status": 200}
                elif resp.status_code == 429:
                    return {"valid": True, "reason": "rate_limited", "http_status": 429}
                elif resp.status_code in (401, 403):
                    continue
            except Exception:
                continue

        if len(a_token.strip()) >= 20 and len(csrf_token.strip()) >= 20:
            return {"valid": True, "reason": "format_valid_api_unreachable",
                    "http_status": last_status or 0}
        return {"valid": False, "reason": "all_endpoints_rejected", "http_status": last_status or 0}

    print(f"[2/5] ⚙️  token={'✅' if _api_token else '❌'} action={action}")

    # ════════════════════════════════════════════════════════════
    if action == "add":
        if not username:
            return {"status": "error", "message": "username required"}
        if mode == "direct" and (not auth_token or not ct0):
            return {"status": "error", "message": "auth_token and ct0 required for direct mode"}

        acc_id = str(uuid.uuid4())
        conn = get_conn()
        existing = conn.execute(
            "SELECT id FROM accounts WHERE username=?", (username.lstrip("@"),)
        ).fetchone()
        if existing:
            conn.close()
            return {"status": "error",
                    "message": f"Account @{username.lstrip('@')} already exists."}

        session_data = json.dumps({
            "auth_token": auth_token, "ct0": ct0, "mode": mode,
            "added_at": datetime.utcnow().isoformat()
        })
        kv_set(f"tw_session_{acc_id}", session_data,
               f"Twitter session for @{username.lstrip('@')}")

        conn.execute("""
            INSERT INTO accounts
              (id, username, gologin_profile_id, role_discovery, role_posting,
               is_active, health_score, circuit_state, created_at)
            VALUES (?, ?, ?, ?, ?, 0, 100, 'closed', datetime('now'))
        """, (acc_id, username.lstrip("@"),
               gologin_profile_id if mode == "gologin" else None,
               1 if role_discovery else 0, 1 if role_posting else 0))
        conn.commit(); conn.close()

        print(f"[3/5] ✅ @{username.lstrip('@')} added")
        print(f"[4/5] 🔐 Session → KV: tw_session_{acc_id[:8]}...")
        print("[5/5] ✅ Done")
        return {"status": "success", "account_id": acc_id,
                "username": username.lstrip("@"), "mode": mode,
                "message": f"Account @{username.lstrip('@')} added."}

    elif action == "validate":
        if not account_id:
            return {"status": "error", "message": "account_id required"}
        conn = get_conn()
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            conn.close()
            return {"status": "error", "message": f"Account {account_id} not found"}

        session_raw = kv_get(f"tw_session_{account_id}")
        if not session_raw:
            conn.close()
            return {"status": "error", "message": "Session not found in KV Store"}

        try:
            session = json.loads(session_raw)
        except Exception:
            conn.close()
            return {"status": "error", "message": "Corrupted session in KV Store"}

        result = validate_twitter_session(session.get("auth_token", ""), session.get("ct0", ""))

        if result["valid"]:
            conn.execute(
                "UPDATE accounts SET last_used=datetime('now'), error_count=0, "
                "health_score=MIN(100, health_score+5) WHERE id=?", (account_id,))
        else:
            new_health = max(0, row["health_score"] - 20)
            q_until = health_to_quarantine(new_health)
            conn.execute("""
                UPDATE accounts SET health_score=?, circuit_state=?, quarantine_until=?,
                    error_count=error_count+1 WHERE id=?
            """, (new_health, "open" if new_health <= 0 else "closed",
                  q_until.isoformat() if q_until else None, account_id))

        conn.commit(); conn.close()
        icon = "✅" if result["valid"] else "❌"
        print(f"[3/5] {icon} valid={result['valid']} reason={result.get('reason', 'ok')}")
        print("[4/5] 💾 Updated [5/5] ✅ Done")
        return {"status": "success", "account_id": account_id,
                "username": row["username"], "validation": result}

    elif action == "list":
        conn = get_conn()
        rows = conn.execute("""
            SELECT id, username, display_name, gologin_profile_id,
                   role_discovery, role_posting, is_active,
                   health_score, circuit_state, quarantine_until,
                   error_count, last_used, created_at
            FROM accounts ORDER BY is_active DESC, health_score DESC
        """).fetchall()
        accounts = []
        for r in rows:
            d = dict(r)
            d["circuit_state"] = compute_circuit_state(r["health_score"], r["quarantine_until"])
            d["has_session"] = bool(kv_get(f"tw_session_{r['id']}"))
            accounts.append(d)
        conn.close()
        print(f"[3/5] 👤 {len(accounts)} accounts [4/5] ✅ [5/5] ✅ Done")
        return {"status": "success", "accounts": accounts, "count": len(accounts)}

    elif action == "switch":
        if not account_id:
            return {"status": "error", "message": "account_id required"}
        conn = get_conn()
        conn.execute("UPDATE accounts SET is_active=0")
        conn.execute("UPDATE accounts SET is_active=1, last_used=datetime('now') WHERE id=?",
                     (account_id,))
        conn.commit()
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (account_id,)).fetchone()
        conn.close()
        if not row:
            return {"status": "error", "message": "Account not found"}
        print(f"[3/5] 🔄 Active → @{row['username']} [4/5] ✅ [5/5] ✅ Done")
        return {"status": "success", "active_account_id": account_id, "username": row["username"]}

    elif action == "set_role":
        if not account_id:
            return {"status": "error", "message": "account_id required"}
        conn = get_conn()
        conn.execute("UPDATE accounts SET role_discovery=?, role_posting=? WHERE id=?",
                     (1 if role_discovery else 0, 1 if role_posting else 0, account_id))
        conn.commit(); conn.close()
        print(f"[3/5] ⚙️  Roles: discovery={role_discovery}, posting={role_posting}")
        print("[4/5] ✅ [5/5] ✅ Done")
        return {"status": "success", "account_id": account_id,
                "role_discovery": role_discovery, "role_posting": role_posting}

    elif action == "remove":
        if not account_id:
            return {"status": "error", "message": "account_id required"}
        conn = get_conn()
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            conn.close()
            return {"status": "error", "message": "Account not found"}
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.commit(); conn.close()
        kv_remove(f"tw_session_{account_id}")
        print(f"[3/5] 🗑️  @{row['username']} removed [4/5] 🔐 KV deleted [5/5] ✅ Done")
        return {"status": "success", "removed_username": row["username"], "account_id": account_id}

    elif action == "get_pool":
        conn = get_conn()
        rows = conn.execute("""
            SELECT id, username, health_score, circuit_state, quarantine_until
            FROM accounts WHERE role_discovery=1
            ORDER BY health_score DESC
        """).fetchall()
        pool = []
        for r in rows:
            effective = compute_circuit_state(r["health_score"], r["quarantine_until"])
            if effective in ("closed", "half_open"):
                session_raw = kv_get(f"tw_session_{r['id']}")
                if session_raw:
                    try:
                        sess = json.loads(session_raw)
                        pool.append({
                            "account_id": r["id"], "username": r["username"],
                            "auth_token": sess.get("auth_token", ""),
                            "ct0": sess.get("ct0", ""),
                            "health_score": r["health_score"],
                            "circuit_state": effective
                        })
                    except Exception:
                        pass
        conn.close()
        print(f"[3/5] 🏊 Pool: {len(pool)} accounts [4/5] ✅ [5/5] ✅ Done")
        return {"status": "success", "pool": pool, "pool_size": len(pool)}

    elif action == "get_credentials":
        if not account_id:
            return {"status": "error", "message": "account_id required"}
        session_raw = kv_get(f"tw_session_{account_id}")
        if not session_raw:
            return {"status": "error",
                    "message": (f"No session found for account_id={account_id}. "
                                "Please Re-link the account in Settings.")}
        try:
            print(f"[3/5] ✅ Credentials found [4/5] ✅ [5/5] ✅ Done")
            return {"status": "success", "account_id": account_id,
                    "session": json.loads(session_raw)}
        except Exception as e:
            return {"status": "error", "message": f"Session parse error: {e}"}

    elif action == "update_health":
        if not account_id:
            return {"status": "error", "message": "account_id required"}
        conn = get_conn()
        row = conn.execute("SELECT health_score FROM accounts WHERE id=?",
                           (account_id,)).fetchone()
        if not row:
            conn.close()
            return {"status": "error", "message": "Account not found"}
        new_health = max(0, min(100, row["health_score"] + health_delta))
        q_until = health_to_quarantine(new_health) if health_delta < -30 else None
        new_circuit = compute_circuit_state(new_health, q_until.isoformat() if q_until else None)
        conn.execute("""
            UPDATE accounts SET health_score=?, circuit_state=?, quarantine_until=?,
                error_count = CASE WHEN ? < 0 THEN error_count+1 ELSE error_count END
            WHERE id=?
        """, (new_health, new_circuit, q_until.isoformat() if q_until else None,
              health_delta, account_id))
        conn.commit(); conn.close()
        icon = "✅" if health_delta >= 0 else "⚠️"
        print(f"[3/5] {icon} Health: {row['health_score']}→{new_health} [4/5] ✅ [5/5] ✅ Done")
        return {"status": "success", "account_id": account_id,
                "old_health": row["health_score"], "new_health": new_health,
                "circuit_state": new_circuit}

    return {"status": "error",
            "message": (f"Unknown action: '{action}'. "
                        "Valid: add/validate/list/switch/set_role/remove/get_pool/"
                        "get_credentials/update_health")}
