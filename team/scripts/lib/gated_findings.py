#!/usr/bin/env python3
"""
gated_findings.py — the JSON side of the gated runners' quality-findings
file (team/scripts/lib/gated_runner_lint.sh).

The bash library keeps the findings as one JSON object per array element;
these commands build, split and write them. Like gated_runner_lanes.py and
gated_suites.py beside it, this file lives in scripts/lib/ and is run by
path, so the runners need nothing outside their own scripts/ tree to record
findings (stdlib only).

Usage (``python3 team/scripts/lib/gated_findings.py <command>``):

  build LINT EXIT_CODE
      Read a coarse lint's captured output on stdin; print one finding:
      {"lint", "severity": "quality", "exit_code", "summary", "detail"}.
      The summary is the output's first non-blank line, stripped, or
      "<LINT> exited <EXIT_CODE>" when there is none.
  record LINT SUMMARY
      Print one finding: {"lint", "severity": "quality", "summary"}.
  split FILE
      Print each element of the JSON array in FILE as one compact line.
  write OUT_PATH RUNNER
      Read findings on stdin, one JSON object per non-blank line, and write
      the findings document to OUT_PATH: {"schema_version": 1,
      "generated_at" (UTC, seconds), "runner", "findings"}, indented two
      spaces, keys sorted, with a trailing newline.

A wrong argument count prints the usage on stderr and exits 2; ``--help``
prints this text and exits 0.

Public API:
    build_finding(lint, exit_code, detail) -> dict
    record_finding(lint, summary) -> dict
    findings_document(runner, findings, now=None) -> dict
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional

__all__ = ["build_finding", "findings_document", "record_finding"]


def build_finding(lint: str, exit_code: int, detail: str) -> dict:
    """The finding a coarse, pass/fail-shaped lint's non-zero exit records."""
    summary_line = next((line.strip() for line in detail.splitlines() if line.strip()), "")
    return {
        "lint": lint,
        "severity": "quality",
        "exit_code": exit_code,
        "summary": summary_line or f"{lint} exited {exit_code}",
        "detail": detail,
    }


def record_finding(lint: str, summary: str) -> dict:
    """A one-line quality finding with no captured output."""
    return {"lint": lint, "severity": "quality", "summary": summary}


def findings_document(runner: str, findings: list, now: Optional[datetime] = None) -> dict:
    """The findings file's document for *runner* holding *findings*."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {"schema_version": 1, "generated_at": stamp, "runner": runner, "findings": findings}


_USAGE = (
    "usage: gated_findings.py "
    "{build LINT EXIT_CODE | record LINT SUMMARY | split FILE | write OUT_PATH RUNNER}"
)
_ARITY = {"build": 2, "record": 2, "split": 1, "write": 2}


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if not args or _ARITY.get(args[0]) != len(args) - 1:
        print(_USAGE, file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    if command == "build":
        print(json.dumps(build_finding(rest[0], int(rest[1]), sys.stdin.read())))
    elif command == "record":
        print(json.dumps(record_finding(rest[0], rest[1])))
    elif command == "split":
        with open(rest[0], encoding="utf-8") as handle:
            for item in json.load(handle):
                print(json.dumps(item))
    else:
        findings = [json.loads(line) for line in sys.stdin if line.strip()]
        with open(rest[0], "w", encoding="utf-8") as handle:
            json.dump(findings_document(rest[1], findings), handle, indent=2, sort_keys=True)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
