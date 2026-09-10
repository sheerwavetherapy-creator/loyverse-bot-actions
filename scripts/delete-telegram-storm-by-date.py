#!/usr/bin/env python3
"""Delete known Telegram storm messages from a topic.

Telegram's Bot API cannot search chat history and ``getUpdates`` never contains messages sent by
the same bot. Supply the message IDs shown in the Telegram message links instead.

Usage example:
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... TELEGRAM_SALES_TOPIC_ID=4442209616/1895 \
        MESSAGE_IDS=4114,4115,4116 DRY_RUN=1 \
        python3 scripts/delete-telegram-storm-by-date.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib import error, request


def api_call(token: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = json.dumps(params or {}).encode("utf-8")
    req = request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def get_updates(token: str, chat_id: str, offset: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
    data = api_call(
        token,
        "getUpdates",
        {"offset": offset, "limit": limit, "allowed_updates": ["message", "edited_message"]},
    )
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates failed: {data}")
    updates = data.get("result") or []
    if not updates:
        return [], offset
    next_offset = max(int(item["update_id"]) for item in updates) + 1
    return updates, next_offset


def in_target_topic(message: dict[str, Any], chat_id: str, topic_id: str | None) -> bool:
    msg_chat = message.get("chat") or {}
    if str(msg_chat.get("id")) != str(chat_id):
        return False
    if topic_id:
        expected = str(topic_id).strip()
        message_thread = str(message.get("message_thread_id") or "").strip()
        if expected.startswith("t.me/"):
            expected = expected.rsplit("/", 1)[-1]
        if message_thread and message_thread != expected:
            return False
    return True


def format_date(ts: Any) -> str:
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return dt.date().isoformat()


def parse_date(value: str) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_receipt_value(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(\d+)(?:-(\d+))?", text)
    if not match:
        return None
    left = int(match.group(1))
    right = int(match.group(2) or match.group(1))
    return left, right


def parse_message_ids(value: str | None) -> list[int]:
    """Parse a comma- or whitespace-separated set of Telegram message IDs."""
    if not value or not value.strip():
        return []
    message_ids: list[int] = []
    for item in re.split(r"[\s,]+", value.strip()):
        if not item.isdigit() or int(item) <= 0:
            raise ValueError(f"invalid message ID: {item!r}")
        message_ids.append(int(item))
    return sorted(set(message_ids))


def receipt_in_range(text: str, receipt_from: tuple[int, int] | None, receipt_to: tuple[int, int] | None) -> bool:
    if receipt_from is None and receipt_to is None:
        return True
    found = False
    for match in re.findall(r"\d+(?:-\d+)?", text):
        receipt = parse_receipt_value(match)
        if receipt is None:
            continue
        found = True
        if receipt_from is not None and receipt < receipt_from:
            continue
        if receipt_to is not None and receipt > receipt_to:
            continue
        return True
    return False if found else True


def message_matches_storm_pattern(message: dict[str, Any], receipt_from: tuple[int, int] | None = None, receipt_to: tuple[int, int] | None = None) -> bool:
    text = (message.get("text") or "").strip()
    if not text:
        return False
    if not receipt_in_range(text, receipt_from, receipt_to):
        return False
    patterns = [
        "SUGGESTED RESTOCK PRIORITY",
        "No low stock items at this time.",
        "PC:",
        "QTY:",
        "ALCOHOL",
        "SNACKS",
        "NONALC",
        "LOW STOCK",
    ]
    text_normalized = text.upper()
    return any(p.upper() in text_normalized for p in patterns)


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    topic_id = (
        os.environ.get("TELEGRAM_SALES_TOPIC_ID")
        or os.environ.get("TELEGRAM_INVENTORY_TOPIC_ID")
        or ""
    ).strip() or None
    message_ids_raw = os.environ.get("MESSAGE_IDS", "")
    dry_run = os.environ.get("DRY_RUN", "0").strip().lower() in {"1", "true", "yes", "y"}

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is required.", file=sys.stderr)
        return 2
    if not chat_id:
        print("ERROR: TELEGRAM_CHAT_ID is required.", file=sys.stderr)
        return 2
    if not topic_id:
        print("ERROR: TELEGRAM_SALES_TOPIC_ID or TELEGRAM_INVENTORY_TOPIC_ID is required.", file=sys.stderr)
        return 2
    try:
        matches = parse_message_ids(message_ids_raw)
    except ValueError as exc:
        print(f"ERROR: MESSAGE_IDS must be a comma- or whitespace-separated list of positive integers: {exc}", file=sys.stderr)
        return 2
    if not matches:
        print("ERROR: MESSAGE_IDS is required. The Telegram Bot API cannot scan historical bot messages by date or receipt.", file=sys.stderr)
        return 2

    print(f"Preparing to delete {len(matches)} message(s) from Telegram chat {chat_id} topic {topic_id}.")
    if not matches:
        return 0

    if dry_run:
        print("Dry run only. No deletions sent.")
        for msg_id in matches:
            print(f"Would delete message_id={msg_id}")
        return 0

    for msg_id in matches:
        try:
            result = api_call(token, "deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
            if result.get("ok"):
                print(f"Deleted message_id={msg_id}")
            else:
                print(f"FAILED to delete message_id={msg_id}: {result}", file=sys.stderr)
        except Exception as exc:  # pragma: no cover - network/API failure handling
            print(f"ERROR deleting message_id={msg_id}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
