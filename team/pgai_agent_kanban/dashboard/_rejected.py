"""
_rejected.py — Sidecar parser for the unified rejected/ directory.

Each quarantined file <F> placed in a project's rejected/ directory may have a
companion sidecar file <F>.reason in the same directory.  This module provides
read_reason_sidecar(), which parses that file and returns a typed dict,
count_quarantined(), the number of quarantined files the status summary
(kanban-status.sh / GET /status) reports on its "Quarantined:" line, and
quarantined_files() / find_quarantined(), which locate quarantined files --
in the project's rejected/ directory and in the legacy per-intake .rejected/
directories -- together with the intake directory each came from and is
restored to (list-rejected.sh, GET /rejected, GET /board and
recover-rejected.sh all read through them).

Sidecar file format
-------------------
Key=value lines, one per line, newline-terminated.  Single-line values only —
embed a literal newline as the two-character sequence ``\n``.

Known keys:

    original_type  — intake queue the file came from: bugs | priority | requirements
    original_dir   — absolute path of the source directory before rejection
    rejected_at    — ISO 8601 UTC timestamp, e.g. 2026-05-28T14:00:00Z
    reason         — single-line free-text description of why the file was rejected
    retry_count    — integer; number of times the file was seen before quarantine

Unknown keys are silently ignored (forward compatibility).
Missing keys return None.

Usage::

    from pgai_agent_kanban.dashboard._rejected import read_reason_sidecar

    info = read_reason_sidecar("/path/to/rejected/malformed-intake-foo.md.reason")
    print(info["reason"])          # e.g. "malformed filename"
    print(info["retry_count"])     # e.g. 3, or None if absent
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Optional


# ---------------------------------------------------------------------------
# Public type alias for the sidecar dict returned by read_reason_sidecar.
# Keys are always present; values are None when the key was absent in the file.
# ---------------------------------------------------------------------------

SIDECAR_KEYS = frozenset(
    {"original_type", "original_dir", "rejected_at", "reason", "retry_count"}
)


def read_reason_sidecar(
    sidecar_path: "str | pathlib.Path",
) -> dict[str, Optional[str]]:
    """Parse a .reason sidecar file and return a dict with all known keys.

    Args:
        sidecar_path: Path to the ``.reason`` file co-located with the
            quarantined file inside the project's ``rejected/`` directory.

    Returns:
        A dict with the following keys (all always present):

        - ``original_type`` (str | None): intake queue (bugs|priority|requirements)
        - ``original_dir``  (str | None): absolute source directory path
        - ``rejected_at``   (str | None): ISO 8601 UTC rejection timestamp
        - ``reason``        (str | None): free-text rejection reason
        - ``retry_count``   (str | None): raw integer string; caller may cast with
          ``int(info["retry_count"])`` after a None check.

        All values are returned as strings (or None when absent).  The caller is
        responsible for any type conversion beyond string.

    Notes:
        - Lines that do not contain ``=`` are silently skipped.
        - Unknown keys are silently ignored (forward compatibility).
        - The embedded newline escape sequence ``\\n`` is NOT decoded here;
          callers that need to preserve literal newlines in ``reason`` must
          handle that substitution themselves.
        - OSError (e.g. file not found) is silently caught; the function returns
          an all-None dict so callers do not need a try/except at every call site.
    """
    result: dict[str, Optional[str]] = {key: None for key in SIDECAR_KEYS}

    path = pathlib.Path(sidecar_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            # Blank lines and comment lines (starting with #) are ignored.
            continue
        if "=" not in line:
            # Not a key=value line; skip silently.
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key in SIDECAR_KEYS:
            result[key] = value if value else None

    return result


#: Suffix of a sidecar file; such files are never counted as quarantined.
SIDECAR_SUFFIX = ".reason"


def count_quarantined(rejected_dir: "str | pathlib.Path") -> int:
    """Return how many files are quarantined in a project's ``rejected/`` directory.

    Counts the regular files directly inside *rejected_dir* (not symlinks,
    not subdirectories, hidden files included) whose name does not end in
    ``.reason`` -- the sidecars describe quarantined files and are not
    quarantined files themselves. A missing directory counts as 0; nothing
    is created.

    Args:
        rejected_dir: The project's ``rejected/`` directory.

    Returns:
        The number of quarantined files.
    """
    path = pathlib.Path(rejected_dir)
    try:
        entries = list(path.iterdir())
    except OSError:
        return 0
    return sum(
        1
        for entry in entries
        if entry.is_file() and not entry.is_symlink() and not entry.name.endswith(SIDECAR_SUFFIX)
    )


def sidecar_path_for(quarantined_file: "str | pathlib.Path") -> pathlib.Path:
    """Return the expected .reason sidecar path for a quarantined file.

    Convenience helper: given ``/path/to/rejected/malformed-intake-foo.md``, returns
    ``/path/to/rejected/malformed-intake-foo.md.reason``.

    Args:
        quarantined_file: Path to the quarantined file inside rejected/.

    Returns:
        pathlib.Path with ``.reason`` appended to the quarantined file path.
    """
    p = pathlib.Path(quarantined_file)
    return p.parent / (p.name + ".reason")


# ---------------------------------------------------------------------------
# Locating quarantined files and where each one came from
# ---------------------------------------------------------------------------

#: The intake directories discovery quarantines from, in the order the
#: rejected-file listing shows them.
INTAKE_DIRS: tuple[str, ...] = ("priority", "bugs", "requirements")

#: The origin of a quarantined file whose intake directory cannot be told:
#: no usable sidecar and a name that carries no intake prefix.
UNKNOWN_ORIGIN = "rejected"

# Intake file-name prefix -> the intake directory such a file belongs in;
# the last resort when a quarantined file has no usable sidecar.
_PREFIX_ORIGINS: tuple[tuple[str, str], ...] = (("PRIORITY-", "priority"), ("BUG-", "bugs"))


@dataclasses.dataclass(frozen=True)
class QuarantinedFile:
    """One quarantined intake file and the intake directory it came from.

    Attributes:
        path:        Where the file sits now -- the project's ``rejected/``
                     directory, or an intake directory's own ``.rejected/``.
        origin:      The intake directory it came from (one of
                     :data:`INTAKE_DIRS`), or :data:`UNKNOWN_ORIGIN`.
        restore_dir: The directory recovery moves it back into, or ``None``
                     when the origin is unknown.
    """

    path: pathlib.Path
    origin: str
    restore_dir: Optional[pathlib.Path]

    @property
    def name(self) -> str:
        """The file's base name."""
        return self.path.name


