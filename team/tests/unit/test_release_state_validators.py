"""
test_release_state_validators.py
=================================
Behavioral unit tests for release-state.md's schema-driven field validator:
``pgai_agent_kanban.cm.read_state_field.RELEASE_STATE_SCHEMA``,
``validate_release_state_fields``, and ``load_release_state``.

Coverage:
  - A well-formed release-state.md (including the legitimate all-'none'
    fresh-project shape, where "Last Released" is entirely absent) loads
    with every field validated and no refusal.
  - Each of the four ways a field can fail — a value the schema does not
    recognise ('NONE' where a literal 'none' or a version is expected), an
    empty value, a missing required heading, a malformed timestamp, and a
    malformed version — produces its own distinguishable refusal, naming
    the value received and the values that would have been accepted, plus
    a runnable remedy.
  - A field's own heading may legitimately hold the literal text 'none';
    that is a value, not an absence, and must not be confused with a
    missing heading or an empty one.
  - The schema is read generically (iterated, not re-enumerated per test),
    so a future field added to RELEASE_STATE_SCHEMA is exercised by the
    existing parametrized tests without editing them.

All tests use tmp_path for scratch -- no bare /tmp paths. Each test is
self-contained; no shared state between tests.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.cm.read_state_field import (
    RELEASE_STATE_SCHEMA,
    FieldKind,
    ReleaseStateValidationError,
    load_release_state,
    validate_release_state_fields,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_release_state(tmp_path: pathlib.Path, headings: dict) -> pathlib.Path:
    """Write a release-state.md at tmp_path with one '## <heading>' block
    per (heading, value) pair in *headings*, in insertion order. A value of
    ``None`` omits the heading entirely (used to simulate a genuinely
    absent field)."""
    lines = ["# Release State", ""]
    for heading, value in headings.items():
        if value is None:
            continue
        lines.append(f"## {heading}")
        lines.append(value)
        lines.append("")
    md = tmp_path / "release-state.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return md


def _fresh_project_headings() -> dict:
    """The legitimate all-'none' shape of a project that has never opened
    an RC and never shipped: every populated field literally holds 'none',
    and the optional 'Last Released' heading does not exist yet."""
    return {
        "Active RC": "none",
        "RC Opened At": "none",
        "RC Opened By Task": "none",
        "Last Released": None,
    }


def _well_formed_headings() -> dict:
    """A release-state.md shape with a real RC in flight and a prior
    release recorded -- every field holds a well-formed, non-'none' value."""
    return {
        "Active RC": "v1.2.3",
        "RC Opened At": "2026-01-01T00:00:00Z",
        "RC Opened By Task": "CM-20260101-001-open-rc",
        "Last Released": "v1.2.2",
    }


# ---------------------------------------------------------------------------
# Well-formed shapes load cleanly
# ---------------------------------------------------------------------------


def test_load_release_state_accepts_fresh_project_all_none_shape(
    tmp_path: pathlib.Path,
) -> None:
    """A never-shipped, never-opened project's release-state.md (three
    populated 'none' fields, 'Last Released' heading absent) loads with
    every field validated and no refusal."""
    md = _write_release_state(tmp_path, _fresh_project_headings())
    result = load_release_state(str(md))
    assert result == {
        "Active RC": "none",
        "RC Opened At": "none",
        "RC Opened By Task": "none",
        "Last Released": "none",
    }


def test_load_release_state_accepts_well_formed_in_flight_shape(
    tmp_path: pathlib.Path,
) -> None:
    """A release-state.md with a real RC in flight and a recorded prior
    release loads every field with its literal value, no refusal."""
    md = _write_release_state(tmp_path, _well_formed_headings())
    result = load_release_state(str(md))
    assert result == _well_formed_headings()


def test_validate_release_state_fields_reports_every_schema_field_ok(
    tmp_path: pathlib.Path,
) -> None:
    """validate_release_state_fields returns one ok FieldResult per schema
    field, in schema order, for a well-formed file."""
    md = _write_release_state(tmp_path, _well_formed_headings())
    results = validate_release_state_fields(str(md))
    assert [r.heading for r in results] == [
        spec.heading for spec in RELEASE_STATE_SCHEMA
    ]
    assert all(r.ok for r in results)


# ---------------------------------------------------------------------------
# 'none' is a value, not an absence
# ---------------------------------------------------------------------------


def test_optional_field_absent_heading_is_treated_as_legitimate_none(
    tmp_path: pathlib.Path,
) -> None:
    """'Last Released' (the one optional schema field) may be entirely
    absent from the file -- that absence is a legitimate 'none', not a
    refusal, distinguishing it from a required field's missing heading."""
    headings = _well_formed_headings()
    headings["Last Released"] = None
    md = _write_release_state(tmp_path, headings)
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    assert results["Last Released"].ok is True
    assert results["Last Released"].value == "none"


