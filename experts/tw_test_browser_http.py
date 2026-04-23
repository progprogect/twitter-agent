# =============================================================================
# EXTELLA EXPERT: tw_test_browser_http
# =============================================================================
# DESCRIPTION: Test expert v4: verifies Twitter session validity and Chrome TLS bypass. Tries multiple API endpoints since settings.json returns 404 (deprecated). Tests: home_timeline, verify_credentials, user_timeline. Also tests Python/OpenSSL vs Playwright/Chrome. Parameters: account_id; flask_port (default 7842); db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "account_id": "",
#   "db_path_key": "tw_db_path",
#   "extella_token_key": "extella_api_token",
#   "flask_port": 7842
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_test_browser_http    # sync this file only
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

def tw_test_browser_http(
    account_id: str = "",
    flask_port: int = 7842,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import os, sys, site, importlib, time, subprocess, requests
    from pathlib import Path

    print("[1/6] 🔬 tw_test_browser_http v4: session validity + Chrome TLS")

    # ── Fix Extella's sys.path ────────────────────────────────────
    def setup_path():
        try:
            up = site.getusersitepackages()
            if up not in sys.path: sys.path.insert(0, up)
        except Exception: pass
        importlib.invalidate_caches()

    def ensure_playwright():
        setup_path()
        try:
            from playwright.sync_api import sync_playwright as _s
            return True
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "--quiet"],
                           capture_output=True)
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                           capture_output=True)
            setup_path()
            try:
                from playwright.sync_api import sync_playwright as _s
                return True
            except ImportError: return False

    if not ensure_playwright():
        return {"status": "error", "message": "Playwright not available."}
    from playwright.sync_api import sync_playwright
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)

    # ── Get credentials via Flask proxy ─────────────────────────
    print("[2/6] 🔐 Getting credentials from Flask proxy...")
    auth_token, ct0, username = "", "", ""
    try:
        url = (f"http://127.0.0.1:{flask_port}/api/credentials/{account_id}"
               if account_id else
               f"http://127.0.0.1:{flask_port}/api/credentials/pool")
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if account_id:
                auth_token = data.get("auth_token", "")
                ct0        = data.get("ct0", "")
                username   = data.get("username", "")
            else:
                pool = data.get("pool", [])
                if pool:
                    auth_token = pool[0]["auth_token"]
                    ct0        = pool[0]["ct0"]
                    username   = pool[0]["username"]
    except Exception as e:
        return {"status": "error", "message": f"Flask proxy error: {e}"}

    if not auth_token or not ct0:
        return {"status": "error",
                "message": "No credentials. Re-link account in Settings → Twitter Accounts."}

    print(f"[2/6] ✅ @{username} token={auth_token[:8]}... ct0={ct0[:8]}...")

    BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
              "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

    # Multiple endpoints to try (settings.json returns 404 as it's deprecated)
    TEST_ENDPOINTS = [
        ("https://x.com/i/api/1.1/account/verify_credentials.json?skip_status=true",
         "verify_credentials"),
        ("https://x.com/i/api/1.1/statuses/home_timeline.json?count=1",
         "home_timeline"),
        ("https://x.com/i/api/1.1/account/settings.json",
         "settings (deprecated)"),
    ]

    def build_headers(ct):
        return {
            "authorization": f"Bearer {BEARER}",
            "x-csrf-token": ct,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "accept": "*/*", "origin": "https://x.com", "referer": "https://x.com/home",
        }

    # ── Test 1: Python requests (OpenSSL) ─────────────────────────
    print("[3/6] 🐍 Test 1: Python requests (OpenSSL TLS)...")
    py_results = {}
    for ep_url, ep_name in TEST_ENDPOINTS:
        try:
            t0 = time.time()
            resp = requests.get(ep_url, headers=build_headers(ct0),
                                cookies={"auth_token": auth_token, "ct0": ct0},
                                timeout=12)
            elapsed = round(time.time() - t0, 2)
            body_sample = ""
            if resp.ok:
                try:
                    j = resp.json()
                    body_sample = (j.get("screen_name") or j.get("name") or
                                   str(j)[:60] if j else "")
                except Exception: pass
            py_results[ep_name] = {
                "http_status": resp.status_code, "ok": resp.ok,
                "elapsed_s": elapsed, "body_sample": body_sample
            }
            if resp.ok:
                print(f"[3/6] ✅ {ep_name}: HTTP {resp.status_code} | {body_sample[:40]}")
                break
            else:
                print(f"[3/6] ❌ {ep_name}: HTTP {resp.status_code}")
        except Exception as e:
            py_results[ep_name] = {"error": str(e)[:80], "ok": False}
            print(f"[3/6] ❌ {ep_name}: {str(e)[:60]}")

    py_ok    = any(v.get("ok") for v in py_results.values())
    py_best  = next(((n, v) for n, v in py_results.items() if v.get("ok")), (None, {}))

    # ── Test 2: Playwright page.evaluate(fetch()) ─────────────────
    print("[4/6] 🌐 Test 2: Playwright fetch() (Chrome BoringSSL TLS)...")
    pw_results = {}
    pw_ok = False

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
                extra_http_headers={"accept-language": "en-US,en;q=0.9"})
            for domain in [".x.com", ".twitter.com"]:
                context.add_cookies([
                    {"name": "auth_token", "value": auth_token, "domain": domain, "path": "/"},
                    {"name": "ct0",        "value": ct0,        "domain": domain, "path": "/"},
                ])
            page = context.new_page()

            t_nav = time.time()
            try:
                page.goto("https://x.com/", wait_until="commit", timeout=20000)
                nav_ok = True
            except Exception: nav_ok = False
            nav_s = round(time.time() - t_nav, 2)
            print(f"[4/6] Nav to x.com: {'✅' if nav_ok else '⚠️'} {nav_s}s")

            for ep_url, ep_name in TEST_ENDPOINTS:
                t0 = time.time()
                try:
                    eval_result = page.evaluate("""
                        async ({url, bearer, ct0}) => {
                            try {
                                const resp = await fetch(url, {
                                    method: 'GET', credentials: 'include',
                                    headers: {
                                        'authorization': 'Bearer ' + bearer,
                                        'x-csrf-token': ct0,
                                        'x-twitter-active-user': 'yes',
                                        'x-twitter-client-language': 'en',
                                        'x-twitter-auth-type': 'OAuth2Session',
                                        'content-type': 'application/json',
                                        'accept': '*/*', 'referer': 'https://x.com/home'
                                    }
                                });
                                const ct = resp.headers.get('content-type') || '';
                                const body = ct.includes('json') ? await resp.json() : await resp.text();
                                return {status: resp.status, ok: resp.ok, body: body};
                            } catch(e) { return {status:0, ok:false, error:e.message, body:null}; }
                        }
                    """, {"url": ep_url, "bearer": BEARER, "ct0": ct0})
                    elapsed = round(time.time() - t0, 2)
                    body_sample = ""
                    if eval_result.get("ok") and isinstance(eval_result.get("body"), dict):
                        j = eval_result["body"]
                        body_sample = j.get("screen_name") or j.get("name") or str(j)[:60]
                    pw_results[ep_name] = {
                        "http_status": eval_result.get("status", 0),
                        "ok": eval_result.get("ok", False),
                        "elapsed_s": elapsed, "body_sample": body_sample,
                        "error": eval_result.get("error")
                    }
                    if eval_result.get("ok"):
                        pw_ok = True
                        print(f"[4/6] ✅ {ep_name}: HTTP {eval_result.get('status')} | {body_sample[:40]}")
                        break
                    else:
                        print(f"[4/6] ❌ {ep_name}: HTTP {eval_result.get('status')} {eval_result.get('error', '')[:40]}")
                except Exception as e:
                    pw_results[ep_name] = {"error": str(e)[:80], "ok": False}
                    print(f"[4/6] ❌ {ep_name}: {str(e)[:60]}")

            browser.close()
    except Exception as e:
        pw_results["launch_error"] = {"error": str(e)[:150], "ok": False}
        print(f"[4/6] ❌ Playwright launch: {str(e)[:80]}")

    # ── Verdict ──────────────────────────────────────────────────
    print("[5/6] 📊 Verdict...")
    if pw_ok and not py_ok:
        verdict = "✅ BYPASS CONFIRMED: Chrome TLS passes, Python/OpenSSL blocked"
    elif pw_ok and py_ok:
        verdict = "✅ Both work — session valid, Playwright preferred for stability"
    elif not pw_ok and not py_ok:
        # Check if all 404 (endpoint issue vs auth issue)
        all_404 = all(
            v.get("http_status") == 404
            for results in [py_results, pw_results]
            for v in results.values()
            if "http_status" in v
        )
        if all_404:
            verdict = ("ℹ️ All endpoints return 404 (API endpoints changed/deprecated). "
                       "Session may be valid — try tw_discover(keywords='test', dry_run=True)")
        else:
            verdict = "⚠️ Session may be expired. Re-link in Settings."
    else:
        verdict = "⚠️ Unexpected result — check details below"

    print(f"[5/6] {verdict}")
    print("[6/6] ✅ Done")

    return {
        "status":           "success",
        "username_tested":  username,
        "token_format_ok":  len(auth_token) >= 20 and len(ct0) >= 20,
        "python_results":   py_results,
        "playwright_results": pw_results,
        "python_any_ok":    py_ok,
        "playwright_any_ok": pw_ok,
        "verdict":          verdict,
        "bypass_confirmed": pw_ok and not py_ok,
        "session_valid":    pw_ok or py_ok,
        "credentials_source": f"localhost:{flask_port}/api/credentials/pool",
        "note": ("404 from both = API endpoints deprecated, NOT auth failure. "
                 "Try tw_discover(keywords='test', dry_run=True) to test real API calls.")
    }
