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
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib import request, error

STATE_FILE = Path.home() / ".telegram_state.json"

# Tracks the previously posted low-stock alert message so it can be deleted on regeneration.
ALERT_STATE_FILE = Path(os.environ.get("LOW_STOCK_ALERT_STATE_FILE") or "low_stock_alert_state.json")


INVENTORY_TOPIC_ID = "4442209616/1895"
INVENTORY_TOPIC_URL = "t.me/c/4442209616/1895"

# Categories that are explicitly excluded from suggestion prompts.
SUGGESTION_EXCEPTIONS = {
    "Alcohol": lambda qty, threshold: qty > 20,
    "Nonalc Drinks": lambda qty, threshold: qty > 10,
    "Snacks": lambda qty, threshold: True,
}


def normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


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


def normalize_header_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def first_header_value(headers: dict[str, Any], exact_names: Iterable[str], prefixes: Iterable[str]) -> Any:
    exact_aliases = {normalize_header_name(name) for name in exact_names}
    for key, candidate in headers.items():
        normalized_key = normalize_header_name(key)
        if normalized_key in exact_aliases:
            value = first_non_empty(candidate)
            if value is not None:
                return value
    for key, candidate in headers.items():
        normalized_key = normalize_header_name(key)
        if any(normalized_key.startswith(normalize_header_name(prefix)) for prefix in prefixes):
            value = first_non_empty(candidate)
            if value is not None:
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

            name = first_header_value(
                normalized,
                ("Item Name", "Name", "item_name", "Item"),
                (),
            )
            category = first_header_value(
                normalized,
                ("Category", "category", "Item Category"),
                (),
            )
            cost_value = first_header_value(
                normalized,
                (
                    "Cost",
                    "purchase cost",
                    "Purchase Cost",
                    "PC",
                    "Item Cost",
                    "Cost (per unit)",
                ),
                (),
            )
            qty = first_header_value(
                normalized,
                ("Quantity", "Qty", "Quantity in Stock", "Stock", "Inventory Quantity"),
                ("In stock [",),
            )
            threshold = first_header_value(
                normalized,
                ("Low Stock Alert Threshold", "Low Stock Threshold", "Threshold", "Low Stock", "U"),
                ("Low stock [",),
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


def format_quantity(value: Any) -> str:
    try:
        quantity = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return str(value).strip()
    if quantity == quantity.to_integral_value():
        return f"{quantity.quantize(Decimal('1'))}"
    return format(quantity.normalize(), "f")


def build_alert_block(rows: Iterable[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    checked_count = 0
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
        checked_count += 1
        if qty <= threshold:
            grouped[str(row.get("category") or "Uncategorized")].append(row)
            print(f"[DEBUG] Low stock: {row.get('item_name')} qty={qty} threshold={threshold}", file=sys.stderr)
        else:
            print(f"[DEBUG] OK: {row.get('item_name')} qty={qty} threshold={threshold}", file=sys.stderr)

    print(f"[DEBUG] Checked {checked_count} items, found {sum(len(v) for v in grouped.values())} low stock", file=sys.stderr)
    if not grouped:
        return "No low stock items at this time."

    lines: list[str] = ["<b>🚨 LOW STOCK ALERT</b>", "----------------------------------"]
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
                f"{item_name} | PC: {format_money(cost)} KSh | QTY: {format_quantity(qty)}"
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


def normalize_api_row(raw: dict[str, Any], category_map: dict[str, str] | None = None) -> dict[str, Any]:
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
    )
    if isinstance(category, dict):
        category = category.get("name") or category.get("title") or category.get("category")
    category_id = first_non_empty(raw.get("category_id"), raw.get("categoryId"))
    if category is None and category_id is not None and category_map:
        category = category_map.get(str(category_id))
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

    # Loyverse nests cost and variant identifiers per-variant rather than on the item itself.
    variant_ids: list[str] = []
    cost = first_non_empty(raw.get("cost"), raw.get("purchaseCost"), raw.get("purchase_cost"))
    variants = raw.get("variants")
    if isinstance(variants, list):
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_id = first_non_empty(variant.get("variant_id"), variant.get("id"))
            if variant_id is not None:
                variant_ids.append(str(variant_id))
            if cost is None:
                cost = first_non_empty(
                    variant.get("cost"),
                    variant.get("purchase_cost"),
                    variant.get("default_price"),
                )
    return {
        "item_name": str(name).strip(),
        "category": str(category or "Uncategorized").strip(),
        "cost": str(cost).strip() if cost is not None else "",
        "quantity": str(quantity).strip() if quantity is not None else "",
        "variant_ids": variant_ids,
    }


def fetch_loyverse_categories(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> dict[str, str]:
    """Fetch the category_id -> name mapping (the /items endpoint only returns category_id)."""
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    names: dict[str, str] = {}
    cursor: str | None = None
    base = f"{base_url.rstrip('/')}/categories"
    for _ in range(50):  # safety cap against runaway pagination
        url = f"{base}?limit=250" + (f"&cursor={cursor}" if cursor else "")
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except (error.HTTPError, error.URLError, ValueError, TimeoutError):
            break
        if not isinstance(data, dict):
            break
        entries = data.get("categories") or data.get("data") or []
        if not isinstance(entries, list):
            break
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            category_id = first_non_empty(entry.get("id"), entry.get("category_id"))
            name = first_non_empty(entry.get("name"), entry.get("category_name"))
            if category_id is not None and name is not None:
                names[str(category_id)] = str(name)
        cursor = data.get("cursor")
        if not cursor:
            break
    return names


def fetch_loyverse_items(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> list[dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    resolved_base_url = base_url or "https://api.loyverse.com/v1.0"
    category_map = fetch_loyverse_categories(api_token, resolved_base_url)
    print(f"[DEBUG] Fetched {len(category_map)} categories from Loyverse API", file=sys.stderr)

    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    base = f"{resolved_base_url.rstrip('/')}/items"
    for _ in range(200):  # safety cap against runaway pagination
        url = f"{base}?limit=250" + (f"&cursor={cursor}" if cursor else "")
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except (error.HTTPError, error.URLError, ValueError, TimeoutError):
            break

        if isinstance(data, list):
            payload = data
            cursor = None
        elif isinstance(data, dict):
            payload = data.get("items") or data.get("data") or data.get("results") or []
            cursor = data.get("cursor")
        else:
            break

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    normalized = normalize_api_row(item, category_map)
                    if normalized:
                        rows.append(normalized)
        if not cursor:
            break
    return rows


def fetch_loyverse_inventory_levels(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> dict[str, Decimal]:
    """Fetch live stock levels keyed by variant_id from the /inventory endpoint.

    The /items endpoint does not include on-hand quantity, so quantity must come from
    here to avoid relying on the (potentially stale) committed CSV export for stock counts.
    """
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    levels: dict[str, Decimal] = {}
    cursor: str | None = None
    base = f"{base_url.rstrip('/')}/inventory"
    for _ in range(50):  # safety cap against runaway pagination
        url = f"{base}?limit=250" + (f"&cursor={cursor}" if cursor else "")
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except (error.HTTPError, error.URLError, ValueError, TimeoutError):
            break
        if not isinstance(data, dict):
            break
        entries = data.get("inventory_levels") or data.get("data") or []
        if not isinstance(entries, list):
            break
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            variant_id = first_non_empty(entry.get("variant_id"), entry.get("id"))
            in_stock = first_non_empty(entry.get("in_stock"), entry.get("quantity"), entry.get("stock"))
            if variant_id is None or in_stock is None:
                continue
            qty = parse_threshold_value(in_stock)
            if qty is None:
                continue
            key = str(variant_id)
            levels[key] = levels.get(key, Decimal(0)) + qty
        cursor = data.get("cursor")
        if not cursor:
            break
    return levels


def merge_api_rows_with_thresholds(api_rows: Iterable[dict[str, Any]], csv_path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    threshold_map = threshold_lookup_from_csv(csv_path) if csv_path else {}
    print(f"[DEBUG] Loaded {len(threshold_map)} thresholds from CSV", file=sys.stderr)
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
        print(f"[DEBUG] Merged {name} with threshold {threshold}", file=sys.stderr)
    return merged


def apply_live_inventory_quantities(rows: list[dict[str, Any]], inventory_levels: dict[str, Decimal]) -> list[dict[str, Any]]:
    """Overwrite each row's quantity with the live stock level for its variant(s), when known."""
    if not inventory_levels:
        return rows
    updated: list[dict[str, Any]] = []
    for row in rows:
        candidate = dict(row)
        variant_ids = row.get("variant_ids") or []
        total = None
        for variant_id in variant_ids:
            level = inventory_levels.get(str(variant_id))
            if level is not None:
                total = (total or Decimal(0)) + level
        if total is not None:
            candidate["quantity"] = format_quantity(total)
        updated.append(candidate)
    return updated


def select_eligible_low_stock_items(csv_path: str | os.PathLike[str] | None = None, api_token: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    if api_token:
        resolved_base_url = base_url or "https://api.loyverse.com/v1.0"
        rows = fetch_loyverse_items(api_token, resolved_base_url)
        if rows:
            print(f"[DEBUG] Fetched {len(rows)} items from Loyverse API", file=sys.stderr)
            inventory_levels = fetch_loyverse_inventory_levels(api_token, resolved_base_url)
            print(f"[DEBUG] Fetched {len(inventory_levels)} live inventory levels from Loyverse API", file=sys.stderr)
            rows = apply_live_inventory_quantities(rows, inventory_levels)
            merged = merge_api_rows_with_thresholds(rows, csv_path)
            print(f"[DEBUG] {len(merged)} items have thresholds after merge", file=sys.stderr)
            # Check if merged rows have quantity data; if not, fall back to CSV
            has_quantity = any(str(row.get("quantity") or "").strip() for row in merged)
            if has_quantity:
                return merged
            print("[DEBUG] API items lack quantity data; falling back to CSV for inventory", file=sys.stderr)

    rows = parse_export_csv(csv_path) if csv_path else []
    print(f"[DEBUG] Parsed {len(rows)} rows from CSV", file=sys.stderr)
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
    print(f"[DEBUG] {len(eligible)} items have quantity and threshold", file=sys.stderr)
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


def load_alert_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state_path = Path(path) if path is not None else ALERT_STATE_FILE
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_alert_state(state: dict[str, Any], path: str | os.PathLike[str] | None = None) -> None:
    state_path = Path(path) if path is not None else ALERT_STATE_FILE
    try:
        state_path.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def telegram_delete_message(bot_token: str, chat_id: str, message_id: int | str) -> bool:
    import json as _json
    import urllib.request
    from urllib import error as urlerror

    endpoint = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
    payload = {"chat_id": chat_id, "message_id": message_id}
    req = urllib.request.Request(
        endpoint,
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            if '"ok":true' in body.replace(" ", "").lower():
                print(f"[OK] Deleted previous low stock alert message_id={message_id}", file=sys.stderr)
                return True
            print(f"[WARN] Could not delete previous message_id={message_id}: {body[:200]}", file=sys.stderr)
            return False
    except urlerror.HTTPError as http_exc:
        try:
            error_body = http_exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = "(unable to read error body)"
        # A missing/already-deleted message is not fatal; just log and move on.
        print(f"[WARN] Telegram deleteMessage HTTP {http_exc.code}: {error_body[:300]}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[WARN] Telegram deleteMessage failed: {exc}", file=sys.stderr)
        return False


def telegram_send_message(text: str, bot_token: str | None = None, chat_id: str | None = None, topic_id: str | None = None, event_time: str | None = None, on_success: "Any" = None) -> bool:
    token = (bot_token or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    target = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not target:
        print("[ERROR] Telegram credentials missing: token or chat_id not set", file=sys.stderr)
        return False

    historical_flags = (
        os.environ.get("ALLOW_HISTORICAL_POSTS"),
        os.environ.get("ALLOW_HISTORICAL_REPLAY"),
        os.environ.get("ALLOW_HISTORICAL_RECOVERY"),
    )
    allow_historical = any(
        (value or "").strip().lower() in {"1", "true", "yes", "y"}
        for value in historical_flags
    )
    last_processed = os.environ.get("LAST_PROCESSED_EVENT_TIME")
    if event_time and is_antiquated_event(event_time) and not allow_historical:
        print(
            "[SKIP] Event time {} is antiquated and no historical replay flag is enabled".format(event_time),
            file=sys.stderr,
        )
        return False
    if event_time and last_processed and is_older_than_last_processed(event_time, last_processed) and not allow_historical:
        print(f"[SKIP] Event time {event_time} is older than LAST_PROCESSED_EVENT_TIME {last_processed}", file=sys.stderr)
        return False

    # Reject messages with timestamps older than the latest known message in this topic (prevents duplicate storms).
    if event_time and target:
        latest_stored = get_latest_topic_timestamp(target, topic_id)
        if latest_stored and event_time < latest_stored:
            print(f"[SKIP] Event time {event_time} is older than latest stored {latest_stored} for topic {topic_id}", file=sys.stderr)
            return False

    payload = {
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
    }
    if topic_id:
        # Convert topic_id to int if possible; Telegram API expects integer for message_thread_id
        try:
            payload["message_thread_id"] = int(str(topic_id).split('/')[-1])
        except (ValueError, AttributeError):
            payload["message_thread_id"] = str(topic_id)

    import json
    import urllib.request
    from urllib import error as urlerror

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Debug: log the payload being sent
    print(f"[DEBUG] Telegram payload: {json.dumps(payload)[:500]}...", file=sys.stderr)
    
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
                print(f"[OK] Telegram message posted to chat {target} topic {topic_id}", file=sys.stderr)
                if on_success is not None:
                    try:
                        on_success(json.loads(body))
                    except (json.JSONDecodeError, TypeError):
                        pass
                return True
            print(f"[ERROR] Telegram API response missing 'ok': {body[:200]}", file=sys.stderr)
            return False
    except urlerror.HTTPError as http_exc:
        # Capture HTTP error details including response body
        try:
            error_body = http_exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = "(unable to read error body)"
        print(f"[ERROR] Telegram HTTP {http_exc.code}: {error_body[:500]}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[ERROR] Telegram POST failed: {exc}", file=sys.stderr)
        return False


def send_low_stock_alert(
    message: str,
    bot_token: str | None,
    chat_id: str | None,
    topic_id: str | None,
    event_time: str | None = None,
    state_path: str | os.PathLike[str] | None = None,
) -> bool:
    state = load_alert_state(state_path)
    message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    previous_message_id = state.get("message_id")
    same_topic = state.get("chat_id") == chat_id and state.get("topic_id") == topic_id
    if same_topic and state.get("message_hash") == message_hash:
        print("[OK] Low stock alert is unchanged; keeping the existing message", file=sys.stderr)
        return True

    new_message_id: dict[str, Any] = {}

    def capture_message_id(body: dict[str, Any]) -> None:
        result = body.get("result") or {}
        if isinstance(result, dict) and result.get("message_id") is not None:
            new_message_id["message_id"] = result["message_id"]

    success = telegram_send_message(
        message,
        bot_token=bot_token,
        chat_id=chat_id,
        topic_id=topic_id,
        event_time=event_time,
        on_success=capture_message_id,
    )
    if success and new_message_id.get("message_id") is not None and chat_id:
        if previous_message_id and bot_token and same_topic:
            telegram_delete_message(bot_token, chat_id, previous_message_id)
        save_alert_state(
            {
                "chat_id": chat_id,
                "topic_id": topic_id,
                "message_id": new_message_id["message_id"],
                "message_hash": message_hash,
            },
            state_path,
        )
    return success


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
        success = send_low_stock_alert(
            message,
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
            topic_id=os.environ.get("TELEGRAM_INVENTORY_TOPIC_ID"),
            event_time=event_time,
        )
        if not success:
            print("[WARN] Low stock alert generated but failed to post to Telegram", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
