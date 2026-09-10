#!/usr/bin/env bash

set -euo pipefail

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN is required}"
: "${TELEGRAM_COMMAND_OFFSET_FILE:?TELEGRAM_COMMAND_OFFSET_FILE must point to persistent storage}"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

exec env PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
	python3 "$SCRIPT_DIR/telegram_lowstock_bot.py"