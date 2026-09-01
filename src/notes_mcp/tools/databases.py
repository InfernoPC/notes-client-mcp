"""Read-only tools for basic database/document access.

Every function does its entire COM interaction inside one NotesBackend.run()
closure and returns only plain Python data - see notes_backend.open_database
for why raw COM objects must never cross back out of that closure.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

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


def _document_to_dict(doc) -> dict:
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


def read_document(backend: NotesBackend, server: str, file_path: str, unid: str) -> dict:
    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No document with UNID {unid!r}")
        return _document_to_dict(doc)

    return backend.run(_op)


def find_document_by_key(
    backend: NotesBackend,
    server: str,
    file_path: str,
    view_name: str,
    key: str | list[str],
    exact: bool = True,
) -> dict | None:
    """Fast lookup by a view's sorted column(s), using the view index -
    NotesView.GetDocumentByKey(key, exact). `key` matches a single sorted
    column, or pass a list to match a categorized view's leading columns in
    order. `exact=False` allows a prefix/partial match. Returns None (not an
    error) if nothing matches - prefer this over search_database when the
    lookup value is a real column in an existing view."""

    def _op(session):
        db = open_database(session, server, file_path)
        view = db.GetView(view_name)
        if view is None:
            raise ValueError(f"No view named {view_name!r}")
        doc = view.GetDocumentByKey(key, exact)
        return _document_to_dict(doc) if doc is not None else None

    return backend.run(_op)


def search_database(
    backend: NotesBackend,
    server: str,
    file_path: str,
    formula: str,
    max_docs: int = 50,
) -> list[dict]:
    """Search a database with a Notes @formula (NotesDatabase.Search) -
    evaluates the formula against every document rather than using a view
    index, so it is much slower than search_view/find_document_by_key.
    Prefer those when the data you need is already exposed by an existing
    view; use this only when no suitable view exists. max_docs caps the
    result size (and roughly the work done) since a broad formula can match
    a very large fraction of the database."""

    def _op(session):
        db = open_database(session, server, file_path)
        collection = db.Search(formula, None, max_docs)
        # db.Search's maxdocs argument only caps collection.Count - confirmed
        # by hand that iterating via GetFirstDocument/GetNextDocument walks
        # every matching document regardless (maxdocs=3 against 56 matches
        # still yielded all 56 through the iterator, though .Count correctly
        # reported 3). Cap the loop explicitly instead of trusting the
        # collection to stop on its own.
        out = []
        doc = collection.GetFirstDocument()
        count = 0
        while doc is not None and (max_docs <= 0 or count < max_docs):
            out.append(_document_to_dict(doc))
            doc = collection.GetNextDocument(doc)
            count += 1
        return out

    return backend.run(_op)


def _walk_view_rows(session, server: str, file_path: str, view_name: str, columns, limit: int):
    """Shared by search_view and export_view_csv - the icon-column alignment
    fix (see below) must not be duplicated between the two."""
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

    rows = []
    nav = view.CreateViewNav()
    entry = nav.GetFirst()
    count = 0
    while entry is not None and count < limit:
        if entry.IsDocument:
            values = entry.ColumnValues
            row = {name: (values[idx] if idx < len(values) else None) for name, idx in col_defs}
            row["unid"] = entry.UniversalID
            rows.append(row)
            count += 1
        entry = nav.GetNext(entry)
    return rows


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
    return backend.run(lambda session: _walk_view_rows(session, server, file_path, view_name, columns, limit))


def export_view_csv(
    backend: NotesBackend,
    server: str,
    file_path: str,
    view_name: str,
    output_path: str | None = None,
    columns: list[str] | None = None,
    limit: int = 10000,
) -> dict:
    """Export a view's rows straight to a local CSV file (UTF-8 with a BOM,
    so Excel opens non-ASCII text correctly), written from inside this
    process rather than returned over MCP - keeps large views out of MCP
    tool-result size limits entirely (confirmed by hand: a ~470-row view's
    JSON result already exceeded the tool-result size limit and got spilled
    to a side file instead of returned directly).

    If output_path is omitted, writes to a generated name in the system temp
    directory."""

    def _op(session):
        rows = _walk_view_rows(session, server, file_path, view_name, columns, limit)
        if output_path:
            path = output_path
        else:
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in view_name)
            path = str(Path(tempfile.gettempdir()) / f"{safe_name}.csv")
        fieldnames = list(rows[0].keys()) if rows else []
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return {"output_path": path, "row_count": len(rows)}

    return backend.run(_op)
