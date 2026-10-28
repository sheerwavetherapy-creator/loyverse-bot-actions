#!/usr/bin/env python3
"""Low stock alert helpers for Loyverse inventory updates.

With LOYVERSE_API_TOKEN, thresholds come from variants[].stores[].low_stock
and quantities from /inventory, joined by (variant_id, store_id). CSV values
are used only in explicit offline mode (no API token). An incomplete live read
fails without replacing the previous alert. Telegram alerts are grouped by
category and split into messages when necessary.

API schema: https://developer.loyverse.com/docs/API-Reference__v1.0.yaml
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
from html import escape
from pathlib import Path
from typing import Any, Iterable
from urllib import request, error
from urllib.parse import urlencode

STATE_FILE = Path.home() / ".telegram_state.json"

# Tracks the previously posted low-stock alert message so it can be deleted on regeneration.
ALERT_STATE_FILE = Path(os.environ.get("LOW_STOCK_ALERT_STATE_FILE") or "low_stock_alert_state.json")


INVENTORY_TOPIC_ID = "4442209616/1895"
INVENTORY_TOPIC_URL = "t.me/c/4442209616/1895"


class LiveInventoryUnavailable(RuntimeError):
    """Raised when a live-inventory alert cannot be generated safely."""

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
        number = Decimal(text)
        return number if number.is_finite() else None
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
        lines.append(escape(category.upper()))
        for row in items:
            cost = row.get("cost")
            qty = row.get("quantity")
            item_name = str(row["item_name"]).strip()
            lines.append(
                f"{escape(item_name)} | PC: {escape(format_money(cost))} KSh | QTY: {format_quantity(qty)}"
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
            f"{escape(str(row['item_name']))} | Category: {escape(category)} | Qty: {qty} | Threshold: {threshold}"
        )
    return "\n".join(lines)


def fetch_loyverse_pages(api_token: str, base_url: str, resource: str, result_key: str) -> list[dict[str, Any]]:
    """Read every page or fail: a partial catalog must never replace a complete alert."""
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
    }
    entries: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(200):
        params = {"limit": "250"}
        if cursor:
            params["cursor"] = cursor
        url = f"{base_url.rstrip('/')}/{resource}?{urlencode(params)}"
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except error.HTTPError as exc:
            raise LiveInventoryUnavailable(f"Loyverse {resource} request failed: HTTP {exc.code}") from exc
        except (error.URLError, ValueError, TimeoutError) as exc:
            raise LiveInventoryUnavailable(f"Loyverse {resource} request failed ({type(exc).__name__})") from exc
        if not isinstance(data, dict) or not isinstance(data.get(result_key), list):
            raise LiveInventoryUnavailable(f"Loyverse {resource} returned an invalid response")
        page = data[result_key]
        if any(not isinstance(entry, dict) for entry in page):
            raise LiveInventoryUnavailable(f"Loyverse {resource} returned an invalid entry")
        entries.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            return entries
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise LiveInventoryUnavailable(f"Loyverse {resource} returned an invalid or repeated cursor")
        seen_cursors.add(cursor)
    raise LiveInventoryUnavailable(f"Loyverse {resource} exceeded the pagination safety limit")


def fetch_loyverse_categories(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> dict[str, str]:
    entries = fetch_loyverse_pages(api_token, base_url, "categories", "categories")
    return {str(entry["id"]): str(entry["name"]) for entry in entries if entry.get("id") and entry.get("name")}


def fetch_loyverse_stores(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> dict[str, str]:
    """Active (non-deleted) stores; overrides for deleted stores are ignored."""
    entries = fetch_loyverse_pages(api_token, base_url, "stores", "stores")
    return {
        str(entry["id"]): str(entry.get("name") or entry["id"])
        for entry in entries
        if entry.get("id") and not entry.get("deleted_at")
    }


def fetch_loyverse_items(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> list[dict[str, Any]]:
    return fetch_loyverse_pages(api_token, base_url, "items", "items")


def normalize_api_rows(
    raw: dict[str, Any],
    category_map: dict[str, str],
    store_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Keep variant/store thresholds separate; stock elsewhere cannot cover a shortage."""
    if raw.get("deleted_at") or not raw.get("track_stock"):
        return []
    # Composite items without production hold no stock of their own; their components are tracked.
    if raw.get("is_composite") and not raw.get("use_production"):
        return []
    name = raw.get("item_name")
    variants = raw.get("variants")
    if not name or not isinstance(variants, list):
        raise LiveInventoryUnavailable("Loyverse returned a tracked item without a name or variants")
    rows: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, dict):
            raise LiveInventoryUnavailable(f"Loyverse returned an invalid variant for {name}")
        if variant.get("deleted_at"):
            continue
        stores = variant.get("stores")
        if not isinstance(stores, list) or any(not isinstance(store, dict) for store in stores):
            raise LiveInventoryUnavailable(f"Loyverse returned invalid stores for {name}")
        options = [str(variant[f"option{i}_value"]) for i in range(1, 4) if variant.get(f"option{i}_value")]
        label = f"{name} ({', '.join(options)})" if options else str(name)
        if len(variants) > 1 and not options:
            label = f"{name} ({variant.get('sku') or variant.get('variant_id')})"
        if store_names is not None:
            stores = [store for store in stores if str(store.get("store_id")) in store_names]
        for store in stores:
            if store.get("low_stock") is None:
                continue
            threshold = parse_threshold_value(store["low_stock"])
            if threshold is None or threshold < 0:
                raise LiveInventoryUnavailable(f"Loyverse returned an invalid low-stock threshold for {label}")
            variant_id, store_id = variant.get("variant_id"), store.get("store_id")
            if not variant_id or not store_id:
                raise LiveInventoryUnavailable(f"Loyverse returned missing variant/store identifiers for {label}")
            store_label = (store_names or {}).get(str(store_id), store_id)
            rows.append({
                "item_name": f"{label} [store: {store_label}]" if len(stores) > 1 else label,
                "category": category_map.get(str(raw.get("category_id")), "Uncategorized"),
                "cost": str(variant["cost"]) if variant.get("cost") is not None else "",
                "variant_id": str(variant_id),
                "store_id": str(store_id),
                "threshold": threshold,
            })
    return rows


