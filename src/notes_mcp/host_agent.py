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
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .notes_backend import NotesBackend, NotesConnectionError
from .tools import databases, design, mail

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

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
            self._send_json(200, {"ok": True, "connected": backend.connected})
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/call":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            tool = body["tool"]
            args = body.get("args", {})
            fn = DISPATCH.get(tool)
            if fn is None:
                raise ValueError(f"Unknown tool: {tool!r}")
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

    httpd = ThreadingHTTPServer((DEFAULT_HOST, DEFAULT_PORT), Handler)
    print(f"notes-host-agent: listening on http://{DEFAULT_HOST}:{DEFAULT_PORT} (localhost only)", file=sys.stderr)
    try:
        httpd.serve_forever()
    finally:
        backend.shutdown()


if __name__ == "__main__":
    main()
