"""notes-client-mcp: single-process MCP server.

Spawned directly by the MCP client (Claude Desktop/Code, GitHub Copilot,
...) over stdio. This one process does everything: holds the backend
NotesSession COM session (on a dedicated STA thread - see sta_worker.py)
and speaks MCP stdio.

Must be launched with a Python interpreter whose bitness matches the
installed Notes Client (see README.md) - COM automation requires this.

Tools are tagged "read", "design", or "write" and only registered if the
active profile includes that tag - see PROFILES below and the four
console-script entry points in pyproject.toml (notes-client-mcp[-design|
-write|-all]). Profile is picked, in order: the `profile` argument to main(),
then the NOTES_MCP_PROFILE env var, then "read".

Write tools ask for interactive confirmation via MCP elicitation before
making any change - see ConfirmWrite/_confirm below.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from . import updates
from .notes_backend import NotesBackend, NotesConnectionError
from .tiers import PROFILES, TOOL_TAGS
from .tools import databases, design, write

# Load .env from the project root (two levels above this file:
# src/notes_mcp/server.py -> src -> project root) regardless of the current
# working directory this process was launched from. NOTES_PASSWORD is read
# later, at connect() time, so it just needs to be in os.environ before
# main() calls backend.connect() - see notes_backend.py for the priority
# order and the explicitly-chosen plaintext-on-disk trade-off this represents.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_BASE_INSTRUCTIONS = (
    "Access to the local HCL Notes Client (mail, databases, and NSF "
    "design elements) via backend COM automation. Requires the HCL "
    "Notes Client to be installed and the user's ID file available on "
    "this machine. Write tools ask the user for interactive "
    "confirmation before making any change."
)

server = MCPServer(name="notes-client-mcp", instructions=_BASE_INSTRUCTIONS)

backend = NotesBackend()


class ConfirmWrite(BaseModel):
    confirm: bool = Field(description="true to proceed with this write, false to cancel")


async def _confirm(ctx: Context, message: str) -> bool:
    result = await ctx.elicit(message=message, schema=ConfirmWrite)
    return result.action == "accept" and bool(result.data and result.data.confirm)


# ---- read tools ----------------------------------------------------------


def get_mail_database_info() -> dict:
    """Resolve and return metadata for the current user's mail database."""
    return databases.get_mail_database_info(backend)


def get_database_info(server_name: str, file_path: str) -> dict:
    """Return basic metadata (title, size, FT-index status) for a database."""
    return databases.get_database_info(backend, server_name, file_path)


def read_document(
    server_name: str,
    file_path: str,
    unid: str,
    include_media: bool = False,
    media_output_dir: str | None = None,
    include_tables: bool = False,
) -> dict:
    """Read one document's fields by UniversalID from any database. Rich
    text fields come back as plain text only - check the "rich_text_items"
    list in the result and call extract_document_media/
    extract_document_tables if you need the actual images/attachments or
    table structure in one of them, or pass include_media=True /
    include_tables=True to get them in this one call (adds "media"/"tables"
    keys, same shape those tools return - each costs a DXL export even if
    the document turns out to have nothing to extract, so leave both False
    for a plain field read when you don't yet know whether you'll need
    them)."""
    return databases.read_document(
        backend, server_name, file_path, unid, include_media, media_output_dir, include_tables
    )


def extract_document_media(
    server_name: str,
    file_path: str,
    unid: str,
    output_dir: str | None = None,
) -> list[dict]:
    """Extract every image and file attachment out of a document's rich text
    fields to local files (so they can be viewed directly) - read_document's
    plain-text item values silently drop both. Covers two different kinds of
    embedded content, found two different ways: real file attachments/OLE
    objects (via each item's EmbeddedObjects) and pasted-in pictures like
    screenshots (only reachable via a DXL export with bitmap-to-GIF
    conversion - they aren't real "embedded objects" at all). See
    databases.extract_document_media's docstring for the full explanation.
    Defaults to a generated per-document folder under the system temp
    directory if output_dir is omitted."""
    return databases.extract_document_media(backend, server_name, file_path, unid, output_dir)


