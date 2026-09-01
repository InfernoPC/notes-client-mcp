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
    uses the view index - does not open each document)."""

    def _op(session):
        db = open_database(session, server, file_path)
        view = db.GetView(view_name)
        if view is None:
            raise ValueError(f"No view named {view_name!r}")
        col_names = columns or [c.Title or c.ItemName for c in view.Columns if not c.IsIcon]
        out = []
        nav = view.CreateViewNav()
        entry = nav.GetFirst()
        count = 0
        while entry is not None and count < limit:
            if entry.IsDocument:
                values = entry.ColumnValues
                row = {}
                for i, name in enumerate(col_names):
                    row[name] = values[i] if i < len(values) else None
                row["unid"] = entry.UniversalID
                out.append(row)
                count += 1
            entry = nav.GetNext(entry)
        return out

    return backend.run(_op)
