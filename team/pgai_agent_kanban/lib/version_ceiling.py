#!/usr/bin/env python3
"""
version_ceiling.py — the version-ceiling defaults and the one rule for reading a ceiling.

A project's ``project.cfg [versioning]`` section caps the version the chain
may reach on its own with three keys, one per semver component:

    max_patch   default 21
    max_minor   default 13
    max_major   default 0   (no major release until an operator raises it)

:data:`CEILING_DEFAULTS` is the single home of those three numbers. Every
ceiling reader uses it: the bash accessors ``pp_max_patch`` /
``pp_max_minor`` / ``pp_max_major`` in team/scripts/lib/project_paths.sh
(through this module's CLI, when a key is absent), discovery eligibility,
the REST project view, ``set-version-ceiling.sh --show``, and the
create-project defaults in ops/projects.py.

A ceiling value reads one of four ways:

- **absent** — no key, an empty value, or no project.cfg at all: the
  default above applies. Deleting the line restores the default; it never
  removes the cap.
- **a non-negative whole number** — that ceiling (``0`` is a real ceiling).
- **``none``** (any case) — no ceiling on that component. This is the only
  spelling that removes a cap; ``set-version-ceiling.sh --no-minor`` /
  ``--no-major`` write it.
- **anything else** — malformed. A reader never treats it as "no ceiling":
  it raises :class:`CeilingValueError`, whose text is the one line that
  names the file, the key and the bad value, and the gate treats the project
  as ceiling-blocked until the value is fixed.

One pair of matching surrounding quotes is stripped before parsing, so
``max_major = "0"`` reads as ``0``.

Public API:
    CEILING_KEYS, CEILING_DEFAULTS, NO_CEILING
    Ceiling                      — one resolved ceiling (limit, defaulted, raw)
    CeilingValueError            — a malformed value; str() is the narrated line
    parse_ceiling(key, raw, source="")  -> Ceiling
    project_cfg_file(project_dir)       -> Path | None
    read_ceiling(cfg_file, key)         -> Ceiling
    read_ceilings(cfg_file)             -> dict[str, Ceiling]
    exceeded_ceiling(version, limits)   -> str | None

CLI (used by the bash accessors through ``pp_run_ops``):
    python3 -m pgai_agent_kanban.lib.version_ceiling default KEY
        Print KEY's default (for example ``0`` for max_major) and exit 0.
        An unknown KEY prints a line naming the accepted keys on stderr and
        exits 2.
    python3 -m pgai_agent_kanban.lib.version_ceiling --help
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import sys
import types
from typing import Mapping, Optional

# Run by file path (``python3 .../version_ceiling.py``), this module has no
# parent package and the interpreter puts only its own directory on
# sys.path, so the package import below would not resolve. Put this file's
# own package root -- the directory holding the pgai_agent_kanban/ this file
# sits in -- at the front of sys.path so the import resolves from the same
# tree, never another. Imported as a package member, sys.path is untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import ini  # noqa: E402

__all__ = [
    "CEILING_DEFAULTS",
    "CEILING_KEYS",
    "CEILING_SECTION",
    "NO_CEILING",
    "Ceiling",
    "CeilingValueError",
    "parse_ceiling",
    "project_cfg_file",
    "read_ceiling",
    "read_ceilings",
    "exceeded_ceiling",
]

#: The project.cfg section the ceilings live in.
CEILING_SECTION = "versioning"

#: The ceiling keys, patch first (the order the template documents them).
CEILING_KEYS: tuple[str, ...] = ("max_patch", "max_minor", "max_major")

#: The documented default for each ceiling key -- the single source every
#: reader uses when a key, or the whole project.cfg, is absent.
CEILING_DEFAULTS: Mapping[str, int] = types.MappingProxyType(
    {"max_patch": 21, "max_minor": 13, "max_major": 0}
)

#: The explicit "no ceiling" spelling (matched case-insensitively, written lowercase).
NO_CEILING = "none"

#: A project's config file names, preferred first (the bash readers'
#: _pp_project_cfg_file order: INI project.cfg, then legacy PROJECT.cfg).
_CFG_NAMES = ("project.cfg", "PROJECT.cfg")

_QUOTES = "\"'"
_INTEGER_RE = re.compile(r"[0-9]+")
_EXPLICIT_VERSION_RE = re.compile(r"^\s*[vV]?([0-9]+)\.([0-9]+)\.([0-9]+)\s*$")


class CeilingValueError(ValueError):
    """A ceiling value that is neither a non-negative whole number nor ``none``.

    ``str()`` is the single narrated line a reader prints: it names the
    file (when known), the key and the bad value, and says how to fix it.

    Attributes:
        key:    The ceiling key (``max_major``).
        raw:    The value as written, quotes stripped (``O``).
        source: The file it came from, or ``""`` when not read from a file.
    """

    def __init__(self, key: str, raw: str, source: str = "") -> None:
        self.key = key
        self.raw = raw
        self.source = source
        where = f"{source} [{CEILING_SECTION}] " if source else ""
        super().__init__(
            f"version ceiling unreadable: {where}{key} = '{raw}' is not a non-negative "
            f"whole number or none; the project is ceiling-blocked until it is fixed -- "
            f"write a number, write none for no ceiling, or delete the line for the "
            f"default {CEILING_DEFAULTS.get(key, '?')}"
        )


@dataclasses.dataclass(frozen=True)
class Ceiling:
    """One resolved ceiling.

    Attributes:
        key:       The ceiling key.
        limit:     The ceiling, or None for no ceiling (``none``).
        defaulted: True when the key was absent and the default applied.
        raw:       The value as written (quotes stripped); ``""`` when absent.
    """

    key: str
    limit: Optional[int]
    defaulted: bool
    raw: str

    @property
    def display(self) -> str:
        """The value as ``--show`` prints it: ``13`` (as written, so ``07``
        stays ``07``), ``13 (default)`` or ``none``."""
        if self.limit is None:
            return NO_CEILING
        return f"{self.limit} (default)" if self.defaulted else self.raw


def _strip_quotes(value: str) -> str:
    """Strip one pair of matching surrounding quotes."""
    if len(value) >= 2 and value[0] in _QUOTES and value[-1] == value[0]:
        return value[1:-1].strip()
    return value


def _check_key(key: str) -> None:
    if key not in CEILING_DEFAULTS:
        raise KeyError(
            f"unknown version ceiling key '{key}' (expected one of: {', '.join(CEILING_KEYS)})"
        )


def parse_ceiling(key: str, raw: Optional[str], source: str = "") -> Ceiling:
    """Resolve one ceiling value by the rules in the module docstring.

    Args:
        key:    One of :data:`CEILING_KEYS`.
        raw:    The value as read; None or blank means absent.
        source: The file it was read from, named in a malformed-value error.

    Returns:
        The resolved :class:`Ceiling`.

    Raises:
        KeyError:          *key* is not a ceiling key.
        CeilingValueError: *raw* is malformed.
    """
    _check_key(key)
    value = _strip_quotes((raw or "").strip())
    if not value:
        return Ceiling(key, CEILING_DEFAULTS[key], defaulted=True, raw="")
    if value.lower() == NO_CEILING:
        return Ceiling(key, None, defaulted=False, raw=value)
    if _INTEGER_RE.fullmatch(value):
        return Ceiling(key, int(value), defaulted=False, raw=value)
    raise CeilingValueError(key, value, source)


def project_cfg_file(project_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """Return the project's config file (project.cfg, else PROJECT.cfg), or None."""
    for name in _CFG_NAMES:
        candidate = pathlib.Path(project_dir) / name
        if candidate.is_file():
            return candidate
    return None


