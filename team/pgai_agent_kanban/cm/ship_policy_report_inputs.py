#!/usr/bin/env python3
"""
ship_policy_report_inputs.py -- the ship-policy matrix's inputs, read from a
TESTER report and from the TESTER task that wrote it.

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

The bash readers ``read_ship_policy_finding_dimensions``,
``read_ship_policy_report_values`` and ``read_ship_policy_tester_lookup``
(team/scripts/lib/cm/release_ship_policy_report.sh) are thin wrappers over
this module's CLI, run through ``pp_run_ops``.

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

TESTER-task lookup (``lookup``)
-------------------------------
Locates the TESTER task for an active RC under a tasks directory in one
pass, reading only task ``README.md`` / ``status.md`` fields (never a
report), and prints ``key=value`` lines::

    tester_task_dir=<absolute path, or empty>
    tester_task_id=<folder basename, or "none">
    tester_state=<status.md ## State, or "none">
    tester_report_path=<path to artifacts/report.md when it exists, or empty>
    tester_task_found=<yes|no>
    mismatch_field=<Role|Release Version>   (zero or more triples, each in
    mismatch_raw=<raw field text>             this order)
    mismatch_path=<README.md path>

- Only a folder holding both ``README.md`` and ``status.md`` is read. Each
  field is read through ``pgai_agent_kanban.cm.read_state_field`` (whose
  section comes from ``pgai_agent_kanban.task_status.read_section``); a
  missing heading or empty value reads as absent.
- A candidate is a task whose ``## Role`` reads TESTER (its first word,
  bold markers, backticks and case ignored) and whose ``## Release
  Version``, reduced by ``pgai_agent_kanban.cm.release_version.version_token``,
  equals the active RC (compared lowercased). A Release Version reading
  ``none`` is a task that targets no RC and is skipped silently.
- A TESTER-role task whose Release Version is present but not
  version-shaped (``vX.Y.Z`` after normalisation) is never a candidate and
  is reported as a ``Release Version`` mismatch, whatever the active RC.
- A candidate whose Role is not exactly ``TESTER``, or whose Release Version
  is not exactly the active RC as written (surrounding whitespace ignored),
  is still a candidate, and one ``Role`` / ``Release Version`` mismatch is
  reported for each such field.
- Folders are visited in ascending name order, which is creation order for
  task IDs. The last DONE candidate (``## State`` compared case-insensitively)
  wins, else the last candidate of any state; a missing State reads
  ``none``.
- Every mismatch is narrated on stderr as it is found, naming the task, the
  field, its raw text and the README path; the stderr lines are flushed
  before any stdout line, so a caller merging both streams reads the
  values after the narration. A missing tasks directory finds nothing.

Usage (CLI):
    python3 -m pgai_agent_kanban.cm.ship_policy_report_inputs dims <report>
    python3 -m pgai_agent_kanban.cm.ship_policy_report_inputs values <report>
    python3 -m pgai_agent_kanban.cm.ship_policy_report_inputs lookup <tasks_dir> <active_rc>
    Every mode exits 0 whatever it reads (a missing or unreadable report or
    tasks directory included); 2 is a usage error.

Usage (import):
    from pgai_agent_kanban.reports.tester_report import parse_report
    from pgai_agent_kanban.cm.ship_policy_report_inputs import finding_dimensions
    regression, integrity = finding_dimensions(parse_report(path))

    from pgai_agent_kanban.cm.ship_policy_report_inputs import tester_task_lookup
    found = tester_task_lookup("/path/to/tasks", "v1.2.3")
    found.task_dir, found.state, found.mismatches
"""

from __future__ import annotations

import argparse
import dataclasses
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

from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.cm.release_version import VERSION_SHAPE_RE, version_token  # noqa: E402
from pgai_agent_kanban.reports.tester_report import (  # noqa: E402
    Finding,
    TesterReport,
    parse_report_text,
    read_report_text,
)

