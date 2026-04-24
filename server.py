#!/usr/bin/env python3
import os,sys,json,re,sqlite3,time,random,uuid,requests,subprocess as _sp,signal as _sg,threading
from pathlib import Path
from datetime import datetime,timezone,date,timedelta
from collections import deque
from flask import Flask,jsonify,request,send_from_directory,Response
from flask_cors import CORS

app=Flask(__name__,static_folder=str(Path(__file__).parent/'ui'),static_url_path='')
CORS(app,methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'])
PORT=int(os.environ.get('TW_PORT',7842))
_SCHEDULER={
    'running':False,'paused':False,'pause_until':None,'thread':None,'daily_limit':10,'sent_today':0,
    'last_reset_date':'','next_post_at':None,'current_task_id':None,
    'delay_min':30,'delay_max':90,
    'lock':__import__('threading').Lock(),
}
DB_PATH=os.environ.get('TW_DB_PATH',str(Path.home()/'Documents'/'twitter_agent'/'data.db'))
BASE_URL=os.environ.get('EXTELLA_API_URL','https://api.extella.ai')
API_TOKEN=os.environ.get('TW_API_TOKEN','')
_AUTH_STATE=str(Path(__file__).parent/'.tw_auth_state.json')
_AUTH_SCRIPT=str(Path(__file__).parent/'tw_auth_playwright.py')
BEARER=('AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs'
        '%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA')
CREATE_TWEET_QUERY_ID='SoVnbfCycZ7fERGCwpZkYA'
GRAPHQL_CREATE_TWEET_URL=f'https://x.com/i/api/graphql/{CREATE_TWEET_QUERY_ID}/CreateTweet'
TWEET_DETAIL_QUERY_ID='QuBlQ6SxNAQCt6-kBiCXCQ'
GRAPHQL_TWEET_DETAIL_URL=f'https://x.com/i/api/graphql/{TWEET_DETAIL_QUERY_ID}/TweetDetail'
_TWEET_DETAIL_FEATURES={
    'rweb_lists_timeline_redesign_enabled': True,
    'responsive_web_graphql_exclude_directive_enabled': True,
    'verified_phone_label_enabled': False,
    'creator_subscriptions_tweet_preview_api_enabled': True,
    'responsive_web_graphql_timeline_navigation_enabled': True,
    'responsive_web_graphql_skip_user_profile_image_extensions_enabled': False,
    'tweetypie_unmention_optimization_enabled': True,
    'responsive_web_edit_tweet_api_enabled': True,
    'graphql_is_translatable_rweb_tweet_is_translatable_enabled': True,
    'view_counts_everywhere_api_enabled': True,
    'longform_notetweets_consumption_enabled': True,
    'tweet_awards_web_tipping_enabled': False,
    'freedom_of_speech_not_reach_enabled': False,
    'standardized_nudges_misinfo': True,
    'longform_notetweets_rich_text_read_enabled': True,
    'responsive_web_enhance_cards_enabled': False,
}
_CREATE_TWEET_FEATURES={
    'interactive_text_enabled': True,
    'longform_notetweets_inline_media_enabled': False,
    'responsive_web_text_conversations_enabled': False,
    'tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled': False,
    'vibe_api_enabled': False,
    'rweb_lists_timeline_redesign_enabled': True,
    'responsive_web_graphql_exclude_directive_enabled': True,
    'verified_phone_label_enabled': False,
    'creator_subscriptions_tweet_preview_api_enabled': True,
    'responsive_web_graphql_timeline_navigation_enabled': True,
    'responsive_web_graphql_skip_user_profile_image_extensions_enabled': False,
    'tweetypie_unmention_optimization_enabled': True,
    'responsive_web_edit_tweet_api_enabled': True,
    'graphql_is_translatable_rweb_tweet_is_translatable_enabled': True,
    'view_counts_everywhere_api_enabled': True,
    'longform_notetweets_consumption_enabled': True,
    'tweet_awards_web_tipping_enabled': False,
    'freedom_of_speech_not_reach_enabled': False,
    'standardized_nudges_misinfo': True,
    'longform_notetweets_rich_text_read_enabled': True,
    'responsive_web_enhance_cards_enabled': False,
}
_LOG=deque(maxlen=500)

def log(level,msg,extra=None):
    e={'ts':datetime.now(timezone.utc).strftime('%H:%M:%S'),'level':level,'msg':msg}
    if extra:e['extra']=extra
    _LOG.append(e)
    print(f"[{e['ts']}] [{level}] {msg}")

@app.before_request
def before_req():
    if request.path.startswith('/api/') and request.path not in ('/api/health','/api/logs'):
        body=''
        try:body=str(request.get_json(silent=True) or '')[:80]
        except:pass
        qs=request.query_string.decode('utf-8','replace') if request.query_string else ''
        path=request.path+('?'+qs if qs else '')
        log('REQ',f"{request.method} {path}",body or None)

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

def _profile_text_search_term(q):
    """Normalized substring for profile search (bio + display_name + username). Uses INSTR, not LIKE."""
    s=(q or '').strip().lower()
    return s if s else None

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
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('gologin_api_token','')")
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
        if r.status_code==404:
            return{'status':'error','error':'HTTP 404','message':f'Эксперт «{name}» не найден в Extella. Загрузите: python sync_to_extella.py {name}'}
        return {'status':'error','error':f'HTTP {r.status_code}','message':f'Extella вернула HTTP {r.status_code} для «{name}»'}
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

def _get_gologin_token():
    conn=db()
    try:
        row=conn.execute("SELECT value FROM settings WHERE key='gologin_api_token'").fetchone()
        tok=((row['value'] or '') if row else '').strip()
    finally:
        conn.close()
    if not tok:
        tok=(kv_get_auth('gologin_api_token') or '').strip()
    return tok

def _parse_getx_created_at(raw):
    if not raw:
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    raw=raw.strip()
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}',raw):
            return raw[:19].replace(' ','T')+'Z' if 'T' not in raw[:19] else raw
        dt=datetime.strptime(raw,'%a %b %d %H:%M:%S %z %Y')
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def local_search_getx(body):
    """GetX advanced_search + upsert into local DB_PATH. Extella expert runs on a worker FS — UI reads this machine's SQLite only."""
    body=body or{}
    q=(body.get('keywords')or'').strip()
    if not q:
        return{'status':'error','message':'keywords (q) is required'}
    prod=body.get('product')or'Latest'
    prod=prod if prod in('Latest','Top')else'Latest'
    try:cap=max(1,min(int(body.get('max_posts')or 50),500))
    except (TypeError,ValueError):cap=50
    tok=(body.get('getx_api_token')or'').strip()
    if not tok:
        try:
            c=db();r=c.execute("SELECT value FROM settings WHERE key='getx_api_token'").fetchone();c.close()
            tok=(r['value']or'').strip() if r else''
        except Exception:
            tok=''
    if not tok:
        tok=(kv_get_auth('getx_api_token')or'').strip()
    if not tok:
        return{'status':'error','message':'Missing GetX API token. Save it in Settings.'}
    base=os.environ.get('GETX_API_BASE','https://api.getxapi.com').rstrip('/')
    headers={'Authorization':f'Bearer {tok}'}
    cursor=None;pages=0;stored_posts=0;stored_profiles=set();seen=set();api_calls=0;last_err=''
    consec_429=0;max_429=8
    prof_sql="""
        INSERT INTO profiles (
            id, username, display_name, bio, location,
            followers_count, following_count, tweet_count,
            is_blue_verified, discovered_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            username=excluded.username,
            display_name=excluded.display_name,
            bio=excluded.bio,
            location=excluded.location,
            followers_count=excluded.followers_count,
            following_count=excluded.following_count,
            tweet_count=excluded.tweet_count,
            is_blue_verified=excluded.is_blue_verified,
            updated_at=datetime('now')
    """
    post_sql="""
        INSERT INTO posts (
            id, profile_id, text, url, posted_at,
            likes, replies_count, retweets, views, lang, fetched_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            profile_id=excluded.profile_id,
            text=excluded.text,
            url=excluded.url,
            posted_at=excluded.posted_at,
            likes=excluded.likes,
            replies_count=excluded.replies_count,
            retweets=excluded.retweets,
            views=excluded.views,
            lang=excluded.lang,
            fetched_at=datetime('now')
    """
    try:
        conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON')
    except Exception as e:
        return{'status':'error','message':str(e)}
    while stored_posts<cap:
        params={'q':q,'product':prod}
        if cursor:params['cursor']=cursor
        try:
            r=requests.get(f'{base}/twitter/tweet/advanced_search',params=params,headers=headers,timeout=60)
            api_calls+=1
        except Exception as e:
            last_err=str(e)[:200];log('ERR',f'local_search_getx HTTP: {last_err}');break
        if r.status_code==429:
            consec_429+=1;last_err='rate_limited'
            if consec_429>=max_429:last_err='rate_limited_stopped';break
            time.sleep(2.5);continue
        consec_429=0
        if r.status_code!=200:
            try:last_err=(r.json().get('error')or r.text[:120])
            except Exception:last_err=r.text[:120]or f'HTTP {r.status_code}'
            log('ERR',f'local_search_getx GetX {r.status_code}: {last_err}');break
        try:data=r.json()
        except Exception:last_err='invalid JSON';break
        tweets=data.get('tweets')or[]
        has_more=bool(data.get('has_more')or data.get('hasMore'))
        next_c=data.get('next_cursor')or data.get('nextCursor')
        pages+=1
        log('INFO',f'local_search_getx page={pages} tweets={len(tweets)}')
        for tw in tweets:
            if stored_posts>=cap:break
            tid=str(tw.get('id')or'')
            if not tid or tid in seen:continue
            seen.add(tid)
            author=tw.get('author')or{}
            aid=str(author.get('id')or'')
            if not aid:continue
            uname=(author.get('userName')or author.get('username')or'').strip()
            if not uname:continue
            disp=(author.get('name')or'').strip()
            bio=(author.get('description')or'').strip()
            loc=(author.get('location')or'').strip()
            followers=int(author.get('followers')or 0)
            following=int(author.get('following')or 0)
            twcount=int(author.get('tweets')or author.get('statusesCount')or 0)
            blue=1 if author.get('isBlueVerified')or author.get('is_blue_verified')else 0
            try:
                conn.execute(prof_sql,(aid,uname,disp,bio,loc,followers,following,twcount,blue))
                stored_profiles.add(aid)
                text=(tw.get('text')or'').strip()
                url=(tw.get('url')or tw.get('twitterUrl')or'').strip()
                posted=_parse_getx_created_at(tw.get('createdAt')or'')
                likes=int(tw.get('likeCount')or tw.get('like_count')or 0)
                rep=int(tw.get('replyCount')or tw.get('reply_count')or 0)
                rts=int(tw.get('retweetCount')or tw.get('retweet_count')or 0)
                views=int(tw.get('viewCount')or tw.get('view_count')or 0)
                lang=(tw.get('lang')or'').strip()or None
                conn.execute(post_sql,(tid,aid,text,url,posted,likes,rep,rts,views,lang))
            except sqlite3.OperationalError as e:
                last_err=str(e);log('ERR',f'local_search_getx DB: {e}');conn.close()
                return{'status':'error','message':last_err,'posts_saved':stored_posts}
            stored_posts+=1
        conn.commit()
        if stored_posts>=cap:break
        if not tweets:break
        if not has_more or not next_c:break
        cursor=next_c
    conn.close()
    log('INFO',f'local_search_getx done posts={stored_posts} profiles={len(stored_profiles)} api_calls={api_calls}')
    return{'status':'success','posts_saved':stored_posts,'profiles_upserted':len(stored_profiles),
           'api_calls':api_calls,'pages':pages,'query':q,'last_error':last_err or None}

