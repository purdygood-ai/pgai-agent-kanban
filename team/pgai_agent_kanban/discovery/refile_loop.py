#!/usr/bin/env python3
"""
refile_loop.py -- collapse a bug re-file loop to one record.

A defect that keeps being filed under new numbers leaves several live bugs
(``## Status`` open, running or needs-operator) that all report it. The
discovery pass groups them by the shared same-defect rule
(``pgai_agent_kanban.lib.bug_filing.same_defect_matches``: a body that cites
another live bug as its defect, or a slug that differs from another's only
by re-file markers -- a re-file word, an ordinal or a trailing counter at
its start or end) and closes every member but one as ``duplicate``,
with a close note pointing at the record it keeps. Bugs linked through a
third member (A matches B, B matches C) are one group.

Which record is kept: the lowest-numbered member, unless the group holds a
``running`` bug -- then the lowest-numbered running one. A ``running`` bug
belongs to an in-flight release and is never closed here, so a group with
several running members keeps all of them and closes only the rest.

Every closure goes through ``ops.write.close_item`` with state
``duplicate`` and a note naming the kept bug's ID and file stem
(``duplicate of BUG-NNNN (BUG-NNNN-<slug>): <why>``). Closing a
needs-operator bug also closes its open operator-inbox entries
(close_item does that). A second pass over the same tree finds no group
with anything left to close and writes nothing.

Commands (``python3 -m pgai_agent_kanban.discovery.refile_loop <command> DIR``,
where DIR is a project's bugs/ directory):

  list DIR
      Print one ``<closed path>\\t<kept path>`` line per bug a pass would
      close, in group order (by kept bug), then by bug number. Reads only.
  close DIR
      Close each of those bugs as ``duplicate``. The project is the one DIR
      sits in, which must be ``<kanban root>/projects/<project>/bugs``. Each
      closure prints one ``closed <name> as duplicate of BUG-NNNN
      (<kept name>): <why>`` line on stderr; nothing is printed on stdout. A
      closure that is refused or fails leaves its bug as it was, is named
      in one ``WARNING:`` stderr line, and the next pass tries again.

Both commands exit 0: the detector narrates and never stops the discovery
pass. A wrong argument count prints the usage on stderr and exits 2.
``--help`` prints this text and exits 0.
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys
from typing import TextIO

# Run by file path (``python3 .../refile_loop.py``) or loaded from a file
# outside its package, this module has no parent package and the
# interpreter puts only its own directory on sys.path, so the package import
# below would not resolve. Put this file's own package root -- the directory
# holding the pgai_agent_kanban/ this file sits in -- at the front of
# sys.path so the import resolves from the same tree, never another.
# Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.bug_filing import (  # noqa: E402
    LiveBug,
    SameDefect,
    iter_live_bugs,
    same_defect_matches,
)
from pgai_agent_kanban.lib.terminal_states import DUPLICATE_STATE, RUNNING_STATE  # noqa: E402
from pgai_agent_kanban.ops.context import context_for_project  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402

__all__ = [
    "RefileClosure",
    "RefileGroup",
    "close_refile_duplicates",
    "refile_groups",
]


@dataclasses.dataclass(frozen=True)
class RefileClosure:
    """One bug a pass closes as a duplicate of its group's kept bug.

    Attributes:
        bug:  The bug to close.
        kept: The group's kept bug the close note points at.
        why:  One plain clause saying how the bug is the same defect.
    """

    bug: LiveBug
    kept: LiveBug
    why: str

    @property
    def note(self) -> str:
        """The ``## Close Note`` the closure records: the pointer, then why."""
        return f"duplicate of {self.kept.bug_id} ({pathlib.Path(self.kept.name).stem}): {self.why}"


@dataclasses.dataclass(frozen=True)
class RefileGroup:
    """Live bugs that report one defect.

    Attributes:
        kept:      The member that stays live (see the module docstring).
        members:   Every member, kept one included, by bug number.
        closures:  The members a pass closes, by bug number: every member
                   but the kept one and every ``running`` one.
    """

    kept: LiveBug
    members: tuple
    closures: tuple


def _number(bug: LiveBug) -> tuple:
    """Sort key: the bug's number, then its file name."""
    return int(bug.bug_id.split("-", 1)[1]), bug.name


def _why(bug: LiveBug, kept: LiveBug, matches: list) -> str:
    """How *bug* is the same defect: its own match to *kept*, the kept bug's
    match to it, or failing both its first link to another member."""
    outgoing = [m for b, m in matches if b.path == bug.path]
    for match in outgoing:
        if match.bug.path == kept.path:
            return match.why
    for source, match in matches:
        if source.path == kept.path and match.bug.path == bug.path:
            if match.rule == "cites":
                return f"{kept.bug_id}'s body cites it as the defect it reports"
            return match.why.replace("its slug", f"{kept.bug_id}'s slug", 1)
    if outgoing:
        return f"{outgoing[0].why}, and {outgoing[0].bug.bug_id} is the same defect as {kept.bug_id}"
    citing = next(source for source, match in matches if match.bug.path == bug.path)
    return f"{citing.bug_id}'s body cites it as the defect it reports, and {citing.bug_id} is the same defect as {kept.bug_id}"


