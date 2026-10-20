#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG_DIR="$HOME/.config/smart-medicine-k1"
UNIT_DIR="$HOME/.config/systemd/user"

command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }
test -x /usr/bin/python3 || { echo "/usr/bin/python3 is required" >&2; exit 1; }
test -x /usr/bin/llama-server || { echo "Install the SpaceMIT llama-server package first" >&2; exit 1; }
test -f "$ROOT_DIR/backend/llm/model_profiles.json" || { echo "Run this script from a complete checkout" >&2; exit 1; }

mkdir -p "$CONFIG_DIR" "$UNIT_DIR"
printf 'PROJECT_ROOT="%s"\n' "$ROOT_DIR" > "$CONFIG_DIR/platform.env"
chmod 600 "$CONFIG_DIR/platform.env"

units=(
  smart-medicine-llm.service
  smart-medicine-qa.service
  smart-medicine-usb-audio.service
  smart-medicine-gateway.service
  smart-medicine-asr.service
  smart-medicine-kiosk.service
)
for unit in "${units[@]}"; do
  install -m 0644 "$ROOT_DIR/deployment/systemd/$unit" "$UNIT_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now smart-medicine-llm.service smart-medicine-qa.service smart-medicine-usb-audio.service smart-medicine-gateway.service

echo "Core services enabled. ASR is installed but not enabled; enable it after its vendor runtime and microphone are ready."
echo "For automatic startup without an interactive user session, configure systemd linger for this user separately."