def fetch_loyverse_inventory_levels(api_token: str, base_url: str = "https://api.loyverse.com/v1.0") -> dict[tuple[str, str], Decimal]:
    entries = fetch_loyverse_pages(api_token, base_url, "inventory", "inventory_levels")
    levels: dict[tuple[str, str], Decimal] = {}
    for entry in entries:
        variant_id, store_id = entry.get("variant_id"), entry.get("store_id")
        qty = parse_threshold_value(entry.get("in_stock"))
        if not variant_id or not store_id or qty is None:
            raise LiveInventoryUnavailable("Loyverse returned an invalid inventory level")
        key = (str(variant_id), str(store_id))
        if key in levels:
            raise LiveInventoryUnavailable("Loyverse returned duplicate inventory levels")
        levels[key] = qty
    return levels


def apply_live_inventory_quantities(rows: list[dict[str, Any]], inventory_levels: dict[tuple[str, str], Decimal]) -> list[dict[str, Any]]:
    """Join on (variant_id, store_id). The inventory read is already complete (every page
    fetched), so a tracked variant with no level has never been stocked: Loyverse shows 0."""
    updated: list[dict[str, Any]] = []
    for row in rows:
        key = (row["variant_id"], row["store_id"])
        level = inventory_levels.get(key)
        if level is None:
            print(
                f"[WARN] No Loyverse inventory level for {row['item_name']} at store {row['store_id']}; treating as 0",
                file=sys.stderr,
            )
            level = Decimal(0)
        updated.append({**row, "quantity": format_quantity(level)})
    return updated


