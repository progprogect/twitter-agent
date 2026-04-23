#!/usr/bin/env python3
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
                write_state({'status':'success','auth_token':tok,'ct0':ct,'pid':pid})
                time.sleep(3);break
        else:
            st=read_state()
            if st.get('status') not in('success','cancelled'):
                write_state({'status':'error','error':'Timeout: login not completed','pid':pid})
        try:browser.close()
        except:pass
except Exception as e:
    write_state({'status':'error','error':str(e),'pid':os.getpid()})
