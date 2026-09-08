#!/usr/bin/env python3
"""Low stock alert helpers for Loyverse inventory updates.

This module implements the workflow described for a restored low stock alert feature:
- load item/export data from a CSV file
- filter to eligible items with a non-empty threshold in column U
- calculate alerts and suggestion blocks for Telegram
- group by category alphabetically for stable output
"""

from __future__ import annotations

import csv
import json
import os
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib import request, error

STATE_FILE = Path.home() / ".telegram_state.json"


INVENTORY_TOPIC_ID = "4442209616/1895"
INVENTORY_TOPIC_URL = "t.me/c/4442209616/1895"

# Categories that are explicitly excluded from suggestion prompts.
SUGGESTION_EXCEPTIONS = {
    "Alcohol": lambda qty, threshold: qty > 20,
    "Nonalc Drinks": lambda qty, threshold: qty > 10,
    "Snacks": lambda qty, threshold: True,
}


def normalize_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("  ", " ")


def is_antiquated_event(event_time: str | None, now: int | None = None) -> bool:
    if not event_time:
        return False
    text = str(event_time).strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    timestamp = parsed.timestamp()
    current = now if now is not None else int(time.time())
    return timestamp < current


def is_older_than_last_processed(event_time: str | None, last_processed: str | None) -> bool:
    if not event_time or not last_processed:
        return False
    try:
        event_ts = datetime.fromisoformat(str(event_time).replace("Z", "+00:00")).timestamp()
        last_ts = datetime.fromisoformat(str(last_processed).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return event_ts < last_ts


def parse_threshold_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value).strip() != "":
            return value
    return None


