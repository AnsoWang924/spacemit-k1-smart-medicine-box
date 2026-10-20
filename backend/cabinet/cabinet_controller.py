#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K1 MUSE Pi Pro 12-door medicine cabinet lock controller.

Hardware:
  K1 40Pin I2C3 -> lock-control PCB -> two MCP23017 chips.

  U_LOCK_OUT  address 0x20: 12 outputs, drive MOSFET gates through resistors.
  U_DOOR_FB   address 0x21: 12 inputs, read door microswitch feedback.

Safety:
  - K1 only talks 3.3V I2C. Never connect 12V to K1.
  - Opening a lock is always a short pulse and all outputs are turned off after.
  - By default, only one lock is energized at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


try:
    from smbus2 import SMBus  # type: ignore
except Exception:  # pragma: no cover - depends on K1 runtime
    try:
        from smbus import SMBus  # type: ignore
    except Exception:  # pragma: no cover
        SMBus = None  # type: ignore


MCP23017_REG = {
    "IODIRA": 0x00,
    "IODIRB": 0x01,
    "IPOLA": 0x02,
    "IPOLB": 0x03,
    "GPINTENA": 0x04,
    "GPINTENB": 0x05,
    "DEFVALA": 0x06,
    "DEFVALB": 0x07,
    "INTCONA": 0x08,
    "INTCONB": 0x09,
    "IOCON": 0x0A,
    "GPPUA": 0x0C,
    "GPPUB": 0x0D,
    "GPIOA": 0x12,
    "GPIOB": 0x13,
    "OLATA": 0x14,
    "OLATB": 0x15,
}


DEFAULT_BUS = 3
LOCK_OUT_ADDR = 0x20
DOOR_FB_ADDR = 0x21
DOOR_COUNT = 12


@dataclass(frozen=True)
class DoorState:
    door: int
    raw: int
    closed: bool

    @property
    def label(self) -> str:
        return "closed" if self.closed else "open"

    @property
    def zh_label(self) -> str:
        return "已关门" if self.closed else "未关门"


def _require_smbus() -> None:
    if SMBus is None:
        raise RuntimeError(
            "未找到 smbus/smbus2。请在 K1 上安装：sudo apt install -y i2c-tools python3-smbus，"
            "或 pip install smbus2。"
        )


def channel_to_port_bit(channel: int) -> Tuple[str, int]:
    """Map door/lock channel 1..12 to MCP23017 port A/B and bit index."""
    if not 1 <= channel <= DOOR_COUNT:
        raise ValueError(f"柜门编号必须是 1~{DOOR_COUNT}，当前是 {channel}")
    if channel <= 8:
        return "A", channel - 1
    return "B", channel - 9


def bit_mask(channel: int) -> Tuple[str, int]:
    port, bit = channel_to_port_bit(channel)
    return port, 1 << bit


class MCP23017:
    def __init__(self, bus: int, address: int):
        _require_smbus()
        self.bus_id = bus
        self.address = address
        self.bus = SMBus(bus)  # type: ignore

    def close(self) -> None:
        close = getattr(self.bus, "close", None)
        if close:
            close()

    def write_reg(self, reg: int, value: int) -> None:
        self.bus.write_byte_data(self.address, reg, value & 0xFF)

    def read_reg(self, reg: int) -> int:
        return int(self.bus.read_byte_data(self.address, reg)) & 0xFF

    def write_gpio(self, port: str, value: int) -> None:
        reg = MCP23017_REG["GPIOA"] if port == "A" else MCP23017_REG["GPIOB"]
        self.write_reg(reg, value)

    def read_gpio(self, port: str) -> int:
        reg = MCP23017_REG["GPIOA"] if port == "A" else MCP23017_REG["GPIOB"]
        return self.read_reg(reg)


