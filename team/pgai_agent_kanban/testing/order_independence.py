"""
order_independence.py -- the per-testcase outcome diff
team/scripts/verify-test-order-independence.sh runs over its two passes.

That script runs the same test estate twice, in forward and in reversed
collection order, each pass writing a junit-xml report. This module reads
the two reports and names every testcase whose outcome differs between
them: a test whose verdict depends on what ran before it.

A testcase is identified as ``<classname>::<name>``. Its outcome is
``FAILED`` when it carries a ``<failure>`` or ``<error>`` element,
``SKIPPED`` when it carries ``<skipped>``, and ``PASSED`` otherwise. A
testcase present in only one report reads as ``MISSING`` in the other, so
it counts as a difference. When a report names one testcase more than once,
the last occurrence wins.

Usage:
    python3 -m pgai_agent_kanban.testing.order_independence FORWARD_XML REVERSED_XML

prints, on stdout:

    Forward order:  <N> tests collected
    Reversed order: <N> tests collected

then a blank line and either
``No verdict differences between forward and reversed collection order.``
(exit 0) or ``ORDER-COUPLING FINDINGS (<count>):`` followed by one
``  <id>: forward=<OUTCOME> reversed=<OUTCOME>`` line per differing
testcase, sorted by id (exit 1). A report that cannot be read or parsed
prints one line on stderr, nothing on stdout, and exits 1. ``--help``
prints this text and exits 0.

Public API:
    junit_outcomes(path) -> dict[str, str]
    outcome_differences(forward, reversed_) -> list[tuple[str, str, str]]
    diff_junit_outcomes(forward_path, reversed_path, out=None) -> int
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from typing import TextIO

__all__ = [
    "MISSING",
    "diff_junit_outcomes",
    "junit_outcomes",
    "main",
    "outcome_differences",
]

#: The outcome recorded for a testcase absent from one of the two reports.
MISSING = "MISSING"


def junit_outcomes(path: str) -> dict:
    """Return ``{"<classname>::<name>": outcome}`` for every testcase in the
    junit-xml report at *path*.

    The outcome is ``FAILED`` (a ``<failure>`` or ``<error>`` child),
    ``SKIPPED`` (a ``<skipped>`` child) or ``PASSED``. Testcases are found at
    any depth. An unreadable file raises ``OSError``; malformed XML raises
    ``xml.etree.ElementTree.ParseError``.
    """
    root = ET.parse(path).getroot()
    result = {}
    for tc in root.iter("testcase"):
        node_id = f'{tc.get("classname")}::{tc.get("name")}'
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "FAILED"
        elif tc.find("skipped") is not None:
            status = "SKIPPED"
        else:
            status = "PASSED"
        result[node_id] = status
    return result


def outcome_differences(forward: dict, reversed_: dict) -> list:
    """Return ``(id, forward_outcome, reversed_outcome)`` for every testcase
    whose outcome differs between the two maps, sorted by id.

    A testcase in only one map is reported with ``MISSING`` on the other
    side.
    """
    diffs = []
    for node_id in sorted(set(forward) | set(reversed_)):
        f = forward.get(node_id, MISSING)
        r = reversed_.get(node_id, MISSING)
        if f != r:
            diffs.append((node_id, f, r))
    return diffs


def diff_junit_outcomes(forward_path: str, reversed_path: str, out: TextIO | None = None) -> int:
    """Diff the per-testcase outcomes of two junit-xml reports.

    Prints the collected counts and every divergence to *out* (stdout when
    omitted) in the form the module docstring shows. Returns 0 when every
    testcase has the same outcome in both reports and 1 when any differs.
    Both reports are parsed before anything is printed, so a read or parse
    error (``OSError`` / ``ParseError``, propagated) leaves *out* untouched.
    """
    if out is None:
        out = sys.stdout
    forward = junit_outcomes(forward_path)
    reversed_ = junit_outcomes(reversed_path)
    diffs = outcome_differences(forward, reversed_)

    print(f"Forward order:  {len(forward)} tests collected", file=out)
    print(f"Reversed order: {len(reversed_)} tests collected", file=out)

    if diffs:
        print(f"\nORDER-COUPLING FINDINGS ({len(diffs)}):", file=out)
        for node_id, f, r in diffs:
            print(f"  {node_id}: forward={f} reversed={r}", file=out)
        return 1

    print("\nNo verdict differences between forward and reversed collection order.", file=out)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.testing.order_independence",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("forward_xml", help="The forward-order pass's junit-xml report.")
    parser.add_argument("reversed_xml", help="The reversed-order pass's junit-xml report.")
    return parser


def main(argv: list | None = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = _build_parser().parse_args(argv)
    try:
        return diff_junit_outcomes(args.forward_xml, args.reversed_xml)
    except (OSError, ET.ParseError) as exc:
        print(f"order_independence: cannot read junit-xml report: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
