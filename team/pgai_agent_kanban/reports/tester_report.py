#!/usr/bin/env python3
"""
tester_report.py -- the one parser for a TESTER verification report.

``parse_report`` reads a report (``artifacts/report.md``) into a
``TesterReport``: the report-level ``recommendation``, ``systemic_risk`` and
``summary``, and one ``Finding`` per finding the report carries. Every
consumer reads these fields instead of matching the report text itself, so
two consumers can never read one report two ways. The rules below are the
whole contract; each is pinned by ``team/tests/unit/test_tester_report.py``.

Reading is liberal, results are strict: the parser tolerates the variation a
writer introduces, and every enumerated field comes back as one lowercase
token from a fixed vocabulary, or ``None`` when absent or unrecognised --
never a guessed value.

What the parser sees
--------------------
Fenced code blocks and HTML comments are not report text. A fenced block
(a run of three or more backticks or tildes, paired with its closer by
``pgai_agent_kanban.lib.markdown_fence``) is example text: a heading or
field line inside one is never read. An HTML comment is found by
``pgai_agent_kanban.lib.html_comments``, the way markdown renders it: a
``<!--`` quoted in prose or inside an inline code span is text, and a
comment opened mid-line counts only when closed in its own paragraph. One
rule is this parser's own: an opener with no closer anywhere after it -- a
fence or a line-start ``<!--`` -- hides nothing and reads as text, so one
stray opener can never hide the findings and fields below it.

Rule 1 -- what starts a finding
-------------------------------
A finding starts at a Markdown ATX heading of level 2 to 6 whose text
begins with the whole word ``Finding`` (singular, any case). The heading is
0-3 spaces, then 2-6 ``#``, then the text; the space after the hashes may be
missing for a finding heading only (``###Finding 1:`` opens a finding;
``###Step 7`` is not a heading at all). So ``### Finding 1: title``,
``#### Finding 2 -- title``, ``## Finding 3:``, ``### Finding: title`` and
``### finding 4`` all open a finding, while ``## Findings``,
``### Findings summary`` (plural), ``# Finding 1`` (level 1 is the report
title) and ``####### Finding`` (not a heading) do not. Emphasis markers
around the heading text are ignored, and trailing closing ``#`` are dropped.

After the word ``Finding`` an optional label follows: digits
(``1``, ``12``, ``3.2``, ``4b``) or a single capital letter followed by a
separator (``A:``). ``number`` is the label's leading digits as an int, or
``None`` for no label or a letter label (``label`` keeps it as written).
``title`` is the rest of the heading after the label and one run of
separators (``:``, ``.``, ``)``, ``-``, ``--``, an en or em dash), or ``""``.

Rule 2 -- where a finding ends
------------------------------
A finding ends at the next heading whose level is no deeper than its own
(``## Lint Findings`` or ``### Step 7`` after ``### Finding 1``; a sibling
``### Finding 2``), or at the end of the report. A deeper heading stays
inside the finding, and that includes a deeper finding heading: ``####
Finding 2`` inside ``### Finding 1`` opens a sub-block of finding 1, not a
finding of its own. The sub-block is split from the outer finding's lines by
these same two rules (it ends at the next heading no deeper than its own)
and comes back parsed in the outer finding's ``sub_findings``. Its lines stay
in the outer finding's ``body``, but its field lines are its own: the outer
finding's fields are read only from the outer finding's own lines, so they
are never dropped, overridden or filled in from a sub-block. Setext headings
(text underlined with ``===``/``---``) are not headings; a ``---`` line is
a thematic break and ends nothing.

Rule 3 -- how a field line is recognised
----------------------------------------
A field line is one line of the form ``Label: value``, read after removing
any leading indentation and one list marker (``-``, ``*``, ``+``, ``1.``,
``1)``). The label may be wrapped in emphasis (``**``, ``__``, ``*``,
``_``) with the colon inside or outside it, the whole line may be wrapped
(``**Label: value**``), and the label and the value may each be wrapped in
quotes or backticks (``"Label"``, ``'high'``, a backticked id). So
``**Systemic Risk:** high``, ``**Systemic Risk**: high``,
``- **Systemic Risk:** "high"``, ``Systemic Risk: high`` and
``**Systemic Risk: high**`` are the same field line. A label starts with a
letter and runs at most 60 characters of letters, digits, spaces and
``_ / & ( ) ' . , + -``. Quoted (``>``) lines and table rows are not field
lines.

The eight named fields (``Filed As``, ``Filed By``, ``Severity``,
``Category``, ``Regression``, ``Integrity``, ``Fix Effort``,
``Systemic Risk``) are read in plain or bold form. Any other label is read
only in bold form and lands in ``Finding.extra`` -- a plain ``Note: ...``
sentence is prose, not a field. ``Integrity Damage`` is its own label (a
yes/no field older reports carry), not ``Integrity``, so it lands in extra.
In each finding the first field line carrying a non-empty value decides a
field; later lines for the same label are ignored, and a label line with
nothing after its colon is passed over.

Rule 4 -- case and whitespace
-----------------------------
Labels match case-insensitively, and any run of spaces, tabs, underscores or
dashes inside a label reads as one space (``fix_effort``, ``Fix-Effort`` and
``FIX  EFFORT`` are ``Fix Effort``). Values are trimmed. Enumerated values
match case-insensitively, a token's own dashes may be written as spaces or
underscores (``pass with caveat`` reads ``pass-with-caveat``), and the token
must lead the value and stand alone: whatever follows it after a space or
punctuation is an annotation and is ignored (``medium (could recur)`` and
``High.`` read ``medium`` and ``high``), but a token running on into more
letters or dashes is not that token (``small-to-medium`` and
``pre-existing-bugs`` read ``None``). The longest token that fits is the
one read, so ``pre-existing-bug`` reads as its own category, never as
``bug``. A value listing two or more of the
field's tokens separated by ``|`` or ``/`` is the report template's
placeholder and reads ``None`` (``low / medium / high``). Headings follow the
same rule: the word ``Finding`` in any case, any spaces or tabs after the
hashes.

Rule 5 -- values that span lines
--------------------------------
A value never spans lines: it is the text after the colon on the field's own
line, and the line break ends it. The next line is body text even when no
blank line separates them (reports routinely put prose directly under a
field line), and a label with nothing after its colon has no value.

Fields
------
``category``       pass | pass-with-caveat | gap | bug | pre-existing-bug |
                   stale-assertion
``severity``       low | medium | high | critical
``fix_effort``     small | medium | large
``systemic_risk``  low | medium | high
``regression``     yes | no
``integrity``      none | breakage | destructive
``filed_as``       the first ``BUG-N`` or ``PRIORITY-N`` id anywhere in the
                   value, in any case, returned upper-case with its number
                   zero-padded to four digits, so a lowercase, unpadded or
                   cited id reads in its canonical form; ``None`` when the
                   value names no id (``none``, ``not filed``)
``filed_by``       the value as written
``extra``          ``{key: value}`` for every other bold label, the key
                   being the label lowercased with each run of other
                   characters turned into ``_`` (``Integrity Damage`` ->
                   ``integrity_damage``)
``raw``            ``{field: value}`` for each named field present, the value
                   as written after Rule 3's unwrapping, so a caller can
                   quote what the report said
``sub_findings``   the nested finding sub-blocks (Rule 2), each a Finding

Report level: ``recommendation`` (pass | ship-with-concerns |
ship-with-serious-concerns) and ``systemic_risk`` (low | medium | high) are
read from the first non-blank line of their own ``## Recommendation`` /
``## Systemic Risk`` section (heading level 1 or 2, text starting with that
name), skipping thematic breaks and deeper headings; a value after a colon on
the heading line itself comes first. The line is read whole first, then as a
field line, so ``SHIP-WITH-CONCERNS``, ``**Decision: PASS**`` and
``Recommendation: pass`` all read. ``summary`` is the text of the
``## Summary`` (or ``## Executive Summary``) section with comments removed
and surrounding blank lines dropped, or ``None``. Nothing at report level is
derived from the findings.

Usage (import):
    from pgai_agent_kanban.reports.tester_report import parse_report
    report = parse_report(pathlib.Path("/path/to/report.md"))
    high = [f for f in report.findings if f.systemic_risk == "high"]

Usage (CLI):
    python3 -m pgai_agent_kanban.reports.tester_report <report> [--json]
    <report> is a report path, or "-" for standard input. --json prints
    TesterReport.to_json(); without it a short plain-text summary prints.
    Exits 0 on any readable report (a report with no findings included),
    1 when the report cannot be read, 2 on a usage error.

Shell callers use team/scripts/lib/tester_report.sh (tester_report_json),
which runs this CLI through pp_run_ops.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import sys
from typing import Optional

# Run by file path (``python3 .../tester_report.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.html_comments import comment_spans  # noqa: E402
from pgai_agent_kanban.lib.markdown_fence import find_fence_close, match_fence_opener  # noqa: E402

__all__ = [
    "ENUMERATED_FIELDS",
    "FIELD_LABELS",
    "RECOMMENDATION_TOKENS",
    "Finding",
    "TesterReport",
    "parse_report",
    "parse_report_text",
    "read_report_text",
]

# The vocabulary of each enumerated finding field (Rule 4 and "Fields").
# "category" holds every finding category the TESTER role file defines.
ENUMERATED_FIELDS = {
    "category": ("pass", "pass-with-caveat", "gap", "bug", "pre-existing-bug", "stale-assertion"),
    "severity": ("low", "medium", "high", "critical"),
    "fix_effort": ("small", "medium", "large"),
    "systemic_risk": ("low", "medium", "high"),
    "regression": ("yes", "no"),
    "integrity": ("none", "breakage", "destructive"),
}
RECOMMENDATION_TOKENS = ("pass", "ship-with-concerns", "ship-with-serious-concerns")
_REPORT_SYSTEMIC_RISK_TOKENS = ENUMERATED_FIELDS["systemic_risk"]

# Normalised label (Rule 4) -> Finding attribute, in Finding field order.
FIELD_LABELS = {
    "filed as": "filed_as",
    "filed by": "filed_by",
    "severity": "severity",
    "category": "category",
    "regression": "regression",
    "integrity": "integrity",
    "fix effort": "fix_effort",
    "systemic risk": "systemic_risk",
}

# Rule 1: an ATX heading needs a space or tab (or nothing) after its hashes...
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
# ...except a finding heading, which may run straight into the word.
_NOSPACE_FINDING_HEADING_RE = re.compile(r"^ {0,3}(#{2,6})((?i:finding)(?![A-Za-z0-9_]).*?)[ \t]*$")
_CLOSING_HASHES_RE = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_FINDING_WORD_RE = re.compile(r"^[*_`\s]*(?i:finding)(?![A-Za-z0-9_])[*_]*")
_FINDING_LABEL_RE = re.compile(
    r"\s*(?:(?i:no\.|number)\s*|#\s*)?"
    r"(?P<label>\d+(?:\.\d+)*[A-Za-z]?(?![A-Za-z0-9])|[A-Z](?=\s*(?:[:.)–—-]|$)))"
)
_TITLE_SEPARATOR_RE = re.compile(r"^[\s*_]*[:.)–—-]*\s*")
_FIRST_DIGITS_RE = re.compile(r"\d+")
_FINDING_MIN_LEVEL = 2
_SECTION_MAX_LEVEL = 2

# Rule 3.
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+")
_EMPHASIS_OPEN_RE = re.compile(r"^(\*\*|__|\*|_)")
_LABEL_TEXT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 \t_/&()'.,+-]{0,59}$")
_QUOTE_PAIRS = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’"}
_THEMATIC_BREAK_RE = re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")

_ID_RE = re.compile(r"\b(BUG|PRIORITY)-(\d+)", re.IGNORECASE)
_ALTERNATIVES_SPLIT_RE = re.compile(r"\s*[|/]\s*")
_SUMMARY_HEADING_RE = re.compile(r"^(?:executive\s+)?summary(?![A-Za-z0-9_])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Finding:
    """One finding: its heading, its body, and the fields read from it."""

    number: Optional[int]
    label: Optional[str]
    title: str
    heading: str
    level: int
    body: str
    filed_as: Optional[str] = None
    filed_by: Optional[str] = None
    severity: Optional[str] = None
    category: Optional[str] = None
    regression: Optional[str] = None
    integrity: Optional[str] = None
    fix_effort: Optional[str] = None
    systemic_risk: Optional[str] = None
    extra: dict = dataclasses.field(default_factory=dict)
    raw: dict = dataclasses.field(default_factory=dict)
    sub_findings: list = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        """The finding as plain JSON-ready data, keys in declaration order."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class TesterReport:
    """A parsed TESTER report: report-level fields plus its findings."""

    recommendation: Optional[str]
    systemic_risk: Optional[str]
    summary: Optional[str]
    findings: list
    raw: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        """The report as plain JSON-ready data, keys in declaration order."""
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        """The canonical JSON form the CLI prints: two-space indent, keys in
        declaration order, non-ASCII kept, one trailing newline."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    def to_text(self) -> str:
        """A short plain-text summary: report fields, then one line per finding."""
        out = [
            f"recommendation: {self.recommendation or 'none'}",
            f"systemic_risk: {self.systemic_risk or 'none'}",
            f"findings: {len(self.findings)}",
        ]
        for finding in self.findings:
            ident = finding.label if finding.label is not None else "-"
            fields = " ".join(
                f"{name}={getattr(finding, name)}"
                for name in ("category", "systemic_risk", "fix_effort", "filed_as")
                if getattr(finding, name) is not None
            )
            tags = f" [{fields}]" if fields else ""
            out.append(f"finding {ident}{tags}: {finding.title}")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# What the parser sees: fences and comments
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Line:
    raw: str     # the line as written
    text: str    # the line with HTML comments removed; "" inside a fence
    code: bool   # part of a fenced code block, delimiters included


def _code_line_flags(lines: list) -> list:
    """Mark each line that belongs to a closed fenced code block."""
    flags = [False] * len(lines)
    idx = 0
    while idx < len(lines):
        opener = match_fence_opener(lines[idx].strip())
        if opener is None:
            idx += 1
            continue
        close = find_fence_close(lines, idx + 1, opener[0])
        if close >= len(lines):
            idx += 1  # never closed: the opener is text and hides nothing
            continue
        for covered in range(idx, close + 1):
            flags[covered] = True
        idx = close + 1
    return flags


def _closed_comment_spans(text: str) -> list:
    """Comment spans, minus any line-start opener that is never closed."""
    spans: list = []
    base = 0
    while True:
        found = comment_spans(text[base:])
        if found:
            start, end = found[-1]
            if base + end == len(text) and not text[base + start:base + end].endswith("-->"):
                spans.extend((s + base, e + base) for s, e in found[:-1])
                base += start + len("<!--")
                continue
        spans.extend((s + base, e + base) for s, e in found)
        return spans


def _visible_lines(report_text: str) -> list:
    """Split *report_text* into lines, each with its comment-free text."""
    lines = report_text.splitlines()
    code = _code_line_flags(lines)
    masked = "\n".join(" " * len(line) if is_code else line for line, is_code in zip(lines, code))
    pieces = []
    pos = 0
    for start, end in _closed_comment_spans(masked):
        pieces.append(masked[pos:start])
        pieces.append("\n" * masked.count("\n", start, end))
        pos = end
    pieces.append(masked[pos:])
    visible = "".join(pieces).split("\n")
    return [
        _Line(raw=line, text="" if is_code else text, code=is_code)
        for line, text, is_code in zip(lines, visible, code)
    ]


# ---------------------------------------------------------------------------
# Rule 1 and 2: headings
# ---------------------------------------------------------------------------


def _heading(line: _Line) -> "tuple[int, str, bool] | None":
    """``(level, text, opens_finding)`` when *line* is a heading, else None."""
    if line.code:
        return None
    match = _HEADING_RE.match(line.text) or _NOSPACE_FINDING_HEADING_RE.match(line.text)
    if match is None:
        return None
    level = len(match.group(1))
    text = _CLOSING_HASHES_RE.sub("", match.group(2) or "").strip()
    opens = level >= _FINDING_MIN_LEVEL and _FINDING_WORD_RE.match(text) is not None
    return level, text, opens


def _finding_identity(heading_text: str) -> "tuple[Optional[int], Optional[str], str]":
    """``(number, label, title)`` from a finding heading's text (Rule 1)."""
    rest = heading_text[_FINDING_WORD_RE.match(heading_text).end():]
    label = None
    match = _FINDING_LABEL_RE.match(rest)
    if match:
        label = match.group("label")
        rest = rest[match.end():]
    title = _TITLE_SEPARATOR_RE.sub("", rest, count=1).strip().strip("*_").strip()
    digits = _FIRST_DIGITS_RE.match(label) if label else None
    return (int(digits.group(0)) if digits else None), label, title