class CabinetController:
    """High-level safe controller for the 12-door lock PCB."""

    def __init__(
        self,
        bus: int = DEFAULT_BUS,
        lock_addr: int = LOCK_OUT_ADDR,
        fb_addr: int = DOOR_FB_ADDR,
        pulse_ms: int = 600,
        active_low_feedback: bool = True,
    ):
        self.bus = bus
        self.lock_addr = lock_addr
        self.fb_addr = fb_addr
        self.pulse_ms = pulse_ms
        self.active_low_feedback = active_low_feedback
        self.lock = MCP23017(bus, lock_addr)
        self.fb = MCP23017(bus, fb_addr)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.all_off()
        except Exception:
            pass
        self.lock.close()
        self.fb.close()
        self._closed = True

    def __enter__(self) -> "CabinetController":
        self.init()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def init(self) -> None:
        """Initialize both MCP23017 chips into safe states."""
        # IOCON: keep BANK=0 sequential register layout. DISSLW=0 keeps I2C normal.
        self.lock.write_reg(MCP23017_REG["IOCON"], 0x00)
        self.fb.write_reg(MCP23017_REG["IOCON"], 0x00)

        # Lock chip: drive latches low before and after switching to output.
        # This keeps restart behavior conservative if a previous process exited badly.
        self.lock.write_reg(MCP23017_REG["GPIOA"], 0x00)
        self.lock.write_reg(MCP23017_REG["GPIOB"], 0x00)
        self.lock.write_reg(MCP23017_REG["OLATA"], 0x00)
        self.lock.write_reg(MCP23017_REG["OLATB"], 0x00)
        self.lock.write_reg(MCP23017_REG["IODIRA"], 0x00)
        self.lock.write_reg(MCP23017_REG["IODIRB"], 0x00)
        self.lock.write_reg(MCP23017_REG["GPIOA"], 0x00)
        self.lock.write_reg(MCP23017_REG["GPIOB"], 0x00)
        self.lock.write_reg(MCP23017_REG["OLATA"], 0x00)
        self.lock.write_reg(MCP23017_REG["OLATB"], 0x00)

        # Door feedback chip: all pins input. Enable internal pullups as a second
        # layer of stability; the PCB already has external 10k pullups.
        self.fb.write_reg(MCP23017_REG["IODIRA"], 0xFF)
        self.fb.write_reg(MCP23017_REG["IODIRB"], 0xFF)
        self.fb.write_reg(MCP23017_REG["GPPUA"], 0xFF)
        self.fb.write_reg(MCP23017_REG["GPPUB"], 0xFF)

    def all_off(self) -> None:
        """Turn off all 12 lock outputs."""
        self.lock.write_reg(MCP23017_REG["GPIOA"], 0x00)
        self.lock.write_reg(MCP23017_REG["GPIOB"], 0x00)
        self.lock.write_reg(MCP23017_REG["OLATA"], 0x00)
        self.lock.write_reg(MCP23017_REG["OLATB"], 0x00)

    def set_lock(self, door: int, on: bool) -> None:
        """Set exactly one lock output bit. Use unlock() for normal operation."""
        port, mask = bit_mask(door)
        reg = MCP23017_REG["GPIOA"] if port == "A" else MCP23017_REG["GPIOB"]
        value = self.lock.read_reg(reg)
        if on:
            value |= mask
        else:
            value &= ~mask
        self.lock.write_reg(reg, value)

    def unlock(self, door: int, pulse_ms: Optional[int] = None) -> None:
        """Pulse one lock. Always turns all outputs off at the end."""
        ms = self.pulse_ms if pulse_ms is None else pulse_ms
        if ms <= 0 or ms > 5000:
            raise ValueError("开锁脉冲必须在 1~5000ms 内；建议 300~800ms。")
        self.all_off()
        try:
            self.set_lock(door, True)
            time.sleep(ms / 1000.0)
        finally:
            self.all_off()

    def read_raw_ports(self) -> Tuple[int, int]:
        return self.fb.read_gpio("A"), self.fb.read_gpio("B")

    def read_door(self, door: int, samples: int = 1, interval_ms: int = 20) -> DoorState:
        """Read one door. Multiple samples provide simple debounce."""
        if samples <= 1:
            return self._read_door_once(door)

        reads = []
        for _ in range(samples):
            reads.append(self._read_door_once(door).closed)
            time.sleep(interval_ms / 1000.0)
        closed = reads.count(True) >= (len(reads) // 2 + 1)
        raw = 0 if closed and self.active_low_feedback else 1
        return DoorState(door=door, raw=raw, closed=closed)

    def _read_door_once(self, door: int) -> DoorState:
        port, mask = bit_mask(door)
        value = self.fb.read_gpio(port)
        raw_high = bool(value & mask)
        closed = (not raw_high) if self.active_low_feedback else raw_high
        return DoorState(door=door, raw=1 if raw_high else 0, closed=closed)

    def read_all(self, samples: int = 1) -> List[DoorState]:
        return [self.read_door(i, samples=samples) for i in range(1, DOOR_COUNT + 1)]

    def wait_until_closed(self, door: int, timeout_s: float = 15.0, poll_s: float = 0.2) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.read_door(door, samples=3).closed:
                return True
            time.sleep(poll_s)
        return False


def format_states(states: Iterable[DoorState]) -> str:
    return " ".join(f"{s.door:02d}:{'关' if s.closed else '开'}" for s in states)


def states_to_dict(states: Iterable[DoorState]) -> Dict[str, object]:
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "doors": [
            {"door": s.door, "raw": s.raw, "closed": s.closed, "status": s.label, "status_zh": s.zh_label}
            for s in states
        ],
    }


