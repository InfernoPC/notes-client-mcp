"""Read-only mail tools, built on the user's mail database
(resolved automatically from notes.ini MailServer/MailFile)."""

from __future__ import annotations

from ..notes_backend import NotesBackend, open_database


def _mail_db(session):
    server = session.GetEnvironmentString("MailServer", True)
    file_path = session.GetEnvironmentString("MailFile", True)
    return open_database(session, server, file_path)


def list_folders(backend: NotesBackend) -> list[dict]:
    def _op(session):
        db = _mail_db(session)
        out = []
        for v in db.Views:
            if v.IsFolder:
                out.append({"name": v.Name, "aliases": list(v.Aliases) if v.Aliases else []})
        return out

    return backend.run(_op)


def search_mail(backend: NotesBackend, query: str, folder: str = "($Inbox)", limit: int = 20) -> list[dict]:
    """Full-text search within one folder/view if the mail db has an FT index,
    otherwise falls back to a linear Subject/From substring scan."""

    def _op(session):
        db = _mail_db(session)
        view = db.GetView(folder)
        if view is None:
            raise ValueError(f"No folder/view named {folder!r}")

        out = []
        if db.IsFTIndexed:
            collection = db.FTSearch(query, limit)
            count = 0
            doc = collection.GetFirstDocument()
            while doc is not None and count < limit:
                out.append(_summarize(doc))
                doc = collection.GetNextDocument(doc)
                count += 1
            return out

        # No FT index: linear scan of the view for a Subject/From substring match.
        needle = query.lower()
        nav = view.CreateViewNav()
        entry = nav.GetFirst()
        count = 0
        while entry is not None and count < limit:
            if entry.IsDocument:
                doc = entry.Document
                summary = _summarize(doc)
                haystack = f"{summary.get('subject', '')} {summary.get('from', '')}".lower()
                if needle in haystack:
                    out.append(summary)
                    count += 1
            entry = nav.GetNext(entry)
        return out

    return backend.run(_op)


def read_mail(backend: NotesBackend, unid: str) -> dict:
    def _op(session):
        db = _mail_db(session)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No mail document with UNID {unid!r}")
        summary = _summarize(doc)
        try:
            summary["body"] = doc.GetItemValue("Body")[0]
        except Exception:  # noqa: BLE001 - rich text bodies don't always render cleanly via .Text
            summary["body"] = None
        return summary

    return backend.run(_op)


def _summarize(doc) -> dict:
    def _first(name, default=None):
        try:
            values = doc.GetItemValue(name)
            return values[0] if values else default
        except Exception:  # noqa: BLE001
            return default

    return {
        "unid": doc.UniversalID,
        "subject": _first("Subject", ""),
        "from": _first("From", ""),
        "sendto": _first("SendTo", ""),
        "posted_date": str(_first("PostedDate", "")),
    }
