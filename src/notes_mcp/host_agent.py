"""Host Agent: the native, 32-bit, COM-holding process.

Prompts once for the Notes ID password on its own console (getpass), keeps
one authenticated backend NotesSession alive on a dedicated STA thread, and
exposes a tiny JSON API on 127.0.0.1 only. The MCP-facing process
(server.py) is a separate, password-free, COM-free relay that talks to this
API - see README.md for why the two can't be one stdio process.

Never bind this to anything other than 127.0.0.1: it holds a live,
authenticated session with mail/database read access for whoever can reach
the port.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

from .notes_backend import NotesBackend, NotesConnectionError
from .tiers import PROFILES, TOOL_TAGS
from .tools import databases, design, mail, write

# Load .env from the project root (two levels above this file:
# src/notes_mcp/host_agent.py -> src -> project root) regardless of the
# current working directory this process was launched from. NOTES_PASSWORD is
# read later, at connect() time, so it just needs to be in os.environ before
# main() calls backend.connect() - see notes_backend.py for the priority order
# and the explicitly-chosen plaintext-on-disk trade-off this represents.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Load-bearing restriction (see tiers.py docstring): the Host Agent enforces
# this itself, independent of whatever profile the Relay thinks it's
# running as. Default "read" means a plain `python -m notes_mcp.host_agent`
# with no override refuses design/write calls outright, even if asked for
# directly over HTTP. Opt into more with NOTES_HOST_AGENT_PROFILE=design/write/all.
HOST_AGENT_PROFILE = os.environ.get("NOTES_HOST_AGENT_PROFILE", "read")
if HOST_AGENT_PROFILE not in PROFILES:
    print(
        f"notes-host-agent: unknown NOTES_HOST_AGENT_PROFILE={HOST_AGENT_PROFILE!r}, "
        f"falling back to 'read'. Valid profiles: {sorted(PROFILES)}",
        file=sys.stderr,
    )
    HOST_AGENT_PROFILE = "read"
ACTIVE_TAGS = PROFILES[HOST_AGENT_PROFILE]

backend = NotesBackend()

DISPATCH = {
    "get_mail_database_info": lambda **kw: databases.get_mail_database_info(backend),
    "get_database_info": lambda **kw: databases.get_database_info(backend, kw["server_name"], kw["file_path"]),
    "read_document": lambda **kw: databases.read_document(backend, kw["server_name"], kw["file_path"], kw["unid"]),
    "search_view": lambda **kw: databases.search_view(
        backend, kw["server_name"], kw["file_path"], kw["view_name"], kw.get("limit", 20), kw.get("columns")
    ),
    "list_mail_folders": lambda **kw: mail.list_folders(backend),
    "search_mail": lambda **kw: mail.search_mail(backend, kw["query"], kw.get("folder", "($Inbox)"), kw.get("limit", 20)),
    "read_mail": lambda **kw: mail.read_mail(backend, kw["unid"]),
    "list_forms": lambda **kw: design.list_forms(backend, kw["server_name"], kw["file_path"]),
    "list_views": lambda **kw: design.list_views(backend, kw["server_name"], kw["file_path"]),
    "list_agents": lambda **kw: design.list_agents(backend, kw["server_name"], kw["file_path"]),
    "export_design_dxl": lambda **kw: design.export_design_dxl(
        backend,
        kw["server_name"],
        kw["file_path"],
        kw.get("include_forms", True),
        kw.get("include_views", True),
        kw.get("include_agents", True),
        kw.get("name_filter"),
    ),
    "create_document": lambda **kw: write.create_document(
        backend, kw["server_name"], kw["file_path"], kw["form"], kw["fields"]
    ),
    "update_document": lambda **kw: write.update_document(
        backend, kw["server_name"], kw["file_path"], kw["unid"], kw["fields"]
    ),
    "send_mail": lambda **kw: write.send_mail(backend, kw["sendto"], kw["subject"], kw["body"]),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003 - quiet by default
        print(f"host-agent: {fmt % args}", file=sys.stderr)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "connected": backend.connected,
                    "profile": HOST_AGENT_PROFILE,
                    "allowed_tags": sorted(ACTIVE_TAGS),
                },
            )
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/shutdown":
            # Reliable stop regardless of terminal/Ctrl+C quirks. server.shutdown()
            # blocks until serve_forever() actually exits, so it must run on a
            # different thread than the one calling it - this request-handler
            # thread (spawned per-request by ThreadingHTTPServer) qualifies.
            self._send_json(200, {"ok": True, "message": "shutting down"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path != "/call":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            tool = body["tool"]
            args = body.get("args", {})
            tags = TOOL_TAGS.get(tool)
            if tags is None:
                raise ValueError(f"Unknown tool: {tool!r}")
            if not (tags & ACTIVE_TAGS):
                raise PermissionError(
                    f"Tool {tool!r} requires tag(s) {sorted(tags)}, but this Host Agent is "
                    f"running with profile={HOST_AGENT_PROFILE!r} (allows {sorted(ACTIVE_TAGS)}). "
                    "Restart it with NOTES_HOST_AGENT_PROFILE=write (or design/all) to allow this."
                )
            fn = DISPATCH[tool]
            result = fn(**args)
            self._send_json(200, {"ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001 - forwarded to the relay as a clean error
            self._send_json(200, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    print("notes-host-agent: connecting to HCL Notes...", file=sys.stderr)
    try:
        username = backend.connect()
    except NotesConnectionError as exc:
        print(f"notes-host-agent: failed to connect: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"notes-host-agent: connected as {username!r}", file=sys.stderr)
    print(
        f"notes-host-agent: profile={HOST_AGENT_PROFILE!r}, allowed tags={sorted(ACTIVE_TAGS)}",
        file=sys.stderr,
    )

    httpd = ThreadingHTTPServer((DEFAULT_HOST, DEFAULT_PORT), Handler)
    # ThreadingHTTPServer's per-request threads are non-daemon by default: if
    # any single request thread ever gets stuck (e.g. blocked on a COM call
    # that never returns), Ctrl+C stops serve_forever() but the process still
    # won't exit, because Python waits for all non-daemon threads to finish.
    # Mark them daemon so Ctrl+C actually terminates the process regardless.
    httpd.daemon_threads = True
    print(f"notes-host-agent: listening on http://{DEFAULT_HOST}:{DEFAULT_PORT} (localhost only)", file=sys.stderr)
    try:
        httpd.serve_forever()
    finally:
        backend.shutdown()


if __name__ == "__main__":
    main()
