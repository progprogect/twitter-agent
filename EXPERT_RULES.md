# Правила написания экспертов Extella

## Обязательная структура

```python
import subprocess

import sys

import os



def load_module(module):

    try:

        if 'import' not in module:

            exec(f'import {module}', globals())

        else:

            exec(module, globals())

        return True

    except:

        return False



def include(module, commands, pip=None):

    if pip is None:

        pip = '/usr/local/bin/extella-pip'

    

    if load_module(module) == False:

        for command in commands:

            # Replace extella-pip with full path

            task = command.replace('extella-pip', '/usr/local/bin/extella-pip')

            task = task.replace('$pip', pip)

            

            # Специальная обработка для rdkit-pypi

            if 'rdkit-pypi' in command:

                rdkit_commands = [

                    f"{pip} install rdkit",

                    f"{pip} install rdkit-pypi --index-url https://pypi.org/simple/",

                ]

                

                rdkit_installed = False

                for rdkit_cmd in rdkit_commands:

                    try:

                        args = list(filter(None, rdkit_cmd.split()))

                        print(f"Пытаемся установить RDKit: {args}")

                        subprocess.run(args, check=True)

                        rdkit_installed = True

                        break

                    except subprocess.CalledProcessError as e:

                        print(f"Ошибка установки RDKit командой {rdkit_cmd}: {e}")

                        continue

                    except FileNotFoundError:

                        continue

                

                if not rdkit_installed:

                    print("Не удалось установить RDKit ни одним из способов")

                    return False

            else:

                # Стандартная обработка для других пакетов

                args = list(filter(None, task.split()))

                print(args)

                try:

                    subprocess.run(args, check=True)

                except subprocess.CalledProcessError as e:

                    print(f"Ошибка установки: {e}")

                    return False

        

        if load_module(module):

            print('imported!')

            return True

        else:

            print('Не удалось импортировать модуль после установки')

            return False

    else:

        print('imported!')

        return True                                       # строка 1 — ВСЕГДА
include("import requests", ["extella-pip install requests"]) # НЕ pip!
include("import sqlite3", [])                               # stdlib = []

def tw_example(          # имя функции = имя файла (snake_case)
    param1: str = "",    # тип + дефолт обязательны
    param2: int = 0,
    api_key_name: str = "openai_api_key",
) -> dict:               # всегда dict
    import requests      # импорты ВНУТРИ функции
    ...
    return {"status": "success", "result": ...}
```

## Что ЗАПРЕЩЕНО

| НЕЛЬЗЯ | НУЖНО |
|--------|-------|
| `pip install X` | `extella-pip install X` |
| API ключ в коде | Читать из KV Store по имени ключа |
| `/Users/john/file.pdf` | `Path.home() / "Downloads" / "file.pdf"` |
| `*args`, `**kwargs` | Явные именованные параметры |
| Возвращать файлы/байты | Возвращать путь к файлу |
| Импорты вне функции | Только внутри функции |

## Читать секреты из KV Store

```python
def kv_get(key):
    import os, requests
    BASE_URL = os.environ.get("EXTELLA_API_URL", "https://api.extella.ai")
    try:
        r = requests.post(BASE_URL + "/api/kv/get", json={"key": key}, timeout=10)
        if r.status_code == 200: return r.json().get("value", "")
    except Exception: pass
    return ""

api_key = kv_get("openai_api_key")   # читать ключ по имени
db_path = kv_get("tw_db_path")       # читать путь к БД
```

## Вызов другого эксперта (nested)

```python
def run_expert(name, params, token, base_url):
    import requests
    r = requests.post(base_url + "/api/expert/run",
        headers={"X-Auth-Token": token, "Content-Type": "application/json"},
        json={"expert_name": name, "params": params}, timeout=120)
    if r.status_code == 200:
        data = r.json()
        result = data.get("result")
        return result if isinstance(result, dict) else data
    return {"status": "error", "error": "HTTP " + str(r.status_code)}
```

## Playwright — обязательные правила

```python
# 1. Добавить user site-packages ДО импорта playwright
import site, importlib, sys
up = site.getusersitepackages()
if up not in sys.path: sys.path.insert(0, up)
importlib.invalidate_caches()
from playwright.sync_api import sync_playwright

# 2. Перехват GraphQL — ТОЛЬКО page.route() (не page.on("response"))
def on_route(route):
    if "SearchTimeline" in route.request.url:
        response = route.fetch()
        data = response.json()
        # ... обработка ...
        route.fulfill(response=response)
    else:
        route.continue_()
page.route("**/**", on_route)
page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
page.wait_for_timeout(4000)

# 3. Cookies для ОБОИХ доменов
for domain in [".x.com", ".twitter.com"]:
    ctx.add_cookies([
        {"name": "auth_token", "value": auth_token, "domain": domain, "path": "/"},
        {"name": "ct0",        "value": ct0,        "domain": domain, "path": "/"},
    ])
```

## Логи (обязательно)

```python
print("[1/5] Начало...")
print("[2/5] Загрузка данных...")
print("[3/5] Обработка...")
print("[4/5] Сохранение...")
print("[5/5] Готово!")
```

## Архитектура Twitter Lead Agent

```
tw_agent (точка входа)
├── tw_server → Flask :7842 + SPA UI
│   ├── /api/credentials/pool       (прокси Twitter сессий)
│   ├── /api/run/smart_discover
│   └── /api/run/posts_for_profile
├── tw_data      → инициализация SQLite
├── tw_auth      → аккаунты (circuit breaker)
├── tw_discover  → поиск профилей (GraphQL)
│   ├── calls: tw_query_expand
│   └── reads: localhost:7842/api/credentials/pool
├── tw_posts     → сбор постов
│   ├── calls: tw_query_expand
│   └── reads: localhost:7842/api/credentials/pool
├── tw_query_expand → NL → ключевые слова (LLM / rule-based)
├── tw_generate  → AI генерация реплаев
├── tw_queue     → FSM: pending→approved→posted
├── tw_post      → постинг через Playwright
│   └── reads: localhost:7842/api/credentials/{id}
├── tw_monitor   → мониторинг ответов
└── tw_debug     → диагностика

KV Store:
  tw_db_path        → путь к SQLite
  extella_api_token → API токен
  tw_session_{uuid} → {auth_token, ct0}
  openai_api_key    → OpenAI
  groq_api_key      → Groq
  anthropic_api_key → Anthropic

SQLite таблицы: accounts, profiles, posts, reply_tasks,
                conversations, analytics, settings, workflow_state
```

## Workflow с Cursor

```bash
# Открой папку в Cursor
# Отредактируй experts/tw_discover.py
# Синхронизируй:
python sync_to_extella.py tw_discover
# Все сразу:
python sync_to_extella.py --all
```