def _quarantined_entries(directory: pathlib.Path) -> list[pathlib.Path]:
    """The quarantined files directly inside *directory*, in name order.

    Regular, non-hidden files only: symlinks, subdirectories and ``.reason``
    sidecars are not quarantined files. A missing directory yields nothing.
    """
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    return sorted(
        (
            entry for entry in entries
            if entry.is_file() and not entry.is_symlink()
            and not entry.name.startswith(".") and not entry.name.endswith(SIDECAR_SUFFIX)
        ),
        key=lambda entry: entry.name,
    )


def _resolved(path: "str | pathlib.Path") -> Optional[pathlib.Path]:
    """*path* resolved to an absolute real path, or ``None`` when it cannot be."""
    try:
        return pathlib.Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def quarantine_origin(
    project_root: "str | pathlib.Path", name: str, sidecar: "dict[str, Optional[str]]"
) -> tuple[str, Optional[pathlib.Path]]:
    """Return ``(origin, restore_dir)`` for a file in the project's ``rejected/``.

    The sidecar decides first. Its ``original_dir`` is honoured when it names
    one of the project's intake directories (any other path -- outside the
    project, or a root that has since moved -- is ignored, so a sidecar can
    never route a file anywhere else); then its ``original_type`` when that
    is an intake directory name. With neither, the name's ``BUG-`` or
    ``PRIORITY-`` prefix decides; otherwise the origin is
    :data:`UNKNOWN_ORIGIN` and there is no restore directory.

    Args:
        project_root: The project's root directory.
        name:         The quarantined file's base name.
        sidecar:      Its parsed sidecar (:func:`read_reason_sidecar`).
    """
    root = pathlib.Path(project_root)
    intake = {directory: root / directory for directory in INTAKE_DIRS}
    recorded_dir = sidecar.get("original_dir")
    if recorded_dir:
        recorded = _resolved(recorded_dir)
        for directory, path in intake.items():
            if recorded is not None and recorded == _resolved(path):
                return directory, path
    recorded_type = sidecar.get("original_type")
    if recorded_type in intake:
        return recorded_type, intake[recorded_type]
    for prefix, directory in _PREFIX_ORIGINS:
        if name.startswith(prefix):
            return directory, intake[directory]
    return UNKNOWN_ORIGIN, None


def quarantined_files(
    project_root: "str | pathlib.Path", rejected_dir: "str | pathlib.Path"
) -> list[QuarantinedFile]:
    """Return every file discovery quarantined for one project.

    Discovery moves a file it keeps rejecting into the project's
    ``rejected/`` directory (*rejected_dir*, ``pp_rejected_dir``) beside a
    ``<name>.reason`` sidecar; when it is given no project name it falls
    back to the intake directory's own ``.rejected/``. Both are read: first
    *rejected_dir*, each file's origin from :func:`quarantine_origin`; then
    ``priority/.rejected/``, ``bugs/.rejected/`` and
    ``requirements/.rejected/``, whose files came from that intake
    directory. Each directory's files are in name order.

    Args:
        project_root: The project's root directory.
        rejected_dir: The project's ``rejected/`` directory.

    Returns:
        The quarantined files; empty when there are none. Nothing is created.
    """
    root = pathlib.Path(project_root)
    rejected = pathlib.Path(rejected_dir)
    found = [
        QuarantinedFile(entry, *quarantine_origin(root, entry.name, read_reason_sidecar(sidecar_path_for(entry))))
        for entry in _quarantined_entries(rejected)
    ]
    for directory in INTAKE_DIRS:
        found.extend(
            QuarantinedFile(entry, directory, root / directory)
            for entry in _quarantined_entries(root / directory / ".rejected")
        )
    return found


def find_quarantined(
    project_root: "str | pathlib.Path", rejected_dir: "str | pathlib.Path", name: str
) -> Optional[QuarantinedFile]:
    """Return the quarantined file called *name*, or ``None``.

    Searches in :func:`quarantined_files` order, so the project's
    ``rejected/`` directory wins over a legacy ``.rejected/``. *name* is
    compared with the base names actually present, so a name carrying a
    path separator or ``..`` never matches anything.
    """
    for quarantined in quarantined_files(project_root, rejected_dir):
        if quarantined.name == name:
            return quarantined
    return None
