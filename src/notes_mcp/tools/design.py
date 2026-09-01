"""Read-only tools for inspecting NSF design elements: forms, views (incl.
selection formulas), agents, and full DXL export.

DXL export of design notes requires at least Designer-level ACL access to
the target database - a plain Reader/Editor role can be refused by the
server even though this same code works fine for data documents. That is a
database ACL limitation, not something this tool can work around.
"""

from __future__ import annotations

from ..notes_backend import NotesBackend, open_database


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


def export_design_dxl(
    backend: NotesBackend,
    server: str,
    file_path: str,
    include_forms: bool = True,
    include_views: bool = True,
    include_agents: bool = True,
    name_filter: str | None = None,
) -> str:
    """Export selected design notes as one DXL (XML) document, including
    full agent LotusScript/formula source and form/view formulas."""

    def _op(session):
        db = open_database(session, server, file_path)
        nc = db.CreateNoteCollection(False)
        nc.SelectForms = include_forms
        nc.SelectViews = include_views
        nc.SelectAgents = include_agents
        if name_filter:
            nc.SelectionFormula = f'@Contains(@LowerCase($TITLE); "{name_filter.lower()}")'
        nc.BuildCollection()
        return _export_dxl(session, nc)

    return backend.run(_op)
