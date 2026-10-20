#!/usr/bin/env bash
set -euo pipefail

browser=${KIOSK_BROWSER:-}
if [[ -z "$browser" ]]; then
  browser=$(command -v chromium-browser || command -v chromium || true)
fi
if [[ -z "$browser" || ! -x "$browser" ]]; then
  echo "Set KIOSK_BROWSER to an installed Chromium-compatible browser" >&2
  exit 1
fi

profile_dir=${KIOSK_PROFILE_DIR:-"${XDG_CACHE_HOME:-$HOME/.cache}/smart-medicine-kiosk"}
mkdir -p "$profile_dir"
url=${KIOSK_URL:-http://127.0.0.1:8799/ui/}
exec "$browser" --kiosk --app="$url" --user-data-dir="$profile_dir" --no-first-run
