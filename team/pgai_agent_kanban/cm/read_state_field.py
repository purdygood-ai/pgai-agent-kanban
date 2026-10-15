#!/usr/bin/env python3
"""
read_state_field.py — Read and validate release-state.md's named fields.

This module is the one reader behind the release-state field reads the
cm/*.sh scripts (team/scripts/cm/release.sh, team/scripts/cm/open-rc.sh,
team/scripts/cm/cancel-rc.sh) and their Python callers share. Its scanning
walk is defined by the three boundary/skip rules named below; change heading
matching or whitespace stripping only together with the tests that pin them.

Section boundaries: a field's section ends at the next heading — any line whose
stripped text starts with '##' — or at a horizontal rule ('---', three or more
dashes on its own line). A heading with no value before that boundary has no
value: it never reads the value of the field after it, and a markdown '---'
rule is never returned as the field value. A single '#' line is a comment and
is skipped, not a boundary.

HTML-comment handling: an HTML comment block (``<!-- ... -->``, on one line or
spanning several) between the heading and its value is skipped exactly as a
``#`` comment line is, and an inline ``<!-- ... -->`` on the value line itself is
removed before the value is taken.  Agent-facing templates carry an "allowed
values" HTML comment directly under a heading; a file that keeps the comment
must still yield the value the author wrote below it, never the comment's own
first line.

A TESTER verification report is not read through this module: its
Recommendation, Systemic Risk and finding fields come from the one report
parser, ``pgai_agent_kanban.reports.tester_report``. This module reads
release-state.md and other ``## <heading>`` state files (a task's README.md
and status.md, for example).

Usage (CLI):
    python3 read_state_field.py <filepath> <heading> [<heading> ...]
    python3 -m pgai_agent_kanban.cm.read_state_field --section <filepath> <heading> [<heading> ...]
    python3 -m pgai_agent_kanban.cm.read_state_field --validate <filepath>

    Several headings print one value per line, in the order named, so a
    caller that needs more than one field pays for one process launch.

Usage (import):
    from pgai_agent_kanban.cm.read_state_field import read_state_field
    value = read_state_field("team/release-state.md", "Active RC")

    from pgai_agent_kanban.cm.read_state_field import read_release_state_field
    value = read_release_state_field("team/release-state.md", "Active RC")

    from pgai_agent_kanban.cm.read_state_field import read_state_section
    value = read_state_section("team/release-state.md", "Active RC")  # None, "" or the value

Both forms return/print the first non-blank, non-comment line after the
"## <heading>" marker in the file, or the literal string "none" when:
  - the file does not exist, or
  - the heading is not found, or
  - no qualifying value follows the heading before the next heading, a
    horizontal rule, or EOF.

Wrap, don't widen the read — widen the module instead
------------------------------------------------------
``read_state_field()`` collapses three distinct situations (file missing,
heading missing, heading present with no value) into the same "none" sentinel.
That collapse is a deliberate, load-bearing contract: ``cm/open-rc.sh``,
``cm/cancel-rc.sh``, ``lib/cm/release_preflight.sh``, and
``pgai_agent_kanban.ops.read.get_release_state`` all call it today and rely on
"none" meaning "nothing to see here" without needing to distinguish why.
Changing that return contract would silently break every one of those
callers, which is exactly the kind of regression this module's own docstring
already warns against.

So this module is **widened, not wrapped**: the scanning walk is factored
into one private helper (``_scan_field_raw``) that ``read_state_field()``,
``read_state_section()`` and the validating API below all call, and a schema-
driven validating layer is added beside the raw reader rather than folded
into it. ``read_state_field()`` keeps its "none" collapse; the validating
API is what distinguishes the three collapsed cases:

  - ``RELEASE_STATE_SCHEMA`` — the single place that names every field
    release-state.md carries, what values each accepts, and whether the
    heading itself may legitimately be absent. Add a field here once; both
    the validator and its tests read this same list.
  - ``validate_release_state_fields(filepath)`` — validates every schema
    field independently and returns one ``FieldResult`` per field (ok or
    refused, with the value it got and the values it would have accepted).
    This is what a caller wanting a per-field OK/ERROR breakdown uses (e.g.
    team/scripts/verify-rc-state.sh's release-state check, invoked via this
    module's ``--validate`` CLI flag).
  - ``load_release_state(filepath)`` — the strict loader: calls the above and
    refuses loud (raising ``ReleaseStateValidationError``, whose message is
    the shared dual-channel error-contract block) when any field fails.
    This is the "loader" a Python caller uses when it needs every field
    validated or nothing at all.
  - ``read_release_state_field(filepath, heading)`` — one field through the
    same loader: validated and refused loud like ``load_release_state``, but
    only for the field asked for, and a missing file reads as ``none``. This
    is what a Python caller that needs a single field (Active RC, Last
    Released) uses instead of parsing release-state.md itself.

'none' is a value, not an absence
----------------------------------
A field whose heading is present and holds the literal text ``none`` is
valid and distinct from a field whose heading is missing, or present but
empty. The validating API below tells these apart: it reports "missing
heading" and "empty value" as their own refusals, never silently folding
them into a value of ``none`` the way ``read_state_field()`` does for its
existing callers, and never into the value of the field that follows.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Raw scanning — shared by read_state_field(), read_state_section() and the
# validating API below, so they can never disagree about what counts as
# "a value follows this heading".
# ---------------------------------------------------------------------------

# Sentinels distinguishing "heading not found at all" from "heading found but
# no qualifying value follows it before the next boundary". Neither is the
# string "none" — that string is reserved for a legitimate field value.
_ABSENT = object()
_EMPTY = object()

# HTML comment delimiters. A block opening on one line and closing on a later
# one is skipped as a unit; a complete "<!-- ... -->" inside a single line is
# removed from that line before its remainder is considered as a value.
_HTML_COMMENT_OPEN = "<!--"
_HTML_COMMENT_CLOSE = "-->"
_HTML_COMMENT_INLINE_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _is_horizontal_rule(line: str) -> bool:
    """Return True when *line* is a markdown horizontal rule (three or more dashes).

    Matches a stripped line composed entirely of three or more dash characters,
    optionally interspersed with spaces (CommonMark HR syntax).  Used by
    read_state_field to treat '---' as a section boundary.
    """
    return bool(re.fullmatch(r"-{3,}", line))


def _scan_field_raw(text: str, heading: str):
    """Scan *text* for "## <heading>" and return its raw result.

    Returns one of:
      - the extracted value string (stripped), which may legitimately be the
        literal text "none";
      - the module sentinel ``_EMPTY`` when the heading is found but no
        qualifying non-blank, non-comment line follows it before the next
        heading (a line whose stripped text starts with ``##``), a
        horizontal rule, or EOF — a "comment" here is a single-``#`` line or
        any part of an HTML ``<!-- ... -->`` block;
      - the module sentinel ``_ABSENT`` when "## <heading>" is never found.

    Every reader in this module calls this one walk, so the validating API
    can tell "no heading" apart from "heading with no value" apart from
    "heading holding the literal value 'none'" — the distinction
    ``read_state_field()``'s own collapsed "none" sentinel cannot express —
    and no reader ever takes a value from the section after the field's own.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            in_html_comment = False
            for follow in lines[i + 1:]:
                v = follow.strip()
                # An HTML comment block is skipped whole, however many lines
                # it spans; an inline "<!-- ... -->" is dropped from a value
                # line so the text beside it is still read as the value.
                if in_html_comment:
                    if _HTML_COMMENT_CLOSE in v:
                        in_html_comment = False
                        v = v.split(_HTML_COMMENT_CLOSE, 1)[1].strip()
                    else:
                        continue
                v = _HTML_COMMENT_INLINE_RE.sub("", v).strip()
                if v.startswith(_HTML_COMMENT_OPEN):
                    in_html_comment = True
                    continue
                # A horizontal rule ('---', three or more dashes) is treated as
                # a section boundary.  Stop here without returning a
                # value rather than returning '---' as the field value.
                if v and _is_horizontal_rule(v):
                    break
                # The next '##' heading (any depth) ends the section, so an
                # empty field never reads the value of the field after it.
                if v.startswith("##"):
                    break
                if v and not v.startswith("#"):
                    return v
            return _EMPTY
    return _ABSENT


def read_state_field(filepath: str, heading: str) -> str:
    """Read a named field from a markdown state file.

    Finds the line "## <heading>" in the file at *filepath*, then returns the
    first non-blank, non-comment line that follows it.  Returns the literal
    string ``"none"`` when the file does not exist, the heading is absent, or
    no qualifying value line exists after the heading.

    Section boundaries: scanning stops (without returning a value) at the
    next ``##`` heading or a horizontal rule (``---``, three or more dashes)
    met before a qualifying value, so a heading with no value reads as
    ``"none"`` — never as the next field's value — and a markdown horizontal
    rule is never returned as the field value.  Single-``#`` comment lines
    and HTML comment blocks (``<!-- ... -->``, single- or multi-line) are
    skipped, not treated as boundaries, so a template's "allowed values"
    comment under a heading never becomes the value.

    The walk is shared with ``read_state_section``; the two differ only in
    that this one folds the absent and empty cases into ``"none"``.

    Args:
        filepath: Path to the markdown file (string or path-like).
        heading:  The heading name to search for (without the leading "## ").

    Returns:
        The field value as a stripped string, or ``"none"``.
    """
    p = pathlib.Path(filepath)
    if not p.exists():
        return "none"
    text = p.read_text(encoding="utf-8")
    result = _scan_field_raw(text, heading)
    if result is _ABSENT or result is _EMPTY:
        return "none"
    return result


def read_state_section(filepath: str, heading: str) -> Optional[str]:
    """Read a named field as a section that ends at the next ``##`` heading.

    The same walk as ``read_state_field`` (blank, ``#`` comment and HTML
    comment lines skipped; the next ``##`` heading or a horizontal rule ends
    the section, so a field with no value never reads the value of the
    field after it), except that the three cases ``read_state_field`` folds
    into ``"none"`` stay apart:

    Returns:
        ``None`` when the file does not exist or the heading is absent;
        ``""`` when the heading is present with no value before the next
        ``##`` heading, a horizontal rule, or EOF; otherwise the value as a
        stripped string (which may be the literal ``"none"``).
    """
    p = pathlib.Path(filepath)
    if not p.exists():
        return None
    result = _scan_field_raw(p.read_text(encoding="utf-8"), heading)
    if result is _ABSENT:
        return None
    if result is _EMPTY:
        return ""
    return result


# ---------------------------------------------------------------------------
# Schema — the single place release-state.md's fields, their accepted
# values, and their heading-presence requirements are named. Both
# ``validate_release_state_fields`` and its tests read this list; adding a
# field to the schema is one edit here.
# ---------------------------------------------------------------------------


class FieldKind:
    """The accepted-value shapes a release-state.md field can declare."""

    VERSION_OR_NONE = "version_or_none"
    TIMESTAMP_OR_NONE = "timestamp_or_none"
    TEXT_OR_NONE = "text_or_none"


# Human-readable description of each kind's accepted forms, used verbatim in
# refusal messages so "the values it would have accepted" is always named.
_ACCEPTED_FORMS = {
    FieldKind.VERSION_OR_NONE: "'none' or vX.Y.Z",
    FieldKind.TIMESTAMP_OR_NONE: (
        "'none' or an ISO-8601 timestamp "
        "(YYYY-MM-DDThh:mm:ss, optionally followed by +HH:MM, -HH:MM, or Z)"
    ),
    FieldKind.TEXT_OR_NONE: "'none' or non-empty text",
}

# Format regexes for the non-"none" branch of each kind. Deliberately
# format-only: version *comparison* stays in team/scripts/lib/semver.sh and
# pgai_agent_kanban/lib/semver.py, which this module does not duplicate.
_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)?$"
)


