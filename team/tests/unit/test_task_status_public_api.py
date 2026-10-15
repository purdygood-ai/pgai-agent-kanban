"""
test_task_status_public_api.py
==============================
Tests for the public status.md API in team/pgai_agent_kanban/task_status.py
(read_section, section_value, read_state, replace_section, set_section,
set_sections), its ``read`` / ``set-section`` subcommands, the
``task-status.sh read`` mode, and the ops/write callers that delegate to it.

Every reader test runs over the variants the writers and hand-edits
produce: an inline value on the heading line, blank lines between heading
and value, a bold value, trailing text on the value line, the template's
HTML comment, and CRLF line endings.

Test coverage:

  1. read_section returns the body for each variant, ``""`` for an empty
     section, ``None`` for an absent heading, and never matches a longer
     heading (``## State Notes``).
  2. read_state reads each variant fixture file to the expected value, and
     returns ``""`` for a missing file.
  3. set_section / set_sections change only the named section — every
     other byte of the file is preserved — keep the blank-line separator,
     canonicalise an inline heading, create a section only when asked to,
     and write nothing when a section is absent.
  4. ``task_status read`` prints the same State value as ``--read-state``
     on every variant fixture, and ``task-status.sh read`` does too.
  5. ``task-status.sh --help`` documents the read mode.
  6. The subcommands refuse a missing target, a conflicting target, a
     missing status.md, and a ``## State`` write dual-channel (error,
     message, remedy), exit 1, and write nothing.
  7. ops/write's close and wontdo writers reset State/Blockers/Needs Human
     through the shared writer and keep the other sections intact; the
     intake close writer creates ## Close Note after ## Status.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pgai_agent_kanban.ops.errors import OpsError
from pgai_agent_kanban.ops.write import _write_intake_close, _write_task_terminal_state
from pgai_agent_kanban.task_status import (
    TaskStatusError,
    main,
    read_section,
    read_state,
    replace_section,
    section_value,
    set_section,
    set_sections,
)

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_TASK_STATUS_SH = _TEAM_DIR / "scripts" / "task-status.sh"
_TEMPLATE_PATH = _TEAM_DIR / "templates" / "task" / "task-status" / "status.md"


def _status(state_block: str) -> str:
    """A small status.md whose ## State section is *state_block* verbatim."""
    return (
        "# Status\n"
        "\n"
        "## Task\n"
        "CODER-20260101-001-example-task\n"
        "\n"
        f"{state_block}"
        "\n"
        "## Summary\n"
        "Original summary.\n"
        "\n"
        "## Blockers\n"
        "none\n"
        "\n"
        "## Needs Human\n"
        "no\n"
    )


# (fixture id, State section text, expected read_section body, expected read_state)
_VARIANTS = [
    ("plain", "## State\nWORKING\n", "WORKING", "WORKING"),
    ("blank-lines", "## State\n\n\nDONE\n", "DONE", "DONE"),
    ("inline", "## State: blocked\n", "blocked", "BLOCKED"),
    ("bold", "## State\n**WONT-DO**\n", "**WONT-DO**", "WONT-DO"),
    ("trailing-text", "## State\nDONE (merged)\n", "DONE (merged)", "DONE (MERGED)"),
    ("trailing-spaces", "## State  \t\nwaiting\n", "waiting", "WAITING"),
    (
        "html-comment",
        "## State\n<!-- One of: BACKLOG WAITING -->\nBACKLOG\n",
        "<!-- One of: BACKLOG WAITING -->\nBACKLOG",
        "BACKLOG",
    ),
]


def _variant_file(tmp_path: Path, fixture_id: str, state_block: str) -> Path:
    path = tmp_path / fixture_id / "status.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_status(state_block), encoding="utf-8")
    return path


def _assert_dual_channel(stderr: str, error_key: str) -> None:
    assert f"error:   {error_key}" in stderr
    assert "message: " in stderr
    assert "remedy:  " in stderr


