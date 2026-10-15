"""
test_release_state_readers_shared.py
====================================
Behaviour-preservation fixtures for the Python readers of release-state.md
fields that now read through ``pgai_agent_kanban.cm.read_state_field``.

Each reader below used to parse release-state.md itself (a regex or a line
walk of its own). The fixtures feed every one of them the release-state.md
shapes CM's writer produces and pin the value each returns -- the same value
its earlier private parser returned on that file:

  * ``pgai_agent_kanban.board.get_active_rc_for_project`` /
    ``get_last_released_for_project`` (the board and ``/projects``);
  * ``scan_attention._read_active_rc`` (the OVERWATCH Decision staleness
    guard);
  * ``halt_after.rc_gate.rc_is_open`` and ``halt_after.drain.check_drain``
    (HALT-AFTER drain and RC-close gate);
  * ``pm_materialize.get_active_rc`` (feature-workflow source branch).

A second group pins what changes on a malformed file now that the loader's
schema validation applies: a value the schema refuses reads as blank on the
display surfaces and refuses loud in pm_materialize. A final check scans the
Python tree so a new private release-state.md parser is caught.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import pathlib

import pytest

from pgai_agent_kanban.board import classifier as board_classifier
from pgai_agent_kanban.cm.read_state_field import (
    ReleaseStateValidationError,
    load_release_state,
    read_release_state_field,
    validate_release_state_fields,
)
from pgai_agent_kanban.dashboard import scan_attention
from pgai_agent_kanban.halt_after import drain, rc_gate

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/


def _load_script_module(name: str, path: pathlib.Path):
    """Import a script that is not on a package path, from its own file."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_pm = _load_script_module(
    "pm_materialize_release_state_readers", _TEAM_DIR / "pm-agent" / "pm_materialize.py"
)


# ---------------------------------------------------------------------------
# Fixture release-state.md shapes (as CM's writer and its event appenders
# leave them) and the value every migrated reader returns for each.
# ---------------------------------------------------------------------------

_IDLE = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nv2.27.3\n"
)
_RC_OPEN = (
    "# Release State\n\n## Active RC\nv2.28.0\n\n## RC Opened At\n"
    "2026-10-03T12:00:00Z\n\n## RC Opened By Task\nCM-20261003-056-open-rc\n\n"
    "## Last Released\nv2.27.3\n"
)
_NO_LAST_RELEASED = (
    "# Release State\n\n## Active RC\nv1.0.0\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n"
)
_ONLY_ACTIVE_RC = "# Release State\n\n## Active RC\nnone\n"
_RETIRED_HEADINGS_RC_OPEN = (
    "# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nv1.1.0\n\n"
    "## Last Released At\n2026-01-01T00:00:00Z\n\n## Last Released By Task\nCM-old\n"
)
_RETIRED_HEADINGS_ONLY = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released At\n2026-01-01T00:00:00Z\n\n"
    "## Last Released By Task\nCM-old\n"
)
_ZERO_LAST_RELEASED = "## Active RC\nnone\n\n## Last Released\nv0.0.0\n"
_BLANK_LINES = "## Active RC\n\n\n   v3.1.4   \n\n## Last Released\n\n\nv3.1.3\n"
_EVENTS_APPENDED = (
    "## Active RC\nv2.0.0\n\n## Last Released\nv1.9.9\n\n"
    "## OVERWATCH Decision\nVersion: v2.0.0\n"
)

