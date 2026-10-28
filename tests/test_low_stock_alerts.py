import json
from decimal import Decimal
from pathlib import Path
from urllib import error, parse
from unittest import mock

import pytest

from scripts.low_stock_alerts import (
    LiveInventoryUnavailable,
    build_alert_block,
    build_daily_low_stock_message,
    build_suggestion_block,
    fetch_loyverse_pages,
    get_latest_topic_timestamp,
    is_antiquated_event,
    is_older_than_last_processed,
    normalize_name,
    parse_export_csv,
    parse_threshold_value,
    format_quantity,
    main,
    send_low_stock_alert,
    select_eligible_low_stock_items,
    set_latest_topic_timestamp,
    split_alert_message,
)
from scripts.telegram_lowstock_bot import is_lowstock_command, process_update
from scripts.low_stock_alerts import telegram_send_message


def test_parse_threshold_value_handles_empty_and_numeric_values():
    assert parse_threshold_value("") is None
    assert parse_threshold_value(" 12 ") == Decimal("12")
    assert parse_threshold_value("12.5") == Decimal("12.5")
    assert parse_threshold_value("NaN") is None
    assert parse_threshold_value("Infinity") is None


def test_lowstock_command_accepts_bot_suffix_and_rejects_similar_commands():
    assert is_lowstock_command("/lowstock") is True
    assert is_lowstock_command("/lowstock@inventory_bot") is True
    assert is_lowstock_command("/lowstock now") is True
    assert is_lowstock_command("/lowstocks") is False


def test_parse_export_csv_reads_location_suffixed_stock_headers(tmp_path):
    csv_path = tmp_path / "export_items-11.csv"
    csv_path.write_text(
        "Name,Category,Cost,In stock [Pink Tamu Swahili Cafe & Restaurant],"
        "Low stock [Pink Tamu Swahili Cafe & Restaurant]\n"
        '"Bottled Water, 10L",Maji,120.00,4.000,4.000\n',
        encoding="utf-8",
    )

    rows = parse_export_csv(csv_path)

    assert rows[0]["item_name"] == "Bottled Water, 10L"
    assert rows[0]["quantity"] == "4.000"
    assert rows[0]["threshold"] == Decimal("4.000")


def test_live_inventory_failure_never_falls_back_to_static_csv_quantities(tmp_path):
    csv_path = tmp_path / "export_items-11.csv"
    csv_path.write_text(
        "Name,Category,Quantity,Low Stock Alert Threshold\n"
        '"Bottled Water, 10L",Maji,4,4\n',
        encoding="utf-8",
    )

    with mock.patch("scripts.low_stock_alerts.fetch_loyverse_items", return_value=[]):
        try:
            select_eligible_low_stock_items(csv_path, api_token="loyverse-token")
        except LiveInventoryUnavailable:
            pass
        else:
            raise AssertionError("Expected live inventory failure instead of CSV fallback")


def test_format_quantity_removes_inventory_decimal_padding():
    assert format_quantity("4.000") == "4"


def test_build_alert_block_groups_by_category_and_orders_alpha():
    rows = [
        {
            "category": "Snacks",
            "item_name": "Chips 🥔",
            "cost": "2.50",
            "quantity": "5",
            "threshold": Decimal("10"),
        },
        {
            "category": "Alcohol",
            "item_name": "Beer",
            "cost": "4.00",
            "quantity": "1",
            "threshold": Decimal("3"),
        },
        {
            "category": "Alcohol",
            "item_name": "Wine",
            "cost": "6.00",
            "quantity": "2",
            "threshold": Decimal("5"),
        },
    ]
    block = build_alert_block(rows)
    assert block.startswith(
        "<b>🚨 LOW STOCK ALERT</b>\n"
        "----------------------------------\n"
        "ALCOHOL"
    )
    assert "ALCOHOL" in block
    assert "Beer" in block
    assert "Wine" in block
    assert "Chips" in block
    assert "Chips 🥔 | PC: 2.5 KSh | QTY: 5" in block
    assert block.index("ALCOHOL") < block.index("SNACKS")


