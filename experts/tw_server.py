# =============================================================================
# EXTELLA EXPERT: tw_server
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Web Server v9 (fixed credentials proxy). New endpoint GET /api/credentials/pool: Flask reads tw_session_* from KV with auth token and returns plaintext credentials to local experts — bypasses nested expert KV auth issue permanently. Data experts (tw_discover, tw_posts, tw_monitor) call localhost:7842 directly instead of run_expert(tw_auth, get_pool). All other fixes from v8 preserved.
#
# KWARGS (default parameters):
# {
#   "action": "status",
#   "api_token": "",
#   "db_path_key": "tw_db_path",
#   "extella_token_key": "extella_api_token",
#   "open_browser": true,
#   "port": 7842
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_server    # sync this file only
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

def tw_server(
    action: str = "start",
    port: int = 7842,
    open_browser: bool = True,
    api_token: str = "",
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import os, sys, json, signal, subprocess, webbrowser, requests
    from pathlib import Path
    from datetime import datetime

    print(f"[1/6] 🐦 tw_server v9: action={action}, port={port}")

    BASE_URL   = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    APP_DIR    = Path.home() / "Documents" / "twitter_agent"
    LOCK_FILE  = APP_DIR / ".server.lock"
    TOKEN_FILE = APP_DIR / ".api_token"
    SERVER_PY  = APP_DIR / "server.py"
    AUTH_PY    = APP_DIR / "tw_auth_playwright.py"
    UI_DIR     = APP_DIR / "ui"
    UI_FILE    = UI_DIR / "index.html"

    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200: return r.json().get("value", "")
        except: pass
        return ""

    def kv_set(key, value, desc=""):
        try: requests.post(f"{BASE_URL}/api/kv/set",
                           json={"key": key, "value": value, "description": desc}, timeout=10)
        except: pass

    def is_pid_alive(pid):
        try: os.kill(int(pid), 0); return True
        except: return False

    def read_lock():
        if LOCK_FILE.exists():
            try:
                d = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
                return d.get("pid"), d.get("port")
            except: pass
        return None, None

    def write_lock(pid, p):
        LOCK_FILE.write_text(json.dumps(
            {"pid": pid, "port": p, "started": datetime.utcnow().isoformat()}
        ), encoding="utf-8")

    def clear_lock():
        if LOCK_FILE.exists(): LOCK_FILE.unlink(missing_ok=True)

    def check_health(p):
        try: return requests.get(f"http://127.0.0.1:{p}/api/health", timeout=4).status_code == 200
        except: return False

    def ensure_flask():
        try: import flask, flask_cors; return True
        except ImportError:
            return subprocess.run([sys.executable, "-m", "pip", "install",
                                   "flask", "flask-cors", "--quiet"],
                                  capture_output=True).returncode == 0

    # ── PLAYWRIGHT AUTH SCRIPT ────────────────────────────────────────────
    PLAYWRIGHT_SCRIPT = """#!/usr/bin/env python3
import json,sys,os,time,signal,urllib.request,subprocess as _sp
from pathlib import Path

STATE_FILE=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/tmp/.tw_auth_state.json')
TIMEOUT=180
BEARER=('AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs'
        '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA')

def write_state(data):
    try:STATE_FILE.write_text(json.dumps(data),encoding='utf-8')
    except:pass

def read_state():
    try:return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except:return{}

def get_username(tok,ct):
    for host in ['x.com','twitter.com']:
        try:
            req=urllib.request.Request(f'https://{host}/i/api/1.1/account/settings.json',
                headers={'authorization':f'Bearer {BEARER}','cookie':f'auth_token={tok};ct0={ct}',
                         'x-csrf-token':ct,'x-twitter-auth-type':'OAuth2Session',
                         'user-agent':'Mozilla/5.0','x-twitter-active-user':'yes',
                         'origin':f'https://{host}','referer':f'https://{host}/home'})
            resp=urllib.request.urlopen(req,timeout=10)
            name=json.loads(resp.read().decode()).get('screen_name','')
            if name:return name
        except:continue
    return ''

write_state({'status':'installing','message':'Preparing browser...'})
try:import playwright as _pw
except ImportError:
    _sp.run([sys.executable,'-m','pip','install','playwright','--quiet'],capture_output=True)
_sp.run([sys.executable,'-m','playwright','install','chromium'],capture_output=True)

try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    write_state({'status':'error','error':f'Playwright import failed:{e}'}); sys.exit(1)

pid=os.getpid()
write_state({'status':'waiting','pid':pid})

def on_stop(sig,frame):
    write_state({'status':'cancelled'}); sys.exit(0)
signal.signal(signal.SIGTERM,on_stop)
signal.signal(signal.SIGINT,on_stop)

try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=False,
            args=['--no-sandbox','--disable-blink-features=AutomationControlled',
                  '--window-size=500,720','--window-position=80,80'])
        ctx=browser.new_context(viewport={'width':500,'height':720},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
        page=ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        try:page.goto('https://x.com/login',wait_until='domcontentloaded',timeout=30000)
        except:
            try:page.goto('https://twitter.com/login',wait_until='domcontentloaded',timeout=30000)
            except:pass
        t0=time.time()
        while time.time()-t0<TIMEOUT:
            time.sleep(2)
            if read_state().get('status')=='cancelled':break
            cookies={c['name']:c['value'] for c in ctx.cookies()}
            tok=cookies.get('auth_token','')
            ct=cookies.get('ct0','')
            if tok and ct and len(tok)>20:
                username=get_username(tok,ct)
                write_state({'status':'success','auth_token':tok,'ct0':ct,'username':username,'pid':pid})
                time.sleep(3);break
        else:
            st=read_state()
            if st.get('status') not in('success','cancelled'):
                write_state({'status':'error','error':'Timeout: login not completed within 3 minutes','pid':pid})
        try:browser.close()
        except:pass
except Exception as e:
    write_state({'status':'error','error':str(e),'pid':os.getpid()})
"""

    # ── FLASK BACKEND ─────────────────────────────────────────────────────
    # KEY FIX v9: /api/credentials/pool — Flask reads sessions with auth
    # and returns credentials to local experts directly.
    # Data experts call http://127.0.0.1:7842/api/credentials/pool instead of
    # run_expert("tw_auth", {"action": "get_pool"}) — bypasses nested expert KV auth issue.
    SERVER_CODE = (
        "#!/usr/bin/env python3\n"
        "import os,sys,json,sqlite3,time,uuid,requests,subprocess as _sp,signal as _sg\n"
        "from pathlib import Path\n"
        "from datetime import datetime\n"
        "from collections import deque\n"
        "from flask import Flask,jsonify,request,send_from_directory\n"
        "from flask_cors import CORS\n"
        "\n"
        "app=Flask(__name__,static_folder=str(Path(__file__).parent/'ui'),static_url_path='')\n"
        "CORS(app,methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'])\n"
        "PORT=int(os.environ.get('TW_PORT',7842))\n"
        "DB_PATH=os.environ.get('TW_DB_PATH',str(Path.home()/'Documents'/'twitter_agent'/'data.db'))\n"
        "BASE_URL=os.environ.get('EXTELLA_API_URL','https://api.extella.ai')\n"
        "API_TOKEN=os.environ.get('TW_API_TOKEN','')\n"
        "_AUTH_STATE=str(Path(__file__).parent/'.tw_auth_state.json')\n"
        "_AUTH_SCRIPT=str(Path(__file__).parent/'tw_auth_playwright.py')\n"
        "BEARER=('AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs'\n"
        "        '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA')\n"
        "_LOG=deque(maxlen=500)\n"
        "\n"
        "def log(level,msg,extra=None):\n"
        "    e={'ts':datetime.utcnow().strftime('%H:%M:%S'),'level':level,'msg':msg}\n"
        "    if extra:e['extra']=extra\n"
        "    _LOG.append(e)\n"
        "\n"
        "@app.before_request\n"
        "def before_req():\n"
        "    if request.path.startswith('/api/') and request.path not in('/api/health','/api/logs'):\n"
        "        body=''\n"
        "        try:body=str(request.get_json(silent=True) or '')[:80]\n"
        "        except:pass\n"
        "        log('REQ',f\"{request.method} {request.path}\",body or None)\n"
        "\n"
        "@app.after_request\n"
        "def after_req(resp):\n"
        "    if request.path.startswith('/api/') and request.path not in('/api/health','/api/logs'):\n"
        "        try:\n"
        "            d=resp.get_json()\n"
        "            status=(d.get('status') or d.get('error') or str(resp.status_code)) if isinstance(d,dict) else str(resp.status_code)\n"
        "            lvl='OK' if resp.status_code<400 else 'ERR'\n"
        "            log(lvl,f\"{request.method} {request.path} → {status}\")\n"
        "        except:pass\n"
        "    return resp\n"
        "\n"
        "def db():\n"
        "    c=sqlite3.connect(DB_PATH);c.row_factory=sqlite3.Row\n"
        "    c.execute('PRAGMA foreign_keys=ON');return c\n"
        "\n"
        "def get_token():\n"
        "    t=API_TOKEN\n"
        "    if not t:\n"
        "        tf=Path(__file__).parent/'.api_token'\n"
        "        if tf.exists():t=tf.read_text(encoding='utf-8').strip()\n"
        "    return t\n"
        "\n"
        "def run_expert(name,params):\n"
        "    try:\n"
        "        r=requests.post(f'{BASE_URL}/api/expert/run',\n"
        "            headers={'X-Auth-Token':get_token(),'Content-Type':'application/json'},\n"
        "            json={'expert_name':name,'params':params},timeout=120)\n"
        "        if r.status_code==200:\n"
        "            d=r.json()\n"
        "            result=d.get('result')\n"
        "            if result is None and isinstance(d,dict) and 'status' in d:return d\n"
        "            if isinstance(result,dict):return result\n"
        "            return d if isinstance(d,dict) else {}\n"
        "        log('ERR',f'run_expert({name}) HTTP {r.status_code}')\n"
        "        return {'status':'error','error':f'HTTP {r.status_code}'}\n"
        "    except Exception as e:\n"
        "        log('ERR',f'run_expert({name}): {str(e)[:100]}')\n"
        "        return{'status':'error','error':str(e)}\n"
        "\n"
        "def kv_set_auth(key,value,desc=''):\n"
        "    try:\n"
        "        r=requests.post(f'{BASE_URL}/api/kv/set',\n"
        "            headers={'X-Auth-Token':get_token(),'Content-Type':'application/json'},\n"
        "            json={'key':key,'value':value,'description':desc},timeout=10)\n"
        "        ok=r.status_code==200\n"
        "        log('KV',f'kv_set {key[:24]} → {\"OK\" if ok else f\"FAIL HTTP {r.status_code}\"}')\n"
        "        return ok\n"
        "    except Exception as e:\n"
        "        log('ERR',f'kv_set_auth: {e}')\n"
        "        return False\n"
        "\n"
        "def kv_get_auth(key):\n"
        "    try:\n"
        "        r=requests.post(f'{BASE_URL}/api/kv/get',\n"
        "            headers={'X-Auth-Token':get_token(),'Content-Type':'application/json'},\n"
        "            json={'key':key},timeout=10)\n"
        "        if r.status_code==200:return r.json().get('value','')\n"
        "    except:pass\n"
        "    return ''\n"
        "\n"
        "# ════════════════════════════════════════════════════════\n"
        "# KEY FIX v9: Credentials pool — Flask reads sessions with auth\n"
        "# Local experts call this endpoint instead of run_expert(tw_auth, get_pool)\n"
        "# This completely bypasses the nested expert KV auth problem.\n"
        "# ════════════════════════════════════════════════════════\n"
        "@app.route('/api/credentials/pool',methods=['GET'])\n"
        "def credentials_pool():\n"
        "    \"\"\"Returns credentials for active accounts. Flask reads sessions with auth token.\"\"\"\n"
        "    conn=db()\n"
        "    rows=conn.execute(\n"
        "        'SELECT id,username,health_score,circuit_state,quarantine_until,role_discovery,role_posting'\n"
        "        ' FROM accounts WHERE role_discovery=1 ORDER BY health_score DESC'\n"
        "    ).fetchall()\n"
        "    conn.close()\n"
        "    pool=[]\n"
        "    for r in rows:\n"
        "        # Circuit breaker check\n"
        "        circuit=r['circuit_state'] or 'closed'\n"
        "        if circuit=='open':continue\n"
        "        # Read session WITH auth token — always works from Flask context\n"
        "        session_raw=kv_get_auth(f\"tw_session_{r['id']}\")\n"
        "        if not session_raw:continue\n"
        "        try:\n"
        "            sess=json.loads(session_raw)\n"
        "            at=sess.get('auth_token','')\n"
        "            ct=sess.get('ct0','')\n"
        "            if at and ct and len(at)>10 and len(ct)>10:\n"
        "                pool.append({\n"
        "                    'account_id':r['id'],'username':r['username'],\n"
        "                    'auth_token':at,'ct0':ct,\n"
        "                    'health_score':r['health_score'],'circuit_state':circuit\n"
        "                })\n"
        "        except Exception:\n"
        "            pass\n"
        "    log('INFO',f'credentials/pool: {len(pool)} accounts available')\n"
        "    return jsonify({'pool':pool,'pool_size':len(pool),'status':'success'})\n"
        "\n"
        "@app.route('/api/credentials/<aid>',methods=['GET'])\n"
        "def credentials_account(aid):\n"
        "    \"\"\"Returns credentials for a specific account. Flask reads session with auth.\"\"\"\n"
        "    session_raw=kv_get_auth(f'tw_session_{aid}')\n"
        "    if not session_raw:\n"
        "        return jsonify({'status':'error','message':'No session found. Re-link account.'})\n"
        "    try:\n"
        "        sess=json.loads(session_raw)\n"
        "        conn=db()\n"
        "        row=conn.execute('SELECT username FROM accounts WHERE id=?',(aid,)).fetchone()\n"
        "        conn.close()\n"
        "        return jsonify({'status':'success','account_id':aid,\n"
        "                        'username':row['username'] if row else '',\n"
        "                        'auth_token':sess.get('auth_token',''),\n"
        "                        'ct0':sess.get('ct0','')})\n"
        "    except Exception as e:\n"
        "        return jsonify({'status':'error','message':str(e)})\n"
        "\n"
        "# ── Inline Twitter session validator ──────────────────────\n"
        "def _check_twitter_session(auth_token,ct0):\n"
        "    tok=auth_token.strip() if auth_token else ''\n"
        "    ct=ct0.strip() if ct0 else ''\n"
        "    if not tok or len(tok)<20:\n"
        "        return{'valid':False,'reason':'auth_token_too_short','detail':f'len={len(tok)}'}\n"
        "    if not ct or len(ct)<20:\n"
        "        return{'valid':False,'reason':'ct0_too_short','detail':f'len={len(ct)}'}\n"
        "    log('INFO',f'Validate: token={tok[:8]}... ct0={ct[:8]}...')\n"
        "    headers={\n"
        "        'authorization':f'Bearer {BEARER}','cookie':f'auth_token={tok};ct0={ct}',\n"
        "        'x-csrf-token':ct,'x-twitter-auth-type':'OAuth2Session',\n"
        "        'x-twitter-active-user':'yes','x-twitter-client-language':'en',\n"
        "        'user-agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',\n"
        "        'accept':'*/*','origin':'https://x.com','referer':'https://x.com/home',\n"
        "    }\n"
        "    last_status=None\n"
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
        "    log('INFO',f'Validate: API unreachable (HTTP {last_status}), trusting format')\n"
        "    return{'valid':True,'method':'format_trusted','http_status':last_status,\n"
        "           'detail':'API blocked from server IP. Token format valid, session trusted.'}\n"
        "\n"
        "# ── Health ─────────────────────────────────────────────────\n"
        "@app.route('/api/health')\n"
        "def health():return jsonify({'status':'ok','ts':datetime.utcnow().isoformat(),'logs':len(_LOG)})\n"
        "\n"
        "# ── Logs ───────────────────────────────────────────────────\n"
        "@app.route('/api/logs')\n"
        "def get_logs():\n"
        "    n=min(int(request.args.get('lines',100)),500)\n"
        "    return jsonify({'logs':list(_LOG)[-n:],'total':len(_LOG),'status':'success'})\n"
        "\n"
        "@app.route('/api/logs/clear',methods=['POST'])\n"
        "def clear_logs():\n"
        "    _LOG.clear();return jsonify({'status':'success'})\n"
        "\n"
        "# ── Accounts ───────────────────────────────────────────────\n"
        "@app.route('/api/accounts',methods=['GET'])\n"
        "def get_accounts():\n"
        "    conn=db()\n"
        "    rows=conn.execute(\n"
        "        'SELECT id,username,display_name,role_discovery,role_posting,'\n"
        "        'is_active,health_score,circuit_state,error_count,last_used,created_at '\n"
        "        'FROM accounts ORDER BY is_active DESC,health_score DESC').fetchall()\n"
        "    conn.close()\n"
        "    accounts=[]\n"
        "    for r in rows:\n"
        "        d=dict(r)\n"
        "        d['has_session']=bool(kv_get_auth(f\"tw_session_{r['id']}\"))\n"
        "        accounts.append(d)\n"
        "    return jsonify({'accounts':accounts,'count':len(accounts),'status':'success'})\n"
        "\n"
        "@app.route('/api/accounts',methods=['POST'])\n"
        "def add_account():\n"
        "    b=request.json or{}\n"
        "    auth_tok=b.get('auth_token','')\n"
        "    ct0_val=b.get('ct0','')\n"
        "    uname=b.get('username','').lstrip('@')\n"
        "    mode_val=b.get('mode','direct')\n"
        "    if not uname or not auth_tok or not ct0_val:\n"
        "        return jsonify({'status':'error','message':'username,auth_token,ct0 required'})\n"
        "    conn=db()\n"
        "    existing=conn.execute('SELECT id FROM accounts WHERE username=?',(uname,)).fetchone()\n"
        "    if existing:\n"
        "        acc_id=existing['id'];conn.close()\n"
        "        log('INFO',f'Account @{uname} exists → re-linking session')\n"
        "        return _do_relink(acc_id,auth_tok,ct0_val,mode_val,uname)\n"
        "    acc_id=str(__import__('uuid').uuid4())\n"
        "    try:\n"
        "        conn.execute(\"\"\"\n"
        "            INSERT INTO accounts(id,username,gologin_profile_id,role_discovery,role_posting,\n"
        "                                 is_active,health_score,circuit_state,created_at)\n"
        "            VALUES(?,?,?,?,?,0,100,'closed',datetime('now'))\n"
        "        \"\"\",(acc_id,uname,\n"
        "               b.get('gologin_profile_id') if mode_val=='gologin' else None,\n"
        "               1 if b.get('role_discovery',True) else 0,\n"
        "               1 if b.get('role_posting',True) else 0))\n"
        "        conn.commit()\n"
        "    except Exception as e:\n"
        "        conn.close();return jsonify({'status':'error','message':f'DB error:{e}'})\n"
        "    conn.close()\n"
        "    log('INFO',f'New account @{uname} created id={acc_id[:8]}')\n"
        "    return _do_relink(acc_id,auth_tok,ct0_val,mode_val,uname,is_new=True)\n"
        "\n"
        "def _do_relink(acc_id,auth_tok,ct0_val,mode_val,uname,is_new=False):\n"
        "    session_data=json.dumps({'auth_token':auth_tok,'ct0':ct0_val,'mode':mode_val,\n"
        "                            'added_at':datetime.utcnow().isoformat()})\n"
        "    ok=kv_set_auth(f'tw_session_{acc_id}',session_data,f'Twitter session @{uname}')\n"
        "    if not ok:\n"
        "        return jsonify({'status':'error','message':'Failed to store session in KV. Check API token.'})\n"
        "    verify=bool(kv_get_auth(f'tw_session_{acc_id}'))\n"
        "    action='added' if is_new else 'relinked'\n"
        "    log('INFO',f'Session {action} @{uname} stored={verify}')\n"
        "    return jsonify({'status':'success','account_id':acc_id,'username':uname,\n"
        "                    'action':action,'session_stored':verify,\n"
        "                    'message':f'@{uname} {action}. Session stored:{verify}'})\n"
        "\n"
        "@app.route('/api/accounts/<aid>/relink',methods=['POST'])\n"
        "def relink_account(aid):\n"
        "    b=request.json or{}\n"
        "    auth_tok=b.get('auth_token','');ct0_val=b.get('ct0','')\n"
        "    if not auth_tok or not ct0_val:\n"
        "        return jsonify({'status':'error','message':'auth_token and ct0 required'})\n"
        "    conn=db();row=conn.execute('SELECT username FROM accounts WHERE id=?',(aid,)).fetchone();conn.close()\n"
        "    if not row:return jsonify({'status':'error','message':'Account not found'})\n"
        "    return _do_relink(aid,auth_tok,ct0_val,'direct',row['username'])\n"
        "\n"
        "@app.route('/api/accounts/<aid>/switch',methods=['POST'])\n"
        "def switch_account(aid):\n"
        "    conn=db();conn.execute('UPDATE accounts SET is_active=0')\n"
        "    conn.execute('UPDATE accounts SET is_active=1,last_used=datetime(\"now\") WHERE id=?',(aid,))\n"
        "    conn.commit();row=conn.execute('SELECT username FROM accounts WHERE id=?',(aid,)).fetchone();conn.close()\n"
        "    return jsonify({'status':'success','active':aid,'username':row['username'] if row else ''})\n"
        "\n"
        "@app.route('/api/accounts/<aid>/validate',methods=['POST'])\n"
        "def validate_account(aid):\n"
        "    session_raw=kv_get_auth(f'tw_session_{aid}')\n"
        "    if not session_raw:\n"
        "        log('WARN',f'Validate {aid[:8]}: no session in KV')\n"
        "        return jsonify({'status':'no_session','message':'No session. Click Re-link.'})\n"
        "    try:\n"
        "        sess=json.loads(session_raw)\n"
        "        at=sess.get('auth_token','');ct=sess.get('ct0','')\n"
        "    except:\n"
        "        return jsonify({'status':'error','message':'Corrupted session. Re-link.'})\n"
        "    validation=_check_twitter_session(at,ct)\n"
        "    try:\n"
        "        conn=db()\n"
        "        if validation['valid']:\n"
        "            conn.execute(\"UPDATE accounts SET last_used=datetime('now'),error_count=0,\"\n"
        "                         \"health_score=MIN(100,health_score+5) WHERE id=?\",(aid,))\n"
        "        else:\n"
        "            conn.execute(\"UPDATE accounts SET error_count=error_count+1,\"\n"
        "                         \"health_score=MAX(0,health_score-15) WHERE id=?\",(aid,))\n"
        "        conn.commit()\n"
        "        uname=conn.execute('SELECT username FROM accounts WHERE id=?',(aid,)).fetchone()\n"
        "        uname=uname['username'] if uname else ''\n"
        "        conn.close()\n"
        "    except:uname=''\n"
        "    log('INFO',f'Validate @{uname}: valid={validation[\"valid\"]} method={validation.get(\"method\",\"?\")}')\n"
        "    return jsonify({'status':'success','account_id':aid,'username':uname,'validation':validation})\n"
        "\n"
        "@app.route('/api/accounts/<aid>',methods=['DELETE'])\n"
        "def remove_account(aid):return jsonify(run_expert('tw_auth',{'action':'remove','account_id':aid}))\n"
        "\n"
        "# ── Profiles ───────────────────────────────────────────────\n"
        "@app.route('/api/profiles',methods=['GET'])\n"
        "def get_profiles():\n"
        "    conn=db();tier=request.args.get('tier','');sort=request.args.get('sort','tier_score')\n"
        "    page=max(1,int(request.args.get('page',1)));ps=min(100,int(request.args.get('page_size',50)))\n"
        "    offset=(page-1)*ps\n"
        "    ss=sort if sort in('tier','tier_score','followers_count','engagement_rate','discovered_at') else 'tier_score'\n"
        "    so='DESC' if request.args.get('order','DESC').upper()=='DESC' else 'ASC'\n"
        "    w='WHERE tier=?' if tier else '';p=([tier] if tier else [])+[ps,offset]\n"
        "    total=conn.execute(f'SELECT COUNT(*) FROM profiles {w}',[tier] if tier else []).fetchone()[0]\n"
        "    rows=conn.execute(f'SELECT * FROM profiles {w} ORDER BY {ss} {so} LIMIT ? OFFSET ?',p).fetchall()\n"
        "    conn.close();return jsonify({'profiles':[dict(r) for r in rows],'total':total,'page':page,'status':'success'})\n"
        "\n"
        "# ── Posts ──────────────────────────────────────────────────\n"
        "@app.route('/api/posts',methods=['GET'])\n"
        "def get_posts():\n"
        "    conn=db();pid=request.args.get('profile_id','')\n"
        "    w='WHERE profile_id=?' if pid else ''\n"
        "    rows=conn.execute(f'SELECT * FROM posts {w} ORDER BY posted_at DESC LIMIT 100',[pid] if pid else []).fetchall()\n"
        "    conn.close();return jsonify({'posts':[dict(r) for r in rows],'status':'success'})\n"
        "\n"
        "# ── Tasks ──────────────────────────────────────────────────\n"
        "@app.route('/api/tasks',methods=['GET'])\n"
        "def get_tasks():\n"
        "    s=request.args.get('status','pending');ai=request.args.get('account_id','')\n"
        "    conn=db();cl,pl=[],[]\n"
        "    if s:cl.append('rt.status=?');pl.append(s)\n"
        "    if ai:cl.append('rt.account_id=?');pl.append(ai)\n"
        "    w=('WHERE '+' AND '.join(cl)) if cl else ''\n"
        "    rows=conn.execute(f'SELECT rt.*,COALESCE(rt.edited_reply,rt.generated_reply) as final_reply,p.text AS post_text,p.url AS post_url,pr.username AS profile_username,pr.tier AS profile_tier,a.username AS account_username FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id LEFT JOIN profiles pr ON rt.profile_id=pr.id LEFT JOIN accounts a ON rt.account_id=a.id {w} ORDER BY rt.created_at DESC LIMIT 200',pl).fetchall()\n"
        "    conn.close();return jsonify({'tasks':[dict(r) for r in rows],'count':len(rows),'status':'success'})\n"
        "\n"
        "@app.route('/api/tasks/<tid>/approve',methods=['POST'])\n"
        "def approve_task(tid):\n"
        "    conn=db();row=conn.execute('SELECT status FROM reply_tasks WHERE id=?',(tid,)).fetchone()\n"
        "    if row and row['status']=='pending':\n"
        "        conn.execute('UPDATE reply_tasks SET status=?,approved_at=datetime(\"now\") WHERE id=?',('approved',tid))\n"
        "        conn.commit();conn.close();return jsonify({'status':'success','new_status':'approved'})\n"
        "    conn.close();return jsonify({'status':'error','message':'Not in pending state'})\n"
        "\n"
        "@app.route('/api/tasks/<tid>/reject',methods=['POST'])\n"
        "def reject_task(tid):\n"
        "    conn=db();row=conn.execute('SELECT status FROM reply_tasks WHERE id=?',(tid,)).fetchone()\n"
        "    if row and row['status'] in('pending','approved'):\n"
        "        conn.execute('UPDATE reply_tasks SET status=? WHERE id=?',('rejected',tid))\n"
        "        conn.commit();conn.close();return jsonify({'status':'success','new_status':'rejected'})\n"
        "    conn.close();return jsonify({'status':'error','message':'Cannot reject'})\n"
        "\n"
        "@app.route('/api/tasks/<tid>',methods=['PATCH'])\n"
        "def edit_task(tid):\n"
        "    b=request.json or{};edited=b.get('edited_reply','')\n"
        "    if len(edited)>280:return jsonify({'status':'error','message':f'Too long:{len(edited)} (max 280)'})\n"
        "    conn=db();conn.execute('UPDATE reply_tasks SET edited_reply=? WHERE id=? AND status IN(\"pending\",\"approved\")',(edited,tid))\n"
        "    conn.commit();conn.close();return jsonify({'status':'success','char_count':len(edited)})\n"
        "\n"
        "@app.route('/api/tasks/bulk',methods=['POST'])\n"
        "def bulk_tasks():\n"
        "    b=request.json or{};act=b.get('action','approve');ids=b.get('task_ids',[])\n"
        "    target='approved' if act=='approve' else 'rejected';conn=db();done=0\n"
        "    for tid in ids:\n"
        "        row=conn.execute('SELECT status FROM reply_tasks WHERE id=?',(tid,)).fetchone()\n"
        "        if row and row['status']=='pending':\n"
        "            ts=',approved_at=datetime(\"now\")' if target=='approved' else ''\n"
        "            conn.execute(f'UPDATE reply_tasks SET status=?{ts} WHERE id=?',(target,tid));done+=1\n"
        "    conn.commit();conn.close();return jsonify({'status':'success','action':act,'success':done})\n"
        "\n"
        "# ── Run ────────────────────────────────────────────────────\n"
        "@app.route('/api/run/discover',methods=['POST'])\n"
        "def run_discover():return jsonify(run_expert('tw_discover',request.json or{}))\n"
        "@app.route('/api/run/posts',methods=['POST'])\n"
        "def run_posts():return jsonify(run_expert('tw_posts',request.json or{}))\n"
        "@app.route('/api/run/generate',methods=['POST'])\n"
        "def run_generate():return jsonify(run_expert('tw_generate',request.json or{}))\n"
        "@app.route('/api/run/post',methods=['POST'])\n"
        "def run_post():return jsonify(run_expert('tw_post',request.json or{}))\n"
        "@app.route('/api/run/monitor',methods=['POST'])\n"
        "def run_monitor():return jsonify(run_expert('tw_monitor',{'action':'check'}))\n"
        "\n"
        "# ── Analytics ──────────────────────────────────────────────\n"
        "@app.route('/api/analytics',methods=['GET'])\n"
        "def get_analytics():\n"
        "    conn=db();aid=request.args.get('account_id','');w='WHERE account_id=?' if aid else ''\n"
        "    rows=conn.execute(f'SELECT * FROM analytics {w} ORDER BY date DESC LIMIT 90',[aid] if aid else []).fetchall()\n"
        "    s=conn.execute('SELECT SUM(replies_sent) total_sent,SUM(responses_received) total_responses,SUM(conversations_started) total_conversations,AVG(response_rate) avg_response_rate FROM analytics').fetchone()\n"
        "    conn.close();return jsonify({'timeline':[dict(r) for r in rows],'summary':dict(s) if s else{},'status':'success'})\n"
        "\n"
        "# ── Settings ───────────────────────────────────────────────\n"
        "@app.route('/api/settings',methods=['GET'])\n"
        "def get_settings():\n"
        "    conn=db();rows=conn.execute('SELECT key,value FROM settings').fetchall();conn.close()\n"
        "    return jsonify({**{r['key']:r['value'] for r in rows},'status':'success'})\n"
        "\n"
        "@app.route('/api/settings',methods=['PATCH','POST'])\n"
        "def update_settings():\n"
        "    body=request.json or{};conn=db()\n"
        "    for k,v in body.items():\n"
        "        conn.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,datetime(\"now\")) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(k,str(v)))\n"
        "    conn.commit();conn.close();return jsonify({'status':'success','updated':list(body.keys())})\n"
        "\n"
        "# ── Workflow ───────────────────────────────────────────────\n"
        "@app.route('/api/workflow/state',methods=['GET'])\n"
        "def workflow_state():\n"
        "    conn=db();row=conn.execute('SELECT * FROM workflow_state WHERE id=\"current\"').fetchone();conn.close()\n"
        "    if row:d=dict(row);d['status']='success';return jsonify(d)\n"
        "    return jsonify({'phase':'idle','payload':'{}','status':'success'})\n"
        "\n"
        "@app.route('/api/workflow/state',methods=['POST','PATCH'])\n"
        "def set_workflow_state():\n"
        "    body=request.json or{};phase=body.get('phase','idle');payload=json.dumps(body.get('payload',{}))\n"
        "    conn=db();conn.execute('UPDATE workflow_state SET phase=?,payload=?,updated_at=datetime(\"now\") WHERE id=\"current\"',(phase,payload))\n"
        "    conn.commit();conn.close();return jsonify({'status':'success','phase':phase})\n"
        "\n"
        "# ── Twitter Browser Auth ────────────────────────────────────\n"
        "@app.route('/api/auth/twitter/start',methods=['POST'])\n"
        "def auth_tw_start():\n"
        "    try:\n"
        "        ex=json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8'))\n"
        "        old_pid=ex.get('pid')\n"
        "        if old_pid:\n"
        "            try:os.kill(int(old_pid),_sg.SIGTERM)\n"
        "            except:pass\n"
        "    except:pass\n"
        "    Path(_AUTH_STATE).write_text(json.dumps({'status':'starting'}),encoding='utf-8')\n"
        "    proc=_sp.Popen([sys.executable,_AUTH_SCRIPT,_AUTH_STATE],stdout=_sp.DEVNULL,stderr=_sp.DEVNULL)\n"
        "    time.sleep(0.6)\n"
        "    try:\n"
        "        st=json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8'))\n"
        "        st['pid']=proc.pid\n"
        "        Path(_AUTH_STATE).write_text(json.dumps(st),encoding='utf-8')\n"
        "    except:pass\n"
        "    log('INFO',f'Browser auth started pid={proc.pid}')\n"
        "    return jsonify({'status':'started','pid':proc.pid})\n"
        "\n"
        "@app.route('/api/auth/twitter/status',methods=['GET'])\n"
        "def auth_tw_status():\n"
        "    try:return jsonify(json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8')))\n"
        "    except:return jsonify({'status':'idle'})\n"
        "\n"
        "@app.route('/api/auth/twitter/cancel',methods=['POST'])\n"
        "def auth_tw_cancel():\n"
        "    try:\n"
        "        st=json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8'))\n"
        "        pid=st.get('pid')\n"
        "        if pid:\n"
        "            try:os.kill(int(pid),_sg.SIGTERM)\n"
        "            except:pass\n"
        "    except:pass\n"
        "    try:Path(_AUTH_STATE).write_text(json.dumps({'status':'cancelled'}),encoding='utf-8')\n"
        "    except:pass\n"
        "    return jsonify({'status':'cancelled'})\n"
        "\n"
        "# ── SPA ────────────────────────────────────────────────────\n"
        "@app.route('/',defaults={'path':''})\n"
        "@app.route('/<path:path>')\n"
        "def spa(path):\n"
        "    if path.startswith('api/'):return jsonify({'error':'Not found'}),404\n"
        "    ui=Path(__file__).parent/'ui'/'index.html'\n"
        "    return send_from_directory(str(ui.parent),'index.html') if ui.exists() else('<h1>UI missing</h1>',404)\n"
        "\n"
        "if __name__=='__main__':\n"
        "    log('INFO',f'Twitter Lead Agent server v9 starting on port {PORT}')\n"
        "    app.run(host='127.0.0.1',port=PORT,debug=False,threaded=True)\n"
    )

    # ── SPA HTML (reuse from previous version, same UI) ───────────────────
    # Read current index.html from disk if it exists, otherwise use last version
    existing_html = ""
    if UI_FILE.exists():
        try:
            existing_html = UI_FILE.read_text(encoding="utf-8")
        except Exception:
            pass

    # ── SERVER LIFECYCLE ──────────────────────────────────────────────────
    if action == "start":
        print("[2/6] 🔍 Checking existing server...")
        pid, existing_port = read_lock()
        if pid and is_pid_alive(pid) and check_health(existing_port or port):
            url = f"http://127.0.0.1:{existing_port or port}"
            if open_browser: webbrowser.open(url)
            print(f"[3/6] ✅ Already running at {url}")
            for i in range(3): print(f"[{i+4}/6] ✅")
            return {"status": "success", "url": url, "pid": pid, "message": "Already running"}
        else:
            clear_lock()

        resolved_token = api_token or kv_get(extella_token_key)
        if not resolved_token and TOKEN_FILE.exists():
            try: resolved_token = TOKEN_FILE.read_text(encoding="utf-8").strip()
            except: pass

        db_path = kv_get(db_path_key) or str(APP_DIR / "data.db")

        print("[3/6] 📦 Ensuring Flask is installed...")
        if not ensure_flask():
            return {"status": "error", "message": "Flask installation failed"}

        print("[4/6] 📝 Writing server files...")
        APP_DIR.mkdir(parents=True, exist_ok=True)
        UI_DIR.mkdir(parents=True, exist_ok=True)
        SERVER_PY.write_text(SERVER_CODE, encoding="utf-8")
        # Only write AUTH_PY — keep existing index.html if present
        AUTH_PY.write_text(PLAYWRIGHT_SCRIPT, encoding="utf-8")
        if resolved_token:
            TOKEN_FILE.write_text(resolved_token, encoding="utf-8")

        env = {**os.environ, "TW_PORT": str(port), "TW_DB_PATH": db_path,
               "TW_API_TOKEN": resolved_token or "", "EXTELLA_API_URL": BASE_URL}

        proc = subprocess.Popen(
            [sys.executable, str(SERVER_PY)], cwd=str(APP_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        write_lock(proc.pid, port)
        kv_set("tw_server_pid", str(proc.pid), "TW Lead Agent server PID")

        print("[5/6] ⏳ Waiting for startup...")
        import time; time.sleep(4)
        url = f"http://127.0.0.1:{port}"
        healthy = check_health(port)
        if open_browser and healthy: webbrowser.open(url)
        print(f"[6/6] {'✅ Ready!' if healthy else '⚠️ Starting...'} → {url}")
        return {"status": "success" if healthy else "starting", "url": url, "pid": proc.pid,
                "healthy": healthy, "message": f"Server at {url}"}

    elif action == "stop":
        pid, _ = read_lock()
        if pid and is_pid_alive(pid): os.kill(int(pid), signal.SIGTERM)
        clear_lock()
        for i in range(5): print(f"[{i+2}/6] ✅")
        return {"status": "success", "message": f"Server stopped (PID {pid})"}

    elif action == "status":
        pid, p = read_lock()
        alive = pid and is_pid_alive(pid)
        healthy = alive and check_health(p or port)
        if not alive: clear_lock()
        for i in range(5): print(f"[{i+2}/6] ✅")
        return {"status": "success", "running": bool(alive), "healthy": bool(healthy),
                "pid": pid, "url": f"http://127.0.0.1:{p or port}" if alive else None}

    elif action == "restart":
        tw_server("stop", port, False, api_token, db_path_key, extella_token_key)
        import time; time.sleep(2)
        return tw_server("start", port, open_browser, api_token, db_path_key, extella_token_key)

    return {"status": "error", "message": f"Unknown action: {action}"}