# (fixture text or None for "no release-state.md", expected values)
# Expected keys: board active, board last, attention active, rc_is_open,
# check_drain("rc:v1.0.0"), pm_materialize active.
_CASES = {
    "missing-file": (None, ("", "", "", False, False, None)),
    "idle": (_IDLE, ("", "v2.27.3", "none", False, True, None)),
    "rc-open": (_RC_OPEN, ("v2.28.0", "v2.27.3", "v2.28.0", True, True, "v2.28.0")),
    "no-last-released": (_NO_LAST_RELEASED, ("v1.0.0", "", "v1.0.0", True, False, "v1.0.0")),
    "only-active-rc": (_ONLY_ACTIVE_RC, ("", "", "none", False, False, None)),
    "zero-last-released": (_ZERO_LAST_RELEASED, ("", "", "none", False, False, None)),
    "blank-lines": (_BLANK_LINES, ("v3.1.4", "v3.1.3", "v3.1.4", True, True, "v3.1.4")),
    "events-appended": (_EVENTS_APPENDED, ("v2.0.0", "v1.9.9", "v2.0.0", True, True, "v2.0.0")),
    "retired-headings-rc-open": (_RETIRED_HEADINGS_RC_OPEN, ("v1.2.0", "v1.1.0", "v1.2.0", True, True, "v1.2.0")),
    "retired-headings-only": (_RETIRED_HEADINGS_ONLY, ("", "", "none", False, False, None)),
}


def _project(tmp_path: pathlib.Path, text: "str | None") -> pathlib.Path:
    """A project directory holding *text* as release-state.md (or none)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    if text is not None:
        (proj / "release-state.md").write_text(text, encoding="utf-8")
    return proj


@pytest.mark.parametrize("case", sorted(_CASES))
def test_migrated_readers_return_the_values_their_private_parsers_returned(
    tmp_path: pathlib.Path, case: str
) -> None:
    """Every migrated reader returns the same value on each fixture shape."""
    text, expected = _CASES[case]
    proj = _project(tmp_path, text)
    actual = (
        board_classifier.get_active_rc_for_project(proj),
        board_classifier.get_last_released_for_project(proj),
        scan_attention._read_active_rc(proj / "release-state.md"),
        rc_gate.rc_is_open(proj),
        drain.check_drain("rc:v1.0.0", proj),
        _pm.get_active_rc(str(proj)),
    )
    assert actual == expected


def test_heading_with_trailing_whitespace_reads_its_value_for_attention(
    tmp_path: pathlib.Path,
) -> None:
    """A '## Active RC' heading carrying trailing spaces still reads its value.

    The attention scanner's earlier regex demanded the heading end the line
    exactly and read such a file as blank; the shared reader strips the
    heading line, as the board and halt_after readers already did.
    """
    proj = _project(tmp_path, "## Active RC   \nv1.2.3\n")
    assert scan_attention._read_active_rc(proj / "release-state.md") == "v1.2.3"
    assert board_classifier.get_active_rc_for_project(proj) == "v1.2.3"


# ---------------------------------------------------------------------------
# The single-field validating reader itself.
# ---------------------------------------------------------------------------


def test_read_release_state_field_reads_missing_file_as_none(tmp_path: pathlib.Path) -> None:
    """No release-state.md is a project that never opened an RC: 'none'."""
    assert read_release_state_field(str(tmp_path / "release-state.md"), "Active RC") == "none"


def test_read_release_state_field_absent_optional_heading_is_none(tmp_path: pathlib.Path) -> None:
    """'Last Released' may be absent before the first release; it reads 'none'."""
    proj = _project(tmp_path, _NO_LAST_RELEASED)
    assert read_release_state_field(str(proj / "release-state.md"), "Last Released") == "none"


@pytest.mark.parametrize(
    "text, error_key",
    [
        ("## Active RC\nNONE\n", "release-state.malformed-version"),
        ("## Active RC\nv5.0.0 (opened)\n", "release-state.malformed-version"),
        ("# Release State\n", "release-state.missing-heading"),
        ("## Active RC\n---\n", "release-state.empty-value"),
    ],
)
def test_read_release_state_field_refuses_values_the_schema_does_not_accept(
    tmp_path: pathlib.Path, text: str, error_key: str
) -> None:
    """A refused field raises with the dual-channel contract naming the fault."""
    proj = _project(tmp_path, text)
    with pytest.raises(ReleaseStateValidationError) as excinfo:
        read_release_state_field(str(proj / "release-state.md"), "Active RC")
    message = str(excinfo.value)
    assert "release-state.invalid-field" in message
    assert "remedy" in message
    (result,) = validate_release_state_fields(str(proj / "release-state.md"), ("Active RC",))
    assert result.error_key == error_key


def test_read_release_state_field_rejects_a_heading_outside_the_schema(
    tmp_path: pathlib.Path,
) -> None:
    """Only the four schema headings are release-state fields."""
    proj = _project(tmp_path, _RC_OPEN)
    with pytest.raises(ValueError, match="schema headings"):
        read_release_state_field(str(proj / "release-state.md"), "Last Released At")


def test_load_release_state_subset_ignores_fields_it_was_not_asked_for(
    tmp_path: pathlib.Path,
) -> None:
    """A malformed field outside the requested subset does not refuse it."""
    proj = _project(
        tmp_path,
        "## Active RC\nv1.0.0\n\n## RC Opened At\nyesterday\n\n## RC Opened By Task\nnone\n",
    )
    path = str(proj / "release-state.md")
    assert load_release_state(path, ("Active RC",)) == {"Active RC": "v1.0.0"}
    with pytest.raises(ReleaseStateValidationError):
        load_release_state(path)


# ---------------------------------------------------------------------------
# Outcomes on a malformed file now that schema validation applies.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "## Active RC\nNONE\n\n## Last Released\nNONE\n",
        "## Active RC\nv5.0.0 (opened)\n\n## Last Released\n4.9.0\n",
        "# Release State\n",
    ],
)
def test_display_readers_show_a_refused_field_as_blank(
    tmp_path: pathlib.Path, text: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The board, /projects and attention scanner show a refused field as ''."""
    proj = _project(tmp_path, text)
    with caplog.at_level(logging.WARNING, logger=board_classifier.__name__):
        assert board_classifier.get_active_rc_for_project(proj) == ""
        assert board_classifier.get_last_released_for_project(proj) == ""
    assert scan_attention._read_active_rc(proj / "release-state.md") == ""
    assert "release-state.invalid-field" in caplog.text