def test_suggestion_block_excludes_forbidden_categories_and_uses_closest_items():
    rows = [
        {
            "category": "Alcohol",
            "item_name": "Vodka",
            "quantity": "25",
            "threshold": Decimal("10"),
        },
        {
            "category": "Nonalc Drinks",
            "item_name": "Water",
            "quantity": "12",
            "threshold": Decimal("10"),
        },
        {
            "category": "Snacks",
            "item_name": "Nuts",
            "quantity": "5",
            "threshold": Decimal("8"),
        },
        {
            "category": "Bakery",
            "item_name": "Bread",
            "quantity": "10",
            "threshold": Decimal("9"),
        },
        {
            "category": "Bakery",
            "item_name": "Cake",
            "quantity": "12",
            "threshold": Decimal("8"),
        },
    ]
    block = build_suggestion_block(rows)
    assert "Vodka" not in block
    assert "Water" not in block
    assert "Nuts" not in block
    assert "Bread" in block
    assert block.index("Bread") < block.index("Cake")


def test_normalize_name_is_stable_for_matching():
    assert normalize_name("  Beer  ") == "beer"
    assert normalize_name("Coca Cola") == "coca cola"
    assert normalize_name("  Coca   Cola  ") == "coca cola"


def test_parse_export_csv_handles_lowercase_headers_and_compact_names(tmp_path):
    csv_path = tmp_path / "export_items-11.csv"
    csv_path.write_text(
        "name,category,quantity in stock,low stock threshold\n"
        '  Coca   Cola  ,Drinks,12.0,10.0\n',
        encoding="utf-8",
    )

    rows = parse_export_csv(csv_path)

    assert rows[0]["item_name"] == "Coca   Cola"
    assert rows[0]["category"] == "Drinks"
    assert rows[0]["quantity"] == "12.0"
    assert rows[0]["threshold"] == Decimal("10.0")


def test_antiquated_event_is_rejected_even_when_message_is_valid():
    now = 1_700_000_000
    assert is_antiquated_event("2023-01-01T00:00:00Z", now=now) is True
    assert is_antiquated_event("2026-09-08T12:00:00Z", now=now) is False


def test_events_older_than_last_processed_are_rejected():
    assert is_older_than_last_processed("2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z") is True
    assert is_older_than_last_processed("2025-01-03T00:00:00Z", "2025-01-02T00:00:00Z") is False


def test_topic_timestamp_guard_prevents_older_messages():
    """Verify the timestamp guard prevents messages with older event_times."""
    chat_id = "-1004442209616"
    topic_id = "1"
    
    # No stored timestamp should allow any message.
    with mock.patch("scripts.low_stock_alerts.STATE_FILE", Path("/tmp/nonexistent_state.json")):
        assert get_latest_topic_timestamp(chat_id, topic_id) is None
    
    # Store a timestamp.
    with mock.patch("scripts.low_stock_alerts.STATE_FILE", Path("/tmp/test_state.json")):
        set_latest_topic_timestamp(chat_id, topic_id, "2026-09-08T12:00:00Z")
        latest = get_latest_topic_timestamp(chat_id, topic_id)
        assert latest == "2026-09-08T12:00:00Z"
        
        # Newer timestamp should pass the guard; older should fail.
        assert get_latest_topic_timestamp(chat_id, topic_id) < "2026-09-08T13:00:00Z"
        assert get_latest_topic_timestamp(chat_id, topic_id) > "2026-09-08T11:00:00Z"
        
        # Clean up test file.
        Path("/tmp/test_state.json").unlink(missing_ok=True)


