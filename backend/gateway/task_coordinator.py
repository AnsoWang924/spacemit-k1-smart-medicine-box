from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Callable, Iterator


FINAL_STATES = {"completed", "failed", "cancelled", "timed_out"}


@dataclass
class TaskRecord:
    taskId: str
    task: str
    sessionId: str
    purpose: str
    requestId: str
    state: str
    queuedAt: float
    startedAt: float
    deadlineAt: float
    finishedAt: float = 0.0
    errorCode: str = ""
    errorMessage: str = ""

    def public(self, now: float) -> dict:
        data = asdict(self)
        data["elapsedSeconds"] = round(max(0.0, (self.finishedAt or now) - self.startedAt), 3)
        data["remainingSeconds"] = round(max(0.0, self.deadlineAt - now), 3) if not self.finishedAt else 0.0
        return data


class TaskBusyError(RuntimeError):
    def __init__(self, active: dict | None):
        super().__init__("inference_busy")
        self.active = active or {}


class TaskCoordinator:
    """Single authoritative runtime inference owner.

    Boot warmups are intentionally not routed through this coordinator. They are
    supervised separately and may run in parallel. User-triggered inference must
    enter here regardless of whether it came from the UI or a direct API call.
    """

    def __init__(
        self,
        *,
        history_size: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._active: TaskRecord | None = None
        self._history: deque[TaskRecord] = deque(maxlen=history_size)

    def _snapshot_locked(self, record: TaskRecord | None) -> dict | None:
        return record.public(self._clock()) if record is not None else None

    def acquire(
        self,
        task: str,
        *,
        session_id: str = "",
        purpose: str = "",
        request_id: str = "",
        task_id: str = "",
        wait_seconds: float = 0.0,
        run_seconds: float = 60.0,
    ) -> TaskRecord:
        queued_at = self._clock()
        wait_deadline = queued_at + max(0.0, wait_seconds)
        with self._condition:
            while self._active is not None:
                remaining = wait_deadline - self._clock()
                if remaining <= 0:
                    raise TaskBusyError(self._snapshot_locked(self._active))
                self._condition.wait(timeout=remaining)
            started_at = self._clock()
            record = TaskRecord(
                taskId=task_id or f"task-{uuid.uuid4().hex}",
                task=str(task),
                sessionId=str(session_id or ""),
                purpose=str(purpose or task),
                requestId=str(request_id or ""),
                state="running",
                queuedAt=queued_at,
                startedAt=started_at,
                deadlineAt=started_at + max(0.1, run_seconds),
            )
            self._active = record
            return record

    def finish(
        self,
        task_id: str,
        *,
        state: str = "completed",
        error_code: str = "",
        error_message: str = "",
    ) -> bool:
        if state not in FINAL_STATES:
            raise ValueError(f"invalid final task state: {state}")
        with self._condition:
            if self._active is None or self._active.taskId != task_id:
                return False
            self._active.state = state
            self._active.errorCode = str(error_code or "")
            self._active.errorMessage = str(error_message or "")
            self._active.finishedAt = self._clock()
            self._history.appendleft(self._active)
            self._active = None
            self._condition.notify_all()
            return True

    def cancel(self, task_id: str, *, reason: str = "cancelled") -> bool:
        return self.finish(task_id, state="cancelled", error_code="task_cancelled", error_message=reason)

    @contextmanager
    def run(
        self,
        task: str,
        *,
        session_id: str = "",
        purpose: str = "",
        request_id: str = "",
        wait_seconds: float = 0.0,
        run_seconds: float = 60.0,
    ) -> Iterator[TaskRecord]:
        record = self.acquire(
            task,
            session_id=session_id,
            purpose=purpose,
            request_id=request_id,
            wait_seconds=wait_seconds,
            run_seconds=run_seconds,
        )
        try:
            yield record
        except TimeoutError as exc:
            self.finish(record.taskId, state="timed_out", error_code="task_timeout", error_message=str(exc))
            raise
        except BaseException as exc:
            self.finish(record.taskId, state="failed", error_code=type(exc).__name__, error_message=str(exc))
            raise
        else:
            self.finish(record.taskId, state="completed")

    def status(self) -> dict:
        with self._condition:
            now = self._clock()
            return {
                "activeTask": self._active.public(now) if self._active else None,
                "busy": self._active is not None,
                "history": [item.public(now) for item in self._history],
            }

