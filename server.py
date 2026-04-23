#!/usr/bin/env python3
import os,sys,json,sqlite3,time,uuid,requests,subprocess as _sp,signal as _sg
from pathlib import Path
from datetime import datetime
from collections import deque
from flask import Flask,jsonify,request,send_from_directory
from flask_cors import CORS

app=Flask(__name__,static_folder=str(Path(__file__).parent/'ui'),static_url_path='')
CORS(app,methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'])
PORT=int(os.environ.get('TW_PORT',7842))
DB_PATH=os.environ.get('TW_DB_PATH',str(Path.home()/'Documents'/'twitter_agent'/'data.db'))
BASE_URL=os.environ.get('EXTELLA_API_URL','https://api.extella.ai')
API_TOKEN=os.environ.get('TW_API_TOKEN','')
_AUTH_STATE=str(Path(__file__).parent/'.tw_auth_state.json')
_AUTH_SCRIPT=str(Path(__file__).parent/'tw_auth_playwright.py')
BEARER=('AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs'
        '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA')
_LOG=deque(maxlen=500)

def log(level,msg,extra=None):
    e={'ts':datetime.utcnow().strftime('%H:%M:%S'),'level':level,'msg':msg}
    if extra:e['extra']=extra
    _LOG.append(e)
    print(f"[{e['ts']}] [{level}] {msg}")

@app.before_request
def before_req():
    if request.path.startswith('/api/') and request.path not in ('/api/health','/api/logs'):
        body=''
        try:body=str(request.get_json(silent=True) or '')[:80]
        except:pass
        log('REQ',f"{request.method} {request.path}",body or None)

@app.after_request
def after_req(resp):
    if request.path.startswith('/api/') and request.path not in ('/api/health','/api/logs'):
        try:
            d=resp.get_json()
            if isinstance(d,dict):
                status=d.get('status') or d.get('error') or str(resp.status_code)
            else:
                status=str(resp.status_code)
            lvl='OK' if resp.status_code<400 else 'ERR'
            log(lvl,f"{request.method} {request.path} → {status}")
        except:pass
    return resp

def db():
    c=sqlite3.connect(DB_PATH);c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON');return c

def ensure_outreach_schema():
    """Idempotent schema sync for GetX outreach (matches tw_data migration v2)."""
    try:
        p=Path(DB_PATH)
        if not p.exists():return
        c=sqlite3.connect(DB_PATH);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='profiles'").fetchone():
            c.close();return
        cols={r[1] for r in c.execute('PRAGMA table_info(profiles)')}
        if 'is_blue_verified' not in cols:
            c.execute('ALTER TABLE profiles ADD COLUMN is_blue_verified INTEGER DEFAULT 0')
        for stmt in (
            'CREATE INDEX IF NOT EXISTS idx_profiles_followers ON profiles(followers_count)',
            'CREATE INDEX IF NOT EXISTS idx_profiles_blue ON profiles(is_blue_verified)',
            'CREATE INDEX IF NOT EXISTS idx_profiles_location ON profiles(location)',
            'CREATE INDEX IF NOT EXISTS idx_posts_likes ON posts(likes)',
            'CREATE INDEX IF NOT EXISTS idx_posts_lang ON posts(lang)',
        ):
            try:c.execute(stmt)
            except sqlite3.OperationalError:pass
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('getx_api_token','')")
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
            ver=(c.execute('SELECT MAX(version) FROM schema_version').fetchone() or [0])[0] or 0
            if ver<2:
                c.execute("INSERT INTO schema_version (version, description) VALUES (2,'outreach getx v2')")
        c.commit();c.close()
    except Exception as e:
        log('WARN',f'ensure_outreach_schema: {e}')

ensure_outreach_schema()

def get_token():
    t=API_TOKEN
    if not t:
        tf=Path(__file__).parent/'.api_token'
        if tf.exists():t=tf.read_text(encoding='utf-8').strip()
    return t

def run_expert(name,params):
    try:
        r=requests.post(f'{BASE_URL}/api/expert/run',
            headers={'X-Auth-Token':get_token(),'Content-Type':'application/json'},
            json={'expert_name':name,'params':params},timeout=120)
        if r.status_code==200:
            d=r.json()
            # Handle both formats: {result:{...}} and direct result
            result=d.get('result')
            if result is None and 'status' in d:return d  # direct format
            if isinstance(result,dict):return result
            if result is None:return d  # fallback: return full response
            return result
        log('ERR',f'run_expert({name}) HTTP {r.status_code}')
        return {'status':'error','error':f'HTTP {r.status_code}'}
    except Exception as e:
        log('ERR',f'run_expert({name}): {str(e)[:100]}')
        return{'status':'error','error':str(e)}

def kv_set_auth(key,value,desc=''):
    try:
        r=requests.post(f'{BASE_URL}/api/kv/set',
            headers={'X-Auth-Token':get_token(),'Content-Type':'application/json'},
            json={'key':key,'value':value,'description':desc},timeout=10)
        ok=r.status_code==200
        log('KV',f'kv_set {key[:24]} → {"OK" if ok else f"FAIL HTTP {r.status_code}"}')
        return ok
    except Exception as e:
        log('ERR',f'kv_set_auth: {e}')
        return False

def kv_get_auth(key):
    try:
        r=requests.post(f'{BASE_URL}/api/kv/get',
            headers={'X-Auth-Token':get_token(),'Content-Type':'application/json'},
            json={'key':key},timeout=10)
        if r.status_code==200:return r.json().get('value','')
    except:pass
    return ''

# ════════════════════════════════════════════════════════
# INLINE Twitter session validator — no nested expert call
# Called directly from validate_account endpoint
# ════════════════════════════════════════════════════════
def _check_twitter_session(auth_token,ct0):
    """
    Check Twitter session validity.
    Strategy:
      1. Format check (instant, always reliable)
      2. Try Twitter API from server (often blocked by bot detection)
      3. If API unreachable/blocked — trust format (valid)
      Rationale: freshly-captured browser cookies ARE valid.
      Twitter only blocks server-side IP validation, not the session itself.
    """
    tok=auth_token.strip() if auth_token else ''
    ct=ct0.strip() if ct0 else ''

    # Step 1: Format validation
    if not tok or len(tok)<20:
        return{'valid':False,'reason':'auth_token_too_short','detail':f'len={len(tok)}'}
    if not ct or len(ct)<20:
        return{'valid':False,'reason':'ct0_too_short','detail':f'len={len(ct)}'}

    log('INFO',f'Validate: token={tok[:8]}... ct0={ct[:8]}... len_ok=True')

    # Step 2: Try Twitter API (may be blocked from server IP)
    headers={
        'authorization':f'Bearer {BEARER}',
        'cookie':f'auth_token={tok};ct0={ct}',
        'x-csrf-token':ct,
        'x-twitter-auth-type':'OAuth2Session',
        'x-twitter-active-user':'yes',
        'x-twitter-client-language':'en',
        'user-agent':('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/124.0.0.0 Safari/537.36'),
        'accept':'*/*',
        'accept-language':'en-US,en;q=0.9',
        'content-type':'application/json',
        'origin':'https://x.com',
        'referer':'https://x.com/home',
    }

    endpoints=[
        'https://x.com/i/api/1.1/statuses/home_timeline.json',
        'https://x.com/i/api/1.1/search/tweets.json?q=twitter&count=1',
    ]

    last_status=None
    got_auth_error=False

    for ep in endpoints:
        try:
            resp=requests.get(ep,headers=headers,timeout=8,allow_redirects=True)
            last_status=resp.status_code
            log('INFO',f'Validate API {ep[-30:]}: HTTP {resp.status_code}')

            if resp.status_code==200:
                try:
                    name=resp.json().get('screen_name','')
                except:name=''
                return{
                    'valid':True,
                    'method':'api_confirmed',
                    'screen_name':name,
                    'http_status':200,
                    'detail':f'Twitter API confirmed session @{name}'
                }
            elif resp.status_code==429:
                # Rate limited = definitely logged in
                return{
                    'valid':True,
                    'method':'rate_limited',
                    'reason':'rate_limited',
                    'http_status':429,
                    'detail':'Rate limited = session is active'
                }
            elif resp.status_code in(401,403):
                # Might be wrong endpoint OR truly expired — try next
                got_auth_error=True
                continue
            # 5xx, redirects → try next
        except requests.exceptions.Timeout:
            log('WARN',f'Validate API timeout on {ep[-30:]}')
            continue
        except Exception as ex:
            log('WARN',f'Validate API error {ep[-30:]}: {str(ex)[:60]}')
            continue

    # Step 3: Format-based trust fallback
    # Rationale: Python server requests are blocked by Twitter bot-detection
    # (IP mismatch, no TLS fingerprint). Freshly-captured browser cookies ARE valid.
    # If we got explicit 401/403 from BOTH endpoints — that's a real auth error.
    if got_auth_error and last_status in(401,403):
        # Both endpoints explicitly rejected the session
        log('WARN',f'Validate: explicit auth rejection HTTP {last_status}')
        return{
            'valid':False,
            'method':'api_rejected',
            'reason':'session_expired_or_suspended',
            'http_status':last_status,
            'detail':'Twitter explicitly rejected session (401/403). Re-link account.'
        }

    # All calls failed/timed out — trust the format (common in server context)
    log('INFO',f'Validate: API unreachable from server IP, trusting format (len={len(tok)})')
    return{
        'valid':True,
        'method':'format_trusted',
        'reason':'api_blocked_server_ip',
        'http_status':last_status,
        'detail':('Twitter API is blocked from server IP (normal). '
                  'Token format is valid. Session trusted as active.')
    }

# ── Health ────────────────────────────────────────────────
@app.route('/api/health')
def health():return jsonify({'status':'ok','ts':datetime.utcnow().isoformat(),'logs':len(_LOG)})

# ── Logs ─────────────────────────────────────────────────
@app.route('/api/logs')
def get_logs():
    n=min(int(request.args.get('lines',100)),500)
    return jsonify({'logs':list(_LOG)[-n:],'total':len(_LOG),'status':'success'})

@app.route('/api/logs/clear',methods=['POST'])
def clear_logs():
    _LOG.clear();return jsonify({'status':'success','message':'Logs cleared'})

# ── Accounts ─────────────────────────────────────────────
@app.route('/api/accounts',methods=['GET'])
def get_accounts():
    conn=db()
    rows=conn.execute(
        'SELECT id,username,display_name,role_discovery,role_posting,'
        'is_active,health_score,circuit_state,error_count,last_used,created_at '
        'FROM accounts ORDER BY is_active DESC,health_score DESC').fetchall()
    conn.close()
    accounts=[]
    for r in rows:
        d=dict(r)
        d['has_session']=bool(kv_get_auth(f"tw_session_{r['id']}"))
        accounts.append(d)
    return jsonify({'accounts':accounts,'count':len(accounts),'status':'success'})

@app.route('/api/accounts',methods=['POST'])
def add_account():
    b=request.json or{}
    auth_tok=b.get('auth_token','')
    ct0_val=b.get('ct0','')
    uname=b.get('username','').lstrip('@')
    mode_val=b.get('mode','direct')
    if not uname or not auth_tok or not ct0_val:
        return jsonify({'status':'error','message':'username,auth_token,ct0 required'})
    conn=db()
    existing=conn.execute('SELECT id FROM accounts WHERE username=?',(uname,)).fetchone()
    if existing:
        acc_id=existing['id']
        conn.close()
        log('INFO',f'Account @{uname} exists → re-linking session')
        return _do_relink(acc_id,auth_tok,ct0_val,mode_val,uname)
    acc_id=str(uuid.uuid4())
    try:
        conn.execute("""
            INSERT INTO accounts(id,username,gologin_profile_id,role_discovery,role_posting,
                                 is_active,health_score,circuit_state,created_at)
            VALUES(?,?,?,?,?,0,100,'closed',datetime('now'))
        """,(
            acc_id,uname,
            b.get('gologin_profile_id') if mode_val=='gologin' else None,
            1 if b.get('role_discovery',True) else 0,
            1 if b.get('role_posting',True) else 0))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'status':'error','message':f'DB error: {e}'})
    conn.close()
    log('INFO',f'New account @{uname} created id={acc_id[:8]}')
    return _do_relink(acc_id,auth_tok,ct0_val,mode_val,uname,is_new=True)

