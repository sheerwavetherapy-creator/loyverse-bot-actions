#!/usr/bin/env python3
"""Long-poll Telegram updates and respond to the /lowstock command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib import error, parse, request

from scripts.low_stock_alerts import build_daily_low_stock_message, telegram_send_message


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


def process_update(update: dict, csv_path: str | None, api_token: str | None, base_url: str, bot_token: str | None = None) -> bool:
    message = update.get("message") or update.get("channel_post")
    if not isinstance(message, dict) or not is_lowstock_command(message.get("text")):
        return False
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return False
    topic_id = message.get("message_thread_id")
    response = build_daily_low_stock_message(csv_path, api_token=api_token, base_url=base_url)
    return telegram_send_message(
        response,
        bot_token=bot_token,
        chat_id=str(chat_id),
        topic_id=str(topic_id) if topic_id else None,
    )


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    csv_path = os.environ.get("EXPORT_CSV_PATH")
    if csv_path is None and Path("export_items-11.csv").exists():
        csv_path = "export_items-11.csv"
    api_token = os.environ.get("LOYVERSE_API_TOKEN")
    base_url = os.environ.get("LOYVERSE_API_BASE_URL") or "https://api.loyverse.com/v1.0"
    offset_path = Path(os.environ.get("TELEGRAM_COMMAND_OFFSET_FILE", ".telegram_lowstock_offset"))
    offset = int(offset_path.read_text().strip()) if offset_path.exists() else 0

    while True:
        try:
            result = telegram_api_call(
                token,
                "getUpdates",
                {"offset": offset, "timeout": 30, "allowed_updates": json.dumps(["message", "channel_post"])},
            )
            for update in result.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                process_update(update, csv_path, api_token, base_url, bot_token=token)
            offset_path.write_text(str(offset))
        except (error.URLError, TimeoutError, ValueError, RuntimeError):
            continue


if __name__ == "__main__":
    raise SystemExit(main())