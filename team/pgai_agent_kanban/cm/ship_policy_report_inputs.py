#!/usr/bin/env python3
"""
ship_policy_report_inputs.py -- the ship-policy matrix's inputs, read from a
TESTER report.

The CM ship-policy decision matrix (``cm_ship_policy``,
team/scripts/lib/cm/release_ship_policy.sh, Step 4d) decides from a handful
of normalized tokens. This module produces the ones that come from the
TESTER report. It never reads the report text itself: every value comes from
the one report parser, ``pgai_agent_kanban.reports.tester_report``
(``parse_report``), whose docstring states how a finding, a field line and
the report-level Recommendation and Systemic Risk sections are recognised.
What this module adds is only the matrix's own vocabulary on top of the
parsed fields: which findings count, how several findings merge, and the
``none`` / ``unparseable`` tokens the matrix has rows for.

The bash readers ``read_ship_policy_finding_dimensions`` and
``read_ship_policy_report_values`` (team/scripts/lib/cm/
release_ship_policy_report.sh) are thin wrappers over this module's CLI,
run through ``pp_run_ops``.

Finding dimensions (``dims``)
-----------------------------
Prints ``<regression> <integrity>`` as its one and only stdout line, the
values cm_ship_policy's systemic_risk=high row reads.

- Every finding the parser returns is consulted, nested sub-findings
  included (each finding followed by its sub-findings, in report order).
- Only a finding whose OWN ``systemic_risk`` is ``high`` counts, so an
  unrelated low-risk finding's Regression never leaks into the decision.
- Across the counted findings ``yes`` beats ``no`` for regression, and
  ``breakage`` / ``destructive`` beat ``none`` for integrity (a later
  ``breakage`` or ``destructive`` replaces an earlier one). An absent or
  unrecognised value never downgrades a value another finding already
  set. With no counted value a dimension reads ``unknown`` -- the
  fail-loud input the matrix treats as "not clear to ship".
- A report that cannot be read prints ``unknown unknown``. A readable
  report with no finding at all prints the same, after one stderr line
  naming the report path and saying the values defaulted for that reason,
  so a report-shape problem is told apart from a finding that genuinely
  reads unknown. A report with findings narrates nothing.

Report values (``values``)
--------------------------
Prints five ``key=value`` lines, in this order::

    recommendation=<pass|ship-with-concerns|ship-with-serious-concerns|none|unparseable>
    recommendation_raw=<the value line as written, or "none" when absent>
    systemic_risk=<low|medium|high|none|unparseable>
    systemic_risk_raw=<the value line as written, or "none" when absent>
    has_large_fix_effort=<yes|no>

- ``recommendation`` / ``systemic_risk`` are the parser's report-level
  tokens. A section the parser finds no value line in reads ``none``. A
  value line that reads the literal word ``none`` reads ``none`` too: the
  whole line for Recommendation, the leading word (after an optional
  ``Label:`` prefix) for Systemic Risk, where ``none`` is a token the
  matrix has a row for. Any other value line the parser cannot reduce to a
  token reads ``unparseable`` -- the caller decides what that costs
  (cm_ship_policy floors it to the SHIP-WITH-CONCERNS row and Step 4d files
  a bug quoting the ``*_raw`` line).
- The ``*_raw`` lines are the parser's ``TesterReport.raw`` values: the
  value line as the report wrote it, HTML comments removed, so a caller
  filing a bug can quote it verbatim.
- ``has_large_fix_effort`` is ``yes`` when any finding's (or nested
  sub-finding's) own ``fix_effort`` is ``large``; prose, the template
  legend and text outside every finding never set it.
- A report that cannot be read prints ``none`` for every value and ``no``
  for the flag, after one stderr line saying so. Each value that was
  normalized from a decorated line, or could not be parsed, is narrated on
  stderr.

Usage (CLI):
    python3 -m pgai_agent_kanban.cm.ship_policy_report_inputs dims <report>
    python3 -m pgai_agent_kanban.cm.ship_policy_report_inputs values <report>
    Both modes exit 0 whatever the report holds (a missing or unreadable
    report included); 2 is a usage error.

Usage (import):
    from pgai_agent_kanban.reports.tester_report import parse_report
    from pgai_agent_kanban.cm.ship_policy_report_inputs import finding_dimensions
    regression, integrity = finding_dimensions(parse_report(path))
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
from typing import Optional

# Run by file path (``python3 .../ship_policy_report_inputs.py``) this module
# has no parent package and the interpreter puts only its own directory on
# sys.path, so the package import below would not resolve. Put this file's
# own package root -- the directory holding the pgai_agent_kanban/ this file
# sits in -- at the front of sys.path so the import resolves from the same
# tree, never another. Imported as a package member, sys.path is left
# untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.reports.tester_report import (  # noqa: E402
    Finding,
    TesterReport,
    parse_report_text,
    read_report_text,
)

__all__ = [
    "all_findings",
    "finding_dimensions",
    "report_values",
    "main",
]

UNKNOWN = "unknown"
NONE = "none"
UNPARSEABLE = "unparseable"

# A value line that says "none": decorations dropped, then optionally one
# plain "Label:" prefix, then the word none standing alone.
_DECORATION_RE = re.compile(r"[*`]")
_LEADING_NONE_RE = re.compile(r"^(?:[A-Za-z][A-Za-z ]*?\s*:\s*)?none(?![A-Za-z0-9_-])", re.IGNORECASE)


def all_findings(report: TesterReport) -> list:
    """Every finding in report order, each followed by its nested
    sub-findings (recursively) -- the findings the matrix inputs read."""
    flat: list = []

    def walk(findings: list) -> None:
        for finding in findings:
            flat.append(finding)
            walk(finding.sub_findings)

    walk(report.findings)
    return flat


def finding_dimensions(report: TesterReport) -> "tuple[str, str]":
    """``(regression, integrity)`` merged over the high-risk findings; see
    the module docstring, "Finding dimensions"."""
    regression = UNKNOWN
    integrity = UNKNOWN
    for finding in all_findings(report):
        if finding.systemic_risk != "high":
            continue
        if finding.regression == "yes":
            regression = "yes"
        elif finding.regression == "no" and regression != "yes":
            regression = "no"
        if finding.integrity in ("breakage", "destructive"):
            integrity = finding.integrity
        elif finding.integrity == "none" and integrity not in ("breakage", "destructive"):
            integrity = "none"
    return regression, integrity


def _cleaned(raw: str) -> str:
    """*raw* without bold/backtick decoration or edge underscores."""
    return _DECORATION_RE.sub(" ", raw).strip().strip("_").strip()


def _matrix_token(token: Optional[str], raw: Optional[str], leading_none: bool) -> str:
    """The matrix token for one report-level value: the parser's *token*, or
    ``none`` / ``unparseable`` (module docstring, "Report values")."""
    if raw is None:
        return NONE
    if token is not None:
        return token
    cleaned = _cleaned(raw)
    if not cleaned or cleaned.lower() == NONE:
        return NONE
    if leading_none and _LEADING_NONE_RE.match(cleaned):
        return NONE
    return UNPARSEABLE


def report_values(report: TesterReport) -> dict:
    """The five ``values`` keys, in output order (module docstring, "Report
    values")."""
    rec_raw = report.raw.get("recommendation")
    risk_raw = report.raw.get("systemic_risk")
    large = any(finding.fix_effort == "large" for finding in all_findings(report))
    return {
        "recommendation": _matrix_token(report.recommendation, rec_raw, leading_none=False),
        "recommendation_raw": rec_raw if rec_raw is not None else NONE,
        "systemic_risk": _matrix_token(report.systemic_risk, risk_raw, leading_none=True),
        "systemic_risk_raw": risk_raw if risk_raw is not None else NONE,
        "has_large_fix_effort": "yes" if large else "no",
    }


def _load(report_path: str) -> Optional[TesterReport]:
    """The parsed report at *report_path*, or None when it cannot be read."""
    try:
        return parse_report_text(read_report_text(report_path))
    except OSError:
        return None


def _run_dims(report_path: str) -> None:
    report = _load(report_path)
    if report is None:
        print(f"{UNKNOWN} {UNKNOWN}")
        return
    if not report.findings:
        # Said before the fail-loud default, so a caller merging both
        # streams can tell a report whose shape hid every finding from a
        # finding that genuinely reads unknown.
        print(
            f"zero finding blocks in {report_path}: no heading at level 2 to 6 "
            f'whose text starts with the word "Finding"; regression/integrity '
            f"defaulted to unknown",
            file=sys.stderr,
        )
        sys.stderr.flush()
    regression, integrity = finding_dimensions(report)
    print(f"{regression} {integrity}")


def _run_values(report_path: str) -> None:
    report = _load(report_path)
    if report is None:
        print(
            f"report not readable at {report_path}: recommendation and "
            f"systemic risk defaulted to none",
            file=sys.stderr,
        )
        report = TesterReport(recommendation=None, systemic_risk=None, summary=None, findings=[])
    values = report_values(report)
    for label, key in (("Recommendation", "recommendation"), ("Systemic Risk", "systemic_risk")):
        raw = report.raw.get(key)
        token = values[key]
        if token == UNPARSEABLE:
            print(f"{label} in {report_path} could not be parsed: {raw!r}", file=sys.stderr)
        elif raw is not None and raw.strip().lower() != token:
            print(f"{label} in {report_path} normalized: {raw!r} -> {token}", file=sys.stderr)
    sys.stderr.flush()
    for key, value in values.items():
        print(f"{key}={value}")


def main(argv: "list | None" = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.ship_policy_report_inputs",
        description=(
            "Print the CM ship-policy matrix's inputs read from a TESTER report: "
            "'dims' prints '<regression> <integrity>' for the systemic_risk=high "
            "row; 'values' prints the normalized Recommendation and Systemic Risk, "
            "their raw lines and the large-fix-effort flag. Reads only; exits 0 "
            "for any report, a missing one included."
        ),
    )
    parser.add_argument("mode", choices=("dims", "values"), help="Which inputs to print.")
    parser.add_argument("report", help="Path to the TESTER report (artifacts/report.md).")
    args = parser.parse_args(argv)
    if args.mode == "dims":
        _run_dims(os.fspath(args.report))
    else:
        _run_values(os.fspath(args.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