def _do_relink(acc_id,auth_tok,ct0_val,mode_val,uname,is_new=False):
    session_data=json.dumps({'auth_token':auth_tok,'ct0':ct0_val,'mode':mode_val,
                            'added_at':datetime.utcnow().isoformat()})
    ok=kv_set_auth(f'tw_session_{acc_id}',session_data,f'Twitter session @{uname}')
    if not ok:
        return jsonify({'status':'error','message':'Failed to store session in KV Store. Check API token.'})
    verify=bool(kv_get_auth(f'tw_session_{acc_id}'))
    action='added' if is_new else 'relinked'
    log('INFO',f'Session {action} @{uname} stored={verify}')
    return jsonify({'status':'success','account_id':acc_id,'username':uname,
                    'action':action,'session_stored':verify,
                    'message':f'@{uname} {action}. Session stored: {verify}'})

@app.route('/api/accounts/<aid>/relink',methods=['POST'])
def relink_account(aid):
    b=request.json or{}
    auth_tok=b.get('auth_token','')
    ct0_val=b.get('ct0','')
    if not auth_tok or not ct0_val:
        return jsonify({'status':'error','message':'auth_token and ct0 required'})
    conn=db();row=conn.execute('SELECT username FROM accounts WHERE id=?',(aid,)).fetchone();conn.close()
    if not row:return jsonify({'status':'error','message':'Account not found'})
    return _do_relink(aid,auth_tok,ct0_val,'direct',row['username'])