def parse_export_csv(csv_path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read CSV rows, with support for the export_items-11.csv schema.

    The importer accepts common header variations and reads both the named export file and
    any generated CSV export from Loyverse.
    """
    rows: list[dict[str, Any]] = []
    path = Path(csv_path)
    if not path.exists():
        return rows

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            if raw is None:
                continue
            normalized = {}
            for key, value in raw.items():
                if key is None:
                    continue
                normalized[key.strip()] = value

            name = first_non_empty(
                normalized.get("Item Name"),
                normalized.get("Name"),
                normalized.get("item_name"),
                normalized.get("Item"),
            )
            category = first_non_empty(
                normalized.get("Category"),
                normalized.get("category"),
                normalized.get("Item Category"),
            )
            cost_value = first_non_empty(
                normalized.get("Cost"),
                normalized.get("purchase cost"),
                normalized.get("Purchase Cost"),
                normalized.get("PC"),
                normalized.get("Item Cost"),
                normalized.get("Cost (per unit)"),
            )
            qty = first_non_empty(
                normalized.get("Quantity"),
                normalized.get("Qty"),
                normalized.get("Quantity in Stock"),
                normalized.get("Stock"),
                normalized.get("Inventory Quantity"),
            )
            threshold = first_non_empty(
                normalized.get("Low Stock Alert Threshold"),
                normalized.get("Low Stock Threshold"),
                normalized.get("Threshold"),
                normalized.get("Low Stock"),
                normalized.get("U"),
            )

            threshold_dec = parse_threshold_value(threshold)
            if name is None:
                continue

            rows.append(
                {
                    "item_name": str(name).strip(),
                    "category": str(category or "Uncategorized").strip(),
                    "cost": str(cost_value).strip() if cost_value is not None else "",
                    "quantity": str(qty).strip() if qty is not None else "",
                    "threshold": threshold_dec,
                }
            )
    return rows


def format_money(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "N/A"
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return str(value).strip()
    if amount == amount.to_integral_value():
        return f"{amount.quantize(Decimal('1'))}"
    return format(amount.normalize(), "f")


def build_alert_block(rows: Iterable[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("item_name"):
            continue
        qty_text = str(row.get("quantity") or "").strip()
        if qty_text == "":
            continue
        try:
            qty = Decimal(qty_text)
        except InvalidOperation:
            continue
        threshold = row.get("threshold")
        if threshold is None:
            continue
        if qty <= threshold:
            grouped[str(row.get("category") or "Uncategorized")].append(row)

    if not grouped:
        return "No low stock items at this time."

    lines: list[str] = []
    for category in sorted(grouped.keys(), key=lambda value: value.lower()):
        items = sorted(
            grouped[category],
            key=lambda row: normalize_name(row["item_name"]),
        )
        lines.append(category.upper())
        for row in items:
            cost = row.get("cost")
            qty = row.get("quantity")
            item_name = str(row["item_name"]).strip()
            lines.append(
                f"{item_name} | PC: {format_money(cost)} | QTY: {qty}"
            )
        lines.append("")
    return "\n".join(lines).strip()


def build_suggestion_block(rows: Iterable[dict[str, Any]]) -> str:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        qty_text = str(row.get("quantity") or "").strip()
        if not qty_text:
            continue
        try:
            qty = Decimal(qty_text)
        except InvalidOperation:
            continue
        threshold = row.get("threshold")
        if threshold is None:
            continue
        category = str(row.get("category") or "Uncategorized").strip()
        rule = SUGGESTION_EXCEPTIONS.get(category)
        if rule is not None and rule(qty, threshold):
            continue
        diff = abs(qty - threshold)
        candidates.append({**row, "distance": diff})

    if not candidates:
        return "No suggestion candidates available."

    items = sorted(candidates, key=lambda row: (row["distance"], normalize_name(row["item_name"])))
    selected = items[:4]
    lines = ["SUGGESTED RESTOCK PRIORITY"]
    for row in selected:
        category = str(row.get("category") or "Uncategorized").strip()
        qty = row.get("quantity")
        threshold = row.get("threshold")
        lines.append(
            f"{row['item_name']} | Category: {category} | Qty: {qty} | Threshold: {threshold}"
        )
    return "\n".join(lines)


def threshold_lookup_from_csv(csv_path: str | os.PathLike[str]) -> dict[str, Decimal]:
    lookup: dict[str, Decimal] = {}
    for row in parse_export_csv(csv_path):
        threshold = row.get("threshold")
        if threshold is None:
            continue
        name = normalize_name(row.get("item_name"))
        category = normalize_name(row.get("category"))
        if name:
            lookup[f"name::{name}"] = threshold
        if name and category:
            lookup[f"name-cat::{name}::{category}"] = threshold
    return lookup


def normalize_api_row(raw: dict[str, Any]) -> dict[str, Any]:
    name = first_non_empty(
        raw.get("name"),
        raw.get("item_name"),
        raw.get("title"),
        raw.get("product_name"),
    )
    category = first_non_empty(
        raw.get("category"),
        raw.get("category_name"),
        raw.get("categoryName"),
        raw.get("item_category"),
        raw.get("categoryName"),
    )
    if isinstance(category, dict):
        category = category.get("name") or category.get("title") or category.get("category")
    cost = first_non_empty(
        raw.get("cost"),
        raw.get("purchaseCost"),
        raw.get("purchase_cost"),
        raw.get("average_cost"),
        raw.get("averageCost"),
        raw.get("price"),
    )
    quantity = first_non_empty(
        raw.get("quantity"),
        raw.get("stock"),
        raw.get("available_quantity"),
        raw.get("quantity_in_stock"),
        raw.get("on_hand"),
    )
    if isinstance(raw.get("inventory"), dict):
        inv = raw["inventory"]
        quantity = first_non_empty(quantity, inv.get("quantity"), inv.get("stock"), inv.get("available_quantity"))
    if isinstance(raw.get("stock"), dict):
        stock = raw["stock"]
        quantity = first_non_empty(quantity, stock.get("quantity"), stock.get("on_hand"), stock.get("available"))
    if name is None:
        return {}
    return {
        "item_name": str(name).strip(),
        "category": str(category or "Uncategorized").strip(),
        "cost": str(cost).strip() if cost is not None else "",
        "quantity": str(quantity).strip() if quantity is not None else "",
    }


def fetch_loyverse_items(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> list[dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    candidates = [
        f"{base_url.rstrip('/')}/items?limit=100&offset=0",
        f"{base_url.rstrip('/')}/items",
    ]

    for url in candidates:
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except (error.HTTPError, error.URLError, ValueError, TimeoutError):
            continue

        if isinstance(data, list):
            payload = data
        elif isinstance(data, dict):
            payload = data.get("data") or data.get("items") or data.get("results") or []
        else:
            payload = []

        rows = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    normalized = normalize_api_row(item)
                    if normalized:
                        rows.append(normalized)
        if rows:
            return rows
    return []


def merge_api_rows_with_thresholds(api_rows: Iterable[dict[str, Any]], csv_path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    threshold_map = threshold_lookup_from_csv(csv_path) if csv_path else {}
    merged: list[dict[str, Any]] = []
    for row in api_rows:
        name = normalize_name(row.get("item_name"))
        category = normalize_name(row.get("category"))
        threshold = None
        if name and f"name-cat::{name}::{category}" in threshold_map:
            threshold = threshold_map[f"name-cat::{name}::{category}"]
        elif name and f"name::{name}" in threshold_map:
            threshold = threshold_map[f"name::{name}"]
        if threshold is None:
            continue
        candidate = dict(row)
        candidate["threshold"] = threshold
        merged.append(candidate)
    return merged


def select_eligible_low_stock_items(csv_path: str | os.PathLike[str] | None = None, api_token: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    if api_token:
        rows = fetch_loyverse_items(api_token, base_url or "https://api.loyverse.com/v1.0")
        if rows:
            return merge_api_rows_with_thresholds(rows, csv_path)

    rows = parse_export_csv(csv_path) if csv_path else []
    eligible = []
    for row in rows:
        qty = str(row.get("quantity") or "").strip()
        if not qty:
            continue
        try:
            Decimal(qty)
        except InvalidOperation:
            continue
        threshold = row.get("threshold")
        if threshold is None:
            continue
        eligible.append(row)
    return eligible


def build_daily_low_stock_message(csv_path: str | os.PathLike[str] | None = None, api_token: str | None = None, base_url: str | None = None) -> str:
    rows = select_eligible_low_stock_items(csv_path, api_token, base_url)
    alert_block = build_alert_block(rows)
    if alert_block == "No low stock items at this time.":
        suggestion_block = build_suggestion_block(rows)
        return suggestion_block
    return alert_block


def telegram_topic_url(topic_id: str | None = None) -> str:
    value = (topic_id or os.environ.get("TELEGRAM_INVENTORY_TOPIC_ID") or INVENTORY_TOPIC_ID).strip()
    return value if value.startswith("t.me/") else f"t.me/c/{value}"


def get_latest_topic_timestamp(chat_id: str, topic_id: str | None) -> str | None:
    """Retrieve the stored latest message timestamp for a (chat_id, topic_id) pair."""
    if not STATE_FILE.exists():
        return None
    try:
        state = json.loads(STATE_FILE.read_text())
        key = f"{chat_id}:{topic_id or 'general'}"
        return state.get(key)
    except (json.JSONDecodeError, OSError):
        return None


def set_latest_topic_timestamp(chat_id: str, topic_id: str | None, timestamp: str) -> None:
    """Store the latest message timestamp for a (chat_id, topic_id) pair."""
    try:
        state = {}
        if STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text())
            except json.JSONDecodeError:
                pass
        key = f"{chat_id}:{topic_id or 'general'}"
        state[key] = timestamp
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def telegram_send_message(text: str, bot_token: str | None = None, chat_id: str | None = None, topic_id: str | None = None, event_time: str | None = None) -> bool:
    token = (bot_token or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    target = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not target:
        return False

    allow_historical = os.environ.get("ALLOW_HISTORICAL_POSTS", "false").strip().lower() in {"1", "true", "yes", "y"}
    last_processed = os.environ.get("LAST_PROCESSED_EVENT_TIME")
    if event_time and is_antiquated_event(event_time) and not allow_historical:
        return False
    if event_time and last_processed and is_older_than_last_processed(event_time, last_processed) and not allow_historical:
        return False

    # Reject messages with timestamps older than the latest known message in this topic (prevents duplicate storms).
    if event_time and target:
        latest_stored = get_latest_topic_timestamp(target, topic_id)
        if latest_stored and event_time < latest_stored:
            return False

    payload = {
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
    }
    if topic_id:
        payload["message_thread_id"] = str(topic_id)

    import json
    import urllib.request

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            if "ok" in body.lower():
                # Update the latest timestamp for this topic to prevent future older messages.
                if event_time and target:
                    set_latest_topic_timestamp(target, topic_id, event_time)
                return True
            return False
    except Exception:
        return False


def main() -> int:
    csv_path = os.environ.get("EXPORT_CSV_PATH")
    if csv_path is None:
        csv_path = "export_items-11.csv" if Path("export_items-11.csv").exists() else None
    api_token = os.environ.get("LOYVERSE_API_TOKEN")
    base_url = os.environ.get("LOYVERSE_API_BASE_URL") or "https://api.loyverse.com/v1.0"
    message = build_daily_low_stock_message(csv_path, api_token=api_token, base_url=base_url)
    print(message)

    if os.environ.get("SEND_TELEGRAM_MESSAGE", "false").lower() in {"1", "true", "yes"}:
        event_time = os.environ.get("EVENT_TIME")
        telegram_send_message(
            message,
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
            topic_id=os.environ.get("TELEGRAM_INVENTORY_TOPIC_ID"),
            event_time=event_time,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
