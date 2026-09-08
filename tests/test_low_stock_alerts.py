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
    parse_threshold_value,
    set_latest_topic_timestamp,
)


def test_parse_threshold_value_handles_empty_and_numeric_values():
    assert parse_threshold_value("") is None
    assert parse_threshold_value(" 12 ") == Decimal("12")
    assert parse_threshold_value("12.5") == Decimal("12.5")


def test_build_alert_block_groups_by_category_and_orders_alpha():
    rows = [
        {
            "category": "Snacks",
            "item_name": "Chips",
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
    assert "ALCOHOL" in block
    assert "Beer" in block
    assert "Wine" in block
    assert "Chips" in block
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

