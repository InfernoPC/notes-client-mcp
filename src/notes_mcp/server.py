"""notes-client-mcp MCP relay.

This process holds NO COM objects and asks for NO password. It is spawned
directly by the MCP client (Claude Desktop/Code, GitHub Copilot, ...) over
stdio, and forwards every tool call over HTTP to the Host Agent
(host_agent.py), which must already be running on 127.0.0.1 (see README.md).

Deliberately COM-free so it can run on any Python (32 or 64-bit) and is the
piece that gets packaged into the Docker image in Phase 2 - the container
never needs pywin32 or a matching Notes bitness, it just needs to reach the
Host Agent over the network (e.g. host.docker.internal).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer

HOST_AGENT_URL = os.environ.get("NOTES_HOST_AGENT_URL", "http://127.0.0.1:8765")

server = MCPServer(
    name="notes-client-mcp",
    instructions=(
        "Read-only access to the local HCL Notes Client (mail, databases, "
        "and NSF design elements), relayed to a separate Host Agent process "
        "that holds the actual Notes COM session. The Host Agent must be "
        "running (see README.md) before these tools will work."
    ),
)


def _call_host_agent(tool: str, **kwargs) -> object:
    payload = json.dumps({"tool": tool, "args": kwargs}).encode("utf-8")
    req = urllib.request.Request(
        f"{HOST_AGENT_URL}/call",
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


@server.tool()
def get_mail_database_info() -> dict:
    """Resolve and return metadata for the current user's mail database."""
    return _call_host_agent("get_mail_database_info")


@server.tool()
def get_database_info(server_name: str, file_path: str) -> dict:
    """Return basic metadata (title, size, FT-index status) for a database."""
    return _call_host_agent("get_database_info", server_name=server_name, file_path=file_path)


@server.tool()
def read_document(server_name: str, file_path: str, unid: str) -> dict:
    """Read one document's fields by UniversalID from any database."""
    return _call_host_agent("read_document", server_name=server_name, file_path=file_path, unid=unid)


@server.tool()
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


@server.tool()
def list_mail_folders() -> list[dict]:
    """List folders in the current user's mail database."""
    return _call_host_agent("list_mail_folders")


@server.tool()
def search_mail(query: str, folder: str = "($Inbox)", limit: int = 20) -> list[dict]:
    """Search the current user's mail (full-text if indexed, else Subject/From substring scan)."""
    return _call_host_agent("search_mail", query=query, folder=folder, limit=limit)


@server.tool()
def read_mail(unid: str) -> dict:
    """Read one mail document (subject/from/sendto/date/body) by UniversalID."""
    return _call_host_agent("read_mail", unid=unid)


@server.tool()
def list_forms(server_name: str, file_path: str) -> list[dict]:
    """List forms in a database (name, aliases, fields)."""
    return _call_host_agent("list_forms", server_name=server_name, file_path=file_path)


@server.tool()
def list_views(server_name: str, file_path: str) -> list[dict]:
    """List views/folders in a database, including selection formulas and column formulas."""
    return _call_host_agent("list_views", server_name=server_name, file_path=file_path)


@server.tool()
def list_agents(server_name: str, file_path: str) -> list[dict]:
    """List agents in a database (name, trigger/target, enabled state, query for query agents)."""
    return _call_host_agent("list_agents", server_name=server_name, file_path=file_path)


@server.tool()
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


def main() -> None:
    print(f"notes-client-mcp relay: forwarding to Host Agent at {HOST_AGENT_URL}", file=sys.stderr)
    server.run("stdio")


if __name__ == "__main__":
    main()
