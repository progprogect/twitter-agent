# =============================================================================
# EXTELLA EXPERT: tw_queue
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Task Queue Manager. Manages the reply task lifecycle with strict FSM (Finite State Machine) transitions: pending→approved/rejected, approved→posting→posted/failed, failed→pending (retry). Supports batch creation from tw_generate results, individual and bulk approve/reject, edit reply text, duplicate detection across all accounts, paginated listing. Parameters: action — create_batch/list/approve/reject/edit/get_stats/bulk_approve/bulk_reject/retry_failed; task_ids — comma-separated UUIDs; edited_reply — new reply text for edit action; account_id — account for create_batch; post_ids — comma-separated post IDs for create_batch; reply_intent — intent for batch creation; status_filter — pending/approved/rejected/posting/posted/failed; page/page_size — pagination; db_path_key — KV key for SQLite; extella_token_key — KV key for Extella API token
#
# KWARGS (default parameters):
# {
#   "account_id": "",
#   "action": "get_stats",
#   "db_path_key": "tw_db_path",
#   "edited_reply": "",
#   "extella_token_key": "extella_api_token",
#   "page": 1,
#   "page_size": 20,
#   "post_ids": "",
#   "reply_intent": "helpful and curious",
#   "status_filter": "pending",
#   "task_ids": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_queue    # sync this file only
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

