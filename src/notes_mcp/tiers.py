"""Single source of truth for which MCP tools are read/design/write, and
which tiers each profile allows.

Both the MCP Relay (server.py - cosmetic: controls which tool names the MCP
client even sees) AND the Host Agent (host_agent.py - load-bearing: the
actual privileged executor) enforce this independently. The Relay's
restriction alone would be a hollow security boundary: anything that can
reach the Host Agent's 127.0.0.1:8765 directly (a bug, a misconfigured
relay, a raw curl call) could otherwise invoke write operations regardless
of what the Relay exposes. Starting the Host Agent with no profile override
must default to "read" and refuse write/design calls outright, not just
rely on the Relay not asking for them.
"""

from __future__ import annotations

TOOL_TAGS: dict[str, frozenset[str]] = {
    "get_mail_database_info": frozenset({"read"}),
    "get_database_info": frozenset({"read"}),
    "read_document": frozenset({"read"}),
    "search_view": frozenset({"read"}),
    "list_mail_folders": frozenset({"read"}),
    "search_mail": frozenset({"read"}),
    "read_mail": frozenset({"read"}),
    "list_forms": frozenset({"design"}),
    "list_views": frozenset({"design"}),
    "list_agents": frozenset({"design"}),
    "export_design_dxl": frozenset({"design"}),
    "create_document": frozenset({"write"}),
    "update_document": frozenset({"write"}),
    "send_mail": frozenset({"write"}),
}

PROFILES: dict[str, frozenset[str]] = {
    "read": frozenset({"read"}),
    "design": frozenset({"read", "design"}),
    "write": frozenset({"read", "write"}),
    "all": frozenset({"read", "design", "write"}),
}


def resolve_profile(profile: str) -> frozenset[str]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}, must be one of {sorted(PROFILES)}")
    return PROFILES[profile]
