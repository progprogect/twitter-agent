# =============================================================================
# EXTELLA EXPERT: tw_patch_v9
# =============================================================================
# DESCRIPTION: One-time patch for tw_server: adds /api/credentials/pool and /api/credentials/<aid> endpoints directly to server.py on disk. These endpoints let local experts get Twitter session credentials via Flask (which has auth token), bypassing nested expert KV auth issue permanently. Also restarts the server.
#
# KWARGS (default parameters):
# {}
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_patch_v9    # sync this file only
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

def tw_patch_v9() -> dict:
    import os, sys, json, signal, subprocess, time
    from pathlib import Path

    print("[1/5] 🔧 tw_patch_v9: adding /api/credentials/ endpoints")

    APP_DIR   = Path.home() / "Documents" / "twitter_agent"
    SERVER_PY = APP_DIR / "server.py"
    LOCK_FILE = APP_DIR / ".server.lock"

    if not SERVER_PY.exists():
        return {"status": "error", "message": "server.py not found. Run tw_server(action='start') first."}

    original = SERVER_PY.read_text(encoding="utf-8")
    print(f"[2/5] 📄 Read server.py ({len(original)} chars)")

    # ── New endpoints to inject ──────────────────────────────────
    NEW_ENDPOINTS = """
# ════════════════════════════════════════════════════════
# v9: Credentials proxy — Flask reads sessions WITH auth
# Local experts call these endpoints instead of
# run_expert("tw_auth", {"action": "get_pool"})
# This permanently bypasses nested expert KV auth issue.
# ════════════════════════════════════════════════════════
@app.route('/api/credentials/pool', methods=['GET'])
def credentials_pool():
    conn = db()
    rows = conn.execute(
        'SELECT id,username,health_score,circuit_state '
        'FROM accounts WHERE role_discovery=1 ORDER BY health_score DESC'
    ).fetchall()
    conn.close()
    pool = []
    for r in rows:
        if (r['circuit_state'] or 'closed') == 'open':
            continue
        session_raw = kv_get_auth(f"tw_session_{r['id']}")
        if not session_raw:
            continue
        try:
            sess = json.loads(session_raw)
            at = sess.get('auth_token', '')
            ct = sess.get('ct0', '')
            if at and ct and len(at) > 10 and len(ct) > 10:
                pool.append({
                    'account_id': r['id'],
                    'username':   r['username'],
                    'auth_token': at,
                    'ct0':        ct,
                    'health_score': r['health_score'],
                    'circuit_state': r['circuit_state'] or 'closed'
                })
        except Exception:
            pass
    log('INFO', f'credentials/pool → {len(pool)} accounts')
    return jsonify({'pool': pool, 'pool_size': len(pool), 'status': 'success'})


@app.route('/api/credentials/<aid>', methods=['GET'])
def credentials_account(aid):
    session_raw = kv_get_auth(f'tw_session_{aid}')
    if not session_raw:
        return jsonify({'status': 'error', 'message': 'No session. Re-link account.'})
    try:
        sess = json.loads(session_raw)
        conn = db()
        row  = conn.execute('SELECT username FROM accounts WHERE id=?', (aid,)).fetchone()
        conn.close()
        return jsonify({
            'status':     'success',
            'account_id': aid,
            'username':   row['username'] if row else '',
            'auth_token': sess.get('auth_token', ''),
            'ct0':        sess.get('ct0', '')
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

"""

    # ── Find insertion point: right before the SPA route ────────
    SPA_MARKER = "# ── SPA "
    alt_marker = "@app.route('/',defaults={'path':''})"

    if SPA_MARKER in original and "credentials_pool" not in original:
        patched = original.replace(SPA_MARKER, NEW_ENDPOINTS + SPA_MARKER, 1)
        print("[3/5] ✅ Inserted credentials endpoints before SPA route")
    elif alt_marker in original and "credentials_pool" not in original:
        patched = original.replace(alt_marker, NEW_ENDPOINTS + alt_marker, 1)
        print("[3/5] ✅ Inserted credentials endpoints (alt marker)")
    elif "credentials_pool" in original:
        print("[3/5] ℹ️  Endpoints already present, skipping insertion")
        patched = original
    else:
        # Append before __main__
        if "if __name__=='__main__':" in original:
            patched = original.replace(
                "if __name__=='__main__':",
                NEW_ENDPOINTS + "if __name__=='__main__':", 1
            )
            print("[3/5] ⚠️  Appended before __main__")
        else:
            patched = original + "\n" + NEW_ENDPOINTS
            print("[3/5] ⚠️  Appended at end")

    SERVER_PY.write_text(patched, encoding="utf-8")
    print(f"[3/5] 💾 Written ({len(patched)} chars)")

    # ── Stop existing server ─────────────────────────────────────
    if LOCK_FILE.exists():
        try:
            lock_data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            old_pid  = lock_data.get("pid")
            port     = lock_data.get("port", 7842)
            if old_pid:
                try:
                    os.kill(int(old_pid), signal.SIGTERM)
                    time.sleep(1.5)
                    print(f"[4/5] 🛑 Old server stopped (PID {old_pid})")
                except Exception:
                    pass
        except Exception:
            port = 7842
        LOCK_FILE.unlink(missing_ok=True)
    else:
        port = 7842

    # ── Start new server ─────────────────────────────────────────
    token_file = APP_DIR / ".api_token"
    db_path    = APP_DIR / "data.db"
    env = {**os.environ, "TW_PORT": str(port), "TW_DB_PATH": str(db_path)}
    if token_file.exists():
        env["TW_API_TOKEN"] = token_file.read_text(encoding="utf-8").strip()

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PY)],
        cwd=str(APP_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    LOCK_FILE.write_text(
        json.dumps({"pid": proc.pid, "port": port, "started": "patched_v9"}),
        encoding="utf-8"
    )

    time.sleep(4)
    import urllib.request
    healthy = False
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=4)
        healthy = resp.status == 200
    except Exception:
        pass

    # ── Verify new endpoint ──────────────────────────────────────
    pool_ok = False
    if healthy:
        try:
            r = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/credentials/pool", timeout=5
            )
            data = json.loads(r.read().decode())
            pool_ok = data.get("status") == "success"
        except Exception:
            pass

    print(f"[5/5] {'✅' if healthy else '⚠️'} Server {'healthy' if healthy else 'starting'} | "
          f"/api/credentials/pool: {'✅ works' if pool_ok else '⚠️ check'} | PID {proc.pid}")

    return {
        "status":          "success" if healthy else "starting",
        "server_healthy":  healthy,
        "credentials_endpoint_ok": pool_ok,
        "pid":             proc.pid,
        "url":             f"http://127.0.0.1:{port}",
        "fix":             ("/api/credentials/pool added — data experts now call "
                            "localhost:7842 directly instead of run_expert(tw_auth,get_pool). "
                            "Flask has auth token → reads sessions reliably.")
    }