# ---------------------------------------------------------------------------
# The four distinguishable refusal shapes
# ---------------------------------------------------------------------------


def test_value_not_matching_schema_refuses_naming_received_value_and_accepted_forms(
    tmp_path: pathlib.Path,
) -> None:
    """An uppercase 'NONE' is not the literal 'none' sentinel and does not
    match the version format either, so it refuses -- naming the value
    received and the forms that would have been accepted."""
    headings = _well_formed_headings()
    headings["Active RC"] = "NONE"
    md = _write_release_state(tmp_path, headings)
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    active_rc = results["Active RC"]
    assert active_rc.ok is False
    assert active_rc.received == "NONE"
    assert "'none' or vX.Y.Z" in active_rc.message
    assert active_rc.remedy
    assert active_rc.error_key == "release-state.malformed-version"


def test_empty_value_refuses_distinguishably_from_missing_heading(
    tmp_path: pathlib.Path,
) -> None:
    """A heading present with no qualifying value (a dangling '## Active
    RC' immediately followed by a horizontal-rule section boundary)
    refuses with its own error key, distinct from a heading that never
    appears at all."""
    md = tmp_path / "release-state.md"
    md.write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "---\n\n"
        "## RC Opened At\n"
        "2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    assert results["Active RC"].ok is False
    assert results["Active RC"].error_key == "release-state.empty-value"


def test_missing_required_heading_refuses_distinguishably_from_empty_value(
    tmp_path: pathlib.Path,
) -> None:
    """A required heading that never appears in the file refuses with its
    own error key, distinct from a heading present but empty."""
    headings = _well_formed_headings()
    headings["RC Opened At"] = None  # required field, heading entirely absent
    md = _write_release_state(tmp_path, headings)
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    assert results["RC Opened At"].ok is False
    assert results["RC Opened At"].error_key == "release-state.missing-heading"


def test_malformed_timestamp_refuses_naming_iso8601_accepted_form(
    tmp_path: pathlib.Path,
) -> None:
    """A value that is neither 'none' nor a well-formed ISO-8601 timestamp
    refuses, naming the ISO-8601 form as an accepted alternative."""
    headings = _well_formed_headings()
    headings["RC Opened At"] = "not-a-timestamp"
    md = _write_release_state(tmp_path, headings)
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    rc_opened_at = results["RC Opened At"]
    assert rc_opened_at.ok is False
    assert rc_opened_at.received == "not-a-timestamp"
    assert "ISO-8601" in rc_opened_at.message
    assert rc_opened_at.remedy
    assert rc_opened_at.error_key == "release-state.malformed-timestamp"


def test_malformed_version_refuses_naming_version_accepted_form(
    tmp_path: pathlib.Path,
) -> None:
    """A value that is neither 'none' nor a well-formed vX.Y.Z version
    refuses, naming the vX.Y.Z form as an accepted alternative."""
    headings = _well_formed_headings()
    headings["Last Released"] = "garbage"
    md = _write_release_state(tmp_path, headings)
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    last_released = results["Last Released"]
    assert last_released.ok is False
    assert last_released.received == "garbage"
    assert "vX.Y.Z" in last_released.message
    assert last_released.remedy
    assert last_released.error_key == "release-state.malformed-version"


