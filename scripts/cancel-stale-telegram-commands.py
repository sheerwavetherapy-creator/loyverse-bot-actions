#!/usr/bin/env python3
"""Drain queued Telegram command updates without answering them.

Use this before (re)starting scripts/telegram_lowstock_bot.py if the poller's
offset file was lost or reset, to cancel a backlog of stale commands (for
example the 30 Aug storm) instead of letting the poller reply to all of them.

Usage:
  TELEGRAM_BOT_TOKEN=... python3 scripts/cancel-stale-telegram-commands.py [--dry-run]

Writes the resulting offset to TELEGRAM_COMMAND_OFFSET_FILE (same file the
poller reads), so the poller resumes cleanly with the backlog discarded.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import parse, request


def telegram_api_call(token: str, method: str, payload: dict) -> dict:
    endpoint = f"https://api.telegram.org/bot{token}/{method}"
    encoded = parse.urlencode(payload).encode("utf-8")
    req = request.Request(endpoint, data=encoded, method="POST")
    with request.urlopen(req, timeout=40) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is required.", file=sys.stderr)
        return 2
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN", "0").strip().lower() in {"1", "true", "yes", "y"}
    offset_path = Path(os.environ.get("TELEGRAM_COMMAND_OFFSET_FILE", ".telegram_lowstock_offset"))

    result = telegram_api_call(token, "getUpdates", {"offset": 0, "timeout": 0})
    backlog = result.get("result", [])
    if not backlog:
        print("No pending updates to cancel.")
        return 0

    print(f"Found {len(backlog)} pending update(s):")
    for update in backlog:
        message = update.get("message") or update.get("channel_post") or {}
        text = (message.get("text") or "").strip()
        date = message.get("date")
        print(f"  update_id={update['update_id']} date={date} text={text[:80]!r}")

    next_offset = max(int(update["update_id"]) for update in backlog) + 1
    if dry_run:
        print(f"Dry run only. Would cancel {len(backlog)} update(s) and set offset={next_offset}.")
        return 0

    # Confirming the offset with Telegram (without processing) discards the backlog.
    telegram_api_call(token, "getUpdates", {"offset": next_offset, "timeout": 0})
    offset_path.write_text(str(next_offset))
    print(f"Cancelled {len(backlog)} stale update(s). Offset advanced to {next_offset} and saved to {offset_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
