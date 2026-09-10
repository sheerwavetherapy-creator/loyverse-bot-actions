#!/usr/bin/env python3
"""Long-poll Telegram updates and respond to the /lowstock command."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib import error, parse, request

from scripts.low_stock_alerts import (
    build_daily_low_stock_message,
    send_low_stock_alert,
    telegram_send_message,
)

# Commands older than this are queued backlog (for example a redeploy replaying
# a prior day's storm) and are discarded instead of answered.
DEFAULT_MAX_COMMAND_AGE_SECONDS = 300


def telegram_api_call(token: str, method: str, payload: dict) -> dict:
    endpoint = f"https://api.telegram.org/bot{token}/{method}"
    encoded = parse.urlencode(payload).encode("utf-8")
    req = request.Request(endpoint, data=encoded, method="POST")
    with request.urlopen(req, timeout=40) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed")
    return result


def is_lowstock_command(text: str | None) -> bool:
    command = (text or "").strip().split(maxsplit=1)[0].lower()
    return command == "/lowstock" or command.startswith("/lowstock@")


def is_stale_command(message: dict, max_age_seconds: int, now: int | None = None) -> bool:
    """True if a message's own `date` is older than max_age_seconds (queued backlog)."""
    date = message.get("date")
    if date is None:
        return False
    current = now if now is not None else int(time.time())
    try:
        return (current - int(date)) > max_age_seconds
    except (TypeError, ValueError):
        return False


def process_update(
    update: dict,
    csv_path: str | None,
    api_token: str | None,
    base_url: str,
    bot_token: str | None = None,
    max_command_age_seconds: int = DEFAULT_MAX_COMMAND_AGE_SECONDS,
) -> bool:
    message = update.get("message") or update.get("channel_post")
    if not isinstance(message, dict) or not is_lowstock_command(message.get("text")):
        return False
    if is_stale_command(message, max_command_age_seconds):
        print(
            f"[SKIP] Discarding stale /lowstock command from backlog, update_id={update.get('update_id')} "
            f"date={message.get('date')}",
            file=sys.stderr,
        )
        return False
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return False
    topic_id = message.get("message_thread_id")
    response = build_daily_low_stock_message(csv_path, api_token=api_token, base_url=base_url)
    return send_low_stock_alert(
        response,
        bot_token=bot_token,
        chat_id=str(chat_id),
        topic_id=str(topic_id) if topic_id else None,
    )


def resolve_startup_offset(token: str, offset_path: Path) -> int:
    """Load the persisted offset, or fast-forward past any backlog on cold start."""
    if offset_path.exists():
        return int(offset_path.read_text().strip())

    # Cold start with no saved offset: fast-forward past any queued backlog
    # instead of replaying it (this is what caused the Aug 30 storm).
    offset = 0
    try:
        probe = telegram_api_call(token, "getUpdates", {"offset": 0, "timeout": 0})
        backlog = probe.get("result", [])
        if backlog:
            offset = max(int(update["update_id"]) for update in backlog) + 1
            print(
                f"[STARTUP] No offset file found; skipping {len(backlog)} backlogged update(s) "
                f"and starting from offset={offset}",
                file=sys.stderr,
            )
    except (error.URLError, TimeoutError, ValueError, RuntimeError):
        pass
    offset_path.write_text(str(offset))
    return offset


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    csv_path = os.environ.get("EXPORT_CSV_PATH")
    if csv_path is None and Path("export_items-11.csv").exists():
        csv_path = "export_items-11.csv"
    api_token = os.environ.get("LOYVERSE_API_TOKEN")
    base_url = os.environ.get("LOYVERSE_API_BASE_URL") or "https://api.loyverse.com/v1.0"
    max_command_age_seconds = int(
        os.environ.get("TELEGRAM_COMMAND_MAX_AGE_SECONDS", DEFAULT_MAX_COMMAND_AGE_SECONDS)
    )
    offset_file_env = os.environ.get("TELEGRAM_COMMAND_OFFSET_FILE")
    if not offset_file_env:
        print(
            "[WARN] TELEGRAM_COMMAND_OFFSET_FILE is not set; defaulting to "
            "'.telegram_lowstock_offset' in the working directory. If this is not a persistent "
            "disk mount, the offset will be lost on every deploy/restart.",
            file=sys.stderr,
        )
    offset_path = Path(offset_file_env or ".telegram_lowstock_offset")
    offset = resolve_startup_offset(token, offset_path)

    while True:
        try:
            result = telegram_api_call(
                token,
                "getUpdates",
                {"offset": offset, "timeout": 30, "allowed_updates": json.dumps(["message", "channel_post"])},
            )
            for update in result.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                process_update(
                    update,
                    csv_path,
                    api_token,
                    base_url,
                    bot_token=token,
                    max_command_age_seconds=max_command_age_seconds,
                )
            offset_path.write_text(str(offset))
        except (error.URLError, TimeoutError, ValueError, RuntimeError):
            continue


if __name__ == "__main__":
    raise SystemExit(main())