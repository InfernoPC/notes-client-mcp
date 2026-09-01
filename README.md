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

## Password: `.env` (required)

Copy `.env.example` to `.env` in the project root and set `NOTES_PASSWORD`
**before** running this at all. **This stores your Notes ID password in
plaintext on disk** — a deliberate convenience-over-security trade-off.
`.env` is gitignored; never commit it, share it, or let it leave this
machine.

There's no interactive prompt fallback: confirmed by hand that it can't
work when an MCP client spawns this process (the client owns stdin entirely
for the JSON-RPC stream, so a prompt just blocks forever and the client
kills it as a connection timeout) — so it isn't worth carrying as dead code.
Without `NOTES_PASSWORD` set, the process fails fast with a clear error
instead.

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
four as separate servers, each a fully independent process with its own
Notes session.

**Enable exactly one at a time - these are a ladder, not add-ons.** Every
profile already includes `read`'s tools (`write` = read+write, `all` =
everything); there's never a reason to have more than one enabled
simultaneously, since the broader one already covers the narrower one and
you'd just be paying for an extra redundant Notes login for nothing. Only
`read` is enabled in your MCP client by default; when you need more, enable
the one matching what you actually want (e.g. `write`) and disable `read`,
rather than enabling both, since that's a real permission escalation, not a
default.

## Tools

Read (`read` profile):
- `get_mail_database_info` — resolves the current user's mail database
  (server + file path) from `notes.ini`. There's no other mail-specific tool:
  once you have this, use it with the generic tools below like any other
  database (there used to be `list_mail_folders`/`search_mail`/`read_mail`
  wrappers; removed as redundant special-casing once the generic tools could
  do the same thing with the mail db's server+path).
- `get_database_info`, `read_document`, `search_view` (any database, by server+file path)
- `export_view_csv` — writes a view's rows straight to a local CSV file
  (path returned, not the data itself) instead of returning them over MCP,
  so it isn't limited by tool-result size the way `search_view` is for a
  large view.
- `find_document_by_key` — fast lookup by a view's sorted column(s) (uses
  the view index). Prefer this over `search_database` whenever the value
  you're looking up is a real column in an existing view.
- `search_database` — Notes `@formula` search across every document in a
  database, for when no suitable view exists. Much slower than
  `search_view`/`find_document_by_key` since it doesn't use a view index;
  `max_docs` caps the result (confirmed by hand: the underlying
  `NotesDatabase.Search` call's own `maxdocs` argument only limits what
  `.Count` reports, not how many documents the iterator actually walks -
  this tool enforces the cap itself instead of trusting that argument).

Design (`design` profile, adds):
- `list_forms`, `list_views` (incl. selection formulas + column formulas), `list_agents`
- `list_view_categories(server_name, file_path, view_name, max_level)` — a
  categorized view's own category values, without touching any document
  entries regardless of view size (confirmed by hand:
  `NotesViewNavigator.MaxLevel` + `GetNextCategory()` correctly skip every
  document at any depth). Since a categorized column is very often a
  formula rather than a plain field, these are the view's own
  rendered/computed values - use them directly with `find_document_by_key`
  rather than guessing at a document's stored field.
- `list_design_elements(server_name, file_path, kind)` — name-based listing
  for design note kinds without their own typed tool: `subforms`,
  `outlines`, `pages`, `framesets`, `script_libraries`, `shared_fields`,
  `database_script`, `navigators`, `image_resources`, `java_resources`,
  `stylesheet_resources`, `data_connections`, `replication_formulas`,
  `profiles`, `folders`, `acl`, `icon`, `help_about`, `help_using` (every
  value confirmed by hand against a real database - a handful of plausible
  extras, e.g. `shared_actions` as its own kind, composite
  applications/components, web pages, XSLTs, don't exist on this Domino
  version's `NotesNoteCollection` API and were left out rather than
  included and silently broken). `actions` (shared actions) is a single
  aggregate note, not individually listable this way - use
  `export_design_dxl(kinds=["actions"])` for its contents.
