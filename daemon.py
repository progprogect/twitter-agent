#!/usr/bin/env python3
import time,json,requests,signal,sys
from pathlib import Path
from datetime import datetime

BASE_URL  = 'https://api.extella.ai'
TOKEN     = '3b3d318d-1a04-430a-91f4-30ac1a0a6d64'
DB_KEY    = 'tw_db_path'
TOK_KEY   = 'extella_api_token'
INTERVAL  = 900
LOCK_FILE = Path('/Users/mikitavalkunovich/Documents/twitter_agent/.background.lock')

def run_expert(name, params):
    try:
        r=requests.post(f'{BASE_URL}/api/expert/run',
            headers={'X-Auth-Token':TOKEN,'Content-Type':'application/json'},
            json={'expert_name':name,'params':params},timeout=120)
        if r.status_code==200:
            d=r.json()
            return d.get('result') or d
    except: pass
    return {}

def kv_set(key,value):
    try:
        requests.post(f'{BASE_URL}/api/kv/set',
            headers={'X-Auth-Token':TOKEN},
            json={'key':key,'value':value},timeout=10)
    except: pass

def on_stop(sig,frame):
    LOCK_FILE.unlink(missing_ok=True)
    sys.exit(0)

signal.signal(signal.SIGTERM,on_stop)
signal.signal(signal.SIGINT,on_stop)

print(f'🔄 Background daemon started. Interval: 15min')

while True:
    try:
        kv_set('tw_last_heartbeat',datetime.utcnow().isoformat())
        ts=datetime.utcnow().strftime('%H:%M:%S')
        print(f'[{ts}] Checking conversations...')
        result=run_expert('tw_monitor',{
            'action':'check',
            'db_path_key':DB_KEY,
            'extella_token_key':TOK_KEY
        })
        n=result.get('new_responses',0)
        if n>0: print(f'  ✅ {n} new responses — followup tasks created')
    except Exception as e:
        print(f'  ⚠️ Error: {e}')
    time.sleep(INTERVAL)