@dataclasses.dataclass(frozen=True)
class FieldSpec:
    """One release-state.md field's schema entry.

    Attributes:
        heading:  Heading name, without the leading "## " (e.g. "Active RC").
        kind:     One of the FieldKind values, naming the accepted-value shape.
        required: When True, the heading itself must be present in the file
                   (its own value may still legitimately be "none"). When
                   False, the heading may be entirely absent — that absence
                   is treated as equivalent to a legitimate "none" value
                   (the shipped shape of "Last Released" before a project's
                   first release: the field does not exist yet).
    """

    heading: str
    kind: str
    required: bool


# The canonical release-state.md schema: exactly the four fields
# team/roles/CM.md documents. "Last Released At" and "Last Released By Task"
# are retired — they are not part of the schema and are not validated.
RELEASE_STATE_SCHEMA: tuple = (
    FieldSpec("Active RC", FieldKind.VERSION_OR_NONE, required=True),
    FieldSpec("RC Opened At", FieldKind.TIMESTAMP_OR_NONE, required=True),
    FieldSpec("RC Opened By Task", FieldKind.TEXT_OR_NONE, required=True),
    FieldSpec("Last Released", FieldKind.VERSION_OR_NONE, required=False),
)


def _value_matches_kind(kind: str, value: str) -> bool:
    """Return True when *value* (never "none" — checked by the caller first)
    matches the accepted format for *kind*."""
    if kind == FieldKind.VERSION_OR_NONE:
        return bool(_VERSION_RE.match(value))
    if kind == FieldKind.TIMESTAMP_OR_NONE:
        return bool(_TIMESTAMP_RE.match(value))
    if kind == FieldKind.TEXT_OR_NONE:
        return bool(value)
    raise ValueError(f"unknown FieldKind: {kind!r}")