def _components(bugs: list, matches: list[tuple[LiveBug, SameDefect]]) -> list:
    """The connected groups of *bugs* under *matches*, each of two or more."""
    parent = {bug.path: bug.path for bug in bugs}

    def find(path: str) -> str:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    for bug, match in matches:
        a, b = find(bug.path), find(match.bug.path)
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups: dict = {}
    for bug in bugs:
        groups.setdefault(find(bug.path), []).append(bug)
    return [members for members in groups.values() if len(members) > 1]


def refile_groups(bugs_dir: pathlib.Path | str) -> list:
    """The same-defect groups among the live bugs in *bugs_dir*. Reads only.

    Returns a RefileGroup for each group of two or more live bugs (status
    open, running or needs-operator) linked by the same-defect rule,
    ordered by kept bug number. A group's ``closures`` may be empty when
    every member but the kept one is ``running``. A missing *bugs_dir*
    has no groups.
    """
    bugs = list(iter_live_bugs(str(bugs_dir)))
    matches = same_defect_matches(bugs)
    groups: list = []
    for members in _components(bugs, matches):
        members = sorted(members, key=_number)
        running = [bug for bug in members if bug.status == RUNNING_STATE]
        kept = (running or members)[0]
        closures = tuple(
            RefileClosure(bug, kept, _why(bug, kept, matches))
            for bug in members
            if bug.path != kept.path and bug.status != RUNNING_STATE
        )
        groups.append(RefileGroup(kept, tuple(members), closures))
    return sorted(groups, key=lambda group: _number(group.kept))


def close_refile_duplicates(bugs_dir: pathlib.Path | str, *, err: TextIO = sys.stderr) -> list:
    """Close every bug :func:`refile_groups` names as ``duplicate``.

    Each closure is ``ops.write.close_item(ctx, project, <stem>,
    "duplicate", <note>)`` and narrates one line on *err* naming the closed
    and kept IDs. Returns the RefileClosures that were written. A closure
    that is refused or fails leaves its bug as it was and is narrated on
    *err*; the rest still run. Never raises for a refused closure.
    """
    closures = [closure for group in refile_groups(bugs_dir) for closure in group.closures]
    if not closures:
        return []
    from pgai_agent_kanban.ops.write import close_item

    try:
        ctx, project = context_for_project(pathlib.Path(bugs_dir), intake_dir=True)
    except OpsError as exc:
        # CONVERT: the duplicates stay live and the pass carries on; nothing
        # is written, and the next pass over a correctly placed bugs/
        # directory closes them.
        names = ", ".join(closure.bug.name for closure in closures)
        print(
            f"WARNING: cannot close re-filed duplicate(s) {names}: "
            f"{pathlib.Path(bugs_dir).resolve()} is not a <kanban root>/projects/<project>/bugs "
            f"directory: {exc}",
            file=err,
        )
        return []
    closed: list = []
    for closure in closures:
        stem = pathlib.Path(closure.bug.name).stem
        try:
            close_item(ctx, project, stem, DUPLICATE_STATE, closure.note)
        except (OpsError, OSError) as exc:
            # CONVERT: this bug stays live; the next discovery pass retries.
            print(
                f"WARNING: could not close {closure.bug.name} as {DUPLICATE_STATE} of "
                f"{closure.kept.bug_id}; it stays '{closure.bug.status}' and the next pass retries: {exc}",
                file=err,
            )
            continue
        print(
            f"closed {closure.bug.name} as {DUPLICATE_STATE} of {closure.kept.bug_id} "
            f"({closure.kept.name}): {closure.why}",
            file=err,
        )
        closed.append(closure)
    return closed


_USAGE = {"list": "list DIR", "close": "close DIR"}


def main(argv: list | None = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    command, rest = args[0], args[1:]
    if command not in _USAGE or len(rest) != 1:
        print(
            "usage: python3 -m pgai_agent_kanban.discovery.refile_loop "
            + (_USAGE.get(command) or "{" + ",".join(_USAGE) + "} DIR"),
            file=sys.stderr,
        )
        return 2
    bugs_dir = pathlib.Path(rest[0])
    if command == "list":
        for group in refile_groups(bugs_dir):
            for closure in group.closures:
                print(f"{pathlib.Path(closure.bug.path).resolve()}\t{pathlib.Path(closure.kept.path).resolve()}")
    else:
        close_refile_duplicates(bugs_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
