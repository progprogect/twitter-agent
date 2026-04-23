# =============================================================================
# EXTELLA EXPERT: tw_discover
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Profile Discovery v6.3 (page.route() sync interception). Fixed: replaced page.on('response') with page.route() for SearchTimeline — synchronous interception avoids async callback timing issues. GraphQL People Search + user_timeline enrichment. Parameters: user_description (primary); keywords (legacy); limit; min_followers/max_followers; lang; topics; dry_run; flask_port; db_path_key; extella_token_key
#
# KWARGS (default parameters):
# {
#   "db_path_key": "tw_db_path",
#   "dry_run": false,
#   "extella_token_key": "extella_api_token",
#   "flask_port": 7842,
#   "keywords": "",
#   "lang": "en",
#   "limit": 10,
#   "max_followers": 0,
#   "min_followers": 0,
#   "topics": "",
#   "user_description": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_discover    # sync this file only
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

def tw_discover(
    user_description: str = "",
    keywords: str = "",
    limit: int = 10,
    min_followers: int = 0,
    max_followers: int = 0,
    lang: str = "en",
    topics: str = "",
    dry_run: bool = False,
    flask_port: int = 7842,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3, json, os, sys, site, importlib, time, random
    import subprocess, requests, base64
    from pathlib import Path
    from datetime import datetime, timezone
    from urllib.parse import quote

    print(f"[1/8] 🔄 tw_discover v6.3: '{user_description[:40]}' / '{keywords[:30]}'")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    BEARER   = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
                "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

    def setup_path():
        try:
            up = site.getusersitepackages()
            if up not in sys.path: sys.path.insert(0, up)
        except Exception: pass
        importlib.invalidate_caches()

    def ensure_playwright():
        setup_path()
        try:
            from playwright.sync_api import sync_playwright as _s; return True
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "--quiet"],
                           capture_output=True)
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                           capture_output=True)
            setup_path()
            try: from playwright.sync_api import sync_playwright as _s; return True
            except ImportError: return False

    if not ensure_playwright():
        return {"status": "error", "message": "Playwright import failed."}
    from playwright.sync_api import sync_playwright
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)

    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200: return r.json().get("value", "")
        except Exception: pass
        return ""

    def run_expert(name, params):
        token = kv_get(extella_token_key)
        try:
            r = requests.post(f"{BASE_URL}/api/expert/run",
                headers={"X-Auth-Token": token, "Content-Type": "application/json"},
                json={"expert_name": name, "params": params}, timeout=60)
            if r.status_code == 200:
                data = r.json()
                result = data.get("result")
                return result if isinstance(result, dict) else (data if isinstance(data, dict) else {})
        except Exception as e: return {"error": str(e)}
        return {}

    def get_pool_from_flask():
        try:
            r = requests.get(f"http://127.0.0.1:{flask_port}/api/credentials/pool", timeout=10)
            if r.status_code == 200: return r.json().get("pool", [])
        except Exception as e: print(f"[*] Flask: {e}")
        return []

    def safe_int(v, default=0):
        if isinstance(v, (int, float)): return int(v)
        try: return int(v)
        except Exception: return default

    def decode_user_id(b64_id: str) -> str:
        try:
            padded = b64_id + "=" * (4 - len(b64_id) % 4)
            decoded = base64.b64decode(padded).decode("utf-8")
            return decoded.split(":", 1)[1] if ":" in decoded else ""
        except Exception: return ""

    def parse_user_result(ur: dict):
        if not isinstance(ur, dict): return None
        if ur.get("__typename") != "User": return None
        core = ur.get("core", {}) or {}
        screen_name = core.get("screen_name", "") or (ur.get("legacy", {}) or {}).get("screen_name", "")
        if not screen_name: return None
        rest_id = ur.get("rest_id", "") or decode_user_id(ur.get("id", ""))
        if not rest_id: return None
        legacy = ur.get("legacy", {}) or {}
        return {
            "id_str":          rest_id,
            "screen_name":     screen_name,
            "name":            legacy.get("name") or core.get("name", ""),
            "description":     (legacy.get("description") or ""),
            "location":        (legacy.get("location") or ""),
            "followers_count": safe_int(legacy.get("followers_count", 0)),
            "friends_count":   safe_int(legacy.get("friends_count", 0)),
            "statuses_count":  safe_int(legacy.get("statuses_count", 0)),
        }

    def extract_users_from_graphql(data: dict) -> list:
        users = []
        try:
            instructions = (data.get("data", {})
                              .get("search_by_raw_query", {})
                              .get("search_timeline", {})
                              .get("timeline", {})
                              .get("instructions", []))
            for inst in instructions:
                for entry in inst.get("entries", []):
                    content = entry.get("content", {}) or {}
                    for item_wrap in content.get("items", []):
                        ur = (item_wrap.get("item", {})
                              .get("itemContent", {})
                              .get("user_results", {})
                              .get("result", {}))
                        u = parse_user_result(ur)
                        if u: users.append(u)
                    ur = (content.get("itemContent", {})
                          .get("user_results", {})
                          .get("result", {}))
                    u = parse_user_result(ur)
                    if u: users.append(u)
        except Exception as e:
            print(f"[*] GraphQL parse error: {e}")
        return users

    db_path = kv_get(db_path_key) or str(Path.home() / "Documents" / "twitter_agent" / "data.db")

    def get_conn():
        c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON"); return c

    def upsert_profile(conn, p):
        conn.execute("""
            INSERT INTO profiles
              (id, username, display_name, bio, location, followers_count, following_count,
               tweet_count, avg_likes, avg_replies, posting_frequency, engagement_rate,
               topic_tags, activity_status, persona_type, tier, tier_score,
               discovery_keywords, primary_language, discovered_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
              followers_count=excluded.followers_count, avg_likes=excluded.avg_likes,
              posting_frequency=excluded.posting_frequency, engagement_rate=excluded.engagement_rate,
              bio=excluded.bio, tweet_count=excluded.tweet_count,
              tier=excluded.tier, tier_score=excluded.tier_score, updated_at=datetime('now')
        """, (p["id"], p["username"], p["display_name"], p["bio"], p["location"],
              p["followers_count"], p["following_count"], p["tweet_count"],
              p["avg_likes"], p["avg_replies"], p["posting_frequency"], p["engagement_rate"],
              json.dumps(p["topic_tags"]), p["activity_status"], p["persona_type"],
              p["tier"], p["tier_score"], p["discovery_keywords"], p["primary_language"]))

    FETCH_JS = """
        async ({url, params, bearer, ct0}) => {
            const u = new URL(url);
            Object.entries(params).forEach(([k,v]) => u.searchParams.set(k,String(v)));
            try {
                const resp = await fetch(u.toString(), {
                    method:'GET', credentials:'include',
                    headers:{
                        'authorization':'Bearer '+bearer,'x-csrf-token':ct0,
                        'x-twitter-active-user':'yes','x-twitter-client-language':'en',
                        'x-twitter-auth-type':'OAuth2Session','content-type':'application/json',
                        'accept':'*/*','referer':'https://x.com/home'
                    }
                });
                const text = await resp.text();
                let body = null;
                try { body = JSON.parse(text); } catch(e) { body = text; }
                return {status:resp.status, ok:resp.ok, body:body};
            } catch(e) { return {status:0,ok:false,error:e.message,body:null}; }
        }
    """

    def tw_delay(): time.sleep(random.uniform(1.0, 2.0))

    # ── STEP 1: Resolve queries ───────────────────────────────────
    if user_description:
        print(f"[2/8] 🤖 Expanding description...")
        expand = run_expert("tw_query_expand", {
            "user_description": user_description,
            "mode": "profiles",
            "flask_port": flask_port,
            "extella_token_key": extella_token_key
        })
        search_queries = expand.get("search_queries", [user_description[:40]])
        topic_keywords = expand.get("topic_keywords", [])
        min_f = max(min_followers, safe_int(expand.get("min_followers", 0)))
        ai_source = expand.get("source", "rule_based")
        discovery_label = user_description[:80]
        print(f"[2/8] ✅ {ai_source}: {search_queries[:3]}")
    else:
        search_queries = [k.strip() for k in keywords.split(",") if k.strip()] or ["startup"]
        topic_keywords = [t.strip().lower() for t in topics.split(",") if t.strip()]
        min_f = min_followers
        ai_source = "legacy"
        discovery_label = keywords[:80]

    # ── STEP 2: Credentials ───────────────────────────────────────
    print("[3/8] 🔐 Getting credentials...")
    pool = get_pool_from_flask()
    if not pool:
        return {"status": "error",
                "message": "No active accounts. Re-link in Settings → Twitter Accounts."}
    account    = pool[0]
    auth_token = account["auth_token"]
    ct0        = account["ct0"]
    print(f"[3/8] ✅ @{account['username']}")

    all_candidates = []
    seen_ids = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            extra_http_headers={"accept-language": "en-US,en;q=0.9"})
        for domain in [".x.com", ".twitter.com"]:
            ctx.add_cookies([
                {"name": "auth_token", "value": auth_token, "domain": domain, "path": "/"},
                {"name": "ct0",        "value": ct0,        "domain": domain, "path": "/"},
            ])
        page = ctx.new_page()
        try: page.goto("https://x.com/", wait_until="commit", timeout=20000)
        except Exception: pass

        # ── STEP 3: GraphQL People Search via page.route() ───────
        # KEY FIX v6.3: Use page.route() instead of page.on("response").
        # Route handlers are called SYNCHRONOUSLY during page.goto() —
        # no async callback timing issues.
        print(f"[4/8] 🔍 GraphQL People Search: {search_queries[:3]}")

        def route_handler(route):
            """Intercept SearchTimeline requests synchronously."""
            url = route.request.url
            if "SearchTimeline" in url:
                try:
                    # Fetch the response through the route
                    response = route.fetch()
                    if response.ok:
                        data = response.json()
                        users = extract_users_from_graphql(data)
                        if users:
                            all_candidates.extend(users)
                            print(f"[4/8]   ✅ GraphQL intercepted: +{len(users)} users "
                                  f"(total={len(all_candidates)})")
                        else:
                            print(f"[4/8]   ⚠️ GraphQL response had 0 users "
                                  f"(data keys: {list(data.keys())})")
                    else:
                        print(f"[4/8]   ⚠️ SearchTimeline HTTP {response.status}")
                    route.fulfill(response=response)
                except Exception as e:
                    print(f"[4/8]   ❌ Route error: {str(e)[:80]}")
                    route.continue_()
            else:
                route.continue_()

        # Register route ONCE for all searches
        page.route("**/**", route_handler)

        for q in search_queries[:3]:
            if not q: continue
            print(f"[4/8]   → Searching: '{q}'")
            try:
                search_url = f"https://x.com/search?q={quote(q)}&src=typed_query&f=people"
                page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"[4/8]   ⚠️ Nav: {str(e)[:50]}")
            tw_delay()
            if len(all_candidates) >= limit * 2: break

        # Unregister route
        try: page.unroute("**/**", route_handler)
        except Exception: pass

        print(f"[4/8] ✅ Total candidates: {len(all_candidates)}")

        # ── STEP 4: Filter & enrich ───────────────────────────────
        print(f"[5/8] 📊 Filtering + enriching...")
        enriched = []

        for u in all_candidates:
            if len(enriched) >= limit: break

            uid = u.get("id_str", "")
            sn  = u.get("screen_name", "")
            fc  = safe_int(u.get("followers_count", 0))

            if not uid or uid in seen_ids: continue
            if min_f > 0 and fc < min_f: continue
            if max_followers > 0 and fc > max_followers: continue

            seen_ids.add(uid)
            print(f"[5/8]   [{len(enriched)+1}] @{sn} ({fc:,} f)...")
            tw_delay()

            # user_timeline?user_id= — confirmed working
            tres = page.evaluate(FETCH_JS, {
                "url": "https://x.com/i/api/1.1/statuses/user_timeline.json",
                "params": {"user_id": uid, "count": "20",
                           "include_rts": "false", "exclude_replies": "false",
                           "tweet_mode": "extended"},
                "bearer": BEARER, "ct0": ct0
            })

            tweets = []
            if tres.get("ok"):
                body = tres.get("body", [])
                if isinstance(body, list): tweets = body

            n = len(tweets)
            avg_likes    = round(sum(safe_int(t.get("favorite_count", 0)) for t in tweets)/n, 2) if n else 0
            avg_replies  = round(sum(safe_int(t.get("reply_count",   0)) for t in tweets)/n, 2) if n else 0
            posting_freq = 0.0
            if n >= 2:
                try:
                    fmt = "%a %b %d %H:%M:%S +0000 %Y"
                    newest = datetime.strptime(tweets[0]["created_at"], fmt).replace(tzinfo=timezone.utc)
                    oldest = datetime.strptime(tweets[-1]["created_at"], fmt).replace(tzinfo=timezone.utc)
                    days = max((newest - oldest).days, 1)
                    posting_freq = round(n / days, 2)
                except Exception: pass

            followers = max(fc, 1)
            engagement_rate = round((avg_likes + avg_replies) / followers * 100, 4)
            activity_status = ("active" if posting_freq >= 1.0
                               else "moderate" if posting_freq >= 0.14 else "inactive")
            sc = safe_int(u.get("statuses_count", 0))
            persona_type = ("influencer" if fc >= 100000 else "practitioner" if fc >= 5000
                            else "power_user" if sc > 5000 else "general")
            combined = (u.get("description") or "").lower() + " " + \
                       " ".join((t.get("full_text") or t.get("text") or "") for t in tweets[:5]).lower()
            matched_topics = ([t for t in topic_keywords if t.lower() in combined]
                              if topic_keywords else [])

            print(f"[5/8]   ✅ @{sn}: {fc:,}f | ❤️{avg_likes:.1f} | {posting_freq:.1f}/d | {n}t")

            enriched.append({
                "id":              uid,
                "username":        sn,
                "display_name":    u.get("name", ""),
                "bio":             (u.get("description") or "")[:500],
                "location":        u.get("location", ""),
                "followers_count": fc,
                "following_count": safe_int(u.get("friends_count", 0)),
                "tweet_count":     sc,
                "avg_likes":       avg_likes,
                "avg_replies":     avg_replies,
                "posting_frequency": posting_freq,
                "engagement_rate": engagement_rate,
                "topic_tags":      matched_topics,
                "activity_status": activity_status,
                "persona_type":    persona_type,
                "primary_language": lang,
                "discovery_keywords": discovery_label,
                "tier":       3,
                "tier_score": 0.0
            })

        browser.close()

    run_expert("tw_auth", {"action": "update_health",
                           "account_id": account["account_id"], "health_delta": 5})

    print(f"[6/8] ✅ {len(enriched)} profiles enriched")
    if not enriched:
        return {
            "status": "success", "profiles_found": 0,
            "message": ("No profiles found. Possible reasons: "
                        "new/restricted account (Twitter limits People search for new accounts), "
                        "or try different keywords.")
        }

    # ── STEP 5: Score & tier ──────────────────────────────────────
    print(f"[7/8] 🏆 Scoring...")
    tier_breakdown = {1: 0, 2: 0, 3: 0}

    def safe_norm(vals):
        mn, mx = min(vals), max(vals)
        return [(v-mn)/(mx-mn) if mx!=mn else 0.5 for v in vals]

    fn = safe_norm([p["followers_count"]   for p in enriched])
    en = safe_norm([p["engagement_rate"]   for p in enriched])
    fr = safe_norm([p["posting_frequency"] for p in enriched])
    ln = safe_norm([p["avg_likes"]         for p in enriched])
    for i, p in enumerate(enriched):
        p["tier_score"] = round(0.35*en[i]+0.30*fn[i]+0.20*fr[i]+0.15*ln[i], 4)

    enriched.sort(key=lambda x: x["tier_score"], reverse=True)
    nv = len(enriched)
    t1, t2 = max(1, int(nv*0.20)), max(2, int(nv*0.50))
    for i, p in enumerate(enriched):
        p["tier"] = 1 if i < t1 else 2 if i < t2 else 3

    for p in enriched: tier_breakdown[p["tier"]] = tier_breakdown.get(p["tier"], 0) + 1
    print(f"[7/8] ✅ T1={tier_breakdown[1]} T2={tier_breakdown[2]} T3={tier_breakdown[3]}")

    if dry_run:
        print("[8/8] 🔍 Dry run complete")
        return {
            "status": "success", "dry_run": True, "profiles_found": len(enriched),
            "tier_breakdown": tier_breakdown, "ai_source": ai_source,
            "search_queries_used": search_queries, "topic_keywords": topic_keywords,
            "preview": [{"username": p["username"], "followers": p["followers_count"],
                         "tier": p["tier"], "engagement_rate": p["engagement_rate"]}
                        for p in enriched[:5]]
        }

    print(f"[8/8] 💾 Saving {len(enriched)} profiles...")
    conn = get_conn()
    for p in enriched: upsert_profile(conn, p)
    conn.commit(); conn.close()
    print("[8/8] ✅ tw_discover v6.3 complete!")
    return {
        "status": "success",
        "profiles_found":      len(enriched),
        "profiles_saved":      len(enriched),
        "tier_breakdown":      tier_breakdown,
        "account_used":        account["username"],
        "ai_source":           ai_source,
        "search_queries_used": search_queries,
        "topic_keywords":      topic_keywords,
        "method":              "graphql_route_intercept_v6.3",
        "profiles": [{"username": p["username"], "followers": p["followers_count"],
                      "tier": p["tier"], "engagement_rate": p["engagement_rate"]}
                     for p in enriched]
    }
