#!/usr/bin/env python3
"""Resolve Smart Medicine USB audio roles from USB hardware identity.

ALSA card numbers are assigned by enumeration order and must never be treated
as the microphone/speaker identity.  This process maps the two known devices
from VID:PID (+ speaker serial), writes a runtime status file, keeps PipeWire's
default sink on the USB speaker, and supplies role-specific environment values
when launching a service.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "audio_roles.json"
RUNTIME_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "smart-medicine-audio-roles.json"
CARD_RE = re.compile(r"card(\d+)$")
# wpctl status renders a tree prefix (for example "│      68."), so the
# numeric node token must be found within the line instead of at its start.
NODE_RE = re.compile(r"(?:\*\s*)?(\d+)\.\s+(.*?)\s*$")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def usb_identity(card_path: Path) -> dict[str, str]:
    node = (card_path / "device").resolve()
    for candidate in (node, *node.parents):
        vendor = read_text(candidate / "idVendor").lower()
        product = read_text(candidate / "idProduct").lower()
        if vendor and product:
            return {"vendor_id": vendor, "product_id": product, "serial": read_text(candidate / "serial")}
    return {"vendor_id": "", "product_id": "", "serial": ""}


def card_capabilities(card_index: int) -> tuple[bool, bool]:
    stream = read_text(Path(f"/proc/asound/card{card_index}/stream0"))
    return "Capture:" in stream, "Playback:" in stream


def enumerate_cards() -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    for card_path in sorted(Path("/sys/class/sound").glob("card[0-9]*")):
        match = CARD_RE.match(card_path.name)
        if not match:
            continue
        index = int(match.group(1))
        capture, playback = card_capabilities(index)
        identity = usb_identity(card_path)
        cards.append({
            "card_index": index,
            "alsa_card_id": read_text(Path(f"/proc/asound/card{index}/id")),
            "capture": capture,
            "playback": playback,
            **identity,
        })
    return cards


def matches(card: dict[str, object], rule: dict[str, object]) -> bool:
    if str(card["vendor_id"]).lower() != str(rule.get("vendor_id", "")).lower():
        return False
    if str(card["product_id"]).lower() != str(rule.get("product_id", "")).lower():
        return False
    serial = str(rule.get("serial", ""))
    if serial and str(card["serial"]) != serial:
        return False
    if rule.get("require_capture") and not card["capture"]:
        return False
    if rule.get("require_playback") and not card["playback"]:
        return False
    return True


def resolve_roles(required_roles: set[str] | None = None) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cards = enumerate_cards()
    resolved: dict[str, dict[str, object]] = {}
    for role, rule in config["roles"].items():
        if required_roles is not None and role not in required_roles:
            continue
        matched = [card for card in cards if matches(card, rule)]
        if len(matched) != 1:
            raise RuntimeError(f"audio_role_{role}_expected_one_found_{len(matched)}")
        resolved[role] = {**matched[0], "rule": rule}
    return config, resolved


def write_status(config: dict[str, object] | None, roles: dict[str, dict[str, object]] | None, error: str = "") -> None:
    RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"ok": not error, "error": error, "roles": roles or {}}
    if config:
        payload["version"] = config.get("version")
    temporary = RUNTIME_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(RUNTIME_PATH)


def pipewire_speaker_node(rule: dict[str, object]) -> str:
    needle = str(rule.get("pipewire_sink_match", ""))
    if not needle:
        return ""
    env = {**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")}
    result = subprocess.run(["wpctl", "status", "-n"], capture_output=True, text=True, env=env, check=False)
    in_sinks = False
    for line in result.stdout.splitlines():
        if "Sinks:" in line:
            in_sinks = True
            continue
        if in_sinks and "Sink endpoints:" in line:
            break
        if in_sinks and needle in line:
            found = NODE_RE.search(line)
            if found:
                return found.group(1)
    return ""


def set_usb_speaker_default(speaker: dict[str, object]) -> bool:
    node_id = pipewire_speaker_node(dict(speaker["rule"]))
    if not node_id:
        return False
    env = {**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")}
    return subprocess.run(["wpctl", "set-default", node_id], env=env, check=False).returncode == 0


def asr_is_busy() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/asr/status", timeout=0.35) as response:
            status = json.loads(response.read().decode("utf-8"))
        return status.get("recording", {}).get("state") in {"recording", "stopping"} or status.get("recognition", {}).get("state") == "running"
    except Exception:
        # Unknown is not idle.  Failing safe prevents a transient status error
        # from restarting ASR while it is recording or recognizing.
        return True


def restart_asr_if_idle() -> bool:
    if asr_is_busy():
        return False
    return subprocess.run(["systemctl", "--user", "try-restart", "smart-medicine-asr.service"], check=False).returncode == 0


def role_environment(role: str, mapping: dict[str, object]) -> dict[str, str]:
    card_index = str(mapping["card_index"])
    if role == "microphone":
        return {"K1_ASR_AUDIO_DEVICE": f"plughw:{card_index},0", "K1_ASR_MIXER_CARD": card_index}
    if role == "speaker":
        return {"K1_TTS_AUDIO_DEVICE": f"plughw:{card_index},0"}
    raise RuntimeError(f"unsupported_audio_role:{role}")


def signature(roles: dict[str, dict[str, object]]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((name, int(value["card_index"])) for name, value in roles.items()))


def watch(interval: float) -> int:
    previous: tuple[tuple[str, int], ...] | None = None
    while True:
        try:
            config, roles = resolve_roles()
            write_status(config, roles)
            set_usb_speaker_default(roles["speaker"])
            current = signature(roles)
            if previous is not None and current != previous:
                restart_asr_if_idle()
            previous = current
        except Exception as exc:
            write_status(None, None, str(exc))
        time.sleep(interval)


def main(argv: list[str]) -> int:
    command: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        command, argv = argv[split + 1 :], argv[:split]
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--exec-role", choices=("microphone", "speaker"))
    args = parser.parse_args(argv)
    if args.watch:
        return watch(max(0.5, args.interval))
    try:
        if args.exec_role:
            if not command:
                raise RuntimeError("missing_exec_command")
            config, roles = resolve_roles({args.exec_role})
            write_status(config, roles)
            env = {**os.environ, **role_environment(args.exec_role, roles[args.exec_role])}
            os.execvpe(command[0], command, env)
        config, roles = resolve_roles()
        write_status(config, roles)
        if args.status:
            print(json.dumps({"ok": True, "roles": roles}, ensure_ascii=False, indent=2))
            return 0
        set_usb_speaker_default(roles["speaker"])
        return 0
    except Exception as exc:
        write_status(None, None, str(exc))
        print(f"audio-role-manager: {exc}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
