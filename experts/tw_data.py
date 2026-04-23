# =============================================================================
# EXTELLA EXPERT: tw_data
# =============================================================================
# DESCRIPTION: Twitter Lead Agent — Database Layer. Manages the local SQLite database: init (creates 9 tables with WAL mode, indexes, defaults), migrate (schema versioning), backup (timestamped copy), export_schema (DDL to SQL file), get_setting/set_setting (settings CRUD), vacuum (optimize), info (table stats). Saves db_path to KV Store automatically on init. Parameters: action — init/migrate/backup/export_schema/get_setting/set_setting/vacuum/info; setting_key — key for get_setting/set_setting; setting_value — value for set_setting; db_path_key — KV Store key holding the DB path (default tw_db_path)
#
# KWARGS (default parameters):
# {
#   "action": "init",
#   "db_path_key": "tw_db_path",
#   "setting_key": "",
#   "setting_value": ""
# }
#
# HOW TO EDIT & SYNC BACK TO EXTELLA:
#   python sync_to_extella.py tw_data    # sync this file only
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
include("import sqlite3", [])
include("import requests", ["extella-pip install requests"])

def tw_data(
    action: str = "init",
    setting_key: str = "",
    setting_value: str = "",
    db_path_key: str = "tw_db_path"
) -> dict:
    import sqlite3
    import json
    import os
    import shutil
    import requests
    from pathlib import Path
    from datetime import datetime

    print(f"[1/5] 🔄 tw_data: action={action}")

    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    APP_DIR = Path.home() / "Documents" / "twitter_agent"
    APP_DIR.mkdir(parents=True, exist_ok=True)

    # ── KV helpers ──────────────────────────────────────────────
    def kv_get(key):
        try:
            r = requests.post(f"{BASE_URL}/api/kv/get", json={"key": key}, timeout=10)
            if r.status_code == 200:
                return r.json().get("value", "")
        except Exception:
            pass
        return ""

    def kv_set(key, value, desc=""):
        try:
            requests.post(
                f"{BASE_URL}/api/kv/set",
                json={"key": key, "value": value, "description": desc},
                timeout=10
            )
        except Exception:
            pass

    # ── Resolve DB path ─────────────────────────────────────────
    db_path = kv_get(db_path_key) or str(APP_DIR / "data.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"[2/5] 📁 DB: {db_path}")

    # ── Full DDL ─────────────────────────────────────────────────
    SCHEMA_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT DEFAULT (datetime('now')),
    description TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id                  TEXT PRIMARY KEY,
    username            TEXT UNIQUE NOT NULL,
    display_name        TEXT,
    gologin_profile_id  TEXT,
    role_discovery      INTEGER DEFAULT 1,
    role_posting        INTEGER DEFAULT 1,
    is_active           INTEGER DEFAULT 0,
    health_score        INTEGER DEFAULT 100,
    circuit_state       TEXT DEFAULT 'closed',
    quarantine_until    TEXT,
    error_count         INTEGER DEFAULT 0,
    last_used           TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profiles (
    id                 TEXT PRIMARY KEY,
    username           TEXT NOT NULL,
    display_name       TEXT,
    bio                TEXT,
    location           TEXT,
    followers_count    INTEGER DEFAULT 0,
    following_count    INTEGER DEFAULT 0,
    tweet_count        INTEGER DEFAULT 0,
    avg_likes          REAL DEFAULT 0,
    avg_replies        REAL DEFAULT 0,
    posting_frequency  REAL DEFAULT 0,
    engagement_rate    REAL DEFAULT 0,
    topic_tags         TEXT DEFAULT '[]',
    activity_status    TEXT DEFAULT 'unknown',
    persona_type       TEXT,
    tier               INTEGER DEFAULT 3,
    tier_score         REAL DEFAULT 0,
    custom_fields      TEXT DEFAULT '{}',
    discovery_keywords TEXT,
    primary_language   TEXT DEFAULT 'en',
    discovered_at      TEXT DEFAULT (datetime('now')),
    updated_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posts (
    id             TEXT PRIMARY KEY,
    profile_id     TEXT REFERENCES profiles(id) ON DELETE CASCADE,
    text           TEXT,
    url            TEXT,
    posted_at      TEXT,
    likes          INTEGER DEFAULT 0,
    replies_count  INTEGER DEFAULT 0,
    retweets       INTEGER DEFAULT 0,
    views          INTEGER DEFAULT 0,
    lang           TEXT,
    matched_topics TEXT DEFAULT '[]',
    fetched_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reply_tasks (
    id               TEXT PRIMARY KEY,
    post_id          TEXT REFERENCES posts(id) ON DELETE SET NULL,
    profile_id       TEXT REFERENCES profiles(id) ON DELETE SET NULL,
    account_id       TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    generated_reply  TEXT,
    edited_reply     TEXT,
    reply_intent     TEXT,
    language         TEXT DEFAULT 'auto',
    status           TEXT DEFAULT 'pending',
    is_duplicate     INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now')),
    approved_at      TEXT,
    posted_at        TEXT,
    posted_tweet_id  TEXT,
    error_msg        TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id               TEXT PRIMARY KEY,
    reply_task_id    TEXT REFERENCES reply_tasks(id) ON DELETE CASCADE,
    depth            INTEGER DEFAULT 0,
    direction        TEXT NOT NULL,
    tweet_id         TEXT,
    text             TEXT,
    author_username  TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    followup_task_id TEXT REFERENCES reply_tasks(id)
);

CREATE TABLE IF NOT EXISTS analytics (
    id                    TEXT PRIMARY KEY,
    account_id            TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    date                  TEXT NOT NULL,
    replies_sent          INTEGER DEFAULT 0,
    responses_received    INTEGER DEFAULT 0,
    conversations_started INTEGER DEFAULT 0,
    conversations_active  INTEGER DEFAULT 0,
    response_rate         REAL DEFAULT 0,
    UNIQUE(account_id, date)
);

CREATE TABLE IF NOT EXISTS workflow_state (
    id         TEXT PRIMARY KEY DEFAULT 'current',
    phase      TEXT NOT NULL DEFAULT 'idle',
    payload    TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_profiles_tier      ON profiles(tier);
CREATE INDEX IF NOT EXISTS idx_profiles_keywords  ON profiles(discovery_keywords);
CREATE INDEX IF NOT EXISTS idx_posts_profile      ON posts(profile_id);
CREATE INDEX IF NOT EXISTS idx_posts_time         ON posts(posted_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status       ON reply_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_account      ON reply_tasks(account_id);
CREATE INDEX IF NOT EXISTS idx_analytics_acc_date ON analytics(account_id, date);
CREATE INDEX IF NOT EXISTS idx_conv_task          ON conversations(reply_task_id);
"""

    DEFAULT_SETTINGS = [
        ("ai_provider",          "extella"),
        ("reply_language",       "auto"),
        ("monitor_interval_min", "15"),
        ("background_enabled",   "false"),
        ("max_replies_per_day",  "20"),
        ("proxy_mode",           "gologin"),
        ("duplicate_protection", "warn"),
        ("onboarding_complete",  "false"),
        ("last_heartbeat",       ""),
        ("app_version",          "1.0.0"),
    ]

    def get_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # ────────────────────────────────────────────────────────────
    if action == "init":
        conn = get_conn()
        conn.executescript(SCHEMA_DDL)
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            DEFAULT_SETTINGS
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, description) VALUES (1, 'Initial schema v1.0')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO workflow_state (id, phase) VALUES ('current', 'idle')"
        )
        conn.commit()

        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        conn.close()

        # Persist path in KV
        kv_set(db_path_key, db_path, "Twitter Lead Agent — SQLite database path")

        print(f"[3/5] ✅ Schema created — {len(tables)} tables")
        print(f"[4/5] ⚙️  Schema version: {version}")
        print("[5/5] ✅ tw_data init complete")
        return {
            "status": "success",
            "action": "init",
            "db_path": db_path,
            "schema_version": version,
            "tables": tables,
            "tables_count": len(tables),
            "message": f"Database initialized: {len(tables)} tables, schema v{version}"
        }

    elif action == "migrate":
        conn = get_conn()
        cur_ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        MIGRATIONS: dict = {}  # {2: "ALTER TABLE ...", 3: "..."}

        applied = []
        for ver, sql in sorted(MIGRATIONS.items()):
            if ver > cur_ver:
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (ver, f"Migration {ver}")
                )
                applied.append(ver)

        conn.commit()
        new_ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        conn.close()
        print(f"[3/5] 📊 Applied: {len(applied)} migrations")
        print(f"[4/5] 🔢 Schema version: {cur_ver} → {new_ver}")
        print("[5/5] ✅ Migration complete")
        return {"status": "success", "applied": applied, "schema_version": new_ver}

    elif action == "backup":
        backup_dir = Path(db_path).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"tw_backup_{ts}.db"
        shutil.copy2(db_path, backup_path)
        size_mb = round(backup_path.stat().st_size / 1024 / 1024, 2)
        print(f"[3/5] 💾 Backup created: {backup_path.name}")
        print(f"[4/5] 📦 Size: {size_mb} MB")
        print("[5/5] ✅ Backup complete")
        return {"status": "success", "backup_path": str(backup_path), "size_mb": size_mb}

    elif action == "export_schema":
        exports_dir = Path(db_path).parent / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        out = exports_dir / "schema.sql"
        conn = get_conn()
        stmts = [
            r[0] for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type DESC, name"
            ).fetchall()
        ]
        conn.close()
        out.write_text("-- Twitter Lead Agent — Database Schema\n-- Generated: "
                       + datetime.now().isoformat() + "\n\n"
                       + "\n\n".join(s + ";" for s in stmts), encoding="utf-8")
        print(f"[3/5] 📄 Exported {len(stmts)} statements")
        print(f"[4/5] 📁 File: {out}")
        print("[5/5] ✅ Export complete")
        return {"status": "success", "output_path": str(out), "statements": len(stmts)}

    elif action == "get_setting":
        if not setting_key:
            return {"status": "error", "message": "setting_key required"}
        conn = get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (setting_key,)).fetchone()
        conn.close()
        value = row["value"] if row else None
        print(f"[3/5] 📖 {setting_key} = {value}")
        print("[4/5] ✅ Done")
        print("[5/5] ✅ Done")
        return {"status": "success", "key": setting_key, "value": value}

    elif action == "set_setting":
        if not setting_key:
            return {"status": "error", "message": "setting_key required"}
        conn = get_conn()
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (setting_key, setting_value)
        )
        conn.commit()
        conn.close()
        print(f"[3/5] 💾 Saved {setting_key} = {setting_value}")
        print("[4/5] ✅ Done")
        print("[5/5] ✅ Done")
        return {"status": "success", "key": setting_key, "value": setting_value}

    elif action == "vacuum":
        conn = get_conn()
        conn.execute("VACUUM")
        conn.close()
        print("[3/5] 🧹 VACUUM executed")
        print("[4/5] ✅ DB optimized")
        print("[5/5] ✅ Done")
        return {"status": "success", "message": "Database vacuumed"}

    elif action == "info":
        conn = get_conn()
        info = {}
        for table in ["accounts", "profiles", "posts", "reply_tasks",
                      "conversations", "analytics", "workflow_state", "settings"]:
            try:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                info[table] = cnt
            except Exception:
                info[table] = -1
        schema_ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        phase = conn.execute("SELECT phase FROM workflow_state WHERE id='current'").fetchone()
        conn.close()
        db_size_mb = round(Path(db_path).stat().st_size / 1024 / 1024, 3) if Path(db_path).exists() else 0
        print(f"[3/5] 📊 {sum(v for v in info.values() if v >= 0)} total records")
        print(f"[4/5] 🔢 Schema v{schema_ver} | {db_size_mb} MB")
        print("[5/5] ✅ Info retrieved")
        return {
            "status": "success",
            "db_path": db_path,
            "db_size_mb": db_size_mb,
            "schema_version": schema_ver,
            "current_workflow_phase": phase["phase"] if phase else "unknown",
            "row_counts": info
        }

    return {"status": "error", "message": f"Unknown action: '{action}'. Valid: init/migrate/backup/export_schema/get_setting/set_setting/vacuum/info"}
