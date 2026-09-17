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
| `notes-client-mcp-write`     | `read` + create/update document                     |
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
- `get_database_by_replica_id` — turns a replica ID into the server + file
  path everything else here needs. Cross-database links don't store a path:
  an outline's `<databaselink database='4825666C0023AB44'/>`, a doclink, a
  `notes://server/<16 hex>/...` URL and the Replication Properties dialog all
  give you a replica ID only, and `session.GetDatabase` (which every other
  tool goes through) only takes a path - so a replica ID used to be a dead
  end that could only be guessed at. Takes either spelling of the ID (bare
  `48257B98001E8842` or colon-separated `48257B98:001E8842`). A replica ID
  names a replica *set*, not a location, so the lookup is scoped to one
  server at a time (`server_name` defaults to `""`, the local data
  directory); it returns null rather than an error when that server has no
  such replica, so hunting across candidate servers is a normal sequence of
  calls. A replica the current user can't open is indistinguishable from an
  absent one. It's a directory scan rather than an index lookup, so once you
  have the `file_path`, use that from then on.
- `extract_document_media` — `read_document`'s item values are plain text
  only, even for rich text fields (`read_document` flags which item names
  are rich text via `rich_text_items` in its result; pass
  `include_media=True` to `read_document` to get both in one call instead
  of calling this separately - it costs a DXL export either way, so leave
  it off for a plain field read when you don't yet know you'll need the
  media). Pasted-in pictures
  (e.g. a screenshot) and real file attachments/OLE objects are two
  genuinely different things stored two different ways (confirmed by hand -
  neither extraction method finds the other's content): attachments/OLE
  objects come from each item's `EmbeddedObjects` (`.ExtractFile`); pasted
  pictures are raw Notes-bitmap CD records with no "embedded object" of
  their own at all, only reachable via a whole-document DXL export with
  `NotesDXLExporter.ConvertNotesBitmapsToGIF = True`, decoding the resulting
  `<gif>`/`<jpeg>` base64 blocks (skipping `<gif originalformat='notesbitmap'>`
  blocks, which are auto-generated attachment thumbnails, and anything
  under 4KB decoded, which is almost always a thumbnail too). Writes files
  to a local per-document temp folder and returns their paths - read an
  image result directly with the Read tool to actually see it.
- `extract_document_tables` — a rich text table's structure (rows/columns,
  merged cells, background color) is completely lost in `read_document`'s
  plain-text item values (every cell's text just runs together). Unlike
  images, tables need no special handling - they're real structured
  elements in a DXL export (`<table>`/`<tablerow>`/`<tablecell>`), just
  parsed directly (confirmed by hand against a real multi-table document,
  including one with merged/colored cells). Returns a flat list of
  `{item_name, table_index, rows, row_labels}` since a field can contain
  more than one table; each cell in `rows` is `{text, colspan, rowspan,
  bgcolor}` (colspan/rowspan default to 1, bgcolor to null) - this is the
  raw per-row cell list as DXL encodes it, not a reconstructed visual grid,
  but colspan/rowspan/bgcolor is everything needed to lay that out by hand;
  `row_labels` carries each row's `tablabel` attribute where present (seen
  on tab-style tables), else null. `read_document`'s `include_tables=True`
  gets the same result in one call instead of a separate one.
- `get_view_info` — a view's entry count, columns and selection formula,
  read off the index without walking it. Call this before `search_view` /
  `export_view_csv` on any view you haven't sized: those cost a COM round
  trip per entry, so a large view can run for many minutes, and since all
  Notes calls share one STA thread a walk that long makes every other tool
  call queue behind it (measured on a 63 GB NSF: a 35,718-entry view had not
  finished exporting after 20 minutes, while `get_view_info` on the same
  view answers instantly). `entry_count` is the view's document count -
  measured against walks of the same views, category header rows are not
  counted, and it is *not* an upper bound on the rows a walk returns: a
  document filed under several values of a multi-value categorized column
  is walked once per value (one view reporting 11 walked to 21 rows over
  those same 11 documents). Treat it as a size estimate for deciding how to
  read a view. `is_large` flags the ones worth paging with `limit`/`skip`
  or narrowing with `category` instead.
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
  `count_only=True` returns just `{"count": N}` off the search collection
  without opening a single document, and deliberately ignores `max_docs`
  because a capped count is a wrong answer rather than a cheap one.

### Asking for less (issue #6)

A Notes document is 200+ items wide whether or not you wanted them - the
whole `$UpdatedBy`/`$Revisions` audit trail, one long string per line of
every embedded table - and a click handler is where a Notes application
keeps its bulk. So the default answer to a narrow question used to be
enormous: measured over six production NSFs, "how many requisitions per
month" came back as 319,206 characters of complete documents, and "which
buttons are on this subform" as 92,882 characters of LotusScript. On a
2.53 GiB NSF the counting version of that question took the server down
outright, since walking a GiB of documents blocks the one STA thread (see
the note further down).

Every one of those has a narrow form now:

- `search_database(..., count_only=True)` → `{"count": N}`, no document
  opened. `list_view_categories`'s per-category counts (below) answer the
  same question entirely from the view index when a suitable categorized
  view exists, and `get_view_info`'s `entry_count` sizes a whole view.
- `search_database(..., fields=[...])`, `read_document(..., fields=[...])`,
  `find_document_by_key(..., fields=[...])` restrict `items` to the named
  items. Matching is case-insensitive (`"xflag"` finds `xFlag`), and names
  the document doesn't carry come back in `missing_fields` so a typo is
  distinguishable from an empty field.
- `list_design_actions(..., include_source=False)` keeps each event's
  language and `source_chars` but drops the code, which turns "which
  buttons exist, who sees them, which have code" into a cheap question to
  ask before pulling the one handler you want to read.

