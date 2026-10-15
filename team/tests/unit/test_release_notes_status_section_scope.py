"""
test_release_notes_status_section_scope.py
==========================================
The release-notes ``## Status`` stamp and Step 4e's placeholder checks read
only the ``## Status`` section, and pass over HTML comments inside it.

A WRITER-drafted notes file keeps the release-notes template's guidance
comments: a one-line comment between ``## Status`` and the
``PENDING-RELEASE`` placeholder, and a top-of-file comment that quotes the
placeholder word. The stamp rendered into ``## Bugs Skipped`` can also quote
the word, when a skipped bug's Symptom cites an error message. None of that
is the placeholder.

Coverage:
  - cm_stamp_release_notes_status (release_known_issue.sh) stamps an
    unmodified copy of the shipped RELEASE-NOTES-TEMPLATE.md: exit 0, the
    target status is the value under ``## Status``, the guidance comment is
    kept, and nothing of the placeholder is left in the Status section.
  - It passes over a multi-line comment block, a comment sharing the
    placeholder's line, and blank lines; a ``## Status`` heading quoted
    inside a comment is not a heading.
  - A ``<!--`` quoted in prose or inside an inline code span above
    ``## Status`` is text, whether it is never closed or a ``-->`` sits in a
    later section: the Status section is still found and stamped. A
    line-start comment is a comment even when a stray backtick sits in the
    paragraph above it.
  - cm_release_notes_status_has_placeholder answers from the Status section
    alone, and falls back to a whole-file search, saying so, when the
    section reader cannot run or finds no heading where the file has a
    ``## Status`` line.
  - The check and the stamp agree: a placeholder the check reports present
    -- bare, bold, in a code span, annotated, or below a concrete line -- is
    one the stamp replaces, and a file the check reports absent is one the
    stamp leaves alone.
  - A stamp step that fails under set -e reaches the trigger-9
    cm-release.trigger-9.stamp-step-failed HALT instead of ending the shell.
  - Step 4e, through the real cm_release_apply_ship_policy on a sandbox
    lane: template-shaped notes whose top comment and rendered Bugs Skipped
    row both quote the word stamp and commit with no trigger-9 halt; so do
    notes that quote ``<!--`` above ``## Status``; a placeholder that
    genuinely survives under ``## Status`` still halts with
    cm-release.trigger-9.placeholder-survived, quoted opener or not; a
    concrete Status with the word elsewhere, and an already-stamped re-run,
    take the already-complete path untouched; a file with no ``## Status``
    heading still halts; and a file whose only ``## Status`` line sits
    inside a comment above a placeholder stops rather than taking the
    already-complete path.

Every path is under pytest's tmp_path; the sandbox lane's repository has no
remote and its kanban root is a throwaway (tests/fixtures/ship_policy_sandbox.py).
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

from pgai_agent_kanban.cm.notes_status_section import (
    stamp_status_placeholder,
    status_section_has_placeholder,
)
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.ship_policy_sandbox import (
    TESTER_TASK,
    VERSION,
    build_lane,
    run_ship_policy,
)
from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/cm/release_known_issue.sh"
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_TEMPLATE = _TEAM_DIR / "templates" / "project" / "release" / "RELEASE-NOTES-TEMPLATE.md"
_PLACEHOLDER = "PENDING-RELEASE"

_SURVIVED_KEY = "cm-release.trigger-9.placeholder-survived"
_STAMP_FAILED_KEY = "cm-release.trigger-9.stamp-step-failed"
_MISSING_HEADING_KEY = "cm-release.trigger-9.notes-missing-status-heading"


def _diag(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _status_body(notes: str) -> str:
    """Raw body of the first '## Status' section, up to the next level-1/2 heading."""
    match = re.search(r"^##[ \t]+Status[ \t]*\n(.*?)(?=^#{1,2}[ \t]|\Z)", notes, re.MULTILINE | re.DOTALL)
    assert match, f"no '## Status' section in:\n{notes}"
    return match.group(1)


def _stamp(tmp_path: pathlib.Path, notes: pathlib.Path, status: str, bugs_skipped: str = ""):
    return run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' '{status}' '' '{bugs_skipped}'",
    )


def _has_placeholder(tmp_path: pathlib.Path, notes: pathlib.Path, extra_env=None, cwd=None):
    return run_bash(
        tmp_path,
        f"source '{_TEAM_DIR / _LIB}' && if cm_release_notes_status_has_placeholder '{notes}'; "
        "then echo verdict=present; else echo verdict=absent; fi",
        extra_env=extra_env,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# cm_stamp_release_notes_status
# ---------------------------------------------------------------------------


def test_unmodified_template_copy_stamps_the_status_placeholder(tmp_path):
    notes = tmp_path / "template-draft.md"
    notes.write_text(_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

    result = _stamp(tmp_path, notes, "KNOWN-BUGS")

    assert result.returncode == 0, _diag(result)
    text = notes.read_text(encoding="utf-8")
    body = _status_body(text)
    assert re.match(r"\A(?:[ \t]*<!--.*?-->[ \t]*\n)*KNOWN-BUGS\n", body), (
        f"'## Status' must be followed by the target status (past its guidance comment):\n{body}"
    )
    assert "<!-- scripts/cm/release.sh stamps this field at release time." in body, "the guidance comment is kept"
    assert _PLACEHOLDER not in body
    assert _PLACEHOLDER in text.split("\n## Status\n", 1)[0], (
        "the template's top-of-file comment is left as written"
    )


def test_multi_line_comment_block_before_the_placeholder_is_passed_over(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text(
        "# Release Notes: demo v1.4.0\n\n"
        "## Status\n"
        "<!--\n"
        "The release stamps this value.\n"
        "## Status\n"
        "Leave the line below exactly as written.\n"
        "-->\n"
        "\n"
        "PENDING-RELEASE\n\n"
        "## Summary\nText.\n",
        encoding="utf-8",
    )

    result = _stamp(tmp_path, notes, "FUNCTIONAL")

    assert result.returncode == 0, _diag(result)
    text = notes.read_text(encoding="utf-8")
    assert "Leave the line below exactly as written.\n-->\n\nFUNCTIONAL\n\n## Summary" in text, text
    assert _PLACEHOLDER not in text


def test_blank_lines_before_the_placeholder_are_passed_over(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\n\n## Status\n\n\nPENDING-RELEASE\n\n## Summary\nText.\n", encoding="utf-8")

    result = _stamp(tmp_path, notes, "KNOWN-BUGS")

    assert result.returncode == 0, _diag(result)
    assert notes.read_text(encoding="utf-8") == "# Notes\n\n## Status\n\n\nKNOWN-BUGS\n\n## Summary\nText.\n"


def test_plain_notes_stamp_byte_for_byte_as_before(tmp_path):
    original = "# Notes\n\n## Status\nPENDING-RELEASE\n\n## Summary\nText.\n"
    notes = tmp_path / "notes.md"
    notes.write_text(original, encoding="utf-8")

    result = _stamp(tmp_path, notes, "NON-FUNCTIONAL")

    assert result.returncode == 0, _diag(result)
    assert notes.read_text(encoding="utf-8") == original.replace(_PLACEHOLDER, "NON-FUNCTIONAL")


def test_already_stamped_notes_are_reported_and_left_unchanged(tmp_path):
    original = "# Notes\n\n## Status\n<!-- stamped at release time -->\nFUNCTIONAL\n\n## Summary\nText.\n"
    notes = tmp_path / "notes.md"
    notes.write_text(original, encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"source {_LIB} && cm_stamp_release_notes_status '{notes}' 'KNOWN-BUGS' ''; echo \"rc=$?\"",
    )

    assert "rc=1" in result.stdout, _diag(result)
    assert "placeholder not found" in result.stderr
    assert notes.read_text(encoding="utf-8") == original


def test_comment_on_the_placeholder_line_is_kept():
    stamped = stamp_status_placeholder(
        "## Status\n<!-- keep --> PENDING-RELEASE <!-- also kept -->\n", "KNOWN-ISSUE",
    )
    assert stamped == "## Status\n<!-- keep --> KNOWN-ISSUE <!-- also kept -->\n"


def test_status_heading_quoted_inside_a_comment_is_not_a_heading():
    text = "<!--\n## Status\nPENDING-RELEASE\n-->\n# Notes\n\n## Status\nPENDING-RELEASE\n"
    stamped = stamp_status_placeholder(text, "FUNCTIONAL")
    assert stamped == "<!--\n## Status\nPENDING-RELEASE\n-->\n# Notes\n\n## Status\nFUNCTIONAL\n"
    assert not status_section_has_placeholder(stamped)


def test_a_later_placeholder_line_is_stamped_and_the_concrete_line_above_it_kept():
    # The check reports this placeholder present, so the stamp must be able
    # to replace it; the concrete line above it is left as written.
    text = "## Status\nTBD\nPENDING-RELEASE\n"
    assert status_section_has_placeholder(text) is True
    assert stamp_status_placeholder(text, "FUNCTIONAL") == "## Status\nTBD\nFUNCTIONAL\n"


@pytest.mark.parametrize(
    ("value", "stamped_value"),
    [
        ("**PENDING-RELEASE**", "KNOWN-BUGS"),
        ("`PENDING-RELEASE`", "KNOWN-BUGS"),
        ("PENDING-RELEASE (CM stamps at ship)", "KNOWN-BUGS (CM stamps at ship)"),
        ("PENDING-RELEASE (note)", "KNOWN-BUGS (note)"),
    ],
    ids=["bold", "code-span", "annotated", "annotated-short"],
)
def test_check_and_stamp_agree_on_a_decorated_placeholder(value, stamped_value):
    text = f"## Status\n<!-- CM stamps this field -->\n{value}\n\n## Summary\nText.\n"

    assert status_section_has_placeholder(text) is True
    stamped = stamp_status_placeholder(text, "KNOWN-BUGS")

    assert stamped == text.replace(value, stamped_value, 1)
    assert status_section_has_placeholder(stamped) is False


@pytest.mark.parametrize(
    "text",
    [
        "## Status\nFUNCTIONAL\n",
        "## Status\n<!-- PENDING-RELEASE goes here -->\nKNOWN-BUGS\n",
        "# Notes\n\nPENDING-RELEASE\n",
    ],
    ids=["concrete", "placeholder-only-in-comment", "no-status-section"],
)
def test_check_and_stamp_agree_when_no_placeholder_is_visible(text):
    assert status_section_has_placeholder(text) is False
    assert stamp_status_placeholder(text, "KNOWN-BUGS") is None


# ---------------------------------------------------------------------------
# A quoted comment opener above ## Status
# ---------------------------------------------------------------------------

_INLINE_CODE_OPENER = (
    "# Release Notes: demo v1.4.0\n\n"
    "**Highlights:** the summary reader no longer treats a quoted `<!--` as a comment opener.\n\n"
)
_PROSE_OPENER = (
    "# Release Notes: demo v1.4.0\n\n"
    "Authors open a guidance comment with <!-- and nothing else on that line.\n\n"
)
_STATUS_AND_SUMMARY = "## Status\nPENDING-RELEASE\n\n## Summary\nText.\n"


@pytest.mark.parametrize(
    "notes",
    [
        _INLINE_CODE_OPENER + _STATUS_AND_SUMMARY,
        _INLINE_CODE_OPENER + _STATUS_AND_SUMMARY + "\n## Upgrade Notes\nClose a comment with `-->`, or -->.\n",
        _PROSE_OPENER + _STATUS_AND_SUMMARY,
        _PROSE_OPENER + _STATUS_AND_SUMMARY + "\n## Upgrade Notes\nA comment ends at -->.\n",
    ],
    ids=["inline-code-unclosed", "inline-code-later-close", "prose-unclosed", "prose-later-close"],
)
def test_quoted_opener_above_status_leaves_the_section_readable_and_stampable(notes):
    assert status_section_has_placeholder(notes) is True

    stamped = stamp_status_placeholder(notes, "KNOWN-BUGS")

    assert stamped == notes.replace("## Status\nPENDING-RELEASE\n", "## Status\nKNOWN-BUGS\n", 1)
    assert _status_body(stamped) == "KNOWN-BUGS\n\n"
    assert status_section_has_placeholder(stamped) is False


def test_quoted_opener_inside_the_status_section_does_not_hide_a_surviving_placeholder():
    notes = "## Status\nPENDING-RELEASE\nstill `<!--` PENDING-RELEASE\n\n## Summary\nText. -->\n"

    stamped = stamp_status_placeholder(notes, "FUNCTIONAL")

    assert stamped is not None
    assert status_section_has_placeholder(stamped) is True, "the second placeholder is visible text"


def test_line_start_comment_stays_a_comment_after_a_stray_backtick():
    # The line-start comment ends the paragraph holding the stray backtick,
    # so that backtick never pairs with the one inside the comment to make
    # the comment read as inline code; its placeholder word is not counted.
    notes = "## Status\nFUNCTIONAL, with a stray ` backtick.\n<!-- PENDING-RELEASE was here; ` -->\n"
    assert status_section_has_placeholder(notes) is False


# ---------------------------------------------------------------------------
# cm_release_notes_status_has_placeholder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("## Status\nPENDING-RELEASE\n\n## Summary\nText.\n", "present"),
        ("## Status\n<!-- note -->\n\nPENDING-RELEASE\n", "present"),
        ("## Status\nKNOWN-BUGS\nstill PENDING-RELEASE here\n\n## Summary\n", "present"),
        ("<!-- write PENDING-RELEASE under Status -->\n## Status\nFUNCTIONAL\n", "absent"),
        ("## Status\nFUNCTIONAL\n\n## Bugs Skipped\n| x | prints \"PENDING-RELEASE\" |\n", "absent"),
        ("## Status\n<!-- leave PENDING-RELEASE as-is -->\nFUNCTIONAL\n", "absent"),
        ("# Notes\n\nPENDING-RELEASE\n", "absent"),
        ("A quoted `<!--` opener.\n\n## Status\nPENDING-RELEASE\n", "present"),
        ("A quoted `<!--` opener.\n\n## Status\nPENDING-RELEASE\n\n## Summary\nEnds -->\n", "present"),
        ("A quoted `<!--` opener.\n\n## Status\nFUNCTIONAL\n\n## Summary\nPENDING-RELEASE -->\n", "absent"),
    ],
    ids=[
        "bare-placeholder",
        "after-comment",
        "inside-status-body",
        "only-in-top-comment",
        "only-in-bugs-skipped-row",
        "only-in-status-comment",
        "no-status-heading",
        "quoted-opener-above-status",
        "quoted-opener-with-later-close",
        "quoted-opener-concrete-status",
    ],
)
def test_placeholder_check_reads_only_the_status_section(tmp_path, body, expected):
    notes = tmp_path / "notes.md"
    notes.write_text(body, encoding="utf-8")

    result = _has_placeholder(tmp_path, notes)

    assert result.returncode == 0, _diag(result)
    assert f"verdict={expected}" in result.stdout, _diag(result)
    assert result.stderr == "", _diag(result)


def test_placeholder_check_falls_back_to_the_whole_file_when_the_reader_cannot_run(tmp_path):
    shadow = tmp_path / "shadow"
    (shadow / "pgai_agent_kanban").mkdir(parents=True)
    (shadow / "pgai_agent_kanban" / "__init__.py").write_text(
        "raise ImportError('section reader unavailable in this fixture')\n", encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("## Status\nFUNCTIONAL\n\n## Bugs Skipped\nPENDING-RELEASE quoted\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("## Status\nFUNCTIONAL\n", encoding="utf-8")

    # Run from tmp_path: "python3 -m" puts its working directory first on
    # sys.path, and team/ (the harness default) holds the real package.
    shadowed = {"PYTHONPATH": str(shadow)}
    stricter = _has_placeholder(tmp_path, elsewhere, extra_env=shadowed, cwd=tmp_path)
    unchanged = _has_placeholder(tmp_path, clean, extra_env=shadowed, cwd=tmp_path)

    assert "verdict=present" in stricter.stdout, _diag(stricter)
    assert "searching the whole file for PENDING-RELEASE instead" in stricter.stderr, _diag(stricter)
    assert "verdict=absent" in unchanged.stdout, _diag(unchanged)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<!--\n## Status\nPENDING-RELEASE\n-->\n# Notes\n", "present"),
        ("<!--\n## Status\nFUNCTIONAL\n-->\n# Notes\n", "absent"),
    ],
    ids=["placeholder-word-in-file", "no-placeholder-word"],
)
def test_placeholder_check_searches_the_whole_file_when_the_reader_finds_no_status_heading(
    tmp_path, body, expected,
):
    # The file has a '## Status' line, but only inside a comment, so the
    # section reader finds no Status section: the check cannot tell, and
    # answers from the whole file instead of answering "absent".
    notes = tmp_path / "notes.md"
    notes.write_text(body, encoding="utf-8")

    result = _has_placeholder(tmp_path, notes)

    assert result.returncode == 0, _diag(result)
    assert f"verdict={expected}" in result.stdout, _diag(result)
    assert "did not read as a heading" in result.stderr, _diag(result)
    assert "searching the whole file for PENDING-RELEASE instead" in result.stderr, _diag(result)


# ---------------------------------------------------------------------------
# Step 4e, through cm_release_apply_ship_policy
# ---------------------------------------------------------------------------

_QUOTING_FINDING = """### Finding 1: Release stamp misreads a guidance comment

