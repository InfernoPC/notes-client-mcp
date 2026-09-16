"""Read-only tools for inspecting NSF design elements: forms, views (incl.
selection formulas), agents, other design note kinds, database settings,
ACL, and full DXL export.

DXL export of design notes requires at least Designer-level ACL access to
the target database - a plain Reader/Editor role can be refused by the
server even though this same code works fine for data documents. That is a
database ACL limitation, not something this tool can work around.

Standard Domino ACL access levels (stable/documented, unlike most of the
COM surface used elsewhere in this file - not verified by hand, but this
numbering has been unchanged since early Domino):
0=No Access, 1=Depositor, 2=Reader, 3=Author, 4=Editor, 5=Designer, 6=Manager
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..exports import resolve_output_path
from ..notes_backend import NotesBackend, open_database

# DXL puts every element in the Lotus namespace, so ElementTree lookups need
# it prefixed. databases.py carries the same constant for its rich text
# parsing; a shared one-line string is not worth a cross-module private
# import.
_DXL_NS = "{http://www.lotus.com/dxl}"

# <code> wraps exactly one of these per event.
_CODE_LANGUAGES = ("formula", "lotusscript", "javascript", "actionformula")

# NotesNoteCollection.SelectXxx flags, confirmed by hand against a real
# database (each of these successfully set to True on Lotus.NotesSession's
# CreateNoteCollection; a few plausible extras - SelectSharedActions,
# SelectCompositeApplications/Components, SelectWebPages, SelectXSLTs,
# SelectFormats - do NOT exist on this Domino version and raised
# "Property ... can not be set", so they're deliberately left out rather
# than included and silently broken.
_NOTE_KIND_FLAGS: dict[str, str] = {
    "forms": "SelectForms",
    "views": "SelectViews",
    "folders": "SelectFolders",
    "agents": "SelectAgents",
    "subforms": "SelectSubforms",
    "outlines": "SelectOutlines",
    "pages": "SelectPages",
    "framesets": "SelectFramesets",
    "script_libraries": "SelectScriptLibraries",
    "shared_fields": "SelectSharedFields",
    "actions": "SelectActions",  # shared actions: one aggregate note, not individually listable - use export_design_dxl to see contents
    "database_script": "SelectDatabaseScript",
    "navigators": "SelectNavigators",
    "image_resources": "SelectImageResources",
    "java_resources": "SelectJavaResources",
    "stylesheet_resources": "SelectStylesheetResources",
    "data_connections": "SelectDataConnections",
    "replication_formulas": "SelectReplicationFormulas",
    "profiles": "SelectProfiles",
    "acl": "SelectAcl",
    "icon": "SelectIcon",
    "help_about": "SelectHelpAbout",
    "help_using": "SelectHelpUsing",
}


def _export_dxl(session, nc) -> str:
    """Domino's COM binding for NotesDXLExporter has been inconsistent across
    versions about how the input NotesNoteCollection is supplied - try the
    plausible shapes in order rather than guessing at just one."""
    errors = []

    try:
        exporter = session.CreateDXLExporter(nc)
        return exporter.Export()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CreateDXLExporter(nc).Export(): {exc}")

    try:
        exporter = session.CreateDXLExporter()
        return exporter.Export(nc)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CreateDXLExporter().Export(nc): {exc}")

    try:
        exporter = session.CreateDXLExporter()
        exporter.Input = nc
        return exporter.Export()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CreateDXLExporter().Input=nc: {exc}")

    try:
        exporter = session.CreateDXLExporter()
        exporter.SetInput(nc)
        return exporter.Export()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CreateDXLExporter().SetInput(nc): {exc}")

    raise RuntimeError("All DXL export call patterns failed: " + " | ".join(errors))


def list_forms(backend: NotesBackend, server: str, file_path: str) -> list[dict]:
    def _op(session):
        db = open_database(session, server, file_path)
        out = []
        for form in db.Forms:
            out.append(
                {
                    "name": form.Name,
                    "aliases": list(form.Aliases) if form.Aliases else [],
                    "is_subform": form.IsSubForm,
                    "fields": list(form.Fields) if form.Fields else [],
                }
            )
        return out

    return backend.run(_op)


def list_views(backend: NotesBackend, server: str, file_path: str) -> list[dict]:
    def _op(session):
        db = open_database(session, server, file_path)
        out = []
        for view in db.Views:
            columns = []
            for col in view.Columns:
                columns.append(
                    {
                        "title": col.Title,
                        "item_name": col.ItemName,
                        "formula": col.Formula,
                    }
                )
            out.append(
                {
                    "name": view.Name,
                    "aliases": list(view.Aliases) if view.Aliases else [],
                    "is_folder": view.IsFolder,
                    "selection_formula": view.SelectionFormula,
                    "columns": columns,
                }
            )
        return out

    return backend.run(_op)


def _entry_count(entry, prop: str) -> int | None:
    """One of a NotesViewEntry's index-side totals, or None.

    These are properties of the index entry, not a walk, so they cost
    nothing - but they are not universally available (an entry in a view
    the navigator cannot total reports nothing), and a missing total must
    not be reported as 0: that reads as "this category is empty", which is
    a wrong answer rather than an absent one.
    """
    try:
        value = getattr(entry, prop)
    except Exception:  # noqa: BLE001 - not offered for this entry/view
        return None
    return None if value is None else int(value)


def list_view_categories(
    backend: NotesBackend,
    server: str,
    file_path: str,
    view_name: str,
    max_level: int = 0,
) -> list[dict]:
    """Walk only the category-header entries of a categorized view - never
    touches document entries, regardless of view size. Confirmed by hand:
    NotesViewNavigator.MaxLevel + GetNextCategory() correctly skip every
    document entry at any depth (12+ calls of GetNextCategory in a row on a
    468-document view, zero document entries encountered).

    `max_level` controls depth: 0 = top-level categories only; 1 = also
    include a second level (e.g. a "Cat1\\Cat2"-style categorized column),
    and so on. The values returned are the view's own rendered/computed
    column value for that category - not a raw document field (a
    categorized column is very often a formula, not a plain field) - so use
    these values directly with find_document_by_key rather than guessing at
    what a document's stored field looks like.

    Each category also carries its own totals, read straight off the index
    entry: `descendant_count` is how many entries sit under it in total
    (documents plus any sub-category headers), `child_count` only its
    immediate children. For a single-level categorized view of documents
    those are the same number and it is the count of documents in that
    category - which makes "how many per status" a view-index question
    rather than a reason to pull the documents. On a multi-level view they
    differ, and neither is a pure document count: prefer
    `descendant_count` at the deepest level. Either is null on the rare
    entry whose count the navigator declines to report, rather than
    reported as 0.

    The value is read from the position of the view's actual *categorized*
    column, which is not necessarily column 0. Reading ColumnValues[0]
    unconditionally is wrong and fails silently: on ap\\ISODoc.nsf's
    "1.All Document By Number", column 0 is a totals column (formula `1`,
    totals='total') and the categorized column is column 1, so index 0
    returned each category's document *count* (18, 126, 1104, 465) instead
    of its name - values that look plausible and are useless for
    find_document_by_key.
    """

    def _op(session):
        db = open_database(session, server, file_path)
        view = db.GetView(view_name)
        if view is None:
            raise ValueError(f"No view named {view_name!r}")

        # One index per categorized column, in view order: a two-level
        # categorized view has two of them, and a category entry's
        # IndentLevel says which one holds its value.
        cat_idxs = [i for i, c in enumerate(view.Columns) if c.IsCategory]
        if not cat_idxs:
            raise ValueError(
                f"View {view_name!r} has no categorized column, so it has no "
                "categories to list. Use search_view/export_view_csv to read "
                "its rows instead."
            )

        nav = view.CreateViewNav()
        nav.MaxLevel = max_level

        out = []
        entry = nav.GetFirst()
        while entry is not None:
            if entry.IsCategory:
                values = entry.ColumnValues
                level = entry.IndentLevel
                idx = cat_idxs[level] if level < len(cat_idxs) else cat_idxs[-1]
                out.append(
                    {
                        "value": values[idx] if idx < len(values) else None,
                        "indent_level": level,
                        "descendant_count": _entry_count(entry, "DescendantCount"),
                        "child_count": _entry_count(entry, "ChildCount"),
                    }
                )
            entry = nav.GetNextCategory(entry)
        return out

    return backend.run(_op)


def list_agents(backend: NotesBackend, server: str, file_path: str) -> list[dict]:
    def _op(session):
        db = open_database(session, server, file_path)
        out = []
        for agent in db.Agents:
            # Trigger/Target are documented integer enums on NotesAgent (see
            # HCL Domino Designer help for NotesAgent.Trigger / .Target) -
            # returned raw here rather than guessed at, to avoid mislabeling.
            entry = {
                "name": agent.Name,
                "comment": agent.Comment,
                "is_enabled": agent.IsEnabled,
                "is_public": agent.IsPublic,
                "owner": agent.Owner,
                "trigger": agent.Trigger,
                "target": agent.Target,
                "last_run": str(agent.LastRun) if agent.LastRun else None,
            }
            try:
                entry["query"] = agent.Query
            except Exception:  # noqa: BLE001 - only meaningful for search-query agents
                entry["query"] = None
            out.append(entry)
        return out

    return backend.run(_op)


def list_design_elements(
    backend: NotesBackend,
    server: str,
    file_path: str,
    kind: str,
) -> list[dict]:
    """List design notes of one kind by name - for kinds without a typed
    collection like Forms/Views/Agents (subforms, outlines, pages,
    framesets, script libraries, shared fields, database script,
    navigators, resources, ...). See design.py's _NOTE_KIND_FLAGS for the
    full set of valid `kind` values. "actions" (shared actions) is a single
    aggregate note, not individually listable this way - use
    export_design_dxl(kinds=["actions"]) to see its contents instead."""

    def _op(session):
        flag = _NOTE_KIND_FLAGS.get(kind)
        if flag is None:
            raise ValueError(f"Unknown kind {kind!r}, must be one of {sorted(_NOTE_KIND_FLAGS)}")
        db = open_database(session, server, file_path)
        nc = db.CreateNoteCollection(False)
        setattr(nc, flag, True)
        nc.BuildCollection()

        out = []
        note_id = nc.GetFirstNoteID()
        while note_id:
            doc = db.GetDocumentByID(note_id)
            title_values = doc.GetItemValue("$TITLE") if doc.HasItem("$TITLE") else []
            title = title_values[0] if title_values else ""
            name, _, alias = title.partition("|")
            out.append(
                {
                    "name": name or title,
                    "aliases": [alias] if alias else [],
                    "note_id": note_id,
                    "unid": doc.UniversalID,
                }
            )
            note_id = nc.GetNextNoteID(note_id)
        return out

    return backend.run(_op)


def get_database_settings(backend: NotesBackend, server: str, file_path: str) -> dict:
    """Database-level settings/properties beyond the basics in
    get_database_info - all confirmed by hand against a real database."""

    def _op(session):
        db = open_database(session, server, file_path)
        return {
            "title": db.Title,
            "categories": db.Categories,
            "design_template_name": db.DesignTemplateName,
            "replica_id": db.ReplicaID,
            "created": str(db.Created),
            "last_modified": str(db.LastModified),
            "size_bytes": db.Size,
            "size_quota": db.SizeQuota,
            "percent_used": db.PercentUsed,
            "managers": list(db.Managers),
            "is_ft_indexed": db.IsFTIndexed,
            "last_ft_indexed": str(db.LastFTIndexed),
            "is_document_locking_enabled": db.IsDocumentLockingEnabled,
            "is_design_locking_enabled": db.IsDesignLockingEnabled,
            "is_multi_db_search": db.IsMultiDbSearch,
            "is_private_address_book": db.IsPrivateAddressBook,
            "is_public_address_book": db.IsPublicAddressBook,
            "is_pending_delete": db.IsPendingDelete,
        }

    return backend.run(_op)


def list_acl(backend: NotesBackend, server: str, file_path: str) -> dict:
    """Database ACL: defined roles, and each entry's name/access level/roles.
    Access level is the standard Domino 0-6 scale - see module docstring."""

    _LEVEL_NAMES = {0: "No Access", 1: "Depositor", 2: "Reader", 3: "Author", 4: "Editor", 5: "Designer", 6: "Manager"}

    def _op(session):
        db = open_database(session, server, file_path)
        acl = db.ACL
        entries = []
        entry = acl.GetFirstEntry()
        while entry is not None:
            entries.append(
                {
                    "name": entry.Name,
                    "level": entry.Level,
                    "level_name": _LEVEL_NAMES.get(entry.Level, "Unknown"),
                    "roles": list(entry.Roles) if entry.Roles else [],
                    "can_create_documents": entry.CanCreateDocuments,
                    "is_public_reader": entry.IsPublicReader,
                }
            )
            entry = acl.GetNextEntry(entry)
        return {"roles": list(acl.Roles) if acl.Roles else [], "entries": entries}

    return backend.run(_op)



# ---- structured design-element readers ----------------------------------
#
# export_design_dxl hands back raw XML, which every caller then has to parse
# itself. Hand-rolled regex parsing of it is a trap that fails *quietly*:
#
#   - `<code` and `event=` are separated by however much whitespace the
#     exporter felt like, so a pattern written as "<code event='x'>" misses
#     the "<code  event='x'>" the exporter actually emits.
#   - <field .../> self-closes when it has no code, so locating a field's
#     end with a naive search for "</field>" runs past it and attributes a
#     *later* field's formula to this one.
#
# The second one yields a perfectly valid-looking formula for the wrong
# field, which is how an audit of six production NSFs came to report three
# healthy forms as broken (issue #1). These readers parse with ElementTree
# instead, scoping every lookup to the element it belongs to, so neither
# failure is reachable.


def _code_source(node) -> str:
    """The full text of a <formula>/<lotusscript> element.

    Not `node.text`: that stops dead at the first child element, and these
    elements really do have children. Notes lets a formula contain control
    characters - the diamond bullet 0x04 is all over hand-formatted REM
    blocks - and DXL cannot put those in text, so it emits
    `<nonxmlchar value='0004'/>` mid-formula and continues in that child's
    tail. Reading only .text therefore returns the formula up to the first
    such character and silently drops everything after it, which in one
    QNP flow subform meant losing the entire stage-advance rule and seeing
    an unterminated `REM {` where the real code was fine (issue #5).
    """
    parts = [node.text or ""]
    for child in node:
        if child.tag == f"{_DXL_NS}nonxmlchar":
            raw = child.get("value") or ""
            try:
                parts.append(chr(int(raw, 16)))
            except ValueError:
                pass  # unparseable escape: drop the char, keep the formula
        else:
            parts.append(_code_source(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _code_events(el, include_source: bool = True) -> dict:
    """{event: {language, source}} for one element's own <code> children.

    Scoped to direct children on purpose: a <form> is full of <pardef>
    hidewhen formulas that have nothing to do with the form itself, and an
    <action>'s own hidewhen must not pick up a neighbour's.

    With include_source=False each event keeps its language and the length
    of its code but drops the code itself. The shape stays the same so a
    caller can tell "this button has a click handler, in LotusScript, ~2 kB
    of it" from "this button has none" - which is the whole question when
    inventorying a note - without carrying the bodies.
    """
    events: dict[str, dict] = {}
    for code in el.findall(f"{_DXL_NS}code"):
        event = code.get("event")
        if not event:
            continue
        for language in _CODE_LANGUAGES:
            node = code.find(f"{_DXL_NS}{language}")
            if node is not None:
                source = _code_source(node).strip()
                entry = {"language": language}
                if include_source:
                    entry["source"] = source
                else:
                    entry["source_chars"] = len(source)
                events.setdefault(event, entry)
                break
    return events


def _design_notes(dxl: str):
    """Yield (element_type, element) for each design note in a DXL export."""
    root = ET.fromstring(dxl)
    for el in root:
        if el.get("name") is not None:
            yield el.tag.replace(_DXL_NS, ""), el


def _note_header(element_type: str, el) -> dict:
    return {
        "element_type": element_type,
        "name": el.get("name"),
        "alias": el.get("alias"),
        "comment": el.get("comment"),
    }


def _collect_dxl(session, server: str, file_path: str, kinds: list[str], name_filter: str | None) -> str:
    db = open_database(session, server, file_path)
    nc = db.CreateNoteCollection(False)
    for kind in kinds:
        flag = _NOTE_KIND_FLAGS.get(kind)
        if flag is None:
            raise ValueError(f"Unknown kind {kind!r}, must be one of {sorted(_NOTE_KIND_FLAGS)}")
        setattr(nc, flag, True)
    if name_filter:
        lowered = name_filter.lower()
        nc.SelectionFormula = '@Contains(@LowerCase($TITLE); "' + lowered + '")'
    nc.BuildCollection()
    return _export_dxl(session, nc)


def list_form_fields(
    backend: NotesBackend,
    server: str,
    file_path: str,
    name_filter: str | None = None,
    kinds: list[str] | None = None,
) -> list[dict]:
    """Every field of the matching forms/subforms, with its formulas.

    `name_filter` is a case-insensitive substring match on the design note
    title, the same as export_design_dxl - so it can match several notes
    (a form and its "-backup20240101" copy, or two forms sharing a name but
    differing by alias), and the result is a list of notes rather than one.

    Fields come back in the order DXL emits them, which is form layout
    order. A name can legitimately appear more than once when a form defines
    the same field twice; duplicates are kept rather than collapsed, because
    which one wins at runtime is not a question this tool should answer by
    silently dropping data.
    """

    def _op(session):
        dxl = _collect_dxl(session, server, file_path, kinds or ["forms", "subforms"], name_filter)
        notes = []
        for element_type, el in _design_notes(dxl):
            fields = []
            for f in el.iter(f"{_DXL_NS}field"):
                fields.append(
                    {
                        "name": f.get("name"),
                        "type": f.get("type"),
                        "kind": f.get("kind"),
                        "allow_multi_values": f.get("allowmultivalues") == "true",
                        "events": _code_events(f),
                    }
                )
            notes.append({**_note_header(element_type, el), "fields": fields})
        return notes

    return backend.run(_op)


def list_design_actions(
    backend: NotesBackend,
    server: str,
    file_path: str,
    name_filter: str | None = None,
    kinds: list[str] | None = None,
    include_source: bool = True,
) -> list[dict]:
    """Every action button of the matching design notes, with its click and
    hidewhen code.

    Action titles use a backslash for submenus, and the same title can
    appear twice on one note when an old version was kept alongside a new
    one - both are returned as-is, in action bar order. `shared` marks an
    action pulled in from the database's shared actions via
    <sharedactionref>.

    Set include_source=False to inventory the action bar without the code:
    each event keeps its language and `source_chars` but not its `source`.
    Click handlers are where a Notes application keeps its bulk - 29 buttons
    on one production subform came to 92k characters, nearly all of it
    LotusScript nobody had asked for - so "which buttons exist, who sees
    them, which ones have code" is worth asking as its own cheap question
    before pulling the handler you actually want to read.
    """

    def _op(session):
        dxl = _collect_dxl(session, server, file_path, kinds or ["forms", "subforms", "views"], name_filter)
        notes = []
        for element_type, el in _design_notes(dxl):
            shared_ids = {
                id(a) for ref in el.iter(f"{_DXL_NS}sharedactionref") for a in ref.iter(f"{_DXL_NS}action")
            }
            actions = []
            for a in el.iter(f"{_DXL_NS}action"):
                actions.append(
                    {
                        "title": a.get("title"),
                        "icon": a.get("icon"),
                        "hide": a.get("hide"),
                        "show_in_bar": a.get("showinbar") != "false",
                        "system_command": a.get("systemcommand"),
                        "shared": id(a) in shared_ids,
                        "events": _code_events(a, include_source),
                    }
                )
            notes.append({**_note_header(element_type, el), "actions": actions})
        return notes

    return backend.run(_op)


def list_subform_refs(
    backend: NotesBackend,
    server: str,
    file_path: str,
    name_filter: str | None = None,
    kinds: list[str] | None = None,
) -> list[dict]:
    """Which subforms the matching forms pull in, and how.

    A <subformref> is one of two things, and telling them apart is usually
    the whole question when tracing which version of a layout a document
    actually renders:

    - static: `name` is the subform's name (or "name | alias"), fixed.
    - computed: no `name`; a `value` formula decides at render time. The
      formula is returned verbatim - it typically keys off a hidden field
      holding a layout version, so reading it is the only way to know which
      subform a given document gets.
    """

    def _op(session):
        dxl = _collect_dxl(session, server, file_path, kinds or ["forms", "subforms"], name_filter)
        notes = []
        for element_type, el in _design_notes(dxl):
            refs = []
            for ref in el.iter(f"{_DXL_NS}subformref"):
                name = ref.get("name")
                events = _code_events(ref)
                refs.append(
                    {
                        "static_name": name,
                        "is_computed": name is None,
                        "formula": events.get("value", {}).get("source"),
                    }
                )
            notes.append({**_note_header(element_type, el), "subform_refs": refs})
        return notes

    return backend.run(_op)


def export_design_dxl(
    backend: NotesBackend,
    server: str,
    file_path: str,
    kinds: list[str] | None = None,
    name_filter: str | None = None,
    output_path: str | None = None,
) -> str | dict:
    """Export selected design notes as one DXL (XML) document, including
    full agent LotusScript/formula source, form/view formulas, and (for
    kinds without individual listing, like "actions"/shared actions) their
    full contents. `kinds` defaults to ["forms", "views", "agents"]; see
    design.py's _NOTE_KIND_FLAGS for every valid value.

    Pass `output_path` to write the DXL to a file and get back
    {output_path, size_bytes, ...} instead of the document itself. Real
    exports run 100 KB - 2.3 MB, well past what a single tool result can
    carry, so writing to disk is usually what you want; see exports.py for
    where a relative or omitted path lands.

    Prefer list_form_fields / list_design_actions / list_subform_refs when
    you want a specific formula rather than the whole document. **Do not
    parse this output with regular expressions** - DXL puts arbitrary
    whitespace between `<code` and `event=`, and self-closes `<field/>`
    when it has no code, so the obvious patterns silently attribute one
    element's formula to another. Parse it as XML (ElementTree), the way
    those three tools do.
    """

    def _op(session):
        selected = kinds or ["forms", "views", "agents"]
        dxl = _collect_dxl(session, server, file_path, selected, name_filter)
        if not output_path:
            return dxl
        path = resolve_output_path(output_path, Path(file_path).stem, "design", suffix=".dxl.xml")
        path.write_text(dxl, encoding="utf-8")
        return {
            "output_path": str(path),
            "size_bytes": path.stat().st_size,
            "kinds": selected,
            "name_filter": name_filter,
        }

    return backend.run(_op)