- `get_database_settings(server_name, file_path)` — categories, design
  template name, replica ID, quota/usage, managers, document/design
  locking, multi-db search, address-book flags, pending-delete state.
- `list_acl(server_name, file_path)` — defined roles, and each entry's
  name/access level (standard Domino 0-6 scale with a readable name)/roles/
  a couple of common capability flags.
- `export_design_dxl(server_name, file_path, kinds, name_filter)` — full DXL
  (XML) export of any of the kinds above (default
  `["forms", "views", "agents"]`), including agent LotusScript/formula
  source, form/view formulas, and full contents for kinds that
  `list_design_elements` can't enumerate individually (like `actions`).
  Requires Designer-level ACL access on the target database.

Write (`write` profile, adds — **each asks for interactive confirmation via
MCP elicitation before writing anything**, so the MCP client needs to support
elicitation for these to work):
Both `create_document`/`update_document` take `fields` as `{name: value}`
with normal JSON types - `str`/`int`/`float`/`bool`/a list all marshal
correctly into the right Notes item type on their own (confirmed by hand).
The one exception is dates: a plain string doesn't become a real Date/Time
item on its own (it just stores as text), so an ISO-8601 string
(`"2026-03-05"` or a full datetime) is auto-detected and converted to a real
Notes date/time value instead - so don't use an ISO-date-shaped string for a
field that's genuinely meant to hold that exact text.

- `create_document(server_name, file_path, form, fields)` — any database.
  Runs `ComputeWithForm` before *and* after setting fields (so default-value
  formulas populate first, then anything depending on your field values
  recomputes), matching real LotusScript practice rather than a raw field
  write - confirmed by hand that a single `ComputeWithForm` after skips
  fields the form only populates on creation.
- `update_document(server_name, file_path, unid, fields)` — any database.
  Never overwrites a conflicting concurrent edit: confirmed by hand that the
  backend `Save()` call does **not** detect or refuse stale writes on its own
  (two independent in-memory copies of the same document both saved
  successfully, the second silently clobbering the first - that protection
  is a front-end `NotesUIDocument` behavior, not this backend class), so
  conflict detection is manual (`LastModified` compared right before
  saving); on a detected conflict it reloads the current document and
  reapplies your `fields` once rather than saving blind, and fails clearly
  if it races a second time. Also checks Document Locking (a database-level
  feature, off in most databases - only enforced when the target database
  actually has it turned on) and refuses with a clear error if someone else
  holds the lock.

There is no `send_mail` - it was removed as an unnecessary special case;
sending mail isn't meaningfully different from other write operations this
tool doesn't otherwise special-case.

## Known limitations

- If your MCP client registers multiple profiles at once (e.g. all four in
  `.mcp.json.example`), their `Initialize()` calls can land in the same
  instant at client startup and collide on the Notes ID file's lock
  (`"The ID file is locked by another process"`) - confirmed by hand, and
  mitigated with a short retry-with-backoff in `notes_backend.py` (the lock
  is only held for the duration of one `Initialize()` call, not the whole
  session, so a retry a moment later just works). Stress-tested at 12/12
  successful simultaneous 4-profile connections; if you ever still see this
  error, it's worth re-testing rather than assuming it's permanent.
- No generic "list all databases" — only the mail database is
  auto-discovered (via `notes.ini`'s `MailServer`/`MailFile`). Other
  databases must be passed explicitly as `server` + `file_path`.
- No calendar tools yet.
- Write tools have been exercised by hand against a live database (create,
  update, conflict detection, field-type handling) through the real MCP
  path with elicitation confirmation - still worth testing against a scratch
  document of your own before trusting them on anything that matters, since
  every database's forms/ACLs differ.
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
client (above) and ask it to call `get_mail_database_info`, then
`search_view` against the result's `($Inbox)` view, and confirm the results
match your mailbox.