__all__ = [
    "TesterTaskLookup",
    "all_findings",
    "finding_dimensions",
    "lookup_lines",
    "report_values",
    "tester_task_lookup",
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


@dataclasses.dataclass
class TesterTaskLookup:
    """What :func:`tester_task_lookup` found.

    ``task_dir`` and ``state`` are None when no candidate matched.
    ``mismatches`` holds ``(field, raw, readme_path)`` triples in the order
    found, and ``notes`` the stderr narration lines, one per mismatch, in
    the same order.
    """

    task_dir: Optional[str] = None
    state: Optional[str] = None
    mismatches: list = dataclasses.field(default_factory=list)
    notes: list = dataclasses.field(default_factory=list)

    @property
    def found(self) -> bool:
        """True when a TESTER task matched (``task_dir`` is set)."""
        return self.task_dir is not None


def _first_word(value: str) -> str:
    """The first letters-and-dashes word of *value*, lowercased, with
    underscores read as dashes and trailing dashes dropped."""
    m = re.match(r"([A-Za-z][A-Za-z_-]*)", value.strip())
    return m.group(1).lower().replace("_", "-").rstrip("-") if m else ""


def _read_task_field(path: str, heading: str) -> Optional[str]:
    """*heading*'s value in the task file at *path*, or None when the file
    is missing or the heading has no value (read_state_field's "none")."""
    value = read_state_field(path, heading)
    return None if value == "none" else value


def tester_task_lookup(tasks_dir: str, active_rc: str) -> TesterTaskLookup:
    """Locate the TESTER task for *active_rc* under *tasks_dir*; see the
    module docstring, "TESTER-task lookup", for the selection and mismatch
    rules. Reads only; never raises for a missing *tasks_dir*."""
    active_rc_token = active_rc.strip().lower()
    result = TesterTaskLookup()
    candidates = []  # (task_dir, state)
    if os.path.isdir(tasks_dir):
        for name in sorted(os.listdir(tasks_dir)):
            task_dir = os.path.join(tasks_dir, name)
            readme = os.path.join(task_dir, "README.md")
            status = os.path.join(task_dir, "status.md")
            if not (os.path.isdir(task_dir) and os.path.isfile(readme) and os.path.isfile(status)):
                continue
            role_raw = _read_task_field(readme, "Role")
            if role_raw is None or _first_word(re.sub(r"[*`]", " ", role_raw)) != "tester":
                continue
            version_raw = _read_task_field(readme, "Release Version")
            if version_raw is None:
                continue
            version = version_token(version_raw)
            if version == "none":
                continue
            if not VERSION_SHAPE_RE.match(version):
                result.mismatches.append(("Release Version", version_raw, readme))
                result.notes.append(
                    f"TESTER task {name}: Release Version {version_raw!r} in "
                    f"{readme} is not version-shaped (expected vX.Y.Z); the task "
                    f"cannot be tied to any RC and is not a candidate for {active_rc}"
                )
                continue
            if version != active_rc_token:
                continue
            if role_raw.strip() != "TESTER":
                result.mismatches.append(("Role", role_raw, readme))
                result.notes.append(
                    f"TESTER task {name}: Role {role_raw!r} in {readme} matched TESTER only after normalization"
                )
            if version_raw.strip() != active_rc.strip():
                result.mismatches.append(("Release Version", version_raw, readme))
                result.notes.append(
                    f"TESTER task {name}: Release Version {version_raw!r} in "
                    f"{readme} matched {active_rc} only after normalization"
                )
            state = _read_task_field(status, "State")
            candidates.append((task_dir, state if state is not None else "none"))

    selected = None
    for task_dir, state in candidates:
        if state.strip().lower() == "done":
            selected = (task_dir, state)
    if selected is None and candidates:
        selected = candidates[-1]
    if selected is not None:
        result.task_dir, result.state = selected
    return result


def lookup_lines(result: TesterTaskLookup) -> list:
    """The ``lookup`` mode's stdout lines for *result*, in output order (module
    docstring, "TESTER-task lookup")."""
    if not result.found:
        lines = [
            "tester_task_dir=",
            "tester_task_id=none",
            "tester_state=none",
            "tester_report_path=",
            "tester_task_found=no",
        ]
    else:
        report = os.path.join(result.task_dir, "artifacts", "report.md")
        lines = [
            f"tester_task_dir={result.task_dir}",
            f"tester_task_id={os.path.basename(result.task_dir)}",
            f"tester_state={result.state}",
            f"tester_report_path={report if os.path.isfile(report) else ''}",
            "tester_task_found=yes",
        ]
    for field, raw, readme in result.mismatches:
        lines.append(f"mismatch_field={field}")
        lines.append(f"mismatch_raw={raw}")
        lines.append(f"mismatch_path={readme}")
    return lines


def _run_lookup(tasks_dir: str, active_rc: str) -> None:
    result = tester_task_lookup(tasks_dir, active_rc)
    for note in result.notes:
        print(note, file=sys.stderr)
    sys.stderr.flush()
    for line in lookup_lines(result):
        print(line)


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
            "Print the CM ship-policy matrix's inputs: 'dims' prints "
            "'<regression> <integrity>' for the systemic_risk=high row; 'values' "
            "prints the normalized Recommendation and Systemic Risk, their raw "
            "lines and the large-fix-effort flag; 'lookup' prints the TESTER task "
            "found for an RC and any Role / Release Version mismatches. Reads "
            "only; exits 0 whatever it reads, a missing report or tasks "
            "directory included."
        ),
    )
    modes = parser.add_subparsers(dest="mode", required=True, metavar="{dims,values,lookup}")
    for mode, help_text in (
        ("dims", "Print '<regression> <integrity>' from a TESTER report."),
        ("values", "Print the report-level values and the large-fix-effort flag."),
    ):
        mode_parser = modes.add_parser(mode, help=help_text)
        mode_parser.add_argument("report", help="Path to the TESTER report (artifacts/report.md).")
    lookup_parser = modes.add_parser("lookup", help="Print the TESTER task for an RC.")
    lookup_parser.add_argument("tasks_dir", help="The project's tasks directory.")
    lookup_parser.add_argument("active_rc", help="The RC being decided (vX.Y.Z).")
    args = parser.parse_args(argv)
    if args.mode == "dims":
        _run_dims(os.fspath(args.report))
    elif args.mode == "values":
        _run_values(os.fspath(args.report))
    else:
        _run_lookup(args.tasks_dir, args.active_rc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
