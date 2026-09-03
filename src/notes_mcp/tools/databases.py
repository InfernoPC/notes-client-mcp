"""Read-only tools for basic database/document access.

Every function does its entire COM interaction inside one NotesBackend.run()
closure and returns only plain Python data - see notes_backend.open_database
for why raw COM objects must never cross back out of that closure.
"""

from __future__ import annotations

import base64
import csv
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..notes_backend import NotesBackend, open_database

_DXL_NS = "{http://www.lotus.com/dxl}"

# NotesItem.Type values actually seen via COM on this Domino version (there is
# no early-bound constants module available with the late-bound Dispatch this
# project uses, so these are hardcoded from observation, not from a symbolic
# constant). Only RICHTEXT (1) matters to the code below; the rest are here
# so _document_to_dict's "type" field is at least readable instead of a bare
# int for the common cases.
_ITEM_TYPE_RICHTEXT = 1
_ITEM_TYPE_NAMES = {
    1: "richtext",
    1024: "datetime",
    1074: "names",
    1075: "readers",
    1076: "authors",
    1280: "text",
}


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
    """Return basic metadata (title, size, FT-index status) for a database.

    `server` is a Notes hierarchical name, abbreviated form (e.g.
    "Server1/ACME") - the same form get_mail_database_info returns for the
    mail server. `file_path` is relative to that server's Data directory and
    is frequently NOT just a bare filename - it commonly includes one or more
    subfolders (e.g. "subdir\\name.nsf"), and is case-sensitive on some
    platforms. Don't guess either value. Reliable ways to get the exact
    pair: a document's doclink/URL (notes://server/replica-or-path/...), the
    database's Properties dialog in the Notes client, or - if you only have
    a Domino Designer local workspace cache - its bookmark folder names,
    which encode "server/path" with '/' escaped as '_2f' and '\\' as '_5c'
    (e.g. a folder named "Server1_2fACME" with a file inside named
    "subdir_5cname.nsf" decodes to server "Server1/ACME", file_path
    "subdir\\name.nsf"). If a call fails, the error message includes the
    underlying reason (e.g. "Database open failed") rather than a bare
    generic failure - read it before retrying with a guessed variant."""

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
    rich_text_items = []
    for item in doc.Items:
        try:
            item_type = item.Type
        except Exception:  # noqa: BLE001
            item_type = None
        if item_type == _ITEM_TYPE_RICHTEXT:
            rich_text_items.append(item.Name)
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
        # `.Text`/GetItemValue on a rich text item returns plain text only -
        # any pasted image or file attachment is silently dropped. If this
        # list is non-empty and you need the actual images/attachments, call
        # extract_document_media on this same document.
        "rich_text_items": rich_text_items,
    }


def _extract_media(session, doc, unid: str, output_dir: str | None) -> list[dict]:
    """Shared by extract_document_media and read_document(include_media=True) -
    see extract_document_media's docstring for the two extraction mechanisms
    this implements and why both are needed."""
    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = Path(tempfile.gettempdir()) / f"notes_media_{unid}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []

    # --- Real attachments / OLE objects, per rich text item ---
    for item in doc.Items:
        try:
            item_type = item.Type
        except Exception:  # noqa: BLE001
            continue
        if item_type != _ITEM_TYPE_RICHTEXT:
            continue
        try:
            embedded = item.EmbeddedObjects
        except Exception:  # noqa: BLE001
            embedded = None
        for idx, obj in enumerate(embedded or []):
            ext = Path(obj.Name).suffix or ".bin"
            out_path = out_dir / f"{item.Name}_{idx}{ext}"
            obj.ExtractFile(str(out_path))
            results.append(
                {
                    "kind": "attachment",
                    "item_name": item.Name,
                    "name": obj.Name,
                    "output_path": str(out_path),
                    "size_bytes": out_path.stat().st_size,
                }
            )

    # --- Pasted-in pictures, whole-document DXL export ---
    exporter = session.CreateDXLExporter()
    exporter.ConvertNotesBitmapsToGIF = True
    dxl = exporter.Export(doc)

    patterns = [
        ("inline_image", "gif", r"<gif>(.*?)</gif>"),
        ("inline_image", "jpg", r"<jpeg>(.*?)</jpeg>"),
    ]
    img_idx = 0
    for kind, ext, pattern in patterns:
        for m in re.finditer(pattern, dxl, re.DOTALL):
            try:
                raw = base64.b64decode(m.group(1).strip())
            except Exception:  # noqa: BLE001
                continue
            if len(raw) < 4096:
                continue  # thumbnail, not real content - see docstring
            out_path = out_dir / f"inline_{img_idx}.{ext}"
            out_path.write_bytes(raw)
            results.append(
                {
                    "kind": kind,
                    "item_name": None,
                    "name": out_path.name,
                    "output_path": str(out_path),
                    "size_bytes": len(raw),
                }
            )
            img_idx += 1

    return results


def read_document(
    backend: NotesBackend,
    server: str,
    file_path: str,
    unid: str,
    include_media: bool = False,
    media_output_dir: str | None = None,
    include_tables: bool = False,
) -> dict:
    """Read one document's fields by UniversalID. Set include_media=True to
    also extract any images/attachments in the same call (see
    extract_document_media's docstring for what that covers and how) - the
    result gains a "media" key with the same shape extract_document_media
    returns. Set include_tables=True to also extract every rich text table
    as plain rows/cells (see extract_document_tables's docstring) - adds a
    "tables" key, same shape extract_document_tables returns. Both default
    to False for a plain field read with no local file writes / DXL export;
    check the result's "rich_text_items" list and call extract_document_media
    / extract_document_tables separately afterward if you only sometimes
    need them and want to avoid paying for a DXL export on every read."""

    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No document with UNID {unid!r}")
        result = _document_to_dict(doc)
        if include_media:
            result["media"] = _extract_media(session, doc, unid, media_output_dir)
        if include_tables:
            result["tables"] = _extract_tables(session, doc)
        return result

    return backend.run(_op)


