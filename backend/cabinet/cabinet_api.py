#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simple safe API for the K1 medicine cabinet lock-control board.

Main programs should import this file instead of operating MCP23017 registers
directly. The API keeps the hardware policy conservative:

- one door per command
- short unlock pulse only
- all lock outputs are turned off after every operation
- status reads are debounced
- emergency_stop() is always available
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from cabinet_controller import CabinetController, DOOR_COUNT


DEFAULT_PULSE_MS = 600
DEFAULT_CLOSE_TIMEOUT_S = 30.0
DEFAULT_COOLDOWN_S = 0.25


@dataclass(frozen=True)
class CabinetDoorStatus:
    door: int
    closed: bool
    raw: int

    @property
    def status(self) -> str:
        return "closed" if self.closed else "open"

    @property
    def status_zh(self) -> str:
        return "已关门" if self.closed else "未关门"

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["status"] = self.status
        data["status_zh"] = self.status_zh
        return data


@dataclass(frozen=True)
class CabinetOpenResult:
    door: int
    pulse_ms: int
    close_wait_enabled: bool
    close_confirmed: Optional[bool]
    status_after_open: CabinetDoorStatus

    @property
    def ok(self) -> bool:
        return self.close_confirmed is not False

    def to_dict(self) -> Dict[str, object]:
        return {
            "door": self.door,
            "pulse_ms": self.pulse_ms,
            "close_wait_enabled": self.close_wait_enabled,
            "close_confirmed": self.close_confirmed,
            "status_after_open": self.status_after_open.to_dict(),
            "ok": self.ok,
        }


class MedicineCabinet:
    """High-level medicine cabinet control facade for app/backend code."""

    def __init__(
        self,
        bus: int = 3,
        pulse_ms: int = DEFAULT_PULSE_MS,
        close_timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        auto_init: bool = True,
    ):
        self.bus = bus
        self.pulse_ms = self._validate_pulse_ms(pulse_ms)
        self.close_timeout_s = close_timeout_s
        self.cooldown_s = max(0.0, cooldown_s)
        self._ctl = CabinetController(bus=bus, pulse_ms=self.pulse_ms)
        self._last_unlock_at = 0.0
        self._closed = False
        if auto_init:
            self.init()

    def __enter__(self) -> "MedicineCabinet":
        self.init()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self.emergency_stop()
        self._ctl.close()
        self._closed = True

    def init(self) -> None:
        """Initialize chips and force all lock outputs off."""
        self._ensure_open()
        self._ctl.init()
        self._ctl.all_off()

    def emergency_stop(self) -> None:
        """Immediately turn off all lock outputs."""
        if self._closed:
            return
        self._ctl.all_off()

    def open_door(
        self,
        door: int,
        pulse_ms: Optional[int] = None,
        wait_closed: bool = False,
        close_timeout_s: Optional[float] = None,
    ) -> CabinetOpenResult:
        """Open one door once.

        Args:
            door: Cabinet door number, 1..12.
            pulse_ms: Unlock pulse. Defaults to 600 ms. Hard limit: 1..5000 ms.
            wait_closed: If True, block until this door is closed again or timeout.
            close_timeout_s: Timeout used when wait_closed=True.

        Returns:
            CabinetOpenResult with status after the unlock pulse.
        """
        self._ensure_open()
        self._validate_door(door)
        ms = self._validate_pulse_ms(self.pulse_ms if pulse_ms is None else pulse_ms)
        self._cooldown_if_needed()

        try:
            self._ctl.unlock(door, pulse_ms=ms)
            self._last_unlock_at = time.monotonic()
            status_after_open = self.get_door_status(door)

            close_confirmed: Optional[bool] = None
            if wait_closed:
                timeout = self.close_timeout_s if close_timeout_s is None else close_timeout_s
                close_confirmed = self.wait_until_closed(door, timeout_s=timeout)

            return CabinetOpenResult(
                door=door,
                pulse_ms=ms,
                close_wait_enabled=wait_closed,
                close_confirmed=close_confirmed,
                status_after_open=status_after_open,
            )
        finally:
            self._ctl.all_off()

    def get_door_status(self, door: int, samples: int = 3) -> CabinetDoorStatus:
        """Read one door state. closed=True means microswitch is pressed."""
        self._ensure_open()
        self._validate_door(door)
        state = self._ctl.read_door(door, samples=samples)
        return CabinetDoorStatus(door=state.door, closed=state.closed, raw=state.raw)

    def is_closed(self, door: int, samples: int = 3) -> bool:
        return self.get_door_status(door, samples=samples).closed

    def get_all_statuses(self, samples: int = 3) -> List[CabinetDoorStatus]:
        """Read all 12 door states."""
        self._ensure_open()
        return [
            CabinetDoorStatus(door=s.door, closed=s.closed, raw=s.raw)
            for s in self._ctl.read_all(samples=samples)
        ]

    def get_all_status_dict(self, samples: int = 3) -> Dict[str, object]:
        statuses = self.get_all_statuses(samples=samples)
        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "doors": [s.to_dict() for s in statuses],
        }

    def wait_until_closed(self, door: int, timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S) -> bool:
        """Wait until one door is closed. Returns False on timeout."""
        self._ensure_open()
        self._validate_door(door)
        return self._ctl.wait_until_closed(door, timeout_s=timeout_s, poll_s=0.2)

    def wait_until_open(self, door: int, timeout_s: float = 5.0) -> bool:
        """Wait until one door becomes open. Useful after open_door() in UI flows."""
        self._ensure_open()
        self._validate_door(door)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.is_closed(door, samples=3):
                return True
            time.sleep(0.2)
        return False

    def health_check(self) -> Dict[str, object]:
        """Lightweight check for startup diagnostics."""
        self._ensure_open()
        port_a, port_b = self._ctl.read_raw_ports()
        return {
            "ok": True,
            "bus": self.bus,
            "lock_addr": hex(self._ctl.lock_addr),
            "fb_addr": hex(self._ctl.fb_addr),
            "feedback_port_a": port_a,
            "feedback_port_b": port_b,
            "doors": [s.to_dict() for s in self.get_all_statuses(samples=3)],
        }

    @staticmethod
    def _validate_door(door: int) -> None:
        if not 1 <= int(door) <= DOOR_COUNT:
            raise ValueError(f"柜门编号必须是 1~{DOOR_COUNT}，当前是 {door}")

    @staticmethod
    def _validate_pulse_ms(pulse_ms: int) -> int:
        ms = int(pulse_ms)
        if ms <= 0 or ms > 5000:
            raise ValueError("开锁脉冲必须在 1~5000ms 内，建议 300~800ms。")
        return ms

    def _cooldown_if_needed(self) -> None:
        if self.cooldown_s <= 0:
            return
        elapsed = time.monotonic() - self._last_unlock_at
        if elapsed < self.cooldown_s:
            time.sleep(self.cooldown_s - elapsed)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("MedicineCabinet 已关闭，请重新创建实例。")


# Backward-friendly aliases for future code readability.
CabinetAPI = MedicineCabinet
