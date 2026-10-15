# TEST-ONLY -- imported by no production code. Kept for this release only,
# with the legacy reader copies beside it; deleted together with
# team/tests/unit/test_tester_report_differential.py once the readers are
# migrated onto pgai_agent_kanban.reports.tester_report.
"""
differential.py -- run the legacy TESTER-report readers over one report and
say where they read it differently.

The legacy readers
------------------
Five consumers read a TESTER report before the one parser replaces them.
Four of them take every finding field through one shared reading path, so
they are one reader here:

``stop_bug_files``
    The block split and field reader of the legacy ``stop_bug_files`` copy
    (``finding_sections`` / ``field_first_word`` / ``filed_as_bug_id``).
    The Known Issues stop list, the Bugs Skipped table
    (``skipped_bug_pairs``) and the ship-policy matrix's ``dims`` and
    ``values`` modes all read their finding fields through it, which the
    differential test proves on every corpus report by running each of
    those consumers and checking its output against this reading. Fields:
    category, systemic_risk, fix_effort, regression, integrity, filed_as.
``resolution_matrix``
    OVERWATCH's ``parse_tester_report_findings``: the same block split, but
    its own two field patterns. Fields: filed_as, regression.
``ship_policy_values``
    The ship-policy ``values`` mode's report-level read (``read_state_field``
    plus its own normalisation). Fields: report recommendation and report
    systemic_risk. No other legacy reader reads these, so they can never
    disagree; they are recorded for the parser comparison.

How a reading is compared
-------------------------
Each reader's reading is expressed in one vocabulary, the parser's: an
enumerated field is its lowercase token from that vocabulary
(``ENUMERATED_FIELDS`` below), and ``filed_as`` is the canonical
id the reader takes the value to name. ``None`` means the reader read the
field as absent: no field line it recognises, a value outside the
vocabulary, or (for ``filed_as``) no filing.

- ``stop_bug_files``: ``field_first_word``, kept when it is a vocabulary
  token; ``filed_as_bug_id``, the first ``BUG-NNNN`` id.
- ``resolution_matrix``: ``regression`` True/False/None as ``yes``/``no``/
  None. ``filed_as`` is None when the matrix counts the finding as not
  filed (no line, or one of its own not-filed sentinels, the rule
  ``findings_all_filed`` applies); otherwise the first ``BUG-N`` or
  ``PRIORITY-N`` id in its value in canonical form, or the value as written
  when it names no id.
- ``ship_policy_values``: the normalised token; its ``none`` and
  ``unparseable`` both read None.

Two readers disagree about a finding field when both read it and their
values differ. A disagreement in the block split itself (the readers'
blocks differ) is reported once per report as ``finding split`` with each
reader's block count, and the per-finding comparison is skipped for that
report because positions no longer line up.

A finding is named by its 1-based position in the report's finding split,
which is also its position in the parser's findings flattened in order
(each finding followed by its nested sub-findings): the legacy split opens
a block at every finding heading, nested ones included.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import pathlib
import re
from typing import Optional

from tests.fixtures.tester_reports.legacy_readers import resolution_matrix as legacy_matrix
from tests.fixtures.tester_reports.legacy_readers import ship_policy_report as legacy_ship_policy
from tests.fixtures.tester_reports.legacy_readers import stop_bug_files as legacy_stop

STOP_BUG_FILES = "stop_bug_files"
RESOLUTION_MATRIX = "resolution_matrix"
SHIP_POLICY_VALUES = "ship_policy_values"
READERS = (STOP_BUG_FILES, RESOLUTION_MATRIX, SHIP_POLICY_VALUES)

# The parser's vocabulary for each enumerated finding field: the words the
# legacy readers' readings are expressed in. Kept equal to
# pgai_agent_kanban.reports.tester_report.ENUMERATED_FIELDS, so a legacy
# reading and the parser's are compared in the same words.
ENUMERATED_FIELDS = {
    "category": ("pass", "pass-with-caveat", "gap", "bug", "pre-existing-bug", "stale-assertion"),
    "systemic_risk": ("low", "medium", "high"),
    "fix_effort": ("small", "medium", "large"),
    "regression": ("yes", "no"),
    "integrity": ("none", "breakage", "destructive"),
}
_FIELD_LABELS = {
    "category": "Category",
    "systemic_risk": "Systemic Risk",
    "fix_effort": "Fix Effort",
    "regression": "Regression",
    "integrity": "Integrity",
}
FINDING_FIELDS = ("category", "systemic_risk", "fix_effort", "regression", "integrity", "filed_as")
REPORT_FIELDS = ("recommendation", "systemic_risk")

# Which finding fields, and which report fields, each reader reads.
READER_FINDING_FIELDS = {
    STOP_BUG_FILES: FINDING_FIELDS,
    RESOLUTION_MATRIX: ("filed_as", "regression"),
    SHIP_POLICY_VALUES: (),
}
READER_REPORT_FIELDS = {
    STOP_BUG_FILES: (),
    RESOLUTION_MATRIX: (),
    SHIP_POLICY_VALUES: REPORT_FIELDS,
}

# The field name a split disagreement is reported under.
SPLIT_FIELD = "finding split"

_ID_RE = re.compile(r"\b(BUG|PRIORITY)-(\d+)", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class ReaderReading:
    """What one legacy reader read from one report.

    ``blocks`` is the body of each finding block in report order (None for a
    reader that does not split findings); ``findings`` holds one
    ``{field: value}`` dict per block for the fields the reader reads;
    ``report`` holds its report-level fields.
    """

    reader: str
    blocks: Optional[tuple]
    findings: tuple
    report: dict


@dataclasses.dataclass(frozen=True)
class Disagreement:
    """Two or more legacy readers reading one field of one report differently.

    ``finding`` is the 1-based position in the finding split, or None for a
    report-level field or the split itself. ``values`` is one
    ``(reader, value)`` pair per reader that reads the field, in READERS
    order.
    """

    report: str
    finding: Optional[int]
    field: str
    values: tuple

    @property
    def key(self) -> tuple:
        return (self.report, self.finding, self.field)


def _vocabulary_token(word: str, field: str) -> Optional[str]:
    return word if word in ENUMERATED_FIELDS[field] else None


def _canonical_id(value: str) -> Optional[str]:
    match = _ID_RE.search(value)
    return f"{match.group(1).upper()}-{int(match.group(2)):04d}" if match else None


def read_with_stop_bug_files(report_text: str) -> ReaderReading:
    """The shared field reader's reading (see the module docstring)."""
    blocks = tuple(body for _heading, body in legacy_stop.finding_sections(report_text))
    findings = []
    for body in blocks:
        reading = {
            field: _vocabulary_token(legacy_stop.field_first_word(body, label), field)
            for field, label in _FIELD_LABELS.items()
        }
        reading["filed_as"] = legacy_stop.filed_as_bug_id(body) or None
        findings.append(reading)
    return ReaderReading(STOP_BUG_FILES, blocks, tuple(findings), {})


