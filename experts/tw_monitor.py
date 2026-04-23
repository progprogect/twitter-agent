# =============================================================================
# EXTELLA EXPERT: tw_monitor
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Conversation Monitor v3 (Flask credentials proxy + Playwright). Calls localhost:7842/api/credentials/pool — same fix as tw_discover/tw_posts. Chrome TLS for reply checking. Parameters: action — check/report/export/update_analytics; account_id; max_conversation_depth; auto_generate_followup; flask_port; db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "account_id": "",
#   "action": "report",
#   "auto_generate_followup": true,
#   "db_path_key": "tw_db_path",
#   "extella_token_key": "extella_api_token",
#   "flask_port": 7842,
#   "max_conversation_depth": 5
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_monitor    # sync this file only
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

def tw_monitor(
    action: str = "check",
    account_id: str = "",
    max_conversation_depth: int = 5,
    auto_generate_followup: bool = True,
    flask_port: int = 7842,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3, json, uuid, os, sys, site, importlib, time, random, subprocess, requests
    from pathlib import Path
    from datetime import datetime, timezone

    print(f"[1/5] 🔄 tw_monitor v3: action={action}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    BEARER   = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
                "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

    def setup_path():
        try:
            up = site.getusersitepackages()
            if up not in sys.path: sys.path.insert(0, up)
        except Exception: pass
        importlib.invalidate_caches()

    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200: return r.json().get("value", "")
        except Exception: pass
        return ""

    def run_expert(name, params):
        token = kv_get(extella_token_key)
        try:
            r = requests.post(f"{BASE_URL}/api/expert/run",
                headers={"X-Auth-Token": token, "Content-Type": "application/json"},
                json={"expert_name": name, "params": params}, timeout=120)
            if r.status_code == 200:
                data = r.json()
                result = data.get("result")
                return result if isinstance(result, dict) else (data if isinstance(data, dict) else {})
        except Exception as e: return {"error": str(e)}
        return {}

    def get_pool_from_flask():
        try:
            r = requests.get(f"http://127.0.0.1:{flask_port}/api/credentials/pool", timeout=10)
            if r.status_code == 200: return r.json().get("pool", [])
        except Exception as e:
            print(f"[*] Flask error: {e}")
        return []

    db_path = kv_get(db_path_key) or str(Path.home() / "Documents" / "twitter_agent" / "data.db")

    def get_conn():
        c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON"); return c

    FETCH_JS = """
        async ({url, params, bearer, ct0}) => {
            const u = new URL(url);
            Object.entries(params).forEach(([k,v]) => u.searchParams.set(k,String(v)));
            try {
                const resp = await fetch(u.toString(), {
                    method:'GET', credentials:'include',
                    headers:{
                        'authorization':'Bearer '+bearer,'x-csrf-token':ct0,
                        'x-twitter-active-user':'yes','x-twitter-client-language':'en',
                        'x-twitter-auth-type':'OAuth2Session','content-type':'application/json',
                        'accept':'*/*','referer':'https://x.com/home'
                    }
                });
                const text = await resp.text();
                let body = null;
                try { body = JSON.parse(text); } catch(e) { body = text; }
                return {status:resp.status, ok:resp.ok, body:body};
            } catch(e) { return {status:0,ok:false,error:e.message,body:null}; }
        }
    """

    # ════════════════════════════════════════════════════════════
    if action == "check":
        def ensure_playwright():
            setup_path()
            try:
                from playwright.sync_api import sync_playwright as _s; return True
            except ImportError:
                subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "--quiet"], capture_output=True)
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)
                setup_path()
                try: from playwright.sync_api import sync_playwright as _s; return True
                except ImportError: return False

        if not ensure_playwright():
            return {"status": "error", "message": "Playwright not available."}
        from playwright.sync_api import sync_playwright
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)

        print("[2/5] 🔐 Getting credentials from Flask proxy...")
        pool = get_pool_from_flask()
        if not pool:
            return {"status": "error", "message": "No active accounts for monitoring."}
        acc = pool[0]
        auth_token, ct0 = acc["auth_token"], acc["ct0"]

        conn = get_conn()
        where  = "AND rt.account_id=?" if account_id else ""
        params = [account_id] if account_id else []
        posted_tasks = conn.execute(f"""
            SELECT rt.id as task_id, rt.posted_tweet_id, rt.profile_id, rt.account_id,
                   p.url as post_url, pr.username as profile_username
            FROM reply_tasks rt
            LEFT JOIN posts p ON rt.post_id=p.id
            LEFT JOIN profiles pr ON rt.profile_id=pr.id
            WHERE rt.status='posted' AND rt.posted_tweet_id IS NOT NULL AND rt.posted_tweet_id != ''
              {where}
            LIMIT 50
        """, params).fetchall()
        conn.close()

        if not posted_tasks:
            print("[2/5] ℹ️  No posted tweets to monitor")
            return {"status": "success", "message": "No posted tweets to monitor yet.", "new_responses": 0}

        print(f"[2/5] 🔍 Monitoring {len(posted_tasks)} posted tweets...")

        browser_module = __import__("playwright.sync_api", fromlist=["sync_playwright"])
        sync_playwright_fn = browser_module.sync_playwright

        new_responses, followups_created = 0, 0

        with sync_playwright_fn() as pw:
            browser = pw.chromium.launch(headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
                extra_http_headers={"accept-language": "en-US,en;q=0.9"})
            for domain in [".x.com", ".twitter.com"]:
                ctx.add_cookies([
                    {"name": "auth_token", "value": auth_token, "domain": domain, "path": "/"},
                    {"name": "ct0",        "value": ct0,        "domain": domain, "path": "/"},
                ])
            page = ctx.new_page()
            try: page.goto("https://x.com/", wait_until="commit", timeout=20000)
            except Exception: pass

            try:
                def tw_api_get(url, params_d):
                    return page.evaluate(FETCH_JS,
                        {"url": url, "params": params_d, "bearer": BEARER, "ct0": ct0})

                for task in posted_tasks:
                    time.sleep(random.uniform(2.0, 4.0))
                    tweet_id = task["posted_tweet_id"]

                    conn = get_conn()
                    depth = conn.execute(
                        "SELECT MAX(depth) FROM conversations WHERE reply_task_id=?",
                        (task["task_id"],)).fetchone()[0] or 0
                    if depth >= max_conversation_depth: conn.close(); continue

                    existing_ids = {r[0] for r in conn.execute(
                        "SELECT tweet_id FROM conversations WHERE reply_task_id=?",
                        (task["task_id"],)).fetchall()}
                    conn.close()

                    tres = tw_api_get("https://x.com/i/api/1.1/statuses/show.json",
                        {"id": tweet_id, "include_reply_count": "true",
                         "tweet_mode": "extended", "include_entities": "true"})
                    if not tres.get("ok"): continue
                    if not isinstance(tres.get("body"), dict): continue
                    if tres["body"].get("reply_count", 0) == 0: continue

                    sres = tw_api_get("https://x.com/i/api/1.1/search/tweets.json",
                        {"q": f"to:me since_id:{tweet_id}", "count": "20",
                         "tweet_mode": "extended", "result_type": "recent"})
                    if not sres.get("ok"): continue
                    replies = sres.get("body", {}).get("statuses", []) if isinstance(sres.get("body"), dict) else []

                    for reply in replies:
                        rid = reply.get("id_str", "")
                        if rid in existing_ids: continue
                        if reply.get("in_reply_to_status_id_str") != tweet_id: continue

                        reply_text = reply.get("full_text") or reply.get("text") or ""
                        author     = reply.get("user", {}).get("screen_name", "")

                        conn = get_conn()
                        conv_id = str(uuid.uuid4())
                        conn.execute("""
                            INSERT OR IGNORE INTO conversations
                              (id,reply_task_id,depth,direction,tweet_id,text,author_username,created_at)
                            VALUES(?,?,?,'theirs',?,?,?,datetime('now'))
                        """, (conv_id, task["task_id"], depth+1, rid, reply_text[:1000], author))
                        conn.commit(); conn.close()
                        new_responses += 1
                        existing_ids.add(rid)

                        if auto_generate_followup:
                            history = [{"role": "assistant", "content": "Our previous reply"},
                                       {"role": "user",      "content": reply_text}]
                            gen = run_expert("tw_generate", {
                                "mode": "followup", "post_id": "",
                                "profile_id": task["profile_id"] or "",
                                "reply_intent": "continue conversation naturally, add value",
                                "conversation_history": json.dumps(history),
                                "db_path_key": db_path_key,
                                "extella_token_key": extella_token_key
                            })
                            if gen.get("status") == "success" and gen.get("generated_reply"):
                                fid = str(uuid.uuid4())
                                conn = get_conn()
                                conn.execute("""
                                    INSERT INTO reply_tasks
                                      (id,post_id,profile_id,account_id,generated_reply,
                                       reply_intent,status,created_at)
                                    SELECT ?,post_id,profile_id,account_id,?,
                                           'followup conversation','pending',datetime('now')
                                    FROM reply_tasks WHERE id=?
                                """, (fid, gen["generated_reply"], task["task_id"]))
                                conn.execute("UPDATE conversations SET followup_task_id=? WHERE id=?",
                                             (fid, conv_id))
                                conn.commit(); conn.close()
                                followups_created += 1
            finally:
                try: browser.close()
                except Exception: pass

        _update_analytics(get_conn, account_id)
        print(f"[4/5] 📊 New responses: {new_responses} | Followups: {followups_created}")
        print("[5/5] ✅ tw_monitor v3 check complete")
        return {"status": "success", "tweets_monitored": len(posted_tasks),
                "new_responses": new_responses, "followups_created": followups_created,
                "method": "playwright_flask_proxy"}

    elif action == "report":
        conn = get_conn()
        w = "WHERE account_id=?" if account_id else ""
        p = [account_id] if account_id else []
        summary  = conn.execute(f"SELECT SUM(replies_sent) as total_sent,SUM(responses_received) as total_responses,SUM(conversations_started) as total_conversations,AVG(response_rate) as avg_response_rate FROM analytics {w}", p).fetchone()
        timeline = conn.execute(f"SELECT date,SUM(replies_sent) sent,SUM(responses_received) received,AVG(response_rate) rate FROM analytics {w} GROUP BY date ORDER BY date DESC LIMIT 30", p).fetchall()
        conn.close()
        print("[2/5] 📊 [3/5] ✅ [4/5] ✅ [5/5] ✅")
        return {"status": "success", "summary": dict(summary) if summary else {},
                "timeline": [dict(r) for r in timeline]}

    elif action == "export":
        exports_dir = Path(db_path).parent / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        out_path = exports_dir / f"analytics_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        conn = get_conn()
        rows = conn.execute("""
            SELECT an.date, a.username, an.replies_sent, an.responses_received,
                   an.conversations_started, an.response_rate
            FROM analytics an JOIN accounts a ON an.account_id=a.id
            ORDER BY an.date DESC, a.username
        """).fetchall()
        conn.close()
        lines = ["date,account,replies_sent,responses_received,conversations,response_rate"]
        for r in rows: lines.append(",".join(str(v or 0) for v in [r[0],r[1],r[2],r[3],r[4],r[5]]))
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[2/5] 💾 Exported {len(rows)} rows [3/5] ✅ [4/5] ✅ [5/5] ✅")
        return {"status": "success", "output_path": str(out_path), "rows": len(rows)}

    elif action == "update_analytics":
        _update_analytics(get_conn, account_id)
        print("[2/5] 📊 Updated [3/5] ✅ [4/5] ✅ [5/5] ✅")
        return {"status": "success", "message": "Analytics updated"}

    return {"status": "error",
            "message": f"Unknown action: {action}. Valid: check/report/export/update_analytics"}