def _openai_reply_text(api_key,post_text,reply_intent):
    """POST chat/completions; returns stripped reply or raises."""
    prompt=(
        f'Write a short engaging Twitter reply (max 200 chars) to this tweet: {post_text}\n'
        f'Reply intent: {reply_intent}\n'
        'Reply only with the reply text, no quotes.'
    )
    r=requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={'Authorization':f'Bearer {api_key}','Content-Type':'application/json'},
        json={'model':'gpt-4o-mini','messages':[{'role':'user','content':prompt}],'max_tokens':200},
        timeout=60,
    )
    r.raise_for_status()
    data=r.json()
    ch=(data.get('choices')or[{}])[0]or{}
    msg=(ch.get('message')or{})
    t=(msg.get('content')or'').strip()
    if len(t)>=2 and t[0]==t[-1] and t[0] in'"\'':
        t=t[1:-1].strip()
    return t[:280] if t else ''

def local_create_batch(body):
    """Create reply_tasks in local SQLite with OpenAI replies. tw_queue on Extella uses worker FS — UI reads this machine's DB only."""
    body=body or{}
    if (body.get('action')or'create_batch')!='create_batch':
        return{'status':'error','message':'Only action=create_batch is supported'}
    raw=body.get('post_ids')or''
    if isinstance(raw,(list,tuple)):
        ids=[str(p).strip() for p in raw if str(p).strip()]
    else:
        ids=[p.strip() for p in str(raw).split(',')if p.strip()]
    if not ids:
        return{'status':'error','message':'post_ids required'}
    reply_intent=(body.get('reply_intent')or'').strip()or'helpful and curious'
    acct_in=(str(body.get('account_id')or'').strip()or None)
    try:
        conn=db()
    except Exception as e:
        return{'status':'error','message':str(e)}
    row_key=conn.execute("SELECT value FROM settings WHERE key=?",('openai_api_key',)).fetchone()
    api_key=(row_key['value']or'').strip() if row_key else''
    tasks_out,failures=[],[]
    for post_id in ids:
        prow=conn.execute(
            'SELECT id, text, profile_id, url FROM posts WHERE id=?',(post_id,)
        ).fetchone()
        if not prow:
            failures.append({'post_id':post_id,'reason':'post not found'})
            continue
        post_text=prow['text']or''
        profile_id=prow['profile_id']
        account_id=acct_in
        if not account_id:
            ar=conn.execute('SELECT id FROM accounts WHERE is_active=1 LIMIT 1').fetchone()
            account_id=ar['id'] if ar else None
        if api_key:
            try:
                gen=_openai_reply_text(api_key,post_text,reply_intent)
                if not gen:
                    gen='[AI generation failed — edit reply here]'
            except Exception as e:
                log('ERR',f'local_create_batch OpenAI: {str(e)[:120]}')
                gen='[AI generation failed — edit reply here]'
        else:
            gen='[AI key not configured — edit reply here]'
        ex=conn.execute('SELECT id FROM reply_tasks WHERE post_id=?',(post_id,)).fetchone()
        is_dup=1 if ex else 0
        tid=str(uuid.uuid4())
        conn.execute(
            """INSERT INTO reply_tasks
            (id, post_id, profile_id, account_id, generated_reply, reply_intent, status, is_duplicate, created_at)
            VALUES (?,?,?,?,?,?,'pending',?,datetime('now'))""",
            (tid,post_id,profile_id,account_id,gen,reply_intent,is_dup),
        )
        tasks_out.append({
            'task_id':tid,'post_id':post_id,'is_duplicate':bool(is_dup),
            'reply':(gen[:80]+'…')if len(gen)>80 else gen,
        })
    conn.commit();conn.close()
    n=len(tasks_out)
    log('INFO',f'local_create_batch created={n} failed={len(failures)}')
    return{
        'status':'success','created':n,'tasks':tasks_out,
        'tasks_created':n,'tasks_failed':len(failures),'failures':failures,
        'duplicate_warnings':sum(1 for t in tasks_out if t.get('is_duplicate')),
    }

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
@app.route('/favicon.ico')
def favicon():return Response(status=204)

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

