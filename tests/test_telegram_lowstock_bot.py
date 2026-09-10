import json
from unittest import mock

from scripts.telegram_lowstock_bot import (
    is_stale_command,
    process_update,
    resolve_startup_offset,
)


def _fake_telegram_response(payload: dict) -> mock.MagicMock:
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def test_is_stale_command_rejects_old_and_accepts_recent():
    now = 1_700_000_000
    assert is_stale_command({"date": now - 301}, max_age_seconds=300, now=now) is True
    assert is_stale_command({"date": now - 100}, max_age_seconds=300, now=now) is False


def test_is_stale_command_allows_messages_without_a_date():
    assert is_stale_command({}, max_age_seconds=300) is False


def test_process_update_skips_stale_lowstock_command_without_sending():
    now = 1_700_000_000
    update = {
        "update_id": 1,
        "message": {"text": "/lowstock", "date": now - 3600, "chat": {"id": 123}},
    }
    with mock.patch("scripts.telegram_lowstock_bot.telegram_send_message") as send:
        handled = process_update(
            update,
            csv_path=None,
            api_token=None,
            base_url="https://api.loyverse.com/v1.0",
            max_command_age_seconds=300,
        )
    assert handled is False
    send.assert_not_called()


def test_process_update_sends_recent_lowstock_command_to_originating_topic():
    update = {
        "update_id": 2,
        "message": {
            "text": "/lowstock",
            "chat": {"id": 123},
            "message_thread_id": 456,
        },
    }
    with (
        mock.patch(
            "scripts.telegram_lowstock_bot.build_daily_low_stock_message",
            return_value="current alert",
        ),
        mock.patch(
            "scripts.telegram_lowstock_bot.send_low_stock_alert",
            return_value=True,
        ) as send,
    ):
        handled = process_update(
            update,
            csv_path="items.csv",
            api_token="loyverse-token",
            base_url="https://api.loyverse.com/v1.0",
            bot_token="telegram-token",
            max_command_age_seconds=300,
        )

    assert handled is True
    send.assert_called_once_with(
        "current alert",
        bot_token="telegram-token",
        chat_id="123",
        topic_id="456",
    )


def test_resolve_startup_offset_persists_across_restarts(tmp_path):
    offset_path = tmp_path / ".telegram_lowstock_offset"

    with mock.patch("scripts.telegram_lowstock_bot.telegram_api_call") as api_call:
        api_call.return_value = {"ok": True, "result": [{"update_id": 41}, {"update_id": 42}]}
        offset = resolve_startup_offset("token", offset_path)

    assert offset == 43
    assert offset_path.read_text().strip() == "43"

    # Simulated restart: the persisted offset is reused without another API probe.
    with mock.patch("scripts.telegram_lowstock_bot.telegram_api_call") as api_call:
        offset_again = resolve_startup_offset("token", offset_path)
        api_call.assert_not_called()

    assert offset_again == 43


def test_resolve_startup_offset_defaults_to_zero_with_no_backlog(tmp_path):
    offset_path = tmp_path / ".telegram_lowstock_offset"

    with mock.patch("scripts.telegram_lowstock_bot.telegram_api_call") as api_call:
        api_call.return_value = {"ok": True, "result": []}
        offset = resolve_startup_offset("token", offset_path)

    assert offset == 0


def test_offset_survives_a_restart_over_the_real_http_layer(tmp_path):
    """End-to-end persistence check: mock urlopen (not the wrapper) so real JSON
    encode/decode runs, then reload the offset from disk as a fresh process would."""
    offset_path = tmp_path / ".telegram_lowstock_offset"

    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _fake_telegram_response(
            {"ok": True, "result": [{"update_id": 7, "message": {"text": "/lowstock", "date": 1_700_000_000, "chat": {"id": 1}}}]}
        )
        offset = resolve_startup_offset("token", offset_path)

    assert offset == 8
    assert offset_path.read_text().strip() == "8"

    # Fresh "process" reload: no network call needed once the file exists.
    with mock.patch("urllib.request.urlopen") as urlopen:
        reloaded_offset = resolve_startup_offset("token", offset_path)
        urlopen.assert_not_called()

    assert reloaded_offset == 8