@dataclasses.dataclass(frozen=True)
class FieldResult:
    """The validation outcome for one release-state.md field.

    Attributes:
        heading:   The field's heading name.
        ok:        True when the field validated.
        value:     The validated value (including a legitimate "none") when
                   ``ok`` is True; ``None`` otherwise.
        received:  The raw value the file held, when there was one to show
                   (None for a missing heading or an absent-but-optional
                   field); used in refusal messages so "the value it got"
                   is always named.
        error_key: A stable, refusal-kind-specific string when not ``ok``
                   (e.g. "release-state.malformed-version"); ``None`` when ok.
        message:   Plain-language description of the refusal; ``None`` when ok.
        remedy:    A runnable remedy for the refusal; ``None`` when ok.
    """

    heading: str
    ok: bool
    value: Optional[str] = None
    received: Optional[str] = None
    error_key: Optional[str] = None
    message: Optional[str] = None
    remedy: Optional[str] = None


def _validate_field(text: str, spec: FieldSpec) -> FieldResult:
    """Validate one schema field against the already-read file *text*."""
    raw = _scan_field_raw(text, spec.heading)

    if raw is _ABSENT:
        if not spec.required:
            # e.g. "Last Released" before a project's first release: the
            # heading legitimately does not exist yet.
            return FieldResult(spec.heading, ok=True, value="none")
        accepted = _ACCEPTED_FORMS[spec.kind]
        return FieldResult(
            spec.heading,
            ok=False,
            error_key="release-state.missing-heading",
            message=(
                f"'## {spec.heading}' heading is missing from release-state.md "
                f"(expected one of: {accepted})"
            ),
            remedy=(
                f"add a '## {spec.heading}' heading to release-state.md with "
                f"a value of {accepted}"
            ),
        )

    if raw is _EMPTY:
        accepted = _ACCEPTED_FORMS[spec.kind]
        return FieldResult(
            spec.heading,
            ok=False,
            error_key="release-state.empty-value",
            message=(
                f"'## {spec.heading}' has no value in release-state.md "
                f"(expected one of: {accepted})"
            ),
            remedy=f"set '## {spec.heading}' to one of: {accepted}",
        )

    # raw is the extracted value string, which may legitimately be "none".
    if raw == "none" or _value_matches_kind(spec.kind, raw):
        return FieldResult(spec.heading, ok=True, value=raw, received=raw)

    accepted = _ACCEPTED_FORMS[spec.kind]
    if spec.kind == FieldKind.VERSION_OR_NONE:
        error_key = "release-state.malformed-version"
    elif spec.kind == FieldKind.TIMESTAMP_OR_NONE:
        error_key = "release-state.malformed-timestamp"
    else:
        error_key = "release-state.malformed-value"
    return FieldResult(
        spec.heading,
        ok=False,
        received=raw,
        error_key=error_key,
        message=(
            f"'## {spec.heading}' is {raw!r}, which is not one of: {accepted}"
        ),
        remedy=f"set '## {spec.heading}' to one of: {accepted}, then re-run",
    )


