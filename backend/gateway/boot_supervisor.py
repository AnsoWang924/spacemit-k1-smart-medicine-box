from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BootModule:
    name: str
    required: bool
    state: str = "not_started"
    attempts: int = 0
    startedAt: float = 0.0
    finishedAt: float = 0.0
    recovered: bool = False
    lastError: dict | None = None
    data: dict = field(default_factory=dict)


class BootSupervisor:
    """Supervise parallel boot warmups and preserve their true outcome."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._modules: dict[str, BootModule] = {}
        self._threads: list[threading.Thread] = []

    @staticmethod
    def _interpret(result) -> tuple[bool, dict, dict | None]:
        if isinstance(result, tuple) and len(result) == 3:
            status, data, err = result
            return int(status) < 400 and err is None, dict(data or {}), dict(err or {}) or None
        if hasattr(result, "returncode"):
            ok = int(result.returncode) == 0
            return ok, {"returncode": int(result.returncode)}, None if ok else {
                "code": "boot_command_failed",
                "message": str(getattr(result, "stderr", "") or f"returncode={result.returncode}"),
            }
        if result is False:
            return False, {}, {"code": "boot_not_ready", "message": "boot target returned false"}
        return True, dict(result or {}) if isinstance(result, dict) else {}, None

    def start(self, jobs: list[tuple[str, Callable, bool]], *, attempts: int = 3) -> None:
        with self._lock:
            for name, _target, required in jobs:
                self._modules[name] = BootModule(name=name, required=required)
        for name, target, _required in jobs:
            thread = threading.Thread(
                target=self._run,
                args=(name, target, max(1, attempts)),
                name=f"boot-supervisor-{name}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def _run(self, name: str, target: Callable, attempts: int) -> None:
        for attempt in range(1, attempts + 1):
            with self._lock:
                item = self._modules[name]
                item.state = "loading" if attempt == 1 else "retrying"
                item.attempts = attempt
                item.startedAt = item.startedAt or self._clock()
            try:
                ok, data, err = self._interpret(target())
            except Exception as exc:
                ok, data, err = False, {}, {
                    "code": type(exc).__name__,
                    "message": str(exc),
                }
            with self._lock:
                item = self._modules[name]
                item.data = data
                item.lastError = err
                if ok:
                    item.state = "recovered" if attempt > 1 else "ready"
                    item.recovered = attempt > 1
                    item.finishedAt = self._clock()
                    return
                item.state = "error"
                item.finishedAt = self._clock()
            if attempt < attempts:
                with self._lock:
                    self._modules[name].state = "retrying"
                time.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))

    def status(self) -> dict:
        with self._lock:
            modules = {
                name: {
                    "name": item.name,
                    "required": item.required,
                    "state": item.state,
                    "ready": item.state in {"ready", "recovered"},
                    "attempts": item.attempts,
                    "startedAt": item.startedAt,
                    "finishedAt": item.finishedAt,
                    "recovered": item.recovered,
                    "lastError": item.lastError,
                    "data": item.data,
                }
                for name, item in self._modules.items()
            }
            required = [item for item in modules.values() if item["required"]]
            return {
                "ready": bool(required) and all(item["ready"] for item in required),
                "loading": any(item["state"] in {"not_started", "loading", "retrying"} for item in required),
                "modules": modules,
            }
