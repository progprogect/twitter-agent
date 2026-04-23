#!/usr/bin/env python3
"""
sync_to_extella.py
------------------
Syncs edited experts from experts/ folder back to Extella database.

USAGE:
  python sync_to_extella.py tw_discover     # one expert
  python sync_to_extella.py --all           # all experts
  python sync_to_extella.py --list          # list available
"""
import sys, os, re, json, requests
from pathlib import Path

BASE_URL    = os.environ.get('EXTELLA_API_URL', 'https://api.extella.ai')
EXPERTS_DIR = Path(__file__).parent / 'experts'
TOKEN_FILE  = Path(__file__).parent / '.api_token'

def get_token():
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding='utf-8').strip()
    t = os.environ.get('EXTELLA_TOKEN', '')
    if t: return t
    try:
        r = requests.post(BASE_URL + '/api/kv/get',
                          json={'key': 'extella_api_token'}, timeout=10)
        if r.status_code == 200:
            return r.json().get('value', '')
    except Exception:
        pass
    return ''

def parse_expert_file(path):
    content = path.read_text(encoding='utf-8')
    lines   = content.splitlines()
    code_start = 0
    last_sep   = 0
    for i, line in enumerate(lines):
        if line.strip().startswith('# ==='):
            last_sep = i
    if last_sep:
        for j in range(last_sep + 1, len(lines)):
            if lines[j].strip():
                code_start = j
                break
    code = '\n'.join(lines[code_start:]).strip()
    description = ''
    m = re.search(r'# DESCRIPTION: (.+)', content)
    if m: description = m.group(1).strip()
    kwargs = {}
    meta_file = EXPERTS_DIR / '_metadata.json'
    if meta_file.exists():
        try:
            all_meta = json.loads(meta_file.read_text(encoding='utf-8'))
            name = path.stem
            if name in all_meta:
                kwargs = all_meta[name].get('kwargs', {})
                if not description:
                    description = all_meta[name].get('description', '')
        except Exception:
            pass
    return {'name': path.stem, 'code': code,
            'description': description, 'kwargs': kwargs}

def upload_expert(expert_data, token):
    headers = {'X-Auth-Token': token, 'Content-Type': 'application/json'}
    payload = {
        'name':        expert_data['name'],
        'code':        expert_data['code'],
        'description': expert_data['description'],
        'kwargs':      expert_data['kwargs'],
    }
    try:
        r = requests.post(BASE_URL + '/api/expert/save',
                          headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            result = r.json()
            if result.get('status') == 'success':
                print('  OK: ' + expert_data['name'])
                return True
            else:
                print('  FAIL: ' + expert_data['name'] + ' -> ' + str(result))
                return False
        else:
            print('  FAIL: ' + expert_data['name'] + ' HTTP ' + str(r.status_code))
            return False
    except Exception as e:
        print('  ERROR: ' + expert_data['name'] + ' -> ' + str(e))
        return False

def main():
    args = sys.argv[1:]
    if not args or '--help' in args:
        print(__doc__); sys.exit(0)
    token = get_token()
    if not token:
        print('No token! Ensure .api_token exists.'); sys.exit(1)
    print('Token: ' + token[:8] + '...')
    if '--list' in args:
        files = sorted(EXPERTS_DIR.glob('*.py'))
        print('Available (' + str(len(files)) + '):')
        for f in files: print('  ' + f.stem)
        sys.exit(0)
    if '--all' in args:
        names = [f.stem for f in sorted(EXPERTS_DIR.glob('*.py'))]
    else:
        names = [a for a in args if not a.startswith('--')]
    if not names:
        print('No experts specified.'); sys.exit(1)
    print('Syncing ' + str(len(names)) + ' expert(s)...')
    ok, fail = 0, 0
    for name in names:
        py_file = EXPERTS_DIR / (name + '.py')
        if not py_file.exists():
            print('  NOT FOUND: ' + name + '.py'); fail += 1; continue
        data = parse_expert_file(py_file)
        if upload_expert(data, token): ok += 1
        else: fail += 1
    print('Done: ' + str(ok) + ' uploaded, ' + str(fail) + ' failed')

if __name__ == '__main__':
    main()