**Category:** bug
**Fix Effort:** small
**Systemic Risk:** low
**Filed As:** BUG-0101

The stamp prints "PENDING-RELEASE placeholder not found" and exits 1.
"""


@pytest.fixture
def _git() -> None:
    require_binary("git", label="release-notes-status-section-scope")


def _lane_with_notes(tmp_path: pathlib.Path, notes: str, *, quoting_finding: bool = False):
    """A SHIP-WITH-CONCERNS lane (ships KNOWN-BUGS) whose RC branch carries *notes*."""
    lane = build_lane(tmp_path, "concerns", None)
    if quoting_finding:
        report = lane.project_root / "tasks" / TESTER_TASK / "artifacts" / "report.md"
        report.write_text(
            f"# Verification Report: {VERSION}\n\n## Recommendation\nSHIP-WITH-CONCERNS\n\n"
            f"## Systemic Risk\nlow\n\n## Findings\n\n{_QUOTING_FINDING}",
            encoding="utf-8",
        )
        (lane.project_root / "bugs" / "BUG-0101-fixture.md").write_text(
            "# BUG-0101-fixture\n\n## Status\nopen\n\n## Symptom\n"
            "The release stamp prints \"PENDING-RELEASE placeholder not found\" "
            "on notes that keep the template's guidance comment.\n\n"
            "## Severity\nmedium\n",
            encoding="utf-8",
        )
    lane.notes_file.write_text(notes, encoding="utf-8")
    for args in (("add", "-A"), ("commit", "-q", "-m", "docs: release notes")):
        subprocess.run(["git", "-C", str(lane.repo), *args], check=True, capture_output=True, text=True)
    return lane


def _head_subject(lane) -> str:
    return subprocess.run(
        ["git", "-C", str(lane.repo), "log", "-1", "--format=%s"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_step_4e_stamps_template_notes_that_quote_the_placeholder_elsewhere(tmp_path, _git):
    lane = _lane_with_notes(tmp_path, _TEMPLATE.read_text(encoding="utf-8"), quoting_finding=True)

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode == 0, _diag(result)
    assert not lane.halt_file.exists(), _diag(result)
    assert "trigger-9" not in result.stdout + result.stderr, _diag(result)
    notes = lane.notes_file.read_text(encoding="utf-8")
    body = _status_body(notes)
    assert "\nKNOWN-BUGS\n" in body and _PLACEHOLDER not in body, body
    skipped = notes.split("\n## Bugs Skipped\n", 1)[1].split("\n## ", 1)[0]
    assert 'prints "PENDING-RELEASE placeholder not found"' in skipped, (
        f"the stamp renders the skipped bug's substance, word and all:\n{skipped}"
    )
    assert _PLACEHOLDER in notes.split("\n## Status\n", 1)[0], "the top-of-file comment is kept"
    assert _head_subject(lane) == f"Stamp release-notes/{VERSION}.md ## Status: KNOWN-BUGS"


@pytest.mark.parametrize(
    "status_body",
    [
        "PENDING-RELEASE\nPENDING-RELEASE\n",
        "PENDING-RELEASE\nawaiting PENDING-RELEASE sign-off\n",
    ],
    ids=["second-placeholder-line", "placeholder-in-trailing-prose"],
)
def test_step_4e_still_halts_when_the_placeholder_survives_under_status(tmp_path, _git, status_body):
    notes = (
        "<!-- leave PENDING-RELEASE under Status -->\n# Release Notes\n\n"
        f"## Status\n<!-- stamped at release time -->\n{status_body}\n## Summary\nText.\n"
    )
    lane = _lane_with_notes(tmp_path, notes)

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode != 0, _diag(result)
    assert lane.halt_file.is_file(), _diag(result)
    assert _SURVIVED_KEY in lane.halt_file.read_text(encoding="utf-8"), _diag(result)
    assert _SURVIVED_KEY in result.stderr, _diag(result)


@pytest.mark.parametrize(
    "notes",
    [
        "<!-- WRITER leaves PENDING-RELEASE below -->\n# Release Notes\n\n## Status\nKNOWN-BUGS\n\n"
        "## Bugs Skipped\n| A defect. | internal-only | LOW | prints \"PENDING-RELEASE placeholder not found\". |\n",
        "# Release Notes\n\n## Status\nKNOWN-BUGS\n\n## Summary\nText.\n",
    ],
    ids=["concrete-status-word-elsewhere", "already-stamped-rerun"],
)
def test_step_4e_takes_the_already_complete_path_for_a_concrete_status(tmp_path, _git, notes):
    lane = _lane_with_notes(tmp_path, notes)
    head_before = _head_subject(lane)

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode == 0, _diag(result)
    assert not lane.halt_file.exists(), _diag(result)
    assert _STAMP_FAILED_KEY not in result.stdout + result.stderr, _diag(result)
    assert "[Step 4e] step 4e: already complete, continuing" in result.stdout, _diag(result)
    assert lane.notes_file.read_text(encoding="utf-8") == notes
    assert _head_subject(lane) == head_before, "nothing is stamped or committed"


def test_step_4e_still_halts_on_notes_with_no_status_heading(tmp_path, _git):
    lane = _lane_with_notes(tmp_path, "# Release Notes\n\n## Summary\nText.\n")

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode != 0, _diag(result)
    assert lane.halt_file.is_file(), _diag(result)
    assert _MISSING_HEADING_KEY in lane.halt_file.read_text(encoding="utf-8"), _diag(result)


@pytest.mark.parametrize(
    "notes",
    [
        _INLINE_CODE_OPENER + _STATUS_AND_SUMMARY,
        _INLINE_CODE_OPENER + _STATUS_AND_SUMMARY + "\n## Upgrade Notes\nA comment ends at -->.\n",
    ],
    ids=["unclosed", "later-close"],
)
def test_step_4e_stamps_notes_that_quote_a_comment_opener_above_status(tmp_path, _git, notes):
    lane = _lane_with_notes(tmp_path, notes)

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode == 0, _diag(result)
    assert not lane.halt_file.exists(), _diag(result)
    assert "trigger-9" not in result.stdout + result.stderr, _diag(result)
    assert "already complete, continuing — release-notes" not in result.stdout, _diag(result)
    stamped = lane.notes_file.read_text(encoding="utf-8")
    assert _status_body(stamped) == "KNOWN-BUGS\n\n", stamped
    assert stamped == notes.replace("## Status\nPENDING-RELEASE\n", "## Status\nKNOWN-BUGS\n", 1)
    assert _head_subject(lane) == f"Stamp release-notes/{VERSION}.md ## Status: KNOWN-BUGS"


def test_step_4e_still_halts_when_the_placeholder_survives_below_a_quoted_opener(tmp_path, _git):
    notes = (
        _INLINE_CODE_OPENER
        + "## Status\nPENDING-RELEASE\nPENDING-RELEASE\n\n## Upgrade Notes\nA comment ends at -->.\n"
    )
    lane = _lane_with_notes(tmp_path, notes)

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode != 0, _diag(result)
    assert lane.halt_file.is_file(), _diag(result)
    assert _SURVIVED_KEY in lane.halt_file.read_text(encoding="utf-8"), _diag(result)
    assert _SURVIVED_KEY in result.stderr, _diag(result)


def test_step_4e_stops_when_the_only_status_line_sits_inside_a_comment_above_a_placeholder(tmp_path, _git):
    # The reader finds no Status section, yet the file has a '## Status'
    # line and the placeholder word: the pre-stamp check cannot tell, so
    # Step 4e tries the stamp, which finds nothing to replace, and the stage
    # stops rather than reporting the notes already complete.
    notes = "<!--\n## Status\nPENDING-RELEASE\n-->\n# Release Notes\n\n## Summary\nText.\n"
    lane = _lane_with_notes(tmp_path, notes)
    head_before = _head_subject(lane)

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode != 0, _diag(result)
    assert "already complete, continuing — release-notes" not in result.stdout, _diag(result)
    assert "searching the whole file for PENDING-RELEASE instead" in result.stderr, _diag(result)
    assert "placeholder not found under ## Status heading" in result.stderr, _diag(result)
    assert lane.notes_file.read_text(encoding="utf-8") == notes
    assert _head_subject(lane) == head_before, "nothing is stamped or committed"
    assert lane.halt_file.is_file(), "the failed stamp reaches its trigger-9 HALT\n" + _diag(result)
    assert _STAMP_FAILED_KEY in lane.halt_file.read_text(encoding="utf-8"), _diag(result)


def test_step_4e_halts_when_the_stamp_step_fails_under_set_e(tmp_path, _git):
    # The stage runs under set -euo pipefail, as release.sh runs it. A stamp
    # that returns non-zero must reach the trigger-9 HALT (HALT file, error
    # contract on stderr, HALT Event in release-state.md), not end the shell
    # silently at the call.
    notes = "# Release Notes\n\n## Status\nPENDING-RELEASE\n\n## Summary\nText.\n"
    lane = _lane_with_notes(tmp_path, notes)

    result = run_ship_policy(
        tmp_path, lane, before="cm_stamp_release_notes_status() { echo stub-stamp-ran >&2; return 7; }",
    )

    assert result.returncode != 0, _diag(result)
    assert "stub-stamp-ran" in result.stderr, _diag(result)
    assert lane.halt_file.is_file(), _diag(result)
    halt = lane.halt_file.read_text(encoding="utf-8")
    assert _STAMP_FAILED_KEY in halt, halt
    assert "rc=7" in halt, halt
    assert _STAMP_FAILED_KEY in result.stderr, _diag(result)
    assert _STAMP_FAILED_KEY in (lane.project_root / "release-state.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "value",
    ["**PENDING-RELEASE**", "PENDING-RELEASE (CM stamps at ship)"],
    ids=["bold", "annotated"],
)
def test_step_4e_stamps_a_decorated_placeholder_without_halting(tmp_path, _git, value):
    notes = f"# Release Notes\n\n## Status\n<!-- CM stamps this field -->\n{value}\n\n## Summary\nText.\n"
    lane = _lane_with_notes(tmp_path, notes)

    result = run_ship_policy(tmp_path, lane)

    assert result.returncode == 0, _diag(result)
    assert not lane.halt_file.exists(), _diag(result)
    assert "trigger-9" not in result.stdout + result.stderr, _diag(result)
    body = _status_body(lane.notes_file.read_text(encoding="utf-8"))
    assert "KNOWN-BUGS" in body and _PLACEHOLDER not in body, body
    assert _head_subject(lane) == f"Stamp release-notes/{VERSION}.md ## Status: KNOWN-BUGS"
