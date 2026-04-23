# =============================================================================
# EXTELLA EXPERT: tw_probe_lookup
# =============================================================================
# DESCRIPTION: Diagnostic: tests users/lookup.json batch endpoint vs individual users/show.json to find which works for getting real follower counts after typeahead search.
#
# KWARGS (default parameters):
# {
#   "flask_port": 7842,
#   "test_usernames": "elonmusk,naval"
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_probe_lookup    # sync this file only
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

def tw_probe_lookup(
    test_usernames: str = "elonmusk,naval",
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

    # Get credentials
    try:
        r = requests.get(f"http://127.0.0.1:{flask_port}/api/credentials/pool", timeout=10)
        pool = r.json().get("pool", [])
        if not pool:
            return {"status": "error", "message": "No accounts"}
        auth_token, ct0 = pool[0]["auth_token"], pool[0]["ct0"]
        username = pool[0]["username"]
    except Exception as e:
        return {"status": "error", "message": f"Flask: {e}"}

    print(f"[1/3] Testing lookup endpoints as @{username}...")

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
                return {status:resp.status, ok:resp.ok, body:body,
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
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        for domain in [".x.com", ".twitter.com"]:
            ctx.add_cookies([
                {"name": "auth_token", "value": auth_token, "domain": domain, "path": "/"},
                {"name": "ct0",        "value": ct0,        "domain": domain, "path": "/"},
            ])
        page = ctx.new_page()
        try: page.goto("https://x.com/", wait_until="commit", timeout=20000)
        except Exception: pass

        tests = [
            ("users_lookup_batch",
             "https://x.com/i/api/1.1/users/lookup.json",
             {"screen_name": test_usernames, "include_entities": "false"}),
            ("users_show_single",
             "https://x.com/i/api/1.1/users/show.json",
             {"screen_name": "naval", "include_entities": "false"}),
            ("typeahead_vc",
             "https://x.com/i/api/1.1/search/typeahead.json",
             {"q": "venture capital", "src": "search_box", "result_type": "users"}),
        ]

        for name, url, params in tests:
            print(f"[2/3] Testing {name}...")
            try:
                r = page.evaluate(FETCH_JS,
                    {"url": url, "params": params, "bearer": BEARER, "ct0": ct0})
                status = r.get("status", 0)
                ok = r.get("ok", False)
                body = r.get("body")
                ct_hdr = r.get("content_type", "")
                # Summarize
                if isinstance(body, list):
                    summary = f"list({len(body)})"
                    if body and isinstance(body[0], dict):
                        first = body[0]
                        summary += f" first: screen_name={first.get('screen_name')} followers={first.get('followers_count')} id={first.get('id_str','')[:8]}"
                elif isinstance(body, dict):
                    summary = f"dict keys={list(body.keys())[:5]}"
                    # typeahead
                    if "users" in body:
                        users = body["users"]
                        summary += f" users={len(users)}"
                        if users:
                            u = users[0]
                            summary += f" first: sn={u.get('screen_name')} followers={u.get('followers_count')} social_proof={u.get('social_proof')}"
                elif isinstance(body, str):
                    summary = f"text(len={len(body)}) first80={body[:80]}"
                else:
                    summary = f"unknown type: {type(body)}"
                results[name] = {
                    "http_status": status, "ok": ok,
                    "content_type": ct_hdr,
                    "summary": summary
                }
                print(f"[2/3] {name}: HTTP {status} | {summary[:100]}")
            except Exception as e:
                results[name] = {"error": str(e)[:100]}
            time.sleep(0.5)

        browser.close()

    print("[3/3] ✅ Done")
    return {"status": "success", "username": username, "results": results}