def test_four_refusal_kinds_are_pairwise_distinguishable(
    tmp_path: pathlib.Path,
) -> None:
    """The four refusal shapes covered above carry four distinct
    error_key values -- a caller can tell them apart programmatically,
    not only by reading the message text."""
    empty_md = tmp_path / "empty.md"
    empty_md.write_text(
        "## Active RC\n---\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    missing_headings = _well_formed_headings()
    missing_headings["RC Opened At"] = None
    missing_md = _write_release_state(missing_dir, missing_headings)

    bad_timestamp_dir = tmp_path / "bad_timestamp"
    bad_timestamp_dir.mkdir()
    bad_timestamp_headings = _well_formed_headings()
    bad_timestamp_headings["RC Opened At"] = "not-a-timestamp"
    bad_timestamp_md = _write_release_state(bad_timestamp_dir, bad_timestamp_headings)

    bad_version_dir = tmp_path / "bad_version"
    bad_version_dir.mkdir()
    bad_version_headings = _well_formed_headings()
    bad_version_headings["Active RC"] = "garbage"
    bad_version_md = _write_release_state(bad_version_dir, bad_version_headings)

    error_keys = {
        "empty": next(
            r.error_key
            for r in validate_release_state_fields(str(empty_md))
            if r.heading == "Active RC"
        ),
        "missing_heading": next(
            r.error_key
            for r in validate_release_state_fields(str(missing_md))
            if r.heading == "RC Opened At"
        ),
        "malformed_timestamp": next(
            r.error_key
            for r in validate_release_state_fields(str(bad_timestamp_md))
            if r.heading == "RC Opened At"
        ),
        "malformed_version": next(
            r.error_key
            for r in validate_release_state_fields(str(bad_version_md))
            if r.heading == "Active RC"
        ),
    }
    assert len(set(error_keys.values())) == 4, error_keys


# ---------------------------------------------------------------------------
# load_release_state: the strict, refuse-loud loader
# ---------------------------------------------------------------------------


def test_load_release_state_raises_naming_value_and_accepted_forms_for_none_variant(
    tmp_path: pathlib.Path,
) -> None:
    """load_release_state refuses loud (rather than silently returning
    'none') when Active RC holds 'NONE' instead of the literal 'none' --
    the exact defect this validator exists to catch."""
    headings = _well_formed_headings()
    headings["Active RC"] = "NONE"
    md = _write_release_state(tmp_path, headings)
    with pytest.raises(ReleaseStateValidationError) as excinfo:
        load_release_state(str(md))
    message = str(excinfo.value)
    assert "NONE" in message
    assert "vX.Y.Z" in message
    assert "remedy:" in message


def test_load_release_state_raises_on_missing_file(tmp_path: pathlib.Path) -> None:
    """load_release_state refuses loud when the file itself does not
    exist, rather than silently returning an all-'none' dict."""
    missing = tmp_path / "does-not-exist.md"
    with pytest.raises(ReleaseStateValidationError) as excinfo:
        load_release_state(str(missing))
    assert str(missing) in str(excinfo.value)


def test_load_release_state_error_message_is_dual_channel_house_shape(
    tmp_path: pathlib.Path,
) -> None:
    """The raised exception's message carries the three-line house shape
    (error / message / remedy) so any caller that prints it gets a
    stranger-runnable remedy without further formatting."""
    headings = _well_formed_headings()
    headings["Active RC"] = "NONE"
    md = _write_release_state(tmp_path, headings)
    with pytest.raises(ReleaseStateValidationError) as excinfo:
        load_release_state(str(md))
    lines = str(excinfo.value).splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("error:")
    assert lines[1].startswith("message:")
    assert lines[2].startswith("remedy:")


# ---------------------------------------------------------------------------
# Schema-generic coverage: every required field independently produces a
# missing-heading refusal, and every field kind's kind constant is used by
# at least one schema entry -- read generically from RELEASE_STATE_SCHEMA
# rather than re-listing headings, so a future field addition is covered
# by these tests without editing them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [spec for spec in RELEASE_STATE_SCHEMA if spec.required],
    ids=[spec.heading for spec in RELEASE_STATE_SCHEMA if spec.required],
)
def test_required_field_missing_heading_refuses(tmp_path, spec) -> None:
    """Every field the schema marks required refuses when its own heading
    is entirely absent from an otherwise well-formed file."""
    headings = _well_formed_headings()
    headings[spec.heading] = None
    md = _write_release_state(tmp_path, headings)
    results = {r.heading: r for r in validate_release_state_fields(str(md))}
    assert results[spec.heading].ok is False
    assert results[spec.heading].error_key == "release-state.missing-heading"


def test_schema_covers_every_field_kind_at_least_once() -> None:
    """Every FieldKind the validator knows how to check is actually used
    by at least one schema entry -- a kind with zero schema users would be
    untested dead code."""
    kinds_in_schema = {spec.kind for spec in RELEASE_STATE_SCHEMA}
    all_kinds = {FieldKind.VERSION_OR_NONE, FieldKind.TIMESTAMP_OR_NONE, FieldKind.TEXT_OR_NONE}
    assert kinds_in_schema == all_kinds
