"""Dedicated STA thread + call queue for driving Notes COM objects.

All Notes COM calls in this project must go through StaWorker.call(). COM
automation of Notes requires the calling thread to be a single-threaded
apartment (STA) that services its own window-message queue; every call must
happen on the same thread that called CoInitialize(). Never call into a
win32com object from any other thread.

Because there is exactly one STA thread, calls are strictly serialized: a
slow COM call blocks every later one. That is unavoidable (a synchronous
COM call cannot be cancelled from another thread - there is no safe
interrupt, and abandoning the thread would leak the apartment), but it used
to be *invisible*: every queued caller just waited forever. Observed on a
63 GB NSF - one export_view_csv over a view whose index needed rebuilding ran
past 20 minutes, and every later call, including a trivial
get_database_info, sat silently in the queue until the MCP client's own
1800 s idle timeout killed it. Five tool calls failed with "sent no response
or progress", none of which named the real cause.

So callers now fail fast while *queued* - see call(). A call that has
already started is never timed out here: abandoning that wait would not stop
the COM call, it would only replace a truthful "still running" with a
misleading error.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable, TypeVar

import pythoncom

T = TypeVar("T")

# How long a call may sit *unstarted* in the queue before it gives up. Only
# ever elapses when an earlier call is still occupying the STA thread, so it
# is a "server busy" bound, not an operation timeout.
_QUEUE_TIMEOUT_ENV = "NOTES_MCP_QUEUE_TIMEOUT"
_DEFAULT_QUEUE_TIMEOUT = 60.0


class NotesBusyError(RuntimeError):
    """Raised when a call could not even start because the STA thread is
    still busy with an earlier one."""


def _queue_timeout() -> float:
    raw = os.environ.get(_QUEUE_TIMEOUT_ENV)
    if not raw:
        return _DEFAULT_QUEUE_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_QUEUE_TIMEOUT
    # 0 (or negative) disables the bound and restores the old wait-forever
    # behaviour, for anyone who would rather block than get an error.
    return value if value > 0 else 0.0


class _Request:
    __slots__ = ("fn", "label", "result_q", "started")

    def __init__(self, fn: Callable[[], object], label: str) -> None:
        self.fn = fn
        self.label = label
        self.result_q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.started = threading.Event()


class StaWorker:
    def __init__(self) -> None:
        self._request_q: "queue.Queue[_Request | None]" = queue.Queue()
        self._started = threading.Event()
        # Written only by the STA thread, read by callers for the busy
        # message. A plain tuple assignment is atomic enough for that.
        self._in_flight: tuple[str, float] | None = None
        self._thread = threading.Thread(target=self._run, name="notes-sta", daemon=True)
        self._thread.start()
        self._started.wait()

    def _run(self) -> None:
        pythoncom.CoInitialize()
        self._started.set()
        try:
            while True:
                item = self._request_q.get()
                if item is None:
                    break
                self._in_flight = (item.label, time.monotonic())
                item.started.set()
                try:
                    result = item.fn()
                    item.result_q.put(("ok", result))
                except Exception as exc:  # noqa: BLE001 - forwarded to caller thread
                    item.result_q.put(("error", exc))
                finally:
                    self._in_flight = None
                pythoncom.PumpWaitingMessages()
        finally:
            pythoncom.CoUninitialize()

    def call(self, fn: Callable[[], T], label: str = "notes call") -> T:
        """Run fn on the STA thread.

        Waits at most NOTES_MCP_QUEUE_TIMEOUT seconds (default 60) for the
        call to *start*; if an earlier call is still running, raises
        NotesBusyError naming it and how long it has been going. Once
        started, waits for the result with no bound - the COM call cannot be
        cancelled, so a deadline here would only mislead."""
        request = _Request(fn, label)
        self._request_q.put(request)

        timeout = _queue_timeout()
        if timeout and not request.started.wait(timeout):
            busy = self._in_flight
            if busy is None:
                # Started between the wait expiring and this read - nothing
                # is wrong, so just fall through to the result wait.
                pass
            else:
                running, since = busy
                raise NotesBusyError(
                    f"notes-client-mcp is busy: {running!r} has been running for "
                    f"{time.monotonic() - since:.0f}s and this call ({label!r}) is "
                    f"still queued behind it. Notes COM calls cannot be cancelled, "
                    f"so the only way out of a stuck call is to restart the MCP "
                    f"server. If {running!r} is expected to take this long, raise "
                    f"{_QUEUE_TIMEOUT_ENV} (seconds, 0 disables) and retry."
                )

        status, result = request.result_q.get()
        if status == "error":
            raise result  # type: ignore[misc]
        return result  # type: ignore[return-value]

    @property
    def in_flight(self) -> tuple[str, float] | None:
        return self._in_flight

    def shutdown(self) -> None:
        self._request_q.put(None)
        self._thread.join(timeout=5)