Design (`design` profile, adds):
- `list_forms`, `list_views` (incl. selection formulas + column formulas), `list_agents`
- `list_view_categories(server_name, file_path, view_name, max_level)` — a
  categorized view's own category values, without touching any document
  entries regardless of view size (confirmed by hand:
  `NotesViewNavigator.MaxLevel` + `GetNextCategory()` correctly skip every
  document at any depth). Since a categorized column is very often a
  formula rather than a plain field, these are the view's own
  rendered/computed values - use them directly with `find_document_by_key`
  rather than guessing at a document's stored field. Each category also
  carries `descendant_count` (entries below it in total) and `child_count`
  (immediate children), read straight off the index entry: on a
  single-level categorized view of documents both are that category's
  document count, so "how many per status/month/department" needs no
  document read and no search at all. On a multi-level view they differ and
  neither is a pure document count - take `descendant_count` at the deepest
  level. Either is null (never 0) where the navigator doesn't report it.
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
- `list_design_actions(server_name, file_path, name_filter, kinds,
  include_source)` — every action button of the matching notes with its
  click/hidewhen code. `include_source=False` gives the same structure
  minus the code bodies (see "Asking for less" above).
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

## Security model: profiles are UX, not a sandbox

The four profiles (`read`/`design`/`write`/`all`) only control which tool
names get registered on the MCP protocol surface in `server.py`. That's a
convenience/intentionality control for yourself - it stops you (or an
assistant just using the tools it's been given) from *accidentally* reaching
a write operation when you only meant to browse.

**It is not a security boundary against the AI agent itself.** Anything with
shell access on the same machine - which any AI coding assistant driving
this project normally has - can simply import `notes_mcp.tools.write`
directly, or drive the COM objects itself, and call `create_document`/
`update_document` regardless of which profile is registered. There is no
runtime check anywhere in this codebase that can reliably stop that, because
the same agent that would be blocked by such a check can also read the
source and write code that doesn't trigger it.

**The only enforcement that actually holds regardless of what code runs is
Notes/Domino's own ACL.** If the ID file this tool authenticates with has
Reader-only access on a given database, every write attempt against it is
rejected server-side - by an MCP tool call, by a hand-written bypass script,
by anything - because the rejection happens inside Notes/Domino itself, not
in this tool's code. Use `list_acl` to check what access level an ID file
actually has on a database before relying on a `read`-only profile as if it
were a guarantee.

If you're deploying this for someone else and want their read-only intent to
actually hold even against a misbehaving or over-eager AI agent, give them
(or have them use) an ID file whose ACL access on the databases in question
is Reader or lower - don't rely on `read`-profile registration alone.

## Update check (tells you; never updates)

At startup the server asks `origin` whether a newer `vX.Y.Z` tag exists. If
one does, the **names** of the tools it adds, removes, or gives new
parameters to are appended to the server's MCP `instructions`, so the
assistant can see that a tool fitting the task exists but isn't reachable
yet - and say so - instead of hand-rolling a workaround for it.

Nothing is ever updated for you. The notice carries the exact commands, and
whether a full restart is needed is decided by comparing the two
`pyproject.toml` dependency lists: unchanged means `git pull` plus a `/mcp`
reconnect is enough, changed means Claude Code has to be closed first,
because pip cannot replace a package whose DLL the running server holds
open.

How it stays cheap and safe:

- Two stages. `git ls-remote --tags origin` downloads no objects and is all
  that runs when you're up to date - which is nearly every session. Only a
  newer tag triggers `git fetch` of that one tag.
- **`fetch`, never `pull`.** Fetched objects sit in `.git/`; the working
  tree is untouched and nothing new executes. A `pull` would replace code
  this process already imported, leaving the version it reports and the
  behaviour it has disagreeing.
- The remote's tool list is read with `ast.parse`, **never by importing
  it** - importing fetched code to enumerate its tools would execute the
  code we declined to trust.
- Only identifiers reach the assistant (tool names, profile tags, parameter
  names, a dotted version), each checked against a strict pattern. Remote
  docstrings and changelog prose are deliberately never read, so a push
  can't inject instructions. Per the security model above, this is cheap
  defence against a tampered mirror or a bad merge - not against someone
  who can push to `origin`, who can already run code in this process.
- `origin` is hard-coded; a public mirror is not equally trusted.
- Results cache for 6 hours, a version is mentioned once and not repeated,
  and every failure (offline, no git, timeout, wheel install with no
  `.git`) is silent. Set `NOTES_MCP_UPDATE_CHECK=0` to switch it off.

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
  databases must be passed explicitly as `server` + `file_path`, or resolved
  from a replica ID with `get_database_by_replica_id` (which still needs to
  be told which server to look on).
- All Notes COM calls are serialized on one STA thread, and a synchronous
  COM call cannot be cancelled from outside it - so one slow call blocks
  every later one until it finishes on its own. This used to be silent:
  observed on a 63 GB NSF, an `export_view_csv` over a view whose index
  needed rebuilding ran past 20 minutes while every later call, including a
  trivial `get_database_info`, sat in the queue until the MCP client's own
  idle timeout killed it, with nothing naming the cause. A call that is
  still *queued* now gives up after `NOTES_MCP_QUEUE_TIMEOUT` seconds
  (default 60, `0` disables) and returns an error naming the tool that is
  hogging the thread and how long it has been running. A call that has
  already started is never timed out - abandoning that wait wouldn't stop
  the COM call, it would only replace a truthful "still running" with a
  misleading error. The only way out of a genuinely stuck call is still to
  restart the MCP server; `get_view_info` is the way to avoid provoking one.
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
