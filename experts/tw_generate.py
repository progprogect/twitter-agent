# =============================================================================
# EXTELLA EXPERT: tw_generate
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — AI Reply Generator. Generates contextual Twitter replies using multi-provider AI with fallback chain. Two modes: single (one reply per post using profile context + reply intent) and followup (continue conversation with history). Auto-detects post language or enforces English. Providers: extella (Extella native AI), openai (GPT-4o), anthropic (Claude), groq (free). Parameters: mode — single/followup; post_id — tweet ID from posts table; profile_id — profile ID for context; reply_intent — tone and goal description; conversation_history — JSON array of {role, content} for followup mode; language — auto/en; ai_provider — extella/openai/anthropic/groq; ai_key_name — KV key for chosen provider API key; dry_run — return prompt without calling AI; db_path_key — KV key for SQLite; extella_token_key — KV key for Extella API token
#
# KWARGS (default parameters):
# {
#   "ai_key_name": "",
#   "ai_provider": "extella",
#   "conversation_history": "[]",
#   "db_path_key": "tw_db_path",
#   "dry_run": false,
#   "extella_token_key": "extella_api_token",
#   "language": "auto",
#   "mode": "single",
#   "post_id": "",
#   "profile_id": "",
#   "reply_intent": "helpful and curious, add value to conversation"
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_generate    # sync this file only
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
include("import sqlite3", [])