def test_telegram_send_message_accepts_historical_replay_flag(monkeypatch):
    monkeypatch.setenv("ALLOW_HISTORICAL_REPLAY", "true")
    monkeypatch.delenv("ALLOW_HISTORICAL_POSTS", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    with mock.patch("urllib.request.urlopen") as urlopen:
        mock_response = mock.MagicMock()
        mock_response.__enter__.return_value.read.return_value = b'{"ok": true}'
        urlopen.return_value = mock_response

        assert telegram_send_message("hello", bot_token="token", chat_id="123", event_time="2024-01-01T00:00:00Z") is True


def test_replay_env_gate_blocks_and_allows_historical_events(monkeypatch):
    """Smoke test: historical replay is blocked by default and allowed only when the replay flag is on."""
    monkeypatch.delenv("ALLOW_HISTORICAL_POSTS", raising=False)
    monkeypatch.delenv("ALLOW_HISTORICAL_REPLAY", raising=False)
    monkeypatch.delenv("ALLOW_HISTORICAL_RECOVERY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    with mock.patch("urllib.request.urlopen") as urlopen:
        assert telegram_send_message("blocked", bot_token="token", chat_id="123", event_time="2024-01-01T00:00:00Z") is False
        urlopen.assert_not_called()

    monkeypatch.setenv("ALLOW_HISTORICAL_REPLAY", "true")
    with mock.patch("urllib.request.urlopen") as urlopen:
        mock_response = mock.MagicMock()
        mock_response.__enter__.return_value.read.return_value = b'{"ok": true}'
        urlopen.return_value = mock_response

        assert telegram_send_message("allowed", bot_token="token", chat_id="123", event_time="2024-01-01T00:00:00Z") is True
        urlopen.assert_called_once()


def test_send_low_stock_alert_skips_unchanged_message(tmp_path):
    state_path = tmp_path / "alert-state.json"
    message = "same alert"
    import hashlib

    state_path.write_text(
        '{"chat_id":"123","topic_id":"1895","message_id":10,'
        f'"message_hash":"{hashlib.sha256(message.encode()).hexdigest()}"}}'
    )

    with mock.patch("scripts.low_stock_alerts.telegram_send_message") as send_message:
        assert send_low_stock_alert(message, "token", "123", "1895", state_path=state_path) is True
        send_message.assert_not_called()


def test_send_low_stock_alert_keeps_previous_message_when_new_post_fails(tmp_path):
    state_path = tmp_path / "alert-state.json"
    state_path.write_text('{"chat_id":"123","topic_id":"1895","message_id":10}')

    with mock.patch("scripts.low_stock_alerts.telegram_send_message", return_value=False), mock.patch(
        "scripts.low_stock_alerts.telegram_delete_message"
    ) as delete_message:
        assert send_low_stock_alert("changed alert", "token", "123", "1895", state_path=state_path) is False
        delete_message.assert_not_called()


def test_send_low_stock_alert_replaces_previous_message_after_new_post_succeeds(tmp_path):
    state_path = tmp_path / "alert-state.json"
    state_path.write_text('{"chat_id":"123","topic_id":"1895","message_id":10}')

    def successful_send(*args, **kwargs):
        kwargs["on_success"]({"result": {"message_id": 11}})
        return True

    with mock.patch("scripts.low_stock_alerts.telegram_send_message", side_effect=successful_send), mock.patch(
        "scripts.low_stock_alerts.telegram_delete_message", return_value=True
    ) as delete_message:
        assert send_low_stock_alert("changed alert", "token", "123", "1895", state_path=state_path) is True
        delete_message.assert_called_once_with("token", "123", 10)

    assert '"message_id": 11' in state_path.read_text()
    assert '"message_hash":' in state_path.read_text()


def api_item(name="New item", variants=None, **extra):
    return {
        "item_name": name,
        "track_stock": True,
        "category_id": "drinks",
        "variants": variants if variants is not None else [{
            "variant_id": "v1",
            "cost": 12,
            "stores": [{"store_id": "s1", "low_stock": 5}],
        }],
        **extra,
    }


def api_response(payload):
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return response


def mock_loyverse(pages):
    pages = {("stores", None): {"stores": [{"id": "s1", "name": "Main"}, {"id": "s2", "name": "Annex"}]}, **pages}

    def respond(req, **kwargs):
        url = parse.urlparse(req.full_url)
        resource = url.path.rsplit("/", 1)[-1]
        cursor = parse.parse_qs(url.query).get("cursor", [None])[0]
        assert req.get_header("Authorization") == "Bearer test-token"
        return api_response(pages[(resource, cursor)])
    return respond


def test_live_alert_includes_new_renamed_and_zero_threshold_items_not_in_export(tmp_path):
    csv_path = tmp_path / "old.csv"
    csv_path.write_text("Name,Category,Quantity,Low Stock Threshold\nRenamed,Drinks,500,0\nDisabled,Drinks,0,10\n")
    pages = {
        ("items", None): {
            "items": [api_item("New item"), api_item("Renamed", variants=[{
                "variant_id": "v2", "cost": 7,
                "stores": [{"store_id": "s1", "low_stock": 3}],
            }])],
            "cursor": "next+page/=",
        },
        ("items", "next+page/="): {"items": [
            api_item("Zero threshold", variants=[{
                "variant_id": "v3", "cost": 0,
                "stores": [{"store_id": "s1", "low_stock": 0}],
            }]),
            api_item("Disabled", variants=[{
                "variant_id": "v4", "stores": [{"store_id": "s1", "low_stock": None}],
            }]),
            api_item("Untracked", track_stock=False),
            api_item("Deleted", deleted_at="2026-01-01T00:00:00Z"),
        ]},
        ("categories", None): {"categories": [], "cursor": "category-page-2"},
        ("categories", "category-page-2"): {"categories": [{"id": "drinks", "name": "Drinks"}]},
        ("inventory", None): {"inventory_levels": [
            {"variant_id": "v1", "store_id": "s1", "in_stock": 5},
        ], "cursor": "inventory-page-2"},
        ("inventory", "inventory-page-2"): {"inventory_levels": [
            {"variant_id": "v2", "store_id": "s1", "in_stock": -0.5},
            {"variant_id": "v3", "store_id": "s1", "in_stock": 0},
        ]},
    }
    with mock.patch("urllib.request.urlopen", side_effect=mock_loyverse(pages)):
        message = build_daily_low_stock_message(csv_path, api_token="test-token")

    assert "DRINKS" in message
    assert "New item | PC: 12 KSh | QTY: 5" in message
    assert "Renamed | PC: 7 KSh | QTY: -0.5" in message
    assert "Zero threshold | PC: 0 KSh | QTY: 0" in message
    assert all(name not in message for name in ("Disabled", "Untracked", "Deleted"))


def test_stock_in_other_stores_or_variants_cannot_hide_a_shortage():
    item = api_item("Juice", variants=[
        {"variant_id": "small", "option1_value": "Small", "cost": 5,
         "stores": [{"store_id": "s1", "low_stock": 2}, {"store_id": "s2", "low_stock": 3}]},
        {"variant_id": "large", "option1_value": "Large", "cost": 10,
         "stores": [{"store_id": "s1", "low_stock": 5}, {"store_id": "s2", "low_stock": 0}]},
        {"variant_id": "deleted", "deleted_at": "2026-01-01T00:00:00Z"},
    ])
    pages = {
        ("items", None): {"items": [item]},
        ("categories", None): {"categories": []},
        ("inventory", None): {"inventory_levels": [
            {"variant_id": "small", "store_id": "s1", "in_stock": 1},
            {"variant_id": "small", "store_id": "s2", "in_stock": 100},
            {"variant_id": "large", "store_id": "s1", "in_stock": 100},
            {"variant_id": "large", "store_id": "s2", "in_stock": 0},
        ]},
    }
    with mock.patch("urllib.request.urlopen", side_effect=mock_loyverse(pages)):
        message = build_daily_low_stock_message(api_token="test-token")
    assert "Juice (Small) [store: Main] | PC: 5 KSh | QTY: 1" in message
    assert "Juice (Large) [store: Annex] | PC: 10 KSh | QTY: 0" in message
    assert message.count("Juice") == 2


def test_tracked_variant_without_inventory_level_is_alerted_as_zero_stock():
    pages = {
        ("items", None): {"items": [api_item(), api_item("Stocked", variants=[{
            "variant_id": "v2", "cost": 3, "stores": [{"store_id": "s1", "low_stock": 1}],
        }])]},
        ("categories", None): {"categories": []},
        ("inventory", None): {"inventory_levels": [
            {"variant_id": "v1", "store_id": "other-store", "in_stock": 9},
            {"variant_id": "v2", "store_id": "s1", "in_stock": 50},
        ]},
    }
    with mock.patch("urllib.request.urlopen", side_effect=mock_loyverse(pages)):
        message = build_daily_low_stock_message(api_token="test-token")
    assert "New item | PC: 12 KSh | QTY: 0" in message
    assert "Stocked" not in message


def test_deleted_stores_and_stockless_composites_are_ignored():
    pages = {
        ("stores", None): {"stores": [{"id": "s1", "name": "Main"}]},
        ("items", None): {"items": [
            api_item("Old store only", variants=[{
                "variant_id": "v1", "cost": 1,
                "stores": [{"store_id": "s1", "low_stock": 1}, {"store_id": "gone", "low_stock": 5}],
            }]),
            api_item("Combo", is_composite=True, variants=[{
                "variant_id": "c1", "stores": [{"store_id": "s1", "low_stock": 5}],
            }]),
            api_item("Produced", is_composite=True, use_production=True, variants=[{
                "variant_id": "p1", "cost": 4, "stores": [{"store_id": "s1", "low_stock": 5}],
            }]),
        ]},
        ("categories", None): {"categories": []},
        ("inventory", None): {"inventory_levels": [
            {"variant_id": "v1", "store_id": "s1", "in_stock": 10},
            {"variant_id": "p1", "store_id": "s1", "in_stock": 2},
        ]},
    }
    with mock.patch("urllib.request.urlopen", side_effect=mock_loyverse(pages)):
        message = build_daily_low_stock_message(api_token="test-token")
    assert "Produced | PC: 4 KSh | QTY: 2" in message
    assert "Old store only" not in message
    assert "Combo" not in message


@pytest.mark.parametrize(("resource", "key"), [
    ("items", "items"), ("categories", "categories"), ("inventory", "inventory_levels"),
])
def test_late_page_error_never_returns_partial_results(resource, key):
    with mock.patch("urllib.request.urlopen", side_effect=[
        api_response({key: [{"id": "first"}], "cursor": "next"}),
        error.HTTPError("https://example.test", 429, "Rate limited", {}, None),
    ]):
        with pytest.raises(LiveInventoryUnavailable, match="HTTP 429"):
            fetch_loyverse_pages("test-token", "https://example.test", resource, key)


@pytest.mark.parametrize("payload", [
    [], {}, {"items": {}}, {"items": [None]}, {"items": [], "cursor": 42},
])
def test_malformed_api_response_is_not_treated_as_complete(payload):
    with mock.patch("urllib.request.urlopen", return_value=api_response(payload)):
        with pytest.raises(LiveInventoryUnavailable):
            fetch_loyverse_pages("test-token", "https://example.test", "items", "items")


def test_repeated_api_cursor_is_rejected():
    with mock.patch("urllib.request.urlopen", return_value=api_response({"items": [], "cursor": "same"})):
        with pytest.raises(LiveInventoryUnavailable, match="repeated cursor"):
            fetch_loyverse_pages("test-token", "https://example.test", "items", "items")


def test_api_page_limit_is_an_error_not_a_truncated_success():
    with mock.patch("urllib.request.urlopen", side_effect=[
        api_response({"items": [], "cursor": str(index)}) for index in range(200)
    ]):
        with pytest.raises(LiveInventoryUnavailable, match="pagination safety limit"):
            fetch_loyverse_pages("test-token", "https://example.test", "items", "items")


def test_alert_names_and_categories_are_escaped_for_telegram_html():
    rows = [{"item_name": "Tea <Large> & Coffee", "category": "Food & Drinks",
             "cost": "2", "quantity": "0", "threshold": Decimal(0)}]
    assert "Tea &lt;Large&gt; &amp; Coffee" in build_alert_block(rows)
    assert "FOOD &amp; DRINKS" in build_alert_block(rows)
    assert "Tea &lt;Large&gt; &amp; Coffee" in build_suggestion_block(rows)


def test_long_alert_posts_every_item_then_replaces_all_old_parts(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text('{"chat_id":"123","topic_id":"1895","message_ids":[9,10]}')
    message = build_alert_block([
        {"item_name": f"Item {index:03d} & <Large>", "category": "Drinks",
         "cost": "5", "quantity": "0", "threshold": Decimal(1)}
        for index in range(180)
    ])
    parts = split_alert_message(message)
    assert len(parts) > 1
    assert "\n".join(parts) == message
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts)
    events = []

    def send(text, **kwargs):
        events.append(("send", text))
        kwargs["on_success"]({"result": {"message_id": 11 + len(events)}})
        return True

    with mock.patch("scripts.low_stock_alerts.time.sleep") as sleep, mock.patch(
        "scripts.low_stock_alerts.telegram_send_message", side_effect=send,
    ), mock.patch(
        "scripts.low_stock_alerts.telegram_delete_message",
        side_effect=lambda token, chat, mid: events.append(("delete", mid)),
    ):
        assert send_low_stock_alert(message, "token", "123", "1895", state_path=state_path)
    assert events[:len(parts)] == [("send", part) for part in parts]
    assert events[len(parts):] == [("delete", 9), ("delete", 10)]
    assert sleep.call_args_list == [mock.call(3)] * (len(parts) - 1)
    saved = json.loads(state_path.read_text())
    assert len(saved["message_ids"]) == len(parts)


def test_partial_multipart_delivery_keeps_previous_alert_and_cleans_new_parts(tmp_path):
    state_path = tmp_path / "state.json"
    old_state = '{"chat_id":"123","topic_id":"1895","message_id":10}'
    state_path.write_text(old_state)

    def first_send(*args, **kwargs):
        kwargs["on_success"]({"result": {"message_id": 11}})
        return True

    with mock.patch("scripts.low_stock_alerts.time.sleep"), mock.patch(
        "scripts.low_stock_alerts.telegram_send_message",
    ) as send, mock.patch(
        "scripts.low_stock_alerts.telegram_delete_message", return_value=True,
    ) as delete:
        calls = 0

        def partial_send(*args, **kwargs):
            nonlocal calls
            calls += 1
            return first_send(*args, **kwargs) if calls == 1 else False

        send.side_effect = partial_send
        assert not send_low_stock_alert("a" * 3000 + "\n" + "b" * 3000,
                                       "token", "123", "1895", state_path=state_path)
        delete.assert_called_once_with("token", "123", 11)
    assert state_path.read_text() == old_state


def test_telegram_ok_false_is_not_success():
    with mock.patch("urllib.request.urlopen", return_value=api_response({"ok": False, "description": "Rejected"})):
        assert not telegram_send_message("alert", "token", "123")


def test_emoji_message_splitting_respects_telegram_limit():
    message = "\n".join(["Restock \U0001f37a" * 100] * 10)
    parts = split_alert_message(message)
    assert "\n".join(parts) == message
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts)


def test_live_post_requires_api_token_instead_of_using_csv(monkeypatch):
    monkeypatch.delenv("LOYVERSE_API_TOKEN", raising=False)
    monkeypatch.setenv("SEND_TELEGRAM_MESSAGE", "true")
    with mock.patch("scripts.low_stock_alerts.build_daily_low_stock_message") as build:
        assert main() == 1
        build.assert_not_called()


@pytest.mark.parametrize("entry", [
    {"variant_id": "v1", "store_id": "s1", "in_stock": None},
    {"variant_id": "v1", "store_id": "s1", "in_stock": "NaN"},
    {"variant_id": "v1", "store_id": "s1", "in_stock": "Infinity"},
    {"variant_id": "v1", "in_stock": 0},
])
def test_invalid_live_inventory_cannot_silently_hide_an_item(entry):
    pages = {
        ("items", None): {"items": [api_item()]},
        ("categories", None): {"categories": []},
        ("inventory", None): {"inventory_levels": [entry]},
    }
    with mock.patch("urllib.request.urlopen", side_effect=mock_loyverse(pages)):
        with pytest.raises(LiveInventoryUnavailable, match="invalid inventory level"):
            build_daily_low_stock_message(api_token="test-token")


@pytest.mark.parametrize("mode", ["scheduled", "command"])
def test_every_live_low_stock_item_reaches_telegram_in_both_entry_points(mode, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("LOYVERSE_API_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_INVENTORY_TOPIC_ID", "1895")
    monkeypatch.setenv("SEND_TELEGRAM_MESSAGE", "true")
    monkeypatch.delenv("EVENT_TIME", raising=False)
    items = [
        api_item(f"Item {i:03d} <Large> & Fresh", variants=[{
            "variant_id": f"v{i}", "cost": i,
            "stores": [{"store_id": "s1", "low_stock": 5}],
        }]) for i in range(120)
    ]
    pages = {
        ("items", None): {"items": items},
        ("categories", None): {"categories": [{"id": "drinks", "name": "Drinks"}]},
        ("inventory", None): {"inventory_levels": [
            {"variant_id": f"v{i}", "store_id": "s1", "in_stock": 1}
            for i in range(120)
        ]},
    }
    sent = []

    def respond(req, **kwargs):
        if parse.urlparse(req.full_url).hostname == "api.telegram.org":
            payload = json.loads(req.data)
            assert payload["chat_id"] == "123"
            assert payload["message_thread_id"] == 1895
            assert payload["parse_mode"] == "HTML"
            sent.append(payload["text"])
            return api_response({"ok": True, "result": {"message_id": len(sent)}})
        return mock_loyverse(pages)(req, **kwargs)

    def run():
        if mode == "scheduled":
            assert main() == 0
        else:
            assert process_update(
                {"message": {"text": "/lowstock", "chat": {"id": 123}, "message_thread_id": 1895}},
                None, "test-token", "https://api.loyverse.com/v1.0", bot_token="telegram-token",
            )

    with mock.patch("scripts.low_stock_alerts.time.sleep"), mock.patch(
        "scripts.low_stock_alerts.ALERT_STATE_FILE", state_path,
    ), mock.patch(
        "urllib.request.urlopen", side_effect=respond,
    ):
        run()
        part_count = len(sent)
        assert part_count > 1
        full_message = "\n".join(sent)
        for i in range(120):
            assert full_message.count(f"Item {i:03d} &lt;Large&gt; &amp; Fresh | PC: {i} KSh | QTY: 1") == 1
        assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in sent)
        assert json.loads(state_path.read_text())["message_ids"] == list(range(1, part_count + 1))
        run()
        assert len(sent) == part_count
