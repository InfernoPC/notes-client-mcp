"""Backend (headless) HCL Notes automation.

Deliberately uses only the backend `NotesSession`/`NotesDatabase` object model
(the same classes LotusScript agents use) via the `Lotus.NotesSession` COM
ProgID. It never touches `Notes.NotesUIWorkspace` or any other UI-driving
object: an earlier spike showed that driving the live client UI (e.g.
`NotesUIWorkspace.OpenDatabase` on a database already open in the client) can
trigger the mail template's own LotusScript event handlers and crash
NLNOTES.exe (reproduced: ACCESS_VIOLATION inside Notes 9.0.1's OLE Automation
error-handling path). Staying on the backend session avoids the client UI
entirely, so it cannot trigger that class of failure.
"""

from __future__ import annotations

import contextvars
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

import win32com.client

from .sta_worker import NotesBusyError, StaWorker

__all__ = [
    "MailAddress",
    "NotesBackend",
    "NotesBusyError",
    "NotesConnectionError",
    "current_operation",
    "normalize_replica_id",
    "open_database",
    "open_database_by_replica_id",
]

# Name of the MCP tool currently being served, set by server.register_tools so
# that a "server busy" error can say which tool is hogging the STA thread
# instead of a generic "notes call". A ContextVar rather than an attribute
# because the MCP SDK may serve tool calls from several threads/tasks at once,
# and a shared mutable label would race between them.
current_operation: contextvars.ContextVar[str] = contextvars.ContextVar(
    "notes_mcp_current_operation", default="notes call"
)

T = TypeVar("T")

# session.Initialize() briefly locks the ID file for the duration of the
# call only (confirmed by hand: a second process can connect immediately
# once the first Initialize() returns, even while the first session stays
# open) - but if an MCP client spawns several notes-client-mcp profile
# processes at once (e.g. registering read + write + design + all
# together), two Initialize() calls can land in the same instant and one
# gets "The ID file is locked by another process. Try again later" and
# exits. A few short retries with jitter absorbs that race.
_INIT_LOCK_ERROR = "locked by another process"
_INIT_MAX_ATTEMPTS = 5
_INIT_RETRY_DELAY_RANGE = (0.5, 1.5)


class NotesConnectionError(RuntimeError):
    pass


@dataclass
class MailAddress:
    server: str
    file_path: str


class NotesBackend:
    """One backend NotesSession, owned by a dedicated STA thread.

    Call connect() once at process startup. After that, every tool call runs
    through run()/get_database() on the same STA thread.
    """

    def __init__(self) -> None:
        self._worker = StaWorker()
        self._session = None

    def connect(self, password: str | None = None) -> str:
        # Priority: explicit arg > NOTES_PASSWORD env var (e.g. from a .env
        # file loaded by the caller - see server.py). No interactive fallback:
        # confirmed by hand that getpass() cannot work when an MCP client
        # spawns this process (it owns stdin entirely for the JSON-RPC
        # stream), so it just hangs until the client times out the
        # connection. NOTES_PASSWORD in .env means the password sits in
        # plaintext on disk - that trade-off was made explicitly by the
        # project owner; it is not the default anyone else should assume.
        # Keep the .gitignore entry for .env and never log this value.
        if password is None:
            password = os.environ.get("NOTES_PASSWORD")
        if not password:
            raise NotesConnectionError(
                "NOTES_PASSWORD is not set. Copy .env.example to .env in the "
                "project root and set NOTES_PASSWORD before running this "
                "under an MCP client."
            )

        def _connect():
            session = win32com.client.Dispatch("Lotus.NotesSession")
            session.Initialize(password)
            return session

        last_exc: Exception | None = None
        for attempt in range(1, _INIT_MAX_ATTEMPTS + 1):
            try:
                self._session = self._worker.call(_connect, label="connect")
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _INIT_LOCK_ERROR not in str(exc) or attempt == _INIT_MAX_ATTEMPTS:
                    break
                time.sleep(random.uniform(*_INIT_RETRY_DELAY_RANGE))

        if last_exc is not None:
            raise NotesConnectionError(f"Notes session Initialize failed: {last_exc}") from last_exc
        return self.run(lambda s: s.UserName)

    @property
    def connected(self) -> bool:
        return self._session is not None

    def _require_session(self):
        if self._session is None:
            raise NotesConnectionError("Not connected - call connect() first")
        return self._session

    def run(self, fn: "Callable[[object], T]") -> T:
        """Run fn(session) on the STA thread and return its result.

        Raises NotesBusyError if an earlier call is still occupying the STA
        thread when this one's queue timeout elapses - see StaWorker.call."""
        session = self._require_session()
        return self._worker.call(lambda: fn(session), label=current_operation.get())

    def get_mail_address(self) -> MailAddress:
        def _resolve(session):
            server = session.GetEnvironmentString("MailServer", True)
            file_path = session.GetEnvironmentString("MailFile", True)
            return MailAddress(server=server, file_path=file_path)

        return self.run(_resolve)

    def shutdown(self) -> None:
        self._worker.shutdown()