def tw_generate(
    mode: str = "single",
    post_id: str = "",
    profile_id: str = "",
    reply_intent: str = "helpful and curious, add value to conversation",
    conversation_history: str = "[]",
    language: str = "auto",
    ai_provider: str = "extella",
    ai_key_name: str = "",
    dry_run: bool = False,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3
    import json
    import os
    import requests
    from pathlib import Path

    print(f"[1/5] 🔄 tw_generate: mode={mode}, provider={ai_provider}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")

    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200:
                return r.json().get("value", "")
        except Exception:
            pass
        return ""

    def run_expert(name, params):
        token = kv_get(extella_token_key)
        try:
            r = requests.post(f"{BASE_URL}/api/expert/run",
                headers={"X-Auth-Token": token, "Content-Type": "application/json"},
                json={"expert_name": name, "params": params}, timeout=120)
            if r.status_code == 200:
                return r.json().get("result", {})
        except Exception as e:
            return {"error": str(e)}
        return {}

    db_path = kv_get(db_path_key) or str(Path.home() / "Documents" / "twitter_agent" / "data.db")

    def get_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    # ── Load context from DB ─────────────────────────────────────
    print("[2/5] 📖 Loading post and profile context...")
    post_text, post_url, post_lang = "", "", "en"
    profile_username, profile_bio, profile_followers = "", "", 0
    profile_persona, profile_topics, profile_tier = "general", "[]", 3

    conn = get_conn()
    if post_id:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row:
            post_text = row["text"] or ""
            post_url = row["url"] or ""
            post_lang = row["lang"] or "en"
            if not profile_id:
                profile_id = row["profile_id"] or ""

    if profile_id:
        row = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if row:
            profile_username = row["username"] or ""
            profile_bio = row["bio"] or ""
            profile_followers = row["followers_count"] or 0
            profile_persona = row["persona_type"] or "general"
            profile_topics = row["topic_tags"] or "[]"
            profile_tier = row["tier"] or 3
    conn.close()

    if not post_text and mode == "single":
        return {"status": "error", "message": f"Post not found: post_id={post_id}. Run tw_posts first."}

    # ── Language detection ───────────────────────────────────────
    reply_lang = "English"
    if language == "auto" and post_lang and post_lang != "und":
        lang_map = {"en": "English", "ru": "Russian", "es": "Spanish", "de": "German",
                    "fr": "French", "pt": "Portuguese", "it": "Italian", "ja": "Japanese",
                    "ko": "Korean", "zh": "Chinese", "ar": "Arabic"}
        reply_lang = lang_map.get(post_lang, "English")
    elif language != "auto":
        reply_lang = "English"

    # ── Build prompts ────────────────────────────────────────────
    topics_str = ", ".join(json.loads(profile_topics)) if profile_topics else "general topics"

    if mode == "single":
        system_prompt = f"""You are a thoughtful Twitter user engaging with {profile_persona} accounts.
Your goal: {reply_intent}

Profile context:
- Username: @{profile_username}
- Bio: {profile_bio[:200] if profile_bio else 'No bio'}
- Followers: {profile_followers:,}
- Topics: {topics_str}
- Tier: {profile_tier} (1=highest priority)

RULES:
- Maximum 280 characters (this is CRITICAL)
- Write ONLY the reply text, no quotes, no alternatives
- Natural conversational tone
- No hashtags unless very relevant
- No marketing language or spam patterns
- Reply in {reply_lang}
- Be genuinely helpful or insightful, not generic"""

        user_prompt = f"Reply to this tweet: \"{post_text[:500]}\""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

    else:  # followup mode
        try:
            history = json.loads(conversation_history) if conversation_history else []
        except Exception:
            history = []

        system_prompt = f"""You are continuing a Twitter conversation with @{profile_username}.
Goal: {reply_intent}
Keep replies under 280 characters. Natural tone. Reply in {reply_lang}.
Be contextually relevant to the conversation history."""

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        if post_text:
            messages.append({"role": "user", "content": f"Their latest message: \"{post_text[:300]}\""})

    print(f"[2/5] ✅ Context loaded: @{profile_username or 'unknown'}, lang={reply_lang}")

    if dry_run:
        print("[3/5] 🔍 Dry run — returning prompt only")
        print("[4/5] ✅")
        print("[5/5] ✅")
        return {
            "status": "success",
            "dry_run": True,
            "prompt_preview": messages[-1]["content"] if messages else "",
            "system_preview": system_prompt[:300]
        }

    # ── AI Provider with fallback chain ──────────────────────────
    print(f"[3/5] 🤖 Calling AI provider: {ai_provider}...")

    generated_reply = None
    provider_used = None
    tokens_used = 0
    error_chain = []

    # Determine provider fallback order
    providers = [ai_provider]
    fallback_order = ["extella", "openai", "anthropic", "groq"]
    for fb in fallback_order:
        if fb not in providers:
            providers.append(fb)

    for provider in providers:
        try:
            if provider == "extella":
                # Use Extella's native openai_chat expert
                result = run_expert("openai_chat", {
                    "prompt": messages[-1]["content"],
                    "system_prompt": system_prompt,
                    "model": "gpt-4o-mini",
                    "temperature": 0.7,
                    "max_tokens": 120
                })
                if result and not result.get("error") and result.get("response"):
                    resp = result["response"]
                    # openai_chat returns response directly or in choices
                    if isinstance(resp, dict):
                        choices = resp.get("choices", [])
                        if choices:
                            generated_reply = choices[0].get("message", {}).get("content", "").strip()
                        else:
                            generated_reply = str(resp)
                    else:
                        generated_reply = str(resp).strip()
                    if generated_reply:
                        provider_used = "extella"
                        break
                else:
                    error_chain.append(f"extella: {result.get('error', 'no response')}")

            elif provider == "openai":
                key_name = ai_key_name or "openai_api_key"
                api_key = kv_get(key_name)
                if not api_key:
                    error_chain.append("openai: no API key in KV Store")
                    continue
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini", "messages": messages, "max_tokens": 120, "temperature": 0.7},
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    generated_reply = data["choices"][0]["message"]["content"].strip()
                    tokens_used = data.get("usage", {}).get("total_tokens", 0)
                    provider_used = "openai"
                    break
                else:
                    error_chain.append(f"openai: HTTP {resp.status_code}")

            elif provider == "anthropic":
                key_name = ai_key_name or "anthropic_api_key"
                api_key = kv_get(key_name)
                if not api_key:
                    error_chain.append("anthropic: no API key in KV Store")
                    continue
                claude_msgs = [m for m in messages if m["role"] != "system"]
                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                             "Content-Type": "application/json"},
                    json={"model": "claude-3-haiku-20240307", "max_tokens": 120,
                          "system": system_prompt, "messages": claude_msgs},
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    generated_reply = data["content"][0]["text"].strip()
                    provider_used = "anthropic"
                    break
                else:
                    error_chain.append(f"anthropic: HTTP {resp.status_code}")

            elif provider == "groq":
                key_name = ai_key_name or "groq_api_key"
                api_key = kv_get(key_name)
                if not api_key:
                    error_chain.append("groq: no API key in KV Store")
                    continue
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": "llama-3.1-8b-instant", "messages": messages, "max_tokens": 120},
                    timeout=20
                )
                if resp.status_code == 200:
                    data = resp.json()
                    generated_reply = data["choices"][0]["message"]["content"].strip()
                    provider_used = "groq"
                    break
                else:
                    error_chain.append(f"groq: HTTP {resp.status_code}")

        except Exception as e:
            error_chain.append(f"{provider}: {str(e)[:100]}")
            continue

    if not generated_reply:
        return {
            "status": "error",
            "message": "All AI providers failed. Check your API keys in Settings.",
            "provider_errors": error_chain
        }

    # Truncate to 280 chars if needed
    if len(generated_reply) > 280:
        generated_reply = generated_reply[:277] + "..."

    print(f"[4/5] ✅ Reply generated via {provider_used} ({len(generated_reply)} chars)")
    print("[5/5] ✅ Done")

    return {
        "status": "success",
        "generated_reply": generated_reply,
        "provider_used": provider_used,
        "char_count": len(generated_reply),
        "tokens_used": tokens_used,
        "reply_language": reply_lang,
        "post_id": post_id,
        "profile_id": profile_id
    }