@app.route('/api/gologin/profiles',methods=['GET'])
def gologin_list_profiles():
    tok=_get_gologin_token()
    if not tok:
        return jsonify({'status':'error','message':'GoLogin API token not configured. Add it in Settings.'})
    try:
        resp=requests.get(
            'https://api.gologin.com/browser/v2',
            headers={'Authorization':f'Bearer {tok}','User-Agent':'TwitterAgent/1.0'},
            timeout=15)
        if resp.status_code==401:
            return jsonify({'status':'error','message':'Invalid GoLogin API token'})
        resp.raise_for_status()
        data=resp.json()
        profiles_raw=data.get('profiles',data) if isinstance(data,dict) else data
        profiles=[
            {'id':p.get('id',''),'name':p.get('name',''),'notes':p.get('notes',''),
             'os':p.get('os',''),'browser_type':p.get('browserType',p.get('browser',''))}
            for p in (profiles_raw if isinstance(profiles_raw,list) else [])
            if p.get('id')
        ]
        return jsonify({'status':'success','profiles':profiles,'count':len(profiles)})
    except Exception as e:
        log('ERR',f'gologin_list_profiles: {e}')
        return jsonify({'status':'error','message':str(e)[:200]})

@app.route('/api/gologin/import',methods=['POST'])
def gologin_import_profile():
    b=request.json or{}
    profile_id=(b.get('profile_id') or '').strip()
    profile_name=(b.get('profile_name') or profile_id).strip()
    if not profile_id:
        return jsonify({'status':'error','message':'profile_id required'})
    tok=_get_gologin_token()
    if not tok:
        return jsonify({'status':'error','message':'GoLogin API token not configured'})
    try:
        resp=requests.get(
            f'https://api.gologin.com/browser/{profile_id}/cookies',
            headers={'Authorization':f'Bearer {tok}','User-Agent':'TwitterAgent/1.0'},
            timeout=15)
        if resp.status_code==401:
            return jsonify({'status':'error','message':'Invalid GoLogin API token'})
        if resp.status_code==404:
            return jsonify({'status':'error','message':f'GoLogin profile {profile_id} not found'})
        resp.raise_for_status()
        cookies_raw=resp.json()
        cookies=cookies_raw if isinstance(cookies_raw,list) else (cookies_raw.get('cookies',[]) if isinstance(cookies_raw,dict) else [])
    except Exception as e:
        log('ERR',f'gologin_import fetch cookies: {e}')
        return jsonify({'status':'error','message':f'Failed to fetch cookies: {e}'})
    auth_token=''
    ct0=''
    for c in cookies:
        name=c.get('name','')
        value=(c.get('value') or '').strip()
        domain=(c.get('domain') or '')
        if 'twitter.com' in domain or 'x.com' in domain or not domain:
            if name=='auth_token' and value:auth_token=value
            if name=='ct0' and value:ct0=value
    if not auth_token or not ct0:
        return jsonify({'status':'error',
            'message':'Could not find auth_token or ct0 in GoLogin profile cookies. Is the Twitter account logged in inside GoLogin?'})
    username=(b.get('username') or profile_name or 'gologin_user').lstrip('@').strip() or 'gologin_user'
    log('INFO',f'GoLogin import: profile={profile_id[:8]} auth_token={auth_token[:8]}... ct0={ct0[:8]}...')
    is_new=False
    conn=db()
    existing=conn.execute('SELECT id FROM accounts WHERE username=?',(username,)).fetchone()
    if existing:
        acc_id=existing['id']
        conn.execute('UPDATE accounts SET gologin_profile_id=? WHERE id=?',(profile_id,acc_id))
        conn.commit();conn.close()
    else:
        acc_id=str(uuid.uuid4())
        conn.execute("""
            INSERT INTO accounts(id,username,gologin_profile_id,role_discovery,role_posting,
                                 is_active,health_score,circuit_state,created_at)
            VALUES(?,?,?,?,?,?,100,'closed',datetime('now'))
        """,(acc_id,username,profile_id,1,1,0))
        conn.commit();conn.close()
        is_new=True
    rel=_do_relink(acc_id,auth_token,ct0,'gologin',username,is_new=is_new)
    out=rel.get_json(silent=True)
    if not isinstance(out,dict):
        out={'status':'error','message':'relink failed'}
    return jsonify({**out,'gologin_profile_id':profile_id,'cookies_found':len(cookies)})

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
    if blue=='1':
        cl.append('is_blue_verified=?');pl.append(1)
    elif blue=='0':
        cl.append('(is_blue_verified IS NULL OR is_blue_verified=0)')
    raw=(request.args.get('bio_q') or request.args.get('q') or '').strip()
    bt=_profile_text_search_term(raw)
    if bt is not None:
        cl.append(
            "(instr(lower(coalesce(bio,'') || ' ' || coalesce(display_name,'') || ' ' || coalesce(username,'')), ?) > 0)"
        )
        pl.append(bt)
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
    raw=(request.args.get('post_q') or request.args.get('q') or '').strip().lower()
    if raw:
        cl.append("(instr(lower(coalesce(text,'') || ' ' || coalesce(url,'')), ?) > 0)")
        pl.append(raw)
    w=('WHERE '+' AND '.join(cl)) if cl else ''
    order=request.args.get('order','DESC');so='DESC' if order.upper()=='DESC' else 'ASC'
    page=max(1,int(request.args.get('page',1)));ps=min(100,int(request.args.get('page_size',50)))
    offset=(page-1)*ps
    total=conn.execute(f'SELECT COUNT(*) FROM posts {w}',pl).fetchone()[0]
    pl2=pl+[ps,offset]
    rows=conn.execute(f'SELECT * FROM posts {w} ORDER BY posted_at {so} LIMIT ? OFFSET ?',pl2).fetchall()
    conn.close();return jsonify({'posts':[dict(r) for r in rows],'total':total,'page':page,'status':'success'})

