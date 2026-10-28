"""
cfg_key_seed.py — add keys to one config file, only where they are absent.

A migration that gives an existing install a new key calls this module. Each
requested ``[section] key = value`` is added when the file has no line for
that key in that section, and left alone when it has one — whatever its
value, a blank one included, since a key present at all is the operator's
own setting. Every other line keeps its bytes.

* A missing section is appended at the end of the file, after a blank line.
* A key is added after its section's last ``key = value`` line.
* Presence is read with the bash ``read_ini`` rules
  (:func:`pgai_agent_kanban.lib.ini.read_line_value`): the header in column
  0, the key matched exactly, comment lines ignored — so a commented
  ``# key = value`` is not a setting and does not stop the seed.
* The file is written once, with
  :func:`pgai_agent_kanban.lib.atomic_write.atomic_write`, keeping its
  permission bits; when nothing is added it is not written at all, so a
  second run is a byte-identical no-op.

Command line (what a migration script calls)::

    python3 -m pgai_agent_kanban.lib.cfg_key_seed --cfg FILE \\
        --key SECTION KEY VALUE [--key ...] [--label NAME] \\
        [--rerun-command CMD] [--dry-run]

Exit codes: 0 success (including nothing to add), 2 usage error, 3 FILE is
not a file, 4 FILE could not be written (nothing was changed).

Public API:
    SeedKey, SeedOutcome
    seed_cfg_keys(cfg_path, keys, *, dry_run=False) -> list[SeedOutcome]
    render_outcomes(label, outcomes, *, dry_run=False) -> list[str]
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import shlex
import sys
from typing import Optional, Sequence

# Started by file path (python3 .../lib/cfg_key_seed.py) there is no parent
# package: put the package root this file sits in -- the directory holding
# pgai_agent_kanban/ -- at the front of sys.path so the imports below resolve
# from the same tree, never another. Imported as a package member, sys.path
# is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib import ini  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

__all__ = [
    "ADDED",
    "EXIT_NOT_A_FILE",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_WRITE_FAILED",
    "PRESENT",
    "SeedKey",
    "SeedOutcome",
    "WOULD_ADD",
    "main",
    "render_outcomes",
    "seed_cfg_keys",
]

#: The key was absent and has been added.
ADDED = "added"
#: The key is absent; a dry run left the file as it is.
WOULD_ADD = "would-add"
#: The file already has a line for the key; it is kept.
PRESENT = "present"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_A_FILE = 3
EXIT_WRITE_FAILED = 4

_WRITE_PREFIX = ".cfg_key_seed_tmp_"


@dataclasses.dataclass(frozen=True)
class SeedKey:
    """One key to add when absent: ``[section] key = value``."""

    section: str
    key: str
    value: str


@dataclasses.dataclass(frozen=True)
class SeedOutcome:
    """What happened to one :class:`SeedKey`.

    Attributes:
        seed:     The requested key.
        status:   ``ADDED``, ``WOULD_ADD`` or ``PRESENT``.
        existing: The value already set, for ``PRESENT``; otherwise ``""``.
    """

    seed: SeedKey
    status: str
    existing: str = ""


def seed_cfg_keys(cfg_path: pathlib.Path, keys: Sequence[SeedKey], *, dry_run: bool = False) -> "list[SeedOutcome]":
    """Add every absent key in *keys* to *cfg_path*; return one outcome per key.

    Raises:
        FileNotFoundError: *cfg_path* is not a file.
        OSError:           it could not be read or written; it is unchanged.
    """
    path = pathlib.Path(cfg_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    original = path.read_text(encoding="utf-8")
    text = original
    outcomes = []
    for seed in keys:
        existing = ini.read_line_value(path, seed.section, seed.key)
        if existing is not None:
            outcomes.append(SeedOutcome(seed, PRESENT, existing))
            continue
        text = ini.set_line_value(text, seed.section, seed.key, seed.value)
        outcomes.append(SeedOutcome(seed, WOULD_ADD if dry_run else ADDED))
    if not dry_run and text != original:
        atomic_write(path, text, prefix=_WRITE_PREFIX, preserve_mode=True)
    return outcomes


def render_outcomes(label: str, outcomes: Sequence[SeedOutcome], *, dry_run: bool = False) -> "list[str]":
    """One operator line per outcome, naming *label* (the project or file)."""
    lines = []
    for item in outcomes:
        where = f"[{item.seed.section}] {item.seed.key}"
        if item.status == PRESENT:
            lines.append(f"  [skip]  {label}: {where} already set ({item.existing or 'empty'}) -- no change")
        elif item.status == WOULD_ADD:
            lines.append(f"  [would-seed]  {label}: would add {where} = {item.seed.value}")
        else:
            lines.append(f"  [seeded]  {label}: added {where} = {item.seed.value}")
    if dry_run and not any(o.status == WOULD_ADD for o in outcomes):
        lines.append(f"  [skip]  {label}: nothing to add")
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.cfg_key_seed",
        description=(
            "Add each [SECTION] KEY = VALUE to a config file when the file has no line "
            "for that key in that section; a key already present, blank or not, is kept."
        ),
    )
    parser.add_argument("--cfg", required=True, help="The config file to seed.")
    parser.add_argument(
        "--key",
        nargs=3,
        action="append",
        required=True,
        metavar=("SECTION", "KEY", "VALUE"),
        help="A key to add when absent. Repeatable.",
    )
    parser.add_argument("--label", default="", help="Name printed on each line (default: the file path).")
    parser.add_argument(
        "--rerun-command",
        default="",
        metavar="CMD",
        help="The command that re-runs the caller, appended to the remedy when the file cannot be written.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be added; write nothing.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Command-line entry point; returns the exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE

    cfg_path = pathlib.Path(args.cfg)
    keys = [SeedKey(*spec) for spec in args.key]
    label = args.label or str(cfg_path)
    remedy_tail = f" && {args.rerun_command}" if args.rerun_command else ""

    try:
        outcomes = seed_cfg_keys(cfg_path, keys, dry_run=args.dry_run)
    except FileNotFoundError:
        # BLOCK-WORTHY: there is no file to seed, and reporting success would
        # let a migration claim keys reached a config that does not exist.
        emit_error_contract(
            "cfg_key_seed.not_a_file",
            f"{cfg_path} is not a file; nothing was seeded.",
            f"ls -l {shlex.quote(str(cfg_path))}{remedy_tail}",
        )
        # CENSUS-ANCHOR: cfg-key-seed-not-a-file-01
        return EXIT_NOT_A_FILE
    except OSError as exc:
        # BLOCK-WORTHY: the keys did not land, and reporting success would
        # leave the install without them and nothing telling the operator;
        # the file itself is unchanged.
        emit_error_contract(
            "cfg_key_seed.write_failed",
            f"{cfg_path} could not be read or rewritten ({exc}); it was left unchanged.",
            f"ls -ld {shlex.quote(str(cfg_path.parent))} {shlex.quote(str(cfg_path))}{remedy_tail}",
        )
        # CENSUS-ANCHOR: cfg-key-seed-write-01
        return EXIT_WRITE_FAILED

    for line in render_outcomes(label, outcomes, dry_run=args.dry_run):
        print(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
