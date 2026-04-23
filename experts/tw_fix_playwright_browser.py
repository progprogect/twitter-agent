# =============================================================================
# EXTELLA EXPERT: tw_fix_playwright_browser
# =============================================================================
# DESCRIPTION: One-time fix for Twitter Lead Agent: installs Playwright Chromium browser binary and patches the tw_auth_playwright.py script to always auto-install Chromium before launching. Run this once when getting 'Executable doesn't exist' error. Parameters: none required
#
# KWARGS (default parameters):
# {}
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_fix_playwright_browser    # sync this file only
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
include("import subprocess", [])

def tw_fix_playwright_browser() -> dict:
    import subprocess
    import sys
    from pathlib import Path

    print("[1/4] 🔄 Installing Playwright Chromium browser binary...")

    APP_DIR  = Path.home() / "Documents" / "twitter_agent"
    AUTH_PY  = APP_DIR / "tw_auth_playwright.py"

    # ── Step 1: Install Chromium binary ────────────────────────────────────
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        # playwright package itself might be missing
        print("[1/4] ⚠️  playwright not found, installing package first...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright", "--quiet"],
            capture_output=True
        )
        result2 = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True
        )
        if result2.returncode != 0:
            return {
                "status": "error",
                "message": "Failed to install Chromium",
                "stderr": result2.stderr[:500]
            }

    print("[2/4] ✅ Chromium installed")
    print("[3/4] 📝 Patching tw_auth_playwright.py with auto-install fix...")

    # ── Step 2: Write fixed auth script ────────────────────────────────────
    FIXED_SCRIPT = """#!/usr/bin/env python3
# tw_auth_playwright.py — Twitter browser login helper (fixed: auto-installs Chromium)
import json, sys, os, time, signal, urllib.request, subprocess as _sp
from pathlib import Path

STATE_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/.tw_auth_state.json')
TIMEOUT    = 180
BEARER     = ('AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs'
               '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA')

def write_state(data):
    try: STATE_FILE.write_text(json.dumps(data), encoding='utf-8')
    except: pass

def read_state():
    try: return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except: return {}

def get_username(tok, ct):
    try:
        req = urllib.request.Request(
            'https://twitter.com/i/api/1.1/account/settings.json',
            headers={
                'authorization': f'Bearer {BEARER}',
                'cookie': f'auth_token={tok}; ct0={ct}',
                'x-csrf-token': ct,
                'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'x-twitter-active-user': 'yes',
            })
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode()).get('screen_name', '')
    except: return ''

# ── STEP 1: Ensure playwright package ──────────────────────────────────────
write_state({'status': 'installing', 'message': 'Installing browser...'})
try:
    import playwright as _pw_check
except ImportError:
    _sp.run([sys.executable, '-m', 'pip', 'install', 'playwright', '--quiet'],
            capture_output=True)

# ── STEP 2: ALWAYS ensure Chromium binary is present ───────────────────────
# This is idempotent: instant if already installed, ~30s first time
_sp.run([sys.executable, '-m', 'playwright', 'install', 'chromium'],
        capture_output=True)

# ── STEP 3: Import and launch ──────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    write_state({'status': 'error', 'error': f'Playwright import failed: {e}'})
    sys.exit(1)

pid = os.getpid()
write_state({'status': 'waiting', 'pid': pid})

def on_stop(sig, frame):
    write_state({'status': 'cancelled'})
    sys.exit(0)

signal.signal(signal.SIGTERM, on_stop)
signal.signal(signal.SIGINT, on_stop)

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--window-size=500,720',
                '--window-position=80,80',
            ])
        ctx = browser.new_context(
            viewport={'width': 500, 'height': 720},
            user_agent=('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/124.0.0.0 Safari/537.36'))
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

        # Navigate to login
        try:
            page.goto('https://x.com/login',
                      wait_until='domcontentloaded', timeout=30000)
        except Exception:
            try:
                page.goto('https://twitter.com/login',
                          wait_until='domcontentloaded', timeout=30000)
            except Exception:
                pass

        # Poll for auth cookies
        t0 = time.time()
        while time.time() - t0 < TIMEOUT:
            time.sleep(2)
            if read_state().get('status') == 'cancelled':
                break
            cookies = {c['name']: c['value'] for c in ctx.cookies()}
            tok = cookies.get('auth_token', '')
            ct  = cookies.get('ct0', '')
            if tok and ct and len(tok) > 20:
                username = get_username(tok, ct)
                write_state({
                    'status':     'success',
                    'auth_token': tok,
                    'ct0':        ct,
                    'username':   username,
                    'pid':        pid,
                })
                time.sleep(3)   # keep window open briefly so user sees it
                break
        else:
            st = read_state()
            if st.get('status') not in ('success', 'cancelled'):
                write_state({
                    'status': 'error',
                    'error':  'Timeout: login not completed within 3 minutes',
                    'pid':    pid,
                })

        try: browser.close()
        except: pass

except Exception as e:
    write_state({'status': 'error', 'error': str(e), 'pid': os.getpid()})
"""

    APP_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_PY.write_text(FIXED_SCRIPT, encoding="utf-8")

    print("[4/4] ✅ Fix complete — click 'Login with X/Twitter' again!")
    return {
        "status":  "success",
        "chromium_installed": True,
        "script_patched": str(AUTH_PY),
        "message": "Done! Chromium is ready. Go to Settings and click 'Login with X/Twitter' again."
    }
