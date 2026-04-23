# Twitter Lead Agent — Local Files

## Структура

```
twitter_agent/
├── server.py              <- Flask backend
├── data.db                <- SQLite: профили, посты, задачи
├── ui/index.html          <- SPA (http://127.0.0.1:7842)
├── .api_token             <- Extella API токен
├── sync_to_extella.py     <- ГЛАВНОЕ: синхронизация в Extella
├── EXPERT_RULES.md        <- Правила написания экспертов
└── experts/               <- Код экспертов
    ├── _metadata.json
    ├── tw_agent.py
    ├── tw_auth.py
    ├── tw_discover.py
    ├── tw_generate.py
    ├── tw_posts.py
    ├── tw_query_expand.py
    ├── tw_server.py
    └── ...
```

## Workflow с Cursor

```bash
# Редактируй experts/tw_discover.py
# Затем синхронизируй:
python sync_to_extella.py tw_discover
# Все эксперты:
python sync_to_extella.py --all
# Список:
python sync_to_extella.py --list
```

## Важно

- Код функции начинается ПОСЛЕ последней строки `# ===...`
- `$extens("include.py")` — первая строка кода, не трогать
- Зависимости через `include(...)`, НЕ через `pip install`
- Полные правила -> EXPERT_RULES.md

## Статус экспорта

- OK: tw_agent
- OK: tw_auth
- OK: tw_data
- OK: tw_debug
- OK: tw_discover
- OK: tw_generate
- OK: tw_monitor
- OK: tw_post
- OK: tw_posts
- OK: tw_queue
- OK: tw_query_expand
- OK: tw_server
- OK: tw_test_suite
- OK: tw_fix_playwright_browser
- OK: tw_install_playwright
- OK: tw_discover_debug
- OK: tw_search_debug
- OK: tw_api_probe
- OK: tw_probe_lookup
- OK: tw_test_browser_http
- OK: tw_env_debug
- OK: tw_extella_ai_probe
- OK: tw_patch_v9
- OK: tw_patch_validate
- OK: tw_patch_validate_endpoint
- OK: tw_ui_v2_patch
- OK: tw_project_tracker

