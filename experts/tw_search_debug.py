# =============================================================================
# EXTELLA EXPERT: tw_search_debug
# =============================================================================
# DESCRIPTION: Diagnostic v2: captures raw GraphQL SearchTimeline response to understand actual JSON structure for user extraction.
#
# KWARGS (default parameters):
# {
#   "flask_port": 7842,
#   "query": "venture capital"
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_search_debug    # sync this file only
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

def tw_search_debug(
    query: str = "venture capital",
    flask_port: int = 7842
) -> dict:
    import os, sys, site, importlib, subprocess, time, json, requests
    from urllib.parse import quote

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

    print(f"[DEBUG] @{username} | query='{query}'")

    raw_graphql = []

    def on_response(response):
        if "SearchTimeline" in response.url and response.status == 200:
            try:
                data = response.json()
                raw_graphql.append(data)
            except Exception as e:
                raw_graphql.append({"parse_error": str(e)})

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
        page.on("response", on_response)

        try: page.goto("https://x.com/", wait_until="commit", timeout=20000)
        except Exception: pass
        page.wait_for_timeout(1500)

        search_url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=people"
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(5000)
        except Exception as e:
            print(f"[DEBUG] Nav error: {str(e)[:60]}")

        try:
            page.evaluate("window.scrollBy(0, 500)")
            page.wait_for_timeout(2000)
        except Exception: pass

        browser.close()

    print(f"[DEBUG] Captured {len(raw_graphql)} SearchTimeline responses")

    analysis = []
    users_found = []

    for i, data in enumerate(raw_graphql):
        print(f"\n[DEBUG] Response {i+1}:")
        # Show top level structure
        def show_structure(d, indent=0, max_depth=4, path=""):
            if indent >= max_depth: return
            if isinstance(d, dict):
                for k, v in list(d.items())[:5]:
                    p = f"{path}.{k}"
                    if isinstance(v, (dict, list)):
                        print(f"{'  '*indent}{k}: {type(v).__name__}({len(v)})")
                        show_structure(v, indent+1, max_depth, p)
                    else:
                        print(f"{'  '*indent}{k}: {repr(v)[:60]}")
            elif isinstance(d, list):
                print(f"{'  '*indent}[list len={len(d)}]")
                if d: show_structure(d[0], indent+1, max_depth, f"{path}[0]")

        show_structure(data)

        # Try multiple possible structures
        # Structure 1: data.search_by_raw_query.search_timeline.timeline.instructions
        def try_extract_users(d):
            found = []
            def recurse(obj, depth=0):
                if depth > 10: return
                if isinstance(obj, dict):
                    # Check if this looks like a user legacy object
                    if "screen_name" in obj and "followers_count" in obj:
                        found.append({
                            "screen_name": obj.get("screen_name"),
                            "followers_count": obj.get("followers_count"),
                            "description": (obj.get("description") or "")[:60]
                        })
                        return
                    for v in obj.values():
                        recurse(v, depth+1)
                elif isinstance(obj, list):
                    for item in obj[:20]:  # Don't recurse too deep
                        recurse(item, depth+1)
            recurse(d)
            return found

        found = try_extract_users(data)
        users_found.extend(found)
        analysis.append({
            "response_idx": i,
            "top_keys": list(data.keys()) if isinstance(data, dict) else "not_dict",
            "users_extracted_by_recursive_search": len(found),
            "sample_user": found[0] if found else None
        })

    print(f"\n[DEBUG] Total users found by recursive extraction: {len(users_found)}")
    for u in users_found[:5]:
        print(f"[DEBUG]   @{u['screen_name']} followers={u['followers_count']}")

    # Also dump first response structure as JSON for analysis
    raw_dump = ""
    if raw_graphql:
        raw_dump = json.dumps(raw_graphql[0], default=str)[:2000]

    return {
        "status": "success",
        "search_timeline_hits": len(raw_graphql),
        "analysis": analysis,
        "users_found_recursive": users_found[:10],
        "raw_response_dump_2000chars": raw_dump,
        "conclusion": (
            f"✅ Found {len(users_found)} users via recursive extraction!"
            if users_found else
            f"❌ SearchTimeline returned {len(raw_graphql)} responses but no users found. "
            "Account may be restricted from People search."
        )
    }