def _schema_subset(headings) -> tuple:
    """Return the ``RELEASE_STATE_SCHEMA`` entries named by *headings*.

    ``None`` selects the whole schema. Each name must be a schema heading
    (without the leading "## "); an unknown name is a caller bug and raises
    ``ValueError`` naming the headings the schema does carry.
    """
    if headings is None:
        return RELEASE_STATE_SCHEMA
    by_heading = {spec.heading: spec for spec in RELEASE_STATE_SCHEMA}
    unknown = [h for h in headings if h not in by_heading]
    if unknown:
        known = ", ".join(by_heading)
        raise ValueError(
            f"not a release-state.md schema heading: {', '.join(unknown)} "
            f"(schema headings: {known})"
        )
    return tuple(by_heading[h] for h in headings)


def validate_release_state_fields(filepath: str, headings=None) -> list:
    """Validate ``RELEASE_STATE_SCHEMA`` fields in *filepath*.

    Args:
        filepath: Path to a release-state.md file.
        headings: Optional iterable of schema headings to validate (e.g.
                  ``("Active RC",)``). ``None`` validates every schema field.

    Returns:
        A list of one ``FieldResult`` per selected schema field, in the
        order selected (schema order by default). When the file itself does
        not exist, returns a single-element list carrying that refusal
        instead of iterating the schema (there is nothing to read fields
        from).

    Raises:
        ValueError: when *headings* names a heading the schema does not carry.
    """
    specs = _schema_subset(headings)
    p = pathlib.Path(filepath)
    if not p.exists():
        headings = ", ".join(spec.heading for spec in RELEASE_STATE_SCHEMA)
        return [
            FieldResult(
                "(file)",
                ok=False,
                error_key="release-state.file-missing",
                message=f"release-state.md not found: {filepath}",
                remedy=(
                    f"create {filepath} with the schema headings: {headings}"
                ),
            )
        ]
    text = p.read_text(encoding="utf-8")
    return [_validate_field(text, spec) for spec in specs]