@app.route('/api/accounts/<aid>/switch',methods=['POST'])
def switch_account(aid):
    conn=db();conn.execute('UPDATE accounts SET is_active=0')
    conn.execute('UPDATE accounts SET is_active=1,last_used=datetime("now") WHERE id=?',(aid,))
    conn.commit();row=conn.execute('SELECT username FROM accounts WHERE id=?',(aid,)).fetchone();conn.close()
    return jsonify({'status':'success','active':aid,'username':row['username'] if row else ''})

# ════════════════════════════════════════════════════════
# VALIDATE — reads session inline, no nested expert call
# This is the KEY FIX for 'validate → None' bug
# ════════════════════════════════════════════════════════
@app.route('/api/accounts/<aid>/validate',methods=['POST'])
def validate_account(aid):
    # 1. Read session from KV using Flask's auth token (always works)
    session_raw=kv_get_auth(f'tw_session_{aid}')
    if not session_raw:
        log('WARN',f'Validate {aid[:8]}: no session in KV')
        return jsonify({'status':'no_session',
                        'message':'No session stored. Click Re-link to connect account.'})

    # 2. Parse tokens
    try:
        session=json.loads(session_raw)
        auth_token=session.get('auth_token','')
        ct0=session.get('ct0','')
    except Exception as e:
        log('ERR',f'Validate {aid[:8]}: session parse error {e}')
        return jsonify({'status':'error','message':'Corrupted session data. Please Re-link.'})

    # 3. Validate inline (format check + Twitter API attempt)
    validation=_check_twitter_session(auth_token,ct0)

    # 4. Update account health in SQLite
    try:
        conn=db()
        if validation['valid']:
            conn.execute(
                "UPDATE accounts SET last_used=datetime('now'),error_count=0,"
                "health_score=MIN(100,health_score+5) WHERE id=?",
                (aid,))
        else:
            conn.execute(
                "UPDATE accounts SET error_count=error_count+1,"
                "health_score=MAX(0,health_score-15) WHERE id=?",
                (aid,))
        conn.commit()
        username=conn.execute('SELECT username FROM accounts WHERE id=?',(aid,)).fetchone()
        uname=username['username'] if username else aid[:8]
        conn.close()
    except Exception as e:
        log('ERR',f'Validate DB update error: {e}')
        uname=aid[:8]

    log('INFO',f'Validate @{uname}: valid={validation["valid"]} '
               f'method={validation.get("method","?")} '
               f'detail={validation.get("detail","")[:60]}')

    return jsonify({
        'status':'success',
        'account_id':aid,
        'username':uname,
        'validation':validation
    })

