# notes-client-mcp (Phase 1: read-only)

Read-only MCP access to a local HCL Notes Client, via backend `NotesSession`
COM automation (no `NotesUIWorkspace` — driving the live client UI was found
to be able to crash NLNOTES.exe, see plan doc).

Two separate processes, always:

1. **Host Agent** (`notes_mcp.host_agent`) — native, **32-bit** Python, holds
   the actual COM session. Prompts once for your Notes ID password on its
   own console. Exposes a tiny JSON API on `127.0.0.1:8765` only.
2. **MCP Relay** (`notes_mcp.server`) — no COM, no password, any Python
   bitness. This is what the MCP client (Claude Desktop/Code, GitHub
   Copilot, ...) actually spawns over stdio; it forwards every tool call to
   the Host Agent over HTTP.

They must be two processes, not one, because MCP `stdio` transport means
**the client spawns the process and owns its stdin/stdout for the JSON-RPC
stream** — it does not attach to a process you already started by hand, and
there's no guarantee the client gives the child process an interactive
console. So a password prompt can't live in the same process the client
spawns; it has to run in a process you start yourself, in a terminal you
control, ahead of time.

## Requirements

- Windows, with HCL/IBM Notes Client installed and already configured
  (an ID file set up, `notes.ini` resolvable).
- A **32-bit** Python interpreter for the Host Agent, matching the bitness
  of the installed Notes Client (check `HKLM\SOFTWARE\WOW6432Node\Lotus\Notes`
  = 32-bit vs `HKLM\SOFTWARE\Lotus\Notes` = 64-bit; caller bitness must
  match). The MCP Relay can run on any Python.
- `pip install -e .` from this directory using the interpreter(s) you'll run it with.

## Running it

**1. Start the Host Agent** (leave this running, in a terminal you keep open):

```
C:\path\to\32bit\python.exe -m notes_mcp.host_agent
```

It prompts once for your Notes ID password (via `getpass` — not echoed,
never written to disk, never sent anywhere) and then listens on
`http://127.0.0.1:8765`.

**The password only ever goes into this terminal.** Never put it into a
Claude Desktop / GitHub Copilot MCP config file.

**2. Register the MCP Relay with your MCP client** — this is the process the
client itself starts/stops, and it needs the Host Agent already running:

```json
{
  "mcpServers": {
    "notes-client": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["-m", "notes_mcp.server"]
    }
  }
}
```

If the Host Agent isn't running yet, every tool call returns a clear error
naming the URL it tried to reach, instead of hanging.

(Phase 2 packages the Relay into a Docker image — the container still just
talks to the Host Agent over the network, e.g. `host.docker.internal:8765`;
the Host Agent itself can never be containerized, since it needs the host's
interactive session for COM. See plan doc for why.)

## Tools (all read-only)

- `get_mail_database_info`, `list_mail_folders`, `search_mail`, `read_mail`
- `get_database_info`, `read_document`, `search_view` (any database, by server+file path)
- `list_forms`, `list_views` (incl. selection formulas + column formulas), `list_agents`
- `export_design_dxl` — full DXL (XML) export of forms/views/agents, including
  agent LotusScript/formula source. Requires Designer-level ACL access on the
  target database.

## Known limitations (Phase 1)

- No generic "list all databases" — only the mail database is
  auto-discovered (via `notes.ini`'s `MailServer`/`MailFile`). Other
  databases must be passed explicitly as `server` + `file_path`.
- No calendar tools yet.
- Write operations (send mail, create/update documents) are Phase 2/3,
  intentionally not implemented yet.
- Host Agent binds to `127.0.0.1` only by design — do not change this to
  `0.0.0.0`; it holds a live, authenticated session with mail/database read
  access for whoever can reach the port.

## Smoke test

Terminal 1:
```
C:\path\to\32bit\python.exe -m notes_mcp.host_agent
```
Confirm it prints `connected as '...'` and `listening on http://127.0.0.1:8765`.

Terminal 2 (health check, no MCP client needed):
```
curl http://127.0.0.1:8765/health
```
Should return `{"ok": true, "connected": true}`.

Then register the Relay with your MCP client (step 2 above) and ask it to
call `get_mail_database_info` / `search_mail` with a real query, and confirm
the results match your mailbox.