# ---------------------------------------------------------------------------
# Rule 3, 4 and 5: field lines and values
# ---------------------------------------------------------------------------


def _unquote(value: str) -> str:
    """*value* trimmed, without one pair of surrounding quotes or backticks."""
    value = value.strip()
    if len(value) >= 2 and value[0] in _QUOTE_PAIRS and value[-1] == _QUOTE_PAIRS[value[0]]:
        value = value[1:-1].strip()
    return value


def _split_field_line(text: str) -> "tuple[str, str, bool] | None":
    """``(label, value, bold)`` when *text* is a field line (Rule 3), else None."""
    line = _LIST_MARKER_RE.sub("", text, count=1).strip()
    emphasis = _EMPHASIS_OPEN_RE.match(line)
    if emphasis:
        marker = emphasis.group(1)
        close = line.find(marker, len(marker))
        inner, rest = (line[len(marker):], "") if close == -1 else (line[len(marker):close], line[close + len(marker):])
        if ":" in inner:
            label, value = inner.split(":", 1)
            value += rest
        elif rest.lstrip().startswith(":"):
            label, value = inner, rest.lstrip()[1:]
        else:
            return None
        bold = True
    else:
        if ":" not in line:
            return None
        label, value = line.split(":", 1)
        bold = False
    label = _unquote(label.strip().strip("*_"))
    if not _LABEL_TEXT_RE.match(label):
        return None
    value = _unquote(value.strip().strip("*").strip())
    return label, value, bold


