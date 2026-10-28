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
  instance LINT CAUSE REMEDY [KEY=VALUE ...]
      Print one per-instance finding in the shape lint_dual_channel_pair.py
      and pgai_agent_kanban.lint.findings write: {"lint", "severity":
      "quality", "summary" (= CAUSE), "cause", "remedy"} plus one key per
      KEY=VALUE argument. A "line" or "pid" value made of digits is written
      as a number; every other value is a string. A KEY=VALUE argument
      without "=" or with an empty key prints the usage and exits 2.
  array OUT_PATH
      Read findings on stdin, one JSON object per non-blank line, and write
      them to OUT_PATH as one JSON array (indented two spaces, keys sorted,
      trailing newline; "[]" when there are none) -- the --findings-file
      shape gated_runner_merge_findings_file reads. Parent directories are
      created.
  count FILE
      Print the number of elements in the JSON array in FILE; 0 when FILE
      does not exist.
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
    instance_finding(lint, cause, remedy, extras) -> dict
    findings_document(runner, findings, now=None) -> dict
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

__all__ = ["build_finding", "findings_document", "instance_finding", "record_finding"]

# Extra keys whose all-digit values are written as JSON numbers.
_NUMERIC_KEYS = frozenset({"line", "pid"})


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


def instance_finding(lint: str, cause: str, remedy: str, extras: dict) -> dict:
    """One per-instance finding: *cause* doubles as the required summary,
    and each *extras* item is added as its own key (a ``line`` or ``pid``
    made of digits becomes a number)."""
    finding = record_finding(lint, cause)
    finding["cause"] = cause
    finding["remedy"] = remedy
    for key, value in extras.items():
        finding[key] = int(value) if key in _NUMERIC_KEYS and value.isdigit() else value
    return finding


def _parse_extras(pairs: list) -> Optional[dict]:
    """``KEY=VALUE`` arguments as a dict, or None when one is malformed."""
    extras = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            return None
        extras[key.strip()] = value
    return extras


def findings_document(runner: str, findings: list, now: Optional[datetime] = None) -> dict:
    """The findings file's document for *runner* holding *findings*."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {"schema_version": 1, "generated_at": stamp, "runner": runner, "findings": findings}


_USAGE = (
    "usage: gated_findings.py "
    "{build LINT EXIT_CODE | record LINT SUMMARY | "
    "instance LINT CAUSE REMEDY [KEY=VALUE ...] | array OUT_PATH | count FILE | "
    "split FILE | write OUT_PATH RUNNER}"
)
_ARITY = {"build": 2, "record": 2, "array": 1, "count": 1, "split": 1, "write": 2}


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args and args[0] == "instance":
        extras = _parse_extras(args[4:]) if len(args) >= 4 else None
        if extras is None:
            print(_USAGE, file=sys.stderr)
            return 2
        print(json.dumps(instance_finding(args[1], args[2], args[3], extras)))
        return 0
    if not args or _ARITY.get(args[0]) != len(args) - 1:
        print(_USAGE, file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    if command == "array":
        findings = [json.loads(line) for line in sys.stdin if line.strip()]
        os.makedirs(os.path.dirname(os.path.abspath(rest[0])), exist_ok=True)
        with open(rest[0], "w", encoding="utf-8") as handle:
            json.dump(findings, handle, indent=2, sort_keys=True)
            handle.write("\n")
    elif command == "count":
        if os.path.exists(rest[0]):
            with open(rest[0], encoding="utf-8") as handle:
                print(len(json.load(handle)))
        else:
            print(0)
    elif command == "build":
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