def test_pm_materialize_refuses_a_malformed_active_rc(tmp_path: pathlib.Path) -> None:
    """A source branch is never named from an Active RC that is not a version."""
    proj = _project(tmp_path, "## Active RC\nNONE\n")
    with pytest.raises(ReleaseStateValidationError):
        _pm.get_active_rc(str(proj))


def test_halt_after_keeps_accepting_an_rc_prefixed_active_rc(tmp_path: pathlib.Path) -> None:
    """halt_after reads the raw field: an 'rc/'-prefixed value is still an open RC."""
    proj = _project(tmp_path, "## Active RC\nrc/v0.55.0\n")
    assert rc_gate.rc_is_open(proj) is True


# ---------------------------------------------------------------------------
# No Python module outside the reader parses release-state.md fields.
# ---------------------------------------------------------------------------

_RELEASE_STATE_HEADINGS = ("Active RC", "Last Released", "RC Opened At", "RC Opened By Task")
# The one reader and the one writer (which rewrites the four headings in place).
_OWNERS = {
    _TEAM_DIR / "pgai_agent_kanban" / "cm" / "read_state_field.py",
    _TEAM_DIR / "pgai_agent_kanban" / "cm" / "write_rc_state.py",
}


def _code_string_constants(tree: ast.AST):
    """Yield every str constant in *tree* that is not a bare docstring/expression."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            docstrings.add(id(node.value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node


def _python_sources():
    for root in ("pgai_agent_kanban", "pm-agent", "scripts", "halt_after"):
        for path in sorted((_TEAM_DIR / root).rglob("*.py")):
            parts = set(path.relative_to(_TEAM_DIR).parts)
            if "tests" in parts or path.name.startswith("test_"):
                continue
            yield path


def test_no_module_besides_the_reader_matches_a_release_state_heading() -> None:
    """A '## <release-state heading>' string or regex lives only in the owners.

    Reading one of the four fields by name -- read_state_field(path,
    "Active RC") -- passes; a module building its own '## Active RC' match
    does not.
    """
    offenders = []
    for path in _python_sources():
        if path in _OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _code_string_constants(tree):
            text = node.value
            if "##" in text and any(h in text for h in _RELEASE_STATE_HEADINGS):
                offenders.append(f"{path.relative_to(_TEAM_DIR)}:{node.lineno}: {text!r}")
    assert offenders == [], "\n".join(offenders)