def select_eligible_low_stock_items(csv_path: str | os.PathLike[str] | None = None, api_token: str | None = None, base_url: str | None = None) -> list[dict[str, Any]]:
    if api_token:
        resolved_base_url = base_url or "https://api.loyverse.com/v1.0"
        items = fetch_loyverse_items(api_token, resolved_base_url)
        if not items:
            raise LiveInventoryUnavailable(
                "Loyverse returned no usable items; refusing to post quantities from the static CSV export."
            )
        category_map = fetch_loyverse_categories(api_token, resolved_base_url)
        store_names = fetch_loyverse_stores(api_token, resolved_base_url)
        if not store_names:
            raise LiveInventoryUnavailable("Loyverse returned no active stores")
        rows = [row for item in items for row in normalize_api_rows(item, category_map, store_names)]
        print(f"[DEBUG] Fetched {len(items)} items; {len(rows)} variant/store thresholds from Loyverse API", file=sys.stderr)
        inventory_levels = fetch_loyverse_inventory_levels(api_token, resolved_base_url)
        if rows and not inventory_levels:
            raise LiveInventoryUnavailable(
                "Loyverse returned no live inventory levels; refusing to post quantities from the static CSV export."
            )
        print(f"[DEBUG] Fetched {len(inventory_levels)} live inventory levels from Loyverse API", file=sys.stderr)
        return apply_live_inventory_quantities(rows, inventory_levels)

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
            result = json.loads(body)
            if isinstance(result, dict) and result.get("ok") is True:
                # Update the latest timestamp for this topic to prevent future older messages.
                if event_time and target:
                    set_latest_topic_timestamp(target, topic_id, event_time)
                print(f"[OK] Telegram message posted to chat {target} topic {topic_id}", file=sys.stderr)
                if on_success is not None:
                    on_success(result)
                return True
            print(f"[ERROR] Telegram API rejected message: {body[:200]}", file=sys.stderr)
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


def split_alert_message(message: str) -> list[str]:
    """Split at line boundaries without breaking HTML tags or escaped item names."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in message.split("\n"):
        line_length = len(line.encode("utf-16-le")) // 2
        if line_length > 4096:
            raise ValueError("A low-stock alert line exceeds Telegram's 4096-character limit")
        if current and length + 1 + line_length > 4096:
            chunks.append("\n".join(current))
            current, length = [], 0
        length += line_length + (1 if current else 0)
        current.append(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


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
    previous_message_ids = state.get("message_ids") or (
        [state["message_id"]] if state.get("message_id") is not None else []
    )
    same_topic = state.get("chat_id") == chat_id and state.get("topic_id") == topic_id
    if same_topic and state.get("message_hash") == message_hash:
        print("[OK] Low stock alert is unchanged; keeping the existing message", file=sys.stderr)
        return True

    try:
        parts = split_alert_message(message)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return False
    new_message_ids: list[int | str] = []

    def capture_message_id(body: dict[str, Any]) -> None:
        result = body.get("result") or {}
        if isinstance(result, dict) and result.get("message_id") is not None:
            new_message_ids.append(result["message_id"])

    for index, part in enumerate(parts):
        if index:
            # Telegram groups allow at most 20 messages per minute.
            time.sleep(3)
        success = telegram_send_message(
            part,
            bot_token=bot_token,
            chat_id=chat_id,
            topic_id=topic_id,
            event_time=event_time,
            on_success=capture_message_id,
        )
        if not success or len(new_message_ids) != index + 1:
            print("[ERROR] Low stock alert delivery is incomplete; keeping previous alert state", file=sys.stderr)
            if bot_token and chat_id:
                for message_id in new_message_ids:
                    telegram_delete_message(bot_token, chat_id, message_id)
            return False
    if chat_id:
        if bot_token and same_topic:
            for message_id in previous_message_ids:
                telegram_delete_message(bot_token, chat_id, message_id)
        save_alert_state(
            {
                "chat_id": chat_id,
                "topic_id": topic_id,
                "message_id": new_message_ids[0],
                "message_ids": new_message_ids,
                "message_hash": message_hash,
            },
            state_path,
        )
    return True


def main() -> int:
    csv_path = os.environ.get("EXPORT_CSV_PATH")
    if csv_path is None:
        csv_path = "export_items-11.csv" if Path("export_items-11.csv").exists() else None
    api_token = os.environ.get("LOYVERSE_API_TOKEN")
    send_requested = os.environ.get("SEND_TELEGRAM_MESSAGE", "false").lower() in {"1", "true", "yes"}
    if send_requested and not api_token:
        print("[ERROR] LOYVERSE_API_TOKEN is required to post live stock alerts", file=sys.stderr)
        return 1
    base_url = os.environ.get("LOYVERSE_API_BASE_URL") or "https://api.loyverse.com/v1.0"
    try:
        message = build_daily_low_stock_message(csv_path, api_token=api_token, base_url=base_url)
    except LiveInventoryUnavailable as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(message)

    if send_requested:
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