@pytest.fixture(autouse=True)
def _no_refusal_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the error emitter from recording refusals against a real project."""
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)


# ---------------------------------------------------------------------------
# Case 1 — read_section
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id,block,body,_state", _VARIANTS, ids=[v[0] for v in _VARIANTS])
def test_read_section_reads_each_writer_variant(
    fixture_id: str, block: str, body: str, _state: str
) -> None:
    assert read_section(_status(block), "State") == body


def test_read_section_reads_crlf_text() -> None:
    text = _status("## State\nDONE\n").replace("\n", "\r\n")
    assert read_section(text, "State") == "DONE"
    assert read_section(text, "Summary") == "Original summary."


def test_read_section_empty_section_reads_empty_not_the_next_heading() -> None:
    assert read_section(_status("## State\n"), "State") == ""


def test_read_section_absent_heading_is_none() -> None:
    assert read_section(_status("## State\nDONE\n"), "Artifacts") is None


def test_read_section_does_not_match_a_longer_heading() -> None:
    text = "## State Notes\nnot the state\n\n## State\nDONE\n"
    assert read_section(text, "State") == "DONE"
    assert read_section("## Status\nopen\n", "State") is None


def test_section_value_takes_the_first_value_line() -> None:
    text = _status("## State\n\n**DONE**\nsecond line\n")
    assert section_value(text, "State") == "DONE"
    assert section_value(text, "Artifacts") == ""


# ---------------------------------------------------------------------------
# Case 2 — read_state on fixture files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id,block,_body,state", _VARIANTS, ids=[v[0] for v in _VARIANTS])
def test_read_state_reads_each_variant_fixture(
    tmp_path: Path, fixture_id: str, block: str, _body: str, state: str
) -> None:
    assert read_state(_variant_file(tmp_path, fixture_id, block)) == state


def test_read_state_reads_the_unwritten_template_value(tmp_path: Path) -> None:
    path = tmp_path / "status.md"
    path.write_text(_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    assert read_state(path) == "BACKLOG"


def test_read_state_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_state(tmp_path / "absent" / "status.md") == ""


# ---------------------------------------------------------------------------
# Case 3 — set_section / set_sections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id,block,_body,_state", _VARIANTS, ids=[v[0] for v in _VARIANTS])
def test_set_section_changes_only_the_named_section(
    tmp_path: Path, fixture_id: str, block: str, _body: str, _state: str
) -> None:
    path = _variant_file(tmp_path, fixture_id, block)
    before = path.read_text(encoding="utf-8")

    set_section(path, "Summary", "New summary.")

    after = path.read_text(encoding="utf-8")
    assert after == before.replace(
        "## Summary\nOriginal summary.\n", "## Summary\nNew summary.\n"
    )


def test_set_section_keeps_the_blank_line_separator_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "status.md"
    path.write_text("## State\nWORKING\n\n\n\n## Summary\nx\n", encoding="utf-8")

    set_section(path, "State", "DONE")
    once = path.read_text(encoding="utf-8")
    set_section(path, "State", "DONE")

    assert once == "## State\nDONE\n\n\n\n## Summary\nx\n"
    assert path.read_text(encoding="utf-8") == once


def test_set_section_rewrites_an_inline_heading_to_two_lines(tmp_path: Path) -> None:
    path = _variant_file(tmp_path, "inline", "## State: blocked\n")
    set_section(path, "State", "DONE")
    text = path.read_text(encoding="utf-8")
    assert "## State\nDONE\n\n## Summary\nOriginal summary.\n" in text
    assert read_state(path) == "DONE"


def test_set_section_missing_section_writes_nothing(tmp_path: Path) -> None:
    path = _variant_file(tmp_path, "plain", "## State\nWORKING\n")
    before = path.read_bytes()
    with pytest.raises(TaskStatusError) as exc_info:
        set_section(path, "Artifacts", "x")
    assert exc_info.value.error_key == "task-status.missing-section"
    assert path.read_bytes() == before


def test_set_section_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(TaskStatusError) as exc_info:
        set_section(tmp_path / "status.md", "State", "DONE")
    assert exc_info.value.error_key == "task-status.missing-status-file"


def test_set_section_insert_after_creates_the_section(tmp_path: Path) -> None:
    path = tmp_path / "bug.md"
    path.write_text("# Bug\n\n## Status\nopen\n\n## Severity\nlow\n", encoding="utf-8")
    set_section(path, "Close Note", "fixed upstream", insert_after="Status")
    assert path.read_text(encoding="utf-8") == (
        "# Bug\n\n## Status\nopen\n\n## Close Note\nfixed upstream\n\n## Severity\nlow\n"
    )


def test_set_sections_all_or_nothing(tmp_path: Path) -> None:
    path = _variant_file(tmp_path, "plain", "## State\nWORKING\n")
    before = path.read_bytes()
    with pytest.raises(TaskStatusError):
        set_sections(path, {"State": "DONE", "Artifacts": "x"})
    assert path.read_bytes() == before

    set_sections(path, {"State": "DONE", "Blockers": "a blocker"})
    text = path.read_text(encoding="utf-8")
    assert "## State\nDONE\n\n## Summary\nOriginal summary.\n\n## Blockers\na blocker\n" in text


def test_replace_section_is_text_only() -> None:
    text = _status("## State\nWORKING\n")
    assert replace_section(text, "State", "  DONE  ") == text.replace("WORKING", "DONE")


# ---------------------------------------------------------------------------
# Case 4 — `read` subcommand agrees with --read-state; task-status.sh read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_id,block,_body,state", _VARIANTS, ids=[v[0] for v in _VARIANTS])
def test_read_subcommand_matches_read_state_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fixture_id: str,
    block: str,
    _body: str,
    state: str,
) -> None:
    path = _variant_file(tmp_path, fixture_id, block)

    assert main(["--read-state", str(path)]) == 0
    flag_out = capsys.readouterr().out
    assert main(["read", str(path)]) == 0
    sub_out = capsys.readouterr().out

    assert sub_out == flag_out == f"{state}\n"


def test_read_subcommand_by_project_and_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    kanban = tmp_path / "kanban"
    path = kanban / "projects" / "demo" / "tasks" / "T-1" / "status.md"
    path.parent.mkdir(parents=True)
    path.write_text(_status("## State\n\nDONE\n"), encoding="utf-8")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban))
    monkeypatch.delenv("KANBAN_ROOT", raising=False)

    assert main(["read", "--project", "demo", "--key", "T-1"]) == 0
    assert capsys.readouterr().out == "DONE\n"
    assert main(["read", "--project", "demo", "--key", "T-1", "--section", "Summary"]) == 0
    assert capsys.readouterr().out == "Original summary.\n"


def test_set_section_subcommand_writes_one_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _variant_file(tmp_path, "plain", "## State\nWORKING\n")
    before = path.read_text(encoding="utf-8")

    assert main(["set-section", str(path), "--section", "Needs Human", "--body", "YES"]) == 0

    assert path.read_text(encoding="utf-8") == before.replace(
        "## Needs Human\nno\n", "## Needs Human\nyes\n"
    )
    assert "status.md updated" in capsys.readouterr().out


def _run_task_status_sh(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PGAI_PROJECT_NAME"}
    return subprocess.run(
        ["bash", str(_TASK_STATUS_SH), *args],
        capture_output=True, text=True, timeout=60, env=env,
    )


def test_task_status_sh_read_mode_returns_the_state(tmp_path: Path) -> None:
    path = _variant_file(tmp_path, "blank-lines", "## State\n\n\nDONE\n")
    result = _run_task_status_sh("read", str(path))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "DONE\n"


# ---------------------------------------------------------------------------
# Case 5 — task-status.sh --help documents the read mode
# ---------------------------------------------------------------------------


def test_task_status_sh_help_documents_read_mode() -> None:
    result = _run_task_status_sh("--help")
    assert result.returncode == 0, result.stderr
    assert "read STATUS_FILE|--project P --key K" in " ".join(result.stdout.split())
    assert "--read-state" in result.stdout


def test_task_status_sh_usage_comment_documents_read_mode() -> None:
    header = _TASK_STATUS_SH.read_text(encoding="utf-8").split("set -euo pipefail")[0]
    assert "task-status.sh read" in header
    assert "task-status.sh set-section" in header


# ---------------------------------------------------------------------------
# Case 6 — subcommand refusals are dual-channel and write nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv_tail,error_key",
    [
        (["read"], "task-status.missing-target"),
        (["set-section", "--section", "Summary", "--body", "x"], "task-status.missing-target"),
        (["read", "{path}", "--project", "demo", "--key", "T-1"], "task-status.conflicting-target"),
        (["read", "{missing}"], "task-status.missing-status-file"),
        (["set-section", "{path}", "--section", "State", "--body", "DONE"], "task-status.state-via-set-section"),
        (["set-section", "{path}", "--body", "x"], "task-status.missing-section-arg"),
        (["set-section", "{path}", "--section", "Summary"], "task-status.missing-body"),
        (["set-section", "{path}", "--section", "Artifacts", "--body", "x"], "task-status.missing-section"),
        (["set-section", "{path}", "--section", "Needs Human", "--body", "maybe"], "task-status.invalid-needs-human"),
    ],
)
def test_subcommand_refusals_are_dual_channel_and_write_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv_tail: list[str],
    error_key: str,
) -> None:
    path = _variant_file(tmp_path, "plain", "## State\nWORKING\n")
    before = path.read_bytes()
    argv = [
        a.replace("{path}", str(path)).replace("{missing}", str(tmp_path / "nope" / "status.md"))
        for a in argv_tail
    ]

    assert main(argv) == 1

    _assert_dual_channel(capsys.readouterr().err, error_key)
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# Case 7 — ops/write delegates its status writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state,op", [("DONE", "close_item"), ("WONT-DO", "wontdo_item")])
def test_ops_terminal_state_writer_resets_three_sections(tmp_path: Path, state: str, op: str) -> None:
    path = tmp_path / "status.md"
    path.write_text(
        _status("## State\nBLOCKED\n")
        .replace("## Blockers\nnone\n", "## Blockers\nwaiting on X\n")
        .replace("## Needs Human\nno\n", "## Needs Human\nyes\n"),
        encoding="utf-8",
    )

    _write_task_terminal_state(path, state, op)

    assert path.read_text(encoding="utf-8") == _status(f"## State\n{state}\n")


def test_ops_terminal_state_writer_refuses_without_state(tmp_path: Path) -> None:
    path = tmp_path / "status.md"
    path.write_text("# Status\n\n## Summary\nx\n", encoding="utf-8")
    with pytest.raises(OpsError, match="no ## State heading"):
        _write_task_terminal_state(path, "DONE", "close_item")


def test_ops_intake_close_creates_close_note_after_status(tmp_path: Path) -> None:
    path = tmp_path / "BUG-0001-example.md"
    path.write_text("# Bug\n\n## Status\nopen\n\n## Severity\nlow\n", encoding="utf-8")

    _write_intake_close(path, "wont-do", "duplicate")

    assert path.read_text(encoding="utf-8") == (
        "# Bug\n\n## Status\nwont-do\n\n## Close Note\nduplicate\n\n## Severity\nlow\n"
    )