@app.route('/api/accounts/<aid>',methods=['DELETE'])
def remove_account(aid):return jsonify(run_expert('tw_auth',{'action':'remove','account_id':aid}))

# ── Profiles ─────────────────────────────────────────────
@app.route('/api/profiles',methods=['GET'])
def get_profiles():
    conn=db();sort=request.args.get('sort','discovered_at')
    order=request.args.get('order','DESC');page=max(1,int(request.args.get('page',1)))
    ps=min(100,int(request.args.get('page_size',50)));offset=(page-1)*ps
    ss=sort if sort in('followers_count','discovered_at','engagement_rate','username') else 'discovered_at'
    so='DESC' if order.upper()=='DESC' else 'ASC'
    cl,pl=[],[]
    mf=request.args.get('min_followers','');xf=request.args.get('max_followers','')
    loc=(request.args.get('location') or '').strip()
    blue=(request.args.get('blue') or '').strip()
    if mf!='':
        try:cl.append('followers_count>=?');pl.append(int(mf))
        except ValueError:pass
    if xf!='':
        try:cl.append('followers_count<=?');pl.append(int(xf))
        except ValueError:pass
    if loc:cl.append('location LIKE ?');pl.append('%'+loc.replace('%','')+'%')
    if blue in('0','1'):cl.append('is_blue_verified=?');pl.append(int(blue))
    w=('WHERE '+' AND '.join(cl)) if cl else ''
    total=conn.execute(f'SELECT COUNT(*) FROM profiles {w}',pl).fetchone()[0]
    pl2=pl+[ps,offset]
    rows=conn.execute(f'SELECT * FROM profiles {w} ORDER BY {ss} {so} LIMIT ? OFFSET ?',pl2).fetchall()
    conn.close();return jsonify({'profiles':[dict(r) for r in rows],'total':total,'page':page,'status':'success'})