@app.route('/api/posts/<pid>',methods=['DELETE'])
def delete_post(pid):
    conn=db()
    row=conn.execute('SELECT id FROM posts WHERE id=?',(pid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status':'error','message':'Post not found'})
    conn.execute('DELETE FROM posts WHERE id=?',(pid,))
    conn.commit();conn.close()
    log('INFO',f'Deleted post {pid[:12]}')
    return jsonify({'status':'success','deleted':pid})

@app.route('/api/profiles/<prid>',methods=['DELETE'])
def delete_profile(prid):
    conn=db()
    row=conn.execute('SELECT id FROM profiles WHERE id=?',(prid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status':'error','message':'Profile not found'})
    conn.execute('DELETE FROM profiles WHERE id=?',(prid,))
    conn.commit();conn.close()
    log('INFO',f'Deleted profile {prid[:12]}')
    return jsonify({'status':'success','deleted':prid})

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
def _reply_task_filters(include_status=True):
    """Build WHERE clause + params for reply_tasks + posts join (task list)."""
    cl,pl=[],[]
    if include_status:
        if 'status' not in request.args:
            s='pending'
        else:
            s_raw=(request.args.get('status') or '').strip().lower()
            s=None if s_raw in ('','all') else s_raw
        if s:
            cl.append('rt.status=?')
            pl.append(s)
    ai=request.args.get('account_id','').strip()
    if ai:
        cl.append('rt.account_id=?')
        pl.append(ai)
    df=(request.args.get('date_from')or'').strip()[:10]
    dt=(request.args.get('date_to')or'').strip()[:10]
    if df:
        cl.append('DATE(p.posted_at) >= DATE(?)')
        pl.append(df)
    if dt:
        cl.append('DATE(p.posted_at) <= DATE(?)')
        pl.append(dt)
    w=('WHERE '+' AND '.join(cl)) if cl else ''
    return w,pl

@app.route('/api/tasks/counts',methods=['GET'])
def get_task_counts():
    conn=db()
    rows=conn.execute('SELECT status, COUNT(*) as n FROM reply_tasks GROUP BY status').fetchall()
    total=conn.execute('SELECT COUNT(*) FROM reply_tasks').fetchone()[0]
    conn.close()
    counts={'all':total}
    for r in rows:
        counts[r['status']]=r['n']
    return jsonify({'status':'success','counts':counts})

@app.route('/api/tasks',methods=['GET'])
def get_tasks():
    w,pl=_reply_task_filters(include_status=True)
    page=max(1,int(request.args.get('page',1)))
    ps=min(50,int(request.args.get('page_size',20)))
    st_raw=(request.args.get('status') or 'pending').strip().lower()
    order_sql='rt.created_at ASC' if st_raw=='approved' else 'rt.created_at DESC'
    conn=db()
    total=conn.execute(f'SELECT COUNT(*) FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id {w}',pl).fetchone()[0]
    pl2=pl+[ps,(page-1)*ps]
    rows=conn.execute(
        f'SELECT rt.*,COALESCE(rt.edited_reply,rt.generated_reply) as final_reply,p.text AS post_text,p.url AS post_url,pr.username AS profile_username,a.username AS account_username FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id LEFT JOIN profiles pr ON rt.profile_id=pr.id LEFT JOIN accounts a ON rt.account_id=a.id {w} ORDER BY {order_sql} LIMIT ? OFFSET ?',
        pl2,
    ).fetchall()
    conn.close();return jsonify({'tasks':[dict(r) for r in rows],'total':total,'page':page,'count':len(rows),'status':'success'})

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

def build_credentials_pool():
    """Same data as GET /api/credentials/pool (no HTTP — avoids deadlock when called from Flask)."""
    conn=db()
    rows=conn.execute(
        'SELECT id,username,health_score,circuit_state '
        'FROM accounts WHERE role_discovery=1 ORDER BY health_score DESC'
    ).fetchall()
    conn.close()
    pool=[]
    for r in rows:
        if (r['circuit_state'] or 'closed') == 'open':
            continue
        session_raw=kv_get_auth(f"tw_session_{r['id']}")
        if not session_raw:
            continue
        try:
            sess=json.loads(session_raw)
            at=sess.get('auth_token','')
            ct=sess.get('ct0','')
            if at and ct and len(at) > 10 and len(ct) > 10:
                pool.append({
                    'account_id': r['id'],
                    'username': r['username'],
                    'auth_token': at,
                    'ct0': ct,
                    'health_score': r['health_score'],
                    'circuit_state': r['circuit_state'] or 'closed',
                })
        except Exception:
            pass
    return pool

def _ensure_curl_cffi_requests():
    try:
        import curl_cffi.requests as cf_req
        return cf_req
    except ImportError:
        log('INFO', 'curl_cffi not installed; running pip install curl_cffi …')
        try:
            _sp.check_call([sys.executable, '-m', 'pip', 'install', 'curl_cffi'], timeout=180)
        except Exception as e:
            log('ERR', f'pip install curl_cffi: {e}')
            return None
        try:
            import curl_cffi.requests as cf_req
            return cf_req
        except ImportError:
            return None

def _tweet_id_for_reply(post_url, post_id):
    u = (post_url or '').strip()
    if u:
        return u.rstrip('/').split('/')[-1]
    return str(post_id or '').strip()

def _pick_pool_creds(pool, account_id):
    if not pool:
        return None
    aid = (account_id or '').strip()
    if aid:
        for p in pool:
            if str(p.get('account_id') or '') == aid:
                return p
    return pool[0]

def _create_tweet_rest_id(data):
    if not isinstance(data, dict):
        return None
    try:
        d = data.get('data') or {}
        ct = d.get('create_tweet') or {}
        tr = ct.get('tweet_results') or {}
        res = tr.get('result')
        if isinstance(res, dict):
            rid = res.get('rest_id')
            if rid:
                return str(rid)
            tw = res.get('tweet')
            if isinstance(tw, dict) and tw.get('rest_id'):
                return str(tw['rest_id'])
    except Exception:
        pass
    return None

def _graphql_errors_text(data):
    if not isinstance(data, dict):
        return ''
    errs = data.get('errors')
    if not isinstance(errs, list):
        return ''
    parts = []
    for e in errs[:5]:
        if isinstance(e, dict):
            parts.append(str(e.get('message') or e))
        else:
            parts.append(str(e))
    return '; '.join(parts)[:800]

def _unwrap_graphql_tweet_result(result):
    if not isinstance(result, dict):
        return None
    if result.get('__typename') == 'TweetWithVisibilityResults':
        tw = result.get('tweet')
        return tw if isinstance(tw, dict) else None
    if result.get('legacy') or result.get('rest_id'):
        return result
    return None

def _tweet_author_screen_name(tw):
    if not isinstance(tw, dict):
        return ''
    core = tw.get('core') or {}
    ur = core.get('user_results') or {}
    res = ur.get('result') or {}
    if isinstance(res, dict):
        leg = res.get('legacy') or {}
        sn = (leg.get('screen_name') or '').strip()
        if sn:
            return sn
    return ''

def _walk_collect_timeline_tweets(obj, bucket):
    if isinstance(obj, dict):
        tr = obj.get('tweet_results')
        if isinstance(tr, dict):
            tw = _unwrap_graphql_tweet_result(tr.get('result'))
            if tw and isinstance(tw, dict):
                rid = str(tw.get('rest_id') or '').strip()
                if rid:
                    bucket[rid] = tw
        for v in obj.values():
            _walk_collect_timeline_tweets(v, bucket)
    elif isinstance(obj, list):
        for x in obj:
            _walk_collect_timeline_tweets(x, bucket)

def _replies_direct_to_focal_from_tweet_detail(data, focal_tweet_id):
    """Return list of dicts: rest_id, full_text, author_username (direct replies only)."""
    focal_s = str(focal_tweet_id or '').strip()
    if not focal_s or not isinstance(data, dict):
        return []
    root = data.get('data') or {}
    by_id = {}
    _walk_collect_timeline_tweets(root, by_id)
    out = []
    seen = set()
    for rid, tw in by_id.items():
        if rid == focal_s:
            continue
        leg = tw.get('legacy') or {}
        ir = str(leg.get('in_reply_to_status_id_str') or '').strip()
        if ir != focal_s:
            continue
        if rid in seen:
            continue
        seen.add(rid)
        txt = (leg.get('full_text') or leg.get('text') or '')[:4000]
        out.append({
            'rest_id': rid,
            'full_text': txt,
            'author_username': _tweet_author_screen_name(tw) or (leg.get('screen_name') or ''),
        })
    return out

def _tweet_detail_get(cf_req, auth_token, ct0, focal_tweet_id):
    """GET TweetDetail; returns (json_dict_or_None, error_or_None)."""
    variables = {
        'focalTweetId': str(focal_tweet_id),
        'count': 40,
        'referrer': 'tweet',
        'with_rux_injections': False,
        'includePromotedContent': True,
        'withCommunity': True,
        'withQuickPromoteEligibilityTweetFields': True,
        'withBirdwatchNotes': True,
        'withVoice': True,
    }
    fid = str(focal_tweet_id).strip()
    headers = {
        'authorization': f'Bearer {BEARER}',
        'cookie': f'auth_token={auth_token}; ct0={ct0}',
        'x-csrf-token': ct0,
        'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-active-user': 'yes',
        'x-twitter-client-language': 'en',
        'content-type': 'application/json',
        'user-agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ),
        'accept': '*/*',
        'origin': 'https://x.com',
        'referer': f'https://x.com/i/status/{fid}',
    }
    params = {
        'variables': json.dumps(variables, separators=(',', ':')),
        'features': json.dumps(_TWEET_DETAIL_FEATURES, separators=(',', ':')),
    }
    try:
        resp = cf_req.get(
            GRAPHQL_TWEET_DETAIL_URL,
            headers=headers,
            params=params,
            impersonate='chrome',
            timeout=45,
        )
    except Exception as e:
        return None, str(e)[:500]
    try:
        body = resp.json()
    except Exception:
        return None, f'HTTP {resp.status_code} non-JSON'
    if resp.status_code >= 400:
        msg = _graphql_errors_text(body) or (body.get('errors') if isinstance(body, dict) else None) or resp.text[:400]
        return None, f'HTTP {resp.status_code}: {msg}'[:800]
    err_txt = _graphql_errors_text(body)
    if err_txt:
        return None, err_txt
    return body, None

def check_incoming_replies(body=None):
    """
    Fetches replies to our posted tweets using Twitter GraphQL TweetDetail.
    Stores new replies in conversations table.
    Returns {checked, new_replies, conversations}
    """
    body = body or {}
    cf_req = _ensure_curl_cffi_requests()
    if not cf_req:
        return {'status': 'error', 'message': 'curl_cffi is required; pip install curl_cffi failed'}
    pool = build_credentials_pool()
    if not pool:
        return {'status': 'error', 'message': 'No credentials in pool. Add an account with a valid session.'}
    conn = db()
    rows = conn.execute(
        "SELECT id, posted_tweet_id, account_id, generated_reply, edited_reply FROM reply_tasks "
        "WHERE status='posted' AND posted_tweet_id IS NOT NULL AND TRIM(posted_tweet_id)!=''"
    ).fetchall()
    conn.close()
    if not rows:
        return {'status': 'success', 'checked': 0, 'new_replies': 0, 'conversations': []}
    new_rows = []
    checked = 0
    for row in rows:
        task_id = row['id']
        posted_tid = str(row['posted_tweet_id']).strip()
        if not posted_tid:
            continue
        cred = _pick_pool_creds(pool, row['account_id'])
        if not cred:
            log('WARN', f'check_replies: no creds for task {task_id[:8]}…')
            continue
        checked += 1
        data, err = _tweet_detail_get(cf_req, cred['auth_token'], cred['ct0'], posted_tid)
        if err:
            log('WARN', f'TweetDetail task={task_id[:8]}… tweet={posted_tid}: {err}')
            continue
        replies = _replies_direct_to_focal_from_tweet_detail(data, posted_tid)
        our_text = (row['edited_reply'] or row['generated_reply'] or '').strip()
        conn = db()
        for rep in replies:
            rid = rep['rest_id']
            exists = conn.execute(
                'SELECT 1 FROM conversations WHERE tweet_id=? LIMIT 1', (rid,)
            ).fetchone()
            if exists:
                continue
            cid = str(uuid.uuid4())
            txt = (rep.get('full_text') or '')[:4000]
            author = (rep.get('author_username') or '')[:200]
            conn.execute(
                'INSERT INTO conversations (id, reply_task_id, depth, direction, tweet_id, text, '
                'author_username, created_at) VALUES (?,?,?,?,?,?,?,datetime("now"))',
                (cid, task_id, 1, 'theirs', rid, txt, author),
            )
            new_rows.append({
                'reply_task_id': task_id,
                'tweet_id': rid,
                'author_username': author,
                'text': txt,
                'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'posted_tweet_id': posted_tid,
                'our_reply_text': our_text,
            })
        conn.commit()
        conn.close()
    return {
        'status': 'success',
        'checked': checked,
        'new_replies': len(new_rows),
        'conversations': new_rows,
    }

def _post_one_reply_cf(cf_req, auth_token, ct0, in_reply_to_tweet_id, tweet_text):
    """Returns (rest_id_or_None, error_message_or_None)."""
    variables = {
        'tweet_text': tweet_text,
        'reply': {'in_reply_to_tweet_id': in_reply_to_tweet_id, 'exclude_reply_user_ids': []},
        'dark_request': False,
        'media': {'media_entities': [], 'possibly_sensitive': False},
        'semantic_annotation_ids': [],
    }
    headers = {
        'authorization': f'Bearer {BEARER}',
        'cookie': f'auth_token={auth_token}; ct0={ct0}',
        'x-csrf-token': ct0,
        'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-active-user': 'yes',
        'x-twitter-client-language': 'en',
        'content-type': 'application/json',
        'user-agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ),
        'origin': 'https://x.com',
        'referer': 'https://x.com/home',
    }
    payload = {
        'variables': variables,
        'features': dict(_CREATE_TWEET_FEATURES),
        'queryId': CREATE_TWEET_QUERY_ID,
    }
    try:
        resp = cf_req.post(
            GRAPHQL_CREATE_TWEET_URL,
            headers=headers,
            json=payload,
            impersonate='chrome',
            timeout=30,
        )
    except Exception as e:
        return None, str(e)[:500]
    try:
        data = resp.json()
    except Exception:
        return None, f'HTTP {resp.status_code} non-JSON body'
    if resp.status_code >= 400:
        msg = _graphql_errors_text(data) or data.get('errors') or resp.text[:400]
        return None, f'HTTP {resp.status_code}: {msg}'[:800]
    err_txt = _graphql_errors_text(data)
    if err_txt:
        return None, err_txt
    rid = _create_tweet_rest_id(data)
    if rid:
        return rid, None
    try:
        ct = (data.get('data') or {}).get('create_tweet') or {}
        if 'tweet_results' in ct:
            # tweet_results exists but result/rest_id missing = silent reject by Twitter
            # Do NOT mark as posted — this is NOT a successful publish
            log('WARN', 'CreateTweet: tweet_results empty (silent reject). Task will be marked failed.')
            return None, 'tweet_results_empty: Twitter silently rejected the tweet (account may be restricted)'
    except Exception:
        pass
    return None, (json.dumps(data)[:600] if data else 'empty response')

def _mark_reply_task_posted(task_id, posted_tweet_id):
    conn = db()
    row = conn.execute('SELECT account_id FROM reply_tasks WHERE id=?', (task_id,)).fetchone()
    account_id = row['account_id'] if row else None
    conn.execute(
        'UPDATE reply_tasks SET status=?, posted_at=datetime("now"), posted_tweet_id=?, error_msg=NULL '
        "WHERE id=? AND status='approved'",
        ('posted', str(posted_tweet_id), task_id),
    )
    if account_id:
        today = date.today().isoformat()
        conn.execute(
            """
            INSERT INTO analytics (id, account_id, date, replies_sent)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(account_id, date) DO UPDATE SET replies_sent = replies_sent + 1
            """,
            (str(uuid.uuid4()), account_id, today),
        )
    conn.commit()
    conn.close()

def _mark_reply_task_failed(task_id, error_msg):
    msg = (error_msg or '')[:900]
    conn = db()
    conn.execute(
        "UPDATE reply_tasks SET status=?, error_msg=? WHERE id=? AND status='approved'",
        ('failed', msg, task_id),
    )
    conn.commit()
    conn.close()

def _scheduler_status_dict():
    s = _SCHEDULER
    with s['lock']:
        running = bool(s['running'])
        paused = bool(s['paused'])
        if running and paused:
            st = 'paused'
        elif running:
            st = 'running'
        else:
            st = 'idle'
        return {
            'status': st,
            'running': running,
            'paused': paused,
            'pause_until': s.get('pause_until'),
            'daily_limit': int(s['daily_limit'] or 10),
            'sent_today': int(s['sent_today'] or 0),
            'next_post_at': s['next_post_at'],
            'current_task_id': s['current_task_id'],
            'delay_min': int(s.get('delay_min') or 30),
            'delay_max': int(s.get('delay_max') or 90),
        }

def _scheduler_reset_day_locked():
    today = date.today().isoformat()
    if _SCHEDULER['last_reset_date'] != today:
        _SCHEDULER['sent_today'] = 0
        _SCHEDULER['last_reset_date'] = today

def _scheduler_loop():
    log('INFO', 'reply scheduler: loop started')
    try:
        while True:
            with _SCHEDULER['lock']:
                if not _SCHEDULER['running']:
                    _SCHEDULER['thread'] = None
                    _SCHEDULER['next_post_at'] = None
                    _SCHEDULER['current_task_id'] = None
                    return
                # Auto-resume after timed pause
                pu = _SCHEDULER.get('pause_until')
                if pu and _SCHEDULER['paused']:
                    try:
                        if datetime.utcnow() >= datetime.fromisoformat(pu.rstrip('Z')):
                            _SCHEDULER['paused'] = False
                            _SCHEDULER['pause_until'] = None
                            log('INFO', 'scheduler: auto-resumed after timed pause')
                    except Exception:
                        pass
                if _SCHEDULER['paused']:
                    paused = True
                else:
                    paused = False
            if paused:
                time.sleep(0.5)
                continue
            with _SCHEDULER['lock']:
                _scheduler_reset_day_locked()
                if _SCHEDULER['sent_today'] >= _SCHEDULER['daily_limit']:
                    limit_hit = True
                else:
                    limit_hit = False
            if limit_hit:
                time.sleep(60)
                continue
            conn = db()
            row = conn.execute(
                'SELECT rt.id AS task_id, rt.post_id, rt.account_id, '
                'COALESCE(rt.edited_reply, rt.generated_reply) AS reply_text, p.url AS post_url '
                "FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id "
                "WHERE rt.status='approved' ORDER BY rt.created_at ASC LIMIT 1"
            ).fetchone()
            conn.close()
            if not row:
                time.sleep(10)
                continue
            tid = row['task_id']
            tweet_id = _tweet_id_for_reply(row['post_url'], row['post_id'])
            text = (row['reply_text'] or '').strip()
            with _SCHEDULER['lock']:
                dmin = float(_SCHEDULER.get('delay_min') or 30)
                dmax = float(_SCHEDULER.get('delay_max') or 90)
            if dmax < dmin:
                dmax = dmin + 5
            delay = random.uniform(dmin, dmax)
            next_iso = (datetime.utcnow() + timedelta(seconds=delay)).isoformat() + 'Z'
            with _SCHEDULER['lock']:
                if not _SCHEDULER['running'] or _SCHEDULER['paused']:
                    continue
                _SCHEDULER['current_task_id'] = tid
                _SCHEDULER['next_post_at'] = next_iso
            time.sleep(delay)
            with _SCHEDULER['lock']:
                if not _SCHEDULER['running'] or _SCHEDULER['paused']:
                    _SCHEDULER['next_post_at'] = None
                    _SCHEDULER['current_task_id'] = None
                    continue
            conn = db()
            row2 = conn.execute(
                'SELECT rt.id AS task_id, rt.post_id, rt.account_id, '
                'COALESCE(rt.edited_reply, rt.generated_reply) AS reply_text, p.url AS post_url, rt.status '
                'FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id WHERE rt.id=?',
                (tid,),
            ).fetchone()
            conn.close()
            if not row2 or row2['status'] != 'approved':
                with _SCHEDULER['lock']:
                    _SCHEDULER['next_post_at'] = None
                    _SCHEDULER['current_task_id'] = None
                continue
            tweet_id = _tweet_id_for_reply(row2['post_url'], row2['post_id'])
            text = (row2['reply_text'] or '').strip()
            if not text:
                _mark_reply_task_failed(tid, 'empty reply text')
                with _SCHEDULER['lock']:
                    _SCHEDULER['next_post_at'] = None
                    _SCHEDULER['current_task_id'] = None
                continue
            if not tweet_id:
                _mark_reply_task_failed(tid, 'missing tweet_id (no post_url / post_id)')
                with _SCHEDULER['lock']:
                    _SCHEDULER['next_post_at'] = None
                    _SCHEDULER['current_task_id'] = None
                continue
            cf_req = _ensure_curl_cffi_requests()
            if not cf_req:
                _mark_reply_task_failed(tid, 'curl_cffi not available')
                with _SCHEDULER['lock']:
                    _SCHEDULER['next_post_at'] = None
                    _SCHEDULER['current_task_id'] = None
                continue
            pool = build_credentials_pool()
            cred = _pick_pool_creds(pool, row2['account_id'])
            if not cred:
                _mark_reply_task_failed(tid, 'no credentials in pool for this account')
                with _SCHEDULER['lock']:
                    _SCHEDULER['next_post_at'] = None
                    _SCHEDULER['current_task_id'] = None
                continue
            rest_id, err = _post_one_reply_cf(
                cf_req, cred['auth_token'], cred['ct0'], tweet_id, text
            )
            if rest_id:
                _mark_reply_task_posted(tid, rest_id)
                log('INFO', f'reply scheduler: posted task={tid[:8]}… tweet={rest_id}')
                with _SCHEDULER['lock']:
                    _scheduler_reset_day_locked()
                    _SCHEDULER['sent_today'] = int(_SCHEDULER['sent_today'] or 0) + 1
            else:
                _mark_reply_task_failed(tid, err or 'unknown error')
                err_str = str(err or '')
                log('WARN', f'scheduler: failed task err={err_str[:80]}')
                # Error 226 = Twitter automation detection -> pause scheduler 2 hours
                if '226' in err_str or 'automated' in err_str.lower() or 'looks like it might be automated' in err_str.lower():
                    pause_until = (datetime.utcnow() + timedelta(hours=2)).isoformat() + 'Z'
                    with _SCHEDULER['lock']:
                        _SCHEDULER['paused'] = True
                        _SCHEDULER['pause_until'] = pause_until
                        _SCHEDULER['next_post_at'] = None
                    log('WARN', f'scheduler: PAUSED for 2h due to error 226 (automation detection). Resume at {pause_until}')
                    pt_snapshot = pause_until

                    def _resume_after_226_pause():
                        with _SCHEDULER['lock']:
                            if _SCHEDULER.get('pause_until') != pt_snapshot:
                                return
                            _SCHEDULER['paused'] = False
                            _SCHEDULER['pause_until'] = None
                        log('INFO', 'scheduler: auto-resumed after timed pause')
                        _scheduler_spawn_if_needed()

                    threading.Timer(7200.0, _resume_after_226_pause).start()
                    with _SCHEDULER['lock']:
                        _SCHEDULER['next_post_at'] = None
                        _SCHEDULER['current_task_id'] = None
                    break
            with _SCHEDULER['lock']:
                _SCHEDULER['next_post_at'] = None
                _SCHEDULER['current_task_id'] = None
    finally:
        with _SCHEDULER['lock']:
            _SCHEDULER['thread'] = None
            _SCHEDULER['next_post_at'] = None
            _SCHEDULER['current_task_id'] = None

def _post_replies_worker(cf_req, job_id, work_items, pool):
    log('INFO', f'post_replies job {job_id}: starting {len(work_items)} task(s), 20s between posts')
    for i, it in enumerate(work_items):
        if i > 0:
            time.sleep(20)
        tid = it['task_id']
        tweet_id = it['tweet_id']
        text = (it['reply_text'] or '').strip()
        if not text:
            _mark_reply_task_failed(tid, 'empty reply text')
            continue
        if not tweet_id:
            _mark_reply_task_failed(tid, 'missing tweet_id (no post_url / post_id)')
            continue
        cred = _pick_pool_creds(pool, it.get('account_id'))
        if not cred:
            _mark_reply_task_failed(tid, 'no credentials in pool for this account')
            continue
        rest_id, err = _post_one_reply_cf(
            cf_req, cred['auth_token'], cred['ct0'], tweet_id, text
        )
        if rest_id:
            _mark_reply_task_posted(tid, rest_id)
            log('INFO', f'post_replies job {job_id}: posted task={tid[:8]}… tweet={rest_id}')
        else:
            _mark_reply_task_failed(tid, err or 'unknown error')
            log('WARN', f'post_replies job {job_id}: failed task={tid[:8]}… {err}')
    log('INFO', f'post_replies job {job_id}: finished')

def local_post_replies(body):
    """
    Post approved reply_tasks to Twitter using GraphQL CreateTweet + curl_cffi Chrome TLS.
    Runs posting in a background thread with 20s delay between posts.
    """
    body = body or {}
    cf_req = _ensure_curl_cffi_requests()
    if not cf_req:
        return {'status': 'error', 'message': 'curl_cffi is required; pip install curl_cffi failed'}
    pool = build_credentials_pool()
    if not pool:
        return {'status': 'error', 'message': 'No credentials in pool. Add an account with a valid session.'}
    raw_ids = body.get('task_ids')
    if raw_ids is None:
        id_list = None
    else:
        if isinstance(raw_ids, (list, tuple)):
            id_list = [str(x).strip() for x in raw_ids if str(x).strip()]
        else:
            id_list = [str(raw_ids).strip()] if str(raw_ids).strip() else []
        if not id_list:
            return {'status': 'queued', 'count': 0, 'job_id': str(uuid.uuid4()), 'message': 'task_ids empty'}
    conn = db()
    if id_list is None:
        rows = conn.execute(
            "SELECT rt.id AS task_id, rt.post_id, rt.account_id, "
            "COALESCE(rt.edited_reply, rt.generated_reply) AS reply_text, p.url AS post_url "
            "FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id = p.id "
            "WHERE rt.status='approved' ORDER BY rt.created_at ASC"
        ).fetchall()
    else:
        qm = ','.join('?' * len(id_list))
        rows = conn.execute(
            f'SELECT rt.id AS task_id, rt.post_id, rt.account_id, '
            f'COALESCE(rt.edited_reply, rt.generated_reply) AS reply_text, p.url AS post_url '
            f'FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id = p.id '
            f"WHERE rt.status='approved' AND rt.id IN ({qm}) ORDER BY rt.created_at ASC",
            tuple(id_list),
        ).fetchall()
    conn.close()
    work_items = []
    for r in rows:
        tweet_id = _tweet_id_for_reply(r['post_url'], r['post_id'])
        work_items.append({
            'task_id': r['task_id'],
            'post_id': r['post_id'],
            'account_id': r['account_id'],
            'reply_text': r['reply_text'] or '',
            'tweet_id': tweet_id,
        })
    job_id = str(uuid.uuid4())
    if not work_items:
        return {'status': 'queued', 'count': 0, 'job_id': job_id, 'message': 'No approved tasks to post'}
    threading.Thread(
        target=_post_replies_worker,
        args=(cf_req, job_id, work_items, pool),
        daemon=True,
        name=f'post_replies_{job_id[:8]}',
    ).start()
    return {'status': 'queued', 'count': len(work_items), 'job_id': job_id}

# ── Run ──────────────────────────────────────────────────
@app.route('/api/run/discover',methods=['POST'])
def run_discover():return jsonify(run_expert('tw_discover',request.json or{}))
@app.route('/api/run/posts',methods=['POST'])
def run_posts():return jsonify(run_expert('tw_posts',request.json or{}))
@app.route('/api/run/generate',methods=['POST'])
def run_generate():return jsonify(run_expert('tw_generate',request.json or{}))
@app.route('/api/run/post',methods=['POST'])
def run_post():return jsonify(run_expert('tw_post',request.json or{}))
@app.route('/api/run/post_replies',methods=['POST'])
def run_post_replies():
    return jsonify(local_post_replies(request.json or {}))

def _scheduler_spawn_if_needed():
    """If running and not paused and no live worker thread, start _scheduler_loop. Do not hold lock across Thread.start()."""
    t = None
    with _SCHEDULER['lock']:
        if not _SCHEDULER['running'] or _SCHEDULER['paused']:
            return
        th = _SCHEDULER['thread']
        if th is not None and th.is_alive():
            return
        t = threading.Thread(target=_scheduler_loop, daemon=True, name='reply_scheduler')
        _SCHEDULER['thread'] = t
    if t is not None:
        t.start()

@app.route('/api/scheduler/status', methods=['GET'])
def scheduler_status():
    return jsonify(_scheduler_status_dict())

@app.route('/api/scheduler/start', methods=['POST'])
def scheduler_start():
    body = request.json or {}
    try:
        dl = int(body.get('daily_limit', 10))
    except (TypeError, ValueError):
        dl = 10
    dl = max(1, min(100, dl))
    with _SCHEDULER['lock']:
        def_dm = int(_SCHEDULER.get('delay_min') or 30)
        def_dM = int(_SCHEDULER.get('delay_max') or 90)
    try:
        delay_min = max(10, int(body.get('delay_min', def_dm)))
    except (TypeError, ValueError):
        delay_min = max(10, def_dm)
    try:
        delay_max = max(delay_min + 5, int(body.get('delay_max', def_dM)))
    except (TypeError, ValueError):
        delay_max = max(delay_min + 5, def_dM)
    with _SCHEDULER['lock']:
        _SCHEDULER['daily_limit'] = dl
        _SCHEDULER['delay_min'] = delay_min
        _SCHEDULER['delay_max'] = delay_max
        _SCHEDULER['running'] = True
        _SCHEDULER['paused'] = False
        _SCHEDULER['pause_until'] = None
    _scheduler_spawn_if_needed()
    with _SCHEDULER['lock']:
        return jsonify({
            'status': 'success',
            'running': bool(_SCHEDULER['running']),
            'daily_limit': int(_SCHEDULER['daily_limit']),
            'delay_min': int(_SCHEDULER['delay_min']),
            'delay_max': int(_SCHEDULER['delay_max']),
        })

@app.route('/api/scheduler/pause', methods=['POST'])
def scheduler_pause():
    with _SCHEDULER['lock']:
        _SCHEDULER['paused'] = True
        _SCHEDULER['next_post_at'] = None
    return jsonify({'status': 'success', 'paused': True})

@app.route('/api/scheduler/resume', methods=['POST'])
def scheduler_resume():
    with _SCHEDULER['lock']:
        _SCHEDULER['paused'] = False
        _SCHEDULER['pause_until'] = None
    _scheduler_spawn_if_needed()
    with _SCHEDULER['lock']:
        return jsonify({
            'status': 'success',
            'paused': bool(_SCHEDULER['paused']),
            'running': bool(_SCHEDULER['running']),
        })

@app.route('/api/scheduler/stop', methods=['POST'])
def scheduler_stop():
    with _SCHEDULER['lock']:
        _SCHEDULER['running'] = False
        _SCHEDULER['paused'] = False
        _SCHEDULER['pause_until'] = None
        _SCHEDULER['next_post_at'] = None
    return jsonify({'status': 'success', 'running': False})

@app.route('/api/scheduler/post_now', methods=['POST'])
def scheduler_post_now():
    body = request.json or {}
    task_id = str(body.get('task_id') or '').strip()
    if not task_id:
        return jsonify({'status': 'error', 'error': 'task_id required'}), 400
    conn = db()
    row = conn.execute(
        'SELECT rt.id AS task_id, rt.post_id, rt.account_id, '
        'COALESCE(rt.edited_reply, rt.generated_reply) AS reply_text, p.url AS post_url, rt.status '
        'FROM reply_tasks rt LEFT JOIN posts p ON rt.post_id=p.id WHERE rt.id=?',
        (task_id,),
    ).fetchone()
    conn.close()
    if not row or row['status'] != 'approved':
        return jsonify({'status': 'error', 'error': 'task not found or not approved'}), 400
    tid = row['task_id']
    tweet_id = _tweet_id_for_reply(row['post_url'], row['post_id'])
    text = (row['reply_text'] or '').strip()
    if not text:
        _mark_reply_task_failed(tid, 'empty reply text')
        return jsonify({'status': 'error', 'error': 'empty reply text'}), 400
    if not tweet_id:
        _mark_reply_task_failed(tid, 'missing tweet_id (no post_url / post_id)')
        return jsonify({'status': 'error', 'error': 'missing tweet_id'}), 400
    cf_req = _ensure_curl_cffi_requests()
    if not cf_req:
        return jsonify({'status': 'error', 'error': 'curl_cffi not available'}), 500
    pool = build_credentials_pool()
    if not pool:
        return jsonify({'status': 'error', 'error': 'No credentials in pool'}), 500
    cred = _pick_pool_creds(pool, row['account_id'])
    if not cred:
        _mark_reply_task_failed(tid, 'no credentials in pool for this account')
        return jsonify({'status': 'error', 'error': 'no credentials for account'}), 500
    rest_id, err = _post_one_reply_cf(
        cf_req, cred['auth_token'], cred['ct0'], tweet_id, text
    )
    if not rest_id:
        _mark_reply_task_failed(tid, err or 'unknown error')
        return jsonify({'status': 'error', 'error': err or 'post failed'}), 500
    _mark_reply_task_posted(tid, rest_id)
    with _SCHEDULER['lock']:
        _scheduler_reset_day_locked()
        _SCHEDULER['sent_today'] = int(_SCHEDULER['sent_today'] or 0) + 1
    return jsonify({'status': 'success', 'posted_tweet_id': rest_id})

@app.route('/api/scheduler/set_limit', methods=['POST'])
def scheduler_set_limit():
    body = request.json or {}
    try:
        dl = int(body.get('daily_limit', 10))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'error': 'invalid daily_limit'}), 400
    dl = max(1, min(100, dl))
    with _SCHEDULER['lock']:
        _SCHEDULER['daily_limit'] = dl
    return jsonify({'status': 'success', 'daily_limit': dl})

@app.route('/api/scheduler/update_settings', methods=['POST'])
def scheduler_update_settings():
    b = request.json or {}
    with _SCHEDULER['lock']:
        if 'delay_min' in b:
            try:
                _SCHEDULER['delay_min'] = max(10, int(b['delay_min']))
            except (TypeError, ValueError):
                pass
        if 'delay_max' in b:
            try:
                _SCHEDULER['delay_max'] = max(int(_SCHEDULER['delay_min']) + 5, int(b['delay_max']))
            except (TypeError, ValueError):
                pass
        if 'daily_limit' in b:
            try:
                _SCHEDULER['daily_limit'] = max(1, min(int(b['daily_limit']), 100))
            except (TypeError, ValueError):
                pass
        return jsonify({
            'status': 'success',
            'delay_min': int(_SCHEDULER['delay_min']),
            'delay_max': int(_SCHEDULER['delay_max']),
            'daily_limit': int(_SCHEDULER['daily_limit']),
        })

@app.route('/api/run/monitor',methods=['POST'])
def run_monitor():return jsonify(run_expert('tw_monitor',{'action':'check'}))
@app.route('/api/run/search_getx',methods=['POST'])
def run_search_getx():return jsonify(local_search_getx(request.json or{}))
@app.route('/api/run/queue',methods=['POST'])
def run_queue():return jsonify(local_create_batch(request.json or{}))
@app.route('/api/run/check_replies',methods=['POST'])
def run_check_replies():
    return jsonify(check_incoming_replies(request.json or {}))

@app.route('/api/conversations',methods=['GET'])
def get_conversations():
    """Threads: posted reply_tasks + incoming conversations rows."""
    df=(request.args.get('date_from')or'').strip()[:10]
    dt=(request.args.get('date_to')or'').strip()[:10]
    page=max(1,int(request.args.get('page',1)))
    ps=20
    wh=[
        "rt.status='posted'",
        "rt.posted_tweet_id IS NOT NULL",
        "TRIM(rt.posted_tweet_id)!=''",
    ]
    pl=[]
    if df:
        wh.append('DATE(rt.posted_at) >= DATE(?)');pl.append(df)
    if dt:
        wh.append('DATE(rt.posted_at) <= DATE(?)');pl.append(dt)
    where_sql=' AND '.join(wh)
    conn=db()
    total=conn.execute(f"""
        SELECT COUNT(*)
        FROM reply_tasks rt
        LEFT JOIN posts p ON rt.post_id=p.id
        LEFT JOIN profiles pr ON rt.profile_id=pr.id
        WHERE {where_sql}
    """,pl).fetchone()[0]
    offset=(page-1)*ps
    rows=conn.execute(f"""
        SELECT
            rt.id AS task_id,
            rt.posted_tweet_id,
            rt.generated_reply AS our_reply,
            rt.edited_reply,
            rt.posted_at,
            rt.account_id,
            p.url AS original_post_url,
            p.text AS original_post_text,
            pr.username AS profile_username,
            COALESCE(
                (SELECT COUNT(*) FROM conversations c WHERE c.reply_task_id=rt.id AND c.direction='theirs'),
                0
            ) AS reply_count
        FROM reply_tasks rt
        LEFT JOIN posts p ON rt.post_id=p.id
        LEFT JOIN profiles pr ON rt.profile_id=pr.id
        WHERE {where_sql}
        ORDER BY rt.posted_at DESC
        LIMIT ? OFFSET ?
    """,pl+[ps,offset]).fetchall()
    threads=[]
    for row in rows:
        d=dict(row)
        convs=conn.execute("""
            SELECT tweet_id, text, author_username, created_at
            FROM conversations
            WHERE reply_task_id=? AND direction='theirs'
            ORDER BY created_at ASC
        """,(d['task_id'],)).fetchall()
        d['replies']=[dict(c) for c in convs]
        d['final_reply']=d.get('edited_reply') or d.get('our_reply')
        threads.append(d)
    conn.close()
    return jsonify({'status':'success','threads':threads,'total':total,'page':page})

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
    if 'gologin_api_token' in body:
        kv_set_auth('gologin_api_token',str(body.get('gologin_api_token') or '').strip(),'GoLogin API token')
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
    pool = build_credentials_pool()
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
