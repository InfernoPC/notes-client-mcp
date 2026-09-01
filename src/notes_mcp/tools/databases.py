"""Read-only tools for basic database/document access.

Every function does its entire COM interaction inside one NotesBackend.run()
closure and returns only plain Python data - see notes_backend.open_database
for why raw COM objects must never cross back out of that closure.
"""

from __future__ import annotations

from ..notes_backend import NotesBackend, open_database


def get_mail_database_info(backend: NotesBackend) -> dict:
    """Resolve the current user's mail database from notes.ini
    (MailServer/MailFile) and return basic metadata."""

    def _op(session):
        server = session.GetEnvironmentString("MailServer", True)
        file_path = session.GetEnvironmentString("MailFile", True)
        db = open_database(session, server, file_path)
        return {
            "server": db.Server,
            "file_path": db.FilePath,
            "title": db.Title,
            "size_bytes": db.Size,
            "is_ft_indexed": db.IsFTIndexed,
        }

    return backend.run(_op)


def get_database_info(backend: NotesBackend, server: str, file_path: str) -> dict:
    def _op(session):
        db = open_database(session, server, file_path)
        return {
            "server": db.Server,
            "file_path": db.FilePath,
            "title": db.Title,
            "size_bytes": db.Size,
            "is_ft_indexed": db.IsFTIndexed,
        }

    return backend.run(_op)


def read_document(backend: NotesBackend, server: str, file_path: str, unid: str) -> dict:
    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No document with UNID {unid!r}")
        items = {}
        for item in doc.Items:
            try:
                items[item.Name] = item.Text if hasattr(item, "Text") else doc.GetItemValue(item.Name)
            except Exception:  # noqa: BLE001 - some item types don't support .Text via COM
                try:
                    items[item.Name] = list(doc.GetItemValue(item.Name))
                except Exception:  # noqa: BLE001
                    items[item.Name] = None
        return {
            "unid": doc.UniversalID,
            "form": doc.GetItemValue("Form")[0] if doc.HasItem("Form") else None,
            "created": str(doc.Created),
            "last_modified": str(doc.LastModified),
            "items": items,
        }

    return backend.run(_op)


def search_view(
    backend: NotesBackend,
    server: str,
    file_path: str,
    view_name: str,
    limit: int = 20,
    columns: list[str] | None = None,
) -> list[dict]:
    """Walk a view's documents in view order and return column values (fast,
    uses the view index - does not open each document). `columns`, if given,
    selects a subset by title/item-name rather than renaming positionally."""

    def _op(session):
        db = open_database(session, server, file_path)
        view = db.GetView(view_name)
        if view is None:
            raise ValueError(f"No view named {view_name!r}")

        # (name, position) pairs from the view's real column order. Icon
        # columns still occupy a slot in entry.ColumnValues - dropping them
        # from the name list without keeping their original index silently
        # misaligns every later column's name with the wrong value. Confirmed
        # by hand on a real view with an icon column in the middle: that bug
        # produced e.g. "server" -> an icon status code, "filepath" -> the
        # real server name, "title" -> the real filepath, and dropped the
        # real description entirely.
        all_cols = [(c.Title or c.ItemName, i) for i, c in enumerate(view.Columns) if not c.IsIcon]
        if columns:
            wanted = set(columns)
            col_defs = [(name, idx) for name, idx in all_cols if name in wanted]
        else:
            col_defs = all_cols

        out = []
        nav = view.CreateViewNav()
        entry = nav.GetFirst()
        count = 0
        while entry is not None and count < limit:
            if entry.IsDocument:
                values = entry.ColumnValues
                row = {name: (values[idx] if idx < len(values) else None) for name, idx in col_defs}
                row["unid"] = entry.UniversalID
                out.append(row)
                count += 1
            entry = nav.GetNext(entry)
        return out

    return backend.run(_op)