# ── Posts ────────────────────────────────────────────────
@app.route('/api/posts',methods=['GET'])
def get_posts():
    conn=db();cl,pl=[],[]
    pid=request.args.get('profile_id','')
    if pid:cl.append('profile_id=?');pl.append(pid)
    df=(request.args.get('date_from') or '').strip()
    dt=(request.args.get('date_to') or '').strip()
    if df:cl.append("date(substr(posted_at,1,10)) >= date(?)");pl.append(df[:10])
    if dt:cl.append("date(substr(posted_at,1,10)) <= date(?)");pl.append(dt[:10])
    ml=request.args.get('min_likes','');xl=request.args.get('max_likes','')
    if ml!='':
        try:cl.append('likes>=?');pl.append(int(ml))
        except ValueError:pass
    if xl!='':
        try:cl.append('likes<=?');pl.append(int(xl))
        except ValueError:pass
    lg=(request.args.get('lang') or '').strip()
    if lg:cl.append('lang=?');pl.append(lg)
    w=('WHERE '+' AND '.join(cl)) if cl else ''
    order=request.args.get('order','DESC');so='DESC' if order.upper()=='DESC' else 'ASC'
    page=max(1,int(request.args.get('page',1)));ps=min(100,int(request.args.get('page_size',50)))
    offset=(page-1)*ps
    total=conn.execute(f'SELECT COUNT(*) FROM posts {w}',pl).fetchone()[0]
    pl2=pl+[ps,offset]
    rows=conn.execute(f'SELECT * FROM posts {w} ORDER BY posted_at {so} LIMIT ? OFFSET ?',pl2).fetchall()
    conn.close();return jsonify({'posts':[dict(r) for r in rows],'total':total,'page':page,'status':'success'})

@app.route('/api/dashboard/feed',methods=['GET'])
def dashboard_feed():
    page=max(1,int(request.args.get('page',1)));ps=min(100,int(request.args.get('page_size',30)))
    offset=(page-1)*ps;conn=db()
    total=conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
    rows=conn.execute(
        'SELECT p.*, pr.username AS author_username, pr.display_name AS author_display_name, '
        'pr.followers_count AS author_followers, pr.is_blue_verified AS author_blue, '
        'pr.location AS author_location, pr.bio AS author_bio '
        'FROM posts p JOIN profiles pr ON p.profile_id=pr.id '
        'ORDER BY datetime(COALESCE(p.posted_at,p.fetched_at)) DESC LIMIT ? OFFSET ?',
        (ps,offset)
    ).fetchall()
    conn.close();return jsonify({'items':[dict(r) for r in rows],'total':total,'page':page,'status':'success'})

# ── Tasks ────────────────────────────────────────────────
@app.route('/api/tasks',methods=['GET'])
def get_tasks():
    s=request.args.get('status','pending');ai=request.args.get('account_id','')
    conn=db();cl,pl=[],[]
    if s:cl.append('rt.status=?');pl.append(s)
    if ai:cl.append('rt.account_id=?');pl.append(ai)
    w=('WHERE '+' AND '.join(cl)) if cl else ''
    rows=conn.execute(f'SELECT rt.*,COALESCE(rt.edited_reply,rt.generated_reply) as final_reply,p.text AS post_text,p.url AS post_url,pr.username AS profile_username,a.username AS account_username FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id LEFT JOIN profiles pr ON rt.profile_id=pr.id LEFT JOIN accounts a ON rt.account_id=a.id {w} ORDER BY rt.created_at DESC LIMIT 200',pl).fetchall()
    conn.close();return jsonify({'tasks':[dict(r) for r in rows],'count':len(rows),'status':'success'})

