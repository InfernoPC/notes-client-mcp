"""Single place every file-producing tool writes to.

Before this, each tool picked its own spot in the system temp directory:
export_view_csv used a tempfile-generated path, extract_document_media a
per-document folder under tempdir, and export_design_dxl did not write at
all (it returned a string that, at the 100 KB - 2.3 MB sizes real design
exports reach, always overflowed the MCP tool-result limit and got spilled
to a file the caller had no control over). Nothing was findable across
calls and nothing survived a reboot.

Everything now lands under one root, so it can be found, cleaned, and
gitignored in one go:

    <cwd>/notes-exports/          # default
    $NOTES_MCP_EXPORT_DIR         # override

Callers that pass an explicit output path/dir still get exactly that path -
the root is only a default, never a sandbox. Relative explicit paths are
resolved against the export root rather than the process cwd, because the
server's cwd is whatever the MCP client happened to launch it from and is
not something the caller can see.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

_ENV_VAR = "NOTES_MCP_EXPORT_DIR"
_DEFAULT_DIR_NAME = "notes-exports"


def export_root() -> Path:
    """The directory all exports default into, created if missing."""
    configured = os.environ.get(_ENV_VAR)
    root = Path(configured).expanduser() if configured else Path.cwd() / _DEFAULT_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe(name: str) -> str:
    """Make one path segment safe: NSF names carry \\, /, spaces and CJK, and
    design element names carry things like 'F1領用\\GFlow4'."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:120] or "unnamed"


def resolve_output_path(output_path: str | None, *default_parts: str, suffix: str = "") -> Path:
    """Where a tool should write, given the caller's optional `output_path`.

    Explicit paths win: absolute ones are used as-is, relative ones are
    resolved against the export root (see module docstring). With no
    explicit path, a name is built from `default_parts` plus a timestamp so
    repeated exports of the same thing do not silently overwrite each other.
    The parent directory is created either way.
    """
    if output_path:
        path = Path(output_path).expanduser()
        if not path.is_absolute():
            path = export_root() / path
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = "_".join(_safe(p) for p in default_parts if p) or "export"
        path = export_root() / f"{stem}_{stamp}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def resolve_output_dir(output_dir: str | None, *default_parts: str) -> Path:
    """Same contract as resolve_output_path, for tools that fill a folder."""
    if output_dir:
        path = Path(output_dir).expanduser()
        if not path.is_absolute():
            path = export_root() / path
    else:
        stem = "_".join(_safe(p) for p in default_parts if p) or "export"
        path = export_root() / stem
    path.mkdir(parents=True, exist_ok=True)
    return path
