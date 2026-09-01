# notes-client-mcp

**Language:** English | [繁體中文](README.zh-TW.md)

MCP access to a local HCL Notes Client, via backend `NotesSession` COM
automation (no `NotesUIWorkspace` — driving the live client UI was found to
be able to crash NLNOTES.exe, see plan doc).

One process, spawned directly by your MCP client (Claude Desktop/Code,
GitHub Copilot, ...) over stdio: it holds the COM session and speaks MCP in
the same process. No separate server to start, no ports, no Docker — just a
`command`/`args` pair like any other MCP server (e.g. `mcp-server-git`).

## Requirements

- Windows, with HCL/IBM Notes Client installed and already configured (an
  ID file set up, `notes.ini` resolvable).
- **A Python interpreter whose bitness matches your installed Notes
  Client.** This is the one real piece of setup friction and there's no way
  around it — COM automation requires the caller's bitness to match the
  registered COM server's bitness. Figure out which you need:

  1. Check the registry:
     - `HKLM\SOFTWARE\WOW6432Node\Lotus\Notes` exists → your Notes Client is
       **32-bit**, you need a 32-bit Python.
     - `HKLM\SOFTWARE\Lotus\Notes` exists (and the WOW6432Node one doesn't)
       → your Notes Client is **64-bit**, you need a 64-bit Python.
     - (Most Notes installs from 12.0.2 / 14.x onward are 64-bit-only;
       older installs are commonly 32-bit even on 64-bit Windows.)
  2. Get a matching Python if you don't have one:
     - Download from [python.org/downloads/windows](https://www.python.org/downloads/windows/)
       — the page offers separate 32-bit ("Windows installer (x86)") and
       64-bit ("Windows installer (x86-64)") builds. Install the one that
       matches.
     - Or, if you use [pyenv-win](https://github.com/pyenv-win/pyenv-win),
       32-bit builds are suffixed `-win32` (e.g. `pyenv install 3.13.1-win32`).
  3. Verify: `python -c "import platform; print(platform.architecture())"`
     should print `32bit` or `64bit` matching what you need.

- `pip install -e .` from this directory using that interpreter.

## Password: `.env` is required when run via an MCP client

Copy `.env.example` to `.env` in the project root and set `NOTES_PASSWORD`
**before** registering this with any MCP client. **This stores your Notes ID
password in plaintext on disk** — a deliberate convenience-over-security
trade-off. `.env` is gitignored; never commit it, share it, or let it leave
this machine.

There's also an interactive `getpass()` fallback for when `NOTES_PASSWORD`
is unset, but **confirmed by hand that it cannot work when the MCP client
spawns this process**: the client owns stdin entirely for the JSON-RPC
stream, so `getpass()` blocks forever waiting for input that can never
arrive, and the client eventually kills it as a connection timeout. That
fallback only works if you run `python -m notes_mcp.server` yourself,
directly, in your own terminal (e.g. for the smoke test below) - never
through an MCP client without `.env` set.

**The password only ever lives on this machine.** Never put it into a
Claude Desktop / GitHub Copilot MCP config file.

## Registering with your MCP client

Tools are split into four tiers by risk; pick which to register via the
command:

| Command                     | Tools                                             |
|------------------------------|----------------------------------------------------|
| `notes-client-mcp`           | mail + generic document/view read (default)        |
| `notes-client-mcp-design`    | `read` + form/view/agent/DXL design inspection      |
| `notes-client-mcp-write`     | `read` + create/update document, send mail          |
| `notes-client-mcp-all`       | everything                                          |

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

(Or point `command` at one of the installed console scripts directly, e.g.
`notes-client-mcp-design.exe`, instead of `python.exe -m notes_mcp.server`.)

Copy `.mcp.json.example` to `.mcp.json` (gitignored - it holds your local
Python path) and fill in your interpreter path; it already registers all
four as separate servers. Only enable the `read` one in your MCP client by
default; enable `write`/`design`/`all` yourself (e.g. via `/mcp`) when you
actually want that access, since it's a real permission escalation, not a
default.

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
- Write tools are code-reviewed and unit-import-tested but **not yet
  exercised against a live database/mailbox** — doing so creates real
  documents / sends real mail. Test them yourself against a scratch
  document or your own inbox before trusting them on anything that matters.
- `export_design_dxl` uses `session.CreateDXLExporter(nc).Export()` - other
  plausible call shapes (`.SetInput()`, `.Input =`) were tried and don't
  exist on this Domino version's COM binding; if a future Domino version
  breaks this again, `tools/design.py`'s `_export_dxl()` already tries all
  four shapes in order and reports every failure if all fail.
- This tool is inherently Windows-only and tied to the specific machine
  Notes is installed on - there is no server/cross-machine mode.

## Smoke test

```
C:\path\to\python.exe -m notes_mcp.server
```
Confirm it prints `connected as '...'`. Then register it with your MCP
client (above) and ask it to call `get_mail_database_info` / `search_mail`
with a real query, and confirm the results match your mailbox.
