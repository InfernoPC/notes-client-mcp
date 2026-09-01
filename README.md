# notes-client-mcp

MCP access to a local HCL Notes Client, via backend `NotesSession` COM
automation (no `NotesUIWorkspace` — driving the live client UI was found to
be able to crash NLNOTES.exe, see plan doc).

Two separate processes, always:

1. **Host Agent** (`notes_mcp.host_agent`) — native, **32-bit** Python, holds
   the actual COM session. Gets the Notes ID password from `NOTES_PASSWORD`
   (via `.env`) if set, otherwise prompts once on its own console. Exposes a
   tiny JSON API on `127.0.0.1:8765` only.
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
control, ahead of time (or be supplied non-interactively via `.env`, below).

## Requirements

- Windows, with HCL/IBM Notes Client installed and already configured
  (an ID file set up, `notes.ini` resolvable).
- A **32-bit** Python interpreter for the Host Agent, matching the bitness
  of the installed Notes Client (check `HKLM\SOFTWARE\WOW6432Node\Lotus\Notes`
  = 32-bit vs `HKLM\SOFTWARE\Lotus\Notes` = 64-bit; caller bitness must
  match). The MCP Relay can run on any Python.
- `pip install -e .` from this directory using the interpreter(s) you'll run it with.

## Password: `.env` or interactive

Copy `.env.example` to `.env` in the project root and set `NOTES_PASSWORD`.
**This stores your Notes ID password in plaintext on disk** — a deliberate
convenience-over-security trade-off. `.env` is gitignored; never commit it,
share it, or let it leave this machine. Leave `NOTES_PASSWORD` unset (no
`.env`, or an empty value) to fall back to the original interactive
`getpass()` prompt instead, which never touches disk.

## Running it

**1. Start the Host Agent** (leave this running, in a terminal you keep open):

```
C:\path\to\32bit\python.exe -m notes_mcp.host_agent
```

If `NOTES_PASSWORD` is set in `.env` it connects immediately; otherwise it
prompts once (via `getpass` — not echoed, never written to disk, never sent
anywhere). Either way it then listens on `http://127.0.0.1:8765`.

**A password (interactive or via `.env`) only ever lives on this machine.**
Never put it into a Claude Desktop / GitHub Copilot MCP config file.

> If Ctrl+C doesn't stop it: `ThreadingHTTPServer`'s per-request threads are
> non-daemon by default, so a single stuck request thread (e.g. blocked on a
> COM call that never returns) can keep the whole process alive even after
> the main loop exits - this is fixed (`daemon_threads = True`) as of the
> current code, but if you're on an older checkout, or it still doesn't
> exit, find and `taskkill /F /PID ...` it (`Get-CimInstance Win32_Process
> -Filter "Name='python.exe'" | Select ProcessId,CommandLine` to find the
> PID). Separately, Git Bash/mintty is also known to not forward Ctrl+C
> reliably to native `python.exe` processes at all - prefer PowerShell/cmd.

**Tool tiers - enforced at TWO independent layers, not just one:**

Tools are tagged `read`/`design`/`write` (`tiers.py` is the single source of
truth). This is enforced twice on purpose:

- **Host Agent** (load-bearing): refuses any call whose tag isn't in its own
  active profile, regardless of what asked for it - a plain
  `python -m notes_mcp.host_agent` with no override only allows `read`, full
  stop, even if something reaches its `127.0.0.1:8765` API directly. Opt into
  more with the `NOTES_HOST_AGENT_PROFILE` env var (`read`/`design`/`write`/`all`).
- **MCP Relay** (cosmetic, UX-level): only *registers* the tool names for its
  own profile with the MCP client, via `NOTES_MCP_PROFILE`, so the client
  doesn't even see tools it can't use. This alone would be a hollow boundary
  without the Host Agent also enforcing it - anything that can reach the
  Host Agent directly would otherwise bypass it entirely.

Both must independently allow a tag for a write/design call to actually
succeed. `curl http://127.0.0.1:8765/health` shows the Host Agent's current
`profile`/`allowed_tags`.