def parse_addr(value: str) -> int:
    return int(value, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="K1 12路药柜锁控板控制工具",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--bus", type=int, default=int(os.environ.get("CABINET_I2C_BUS", DEFAULT_BUS)))
    parser.add_argument("--lock-addr", type=parse_addr, default=LOCK_OUT_ADDR)
    parser.add_argument("--fb-addr", type=parse_addr, default=DOOR_FB_ADDR)
    parser.add_argument("--pulse-ms", type=int, default=600)
    parser.add_argument("--json", action="store_true", help="状态输出为 JSON")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化 MCP23017，所有锁输出关闭")
    sub.add_parser("all-off", help="立即关闭所有锁输出")
    sub.add_parser("status", help="读取 12 个柜门开关状态")

    unlock = sub.add_parser("unlock", help="打开指定柜门一次")
    unlock.add_argument("door", type=int, help="柜门编号 1~12")
    unlock.add_argument("--wait-close", action="store_true", help="开锁后等待该柜门关上")
    unlock.add_argument("--timeout", type=float, default=15.0, help="等待关门超时秒数")

    mon = sub.add_parser("monitor", help="实时监控所有柜门状态")
    mon.add_argument("--interval", type=float, default=0.25, help="刷新间隔秒")
    mon.add_argument("--changes-only", action="store_true", help="只在状态变化时打印")

    demo = sub.add_parser("demo", help="从 1 号柜到 12 号柜依次弹出")
    demo.add_argument("--gap", type=float, default=1.2, help="两个柜门之间等待秒数")
    demo.add_argument("--wait-close", action="store_true", help="每个柜门等待关门后再开下一个")
    demo.add_argument("--timeout", type=float, default=15.0, help="等待关门超时秒数")

    sub.add_parser("scan-note", help="打印 I2C 扫描命令提示")
    return parser


def make_controller(args: argparse.Namespace) -> CabinetController:
    return CabinetController(
        bus=args.bus,
        lock_addr=args.lock_addr,
        fb_addr=args.fb_addr,
        pulse_ms=args.pulse_ms,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "scan-note":
        print("在 K1 上建议先执行：")
        print(f"  sudo i2cdetect -y {args.bus}")
        print("应能看到 0x20 和 0x21。若看不到，请检查 3.3V/GND/SDA/SCL 接线。")
        return 0

    ctl: Optional[CabinetController] = None

    def safe_exit(signum=None, frame=None):  # noqa: ANN001
        if ctl is not None:
            ctl.close()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, safe_exit)
    signal.signal(signal.SIGTERM, safe_exit)

    try:
        ctl = make_controller(args)
        ctl.init()

        if args.command == "init":
            print("已初始化：所有开锁输出已关闭。")
            return 0

        if args.command == "all-off":
            ctl.all_off()
            print("已关闭所有开锁输出。")
            return 0

        if args.command == "status":
            states = ctl.read_all(samples=3)
            print(json.dumps(states_to_dict(states), ensure_ascii=False, indent=2) if args.json else format_states(states))
            return 0

        if args.command == "unlock":
            print(f"准备打开 {args.door} 号柜，脉冲 {args.pulse_ms}ms")
            ctl.unlock(args.door)
            states = ctl.read_all(samples=3)
            print(json.dumps(states_to_dict(states), ensure_ascii=False, indent=2) if args.json else format_states(states))
            if args.wait_close:
                ok = ctl.wait_until_closed(args.door, timeout_s=args.timeout)
                print(f"{args.door} 号柜关门确认：{'成功' if ok else '超时'}")
                return 0 if ok else 2
            return 0

        if args.command == "monitor":
            last = None
            while True:
                states = ctl.read_all(samples=1)
                text = json.dumps(states_to_dict(states), ensure_ascii=False) if args.json else format_states(states)
                if not args.changes_only or text != last:
                    print(text, flush=True)
                    last = text
                time.sleep(args.interval)

        if args.command == "demo":
            for door in range(1, DOOR_COUNT + 1):
                print(f"\n=== 打开 {door:02d} 号柜 ===", flush=True)
                ctl.unlock(door)
                print(format_states(ctl.read_all(samples=3)), flush=True)
                if args.wait_close:
                    ok = ctl.wait_until_closed(door, timeout_s=args.timeout)
                    print(f"{door:02d} 号柜关门确认：{'成功' if ok else '超时'}", flush=True)
                time.sleep(args.gap)
            print("\n演示完成，所有开锁输出已关闭。")
            ctl.all_off()
            return 0

        raise RuntimeError(f"未知命令：{args.command}")
    finally:
        if ctl is not None:
            ctl.close()


if __name__ == "__main__":
    raise SystemExit(main())