def _read_raw(cfg_file: pathlib.Path, key: str) -> Optional[str]:
    """Read ``[versioning] key`` the way the bash ``_pp_read_cfg_key`` does.

    INI lookup first (``ini.read_line_value``, the line-for-line form of
    bash ``read_ini``); for a legacy PROJECT.cfg with no section headers,
    the first bare ``key=value`` line.
    """
    value = ini.read_line_value(cfg_file, CEILING_SECTION, key)
    if value or cfg_file.name != "PROJECT.cfg":
        return value
    try:
        text = cfg_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return value
    match = re.search(rf"^[ \t]*{re.escape(key)}[ \t]*=(.*)$", text, re.M)
    return match.group(1).strip() if match else value


def read_ceiling(cfg_file: Optional[pathlib.Path], key: str) -> Ceiling:
    """Resolve one ceiling from *cfg_file*; None (no config file) means every default.

    Raises:
        KeyError:          *key* is not a ceiling key.
        CeilingValueError: the value in *cfg_file* is malformed.
    """
    if cfg_file is None:
        return parse_ceiling(key, None)
    return parse_ceiling(key, _read_raw(pathlib.Path(cfg_file), key), source=str(cfg_file))


def read_ceilings(cfg_file: Optional[pathlib.Path]) -> dict[str, Ceiling]:
    """Resolve all three ceilings from *cfg_file*, keyed by :data:`CEILING_KEYS`.

    Raises:
        CeilingValueError: for the first malformed value, in key order.
    """
    return {key: read_ceiling(cfg_file, key) for key in CEILING_KEYS}


def exceeded_ceiling(version: str, limits: Mapping[str, Optional[int]]) -> Optional[str]:
    """Return the ceiling key *version* exceeds, or None when it is within every ceiling.

    *version* is an explicit ``vX.Y.Z`` (the ``v`` optional); anything else --
    ``auto``, a label, an empty string -- is never ceiling-blocked here, since
    no component can be compared. *limits* maps each ceiling key to its limit,
    ``None`` meaning no ceiling; a key left out of it is not checked. The
    components are checked major, then minor, then patch, the order discovery
    names the violated ceiling in.
    """
    match = _EXPLICIT_VERSION_RE.match(version or "")
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    for key, value in (("max_major", major), ("max_minor", minor), ("max_patch", patch)):
        limit = limits.get(key)
        if limit is not None and value > limit:
            return key
    return None


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if len(args) != 2 or args[0] != "default":
        print(
            "usage: python3 -m pgai_agent_kanban.lib.version_ceiling default KEY "
            f"(KEY one of: {', '.join(CEILING_KEYS)})",
            file=sys.stderr,
        )
        return 2
    try:
        _check_key(args[1])
    except KeyError as exc:
        print(f"version_ceiling: {exc.args[0]}", file=sys.stderr)
        return 2
    print(CEILING_DEFAULTS[args[1]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