def _matrix_filed_as(raw: Optional[str]) -> Optional[str]:
    """resolution_matrix's Filed As reading in the parser's vocabulary."""
    if raw is None or raw.strip().lower() in legacy_matrix._NOT_FILED_VALUES:
        return None
    return _canonical_id(raw) or raw


def read_with_resolution_matrix(report_text: str) -> ReaderReading:
    """OVERWATCH's finding parser's reading (see the module docstring)."""
    parsed = legacy_matrix.parse_tester_report_findings(report_text)
    findings = tuple(
        {
            "filed_as": _matrix_filed_as(finding["filed_as"]),
            "regression": {True: "yes", False: "no", None: None}[finding["regression"]],
        }
        for finding in parsed
    )
    return ReaderReading(RESOLUTION_MATRIX, tuple(f["body"] for f in parsed), findings, {})


def read_with_ship_policy_values(report_path: pathlib.Path) -> ReaderReading:
    """The ship-policy values mode's report-level reading."""
    with contextlib.redirect_stderr(io.StringIO()):
        values = legacy_ship_policy.report_values(str(report_path))
    report = {
        field: (None if values[field] in ("none", "unparseable") else values[field])
        for field in REPORT_FIELDS
    }
    return ReaderReading(SHIP_POLICY_VALUES, None, (), report)


def legacy_readings(report_path: pathlib.Path) -> dict:
    """Every legacy reader's reading of the report at *report_path*."""
    text = pathlib.Path(report_path).read_text(encoding="utf-8")
    return {
        STOP_BUG_FILES: read_with_stop_bug_files(text),
        RESOLUTION_MATRIX: read_with_resolution_matrix(text),
        SHIP_POLICY_VALUES: read_with_ship_policy_values(pathlib.Path(report_path)),
    }


def _splitters(readings: dict) -> list:
    return [readings[name] for name in READERS if readings[name].blocks is not None]


def split_agrees(readings: dict) -> bool:
    """True when every reader that splits findings split them identically."""
    return len({reading.blocks for reading in _splitters(readings)}) <= 1


def finding_count(readings: dict) -> int:
    """The number of findings the readers split the report into (the split
    must agree; see split_agrees)."""
    splitters = _splitters(readings)
    return len(splitters[0].blocks) if splitters else 0


def disagreements(report_name: str, readings: dict) -> list:
    """Every field of the report two or more legacy readers read differently."""
    found = []
    if not split_agrees(readings):
        found.append(Disagreement(
            report_name, None, SPLIT_FIELD,
            tuple((r.reader, len(r.blocks)) for r in _splitters(readings)),
        ))
    else:
        for index in range(finding_count(readings)):
            for field in FINDING_FIELDS:
                values = tuple(
                    (name, readings[name].findings[index][field])
                    for name in READERS if field in READER_FINDING_FIELDS[name]
                )
                if len({value for _name, value in values}) > 1:
                    found.append(Disagreement(report_name, index + 1, field, values))
    for field in REPORT_FIELDS:
        values = tuple(
            (name, readings[name].report[field])
            for name in READERS if field in READER_REPORT_FIELDS[name]
        )
        if len({value for _name, value in values}) > 1:
            found.append(Disagreement(report_name, None, field, values))
    return found


def agreed_values(readings: dict) -> dict:
    """``{(finding, field): value}`` for every field the legacy readers that
    read it all read the same way. ``finding`` is the 1-based position, or
    None for a report-level field. Per-finding fields are included only when
    the split agrees."""
    agreed = {}
    if split_agrees(readings):
        for index in range(finding_count(readings)):
            for field in FINDING_FIELDS:
                values = {
                    readings[name].findings[index][field]
                    for name in READERS if field in READER_FINDING_FIELDS[name]
                }
                if len(values) == 1:
                    agreed[(index + 1, field)] = values.pop()
    for field in REPORT_FIELDS:
        values = {
            readings[name].report[field]
            for name in READERS if field in READER_REPORT_FIELDS[name]
        }
        if len(values) == 1:
            agreed[(None, field)] = values.pop()
    return agreed


def flattened_findings(findings: list) -> list:
    """Parser findings in report order, each followed by its sub-findings,
    recursively -- the order the legacy split opens blocks in. Works on
    Finding objects and on their JSON dicts alike."""
    flat = []
    for finding in findings:
        flat.append(finding)
        subs = finding["sub_findings"] if isinstance(finding, dict) else finding.sub_findings
        flat.extend(flattened_findings(subs))
    return flat
