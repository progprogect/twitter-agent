# =============================================================================
# EXTELLA EXPERT: tw_patch_validate_endpoint
# =============================================================================
# DESCRIPTION: One-time patch: replaces _check_twitter_session in server.py to use working endpoints (home_timeline) instead of deprecated account/settings.json (returns 404). Handles compact formatting from SERVER_CODE string. Restarts server.
#
# KWARGS (default parameters):
# {}
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_patch_validate_endpoint    # sync this file only
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

def tw_patch_validate_endpoint() -> dict:
    import os, sys, json, signal, subprocess, time, re
    from pathlib import Path

    print("[1/4] 🔧 Patching _check_twitter_session in server.py")

    APP_DIR   = Path.home() / "Documents" / "twitter_agent"
    SERVER_PY = APP_DIR / "server.py"
    LOCK_FILE = APP_DIR / ".server.lock"

    if not SERVER_PY.exists():
        return {"status": "error", "message": "server.py not found."}

    original = SERVER_PY.read_text(encoding="utf-8")
    print(f"[2/4] 📄 Read {len(original)} chars")

    # Check what's actually in the file
    if "_check_twitter_session" not in original:
        return {"status": "error", "message": "_check_twitter_session not found in server.py."}

    # Show what endpoints are currently used
    if "settings.json" in original:
        current_ep = "account/settings.json (deprecated, returns 404)"
    elif "home_timeline" in original:
        current_ep = "home_timeline (already patched!)"
    else:
        current_ep = "unknown"
    print(f"[2/4] 📌 Current endpoint: {current_ep}")

    if "home_timeline" in original:
        print("[2/4] ℹ️  Already using home_timeline. Just restarting server...")
    else:
        # Replace the endpoint list inside _check_twitter_session
        # The compact version from SERVER_CODE string has this pattern:
        OLD_ENDPOINTS = (
            "for ep in['https://x.com/i/api/1.1/account/settings.json',\n"
            "              'https://twitter.com/i/api/1.1/account/settings.json']:"
        )

        NEW_ENDPOINTS = (
            "for ep_name,ep_url,ep_params in[\n"
            "        ('home_timeline','https://x.com/i/api/1.1/statuses/home_timeline.json',{'count':'1'}),\n"
            "        ('search_tweets','https://x.com/i/api/1.1/search/tweets.json',{'q':'twitter','count':'1'}),\n"
            "    ]:\n"
            "        import urllib.parse as _up\n"
            "        ep=ep_url+'?'+_up.urlencode(ep_params)"
        )

        # Also patch the API call inside the loop (was: requests.get(ep,...))
        # And patch the success return to include screen_name + endpoint name

        # Strategy: replace the entire endpoint check block using regex
        # Find the block from "for ep in[..." to end of the for loop (before next def or decorator)
        pattern = (
            r"for ep in\[.+?\]:.*?"      # for loop header
            r"(?=\n\s*log\('INFO',f'Validate: API unreachable)"  # until the fallback log
        )

        NEW_LOOP_BODY = """for ep_name, ep_url, ep_params in [
        ('home_timeline', 'https://x.com/i/api/1.1/statuses/home_timeline.json', {'count': '1'}),
        ('search_tweets', 'https://x.com/i/api/1.1/search/tweets.json', {'q': 'twitter', 'count': '1'}),
    ]:
        import urllib.parse as _up
        ep = ep_url + '?' + _up.urlencode(ep_params)
        try:
            resp=requests.get(ep,headers=headers,timeout=10,allow_redirects=True)
            last_status=resp.status_code
            log('INFO',f'Validate {ep_name}: HTTP {resp.status_code}')
            if resp.status_code==200:
                screen_name=''
                try:
                    data=resp.json()
                    if isinstance(data,list) and data:
                        screen_name=data[0].get('user',{}).get('screen_name','')
                    elif isinstance(data,dict):
                        ss=data.get('statuses',[])
                        if ss:screen_name=ss[0].get('user',{}).get('screen_name','')
                except Exception:pass
                return{'valid':True,'method':'api_confirmed','endpoint':ep_name,
                       'screen_name':screen_name,'http_status':200,
                       'detail':f'Confirmed via {ep_name}'+(f' @{screen_name}' if screen_name else '')}
            elif resp.status_code==429:
                return{'valid':True,'method':'rate_limited','http_status':429,'detail':'Rate limited = session active'}
        except:continue
    """

        # Use simple string replacement for the endpoints list
        # The compact server.py has this exact pattern:
        OLD_EP_SIMPLE = (
            "    for ep in['https://x.com/i/api/1.1/account/settings.json',\n"
            "              'https://twitter.com/i/api/1.1/account/settings.json']:\n"
            "        try:\n"
            "            resp=requests.get(ep,headers=headers,timeout=8,allow_redirects=True)\n"
            "            last_status=resp.status_code\n"
            "            log('INFO',f'Validate {ep[-30:]}: HTTP {resp.status_code}')\n"
            "            if resp.status_code==200:\n"
            "                sn=''\n"
            "                try:sn=resp.json().get('screen_name','')\n"
            "                except:pass\n"
            "                return{'valid':True,'method':'api_confirmed','screen_name':sn,'http_status':200}\n"
            "            elif resp.status_code==429:\n"
            "                return{'valid':True,'method':'rate_limited','http_status':429}\n"
            "            elif resp.status_code in(401,403):continue\n"
            "        except:continue\n"
        )

        NEW_EP_SIMPLE = (
            "    for ep_name,ep_url,ep_params in[\n"
            "        ('home_timeline','https://x.com/i/api/1.1/statuses/home_timeline.json',{'count':'1'}),\n"
            "        ('search_tweets','https://x.com/i/api/1.1/search/tweets.json',{'q':'twitter','count':'1'}),\n"
            "    ]:\n"
            "        import urllib.parse as _up\n"
            "        ep=ep_url+'?'+_up.urlencode(ep_params)\n"
            "        try:\n"
            "            resp=requests.get(ep,headers=headers,timeout=10,allow_redirects=True)\n"
            "            last_status=resp.status_code\n"
            "            log('INFO',f'Validate {ep_name}: HTTP {resp.status_code}')\n"
            "            if resp.status_code==200:\n"
            "                sn=''\n"
            "                try:\n"
            "                    data=resp.json()\n"
            "                    if isinstance(data,list) and data:sn=data[0].get('user',{}).get('screen_name','')\n"
            "                    elif isinstance(data,dict):\n"
            "                        ss=data.get('statuses',[])\n"
            "                        if ss:sn=ss[0].get('user',{}).get('screen_name','')\n"
            "                except:pass\n"
            "                return{'valid':True,'method':'api_confirmed','endpoint':ep_name,\n"
            "                       'screen_name':sn,'http_status':200,\n"
            "                       'detail':f'Confirmed via {ep_name}'+(f' @{sn}' if sn else '')}\n"
            "            elif resp.status_code==429:\n"
            "                return{'valid':True,'method':'rate_limited','http_status':429,\n"
            "                       'detail':'Rate limited = session active'}\n"
            "            elif resp.status_code in(401,403):continue\n"
            "        except:continue\n"
        )

        if OLD_EP_SIMPLE in original:
            patched = original.replace(OLD_EP_SIMPLE, NEW_EP_SIMPLE, 1)
            print("[2/4] ✅ Replaced endpoint block (exact match)")
        else:
            # Fallback: find and replace just the endpoint URLs
            patched = original.replace(
                "'https://x.com/i/api/1.1/account/settings.json'",
                "'https://x.com/i/api/1.1/statuses/home_timeline.json'", 1
            ).replace(
                "'https://twitter.com/i/api/1.1/account/settings.json'",
                "'https://x.com/i/api/1.1/search/tweets.json?q=twitter&count=1'", 1
            )
            print("[2/4] ⚠️  Used URL-only replacement (fallback)")

        SERVER_PY.write_text(patched, encoding="utf-8")
        print(f"[2/4] 💾 Written ({len(patched)} chars)")

    # ── Restart server ────────────────────────────────────────────
    port = 7842
    if LOCK_FILE.exists():
        try:
            lock    = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            port    = lock.get("port", 7842)
            old_pid = lock.get("pid")
            if old_pid:
                try:
                    os.kill(int(old_pid), signal.SIGTERM)
                    time.sleep(1.5)
                    print(f"[3/4] 🛑 Old server stopped (PID {old_pid})")
                except Exception:
                    pass
        except Exception:
            pass
        LOCK_FILE.unlink(missing_ok=True)

    token_file = APP_DIR / ".api_token"
    env = {**os.environ, "TW_PORT": str(port),
           "TW_DB_PATH": str(APP_DIR / "data.db")}
    if token_file.exists():
        env["TW_API_TOKEN"] = token_file.read_text(encoding="utf-8").strip()

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PY)],
        cwd=str(APP_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    LOCK_FILE.write_text(
        json.dumps({"pid": proc.pid, "port": port, "started": "patched_validate_ep"}),
        encoding="utf-8"
    )

    time.sleep(4)
    healthy = False
    try:
        import urllib.request
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=4)
        healthy = r.status == 200
    except Exception:
        pass

    import webbrowser
    if healthy:
        webbrowser.open(f"http://127.0.0.1:{port}")

    print(f"[4/4] {'✅ Server healthy' if healthy else '⚠️ Starting'} | PID {proc.pid}")
    return {
        "status":         "success" if healthy else "starting",
        "server_healthy": healthy,
        "pid":            proc.pid,
        "url":            f"http://127.0.0.1:{port}",
        "fix": ("Validate now uses home_timeline + search/tweets endpoints. "
                "Logs will show 'api_confirmed @username' instead of 'format_trusted'.")
    }