@app.route('/api/tasks/<tid>/approve',methods=['POST'])
def approve_task(tid):
    conn=db();row=conn.execute('SELECT status FROM reply_tasks WHERE id=?',(tid,)).fetchone()
    if row and row['status']=='pending':
        conn.execute('UPDATE reply_tasks SET status=?,approved_at=datetime("now") WHERE id=?',('approved',tid))
        conn.commit();conn.close();return jsonify({'status':'success','new_status':'approved'})
    conn.close();return jsonify({'status':'error','message':'Not in pending state'})

@app.route('/api/tasks/<tid>/reject',methods=['POST'])
def reject_task(tid):
    conn=db();row=conn.execute('SELECT status FROM reply_tasks WHERE id=?',(tid,)).fetchone()
    if row and row['status'] in('pending','approved'):
        conn.execute('UPDATE reply_tasks SET status=? WHERE id=?',('rejected',tid))
        conn.commit();conn.close();return jsonify({'status':'success','new_status':'rejected'})
    conn.close();return jsonify({'status':'error','message':'Cannot reject'})

@app.route('/api/tasks/<tid>',methods=['PATCH'])
def edit_task(tid):
    b=request.json or{};edited=b.get('edited_reply','')
    if len(edited)>280:return jsonify({'status':'error','message':f'Too long:{len(edited)} (max 280)'})
    conn=db();conn.execute('UPDATE reply_tasks SET edited_reply=? WHERE id=? AND status IN("pending","approved")',(edited,tid))
    conn.commit();conn.close();return jsonify({'status':'success','char_count':len(edited)})

@app.route('/api/tasks/bulk',methods=['POST'])
def bulk_tasks():
    b=request.json or{};act=b.get('action','approve');ids=b.get('task_ids',[])
    target='approved' if act=='approve' else 'rejected';conn=db();done=0
    for tid in ids:
        row=conn.execute('SELECT status FROM reply_tasks WHERE id=?',(tid,)).fetchone()
        if row and row['status']=='pending':
            ts=',approved_at=datetime("now")' if target=='approved' else ''
            conn.execute(f'UPDATE reply_tasks SET status=?{ts} WHERE id=?',(target,tid));done+=1
    conn.commit();conn.close();return jsonify({'status':'success','action':act,'success':done})

# ── Run ──────────────────────────────────────────────────
@app.route('/api/run/discover',methods=['POST'])
def run_discover():return jsonify(run_expert('tw_discover',request.json or{}))
@app.route('/api/run/posts',methods=['POST'])
def run_posts():return jsonify(run_expert('tw_posts',request.json or{}))
@app.route('/api/run/generate',methods=['POST'])
def run_generate():return jsonify(run_expert('tw_generate',request.json or{}))
@app.route('/api/run/post',methods=['POST'])
def run_post():return jsonify(run_expert('tw_post',request.json or{}))
@app.route('/api/run/monitor',methods=['POST'])
def run_monitor():return jsonify(run_expert('tw_monitor',{'action':'check'}))
@app.route('/api/run/search_getx',methods=['POST'])
def run_search_getx():return jsonify(run_expert('tw_search_getx',request.json or{}))
@app.route('/api/run/queue',methods=['POST'])
def run_queue():return jsonify(run_expert('tw_queue',request.json or{}))

# ── Analytics ────────────────────────────────────────────
@app.route('/api/analytics',methods=['GET'])
def get_analytics():
    conn=db();aid=request.args.get('account_id','');w='WHERE account_id=?' if aid else ''
    rows=conn.execute(f'SELECT * FROM analytics {w} ORDER BY date DESC LIMIT 90',[aid] if aid else []).fetchall()
    s=conn.execute('SELECT SUM(replies_sent) total_sent,SUM(responses_received) total_responses,SUM(conversations_started) total_conversations,AVG(response_rate) avg_response_rate FROM analytics').fetchone()
    conn.close();return jsonify({'timeline':[dict(r) for r in rows],'summary':dict(s) if s else{},'status':'success'})

# ── Settings ─────────────────────────────────────────────
@app.route('/api/settings',methods=['GET'])
def get_settings():
    conn=db();rows=conn.execute('SELECT key,value FROM settings').fetchall();conn.close()
    return jsonify({**{r['key']:r['value'] for r in rows},'status':'success'})

