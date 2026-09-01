"""Write tools: create/update documents.

These do their entire COM interaction inside one NotesBackend.run() closure,
same as the read-only tools (see notes_backend.open_database). Unlike the
read tools, every one of these is exposed to the MCP client behind an
elicitation confirmation (see server.py) - this module itself does not
confirm anything; it assumes the caller already got the user's go-ahead.
"""

from __future__ import annotations

import datetime
from typing import Any

from ..notes_backend import NotesBackend, open_database


def _to_notes_value(session, value: Any) -> Any:
    """Convert a JSON-friendly field value into whatever ReplaceItemValue
    needs to produce the right Notes item type.

    Confirmed by hand: plain Python int/float/bool/list/str already marshal
    correctly via COM into Number/Number/Number/Text-multivalue/Text items
    respectively - no conversion needed. The one exception is dates: a raw
    ISO string just becomes a Text item (confirmed: "2026-01-15" stored as
    plain text, not a date/time item), it needs an actual NotesDateTime
    object instead. Auto-detect ISO-8601 date/datetime strings and convert
    those via session.CreateDateTime(); every other string is left as plain
    text, which does mean a legitimate text value that happens to look like
    an ISO date/datetime gets converted too - an accepted trade-off for not
    needing a separate wrapper syntax for dates.
    """
    if isinstance(value, str):
        try:
            datetime.datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            return session.CreateDateTime(value)
    return value


def create_document(
    backend: NotesBackend,
    server: str,
    file_path: str,
    form: str,
    fields: dict[str, Any],
) -> dict:
    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.CreateDocument()
        doc.ReplaceItemValue("Form", form)
        # Plain ReplaceItemValue() never runs the form's own field logic (default
        # value / translation / computed-for-display formulas) - confirmed by
        # hand: without this, a computed field like `doc_url` on the "dblink"
        # form comes back completely absent. Two calls, not one: the first
        # (right after setting Form, before our own field values) populates
        # default-value/hidden computed fields that later formulas may depend
        # on; the second (after our field values are set) recomputes anything
        # that depends on those values. (False, False): not a doc-mod compute,
        # don't raise on a validation formula failure - a create succeeding but
        # a display-only formula erroring shouldn't lose the write.
        doc.ComputeWithForm(False, False)
        for name, value in fields.items():
            doc.ReplaceItemValue(name, _to_notes_value(session, value))
        doc.ComputeWithForm(False, False)
        doc.Save(False, False)  # new document, nothing to conflict with
        return {"unid": doc.UniversalID, "note_id": doc.NoteID}

    return backend.run(_op)


class DocumentLockedError(RuntimeError):
    pass


class SaveConflictError(RuntimeError):
    pass


def update_document(
    backend: NotesBackend,
    server: str,
    file_path: str,
    unid: str,
    fields: dict[str, Any],
) -> dict:
    def _op(session):
        def _apply(doc):
            for name, value in fields.items():
                doc.ReplaceItemValue(name, _to_notes_value(session, value))
            doc.ComputeWithForm(False, False)

        db = open_database(session, server, file_path)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No document with UNID {unid!r}")

        # Document Locking (a database-level feature, off by default - confirmed
        # by hand it raises "Document Locking not enabled or no Master Lock
        # Database specified" if the database doesn't have it configured, even
        # with provisionalok=True) is the only way Notes itself can tell us
        # "someone else is editing this." Only check/acquire it when the
        # database actually has it enabled; there's nothing to check otherwise.
        locking_enabled = db.IsDocumentLockingEnabled
        if locking_enabled:
            if not doc.Lock("", True):
                holders = [h for h in doc.LockHolders if h]
                holder_text = f" (locked by: {', '.join(holders)})" if holders else ""
                raise DocumentLockedError(
                    f"Document {unid} is locked and cannot be modified{holder_text}. "
                    "Ask them to release the lock, or try again later."
                )

        try:
            # Save() does NOT detect or refuse a stale write on its own -
            # confirmed by hand: two independent in-memory copies of the same
            # document both saved successfully with Save(False, False), the
            # second silently overwriting the first with no error and no
            # conflict document created. That's a front-end (NotesUIDocument)
            # behavior, not this backend class. So conflict detection has to be
            # manual: capture LastModified before editing, then re-check it
            # right before saving - if it changed, someone else saved a change
            # in between. Never save over that; reload fresh and reapply our
            # fields once (fields is a declarative "set these to X", not a
            # diff, so reapplying it onto the now-current document preserves
            # the other editor's other field changes). If it races a second
            # time, give up rather than loop.
            last_modified_before = doc.LastModified
            _apply(doc)

            current = db.GetDocumentByUNID(unid)
            if current.LastModified != last_modified_before:
                doc = current
                last_modified_before = doc.LastModified
                _apply(doc)
                recheck = db.GetDocumentByUNID(unid)
                if recheck.LastModified != last_modified_before:
                    raise SaveConflictError(
                        f"Save conflict: document {unid} keeps being modified by "
                        "someone else while updating it. Not saving - please "
                        "re-read the document and retry."
                    )

            doc.Save(False, False)
            return {"unid": doc.UniversalID, "last_modified": str(doc.LastModified)}
        finally:
            if locking_enabled:
                doc.Unlock()

    return backend.run(_op)