def extract_document_tables(server_name: str, file_path: str, unid: str) -> list[dict]:
    """Extract every rich text table in a document as rows of cells -
    read_document's plain-text item values collapse a table's structure
    (and any merged cells / background color) away entirely. Returns a
    list of {item_name, table_index, rows, row_labels}; each cell in
    `rows` is {text, colspan, rowspan, bgcolor}. A document can contain
    more than one table, hence the flat list. See
    databases.extract_document_tables's docstring for details."""
    return databases.extract_document_tables(backend, server_name, file_path, unid)


def search_view(
    server_name: str,
    file_path: str,
    view_name: str,
    limit: int = 20,
    columns: list[str] | None = None,
    include_conflicts: bool = False,
    include_responses: bool = False,
    category: str | None = None,
    match: dict | None = None,
    skip: int = 0,
) -> list[dict]:
    """List rows from a view/folder in view order (fast index scan).

    Replication/save-conflict and response rows are skipped by default: a
    conflict is a separate document the view index does return, and its
    stored fields can be byte-identical to the winning document's, so it
    otherwise shows up as a duplicate row that nothing downstream can tell
    apart. Set include_conflicts/include_responses to get them, which also
    adds an is_conflict/is_response column. This returns a bare list, so the
    skipped count is not reported - use export_view_csv if you need it."""
    return databases.search_view(
        backend,
        server_name,
        file_path,
        view_name,
        limit,
        columns,
        include_conflicts,
        include_responses,
        category,
        match,
        skip,
    )


def export_view_csv(
    server_name: str,
    file_path: str,
    view_name: str,
    output_path: str | None = None,
    columns: list[str] | None = None,
    limit: int = 10000,
    include_conflicts: bool = False,
    include_responses: bool = False,
    category: str | None = None,
    match: dict | None = None,
    skip: int = 0,
) -> dict:
    """Export a view's rows straight to a local CSV file (written on this
    machine, not returned over MCP - handles views far larger than a single
    tool result could carry). Defaults to a generated path in the system
    temp directory if output_path is omitted.

    Replication/save-conflict and response rows are skipped by default: a
    conflict is a separate document the view index does return, and its
    stored fields can be byte-identical to the winning document's, so it
    otherwise shows up as a duplicate row that nothing downstream can tell
    apart. Set include_conflicts/include_responses to get them, which also
    adds an is_conflict/is_response column. The result's `skipped` counts
    report how many rows each exclusion dropped."""
    return databases.export_view_csv(
        backend,
        server_name,
        file_path,
        view_name,
        output_path,
        columns,
        limit,
        include_conflicts,
        include_responses,
        category,
        match,
        skip,
    )


def find_document_by_key(
    server_name: str,
    file_path: str,
    view_name: str,
    key: str | list[str],
    exact: bool = True,
) -> dict | None:
    """Fast lookup by a view's sorted column(s) (uses the view index). Pass a
    list for `key` to match a categorized view's leading columns in order.
    `exact=False` allows a prefix/partial match. Returns null if nothing
    matches. Prefer this over search_database when the value you're looking
    up is a real column in an existing view."""
    return databases.find_document_by_key(backend, server_name, file_path, view_name, key, exact)


def search_database(server_name: str, file_path: str, formula: str, max_docs: int = 50) -> list[dict]:
    """Search a database with a Notes @formula, evaluated against every
    document rather than using a view index - much slower than
    search_view/find_document_by_key, so prefer those when a suitable view
    already exists. max_docs caps the result size."""
    return databases.search_database(backend, server_name, file_path, formula, max_docs)


# ---- design tools ---------------------------------------------------------


def list_forms(server_name: str, file_path: str) -> list[dict]:
    """List forms in a database (name, aliases, fields)."""
    return design.list_forms(backend, server_name, file_path)


def list_views(server_name: str, file_path: str) -> list[dict]:
    """List views/folders in a database, including selection formulas and column formulas."""
    return design.list_views(backend, server_name, file_path)


def list_view_categories(server_name: str, file_path: str, view_name: str, max_level: int = 0) -> list[dict]:
    """List a categorized view's own category values (not raw document
    fields - the categorized column is very often a formula), without
    touching any document entries regardless of view size. max_level=0 is
    top-level only; increase to include deeper category levels (e.g. 1 for
    a "Cat1\\Cat2"-style categorized column). Use the returned values
    directly with find_document_by_key."""
    return design.list_view_categories(backend, server_name, file_path, view_name, max_level)


