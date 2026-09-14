"""Single source of truth for which MCP tools are read/design/write, and
which tiers each profile allows.

server.py uses this to decide which tool names to register with the MCP
client for a given profile (NOTES_MCP_PROFILE / the notes-client-mcp[-design|
-write|-all] console scripts) - see register_tools() there.
"""

from __future__ import annotations

TOOL_TAGS: dict[str, frozenset[str]] = {
    "get_mail_database_info": frozenset({"read"}),
    "get_database_info": frozenset({"read"}),
    "read_document": frozenset({"read"}),
    "extract_document_media": frozenset({"read"}),
    "extract_document_tables": frozenset({"read"}),
    "search_view": frozenset({"read"}),
    "export_view_csv": frozenset({"read"}),
    "find_document_by_key": frozenset({"read"}),
    "search_database": frozenset({"read"}),
    "list_forms": frozenset({"design"}),
    "list_views": frozenset({"design"}),
    "list_view_categories": frozenset({"design"}),
    "list_agents": frozenset({"design"}),
    "list_design_elements": frozenset({"design"}),
    "get_database_settings": frozenset({"design"}),
    "list_acl": frozenset({"design"}),
    "list_form_fields": frozenset({"design"}),
    "list_design_actions": frozenset({"design"}),
    "list_subform_refs": frozenset({"design"}),
    "export_design_dxl": frozenset({"design"}),
    "create_document": frozenset({"write"}),
    "update_document": frozenset({"write"}),
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
