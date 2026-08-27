"""Bounded child-process control for the LocAgent agent loop.

This module deliberately carries only exception type/category metadata across
the multiprocessing boundary.  Exception text can contain provider response
details, so it is never placed in the result queue.
"""
from __future__ import annotations

import os
import signal
import traceback
from queue import Empty


class ChildExecutionError(RuntimeError):
    def __init__(self, kind: str, status_code: str = "unknown") -> None:
        self.kind = kind
        self.status_code = status_code
        super().__init__(f"child execution {kind} ({status_code})")


def _status_category(exc: BaseException) -> str:
    for obj in (exc, getattr(exc, "response", None)):
        code = getattr(obj, "status_code", None)
        if isinstance(code, int):
            return f"{code // 100}xx"
    return "unknown"


def _safe_tracepoint(exc: BaseException) -> dict:
    """Return only traceback coordinates, never source or exception text."""
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    safe_frames = [
        {
            "file": os.path.basename(frame.filename),
            "line": int(frame.lineno),
            "function": frame.name,
        }
        for frame in frames
    ]
    if not frames:
        return {
            "file": "unknown",
            "line": 0,
            "function": "unknown",
            "frames": [],
        }
    frame = frames[-1]
    return {
        "file": os.path.basename(frame.filename),
        "line": int(frame.lineno),
        "function": frame.name,
        "frames": safe_frames,
    }


def safe_error_result(exc: BaseException, stage: str = "agent_process") -> dict:
    """Return a response-safe structured error without exception text."""
    return {
        "status": "error",
        "stage": stage,
        "error_type": type(exc).__name__,
        "status_code": _status_category(exc),
        "pid": os.getpid(),
        "tracepoint": _safe_tracepoint(exc),
    }


def guarded_call(result_queue, target, kwargs: dict) -> None:
    """Run a child target and always try to publish a structured result."""
    try:
        target(**kwargs)
    except Exception as exc:
        result_queue.put(safe_error_result(exc))


def terminate_child(process, grace_s: float = 2.0) -> None:
    """Terminate one exact child and wait; escalate only if still alive."""
    if not process.is_alive():
        process.join(timeout=grace_s)
        return
    process.terminate()
    process.join(timeout=grace_s)
    if process.is_alive():
        process.kill()
        process.join(timeout=grace_s)


def wait_for_result(process, result_queue, join_timeout: float, result_timeout: float = 5.0):
    """Join a child and boundedly collect its one result."""
    process.join(timeout=join_timeout)
    if process.is_alive():
        terminate_child(process)
        raise ChildExecutionError("timeout", "unknown")
    exitcode = process.exitcode
    if exitcode not in (0, None):
        raise ChildExecutionError("child_exit", "unknown")
    try:
        return result_queue.get(timeout=result_timeout)
    except Empty as exc:
        raise ChildExecutionError("empty_queue", "unknown") from exc
