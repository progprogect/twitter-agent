# =============================================================================
# EXTELLA EXPERT: tw_query_expand
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — LLM Query Expansion. Reads ai_provider from Settings. Same fallback chain as tw_generate: tries configured provider first, then falls to rule_based. For extella/openai — passes api_key_name='openai_api_key' to openai_chat. For groq — reads groq_api_key. For anthropic — reads anthropic_api_key. Parameters: user_description; mode — profiles/posts; flask_port; db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "db_path_key": "tw_db_path",
#   "extella_token_key": "extella_api_token",
#   "flask_port": 7842,
#   "mode": "profiles",
#   "user_description": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_query_expand    # sync this file only
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

def tw_query_expand(
    user_description: str = "",
    mode: str = "profiles",
    flask_port: int = 7842,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import os, json, re, requests

    print(f"[1/4] 🤖 tw_query_expand: mode={mode}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")

    if not user_description.strip():
        return {"status": "error", "message": "user_description is required"}

    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200: return r.json().get("value", "")
        except Exception: pass
        return ""

    def run_expert(name, params):
        token = kv_get(extella_token_key)
        if not token:
            return {"status": "error", "error": "no_token"}
        try:
            r = requests.post(f"{BASE_URL}/api/expert/run",
                headers={"X-Auth-Token": token, "Content-Type": "application/json"},
                json={"expert_name": name, "params": params}, timeout=60)
            if r.status_code == 200:
                data = r.json()
                result = data.get("result")
                return result if isinstance(result, dict) else (data if isinstance(data, dict) else {})
            return {"status": "error", "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Read AI provider from Settings ────────────────────────────
    ai_provider = "extella"
    try:
        r = requests.get(f"http://127.0.0.1:{flask_port}/api/settings", timeout=5)
        if r.status_code == 200:
            ai_provider = r.json().get("ai_provider", "extella") or "extella"
    except Exception:
        pass
    print(f"[2/4] ⚙️  Provider from settings: {ai_provider}")

    # ── Build prompt ──────────────────────────────────────────────
    system_prompt = ("You are an expert at converting natural language into precise Twitter "
                     "search parameters. Always return valid JSON only, no markdown, no text "
                     "outside the JSON.")

    if mode == "profiles":
        prompt = f"""Convert this description into Twitter search parameters.
User wants to find: "{user_description}"

Return ONLY valid JSON:
{{
  "search_queries": ["phrase1", "phrase2", "phrase3"],
  "topic_keywords": ["topic1", "topic2", "topic3", "topic4", "topic5"],
  "min_followers": 500,
  "rationale": "one-line explanation"
}}

- search_queries: 3-5 short Twitter people search phrases (2-4 words each)
- topic_keywords: 5-10 words to match in bios/posts for relevance filtering
- min_followers: appropriate minimum (0-10000)"""
    else:
        prompt = f"""Extract topic keywords for Twitter post filtering.
User wants posts about: "{user_description}"

Return ONLY valid JSON:
{{
  "topic_keywords": ["keyword1", "keyword2", "keyword3"],
  "must_include_any": ["high_signal1", "high_signal2"],
  "rationale": "one-line explanation"
}}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": prompt}
    ]

    # ── Try AI providers with fallback — same pattern as tw_generate ──
    result_text = None
    source = "rule_based"
    errors = []

    # Build provider order: configured first, then fallback
    provider_order = [ai_provider]
    for fb in ["openai", "groq", "anthropic"]:
        if fb != ai_provider and fb not in provider_order:
            provider_order.append(fb)

    for provider in provider_order:
        if result_text:
            break

        try:
            # ── extella or openai: use openai_chat expert with stored key ──
            if provider in ("extella", "openai"):
                # openai_chat requires api_key_name pointing to KV Store key
                # "Extella AI" = openai_chat with user's stored openai_api_key
                result = run_expert("openai_chat", {
                    "prompt": prompt,
                    "system_prompt": system_prompt,
                    "api_key_name": "openai_api_key",  # reads from KV Store
                    "model": "gpt-4o-mini",
                    "temperature": 0.2,
                    "max_tokens": 400
                })
                if result and result.get("status") != "error" and not result.get("error"):
                    resp = result.get("response", result.get("content", ""))
                    if isinstance(resp, dict):
                        choices = resp.get("choices", [])
                        if choices:
                            result_text = choices[0].get("message", {}).get("content", "").strip()
                    elif isinstance(resp, str) and resp.strip():
                        result_text = resp.strip()

                    if result_text:
                        source = "extella" if ai_provider == "extella" else "openai"
                        print(f"[3/4] ✅ {source} (openai_chat) success")
                    else:
                        err = f"{provider}: empty response ({str(result)[:80]})"
                        errors.append(err)
                        print(f"[3/4] ⚠️  {err}")
                else:
                    msg = result.get("error", result.get("message", "?")) if result else "no result"
                    errors.append(f"{provider}: {str(msg)[:80]}")
                    print(f"[3/4] ⚠️  {provider}: {str(msg)[:60]}")

            # ── groq: free, fast, no OpenAI dependency ────────────
            elif provider == "groq":
                api_key = kv_get("groq_api_key")
                if not api_key:
                    errors.append("groq: groq_api_key not in KV Store")
                    print("[3/4] ⚠️  Groq: key not found")
                    continue
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={"model": "llama-3.1-8b-instant", "messages": messages,
                          "temperature": 0.2, "max_tokens": 400},
                    timeout=15
                )
                if r.status_code == 200:
                    result_text = r.json()["choices"][0]["message"]["content"]
                    source = "groq"
                    print(f"[3/4] ✅ Groq success")
                else:
                    errors.append(f"groq: HTTP {r.status_code}")

            # ── anthropic ─────────────────────────────────────────
            elif provider == "anthropic":
                api_key = kv_get("anthropic_api_key")
                if not api_key:
                    errors.append("anthropic: key not in KV Store")
                    continue
                r = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                             "Content-Type": "application/json"},
                    json={"model": "claude-3-haiku-20240307", "max_tokens": 400,
                          "system": system_prompt,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=15
                )
                if r.status_code == 200:
                    result_text = r.json()["content"][0]["text"]
                    source = "anthropic"
                    print(f"[3/4] ✅ Anthropic success")
                else:
                    errors.append(f"anthropic: HTTP {r.status_code}")

        except Exception as e:
            errors.append(f"{provider}: {str(e)[:80]}")
            print(f"[3/4] ⚠️  {provider} exception: {str(e)[:60]}")

    # ── Parse AI result ───────────────────────────────────────────
    if result_text:
        try:
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                parsed["status"] = "success"
                parsed["source"] = source
                n = len(parsed.get("search_queries", parsed.get("topic_keywords", [])))
                print(f"[4/4] ✅ {source}: {n} terms")
                return parsed
        except Exception as e:
            print(f"[3/4] ⚠️  JSON parse failed: {e}")

    # ── Rule-based fallback ───────────────────────────────────────
    print(f"[3/4] 🔧 Rule-based (all providers failed: {errors[:2]})")
    result = _expand_rule_based(user_description, mode)
    result["provider_errors"] = errors
    print(f"[4/4] ✅ Rule-based: {len(result.get('search_queries', result.get('topic_keywords', [])))} terms")
    return result


def _expand_rule_based(description: str, mode: str) -> dict:
    import re

    STOP_WORDS = {
        'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
        'from', 'this', 'that', 'these', 'those', 'is', 'are', 'was', 'were', 'will',
        'would', 'could', 'should', 'have', 'has', 'had', 'not', 'also', 'very',
        'they', 'their', 'who', 'what', 'which', 'when', 'where', 'how', 'all', 'any',
        'some', 'looking', 'active', 'people', 'users', 'twitter', 'someone',
        'interested', 'want', 'need', 'like', 'make', 'know', 'about', 'find', 'get',
        'posts', 'tweets', 'content', 'account', 'person', 'around', 'focus', 'focused',
        'type', 'kind', 'such', 'more', 'most', 'less', 'than', 'too', 'also', 'well'
    }

    words = re.findall(r'\b[a-zA-Z]{3,}\b', description.lower())
    keywords = list(dict.fromkeys(w for w in words if w not in STOP_WORDS))

    search_queries = []
    if keywords:
        search_queries.append(keywords[0])
        if len(keywords) >= 2: search_queries.append(f"{keywords[0]} {keywords[1]}")
        if len(keywords) >= 4: search_queries.append(f"{keywords[2]} {keywords[3]}")
        if len(keywords) >= 3: search_queries.append(keywords[2])
    else:
        search_queries = [description.strip()[:40]]

    if mode == "profiles":
        return {
            "status": "success",
            "search_queries": search_queries[:4],
            "topic_keywords": keywords[:8],
            "min_followers": 100,
            "persona_type": "general",
            "rationale": "Rule-based extraction. Add OpenAI key in Settings → AI Provider for better results.",
            "source": "rule_based"
        }
    else:
        return {
            "status": "success",
            "topic_keywords": keywords[:10],
            "must_include_any": keywords[:3],
            "rationale": "Rule-based extraction. Add API key in Settings → AI Provider for better results.",
            "source": "rule_based"
        }
