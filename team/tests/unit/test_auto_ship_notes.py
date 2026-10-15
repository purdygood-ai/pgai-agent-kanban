"""
test_auto_ship_notes.py
=======================
Unit tests for pgai_agent_kanban.cm.auto_ship_notes -- the wording of the
auto-ship line and where it goes in a release-notes file's
"## Known Issues" section.

Coverage:
  - The line's exact wording, with the trigger number and error key.
  - Placement: the lines open the section ahead of existing text, separated
    by one blank line; an empty or "None"/"None." body (HTML comments
    ignored) is replaced; a missing section is appended; the next section
    and the whitespace around the body are kept.
  - Idempotency: a line already in the section is not added again, and a
    second run leaves the file byte-identical.
  - The CLI: rewrites in place keeping the file mode, reports already-
    present lines, exits 1 on an unreadable file (leaving nothing behind)
    and 2 on a usage error.
"""

from __future__ import annotations

import os
import pathlib
import stat

import pytest

from pgai_agent_kanban.cm.auto_ship_notes import auto_ship_line, main, place_auto_ship_lines

_T2 = "cm-release.trigger-2.systemic-risk-high"
_T8 = "cm-release.trigger-8.last-3-rcs-non-functional"
_LINE_2 = "Auto-shipped under ship_posture=always (would have halted: trigger 2 — cm-release.trigger-2.systemic-risk-high)"
_LINE_8 = "Auto-shipped under ship_posture=always (would have halted: trigger 8 — cm-release.trigger-8.last-3-rcs-non-functional)"


def test_line_names_the_trigger_and_error_key():
    assert auto_ship_line("2", _T2) == _LINE_2
    assert auto_ship_line("8", _T8) == _LINE_8


@pytest.mark.parametrize("body", ["None", "None.", "none", "<!-- nothing to report -->\nNone."])
def test_a_body_that_says_nothing_is_replaced(body):
    text = f"## Status\nFUNCTIONAL\n\n## Known Issues\n{body}\n"
    assert place_auto_ship_lines(text, [_LINE_8]) == f"## Status\nFUNCTIONAL\n\n## Known Issues\n{_LINE_8}\n"


def test_existing_text_follows_the_lines_after_one_blank_line():
    text = (
        "## Known Issues\nThis release is shipped non-functional.\n- A finding.\n\n"
        "## Bugs Skipped\nNone.\n"
    )
    assert place_auto_ship_lines(text, [_LINE_8, _LINE_2]) == (
        f"## Known Issues\n{_LINE_8}\n{_LINE_2}\n\nThis release is shipped non-functional.\n- A finding.\n\n"
        "## Bugs Skipped\nNone.\n"
    )


def test_the_spacing_after_the_heading_is_kept():
    text = "## Known Issues\n\nAuthor's own note.\n\n"
    assert place_auto_ship_lines(text, [_LINE_2]) == f"## Known Issues\n\n{_LINE_2}\n\nAuthor's own note.\n\n"


def test_an_empty_section_followed_by_another_keeps_a_blank_line_before_it():
    text = "## Known Issues\n## Bugs Skipped\nNone.\n"
    assert place_auto_ship_lines(text, [_LINE_2]) == f"## Known Issues\n{_LINE_2}\n\n## Bugs Skipped\nNone.\n"


def test_a_heading_at_end_of_file_without_a_newline_gets_a_body():
    assert place_auto_ship_lines("## Known Issues", [_LINE_2]) == f"## Known Issues\n{_LINE_2}\n"


def test_a_missing_section_is_appended():
    text = "## Status\nFUNCTIONAL\n"
    assert place_auto_ship_lines(text, [_LINE_2]) == f"## Status\nFUNCTIONAL\n\n## Known Issues\n\n{_LINE_2}\n"


def test_the_heading_is_matched_liberally():
    text = "##  known issues \t\nNone\n"
    assert place_auto_ship_lines(text, [_LINE_2]) == f"##  known issues \t\n{_LINE_2}\n"


def test_lines_already_present_are_not_added_again():
    once = place_auto_ship_lines("## Known Issues\nNone\n", [_LINE_8])
    assert place_auto_ship_lines(once, [_LINE_8]) == once
    both = place_auto_ship_lines(once, [_LINE_8, _LINE_2])
    assert both.count(_LINE_8) == 1 and both.count(_LINE_2) == 1


def test_no_lines_leaves_the_text_unchanged():
    for text in ("## Known Issues\nNone\n", "## Status\nFUNCTIONAL\n"):
        assert place_auto_ship_lines(text, []) == text


def test_cli_rewrites_in_place_and_keeps_the_file_mode(tmp_path, capsys):
    notes_dir = tmp_path / "release-notes"
    notes_dir.mkdir()
    notes = notes_dir / "v1.0.0.md"
    notes.write_text("## Status\nFUNCTIONAL\n\n## Known Issues\nNone\n", encoding="utf-8")
    os.chmod(notes, 0o644)

    assert main([str(notes), "--stop", "8", _T8, "--stop", "2", _T2]) == 0

    assert notes.read_text(encoding="utf-8") == (
        f"## Status\nFUNCTIONAL\n\n## Known Issues\n{_LINE_8}\n{_LINE_2}\n"
    )
    assert stat.S_IMODE(notes.stat().st_mode) == 0o644
    assert "Stamped auto-ship line(s) under ## Known Issues in release-notes/v1.0.0.md" in capsys.readouterr().out

    before = notes.read_bytes()
    assert main([str(notes), "--stop", "8", _T8]) == 0
    assert notes.read_bytes() == before
    assert "already carries the auto-ship line(s)" in capsys.readouterr().out
    assert sorted(p.name for p in notes_dir.iterdir()) == ["v1.0.0.md"], "no scratch file left behind"


def test_cli_exits_1_naming_the_file_when_it_cannot_be_read(tmp_path, capsys):
    missing = tmp_path / "v1.0.0.md"
    assert main([str(missing), "--stop", "2", _T2]) == 1
    assert str(missing) in capsys.readouterr().err
    assert not missing.exists()


def test_cli_without_a_stop_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path / "v1.0.0.md")])
    assert exc.value.code == 2


def test_help_describes_the_arguments(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "notes_file" in out and "--stop TRIGGER ERROR_KEY" in out
