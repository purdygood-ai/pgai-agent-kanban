#!/usr/bin/env python3
"""
tree_compare.py — does a target directory hold a current copy of a source tree?

This is the one comparator for "every directory and file under the source is
in the target, every file byte-identical; files only in the target do not
count". Every caller asks it here: the skills module
(``skills_install.compare_skill_dir`` and its install plan) imports it, and
the bash deploy steps reach the command line below through ``pp_run_ops`` —
``deploy_tree_current`` in ``scripts/lib/subagent_deploy.sh``, used by
``deploy_drift`` (the read-only provider-home comparison) and by upgrade.sh's
deposit, which skips a directory that is already current.

The rules:

* A source directory must be a directory in the target (a symlink to one
  counts). A symlinked directory in the source is listed but not descended
  into.
* A source file (a symlink to a file counts; a dangling symlink or special
  file is not a file) must be a file in the target with identical bytes.
  Mode and timestamps are not compared.
* An entry whose name matches an ``exclude`` glob — matched against the one
  path component, e.g. ``__pycache__`` or ``*.pyc`` — is skipped, and an
  excluded directory is not descended into.
* Anything present only in the target is not a difference: a deploy copies
  over and never deletes, so counting it would leave a tree differing
  forever.

The module imports only the standard library, so install.sh and upgrade.sh
can run it before any optional dependency is installed.

Usage:
    python3 -m pgai_agent_kanban.lib.tree_compare [--exclude GLOB]... SOURCE TARGET
    python3 team/pgai_agent_kanban/lib/tree_compare.py [--exclude GLOB]... SOURCE TARGET   (same, by path)

Prints each differing source-relative path, one per line (directories
first), and exits 0 when TARGET is current, 1 when anything differs or
TARGET is not a directory, 2 on a usage error (SOURCE is not a directory,
or a missing argument). ``--help`` prints the usage and exits 0.

Example:
    python3 -m pgai_agent_kanban.lib.tree_compare --exclude __pycache__ team/scripts ~/pgai_agent_kanban/scripts

Public API:
    tree_entries(directory, *, exclude=()) -> tuple[list[str], list[str]]
    tree_differing_paths(source, target, *, exclude=()) -> list[str]
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import filecmp
import fnmatch
import os
import pathlib
import sys
from collections.abc import Iterable, Sequence

__all__ = ["EXIT_CURRENT", "EXIT_DIFFERS", "EXIT_USAGE", "main", "tree_differing_paths", "tree_entries"]

EXIT_CURRENT = 0
EXIT_DIFFERS = 1
EXIT_USAGE = 2


def _excluded(name: str, exclude: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in exclude)


def tree_entries(
    directory: str | os.PathLike[str], *, exclude: Iterable[str] = ()
) -> tuple[list[str], list[str]]:
    """Return ``(directories, files)`` under *directory* as sorted POSIX
    relative paths.

    Files include symlinks to files; symlinked directories are listed as
    directories but not descended into. An entry whose own name matches an
    *exclude* glob is left out, and an excluded directory is not walked.
    """
    root = pathlib.Path(directory)
    patterns = tuple(exclude)
    dirs: list[str] = []
    files: list[str] = []
    for current, dirnames, filenames in os.walk(root):
        rel_root = pathlib.Path(current).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not _excluded(d, patterns))
        dirs.extend((rel_root / d).as_posix() for d in dirnames)
        for name in filenames:
            if not _excluded(name, patterns) and (pathlib.Path(current) / name).is_file():
                files.append((rel_root / name).as_posix())
    return sorted(dirs), sorted(files)


def tree_differing_paths(
    source: str | os.PathLike[str], target: str | os.PathLike[str], *, exclude: Iterable[str] = ()
) -> list[str]:
    """Source-relative directories missing from, and files missing from or
    differing in, *target*; sorted, directories first. Empty when *target*
    holds a current copy of *source*. Files only in *target* are not
    differences; names matching an *exclude* glob are not compared."""
    source = pathlib.Path(source)
    target = pathlib.Path(target)
    dirs, files = tree_entries(source, exclude=exclude)
    out = [d for d in dirs if not (target / d).is_dir()]
    for rel in files:
        dest = target / rel
        if not dest.is_file() or not filecmp.cmp(source / rel, dest, shallow=False):
            out.append(rel)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Compare TARGET with SOURCE, print the differing paths; return the exit code."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lib.tree_compare",
        description=(
            "Check that TARGET holds every directory and a byte-identical copy of every file under SOURCE "
            "(files only in TARGET do not count). Prints each differing path. Exit 0 current, 1 differs or "
            "TARGET is not a directory, 2 usage error."
        ),
        epilog="Example: python3 -m pgai_agent_kanban.lib.tree_compare --exclude __pycache__ SRC DST",
    )
    parser.add_argument(
        "--exclude",
        metavar="GLOB",
        action="append",
        default=[],
        help="Skip every entry whose name matches GLOB (one path component, e.g. '*.pyc'); repeatable.",
    )
    parser.add_argument("source", metavar="SOURCE", help="The shipped tree.")
    parser.add_argument("target", metavar="TARGET", help="The deployed copy to check.")
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as exc:
        return EXIT_CURRENT if exc.code in (0, None) else EXIT_USAGE
    source = pathlib.Path(args.source)
    if not source.is_dir():
        print(f"tree_compare: SOURCE is not a directory: {source}", file=sys.stderr)
        return EXIT_USAGE
    target = pathlib.Path(args.target)
    if not target.is_dir():
        print(".")
        return EXIT_DIFFERS
    differing = tree_differing_paths(source, target, exclude=args.exclude)
    for rel in differing:
        print(rel)
    return EXIT_DIFFERS if differing else EXIT_CURRENT


if __name__ == "__main__":
    sys.exit(main())
