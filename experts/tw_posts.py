# =============================================================================
# EXTELLA EXPERT: tw_posts
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Posts Fetcher v4 (natural language + Flask credentials proxy + Playwright). Accepts content_description (natural language) → tw_query_expand → topic filters. Flask credentials proxy for reliable session access. Parameters: profile_ids; posts_per_profile; content_description — natural language (primary); topics — comma-separated (legacy); min_likes; since_hours; flask_port; db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "content_description": "",
#   "db_path_key": "tw_db_path",
#   "dry_run": false,
#   "extella_token_key": "extella_api_token",
#   "flask_port": 7842,
#   "min_likes": 0,
#   "posts_per_profile": 10,
#   "profile_ids": "",
#   "since_hours": 168,
#   "topics": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_posts    # sync this file only
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

def tw_posts(
    profile_ids: str = "",
    posts_per_profile: int = 10,
    content_description: str = "",
    topics: str = "",
    min_likes: int = 0,
    since_hours: int = 168,
    dry_run: bool = False,
    flask_port: int = 7842,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3, json, os, sys, site, importlib, time, random, subprocess, requests
    from pathlib import Path
    from datetime import datetime, timezone, timedelta

    print(f"[1/6] 🔄 tw_posts v4: profiles='{profile_ids or 'all'}', per={posts_per_profile}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    BEARER   = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
                "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

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
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "--quiet"],
                           capture_output=True)
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                           capture_output=True)
            setup_path()
            try: from playwright.sync_api import sync_playwright as _s; return True
            except ImportError: return False

    if not ensure_playwright():
        return {"status": "error", "message": "Playwright import failed."}
    from playwright.sync_api import sync_playwright
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)

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

    # ── Resolve topic filters ─────────────────────────────────────
    if content_description:
        print(f"[2/6] 🤖 Expanding content description: '{content_description[:50]}'...")
        expand = run_expert("tw_query_expand", {
            "user_description": content_description,
            "mode": "posts",
            "flask_port": flask_port,
            "extella_token_key": extella_token_key
        })
        topic_list = expand.get("topic_keywords", [])
        must_include = expand.get("must_include_any", [])
        ai_source = expand.get("source", "rule_based")
        print(f"[2/6] ✅ {ai_source}: {len(topic_list)} topic keywords, "
              f"{len(must_include)} must-include terms")
    else:
        topic_list   = [t.strip().lower() for t in topics.split(",") if t.strip()]
        must_include = []
        ai_source    = "legacy"
        print(f"[2/6] ℹ️  Using legacy topics: {topic_list[:5]}")

    # ── Get credentials ───────────────────────────────────────────
    print("[3/6] 🔐 Getting credentials from Flask proxy...")
    pool = get_pool_from_flask()
    if not pool:
        return {"status": "error", "message": "No active accounts. Re-link in Settings."}
    account    = pool[0]
    auth_token = account["auth_token"]
    ct0        = account["ct0"]

    conn = get_conn()
    if profile_ids.strip():
        ids  = [p.strip() for p in profile_ids.split(",") if p.strip()]
        rows = conn.execute(
            f"SELECT id, username FROM profiles WHERE id IN ({','.join(['?']*len(ids))})", ids
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, username FROM profiles ORDER BY followers_count DESC LIMIT 50"
        ).fetchall()
    conn.close()

    profiles_to_fetch = [dict(r) for r in rows]
    print(f"[3/6] ✅ @{account['username']} | {len(profiles_to_fetch)} profiles")
    if not profiles_to_fetch:
        return {"status": "error", "message": "No profiles found. Run tw_discover first."}

    since_dt      = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    tw_fmt        = "%a %b %d %H:%M:%S +0000 %Y"
    total_saved   = 0
    total_skipped = 0
    preview       = []

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

    def post_matches(text: str) -> bool:
        """Check if post matches topic filters."""
        if not topic_list and not must_include:
            return True  # No filter = accept all
        text_lower = text.lower()
        # If must_include_any provided: at least one must appear
        if must_include:
            if not any(t.lower() in text_lower for t in must_include):
                return False
        # If topic_list: at least one topic should match
        if topic_list:
            if not any(t.lower() in text_lower for t in topic_list):
                return False
        return True

    def matched_topics_for(text: str) -> list:
        """Return matched topic keywords for a post."""
        text_lower = text.lower()
        return [t for t in topic_list if t.lower() in text_lower]

    print(f"[4/6] 📥 Fetching posts for {len(profiles_to_fetch)} profiles...")

    with sync_playwright() as pw:
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
            for idx, profile in enumerate(profiles_to_fetch):
                time.sleep(random.uniform(1.0, 2.5))
                print(f"[4/6]   [{idx+1}/{len(profiles_to_fetch)}] @{profile['username']}")

                tres = page.evaluate(FETCH_JS, {
                    "url": "https://x.com/i/api/1.1/statuses/user_timeline.json",
                    "params": {"user_id": profile["id"],
                               "count": str(min(posts_per_profile*2, 40)),
                               "include_rts": "false", "exclude_replies": "false",
                               "tweet_mode": "extended"},
                    "bearer": BEARER, "ct0": ct0
                })

                if not tres.get("ok"):
                    s = tres.get("status", 0)
                    if s == 429:
                        print(f"[4/6] ⚠️ Rate limited, skipping @{profile['username']}")
                        continue
                    if s in (401, 403):
                        print(f"[4/6] ❌ Auth error HTTP {s}, stopping")
                        break
                    print(f"[4/6] ⚠️ HTTP {s} @{profile['username']}, skipping")
                    continue

                body = tres.get("body", [])
                tweets = body if isinstance(body, list) else []

                saved_for_profile = 0
                conn = get_conn()
                existing_ids = {r[0] for r in conn.execute(
                    "SELECT id FROM posts WHERE profile_id=?", (profile["id"],)).fetchall()}

                for tweet in tweets:
                    if saved_for_profile >= posts_per_profile: break
                    tweet_id = tweet.get("id_str", "")
                    if not tweet_id or tweet_id in existing_ids:
                        total_skipped += 1; continue

                    try:
                        tweet_dt = datetime.strptime(tweet["created_at"], tw_fmt).replace(tzinfo=timezone.utc)
                        if tweet_dt < since_dt: continue
                    except Exception: pass

                    text = tweet.get("full_text") or tweet.get("text") or ""
                    if not post_matches(text): continue

                    likes = int(tweet.get("favorite_count", 0) or 0)
                    if likes < min_likes: continue

                    url_tw = f"https://twitter.com/{profile['username']}/status/{tweet_id}"
                    try: posted_at = datetime.strptime(tweet["created_at"], tw_fmt).isoformat()
                    except Exception: posted_at = None
                    matched = matched_topics_for(text)

                    if not dry_run:
                        conn.execute("""
                            INSERT OR IGNORE INTO posts
                              (id,profile_id,text,url,posted_at,likes,replies_count,
                               retweets,views,lang,matched_topics,fetched_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                        """, (tweet_id, profile["id"], text[:1000], url_tw, posted_at, likes,
                              int(tweet.get("reply_count", 0) or 0),
                              int(tweet.get("retweet_count", 0) or 0), 0,
                              tweet.get("lang", ""), json.dumps(matched)))
                        saved_for_profile += 1; total_saved += 1
                    else:
                        preview.append({"profile": profile["username"], "tweet_id": tweet_id,
                                        "text": text[:120], "likes": likes, "matched": matched})
                        saved_for_profile += 1

                if not dry_run: conn.commit()
                conn.close()
        finally:
            try: browser.close()
            except Exception: pass

    run_expert("tw_auth", {"action": "update_health",
                           "account_id": account["account_id"], "health_delta": 5})

    print(f"[5/6] ✅ Saved: {total_saved} | Skipped (dup): {total_skipped}")
    print("[6/6] ✅ tw_posts v4 complete!")
    return {
        "status":               "success",
        "profiles_processed":   len(profiles_to_fetch),
        "posts_saved":          total_saved if not dry_run else 0,
        "posts_skipped_duplicate": total_skipped,
        "dry_run":              dry_run,
        "ai_source":            ai_source,
        "topic_keywords_used":  topic_list,
        "must_include_used":    must_include,
        "preview":              preview[:10] if dry_run else [],
        "account_used":         account["username"],
        "method":               "playwright_flask_proxy_v4"
    }
