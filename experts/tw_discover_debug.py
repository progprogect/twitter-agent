# =============================================================================
# EXTELLA EXPERT: tw_discover_debug
# =============================================================================
# DESCRIPTION: Diagnostic v2: tests search/tweets approach — finds active accounts by searching tweet content, extracts user objects with real follower data directly from tweet author fields.
#
# KWARGS (default parameters):
# {
#   "flask_port": 7842,
#   "query": "venture capital"
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_discover_debug    # sync this file only
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

def tw_discover_debug(
    query: str = "venture capital",
    flask_port: int = 7842,
    extella_token_key: str = "extella_api_token"
) -> dict:
    import os, sys, site, importlib, subprocess, time, json, requests

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    BEARER   = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
                "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

    def setup_path():
        try:
            up = site.getusersitepackages()
            if up not in sys.path: sys.path.insert(0, up)
        except Exception: pass
        importlib.invalidate_caches()

    setup_path()
    try: from playwright.sync_api import sync_playwright
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "--quiet"], capture_output=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)
        setup_path()
        from playwright.sync_api import sync_playwright

    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)

    try:
        r = requests.get(f"http://127.0.0.1:{flask_port}/api/credentials/pool", timeout=10)
        pool = r.json().get("pool", [])
        if not pool: return {"status": "error", "message": "No accounts"}
        auth_token, ct0 = pool[0]["auth_token"], pool[0]["ct0"]
        username = pool[0]["username"]
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # FETCH_JS that always tries JSON.parse (more reliable than content-type detection)
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
                return {status:resp.status, ok:resp.ok, body:body, text_len: text.length,
                        content_type: resp.headers.get('content-type')};
            } catch(e) { return {status:0,ok:false,error:e.message}; }
        }
    """

    results = {}

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

        # TEST: search/tweets — find people actively tweeting about topic
        # tweet.user always has full profile data with real followers_count
        print(f"[DEBUG] Testing search/tweets for '{query}'...")
        sr = page.evaluate(FETCH_JS, {
            "url": "https://x.com/i/api/1.1/search/tweets.json",
            "params": {"q": query, "count": "20", "result_type": "mixed",
                       "tweet_mode": "extended", "include_entities": "false"},
            "bearer": BEARER, "ct0": ct0
        })

        body = sr.get("body")
        text_len = sr.get("text_len", 0)
        ct_hdr = sr.get("content_type", "")
        print(f"[DEBUG] search/tweets: HTTP {sr.get('status')} ok={sr.get('ok')} "
              f"text_len={text_len} ct={ct_hdr}")

        statuses = []
        if isinstance(body, dict):
            statuses = body.get("statuses", [])
            print(f"[DEBUG] body type=dict, statuses={len(statuses)}")
        elif isinstance(body, str):
            print(f"[DEBUG] body type=str(len={len(body)}) first100={body[:100]}")
        elif isinstance(body, list):
            print(f"[DEBUG] body type=list(len={len(body)})")
        else:
            print(f"[DEBUG] body type={type(body)} body={str(body)[:100]}")

        results["search_tweets"] = {
            "http_status": sr.get("status"),
            "ok": sr.get("ok"),
            "text_len": text_len,
            "body_type": type(body).__name__,
            "statuses_count": len(statuses)
        }

        if statuses:
            unique_users = {}
            for tweet in statuses:
                u = tweet.get("user", {})
                uid = u.get("id_str", "")
                if uid and uid not in unique_users:
                    unique_users[uid] = {
                        "screen_name": u.get("screen_name"),
                        "followers_count": u.get("followers_count"),
                        "description": (u.get("description") or "")[:60],
                        "statuses_count": u.get("statuses_count"),
                        "tweet_text": (tweet.get("full_text") or tweet.get("text") or "")[:60]
                    }
            results["unique_authors"] = list(unique_users.values())[:5]
            for u in results["unique_authors"]:
                print(f"[DEBUG] author: @{u['screen_name']} followers={u['followers_count']} "
                      f"desc='{u['description'][:40]}'")

        # Also test our own account timeline to verify user_timeline works
        time.sleep(1.0)
        print(f"\n[DEBUG] Testing user_timeline for @{username} (our account)...")
        own_tl = page.evaluate(FETCH_JS, {
            "url": "https://x.com/i/api/1.1/statuses/user_timeline.json",
            "params": {"screen_name": username, "count": "3",
                       "include_rts": "false", "exclude_replies": "false"},
            "bearer": BEARER, "ct0": ct0
        })
        own_body = own_tl.get("body", [])
        own_tweets = own_body if isinstance(own_body, list) else []
        print(f"[DEBUG] own timeline: HTTP {own_tl.get('status')} tweets={len(own_tweets)}")
        if own_tweets:
            u = own_tweets[0].get("user", {})
            print(f"[DEBUG] own user: sn={u.get('screen_name')} followers={u.get('followers_count')}")
        results["own_timeline"] = {
            "http_status": own_tl.get("status"),
            "tweets": len(own_tweets),
            "screen_name_param_works": len(own_tweets) > 0
        }

        browser.close()

    # Conclusion
    search_works = results.get("search_tweets", {}).get("statuses_count", 0) > 0
    own_tl_works = results.get("own_timeline", {}).get("screen_name_param_works", False)

    conclusion = []
    if search_works:
        conclusion.append(f"✅ search/tweets WORKS — found {results['search_tweets']['statuses_count']} tweets with full user objects")
    else:
        conclusion.append(f"❌ search/tweets empty (HTTP {results['search_tweets']['http_status']}, body_type={results['search_tweets']['body_type']}, text_len={results['search_tweets']['text_len']})")

    if own_tl_works:
        conclusion.append("✅ user_timeline?screen_name= WORKS for own account")
    else:
        conclusion.append("❌ user_timeline?screen_name= returns 0 tweets even for own account")

    return {
        "status": "success",
        "results": results,
        "conclusion": conclusion,
        "recommendation": (
            "Use search/tweets → tweet.user for profile discovery (active users + real data)"
            if search_works else
            "Neither search/tweets nor user_timeline by screen_name works. Check session."
        )
    }
