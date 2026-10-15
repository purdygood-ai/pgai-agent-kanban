#!/usr/bin/env python3
"""
eligibility.py — which requirements files are eligible for PM pickup.

The discovery pipeline's Step 3 (team/scripts/lib/discovery_eligibility.sh,
``_disc_list_all_eligible_requirements``) prints this module's answer and
applies its own per-file idempotency gate to it.

Usage:
    python3 -m pgai_agent_kanban.discovery.eligibility \\
        DIR LAST_RELEASED [MAX_MINOR] [MAX_MAJOR] [MAX_PATCH] [VERSION_SEMANTICS]

DIR is the project's requirements/ directory and LAST_RELEASED the last
shipped version (``v`` prefix optional; empty reads as 0.0.0). MAX_MINOR,
MAX_MAJOR and MAX_PATCH are the version ceilings, honoured on the semver
path only, read by pgai_agent_kanban.lib.version_ceiling's rule: a
non-negative whole number is that ceiling, ``none`` is no ceiling, and an
omitted or empty argument is the documented default (max_minor 13,
max_major 0, max_patch 21). Any other value prints the one narrated
version-ceiling line on stderr and exits 2. VERSION_SEMANTICS is ``semver``
(the default) or ``label`` / ``none``.

Candidates are the ``*.md`` files directly in DIR, other than README.md,
whose name matches the intake requirements pattern or a BUG-/PRIORITY- item
name. A candidate whose ``## Status`` is ``done`` or ``wont-do`` is never
eligible (``wont-do`` prints a stderr skip line).

semver:
    A requirements name without a dotted vX.Y.Z prefix is skipped with a
    stderr line. A file whose ``## Target Version`` is ``auto``,
    ``next-patch``, absent or not a dotted version prints as
    ``<path>:auto``. A file whose version is at or below LAST_RELEASED is
    not printed. Of the rest, those within every ceiling print first in
    ascending version order, then those above a ceiling as
    ``<path>:ceiling-blocked:<version>:<component>:<value>:<ceiling>:<limit>``
    (also ascending), then the ``:auto`` files.
label / none:
    Every candidate prints in file-name order, except that one whose Target
    Version is ``auto``, ``next-patch`` or absent prints afterwards as
    ``<path>:auto-invalid``. No version parse, no ceiling.

Paths are absolute. Exit 0; a wrong argument count prints the usage on
stderr and exits 2, as does a malformed ceiling. ``--help`` prints this
text and exits 0.
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Iterator, Optional, TextIO

# Run by file path (``python3 .../eligibility.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.terminal_states import TERMINAL_STATES, canonical_state  # noqa: E402
from pgai_agent_kanban.lib.version_ceiling import (  # noqa: E402
    CEILING_DEFAULTS,
    CeilingValueError,
    parse_ceiling,
)
from pgai_agent_kanban.lib.filename_contract import (  # noqa: E402
    INTAKE_REQUIREMENTS_RE as _INTAKE_RE,
    SEMVER_REQUIREMENTS_RE as _SEMVER_RE,
    SKIP_REASON_SEMVER_SHAPE_REQUIRED as _SKIP_SEMVER_SHAPE,
    skip_log_line as _skip_log_line,
)

__all__ = ["list_eligible_requirements"]

# Target Version values that mean "compute the version at runtime".
AUTO_SENTINELS = {"auto", "next-patch", ""}

# Non-requirements intake items (BUG- and PRIORITY- prefixes).
_NON_REQ_BUNDLE_RE = re.compile(r'^(PRIORITY-[0-9]{4,}-.+|BUG-[0-9]{4,}-.+)\.md$', re.IGNORECASE)
_STATUS_FIELD_RE = re.compile(r'^##\s+Status\s*\n\s*(\S+)', re.M | re.IGNORECASE)
_TARGET_FIELD_RE = re.compile(r'^##\s+Target Version\s*\n+\s*(\S+)', re.M | re.IGNORECASE)
_EXPLICIT_VER_RE = re.compile(r'^v?\d+\.\d+\.\d+$')


def _version_triple(value: str) -> Optional[tuple]:
    """``vX.Y.Z`` (missing parts read 0) as an int triple, or None."""
    parts = value.lstrip("v").split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return None


def _is_terminal(text: str, name: str, err: TextIO) -> bool:
    """True for an item in a closed state (lib.terminal_states: done, or
    wont-do -- the retired "superseded" reads as wont-do); a closed item that
    is not done is narrated on *err*."""
    status_match = _STATUS_FIELD_RE.search(text)
    if status_match:
        item_status = status_match.group(1).strip().lower()
        canonical = canonical_state(item_status)
        if canonical in TERMINAL_STATES:
            if canonical != "done":
                print(f"discovery: skipping {name}: terminal state '{item_status}'", file=err)
            return True
    return False


def _candidates(req_dir: pathlib.Path) -> Iterator[tuple]:
    """Yield ``(entry, text)`` for every file passing the common filters, in
    file-name order."""
    for entry in sorted(req_dir.iterdir()):
        if not entry.is_file() or not entry.name.endswith(".md"):
            continue
        if entry.name == "README.md":
            continue
        if "templates" in entry.parts or "rejected" in entry.parts:
            continue
        if not (_INTAKE_RE.match(entry.name) or _NON_REQ_BUNDLE_RE.match(entry.name)):
            continue
        yield entry, entry.read_text(encoding="utf-8", errors="replace")


def _target_value(text: str) -> str:
    m = _TARGET_FIELD_RE.search(text)
    return m.group(1).strip().lower() if m else ""


def list_eligible_requirements(
    req_dir: pathlib.Path,
    last_released: str,
    max_minor: Optional[int] = CEILING_DEFAULTS["max_minor"],
    max_major: Optional[int] = CEILING_DEFAULTS["max_major"],
    max_patch: Optional[int] = CEILING_DEFAULTS["max_patch"],
    version_semantics: str = "semver",
    *,
    err: TextIO = sys.stderr,
) -> list[str]:
    """Return the output lines described in the module docstring.

    A ceiling left out takes its documented default; ``None`` means no
    ceiling on that component (the ``none`` spelling).
    """
    lines: list[str] = []
    if version_semantics == "semver":
        last_tuple = _version_triple(last_released.lstrip("v") or "0.0.0") or (0, 0, 0)
        eligible = []
        blocked = []
        auto = []
        for entry, text in _candidates(req_dir):
            if _INTAKE_RE.match(entry.name) and not _SEMVER_RE.match(entry.name):
                print(_skip_log_line(entry.name, _SKIP_SEMVER_SHAPE), file=err)
                continue
            if _is_terminal(text, entry.name, err):
                continue
            raw_val = _target_value(text)
            if raw_val in AUTO_SENTINELS or not _EXPLICIT_VER_RE.match(raw_val):
                auto.append(entry.resolve())
                continue
            tv = _version_triple(raw_val)
            if tv is None:
                print(
                    f"discovery: skipping {entry.name}: non-semver Target Version '{raw_val}' on semver project",
                    file=err,
                )
                continue
            if tv <= last_tuple:
                continue
            major, minor, patch = tv
            violation = None
            if max_major is not None and major > max_major:
                violation = ("major", major, "max_major", max_major)
            elif max_minor is not None and minor > max_minor:
                violation = ("minor", minor, "max_minor", max_minor)
            elif max_patch is not None and patch > max_patch:
                violation = ("patch", patch, "max_patch", max_patch)
            if violation is not None:
                comp, val, cname, cval = violation
                ver_str = raw_val if raw_val.startswith("v") else "v" + raw_val
                blocked.append((tv, entry.resolve(), f":ceiling-blocked:{ver_str}:{comp}:{val}:{cname}:{cval}"))
            else:
                eligible.append((tv, entry.resolve()))
        eligible.sort()
        blocked.sort()
        lines.extend(str(path) for _tv, path in eligible)
        lines.extend(str(path) + suffix for _tv, path, suffix in blocked)
        lines.extend(str(path) + ":auto" for path in auto)
        return lines

    label_eligible = []
    auto_invalid = []
    for entry, text in _candidates(req_dir):
        if _is_terminal(text, entry.name, err):
            continue
        if _target_value(text) in AUTO_SENTINELS:
            auto_invalid.append(entry.resolve())
            continue
        label_eligible.append(entry.resolve())
    lines.extend(str(path) for path in label_eligible)
    lines.extend(str(path) + ":auto-invalid" for path in auto_invalid)
    return lines


def _ceiling_arg(key: str, value: str) -> Optional[int]:
    """One ceiling argument: a number, ``none`` (None) or empty (the default)."""
    return parse_ceiling(key, value).limit


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if not 2 <= len(args) <= 6:
        print(
            "usage: python3 -m pgai_agent_kanban.discovery.eligibility "
            "DIR LAST_RELEASED [MAX_MINOR] [MAX_MAJOR] [MAX_PATCH] [VERSION_SEMANTICS]",
            file=sys.stderr,
        )
        return 2
    padded = args + [""] * (6 - len(args))
    try:
        ceilings = {
            key: _ceiling_arg(key, padded[index])
            for index, key in ((2, "max_minor"), (3, "max_major"), (4, "max_patch"))
        }
    except CeilingValueError as exc:
        print(f"discovery: {exc}", file=sys.stderr)
        return 2
    for line in list_eligible_requirements(
        pathlib.Path(padded[0]),
        padded[1],
        **ceilings,
        version_semantics=padded[5].strip().lower() if len(args) > 5 else "semver",
    ):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
