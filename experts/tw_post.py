# =============================================================================
# EXTELLA EXPERT: tw_post
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Reply Poster v2 (Flask credentials proxy). Calls localhost:7842/api/credentials/<id> instead of nested run_expert(tw_auth, get_credentials) — bypasses KV auth issue. Playwright browser automation with human-like delays, rate limiting, dry-run mode. Parameters: task_id; account_id; proxy_mode (direct/gologin); dry_run; gologin_token_key; flask_port; db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "account_id": "",
#   "db_path_key": "tw_db_path",
#   "dry_run": false,
#   "extella_token_key": "extella_api_token",
#   "flask_port": 7842,
#   "gologin_token_key": "tw_gologin_token",
#   "proxy_mode": "direct",
#   "task_id": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_post    # sync this file only
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

def tw_post(
    task_id: str = "",
    account_id: str = "",
    proxy_mode: str = "direct",
    dry_run: bool = False,
    gologin_token_key: str = "tw_gologin_token",
    flask_port: int = 7842,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3, json, os, sys, site, importlib, time, random, subprocess, requests
    from pathlib import Path
    from datetime import datetime, timezone

    print(f"[1/6] 🔄 tw_post v2: task_id='{task_id or 'all approved'}', proxy={proxy_mode}, dry_run={dry_run}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")

    def setup_path():
        try:
            up = site.getusersitepackages()
            if up not in sys.path: sys.path.insert(0, up)
        except Exception: pass
        importlib.invalidate_caches()

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

    print("[2/6] 📦 Checking Playwright...")
    if not ensure_playwright():
        return {"status": "error", "message": "Playwright not available."}
    from playwright.sync_api import sync_playwright

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
                json={"expert_name": name, "params": params}, timeout=60)
            if r.status_code == 200:
                data = r.json()
                result = data.get("result")
                return result if isinstance(result, dict) else (data if isinstance(data, dict) else {})
        except Exception as e: return {"error": str(e)}
        return {}

    # Flask credentials proxy — fixes KV auth issue for nested experts
    def get_credentials_from_flask(acc_id: str):
        """Get auth_token + ct0 for specific account via Flask proxy."""
        try:
            r = requests.get(
                f"http://127.0.0.1:{flask_port}/api/credentials/{acc_id}", timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "success":
                    return data.get("auth_token", ""), data.get("ct0", "")
        except Exception as e:
            print(f"[*] Flask credentials error: {e}")
        return "", ""

    db_path = kv_get(db_path_key) or str(Path.home() / "Documents" / "twitter_agent" / "data.db")

    def get_conn():
        c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON"); return c

    def get_setting(conn, key, default=""):
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    print("[3/6] 📋 Loading approved tasks...")
    conn = get_conn()
    max_per_day = int(get_setting(conn, "max_replies_per_day", "20"))

    if task_id:
        rows = conn.execute(
            "SELECT rt.*, p.url as post_url, p.text as post_text, a.id as acc_id "
            "FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id "
            "LEFT JOIN accounts a ON rt.account_id=a.id "
            "WHERE rt.id=? AND rt.status='approved'", (task_id,)
        ).fetchall()
    else:
        rows = conn.execute("""
            SELECT rt.*, p.url as post_url, p.text as post_text, a.id as acc_id
            FROM reply_tasks rt
            LEFT JOIN posts p ON rt.post_id=p.id
            LEFT JOIN accounts a ON rt.account_id=a.id
            WHERE rt.status='approved'
            ORDER BY rt.approved_at ASC LIMIT ?
        """, (max_per_day,)).fetchall()

    tasks = [dict(r) for r in rows]
    conn.close()

    if not tasks:
        return {"status": "success", "message": "No approved tasks to post.", "posted": 0}

    print(f"[3/6] ✅ {len(tasks)} tasks to post")

    # Determine posting account
    posting_account_id = account_id or (tasks[0].get("account_id") or "")
    if not posting_account_id:
        conn = get_conn()
        row = conn.execute("SELECT id FROM accounts WHERE is_active=1 AND role_posting=1 LIMIT 1").fetchone()
        conn.close()
        if row: posting_account_id = row["id"]

    if not posting_account_id:
        return {"status": "error", "message": "No posting account. Add one in Settings."}

    # Get credentials via Flask proxy (KEY FIX: replaces run_expert(tw_auth, get_credentials))
    auth_token, ct0 = get_credentials_from_flask(posting_account_id)
    if not auth_token or not ct0:
        return {"status": "error",
                "message": ("Could not get credentials. "
                            "Re-link account in Settings → Twitter Accounts.")}

    # Rate limit check
    conn = get_conn()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    posted_today = conn.execute(
        "SELECT COUNT(*) FROM reply_tasks WHERE DATE(posted_at)=? AND status='posted'", (today,)
    ).fetchone()[0]
    conn.close()

    if posted_today >= max_per_day:
        return {"status": "error",
                "message": f"Daily limit: {posted_today}/{max_per_day} replies posted today."}

    print(f"[4/6] 🔐 Credentials OK | posted today: {posted_today}/{max_per_day}")

    posted, failed = [], []

    print("[5/6] 🚀 Starting Playwright session...")
    with sync_playwright() as pw:
        if proxy_mode == "gologin":
            try:
                gologin_token = kv_get(gologin_token_key)
                if not gologin_token:
                    return {"status": "error", "message": "GoLogin token not configured."}
                try: from gologin import GoLogin
                except ImportError:
                    subprocess.run([sys.executable, "-m", "pip", "install", "gologin", "--quiet"],
                                   capture_output=True)
                    from gologin import GoLogin
                conn = get_conn()
                pid_row = conn.execute(
                    "SELECT gologin_profile_id FROM accounts WHERE id=?", (posting_account_id,)
                ).fetchone()
                conn.close()
                if not pid_row or not pid_row["gologin_profile_id"]:
                    return {"status": "error", "message": "GoLogin profile_id not set for this account."}
                gl = GoLogin({"token": gologin_token, "profile_id": pid_row["gologin_profile_id"]})
                debugger_addr = gl.start()
                browser = pw.chromium.connect_over_cdp(f"http://{debugger_addr}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page    = context.pages[0] if context.pages else context.new_page()
            except Exception as e:
                return {"status": "error", "message": f"GoLogin error: {e}"}
        else:
            browser = pw.chromium.launch(
                headless=not dry_run,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                      f"--window-size={random.randint(1280,1920)},{random.randint(800,1080)}"])
            context = browser.new_context(
                viewport={"width": random.randint(1280,1920), "height": random.randint(800,1080)},
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
            context.add_cookies([
                {"name": "auth_token", "value": auth_token, "domain": ".twitter.com", "path": "/"},
                {"name": "ct0",        "value": ct0,        "domain": ".twitter.com", "path": "/"},
                {"name": "auth_token", "value": auth_token, "domain": ".x.com",       "path": "/"},
                {"name": "ct0",        "value": ct0,        "domain": ".x.com",       "path": "/"},
            ])
            page = context.new_page()

        try:
            for i, task in enumerate(tasks):
                if posted_today + len(posted) >= max_per_day:
                    print(f"[5/6] ⚠️ Daily limit reached after {len(posted)} posts")
                    break

                tweet_url  = task.get("post_url") or ""
                reply_text = task.get("edited_reply") or task.get("generated_reply") or ""
                tid        = task["id"]

                if not tweet_url or not reply_text:
                    failed.append({"task_id": tid, "reason": "missing url or reply text"})
                    continue

                print(f"[5/6]   [{i+1}/{len(tasks)}] → {tweet_url[:60]}...")

                try:
                    page.goto(tweet_url, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(random.uniform(1.5, 3.0))

                    ss_path = f"/tmp/tw_post_{tid[:8]}.png"
                    page.screenshot(path=ss_path)

                    if dry_run:
                        print(f"[5/6]   📸 Dry run screenshot: {ss_path}")
                        posted.append({"task_id": tid, "dry_run": True, "screenshot": ss_path})
                        continue

                    reply_btn = page.locator('[data-testid="reply"]').first
                    reply_btn.wait_for(timeout=10000)
                    time.sleep(random.uniform(0.5, 1.5))
                    reply_btn.click()
                    time.sleep(random.uniform(1.0, 2.0))

                    reply_input = page.locator('[data-testid="tweetTextarea_0"]').first
                    reply_input.wait_for(timeout=8000)
                    reply_input.click()
                    time.sleep(random.uniform(0.3, 0.8))

                    for char in reply_text:
                        page.keyboard.type(char)
                        time.sleep(random.uniform(0.04, 0.13))

                    time.sleep(random.uniform(1.0, 2.5))
                    page.screenshot(path=ss_path)

                    submit_btn = page.locator('[data-testid="tweetButtonInline"]').first
                    submit_btn.wait_for(timeout=5000)
                    submit_btn.click()
                    time.sleep(random.uniform(2.0, 4.0))

                    new_url = page.url
                    posted_tweet_id = ""
                    if "/status/" in new_url:
                        posted_tweet_id = new_url.split("/status/")[-1].split("?")[0]

                    conn = get_conn()
                    conn.execute("""
                        UPDATE reply_tasks
                        SET status='posted', posted_at=datetime('now'), posted_tweet_id=?
                        WHERE id=?
                    """, (posted_tweet_id, tid))
                    conn.commit(); conn.close()

                    posted.append({"task_id": tid, "posted_tweet_id": posted_tweet_id,
                                   "reply_text": reply_text[:80]})
                    run_expert("tw_auth", {"action": "update_health",
                                           "account_id": posting_account_id, "health_delta": 5})

                    if i < len(tasks) - 1:
                        wait = random.uniform(300, 420)
                        print(f"[5/6]   ⏳ Waiting {int(wait)}s...")
                        time.sleep(wait)

                except Exception as e:
                    msg = str(e)[:300]
                    print(f"[5/6]   ❌ Failed: {msg[:80]}")
                    conn = get_conn()
                    conn.execute("UPDATE reply_tasks SET status='failed' WHERE id=?", (tid,))
                    conn.commit(); conn.close()
                    run_expert("tw_auth", {"action": "update_health",
                                           "account_id": posting_account_id, "health_delta": -15})
                    failed.append({"task_id": tid, "reason": msg[:100]})
        finally:
            browser.close()
            if proxy_mode == "gologin":
                try: gl.stop()
                except Exception: pass

    print(f"[6/6] ✅ Posted: {len(posted)} | Failed: {len(failed)} | dry_run: {dry_run}")
    return {
        "status": "success", "posted_count": len(posted), "failed_count": len(failed),
        "dry_run": dry_run, "posted": posted, "failed": failed,
        "daily_total": posted_today + len(posted)
    }