def _failure_label(result: FieldResult) -> str:
    """Name one refused field for the loader's message: what it held and why.

    A value the file held is shown quoted; a present heading with no value
    reads ``<empty>`` and an absent heading ``<missing>``, so the two faults
    never look alike. The field's own refusal key follows in brackets, e.g.
    ``Active RC=<empty> [release-state.empty-value]``.
    """
    if result.received is not None:
        shown = repr(result.received)
    elif result.error_key == "release-state.empty-value":
        shown = "<empty>"
    else:
        shown = "<missing>"
    return f"{result.heading}={shown} [{result.error_key}]"


class ReleaseStateValidationError(ValueError):
    """Raised by load_release_state when one or more fields fail validation.

    ``str(exc)`` is the shared dual-channel house-shape block (error /
    message / remedy) from ``pgai_agent_kanban.lib.error_contract``, so any
    caller that prints the exception gets a stranger-runnable remedy without
    further formatting.
    """


def load_release_state(filepath: str, headings=None) -> dict:
    """The validating loader: read and validate release-state.md fields.

    Calls ``validate_release_state_fields`` and, when every selected field
    validates, returns a ``{heading: value}`` dict (including legitimate
    "none" values). When any selected field fails, refuses loud rather than
    returning a partial or guessed result.

    Args:
        filepath: Path to a release-state.md file.
        headings: Optional iterable of schema headings to load. ``None``
                  (the default) loads and validates every schema field; a
                  subset validates only those fields, so a caller that needs
                  one field is not refused over another it never reads.

    Returns:
        Dict mapping each selected schema heading to its validated value.

    Raises:
        ReleaseStateValidationError: when one or more fields fail validation.
            The message names every failing field's received value and the
            values it would have accepted, plus a runnable remedy, via the
            shared dual-channel error-contract format.
    """
    results = validate_release_state_fields(filepath, headings)
    failures = [r for r in results if not r.ok]
    if failures:
        from pgai_agent_kanban.lib.error_contract import format_error_contract

        received_bits = "; ".join(_failure_label(r) for r in failures)
        remedy_bits = "; ".join(f"{r.heading}: {r.remedy}" for r in failures)
        contract = format_error_contract(
            "release-state.invalid-field",
            f"release-state.md failed validation ({received_bits}) "
            f"— file: {filepath}",
            remedy_bits,
        )
        # BLOCK-WORTHY: a malformed release-state.md field is a schema
        # invariant violation the running chain cannot safely interpret (a
        # field meant to gate RC-in-flight decisions could silently misread
        # as something else) -- refuse loud rather than let the loop guess
        # at what an unrecognised value means.
        # CENSUS-ANCHOR: release-state-validators-01
        raise ReleaseStateValidationError(contract)
    return {r.heading: r.value for r in results}


