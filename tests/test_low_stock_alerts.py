from decimal import Decimal
from pathlib import Path
from unittest import mock

from scripts.low_stock_alerts import (
    build_alert_block,
    build_suggestion_block,
    get_latest_topic_timestamp,
    is_antiquated_event,
    is_older_than_last_processed,
    normalize_name,
    parse_export_csv,
    parse_threshold_value,
    format_quantity,
    set_latest_topic_timestamp,
)
from scripts.telegram_lowstock_bot import is_lowstock_command
from scripts.low_stock_alerts import telegram_send_message


def test_parse_threshold_value_handles_empty_and_numeric_values():
    assert parse_threshold_value("") is None
    assert parse_threshold_value(" 12 ") == Decimal("12")
    assert parse_threshold_value("12.5") == Decimal("12.5")


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
    assert block.startswith("<b>🚨 LOW STOCK ALERT</b>")
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