def extract_document_media(
    backend: NotesBackend,
    server: str,
    file_path: str,
    unid: str,
    output_dir: str | None = None,
) -> list[dict]:
    """Extract every image and file attachment out of a document's rich text
    fields to local files, so they can actually be viewed (read_document's
    items only ever contain plain text - a rich text field's `.Text`/
    GetItemValue value silently drops any image or attachment). Check
    read_document's "rich_text_items" first; only call this when that list
    is non-empty. (Or pass include_media=True to read_document instead, to
    get both in one call.)

    There are two genuinely different kinds of embedded content, extracted
    two different ways (confirmed by hand - neither mechanism finds the
    other's content):

    - Real file attachments and OLE objects: found via each rich text
      item's `.EmbeddedObjects`, saved with `NotesEmbeddedObject.ExtractFile`.
    - Pasted-in pictures (e.g. a screenshot pasted directly into the body):
      stored as raw Notes-bitmap CD records, invisible to `.EmbeddedObjects`
      and to `NotesRichTextNavigator` element search. The only way to reach
      them is a whole-document DXL export with
      `NotesDXLExporter.ConvertNotesBitmapsToGIF = True`, which converts them
      to base64-encoded `<gif>`/`<jpeg>` blocks in the exported XML - decoded
      and saved here. `<gif originalformat='notesbitmap'>` blocks are
      skipped (those are auto-generated attachment/OLE thumbnails, not real
      pictures), and anything under 4KB after decoding is skipped too (still
      almost always a thumbnail, not real content).

    Returns a list of {kind, item_name, name, output_path, size_bytes} -
    kind is "attachment" or "inline_image" (inline_image has no item_name;
    DXL export is whole-document, not scoped to one field, so the source
    field isn't recoverable from it). output_dir defaults to a generated
    per-document folder under the system temp directory."""

    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No document with UNID {unid!r}")
        return _extract_media(session, doc, unid, output_dir)

    return backend.run(_op)


def _cell_dict(cell) -> dict:
    """A <tablecell>'s DXL attributes for merged cells and background color
    are `columnspan`/`rowspan`/`bgcolor` (confirmed by hand); each defaults
    to absent (colspan/rowspan of 1, no bgcolor) when the cell isn't merged
    or colored. Not grid-reconstructed (no attempt to figure out which
    logical row/column a spanned cell's neighbors land in) - this is the raw
    per-row cell list as DXL encodes it, which already carries everything
    needed to reconstruct the visual layout by hand."""
    return {
        "text": "".join(cell.itertext()).strip(),
        "colspan": int(cell.get("columnspan", "1")),
        "rowspan": int(cell.get("rowspan", "1")),
        "bgcolor": cell.get("bgcolor"),
    }


def _extract_tables(session, doc) -> list[dict]:
    """DXL-export the whole document and pull every rich text table out of
    the resulting XML into rows of cells. Unlike pasted pictures, tables
    show up as real structured elements in DXL (<table>/<tablerow>/
    <tablecell>/<par>) - no bitmap conversion or special handling needed,
    just parse it (confirmed by hand: a table's numbers/text, merged cells,
    and background colors all come through exactly as authored). A
    `tablerow`'s `tablabel` attribute, when present, is a per-row label
    (seen on tab-style tables) - included since it's often the only
    human-readable identifier for that row."""
    exporter = session.CreateDXLExporter()
    dxl = exporter.Export(doc)
    root = ET.fromstring(dxl)

    results = []
    for item in root.findall(f"{_DXL_NS}item"):
        item_name = item.get("name")
        richtext = item.find(f"{_DXL_NS}richtext")
        if richtext is None:
            continue
        for table_index, table in enumerate(richtext.findall(f".//{_DXL_NS}table")):
            rows = []
            row_labels = []
            for row in table.findall(f"{_DXL_NS}tablerow"):
                row_labels.append(row.get("tablabel"))
                rows.append([_cell_dict(cell) for cell in row.findall(f"{_DXL_NS}tablecell")])
            results.append(
                {
                    "item_name": item_name,
                    "table_index": table_index,
                    "rows": rows,
                    "row_labels": row_labels,
                }
            )
    return results


def extract_document_tables(
    backend: NotesBackend,
    server: str,
    file_path: str,
    unid: str,
) -> list[dict]:
    """Extract every rich text table in a document as rows of cells -
    read_document's plain-text item values collapse a table's structure
    away entirely (all cell text runs together with no row/column
    boundaries, no merge/color info at all). Returns a list of {item_name,
    table_index, rows, row_labels} - `rows` is a list of rows, each a list
    of cell dicts {text, colspan, rowspan, bgcolor} in column order
    (colspan/rowspan default to 1, bgcolor to null, when the cell isn't
    merged/colored); `row_labels` is the same length as `rows` and holds
    each row's `tablabel` attribute (a per-row label seen on tab-style
    tables), or null where a row has none. This is the raw per-row cell
    list as DXL encodes it, not a reconstructed visual grid - a merged
    cell's neighbors on other rows aren't figured out for you, but
    colspan/rowspan/bgcolor is everything needed to do that by hand. A
    document/field can contain more than one table, hence the flat list
    rather than one table per item_name."""

    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No document with UNID {unid!r}")
        return _extract_tables(session, doc)

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
