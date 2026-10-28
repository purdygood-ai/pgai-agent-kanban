"""
governance_byte_budget.py -- the findings file
team/scripts/lint_governance_byte_budgets.sh writes with --findings-file.

That lint compares each role's assembled governance bytes with its budget
and, for every role over budget, emits one tab-separated line
``<role>\\t<assembled_bytes>\\t<budget_bytes>``. This module turns those
lines into the quality-class findings the gated runners merge, one object
per line:

    {
      "assembled_bytes": <int>,
      "budget_bytes": <int>,
      "lint": "governance_byte_budget",
      "role": "<role>",
      "severity": "quality",
      "summary": "<role> over governance byte budget: assembled=<N> bytes budget=<N> bytes"
    }

Usage:
    printf 'coder\\t120\\t100\\n' | python3 -m pgai_agent_kanban.lint.governance_byte_budget

reads the lines on stdin and prints the findings as one JSON array on
stdout, indented by two spaces with keys sorted, in input order; blank
lines are skipped and no input prints ``[]``. A line that is not three
tab-separated fields with whole-number byte counts prints one line on
stderr, nothing on stdout, and exits 1. ``--help`` prints this text and
exits 0.

Public API:
    LINT_NAME
    finding(role, assembled_bytes, budget_bytes) -> dict
    findings_from_lines(lines) -> list[dict]
    render_findings(lines) -> str
    main(argv=None, stdin=None) -> int
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable, Optional, TextIO

__all__ = [
    "LINT_NAME",
    "finding",
    "findings_from_lines",
    "main",
    "render_findings",
]

#: The ``lint`` key every finding carries.
LINT_NAME = "governance_byte_budget"


def finding(role: str, assembled_bytes: int, budget_bytes: int) -> dict:
    """Return the quality-class finding for *role* assembling
    *assembled_bytes* against a budget of *budget_bytes*."""
    return {
        "lint": LINT_NAME,
        "severity": "quality",
        "role": role,
        "assembled_bytes": assembled_bytes,
        "budget_bytes": budget_bytes,
        "summary": (
            f"{role} over governance byte budget: "
            f"assembled={assembled_bytes} bytes budget={budget_bytes} bytes"
        ),
    }


def findings_from_lines(lines: Iterable[str]) -> list:
    """Return one :func:`finding` per ``<role>\\t<assembled>\\t<budget>`` line.

    A trailing newline is stripped and an empty line is skipped. A line
    without exactly three tab-separated fields, or whose byte counts are not
    integers, raises ``ValueError``.
    """
    findings = []
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        role, assembled, budget = line.split("\t")
        findings.append(finding(role, int(assembled), int(budget)))
    return findings


def render_findings(lines: Iterable[str]) -> str:
    """Return :func:`findings_from_lines`'s findings as the JSON text the
    findings file holds: an array indented by two spaces with keys sorted,
    and no trailing newline (``[]`` when there are none)."""
    return json.dumps(findings_from_lines(lines), indent=2, sort_keys=True)


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.lint.governance_byte_budget",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def main(argv: Optional[list] = None, stdin: Optional[TextIO] = None) -> int:
    """Command-line entry point; see the module docstring."""
    _build_parser().parse_args(argv)
    source = sys.stdin if stdin is None else stdin
    try:
        text = render_findings(source)
    except ValueError as exc:
        print(
            "governance_byte_budget: expected '<role>\\t<assembled_bytes>\\t<budget_bytes>' "
            f"lines on stdin: {exc}",
            file=sys.stderr,
        )
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
