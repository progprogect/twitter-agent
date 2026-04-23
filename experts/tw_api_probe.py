# =============================================================================
# EXTELLA EXPERT: tw_api_probe
# =============================================================================
# DESCRIPTION: Diagnostic: probes multiple Twitter API endpoints to find what actually works for user search. Tests v1.1 users/search, search/tweets, and GraphQL user lookup. Returns raw responses for analysis.
#
# KWARGS (default parameters):
# {
#   "extella_token_key": "extella_api_token",
#   "flask_port": 7842,
#   "query": "AI"
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_api_probe    # sync this file only
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

def tw_api_probe(
    query: str = "AI",
    flask_port: int = 7842,
    extella_token_key: str = "extella_api_token"
) -> dict:
    import os, sys, site, importlib, subprocess, time, requests

    print(f"[1/5] 🔬 tw_api_probe: probing Twitter API endpoints for '{query}'")

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

    if not ensure_playwright():
        return {"status": "error", "message": "Playwright not available"}
    from playwright.sync_api import sync_playwright
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)

    # ── Get credentials ──────────────────────────────────────────
    print("[2/5] 🔐 Getting credentials from Flask proxy...")
    try:
        r = requests.get(f"http://127.0.0.1:{flask_port}/api/credentials/pool", timeout=10)
        pool = r.json().get("pool", [])
        if not pool:
            return {"status": "error", "message": "No accounts. Re-link in Settings."}
        auth_token = pool[0]["auth_token"]
        ct0        = pool[0]["ct0"]
        username   = pool[0]["username"]
    except Exception as e:
        return {"status": "error", "message": f"Flask proxy: {e}"}

    print(f"[2/5] ✅ @{username}")

    BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
              "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

    # Endpoints to probe
    ENDPOINTS = [
        ("users_search_v1",
         "https://x.com/i/api/1.1/users/search.json",
         {"q": query, "count": "3", "include_entities": "false"}),
        ("search_tweets_v1",
         "https://x.com/i/api/1.1/search/tweets.json",
         {"q": query, "count": "3", "result_type": "recent"}),
        ("typeahead",
         "https://x.com/i/api/1.1/search/typeahead.json",
         {"q": query, "src": "search_box", "result_type": "users"}),
        ("user_by_screen_name_graphql",
         "https://x.com/i/api/graphql/G3KGOASz96M-Qu0nwmGXNg/UserByScreenName",
         {"variables": '{"screen_name":"elonmusk","withSafetyModeUserFields":true}',
          "features": '{"hidden_profile_likes_enabled":true,"hidden_profile_subscriptions_enabled":true}'}),
    ]

    print("[3/5] 🌐 Probing endpoints via Playwright...")
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
        try:
            page.goto("https://x.com/", wait_until="commit", timeout=20000)
            print("[3/5] ✅ Navigated to x.com")
        except Exception as e:
            print(f"[3/5] ⚠️ Nav: {e}")

        for name, url, params in ENDPOINTS:
            try:
                result = page.evaluate("""
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
                            const ct = resp.headers.get('content-type')||'';
                            let body = null;
                            if (ct.includes('json')) {
                                body = await resp.json();
                            } else {
                                body = await resp.text();
                            }
                            return {status:resp.status,ok:resp.ok,body:body};
                        } catch(e) { return {status:0,ok:false,error:e.message}; }
                    }
                """, {"url": url, "params": params, "bearer": BEARER, "ct0": ct0})

                status = result.get("status", 0)
                ok     = result.get("ok", False)
                body   = result.get("body")

                # Summarize body
                summary = ""
                if isinstance(body, list):
                    summary = f"list({len(body)} items)"
                    if body:
                        first = body[0]
                        if isinstance(first, dict):
                            summary += f" first keys: {list(first.keys())[:5]}"
                elif isinstance(body, dict):
                    summary = f"dict keys: {list(body.keys())[:8]}"
                    # Check for users inside
                    for k in ["users", "statuses", "data", "results"]:
                        if k in body:
                            v = body[k]
                            cnt = len(v) if isinstance(v, list) else "?"
                            summary += f" | {k}:{cnt}"
                elif isinstance(body, str):
                    summary = f"text: {body[:80]}"

                results[name] = {
                    "url": url[:60],
                    "http_status": status,
                    "ok": ok,
                    "body_summary": summary,
                    "error": result.get("error")
                }
                icon = "✅" if ok else ("⚠️" if status == 404 else "❌")
                print(f"[3/5] {icon} {name}: HTTP {status} | {summary[:60]}")

            except Exception as e:
                results[name] = {"error": str(e)[:100], "ok": False}
                print(f"[3/5] ❌ {name}: {str(e)[:60]}")

            time.sleep(0.5)

        browser.close()

    print("[4/5] 📊 Summary...")
    working   = [n for n, v in results.items() if v.get("ok")]
    not_found = [n for n, v in results.items() if v.get("http_status") == 404]
    errors    = [n for n, v in results.items() if not v.get("ok") and v.get("http_status") != 404]

    verdict = ""
    if working:
        verdict = f"✅ Working endpoints: {working}"
    elif not_found == list(results.keys()):
        verdict = ("⚠️ All v1.1 endpoints return 404 — Twitter deprecated them. "
                   "Need GraphQL or scraping approach.")
    else:
        verdict = f"⚠️ Issues: {errors}"

    print(f"[4/5] {verdict}")
    print("[5/5] ✅ Done")

    return {
        "status":     "success",
        "username":   username,
        "query":      query,
        "results":    results,
        "working":    working,
        "not_found":  not_found,
        "errors":     errors,
        "verdict":    verdict,
        "recommendation": (
            "Use GraphQL user search (user_by_screen_name_graphql) or "
            "search/tweets.json with user mentions" if not working else
            f"Use {working[0]} for discovery"
        )
    }
