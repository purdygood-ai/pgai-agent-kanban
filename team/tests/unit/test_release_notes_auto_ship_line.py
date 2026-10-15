"""
test_release_notes_auto_ship_line.py
====================================
The release notes of a release the ship posture shipped through a
ship-policy stop carry, under "## Known Issues", the line

    Auto-shipped under ship_posture=always (would have halted: trigger N — <error key>)

with the real trigger number and error key, on both release-notes paths:

  - the Step 4e stamp of WRITER-authored notes, run here through the real
    cm_release_apply_ship_policy (release_ship_policy.sh) against a sandbox
    lane whose RC branch carries notes with the PENDING-RELEASE placeholder;
  - the Step 11a generator (the module
    pgai_agent_kanban/cm/release_notes_generator.py, run with the argv
    cm_release_generate_notes passes) followed by
    cm_ship_posture_stamp_notes, the call Step 11a makes before committing.

Coverage:
  - Under ship_posture = always, the trigger-2, trigger-3 and trigger-8
    fixtures ship, and the notes carry the exact line once, naming that
    trigger and its error key: on both paths for triggers 2 and 3, with the
    stamped WRITER notes committed with the line in them; on the generated
    path for trigger 8. The pattern check reads every release-notes file,
    the RC's own included, so a lane whose RC carries WRITER notes still
    waiting on the PENDING-RELEASE stamp never reaches trigger 8.
  - Under the matrix posture, with the key absent, and under always on a
    fixture with no stop to ship through, both notes paths are
    byte-identical to what the status stamp and the generator produce on
    their own, and no auto-ship text is printed or written.
  - A notes file the line cannot be written to is a warning naming the
    command that adds it, never an exit.

The lane builders live in tests/fixtures/ship_policy_sandbox.py; every path
is under tmp_path and the repository has no remote.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.ship_policy_sandbox import (
    KEY_T2,
    KEY_T3,
    KEY_T8,
    VERSION,
    WRITER_NOTES,
    build_lane,
    generate_notes_bash,
    run_ship_policy,
    section,
    write_notes_generator,
)
from tests.unit.shell_harness import run_bash


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH."""
    require_binary("git", label="release-notes-auto-ship-line")


def _line(trigger: int, error_key: str) -> str:
    return f"Auto-shipped under ship_posture=always (would have halted: trigger {trigger} — {error_key})"