def _normal_label(label: str) -> str:
    """Rule 4: lowercase, each run of spaces, underscores or dashes one space."""
    return re.sub(r"[\s_-]+", " ", label.lower()).strip()


def _extra_key(label: str) -> str:
    """The ``extra`` key for *label*: lowercase, other runs turned into ``_``."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _leading_token(value: str, tokens) -> Optional[str]:
    """The token that leads *value* and stands alone (Rule 4), or None."""
    for token in sorted(tokens, key=len, reverse=True):
        pattern = r"[\s_-]+".join(re.escape(part) for part in token.split("-"))
        if re.match(pattern + r"(?![A-Za-z0-9_-])", value, re.IGNORECASE):
            return token
    return None


def _enum_value(value: str, tokens) -> Optional[str]:
    """Normalise *value* to one of *tokens*, or None (Rule 4)."""
    cleaned = _unquote(value.replace("*", "").replace("`", "")).strip("_ \t")
    parts = _ALTERNATIVES_SPLIT_RE.split(cleaned)
    if len(parts) > 1 and sum(1 for part in parts if _leading_token(part, tokens)) >= 2:
        return None
    return _leading_token(cleaned, tokens)


def _filed_as_id(value: str) -> Optional[str]:
    """The first BUG-/PRIORITY- id in *value*, canonical form, or None."""
    match = _ID_RE.search(value)
    return f"{match.group(1).upper()}-{int(match.group(2)):04d}" if match else None


def _read_fields(finding: Finding, lines: list) -> None:
    """Fill *finding*'s named fields, ``extra`` and ``raw`` from its lines."""
    for line in lines:
        if line.code or _heading(line) is not None:
            continue
        parsed = _split_field_line(line.text)
        if parsed is None:
            continue
        label, value, bold = parsed
        if not value:
            continue
        attr = FIELD_LABELS.get(_normal_label(label))
        if attr is None:
            if bold:
                finding.extra.setdefault(_extra_key(label), value)
            continue
        if attr in finding.raw:
            continue
        finding.raw[attr] = value
        if attr == "filed_as":
            finding.filed_as = _filed_as_id(value)
        elif attr == "filed_by":
            finding.filed_by = value
        else:
            setattr(finding, attr, _enum_value(value, ENUMERATED_FIELDS[attr]))


