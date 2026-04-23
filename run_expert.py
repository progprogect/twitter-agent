#!/usr/bin/env python3
"""
run_expert.py — вызов Extella эксперта из консоли Cursor

ИСПОЛЬЗОВАНИЕ:
  python run_expert.py <expert_name> [param=value ...]
  python run_expert.py <expert_name> --json '{"param": "value"}'
  python run_expert.py --list
  python run_expert.py --search <query>

ПРИМЕРЫ:
  python run_expert.py tw_search_getx profile_description="venture investors" limit=5 dry_run=true
  python run_expert.py tw_discover keywords="AI startup" limit=3 dry_run=true
  python run_expert.py tw_posts posts_per_profile=5 dry_run=true
  python run_expert.py tw_debug action=diagnostics
  python run_expert.py tw_server action=status
  python run_expert.py --search "twitter discover profiles"
  python run_expert.py --list

ASYNC (для долгих задач — tw_discover, tw_posts):
  python run_expert.py tw_discover keywords="startup" --async
"""

import sys, os, json, time, requests
from pathlib import Path

BASE_URL    = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
TOKEN_FILE  = Path(__file__).parent / ".api_token"

def get_token():
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return os.environ.get("EXTELLA_TOKEN", "")

def headers():
    t = get_token()
    if not t:
        print("❌ No token. Ensure .api_token exists.")
        sys.exit(1)
    return {"X-Auth-Token": t, "Content-Type": "application/json"}

def parse_value(v):
    """Auto-cast: 'true'→bool, '42'→int, '3.14'→float, else str"""
    if v.lower() == "true":  return True
    if v.lower() == "false": return False
    try: return int(v)
    except ValueError: pass
    try: return float(v)
    except ValueError: pass
    return v

def run_expert(name, params, async_mode=False):
    payload = {"expert_name": name, "params": params}
    if async_mode:
        payload["wait"] = False

    r = requests.post(
        BASE_URL + "/api/expert/run",
        headers=headers(),
        json=payload,
        timeout=180
    )

    if r.status_code != 200:
        print(f"❌ HTTP {r.status_code}: {r.text[:200]}")
        sys.exit(1)

    data = r.json()

    if async_mode and data.get("task_id"):
        task_id = data["task_id"]
        print(f"⏳ Async task started: {task_id}")
        print("   Polling for result...")
        return poll_task(task_id)

    result = data.get("result", data)
    return result

def poll_task(task_id, max_wait=300, interval=3):
    """Poll task status until completed."""
    waited = 0
    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        r = requests.post(
            BASE_URL + "/api/task/check",
            headers=headers(),
            json={"task_id": task_id},
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            status = data.get("task_status", "")
            if "completed" in str(status).lower():
                return data.get("result", data)
            if "error" in str(status).lower():
                print(f"❌ Task failed: {status}")
                return data
            # Still running
            print(f"   [{waited}s] {status}...")
    print(f"⚠️  Timeout after {max_wait}s")
    return {"status": "timeout", "task_id": task_id}

def list_experts(query="tw_"):
    r = requests.post(
        BASE_URL + "/api/blocks/search",
        headers=headers(),
        json={"query": query, "limit": 30}
    )
    if r.status_code == 200:
        matches = r.json().get("matches", [])
        print(f"\n{'NAME':<30} {'SCORE':<8} DESCRIPTION")
        print("-" * 80)
        for m in matches:
            desc = (m.get("description") or "")[:45]
            print(f"{m['name']:<30} {m.get('score',0):<8} {desc}")
    else:
        print(f"❌ HTTP {r.status_code}")

def main():
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__); sys.exit(0)

    # --list
    if args[0] == "--list":
        list_experts("tw_")
        sys.exit(0)

    # --search <query>
    if args[0] == "--search":
        q = " ".join(args[1:]) if len(args) > 1 else "twitter"
        list_experts(q)
        sys.exit(0)

    expert_name = args[0]
    async_mode  = "--async" in args
    remaining   = [a for a in args[1:] if a != "--async"]

    params = {}

    # --json '{"key": "value"}'
    if "--json" in remaining:
        idx = remaining.index("--json")
        try:
            params = json.loads(remaining[idx + 1])
        except (IndexError, json.JSONDecodeError) as e:
            print(f"❌ Invalid JSON: {e}")
            sys.exit(1)
    else:
        # key=value pairs
        for arg in remaining:
            if "=" in arg:
                k, v = arg.split("=", 1)
                params[k.strip()] = parse_value(v.strip())
            else:
                print(f"⚠️  Skipping unrecognized arg: {arg}")

    print(f"🚀 Running: {expert_name}")
    if params:
        print(f"   Params: {json.dumps(params, ensure_ascii=False)}")
    if async_mode:
        print("   Mode: async")
    print()

    result = run_expert(expert_name, params, async_mode)

    # Pretty print result
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    # Summary line for common fields
    if isinstance(result, dict):
        status = result.get("status", "?")
        icon   = "✅" if status == "success" else "❌"
        extra  = ""
        for key in ("profiles_found", "posts_saved", "message", "url", "error"):
            if key in result:
                extra = f" | {key}: {result[key]}"
                break
        print(f"\n{icon} {status}{extra}")

if __name__ == "__main__":
    main()