def tw_queue(
    action: str = "list",
    task_ids: str = "",
    edited_reply: str = "",
    account_id: str = "",
    post_ids: str = "",
    reply_intent: str = "helpful and curious",
    status_filter: str = "pending",
    page: int = 1,
    page_size: int = 20,
    db_path_key: str = "tw_db_path",
    extella_token_key: str = "extella_api_token"
) -> dict:
    import sqlite3
    import json
    import uuid
    import os
    import requests
    from pathlib import Path
    from datetime import datetime

    print(f"[1/4] 🔄 tw_queue: action={action}")

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
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # ── Allowed FSM transitions ─────────────────────────────────
    ALLOWED_TRANSITIONS = {
        "pending":  ["approved", "rejected"],
        "approved": ["posting", "rejected"],
        "posting":  ["posted", "failed"],
        "posted":   [],
        "rejected": [],
        "failed":   ["pending"],   # retry
    }

    def transition_ok(current: str, target: str) -> bool:
        return target in ALLOWED_TRANSITIONS.get(current, [])

    # ════════════════════════════════════════════════════════════
    if action == "create_batch":
        """Generate replies for a list of post_ids and create pending tasks."""
        if not post_ids:
            return {"status": "error", "message": "post_ids required for create_batch"}

        ids = [p.strip() for p in post_ids.split(",") if p.strip()]
        if not ids:
            return {"status": "error", "message": "No post_ids provided"}

        conn = get_conn()
        created, skipped_dup, failed_gen = [], [], []

        for post_id in ids:
            # Duplicate check: any task already exists for this post?
            existing = conn.execute(
                "SELECT id, status FROM reply_tasks WHERE post_id=?", (post_id,)
            ).fetchone()
            is_dup = existing is not None

            # Get profile_id for the post
            post_row = conn.execute("SELECT profile_id FROM posts WHERE id=?", (post_id,)).fetchone()
            if not post_row:
                failed_gen.append({"post_id": post_id, "reason": "post not found"})
                continue

            # Generate reply via tw_generate
            gen_result = run_expert("tw_generate", {
                "mode": "single",
                "post_id": post_id,
                "profile_id": post_row["profile_id"],
                "reply_intent": reply_intent,
                "db_path_key": db_path_key,
                "extella_token_key": extella_token_key
            })

            if gen_result.get("status") != "success" or not gen_result.get("generated_reply"):
                failed_gen.append({"post_id": post_id, "reason": gen_result.get("message", "generation failed")})
                continue

            task_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO reply_tasks
                  (id, post_id, profile_id, account_id, generated_reply,
                   reply_intent, status, is_duplicate, created_at)
                VALUES (?,?,?,?,?,?,'pending',?,datetime('now'))
            """, (
                task_id, post_id, post_row["profile_id"], account_id or None,
                gen_result["generated_reply"], reply_intent, 1 if is_dup else 0
            ))
            created.append({
                "task_id": task_id,
                "post_id": post_id,
                "is_duplicate": is_dup,
                "reply": gen_result["generated_reply"][:80] + "..."
            })
            if is_dup:
                skipped_dup.append(post_id)

        conn.commit()
        conn.close()

        print(f"[2/4] ✅ Created {len(created)} tasks | Failed: {len(failed_gen)}")
        print(f"[3/4] ⚠️  {len(skipped_dup)} marked as duplicate (but still created)")
        print("[4/4] ✅ Done")
        return {
            "status": "success",
            "tasks_created": len(created),
            "tasks_failed": len(failed_gen),
            "duplicate_warnings": len(skipped_dup),
            "tasks": created,
            "failures": failed_gen
        }

    elif action == "list":
        conn = get_conn()
        offset = (max(1, page) - 1) * page_size
        where_parts, params = [], []
        if status_filter:
            where_parts.append("rt.status=?")
            params.append(status_filter)
        where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
        total = conn.execute(f"SELECT COUNT(*) FROM reply_tasks rt {where}", params).fetchone()[0]
        rows = conn.execute(f"""
            SELECT rt.*,
                   COALESCE(rt.edited_reply, rt.generated_reply) as final_reply,
                   p.text as post_text, p.url as post_url,
                   pr.username as profile_username,
                   a.username as account_username
            FROM reply_tasks rt
            LEFT JOIN posts p ON rt.post_id=p.id
            LEFT JOIN profiles pr ON rt.profile_id=pr.id
            LEFT JOIN accounts a ON rt.account_id=a.id
            {where}
            ORDER BY rt.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [page_size, offset]).fetchall()
        conn.close()
        tasks = [dict(r) for r in rows]
        print(f"[2/4] 📋 {len(tasks)} tasks (total={total})")
        print("[3/4] ✅")
        print("[4/4] ✅")
        return {"status": "success", "tasks": tasks, "total": total, "page": page, "page_size": page_size}

    elif action in ("approve", "reject"):
        ids = [t.strip() for t in task_ids.split(",") if t.strip()]
        if not ids:
            return {"status": "error", "message": "task_ids required"}

        target_status = "approved" if action == "approve" else "rejected"
        conn = get_conn()
        success, blocked = [], []

        for tid in ids:
            row = conn.execute("SELECT status FROM reply_tasks WHERE id=?", (tid,)).fetchone()
            if not row:
                blocked.append({"id": tid, "reason": "not found"})
                continue
            if not transition_ok(row["status"], target_status):
                blocked.append({"id": tid, "reason": f"FSM: {row['status']} → {target_status} not allowed"})
                continue
            ts_col = "approved_at" if target_status == "approved" else ""
            if ts_col:
                conn.execute(f"UPDATE reply_tasks SET status=?, {ts_col}=datetime('now') WHERE id=?",
                             (target_status, tid))
            else:
                conn.execute("UPDATE reply_tasks SET status=? WHERE id=?", (target_status, tid))
            success.append(tid)

        conn.commit()
        conn.close()
        icon = "✅" if action == "approve" else "❌"
        print(f"[2/4] {icon} {action}: {len(success)} ok | {len(blocked)} blocked")
        print("[3/4] 💾 DB updated")
        print("[4/4] ✅ Done")
        return {
            "status": "success",
            "action": action,
            "success_count": len(success),
            "blocked_count": len(blocked),
            "success_ids": success,
            "blocked": blocked
        }

    elif action == "edit":
        ids = [t.strip() for t in task_ids.split(",") if t.strip()]
        if not ids:
            return {"status": "error", "message": "task_ids required"}
        if not edited_reply:
            return {"status": "error", "message": "edited_reply required for edit action"}
        if len(edited_reply) > 280:
            return {"status": "error", "message": f"Reply too long: {len(edited_reply)} chars (max 280)"}

        conn = get_conn()
        updated = []
        for tid in ids:
            row = conn.execute("SELECT status FROM reply_tasks WHERE id=?", (tid,)).fetchone()
            if row and row["status"] in ("pending", "approved"):
                conn.execute("UPDATE reply_tasks SET edited_reply=? WHERE id=?", (edited_reply, tid))
                updated.append(tid)
        conn.commit()
        conn.close()
        print(f"[2/4] ✏️  Edited {len(updated)} tasks")
        print("[3/4] 💾 Saved")
        print("[4/4] ✅ Done")
        return {"status": "success", "updated": len(updated), "char_count": len(edited_reply)}

    elif action in ("bulk_approve", "bulk_reject"):
        target = "approved" if "approve" in action else "rejected"
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, status FROM reply_tasks WHERE status='pending' LIMIT 500"
        ).fetchall()
        success, blocked = [], []
        for r in rows:
            if transition_ok(r["status"], target):
                col = "approved_at=datetime('now')," if target == "approved" else ""
                conn.execute(
                    f"UPDATE reply_tasks SET status=?, {col} WHERE id=?".replace(",WHERE", " WHERE"),
                    (target, r["id"])
                )
                success.append(r["id"])
            else:
                blocked.append(r["id"])
        conn.commit()
        conn.close()
        print(f"[2/4] ✅ Bulk {action}: {len(success)} ok")
        print("[3/4] 💾 Done")
        print("[4/4] ✅")
        return {"status": "success", "action": action, "success": len(success), "blocked": len(blocked)}

    elif action == "retry_failed":
        conn = get_conn()
        conn.execute("UPDATE reply_tasks SET status='pending', error_msg=NULL WHERE status='failed'")
        count = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        conn.close()
        print(f"[2/4] 🔄 Reset {count} failed tasks to pending")
        print("[3/4] ✅")
        print("[4/4] ✅")
        return {"status": "success", "reset_count": count}

    elif action == "get_stats":
        conn = get_conn()
        breakdown = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM reply_tasks GROUP BY status"
        ).fetchall()}
        total = sum(breakdown.values())
        dup_count = conn.execute("SELECT COUNT(*) FROM reply_tasks WHERE is_duplicate=1").fetchone()[0]
        conn.close()
        print(f"[2/4] 📊 Total: {total} | Breakdown: {breakdown}")
        print("[3/4] ✅")
        print("[4/4] ✅")
        return {"status": "success", "breakdown": breakdown, "total": total, "duplicates_warned": dup_count}

    return {"status": "error", "message": f"Unknown action: '{action}'. Valid: create_batch/list/approve/reject/edit/bulk_approve/bulk_reject/retry_failed/get_stats"}