def list_agents(server_name: str, file_path: str) -> list[dict]:
    """List agents in a database (name, trigger/target, enabled state, query for query agents)."""
    return design.list_agents(backend, server_name, file_path)


def list_design_elements(server_name: str, file_path: str, kind: str) -> list[dict]:
    """List design notes of one kind by name, for kinds without a typed
    listing tool of their own (list_forms/list_views/list_agents cover
    those). Valid kind values: forms, views, folders, agents, subforms,
    outlines, pages, framesets, script_libraries, shared_fields, actions,
    database_script, navigators, image_resources, java_resources,
    stylesheet_resources, data_connections, replication_formulas, profiles,
    acl, icon, help_about, help_using. "actions" (shared actions) is a
    single aggregate note, not individually listable - use
    export_design_dxl(kinds=["actions"]) for its contents instead."""
    return design.list_design_elements(backend, server_name, file_path, kind)


def get_database_settings(server_name: str, file_path: str) -> dict:
    """Database-level settings beyond get_database_info: categories, design
    template, replica ID, quota/usage, managers, document/design locking,
    multi-db search, address-book flags, pending-delete state."""
    return design.get_database_settings(backend, server_name, file_path)


def list_acl(server_name: str, file_path: str) -> dict:
    """Database ACL: defined roles, and each entry's name, access level
    (standard Domino 0-6 scale, with a human-readable name), roles, and a
    couple of common capability flags."""
    return design.list_acl(backend, server_name, file_path)


def list_form_fields(
    server_name: str,
    file_path: str,
    name_filter: str | None = None,
    kinds: list[str] | None = None,
) -> list[dict]:
    """Every field of the matching forms/subforms, with its formulas
    (defaultvalue, inputvalidation, inputtranslation, hidewhen, ...) already
    parsed out of DXL - use this instead of export_design_dxl when you want
    to know what a field computes. `name_filter` is a case-insensitive
    substring match on the design note title, so it can match several notes
    and the result is a list of them. `kinds` defaults to
    ["forms", "subforms"]."""
    return design.list_form_fields(backend, server_name, file_path, name_filter, kinds)


def list_design_actions(
    server_name: str,
    file_path: str,
    name_filter: str | None = None,
    kinds: list[str] | None = None,
) -> list[dict]:
    """Every action button of the matching design notes, with its click and
    hidewhen code - the fastest way to answer "who can press this, and what
    does it do". Titles use a backslash for submenus. `kinds` defaults to
    ["forms", "subforms", "views"]."""
    return design.list_design_actions(backend, server_name, file_path, name_filter, kinds)


def list_subform_refs(
    server_name: str,
    file_path: str,
    name_filter: str | None = None,
    kinds: list[str] | None = None,
) -> list[dict]:
    """Which subforms the matching forms pull in, and whether each reference
    is static or computed. A computed one returns its `value` formula
    verbatim - that formula is usually what decides which version of a
    layout a given document renders, so it is the thing to read when tracing
    that. `kinds` defaults to ["forms", "subforms"]."""
    return design.list_subform_refs(backend, server_name, file_path, name_filter, kinds)


def export_design_dxl(
    server_name: str,
    file_path: str,
    kinds: list[str] | None = None,
    name_filter: str | None = None,
    output_path: str | None = None,
) -> str | dict:
    """Export selected design notes as DXL (XML), including full agent
    LotusScript/formula source, form/view formulas, and (for kinds without
    individual listing, like "actions"/shared actions) their full contents.
    `kinds` defaults to ["forms", "views", "agents"] - see
    list_design_elements's docstring for every valid value. Requires
    Designer-level ACL access on the target database.

    Pass `output_path` to write the DXL to a file and get back
    {output_path, size_bytes, ...} instead of the document itself. Real
    exports run 100 KB - 2.3 MB, past what one tool result can carry, so
    writing to disk is usually what you want; a relative or omitted path
    lands under the export directory (NOTES_MCP_EXPORT_DIR, default
    <cwd>/notes-exports).

    Prefer list_form_fields / list_design_actions / list_subform_refs when
    you want one specific formula. **Do not parse this output with regular
    expressions** - DXL puts arbitrary whitespace between `<code` and
    `event=`, and self-closes `<field/>` when it has no code, so the obvious
    patterns silently attribute one element's formula to another. Parse it
    as XML."""
    return design.export_design_dxl(backend, server_name, file_path, kinds, name_filter, output_path)


