# =============================================================================
# EXTELLA EXPERT: tw_search_getx
# =============================================================================
# DESCRIPTION: Twitter Outreach — GetXAPI advanced_search with cursor pagination,
# upsert into profiles and posts. Docs: https://docs.getxapi.com/docs
# Parameters: keywords — search query q; max_posts — stop after N tweets stored;
# product — Latest or Top; getx_api_token — optional override; getx_api_key_name —
# KV key for token (default getx_api_token); db_path_key; extella_token_key
# =============================================================================

$extens("include.py")
include("import requests", ["extella-pip install requests"])
include("import sqlite3", [])


def tw_search_getx(
    keywords: str = "",
    max_posts: int = 50,
    product: str = "Latest",
    getx_api_token: str = "",
    getx_api_key_name: str = "getx_api_token",
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token",
) -> dict:
    import json
    import os
    import re
    import sqlite3
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    print("[1/6] 🔄 tw_search_getx: GetX advanced_search")

    BASE_URL_X = os.environ.get("GETX_API_BASE", "https://api.getxapi.com").rstrip("/")
    BASE_EXT = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")

    def kv_get(key: str) -> str:
        try:
            r = requests.post(
                f"{BASE_EXT}/api/kv/get", json={"key": key}, timeout=10
            )
            if r.status_code == 200:
                return r.json().get("value", "") or ""
        except Exception:
            pass
        return ""

    def parse_created_at(raw: str) -> str:
        if not raw:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = raw.strip()
        try:
            if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
                return raw[:19].replace(" ", "T") + "Z" if "T" not in raw[:19] else raw
            dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    token = (getx_api_token or "").strip() or kv_get(getx_api_key_name)
    if not token:
        print("[2/6] ❌ No GetX API token (settings / KV getx_api_token)")
        return {
            "status": "error",
            "message": "Missing GetX API token. Save it in Settings.",
        }

    db_path = kv_get(db_path_key) or str(
        Path.home() / "Documents" / "twitter_agent" / "data.db"
    )
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    q = (keywords or "").strip()
    if not q:
        return {"status": "error", "message": "keywords (q) is required"}

    prod = product if product in ("Latest", "Top") else "Latest"
    cap = max(1, min(int(max_posts or 50), 500))

    print(f"[2/6] 📡 DB={db_path} max_posts={cap} product={prod}")

    headers = {"Authorization": f"Bearer {token}"}
    cursor = None
    pages = 0
    stored_posts = 0
    stored_profiles = set()
    seen_tweet_ids = set()
    api_calls = 0
    last_error = ""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    prof_sql = """
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

    post_sql = """
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

    while stored_posts < cap:
        params = {"q": q, "product": prod}
        if cursor:
            params["cursor"] = cursor

        try:
            r = requests.get(
                f"{BASE_URL_X}/twitter/tweet/advanced_search",
                params=params,
                headers=headers,
                timeout=60,
            )
            api_calls += 1
        except Exception as e:
            last_error = str(e)[:200]
            print(f"[3/6] ❌ HTTP error: {last_error}")
            break

        if r.status_code == 429:
            last_error = "rate_limited"
            print("[3/6] ⏳ 429 — short backoff")
            time.sleep(2.5)
            continue

        if r.status_code != 200:
            try:
                body = r.json()
                last_error = body.get("error", r.text[:120])
            except Exception:
                last_error = r.text[:120] or f"HTTP {r.status_code}"
            print(f"[3/6] ❌ {r.status_code}: {last_error}")
            break

        try:
            data = r.json()
        except Exception:
            last_error = "invalid JSON"
            break

        tweets = data.get("tweets") or []
        has_more = bool(data.get("has_more"))
        next_c = data.get("next_cursor") or data.get("nextCursor")
        pages += 1
        print(f"[3/6] 📄 page {pages} tweets={len(tweets)} has_more={has_more}")

        for tw in tweets:
            if stored_posts >= cap:
                break
            tid = str(tw.get("id") or "")
            if not tid or tid in seen_tweet_ids:
                continue
            seen_tweet_ids.add(tid)

            author = tw.get("author") or {}
            aid = str(author.get("id") or "")
            if not aid:
                continue

            uname = (author.get("userName") or author.get("username") or "").strip()
            if not uname:
                continue

            disp = (author.get("name") or "").strip()
            bio = (author.get("description") or "").strip()
            loc = (author.get("location") or "").strip()
            followers = int(author.get("followers") or 0)
            following = int(author.get("following") or 0)
            twcount = int(author.get("tweets") or author.get("statusesCount") or 0)
            blue = 1 if author.get("isBlueVerified") or author.get("is_blue_verified") else 0

            conn.execute(
                prof_sql,
                (
                    aid,
                    uname,
                    disp,
                    bio,
                    loc,
                    followers,
                    following,
                    twcount,
                    blue,
                ),
            )
            stored_profiles.add(aid)

            text = (tw.get("text") or "").strip()
            url = (tw.get("url") or tw.get("twitterUrl") or "").strip()
            posted_at = parse_created_at(tw.get("createdAt") or "")
            likes = int(tw.get("likeCount") or tw.get("like_count") or 0)
            replies_count = int(tw.get("replyCount") or tw.get("reply_count") or 0)
            retweets = int(tw.get("retweetCount") or tw.get("retweet_count") or 0)
            views = int(tw.get("viewCount") or tw.get("view_count") or 0)
            lang = (tw.get("lang") or "").strip() or None

            conn.execute(
                post_sql,
                (
                    tid,
                    aid,
                    text,
                    url,
                    posted_at,
                    likes,
                    replies_count,
                    retweets,
                    views,
                    lang,
                ),
            )
            stored_posts += 1

        conn.commit()

        if stored_posts >= cap:
            break
        if not tweets:
            break
        if not has_more or not next_c:
            break
        cursor = next_c

    conn.close()

    print(f"[4/6] ✅ posts_saved={stored_posts} profiles_touched={len(stored_profiles)} api_calls={api_calls}")
    print("[5/6] ✅")
    print("[6/6] ✅ tw_search_getx done")

    return {
        "status": "success",
        "posts_saved": stored_posts,
        "profiles_upserted": len(stored_profiles),
        "api_calls": api_calls,
        "pages": pages,
        "query": q,
        "last_error": last_error or None,
    }