def open_database(session, server: str, file_path: str):
    """Open a NotesDatabase. Only call this from inside a function passed to
    NotesBackend.run() - `session` and the returned NotesDatabase are raw COM
    objects that are only safe to use on the STA thread that produced them.
    Never return them out of the run() closure; extract plain data instead."""
    db = session.GetDatabase(server, file_path)
    if db is None:
        raise NotesConnectionError(f"Database not found: {server!r} {file_path!r}")
    try:
        if not db.IsOpen:
            db.Open()
    except Exception as exc:  # noqa: BLE001 - surface the real COM reason, not a bare failure
        raise NotesConnectionError(
            f"Could not open database (server={server!r}, file_path={file_path!r}): "
            f"{exc}. file_path is relative to the server's Data directory and often "
            "includes a subfolder - don't guess it; confirm the exact server/file_path "
            "pair via a doclink/URL or the database's Properties dialog instead of "
            "retrying with variants."
        ) from exc
    return db


def normalize_replica_id(replica_id: str) -> str:
    """Accept either spelling of a replica ID and return the bare 16-hex form.

    Notes shows the same value two ways and both turn up in the wild: DXL
    and `NotesDatabase.ReplicaID` give 16 bare hex digits
    ("48257B98001E8842"), while the Replication/Properties dialogs and most
    LotusScript examples use the colon-separated pair
    ("48257B98:001E8842"). Casing varies too. Normalizing here means callers
    can paste whichever one they have."""
    cleaned = re.sub(r"[\s:-]", "", replica_id or "").upper()
    if not re.fullmatch(r"[0-9A-F]{16}", cleaned):
        raise ValueError(
            f"Not a replica ID: {replica_id!r}. Expected 16 hex digits, "
            "optionally colon-separated (e.g. '48257B98001E8842' or "
            "'48257B98:001E8842')."
        )
    return cleaned


def open_database_by_replica_id(session, server: str, replica_id: str):
    """Open a NotesDatabase by replica ID instead of file path, or return
    None if that server holds no such replica. Same STA-thread rule as
    open_database - never let the returned COM object out of the run()
    closure.

    A replica ID identifies a replica *set*, not a location, so the lookup
    is still scoped to one server: it asks that server's
    NotesDbDirectory for the replica and gets Nothing if the copy lives
    somewhere else. `server=""` searches the local Notes data directory.

    Two call shapes are tried because neither is universally documented as
    accepting both ID spellings, and both are cheap:
    `NotesDbDirectory.OpenDatabaseByReplicaID` first (it is the method that
    exists for exactly this), then `NotesDatabase.OpenByReplicaID` on the
    placeholder database `GetDatabase(server, "")` returns. Each is tried
    with the bare 16-hex form and then the colon-separated form.

    Returns None - not an error - when nothing matches, because "the
    replica is on a different server" is the normal outcome of a hunt
    across candidate servers, not a failure. Note that a replica the
    caller has no access to is indistinguishable from an absent one here:
    both come back as Nothing."""
    rid = normalize_replica_id(replica_id)
    spellings = (rid, f"{rid[:8]}:{rid[8:]}")
    server = server or ""

    try:
        directory = session.GetDbDirectory(server)
    except Exception as exc:  # noqa: BLE001 - surface the real COM reason
        raise NotesConnectionError(
            f"Could not open the database directory of server={server!r}: {exc}"
        ) from exc

    for spelling in spellings:
        try:
            db = directory.OpenDatabaseByReplicaID(spelling)
        except Exception:  # noqa: BLE001 - wrong spelling for this build; try the next
            db = None
        if db is not None:
            return db

    placeholder = session.GetDatabase(server, "")
    if placeholder is not None:
        for spelling in spellings:
            try:
                if placeholder.OpenByReplicaID(server, spelling):
                    return placeholder
            except Exception:  # noqa: BLE001
                continue
    return None
