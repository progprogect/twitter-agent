# =============================================================================
# EXTELLA EXPERT: tw_extella_ai_probe
# =============================================================================
# DESCRIPTION: Diagnostic: probes Extella's internal AI endpoints to find what's available for LLM calls without user's own OpenAI key
#
# KWARGS (default parameters):
# {
#   "extella_token_key": "extella_api_token"
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_extella_ai_probe    # sync this file only
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

def tw_extella_ai_probe(
    extella_token_key: str = "extella_api_token"
) -> dict:
    import os, requests

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")

    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200: return r.json().get("value", "")
        except Exception: pass
        return ""

    token = kv_get(extella_token_key)
    headers = {"X-Auth-Token": token, "Content-Type": "application/json"} if token else {}

    results = {}
    test_prompt = "Reply with exactly: OK"

    # Endpoints to probe
    endpoints = [
        ("POST", f"{BASE_URL}/api/ai/chat",         {"message": test_prompt}),
        ("POST", f"{BASE_URL}/api/ai/complete",      {"prompt": test_prompt}),
        ("POST", f"{BASE_URL}/api/llm/chat",         {"message": test_prompt}),
        ("POST", f"{BASE_URL}/api/llm/complete",     {"prompt": test_prompt}),
        ("POST", f"{BASE_URL}/api/chat",             {"message": test_prompt}),
        ("POST", f"{BASE_URL}/api/ai/completion",    {"prompt": test_prompt}),
        ("GET",  f"{BASE_URL}/api/ai/models",        None),
        ("GET",  f"{BASE_URL}/api/llm/models",       None),
        ("POST", f"{BASE_URL}/api/openai/chat/completions",
         {"model": "gpt-4o-mini",
          "messages": [{"role": "user", "content": test_prompt}],
          "max_tokens": 10}),
        ("POST", f"{BASE_URL}/api/v1/chat/completions",
         {"model": "gpt-4o-mini",
          "messages": [{"role": "user", "content": test_prompt}],
          "max_tokens": 10}),
    ]

    for method, url, body in endpoints:
        try:
            if method == "POST":
                r = requests.post(url, headers=headers, json=body, timeout=8)
            else:
                r = requests.get(url, headers=headers, timeout=8)
            results[url.replace(BASE_URL, "")] = {
                "status": r.status_code,
                "body": str(r.json())[:120] if r.headers.get("content-type","").startswith("application/json") else r.text[:80]
            }
            print(f"[probe] {method} {url[-40:]}: HTTP {r.status_code}")
        except Exception as e:
            results[url.replace(BASE_URL, "")] = {"error": str(e)[:60]}

    working = [k for k, v in results.items() if v.get("status", 0) not in (404, 405, 0)]
    return {
        "status": "success",
        "token_present": bool(token),
        "results": results,
        "working_endpoints": working,
        "conclusion": f"Found {len(working)} non-404 endpoints: {working}" if working else "All endpoints 404/error"
    }