| Profile  | Adds                                              |
|----------|----------------------------------------------------|
| `read`   | mail + generic document/view read (default)        |
| `design` | `read` + form/view/agent/DXL design inspection      |
| `write`  | `read` + create/update document, send mail          |
| `all`    | everything                                          |

**2. Register the MCP Relay with your MCP client** — this is the process the
client itself starts/stops, and it needs the Host Agent already running
**with a matching or wider profile** (see above):

```json
{
  "mcpServers": {
    "notes-client": {
      "command": "C:\\path\\to\\python.exe",
      "args": ["-m", "notes_mcp.server"],
      "env": { "NOTES_MCP_PROFILE": "read" }
    }
  }
}
```

Copy `.mcp.json.example` to `.mcp.json` (gitignored - it holds your local
Python path) and fill in your interpreter path; it already registers all
four as separate servers (`notes-client`, `notes-client-design`,
`notes-client-write`, `notes-client-all`) — only enable `notes-client`
(read) in your MCP client by default; enable the others yourself (e.g. via
`/mcp`) when you actually want write or design access, since that's a real
permission escalation, not a default.

If the Host Agent isn't running yet, every tool call returns a clear error
naming the URL it tried to reach, instead of hanging.

### Docker (Relay only)

Only the Relay is (or can be) containerized — the Host Agent can never be,
since it needs the host's interactive Windows session for COM. Build and run:

```
docker build -t notes-client-mcp-relay .
```

MCP client config, spawning the container per session (`-i` keeps stdin open
for the JSON-RPC stream, `--rm` cleans up on exit):

```json
{
  "mcpServers": {
    "notes-client-docker": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "NOTES_MCP_PROFILE=read",
        "notes-client-mcp-relay"
      ]
    }
  }
}
```

`NOTES_HOST_AGENT_URL` already defaults to `http://host.docker.internal:8765`
inside the image (only override it if your Host Agent listens elsewhere).

**Gotcha found while validating this**: `host.docker.internal` can resolve to
both an IPv4 and IPv6 address inside a container, where the IPv6 route is
broken but IPv4 works fine - Python's default dual-stack connect logic hits
the broken address first and fails outright instead of falling back. Fixed
in `server.py` (`_force_ipv4()`, resolves and connects via the IPv4 literal
directly) - confirmed by hand: without the fix you get `OSError: Network is
unreachable`; with it, a not-yet-running Host Agent correctly produces a
clean `Connection refused` instead.

## Tools

Read (`read` profile):
- `get_mail_database_info`, `list_mail_folders`, `search_mail`, `read_mail`
- `get_database_info`, `read_document`, `search_view` (any database, by server+file path)

Design (`design` profile, adds):
- `list_forms`, `list_views` (incl. selection formulas + column formulas), `list_agents`
- `export_design_dxl` — full DXL (XML) export of forms/views/agents, including
  agent LotusScript/formula source. Requires Designer-level ACL access on the
  target database.

Write (`write` profile, adds — **each asks for interactive confirmation via
MCP elicitation before writing anything**, so the MCP client needs to support
elicitation for these to work):
- `create_document(server_name, file_path, form, fields)` — any database
- `update_document(server_name, file_path, unid, fields)` — any database
- `send_mail(sendto, subject, body)` — from the current user's mail account

## Known limitations

- No generic "list all databases" — only the mail database is
  auto-discovered (via `notes.ini`'s `MailServer`/`MailFile`). Other
  databases must be passed explicitly as `server` + `file_path`.
- No calendar tools yet.
- Host Agent binds to `127.0.0.1` only by design — do not change this to
  `0.0.0.0`; it holds a live, authenticated session with mail/database
  read *and write* access for whoever can reach the port.
- Write tools are code-reviewed and unit-import-tested but **not yet
  exercised against a live database/mailbox** — doing so creates real
  documents / sends real mail. Test them yourself against a scratch
  document or your own inbox before trusting them on anything that matters.
- `export_design_dxl` uses `session.CreateDXLExporter(nc).Export()` - other
  plausible call shapes (`.SetInput()`, `.Input =`) were tried and don't
  exist on this Domino version's COM binding; if a future Domino version
  breaks this again, `tools/design.py`'s `_export_dxl()` already tries all
  four shapes in order and reports every failure if all fail.

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
