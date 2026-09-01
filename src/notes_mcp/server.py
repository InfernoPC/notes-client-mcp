"""notes-client-mcp MCP relay.

This process holds NO COM objects and asks for NO password. It is spawned
directly by the MCP client (Claude Desktop/Code, GitHub Copilot, ...) over
stdio, and forwards every tool call over HTTP to the Host Agent
(host_agent.py), which must already be running on 127.0.0.1 (see README.md).

Deliberately COM-free so it can run on any Python (32 or 64-bit) and is the
piece that gets packaged into the Docker image in Phase 2 - the container
never needs pywin32 or a matching Notes bitness, it just needs to reach the
Host Agent over the network (e.g. host.docker.internal).

Tools are tagged "read", "design", or "write" and only registered if the
active profile includes that tag - see PROFILES below and the four
console-script entry points in pyproject.toml (notes-client-mcp[-design|
-write|-all]). Profile is picked, in order: the `profile` argument to main(),
then the NOTES_MCP_PROFILE env var, then "read".
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from .tiers import PROFILES, TOOL_TAGS

HOST_AGENT_URL = os.environ.get("NOTES_HOST_AGENT_URL", "http://127.0.0.1:8765")


def _force_ipv4(url: str) -> str:
    """Rewrite url's host to a literal IPv4 address before connecting.

    Docker Desktop's `host.docker.internal` can resolve to both an IPv4 and
    an IPv6 address inside a container, where the IPv6 route is broken
    (ENETUNREACH) but IPv4 works fine - confirmed by hand while testing the
    Docker packaging. urllib's default dual-stack connect logic can hit the
    bad IPv6 address first and fail outright rather than falling back, so
    force IPv4 resolution here instead of trusting getaddrinfo's ordering.
    Falls back to the original url if resolution fails for any reason (e.g.
    running natively against 127.0.0.1, where this is a no-op anyway).
    """
    parts = urlsplit(url)
    if not parts.hostname:
        return url
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ipv4 = socket.getaddrinfo(parts.hostname, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError:
        return url
    return urlunsplit((parts.scheme, f"{ipv4}:{port}", parts.path, parts.query, parts.fragment))

server = MCPServer(
    name="notes-client-mcp",
    instructions=(
        "Access to the local HCL Notes Client (mail, databases, and NSF "
        "design elements), relayed to a separate Host Agent process that "
        "holds the actual Notes COM session. The Host Agent must be running "
        "(see README.md) before these tools will work. Write tools ask the "
        "user for interactive confirmation before making any change."
    ),
)


def _call_host_agent(tool: str, **kwargs) -> object:
    payload = json.dumps({"tool": tool, "args": kwargs}).encode("utf-8")
    req = urllib.request.Request(
        _force_ipv4(f"{HOST_AGENT_URL}/call"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach the Notes Host Agent at {HOST_AGENT_URL} ({exc}). "
            "Is `python -m notes_mcp.host_agent` running on this machine?"
        ) from exc
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Host Agent call failed"))
    return result["result"]


class ConfirmWrite(BaseModel):
    confirm: bool = Field(description="true to proceed with this write, false to cancel")


async def _confirm(ctx: Context, message: str) -> bool:
    result = await ctx.elicit(message=message, schema=ConfirmWrite)
    return result.action == "accept" and bool(result.data and result.data.confirm)


# ---- read tools ----------------------------------------------------------


def get_mail_database_info() -> dict:
    """Resolve and return metadata for the current user's mail database."""
    return _call_host_agent("get_mail_database_info")


def get_database_info(server_name: str, file_path: str) -> dict:
    """Return basic metadata (title, size, FT-index status) for a database."""
    return _call_host_agent("get_database_info", server_name=server_name, file_path=file_path)


def read_document(server_name: str, file_path: str, unid: str) -> dict:
    """Read one document's fields by UniversalID from any database."""
    return _call_host_agent("read_document", server_name=server_name, file_path=file_path, unid=unid)


def search_view(
    server_name: str,
    file_path: str,
    view_name: str,
    limit: int = 20,
    columns: list[str] | None = None,
) -> list[dict]:
    """List rows from a view/folder in view order (fast index scan)."""
    return _call_host_agent(
        "search_view",
        server_name=server_name,
        file_path=file_path,
        view_name=view_name,
        limit=limit,
        columns=columns,
    )


def list_mail_folders() -> list[dict]:
    """List folders in the current user's mail database."""
    return _call_host_agent("list_mail_folders")


def search_mail(query: str, folder: str = "($Inbox)", limit: int = 20) -> list[dict]:
    """Search the current user's mail (full-text if indexed, else Subject/From substring scan)."""
    return _call_host_agent("search_mail", query=query, folder=folder, limit=limit)


def read_mail(unid: str) -> dict:
    """Read one mail document (subject/from/sendto/date/body) by UniversalID."""
    return _call_host_agent("read_mail", unid=unid)


# ---- design tools ---------------------------------------------------------


def list_forms(server_name: str, file_path: str) -> list[dict]:
    """List forms in a database (name, aliases, fields)."""
    return _call_host_agent("list_forms", server_name=server_name, file_path=file_path)


def list_views(server_name: str, file_path: str) -> list[dict]:
    """List views/folders in a database, including selection formulas and column formulas."""
    return _call_host_agent("list_views", server_name=server_name, file_path=file_path)


def list_agents(server_name: str, file_path: str) -> list[dict]:
    """List agents in a database (name, trigger/target, enabled state, query for query agents)."""
    return _call_host_agent("list_agents", server_name=server_name, file_path=file_path)


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
    return _call_host_agent(
        "export_design_dxl",
        server_name=server_name,
        file_path=file_path,
        include_forms=include_forms,
        include_views=include_views,
        include_agents=include_agents,
        name_filter=name_filter,
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
    return _call_host_agent("create_document", server_name=server_name, file_path=file_path, form=form, fields=fields)


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
    return _call_host_agent("update_document", server_name=server_name, file_path=file_path, unid=unid, fields=fields)


async def send_mail(sendto: str, subject: str, body: str, ctx: Context) -> dict:
    """Send an email from the current user's mail account. Asks for
    interactive confirmation before sending anything."""
    ok = await _confirm(
        ctx,
        f"Send mail to {sendto!r}\nSubject: {subject}\n\n{body}\n\nThis sends immediately. Proceed?",
    )
    if not ok:
        return {"status": "cancelled"}
    return _call_host_agent("send_mail", sendto=sendto, subject=subject, body=body)


# ---- registration ----------------------------------------------------------

_TOOL_FUNCS: dict[str, object] = {
    "get_mail_database_info": get_mail_database_info,
    "get_database_info": get_database_info,
    "read_document": read_document,
    "search_view": search_view,
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
    print(
        f"notes-client-mcp relay: profile={profile!r}, forwarding to Host Agent at {HOST_AGENT_URL}",
        file=sys.stderr,
    )
    server.run("stdio")


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