# ---------------------------------------------------------------------------
# Report-level sections
# ---------------------------------------------------------------------------


def _section_lines(lines: list, matches) -> "tuple[Optional[str], list] | None":
    """``(inline value, body lines)`` of the first level-1/2 section whose
    heading text satisfies *matches*; the body runs to the next heading of
    level 1 or 2. None when there is no such section."""
    for idx, line in enumerate(lines):
        heading = _heading(line)
        if heading is None or heading[0] > _SECTION_MAX_LEVEL:
            continue
        text = heading[1].strip("*_ ")
        if not matches(text):
            continue
        inline = text.split(":", 1)[1].strip() if ":" in text else None
        body = []
        for follow in lines[idx + 1:]:
            nested = _heading(follow)
            if nested is not None and nested[0] <= _SECTION_MAX_LEVEL:
                break
            body.append(follow)
        return inline, body
    return None


def _section_value(lines: list, name: str) -> Optional[str]:
    """The value line of the ``## <name>`` section, as written, or None."""
    prefix = re.compile(re.escape(name) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
    section = _section_lines(lines, prefix.match)
    if section is None:
        return None
    inline, body = section
    if inline:
        return inline
    for line in body:
        text = line.text.strip()
        if line.code or not text or _THEMATIC_BREAK_RE.match(text) or _heading(line) is not None:
            continue
        return text
    return None


def _report_token(raw: Optional[str], tokens) -> Optional[str]:
    """Read a report-level value line whole, then as a field line."""
    if raw is None:
        return None
    token = _enum_value(raw, tokens)
    if token is None:
        field = _split_field_line(raw)
        if field is not None and field[1]:
            token = _enum_value(field[1], tokens)
    return token


def _summary(lines: list) -> Optional[str]:
    section = _section_lines(lines, _SUMMARY_HEADING_RE.match)
    if section is None:
        return None
    inline, body = section
    texts = ([inline] if inline else []) + [
        (line.raw if line.code else line.text).rstrip() for line in body
    ]
    text = "\n".join(texts).strip("\n")
    return text or None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _strip_blank_edges(lines: list) -> list:
    start, end = 0, len(lines)
    while start < end and not lines[start].raw.strip():
        start += 1
    while end > start and not lines[end - 1].raw.strip():
        end -= 1
    return lines[start:end]


def _split_blocks(lines: list) -> "tuple[list, list]":
    """Split *lines* by Rules 1 and 2 into ``(outside, blocks)``: the lines
    belonging to no finding block, and one ``(level, heading text, body
    lines)`` per finding block, in order."""
    outside: list = []
    blocks: list = []
    current = None
    for line in lines:
        heading = _heading(line)
        if heading is not None:
            level, text, opens = heading
            if current is not None and level <= current[0]:
                blocks.append(current)
                current = None
            if current is None and opens:
                current = (level, text, [])
                continue
        if current is not None:
            current[2].append(line)
        else:
            outside.append(line)
    if current is not None:
        blocks.append(current)
    return outside, blocks


def _build_finding(level: int, text: str, body: list) -> Finding:
    """A Finding from one block: fields from its own lines, sub-blocks parsed
    the same way into ``sub_findings``."""
    body = _strip_blank_edges(body)
    own, sub_blocks = _split_blocks(body)
    number, label, title = _finding_identity(text)
    finding = Finding(
        number=number, label=label, title=title, heading=text, level=level,
        body="\n".join(line.raw for line in body),
    )
    _read_fields(finding, own)
    finding.sub_findings = [_build_finding(*block) for block in sub_blocks]
    return finding


def _findings(lines: list) -> list:
    """The report's findings (Rules 1 and 2), each with its fields read."""
    return [_build_finding(*block) for block in _split_blocks(lines)[1]]


def read_report_text(path: "str | os.PathLike[str]") -> str:
    """Return the text of the report at *path* (UTF-8; undecodable bytes are
    replaced, never fatal). Raises OSError when the file cannot be read."""
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def parse_report_text(text: str) -> TesterReport:
    """Parse report *text* into a TesterReport by the module's rules."""
    lines = _visible_lines(text)
    raw = {}
    recommendation_raw = _section_value(lines, "Recommendation")
    systemic_risk_raw = _section_value(lines, "Systemic Risk")
    if recommendation_raw is not None:
        raw["recommendation"] = recommendation_raw
    if systemic_risk_raw is not None:
        raw["systemic_risk"] = systemic_risk_raw
    return TesterReport(
        recommendation=_report_token(recommendation_raw, RECOMMENDATION_TOKENS),
        systemic_risk=_report_token(systemic_risk_raw, _REPORT_SYSTEMIC_RISK_TOKENS),
        summary=_summary(lines),
        findings=_findings(lines),
        raw=raw,
    )


def parse_report(path_or_text: "str | os.PathLike[str]") -> TesterReport:
    """Parse a TESTER report given as a path or as its text.

    A path object (``os.PathLike``) is always read as a path. A ``str`` is
    report text when it contains a newline or is empty, and a path
    otherwise, so a report read into memory and a path on disk need no
    flag; ``parse_report_text`` and ``read_report_text`` are the explicit
    forms. Raises OSError when a path cannot be read and TypeError for any
    other argument type; the parse itself never raises.
    """
    if isinstance(path_or_text, os.PathLike):
        return parse_report_text(read_report_text(path_or_text))
    if isinstance(path_or_text, str):
        if "\n" in path_or_text or not path_or_text:
            return parse_report_text(path_or_text)
        return parse_report_text(read_report_text(path_or_text))
    raise TypeError(
        f"parse_report takes a report path or report text, not {type(path_or_text).__name__}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list | None" = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.reports.tester_report",
        description=(
            "Parse a TESTER verification report and print its recommendation, "
            "systemic risk, summary and findings. Reads only."
        ),
    )
    parser.add_argument(
        "report",
        help='Path to the TESTER report (artifacts/report.md), or "-" to read it from standard input.',
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full parse as JSON (TesterReport.to_json) instead of the plain-text summary.",
    )
    args = parser.parse_args(argv)
    try:
        text = sys.stdin.read() if args.report == "-" else read_report_text(args.report)
    except OSError as exc:
        print(
            f"tester_report: cannot read report {args.report}: {exc.strerror or exc}. "
            f"Pass the path of an existing report file, or - to read standard input.",
            file=sys.stderr,
        )
        return 1
    report = parse_report_text(text)
    sys.stdout.write(report.to_json() if args.json else report.to_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
