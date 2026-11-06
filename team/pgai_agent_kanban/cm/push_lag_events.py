#!/usr/bin/env python3
"""
push_lag_events.py -- read the RC branches release-state.md's Push Lag
Events still want deleted from the remote.

When the remote refuses a release's pushes after the tag is cut, the release
(``team/scripts/lib/cm/release_resolve.sh``, ``cm_release_record_push_lag``)
appends one ``## Push Lag Event`` to release-state.md. When the refused push
was the RC-branch delete, the event's ``Not On Remote:`` list carries an
item of the form::

    ## Push Lag Event
    Timestamp: 2026-01-01T00:00:00Z
    Version: v1.2.3
    Remote: origin
    Not On Remote:
      - ai_rc/v1.2.3 (still on origin; deletion failed)
    Retry: <root>/scripts/cm/push-watchdog.sh --project <name>

``team/scripts/cm/push-watchdog.sh`` calls this module to learn which remote
branches it may delete once the remote accepts. Reading is liberal (spacing
and heading case vary); the decision is strict, because it gates a remote
branch delete: an item is a delete only when its branch is exactly
``<branch_prefix>rc/<Version>`` for a ``vX.Y.Z`` Version, and both the item's
remote and the event's ``Remote:`` are the remote the caller names. Every
other ``deletion failed`` item is reported as a skip with its reason and is
never deleted. Events and items that name no failed deletion are ignored.

This module reads only; it never writes release-state.md and never runs git.
Whether the branch is still on the remote is the caller's check.

Usage (CLI)::

    python3 -m pgai_agent_kanban.cm.push_lag_events rc-branch-deletes \\
        <release-state.md> --remote origin --branch-prefix ai_

prints one tab-separated line per ``deletion failed`` item, in file order
with duplicates dropped::

    delete<TAB><branch>
    skip<TAB><item text><TAB><reason>

A missing file prints nothing. Exit 0 whenever the file could be read (or
is absent), 2 for bad arguments (argparse).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass

# Run by file path the module has no parent package; put this tree's own
# package root first so the package imports resolve from the same tree.
# Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban import task_status as ts  # noqa: E402
from pgai_agent_kanban.lib import semver  # noqa: E402
from pgai_agent_kanban.lib.text import one_line  # noqa: E402

_EVENT_HEADING = "Push Lag Event"
_VERSION_RE = re.compile(r"^\s*version\s*:\s*(\S+)\s*$", re.IGNORECASE)
_REMOTE_RE = re.compile(r"^\s*remote\s*:\s*(\S+)\s*$", re.IGNORECASE)
_DELETE_ITEM_RE = re.compile(
    r"^\s*[-*]\s+(?P<branch>\S+)\s+\(\s*still\s+on\s+(?P<remote>[^;)\s]+)\s*;\s*deletion\s+failed\s*\)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RcBranchDecision:
    """One ``deletion failed`` item and what the caller may do with it.

    ``action`` is ``delete`` (``branch`` is safe to delete from the remote)
    or ``skip`` (``reason`` says why it is not).
    """

    action: str
    branch: str
    item: str
    reason: str = ""


def _event_blocks(text: str) -> list[list[str]]:
    """The body lines of every ``## Push Lag Event`` section, in file order.

    Each body is read by the one section reader; its span only moves the
    read past that event to the next one under the same repeated heading.
    """
    blocks: list[list[str]] = []
    rest = text.replace("\r\n", "\n")
    while True:
        body = ts.read_section(rest, _EVENT_HEADING)
        span = ts.section_span(rest, _EVENT_HEADING)
        if body is None or span is None:
            return blocks
        blocks.append(body.splitlines())
        rest = rest[span.body_end :]


def _field(lines: list[str], pattern: re.Pattern[str]) -> str:
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1)
    return ""


def _is_release_version(value: str) -> bool:
    """True for a ``vX.Y.Z`` release version (the semver helper's X.Y.Z form)."""
    if not value.startswith("v"):
        return False
    try:
        semver.parse(value)
    except ValueError:
        return False
    return True


def rc_branch_decisions(text: str, remote: str, branch_prefix: str) -> list[RcBranchDecision]:
    """Decide, for every ``deletion failed`` item in *text*, delete or skip.

    *remote* is the git remote the caller would delete from; *branch_prefix*
    is the project's ``branch_prefix`` ("" for none). Duplicate items (the
    same branch named by more than one event) are reported once.
    """
    decisions: list[RcBranchDecision] = []
    seen = set()
    for lines in _event_blocks(text):
        version = _field(lines, _VERSION_RE)
        event_remote = _field(lines, _REMOTE_RE)
        for line in lines:
            match = _DELETE_ITEM_RE.match(line)
            if not match:
                continue
            branch, item_remote = match.group("branch"), match.group("remote")
            item = line.strip().lstrip("-*").strip()
            if (branch, item_remote, version, event_remote) in seen:
                continue
            seen.add((branch, item_remote, version, event_remote))
            reason = ""
            if not _is_release_version(version):
                reason = f"the event's Version {version or '(missing)'!s} is not vX.Y.Z"
            elif branch != f"{branch_prefix}rc/{version}":
                reason = f"{branch} is not this project's RC branch for {version} ({branch_prefix}rc/{version})"
            elif item_remote != remote or event_remote != remote:
                reason = (
                    f"the event names remote {event_remote or '(missing)'} and the item {item_remote}, "
                    f"not the configured remote {remote}"
                )
            if reason:
                decisions.append(RcBranchDecision("skip", branch, item, reason))
            else:
                decisions.append(RcBranchDecision("delete", branch, item))
    return decisions


def main(argv: list[str] | None = None) -> int:
    """Run the ``rc-branch-deletes`` command; return the exit code.

    Prints one tab-separated decision line per failed RC-branch deletion in
    the release-state.md *argv* names, in file order with duplicates dropped:
    ``delete<TAB><branch>`` or ``skip<TAB><item><TAB><reason>``. Returns 0
    when the file was read, and 0 with nothing printed when it is absent (or
    is not a regular file). Bad arguments exit 2 through argparse.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.push_lag_events",
        description="Read the RC branches release-state.md's Push Lag Events still want deleted.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    deletes = sub.add_parser(
        "rc-branch-deletes",
        help="print 'delete<TAB>branch' or 'skip<TAB>item<TAB>reason' per failed RC-branch deletion",
    )
    deletes.add_argument("release_state", help="path to the project's release-state.md")
    deletes.add_argument("--remote", required=True, help="the git remote the caller deletes from")
    deletes.add_argument("--branch-prefix", default="", help="the project's branch_prefix ('' for none)")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.release_state)
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    for decision in rc_branch_decisions(text, args.remote, args.branch_prefix):
        if decision.action == "delete":
            print(f"delete\t{decision.branch}")
        else:
            print(f"skip\t{one_line(decision.item)}\t{one_line(decision.reason)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