def _diag(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# fixture -> (trigger, error key, release-notes Status it ships with,
#             whether the RC carries WRITER-authored notes for Step 4e)
_STOPS = {
    "trigger-2": (2, KEY_T2, "NON-FUNCTIONAL", True),
    "trigger-3": (3, KEY_T3, "NON-FUNCTIONAL", True),
    "trigger-8": (8, KEY_T8, "FUNCTIONAL", False),
}


@pytest.mark.parametrize("fixture", sorted(_STOPS))
def test_auto_shipped_release_notes_name_the_stop_under_known_issues_on_both_paths(tmp_path, fixture):
    trigger, error_key, status, writer_notes = _STOPS[fixture]
    lane = build_lane(tmp_path, fixture, "always", writer_notes=writer_notes)
    generated_root = tmp_path / "generated"
    generated = generated_root / "release-notes" / f"{VERSION}.md"
    after = "\n".join([
        generate_notes_bash(write_notes_generator(tmp_path), generated_root),
        f"cm_ship_posture_stamp_notes '{generated}'",
    ])

    result = run_ship_policy(tmp_path, lane, after)

    assert result.returncode == 0, _diag(result)
    assert not lane.halt_file.exists(), _diag(result)
    expected = _line(trigger, error_key)
    notes_paths = (lane.notes_file, generated) if writer_notes else (generated,)
    for path in notes_paths:
        notes = path.read_text(encoding="utf-8")
        assert section(notes, "Status") == status, notes
        known = section(notes, "Known Issues")
        assert known.splitlines().count(expected) == 1, (
            f"{path.name} must carry {expected!r} once, as its own line, under ## Known Issues:\n{notes}"
        )
        assert known.splitlines()[0] == expected, known
        if status == "NON-FUNCTIONAL":
            assert "This release is shipped non-functional." in known, known
        else:
            assert known == expected, f"the empty Known Issues body is replaced by the line:\n{known}"
    if writer_notes:
        committed = run_bash(tmp_path, f"git -C '{lane.repo}' show HEAD:release-notes/{VERSION}.md")
        assert committed.stdout == lane.notes_file.read_text(encoding="utf-8"), (
            "Step 4e must commit the notes with the auto-ship line in them"
        )


# Fixtures that ship without a covered stop, and the postures that must
# leave their notes exactly as the stamp and the generator write them.
_PLAIN_FIXTURES = ("pass", "concerns", "serious-small", "known-issue")
_PLAIN_POSTURES = {"key_absent": None, "matrix": "matrix", "always_without_a_stop": "always"}


@pytest.mark.parametrize("posture_id", sorted(_PLAIN_POSTURES))
@pytest.mark.parametrize("fixture", _PLAIN_FIXTURES)
def test_release_notes_without_an_auto_ship_stop_are_byte_identical(tmp_path, fixture, posture_id):
    lane = build_lane(tmp_path, fixture, _PLAIN_POSTURES[posture_id], writer_notes=True)
    reference_writer = tmp_path / "reference-writer.md"
    reference_writer.write_text(WRITER_NOTES, encoding="utf-8")
    generated_root = tmp_path / "generated"
    generated = generated_root / "release-notes" / f"{VERSION}.md"
    reference_generated = tmp_path / "reference-generated.md"
    stamp_output = tmp_path / "stamp-output.txt"
    after = "\n".join([
        f"cm_stamp_release_notes_status '{reference_writer}' \"$_RELEASE_NOTES_STATUS\" "
        "\"$_KNOWN_ISSUE_NOTES_BLOCK\" \"$_BUGS_SKIPPED_NOTES_BLOCK\" >/dev/null",
        generate_notes_bash(write_notes_generator(tmp_path), generated_root),
        f"cp '{generated}' '{reference_generated}'",
        f"cm_ship_posture_stamp_notes '{generated}' > '{stamp_output}' 2>&1",
    ])

    result = run_ship_policy(tmp_path, lane, after)

    assert result.returncode == 0, _diag(result)
    assert lane.notes_file.read_bytes() == reference_writer.read_bytes(), (
        "Step 4e must write exactly the status stamp when no stop was shipped through"
    )
    assert generated.read_bytes() == reference_generated.read_bytes(), (
        "Step 11a must leave the generator's output untouched when no stop was shipped through"
    )
    assert stamp_output.read_text(encoding="utf-8") == ""
    assert "Auto-shipped" not in result.stdout + result.stderr
    assert "Auto-shipped" not in lane.notes_file.read_text(encoding="utf-8")


def test_a_notes_file_that_cannot_be_updated_is_a_warning_not_an_exit(tmp_path):
    missing = tmp_path / "release-notes" / f"{VERSION}.md"
    script = "\n".join([
        "set -euo pipefail",
        "source scripts/lib/pp_run_ops.sh",
        "source scripts/lib/cm/release_ship_posture.sh",
        f"_CM_AUTO_SHIP_EVENTS=($'2026-01-01T00:00:00Z\\t2\\t{KEY_T2}\\t')",
        f"cm_ship_posture_stamp_notes '{missing}'",
        "echo continued",
    ])

    result = run_bash(tmp_path, script)

    assert result.returncode == 0, _diag(result)
    assert result.stdout.strip().endswith("continued"), _diag(result)
    assert "WARNING: the auto-ship line(s) could not be added" in result.stderr, _diag(result)
    assert f"--stop 2 {KEY_T2}" in result.stderr, "the warning names the command that adds the line"
    assert not missing.exists()


def test_the_warning_remedy_is_a_command_that_adds_the_line_when_pasted(tmp_path):
    """The by-hand command the warning prints runs as printed and places the line.

    The stamp fails on a missing notes file; once the file exists, running
    the remedy text verbatim in a shell from an unrelated working directory
    adds the auto-ship line under ``## Known Issues``. The remedy names the
    notes file and every ``--stop`` pair, and runs the module by file path.
    """
    team_dir = pathlib.Path(__file__).resolve().parents[2]
    notes = tmp_path / "release-notes" / f"{VERSION}.md"
    script = "\n".join([
        "set -euo pipefail",
        "source scripts/lib/pp_run_ops.sh",
        "source scripts/lib/cm/release_ship_posture.sh",
        f"_CM_AUTO_SHIP_EVENTS=($'2026-01-01T00:00:00Z\\t2\\t{KEY_T2}\\t')",
        f"cm_ship_posture_stamp_notes '{notes}'",
    ])
    warned = run_bash(tmp_path, script, extra_env={"KANBAN_ROOT": str(team_dir)})
    assert warned.returncode == 0, _diag(warned)
    marker = "Add them by hand and commit the file: "
    assert marker in warned.stderr, _diag(warned)
    remedy = warned.stderr.split(marker, 1)[1].strip()
    assert str(notes) in remedy and f"--stop 2 {KEY_T2}" in remedy, remedy
    assert "python3 -m" not in remedy, remedy

    notes.parent.mkdir(parents=True)
    notes.write_text("# Release\n\n## Known Issues\n\nNone.\n", encoding="utf-8")
    pasted = run_bash(tmp_path, remedy, cwd=tmp_path)

    assert pasted.returncode == 0, _diag(pasted)
    assert _line(2, KEY_T2) in notes.read_text(encoding="utf-8").splitlines()
