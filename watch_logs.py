#!/usr/bin/env python3
"""
watch_logs.py — live tail of Twitter Lead Agent server logs

ИСПОЛЬЗОВАНИЕ:
  python watch_logs.py              # показывать последние 20 строк и следить
  python watch_logs.py --lines 50  # последние 50 строк
  python watch_logs.py --filter ERR  # только ошибки
  python watch_logs.py --filter REQ  # только запросы

УРОВНИ: REQ, OK, ERR, WARN, INFO, KV
"""

import sys, os, time, requests
from pathlib import Path

SERVER_URL = "http://127.0.0.1:7842"

COLORS = {
    "REQ":  "\033[94m",   # blue
    "OK":   "\033[92m",   # green
    "ERR":  "\033[91m",   # red
    "WARN": "\033[93m",   # yellow
    "INFO": "\033[95m",   # magenta
    "KV":   "\033[96m",   # cyan
}
RESET = "\033[0m"

def fetch_logs(lines=100):
    try:
        r = requests.get(f"{SERVER_URL}/api/logs?lines={lines}", timeout=5)
        if r.status_code == 200:
            return r.json().get("logs", [])
    except Exception:
        pass
    return None

def format_line(entry, use_color=True):
    level = entry.get("level", "?")
    ts    = entry.get("ts", "")
    msg   = entry.get("msg", "")
    extra = entry.get("extra", "")
    color = COLORS.get(level, "") if use_color else ""
    extra_str = f"  \033[2m{extra}\033[0m" if extra and use_color else (f"  {extra}" if extra else "")
    return f"{color}{ts} {level:<5}{RESET} {msg}{extra_str}"

def main():
    args      = sys.argv[1:]
    n_lines   = 20
    log_filter = None
    use_color  = sys.stdout.isatty()

    i = 0
    while i < len(args):
        if args[i] == "--lines" and i + 1 < len(args):
            n_lines = int(args[i+1]); i += 2
        elif args[i] == "--filter" and i + 1 < len(args):
            log_filter = args[i+1].upper(); i += 2
        elif args[i] in ("--no-color", "--plain"):
            use_color = False; i += 1
        else:
            i += 1

    print(f"📡 Watching {SERVER_URL}/api/logs  (Ctrl+C to stop)\n")

    seen = set()
    first = True

    while True:
        logs = fetch_logs(200)
        if logs is None:
            print("\r⚠️  Server unreachable, retrying...", end="", flush=True)
            time.sleep(3)
            continue

        new_lines = []
        for entry in logs:
            key = (entry.get("ts"), entry.get("msg"))
            if key not in seen:
                seen.add(key)
                if log_filter and entry.get("level") != log_filter:
                    continue
                new_lines.append(entry)

        if first:
            # On first run: show last n_lines (already filtered)
            to_show = new_lines[-n_lines:] if not log_filter else new_lines
            for entry in to_show:
                print(format_line(entry, use_color))
            first = False
        else:
            for entry in new_lines:
                print(format_line(entry, use_color))

        time.sleep(2)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Stopped.")