def _update_analytics(get_conn_fn, account_id: str):
    import sqlite3, uuid
    from datetime import datetime
    conn = get_conn_fn()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    accs = conn.execute(
        "SELECT id FROM accounts" + (" WHERE id=?" if account_id else ""),
        ([account_id] if account_id else [])
    ).fetchall()
    for acc in accs:
        aid  = acc["id"]
        sent = conn.execute(
            "SELECT COUNT(*) FROM reply_tasks WHERE account_id=? AND status='posted' AND DATE(posted_at)=?",
            (aid, today)).fetchone()[0]
        total_conv = conn.execute(
            "SELECT COUNT(DISTINCT reply_task_id) FROM conversations WHERE direction='theirs'"
        ).fetchone()[0]
        received = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE direction='theirs' AND DATE(created_at)=?",
            (today,)).fetchone()[0]
        rate = round(received / sent * 100, 2) if sent > 0 else 0.0
        conn.execute("""
            INSERT INTO analytics(id,account_id,date,replies_sent,responses_received,
                                  conversations_started,response_rate)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(account_id,date) DO UPDATE SET
              replies_sent=excluded.replies_sent,responses_received=excluded.responses_received,
              conversations_started=excluded.conversations_started,response_rate=excluded.response_rate
        """, (str(uuid.uuid4()), aid, today, sent, received, total_conv, rate))
    conn.commit(); conn.close()