def read_release_state_field(filepath: str, heading: str) -> str:
    """Read one release-state.md schema field, validated against the schema.

    The single-field form of ``load_release_state``: the value is read by the
    same scanning walk as ``read_state_field`` and validated exactly as the
    loader validates it, so a heading that is missing, empty, or holds a
    value outside its accepted forms (``NONE``, ``1.2.3``, a stray word) is
    refused loud instead of being passed on as if it were a version. A
    heading with no value before the next ``##`` heading is refused as
    ``release-state.empty-value``; it never reads as the next field's value.

    A missing *file* is not refused: it reads as ``"none"``, the same answer
    ``read_state_field`` gives, because a project that has never opened an RC
    has no release-state.md and that is a normal state for every reader of a
    single field. A caller that needs the file itself to exist uses
    ``load_release_state``.

    Args:
        filepath: Path to a release-state.md file.
        heading:  A ``RELEASE_STATE_SCHEMA`` heading, without the leading
                  "## " (e.g. "Active RC", "Last Released").

    Returns:
        The validated value — ``"none"`` or a value of the field's accepted
        form (e.g. ``"v1.2.3"`` for a version field).

    Raises:
        ReleaseStateValidationError: when the field fails validation; the
            message is the dual-channel error-contract block.
        ValueError: when *heading* is not a schema heading.
    """
    _schema_subset((heading,))
    if not pathlib.Path(filepath).exists():
        return "none"
    return load_release_state(filepath, (heading,))[heading]


def main() -> None:
    """CLI entry point.

    Three modes:
      python3 read_state_field.py <filepath> <heading> [<heading> ...]
          Print the value of the first non-blank, non-comment line after
          each '## <heading>' marker and before the next '##' heading, one
          line per heading in the order named, or 'none' when the file is
          missing, the heading is not found, or it has no value
          (read_state_field's raw, sentinel-collapsing contract).

      python3 -m pgai_agent_kanban.cm.read_state_field --section <filepath> <heading> [<heading> ...]
          Read each heading as a section that ends at the next '##'
          heading (read_state_section). Prints one line per heading: the
          value, or an empty line when the heading has no value or is
          absent. Exits 1 with no output when the file does not exist, and
          exits 1 after printing every line when any heading is absent;
          exits 0 otherwise.

      python3 -m pgai_agent_kanban.cm.read_state_field --validate <filepath>
          Validate every RELEASE_STATE_SCHEMA field in <filepath>. Prints one
          tab-separated line per field: "<heading>\\tOK\\t<value>" or
          "<heading>\\tERROR\\t<message> — remedy: <remedy>". Exits 1 if any
          field failed validation, 0 otherwise.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Read a named heading field from a markdown state file, or "
            "validate every release-state.md schema field with --validate."
        ),
    )
    parser.add_argument(
        "filepath",
        help="Path to the markdown file to read or validate.",
    )
    parser.add_argument(
        "heading",
        nargs="*",
        help=(
            "The heading name(s) to look up (without the leading '## '). "
            "Example: 'Active RC' matches the line '## Active RC'. Several "
            "headings print one value per line, in the order named. "
            "Omit when using --validate."
        ),
    )
    parser.add_argument(
        "--section",
        action="store_true",
        help=(
            "Read each heading as a section that ends at the next '##' "
            "heading: print its value, or an empty line when it has no "
            "value or is absent. Exit 1 when the file is missing (nothing "
            "printed) or any heading is absent."
        ),
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Validate every RELEASE_STATE_SCHEMA field in filepath instead "
            "of reading a single heading."
        ),
    )
    args = parser.parse_args()

    if args.validate:
        results = validate_release_state_fields(args.filepath)
        any_failed = False
        for r in results:
            if r.ok:
                print(f"{r.heading}\tOK\t{r.value}")
            else:
                any_failed = True
                print(f"{r.heading}\tERROR\t{r.message} — remedy: {r.remedy}")
        sys.exit(1 if any_failed else 0)

    if not args.heading:
        parser.error("heading is required unless --validate is given")

    if args.section:
        if not pathlib.Path(args.filepath).exists():
            sys.exit(1)
        values = [read_state_section(args.filepath, h) for h in args.heading]
        for value in values:
            print(value or "")
        sys.exit(1 if any(value is None for value in values) else 0)

    for heading in args.heading:
        print(read_state_field(args.filepath, heading))


if __name__ == "__main__":
    main()
