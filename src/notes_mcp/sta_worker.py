"""Dedicated STA thread + call queue for driving Notes COM objects.

All Notes COM calls in this project must go through StaWorker.call(). COM
automation of Notes requires the calling thread to be a single-threaded
apartment (STA) that services its own window-message queue; every call must
happen on the same thread that called CoInitialize(). Never call into a
win32com object from any other thread.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, TypeVar

import pythoncom

T = TypeVar("T")


class StaWorker:
    def __init__(self) -> None:
        self._request_q: "queue.Queue[tuple[Callable[[], object], queue.Queue] | None]" = queue.Queue()
        self._started = threading.Event()
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
                fn, result_q = item
                try:
                    result = fn()
                    result_q.put(("ok", result))
                except Exception as exc:  # noqa: BLE001 - forwarded to caller thread
                    result_q.put(("error", exc))
                pythoncom.PumpWaitingMessages()
        finally:
            pythoncom.CoUninitialize()

    def call(self, fn: Callable[[], T]) -> T:
        result_q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._request_q.put((fn, result_q))
        status, result = result_q.get()
        if status == "error":
            raise result  # type: ignore[misc]
        return result  # type: ignore[return-value]

    def shutdown(self) -> None:
        self._request_q.put(None)
        self._thread.join(timeout=5)
