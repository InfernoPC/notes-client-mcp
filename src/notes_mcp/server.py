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

from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from .notes_backend import NotesBackend, NotesConnectionError
from .tiers import PROFILES, TOOL_TAGS
from .tools import databases, design, mail, write

# Load .env from the project root (two levels above this file:
# src/notes_mcp/server.py -> src -> project root) regardless of the current
# working directory this process was launched from. NOTES_PASSWORD is read
# later, at connect() time, so it just needs to be in os.environ before
# main() calls backend.connect() - see notes_backend.py for the priority
# order and the explicitly-chosen plaintext-on-disk trade-off this represents.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

server = MCPServer(
    name="notes-client-mcp",
    instructions=(
        "Access to the local HCL Notes Client (mail, databases, and NSF "
        "design elements) via backend COM automation. Requires the HCL "
        "Notes Client to be installed and the user's ID file available on "
        "this machine. Write tools ask the user for interactive "
        "confirmation before making any change."
    ),
)

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


def read_document(server_name: str, file_path: str, unid: str) -> dict:
    """Read one document's fields by UniversalID from any database."""
    return databases.read_document(backend, server_name, file_path, unid)


def search_view(
    server_name: str,
    file_path: str,
    view_name: str,
    limit: int = 20,
    columns: list[str] | None = None,
) -> list[dict]:
    """List rows from a view/folder in view order (fast index scan)."""
    return databases.search_view(backend, server_name, file_path, view_name, limit, columns)


def export_view_csv(
    server_name: str,
    file_path: str,
    view_name: str,
    output_path: str | None = None,
    columns: list[str] | None = None,
    limit: int = 10000,
) -> dict:
    """Export a view's rows straight to a local CSV file (written on this
    machine, not returned over MCP - handles views far larger than a single
    tool result could carry). Defaults to a generated path in the system
    temp directory if output_path is omitted."""
    return databases.export_view_csv(backend, server_name, file_path, view_name, output_path, columns, limit)


def list_mail_folders() -> list[dict]:
    """List folders in the current user's mail database."""
    return mail.list_folders(backend)


def search_mail(query: str, folder: str = "($Inbox)", limit: int = 20) -> list[dict]:
    """Search the current user's mail (full-text if indexed, else Subject/From substring scan)."""
    return mail.search_mail(backend, query, folder, limit)


def read_mail(unid: str) -> dict:
    """Read one mail document (subject/from/sendto/date/body) by UniversalID."""
    return mail.read_mail(backend, unid)


# ---- design tools ---------------------------------------------------------


def list_forms(server_name: str, file_path: str) -> list[dict]:
    """List forms in a database (name, aliases, fields)."""
    return design.list_forms(backend, server_name, file_path)


def list_views(server_name: str, file_path: str) -> list[dict]:
    """List views/folders in a database, including selection formulas and column formulas."""
    return design.list_views(backend, server_name, file_path)


def list_agents(server_name: str, file_path: str) -> list[dict]:
    """List agents in a database (name, trigger/target, enabled state, query for query agents)."""
    return design.list_agents(backend, server_name, file_path)


def export_design_dxl(
    server_name: str,
    file_path: str,
    include_forms: bool = True,
    include_views: bool = True,
    include_agents: bool = True,
    name_filter: str | None = None,
) -> str:
    """Export selected design notes as DXL (XML), including full agent
    LotusScript/formula source and form/view formulas. Requires Designer-level
    ACL access on the target database."""
    return design.export_design_dxl(
        backend, server_name, file_path, include_forms, include_views, include_agents, name_filter
    )


# ---- write tools (each requires interactive confirmation) ----------------


async def create_document(
    server_name: str,
    file_path: str,
    form: str,
    fields: dict[str, str],
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
    fields: dict[str, str],
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


async def send_mail(sendto: str, subject: str, body: str, ctx: Context) -> dict:
    """Send an email from the current user's mail account. Asks for
    interactive confirmation before sending anything."""
    ok = await _confirm(
        ctx,
        f"Send mail to {sendto!r}\nSubject: {subject}\n\n{body}\n\nThis sends immediately. Proceed?",
    )
    if not ok:
        return {"status": "cancelled"}
    return write.send_mail(backend, sendto, subject, body)


# ---- registration ----------------------------------------------------------

_TOOL_FUNCS: dict[str, object] = {
    "get_mail_database_info": get_mail_database_info,
    "get_database_info": get_database_info,
    "read_document": read_document,
    "search_view": search_view,
    "export_view_csv": export_view_csv,
    "list_mail_folders": list_mail_folders,
    "search_mail": search_mail,
    "read_mail": read_mail,
    "list_forms": list_forms,
    "list_views": list_views,
    "list_agents": list_agents,
    "export_design_dxl": export_design_dxl,
    "create_document": create_document,
    "update_document": update_document,
    "send_mail": send_mail,
}


def register_tools(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}, must be one of {sorted(PROFILES)}")
    active = PROFILES[profile]
    for name, fn in _TOOL_FUNCS.items():
        if TOOL_TAGS[name] & active:
            server.tool()(fn)


def main(profile: str | None = None) -> None:
    profile = profile or os.environ.get("NOTES_MCP_PROFILE", "read")
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