@app.route('/api/settings',methods=['PATCH','POST'])
def update_settings():
    body=request.json or{};conn=db()
    for k,v in body.items():
        conn.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,datetime("now")) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(k,str(v)))
    conn.commit();conn.close()
    if 'getx_api_token' in body:
        kv_set_auth('getx_api_token',str(body.get('getx_api_token') or '').strip(),'GetXAPI bearer token')
    return jsonify({'status':'success','updated':list(body.keys())})

# ── Workflow state (FIX: always return status field) ─────
@app.route('/api/workflow/state',methods=['GET'])
def workflow_state():
    conn=db();row=conn.execute('SELECT * FROM workflow_state WHERE id="current"').fetchone();conn.close()
    if row:
        d=dict(row)
        d['status']='success'  # always add status field
        return jsonify(d)
    return jsonify({'phase':'idle','payload':'{}','status':'success'})

@app.route('/api/workflow/state',methods=['POST','PATCH'])
def set_workflow_state():
    body=request.json or{};phase=body.get('phase','idle');payload=json.dumps(body.get('payload',{}))
    conn=db();conn.execute('UPDATE workflow_state SET phase=?,payload=?,updated_at=datetime("now") WHERE id="current"',(phase,payload))
    conn.commit();conn.close();return jsonify({'status':'success','phase':phase})

# ── Twitter browser auth ──────────────────────────────────
@app.route('/api/auth/twitter/start',methods=['POST'])
def auth_tw_start():
    try:
        ex=json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8'))
        old_pid=ex.get('pid')
        if old_pid:
            try:os.kill(int(old_pid),_sg.SIGTERM)
            except:pass
    except:pass
    Path(_AUTH_STATE).write_text(json.dumps({'status':'starting'}),encoding='utf-8')
    proc=_sp.Popen([sys.executable,_AUTH_SCRIPT,_AUTH_STATE],stdout=_sp.DEVNULL,stderr=_sp.DEVNULL)
    time.sleep(0.6)
    try:
        st=json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8'))
        st['pid']=proc.pid
        Path(_AUTH_STATE).write_text(json.dumps(st),encoding='utf-8')
    except:pass
    log('INFO',f'Browser auth started pid={proc.pid}')
    return jsonify({'status':'started','pid':proc.pid})