# ---- write tools (each requires interactive confirmation) ----------------


async def create_document(
    server_name: str,
    file_path: str,
    form: str,
    fields: dict[str, Any],
    ctx: Context,
) -> dict:
    """Create a new document. Asks for interactive confirmation before writing anything."""
    preview = "\n".join(f"  {k} = {v}" for k, v in fields.items())
    ok = await _confirm(
        ctx,
        f"Create a new '{form}' document in {server_name}!!{file_path}:\n{preview}\n"
        "This writes to the database and cannot be easily undone. Proceed?",
    )
    if not ok:
        return {"status": "cancelled"}
    return write.create_document(backend, server_name, file_path, form, fields)


async def update_document(
    server_name: str,
    file_path: str,
    unid: str,
    fields: dict[str, Any],
    ctx: Context,
) -> dict:
    """Update an existing document's fields by UniversalID. Asks for interactive
    confirmation before writing anything."""
    preview = "\n".join(f"  {k} = {v}" for k, v in fields.items())
    ok = await _confirm(
        ctx,
        f"Update document {unid} in {server_name}!!{file_path}, setting:\n{preview}\n"
        "This overwrites existing field values and cannot be easily undone. Proceed?",
    )
    if not ok:
        return {"status": "cancelled"}
    return write.update_document(backend, server_name, file_path, unid, fields)


# ---- registration ----------------------------------------------------------

_TOOL_FUNCS: dict[str, object] = {
    "get_mail_database_info": get_mail_database_info,
    "get_database_info": get_database_info,
    "read_document": read_document,
    "extract_document_media": extract_document_media,
    "extract_document_tables": extract_document_tables,
    "search_view": search_view,
    "export_view_csv": export_view_csv,
    "find_document_by_key": find_document_by_key,
    "search_database": search_database,
    "list_forms": list_forms,
    "list_views": list_views,
    "list_view_categories": list_view_categories,
    "list_agents": list_agents,
    "list_design_elements": list_design_elements,
    "get_database_settings": get_database_settings,
    "list_acl": list_acl,
    "list_form_fields": list_form_fields,
    "list_design_actions": list_design_actions,
    "list_subform_refs": list_subform_refs,
    "export_design_dxl": export_design_dxl,
    "create_document": create_document,
    "update_document": update_document,
}


def register_tools(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}, must be one of {sorted(PROFILES)}")
    active = PROFILES[profile]
    for name, fn in _TOOL_FUNCS.items():
        if TOOL_TAGS[name] & active:
            server.tool()(fn)


def main(profile: str | None = None) -> None:
    global server

    profile = profile or os.environ.get("NOTES_MCP_PROFILE", "read")

    # MCPServer.instructions is a read-only property and the client only ever
    # reads it once, at initialize - so an update notice has to be in place
    # before the object is built. Rebuilding here rather than reaching into
    # the SDK's private lowlevel server keeps this on the public API; the
    # constructor does no I/O, and tools are registered onto whichever
    # instance this leaves behind.
    notice = updates.startup_notice()
    if notice:
        server = MCPServer(
            name="notes-client-mcp",
            instructions=f"{_BASE_INSTRUCTIONS}\n\n{notice}",
        )
        print(f"notes-client-mcp: {notice.splitlines()[0]}", file=sys.stderr)

    register_tools(profile)

    print("notes-client-mcp: connecting to HCL Notes...", file=sys.stderr)
    try:
        username = backend.connect()
    except NotesConnectionError as exc:
        print(f"notes-client-mcp: failed to connect: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"notes-client-mcp: connected as {username!r}, profile={profile!r}", file=sys.stderr)

    try:
        server.run("stdio")
    finally:
        backend.shutdown()


def main_read() -> None:
    main(profile="read")


def main_design() -> None:
    main(profile="design")


def main_write() -> None:
    main(profile="write")


def main_all() -> None:
    main(profile="all")


if __name__ == "__main__":
    main()