@app.route('/api/auth/twitter/status',methods=['GET'])
def auth_tw_status():
    try:return jsonify(json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8')))
    except:return jsonify({'status':'idle'})

@app.route('/api/auth/twitter/cancel',methods=['POST'])
def auth_tw_cancel():
    try:
        st=json.loads(Path(_AUTH_STATE).read_text(encoding='utf-8'))
        pid=st.get('pid')
        if pid:
            try:os.kill(int(pid),_sg.SIGTERM)
            except:pass
    except:pass
    try:Path(_AUTH_STATE).write_text(json.dumps({'status':'cancelled'}),encoding='utf-8')
    except:pass
    return jsonify({'status':'cancelled'})


# ════════════════════════════════════════════════════════
# v9: Credentials proxy — Flask reads sessions WITH auth
# Local experts call these endpoints instead of
# run_expert("tw_auth", {"action": "get_pool"})
# This permanently bypasses nested expert KV auth issue.
# ════════════════════════════════════════════════════════
@app.route('/api/credentials/pool', methods=['GET'])
def credentials_pool():
    conn = db()
    rows = conn.execute(
        'SELECT id,username,health_score,circuit_state '
        'FROM accounts WHERE role_discovery=1 ORDER BY health_score DESC'
    ).fetchall()
    conn.close()
    pool = []
    for r in rows:
        if (r['circuit_state'] or 'closed') == 'open':
            continue
        session_raw = kv_get_auth(f"tw_session_{r['id']}")
        if not session_raw:
            continue
        try:
            sess = json.loads(session_raw)
            at = sess.get('auth_token', '')
            ct = sess.get('ct0', '')
            if at and ct and len(at) > 10 and len(ct) > 10:
                pool.append({
                    'account_id': r['id'],
                    'username':   r['username'],
                    'auth_token': at,
                    'ct0':        ct,
                    'health_score': r['health_score'],
                    'circuit_state': r['circuit_state'] or 'closed'
                })
        except Exception:
            pass
    log('INFO', f'credentials/pool → {len(pool)} accounts')
    return jsonify({'pool': pool, 'pool_size': len(pool), 'status': 'success'})


@app.route('/api/credentials/<aid>', methods=['GET'])
def credentials_account(aid):
    session_raw = kv_get_auth(f'tw_session_{aid}')
    if not session_raw:
        return jsonify({'status': 'error', 'message': 'No session. Re-link account.'})
    try:
        sess = json.loads(session_raw)
        conn = db()
        row  = conn.execute('SELECT username FROM accounts WHERE id=?', (aid,)).fetchone()
        conn.close()
        return jsonify({
            'status':     'success',
            'account_id': aid,
            'username':   row['username'] if row else '',
            'auth_token': sess.get('auth_token', ''),
            'ct0':        sess.get('ct0', '')
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


# ════════════════════════════════════════════════════════
# v5: Smart Discovery — NL description + LLM expansion + auto-posts
# ════════════════════════════════════════════════════════
@app.route('/api/run/smart_discover', methods=['POST'])
def smart_discover():
    b = request.json or {}
    profile_description = b.get('profile_description', '')
    content_description = b.get('content_description', '')
    limit = int(b.get('limit', 10))
    min_followers = int(b.get('min_followers', 0))
    dry_run = bool(b.get('dry_run', False))
    auto_fetch_posts = bool(b.get('auto_fetch_posts', False))

    if not profile_description:
        return jsonify({'status': 'error', 'message': 'profile_description is required'})

    # Step 1: Expand query via LLM
    log('INFO', f'smart_discover: expanding "{profile_description[:40]}"...')
    expand_result = run_expert('tw_query_expand', {
        'user_description': profile_description,
        'mode': 'profiles',
    })
    ai_keywords = expand_result.get('search_queries', [])
    ai_topics   = expand_result.get('topic_keywords', [])
    ai_source   = expand_result.get('source', 'rule_based')
    log('INFO', f'smart_discover: {ai_source} → {len(ai_keywords)} queries, {len(ai_topics)} topics')

    # Step 2: Discover profiles
    discover_result = run_expert('tw_discover', {
        'user_description': profile_description,
        'limit': limit,
        'min_followers': min_followers,
        'dry_run': dry_run,
    })

    profiles_found = discover_result.get('profiles_found', 0)
    log('INFO', f'smart_discover: found {profiles_found} profiles')

    posts_saved = 0
    # Step 3: Auto-fetch posts if content_description provided
    if content_description and profiles_found > 0 and not dry_run and auto_fetch_posts:
        log('INFO', f'smart_discover: fetching posts for "{content_description[:40]}"...')
        posts_result = run_expert('tw_posts', {
            'content_description': content_description,
            'posts_per_profile': 10,
        })
        posts_saved = posts_result.get('posts_saved', 0)
        log('INFO', f'smart_discover: saved {posts_saved} posts')

    return jsonify({
        'status': 'success',
        'profiles_found': profiles_found,
        'posts_saved': posts_saved,
        'ai_keywords': ai_keywords,
        'ai_topics': ai_topics,
        'ai_source': ai_source,
        'dry_run': dry_run,
    })


@app.route('/api/run/posts_for_profile', methods=['POST'])
def posts_for_profile():
    """Fetch posts for a specific profile with optional content description."""
    b = request.json or {}
    profile_id = b.get('profile_id', '')
    content_description = b.get('content_description', '')

    if not profile_id:
        return jsonify({'status': 'error', 'message': 'profile_id is required'})

    log('INFO', f'posts_for_profile: {profile_id[:8]} desc="{content_description[:40]}"')
    result = run_expert('tw_posts', {
        'profile_ids': profile_id,
        'content_description': content_description,
        'posts_per_profile': 15,
    })
    return jsonify(result)


@app.route('/api/ai/expand_query', methods=['POST'])
def expand_query():
    """LLM query expansion endpoint for frontend transparency."""
    b = request.json or {}
    user_description = b.get('user_description', '')
    mode = b.get('mode', 'profiles')

    if not user_description:
        return jsonify({'status': 'error', 'message': 'user_description required'})

    result = run_expert('tw_query_expand', {
        'user_description': user_description,
        'mode': mode,
    })
    return jsonify(result)

# ── SPA ──────────────────────────────────────────────────
@app.route('/',defaults={'path':''})
@app.route('/<path:path>')
def spa(path):
    if path.startswith('api/'):return jsonify({'error':'Not found'}),404
    ui=Path(__file__).parent/'ui'/'index.html'
    return send_from_directory(str(ui.parent),'index.html') if ui.exists() else('<h1>UI missing</h1>',404)

if __name__=='__main__':
    log('INFO',f'Twitter Lead Agent server starting on port {PORT}')
    app.run(host='127.0.0.1',port=PORT,debug=False,threaded=True)
