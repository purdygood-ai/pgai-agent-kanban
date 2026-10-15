"""
test_task_status.py
====================
Tests for team/pgai_agent_kanban/task_status.py — the status-writer tool
that agents call instead of hand-editing a task's status.md.

Test coverage:

  1. Each of the six canonical states writes and round-trips correctly,
     case-insensitively.
  2. validate_state / validate_needs_human / validate_required_text return
     the canonical form or raise TaskStatusError with the dual-channel
     fields (error_key, message, remedy).
  3. Bad input at the library layer (write_task_status) writes NOTHING —
     asserted byte-for-byte against the pre-call file content.
  4. Bad input at the CLI layer (main()) — invalid --state, missing --key,
     missing --project — each emits all three dual-channel fields to
     stderr, returns 1, and leaves the target file byte-identical.
  5. Every unmanaged section (Artifacts, Blocked By Agent, Blocked Reason,
     Transient Retry, Next Recommended Step, Instruction Conflicts) survives
     byte-for-byte across a write that touches State/Summary/Blockers/Needs
     Human.
  6. The golden-path output matches the canonical template shape exactly —
     no extra blank lines introduced by the write (the growth bug the
     section-substitution regex is designed to avoid).
  7. A concurrent reader never observes a partially-written file.
  8. resolve_status_file matches the OpsContext tasks_dir layout.
  9. --help exits 0.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.task_status import (
    CANONICAL_NEEDS_HUMAN,
    CANONICAL_STATES,
    TaskStatusError,
    _atomic_write,
    _set_section_body,
    main,
    read_task_state,
    resolve_status_file,
    validate_needs_human,
    validate_required_text,
    validate_state,
    write_task_status,
)

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_TASK_STATUS_SCRIPT = _TEAM_DIR / "pgai_agent_kanban" / "task_status.py"
_TEMPLATE_PATH = _TEAM_DIR / "templates" / "task" / "task-status" / "status.md"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _template_text() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _make_status_file(tmp_path: Path, project: str = "demo", key: str = "T-1") -> Path:
    """Write the canonical template into a fresh task folder and return its path."""
    status_file = tmp_path / "kanban" / "projects" / project / "tasks" / key / "status.md"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(_template_text(), encoding="utf-8")
    return status_file


def _fully_populated_status_text() -> str:
    """A status.md carrying every narrative section with non-placeholder text —
    the fixture the byte-for-byte preservation acceptance criterion requires."""
    return (
        "# Status\n"
        "\n"
        "## Task\n"
        "CODER-20260911-999-example\n"
        "\n"
        "## Participant\n"
        "Claude\n"
        "\n"
        "## Role\n"
        "CODER\n"
        "\n"
        "## Model\n"
        "claude-example-5\n"
        "\n"
        "## State\n"
        "WORKING\n"
        "\n"
        "## Summary\n"
        "Original summary text.\n"
        "\n"
        "## Artifacts\n"
        "- src/example.py\n"
        "\n"
        "## Blockers\n"
        "none\n"
        "\n"
        "## Blocked By Agent\n"
        "none\n"
        "\n"
        "## Blocked Reason\n"
        "none\n"
        "\n"
        "## Transient Retry\n"
        "none\n"
        "\n"
        "## Needs Human\n"
        "no\n"
        "\n"
        "## Next Recommended Step\n"
        "Continue implementation.\n"
        "\n"
        "## Instruction Conflicts\n"
        "none\n"
    )


# ---------------------------------------------------------------------------
# Case 1 — Canonical states write and round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", CANONICAL_STATES)
def test_each_canonical_state_writes_and_reads_back(tmp_path: Path, state: str) -> None:
    status_file = _make_status_file(tmp_path)
    write_task_status(status_file, state=state)
    text = status_file.read_text(encoding="utf-8")
    m = re.search(r"^## State\s*\n(.*?)(\n+##|\Z)", text, flags=re.S | re.M)
    assert m is not None
    assert m.group(1).strip() == state


@pytest.mark.parametrize("state", CANONICAL_STATES)
def test_each_canonical_state_accepted_case_insensitively(
    tmp_path: Path, state: str
) -> None:
    status_file = _make_status_file(tmp_path)
    write_task_status(status_file, state=state.lower())
    text = status_file.read_text(encoding="utf-8")
    m = re.search(r"^## State\s*\n(.*?)(\n+##|\Z)", text, flags=re.S | re.M)
    assert m is not None
    assert m.group(1).strip() == state


def test_validate_state_returns_canonical_uppercase_form() -> None:
    assert validate_state("  done ") == "DONE"
    assert validate_state("wont-do") == "WONT-DO"


# ---------------------------------------------------------------------------
# Case 2 — Validators raise dual-channel TaskStatusError
# ---------------------------------------------------------------------------


def test_bad_state_raises_task_status_error_with_dual_channel_fields() -> None:
    with pytest.raises(TaskStatusError) as excinfo:
        validate_state("NOT-A-STATE")
    exc = excinfo.value
    assert exc.error_key == "task-status.invalid-state"
    assert "NOT-A-STATE" in exc.message
    assert "NOT-A-STATE" not in exc.remedy  # remedy names the fix, not the bad value
    assert "--state" in exc.remedy


def test_bad_needs_human_raises_with_dual_channel_fields() -> None:
    with pytest.raises(TaskStatusError) as excinfo:
        validate_needs_human("maybe")
    exc = excinfo.value
    assert exc.error_key == "task-status.invalid-needs-human"
    assert exc.message
    assert exc.remedy


@pytest.mark.parametrize("value", CANONICAL_NEEDS_HUMAN)
def test_validate_needs_human_accepts_canonical_values(value: str) -> None:
    assert validate_needs_human(value) == value


def test_validate_needs_human_is_case_insensitive() -> None:
    assert validate_needs_human("YES") == "yes"
    assert validate_needs_human(" No ") == "no"


def test_validate_required_text_strips_and_returns() -> None:
    assert validate_required_text("  demo  ", flag="project", error_key="x") == "demo"


@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_validate_required_text_raises_on_blank_or_none(bad_value) -> None:
    with pytest.raises(TaskStatusError) as excinfo:
        validate_required_text(bad_value, flag="project", error_key="task-status.missing-project")
    exc = excinfo.value
    assert exc.error_key == "task-status.missing-project"
    assert "--project" in exc.message
    assert "--project" in exc.remedy


# ---------------------------------------------------------------------------
# Case 3 — Bad input at the library layer writes nothing
# ---------------------------------------------------------------------------


def test_bad_state_writes_nothing(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path)
    before = status_file.read_bytes()
    with pytest.raises(TaskStatusError):
        write_task_status(status_file, state="NOT-A-STATE")
    after = status_file.read_bytes()
    assert before == after


def test_bad_needs_human_raises_and_writes_nothing(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path)
    before = status_file.read_bytes()
    with pytest.raises(TaskStatusError) as excinfo:
        write_task_status(status_file, state="DONE", needs_human="maybe")
    assert excinfo.value.error_key == "task-status.invalid-needs-human"
    after = status_file.read_bytes()
    assert before == after


def test_missing_status_file_raises_and_writes_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "status.md"
    with pytest.raises(TaskStatusError) as excinfo:
        write_task_status(missing, state="DONE")
    assert excinfo.value.error_key == "task-status.missing-status-file"
    assert not missing.exists()


def test_missing_section_raises_and_writes_nothing(tmp_path: Path) -> None:
    status_file = tmp_path / "status.md"
    # A status.md with a ## State heading but no ## Blockers heading at all.
    status_file.write_text("# Status\n\n## State\nBACKLOG\n", encoding="utf-8")
    before = status_file.read_bytes()
    with pytest.raises(TaskStatusError) as excinfo:
        write_task_status(status_file, state="DONE", blockers="none")
    assert excinfo.value.error_key == "task-status.missing-section"
    assert "Blockers" in excinfo.value.message
    after = status_file.read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# Case 4 — CLI-layer bad input: dual-channel + byte-identical file
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch: pytest.MonkeyPatch, kanban_root: Path, argv: list[str]) -> int:
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))
    monkeypatch.delenv("KANBAN_ROOT", raising=False)
    return main(argv)


def test_cli_invalid_state_dual_channel_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    before = status_file.read_bytes()

    rc = _run_cli(
        monkeypatch,
        tmp_path / "kanban",
        ["--project", "demo", "--key", "T-1", "--state", "NOPE"],
    )

    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "message:" in err
    assert "remedy:" in err
    assert "task-status.invalid-state" in err
    assert status_file.read_bytes() == before


def test_cli_missing_key_dual_channel_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    before = status_file.read_bytes()

    rc = _run_cli(
        monkeypatch,
        tmp_path / "kanban",
        ["--project", "demo", "--state", "DONE"],
    )

    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "message:" in err
    assert "remedy:" in err
    assert "task-status.missing-key" in err
    assert status_file.read_bytes() == before


def test_cli_missing_project_dual_channel_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    before = status_file.read_bytes()

    rc = _run_cli(
        monkeypatch,
        tmp_path / "kanban",
        ["--key", "T-1", "--state", "DONE"],
    )

    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "message:" in err
    assert "remedy:" in err
    assert "task-status.missing-project" in err
    assert status_file.read_bytes() == before


def test_cli_main_succeeds_and_prints_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")

    rc = _run_cli(
        monkeypatch,
        tmp_path / "kanban",
        ["--project", "demo", "--key", "T-1", "--state", "done"],
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "status.md updated" in out
    assert "DONE" in out
    text = status_file.read_text(encoding="utf-8")
    assert re.search(r"^## State\s*\nDONE\n", text, flags=re.M)


# ---------------------------------------------------------------------------
# Case 5 — Byte-for-byte preservation of unmanaged sections
# ---------------------------------------------------------------------------


def test_unrelated_sections_preserved_byte_for_byte(tmp_path: Path) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(_fully_populated_status_text(), encoding="utf-8")

    write_task_status(
        status_file,
        state="DONE",
        needs_human="no",
        summary="Finished the work.",
        blockers="none",
    )

    text = status_file.read_text(encoding="utf-8")

    # Managed fields changed as requested.
    assert re.search(r"^## State\s*\nDONE\n", text, flags=re.M)
    assert re.search(r"^## Summary\s*\nFinished the work\.\n", text, flags=re.M)

    # Every unmanaged section survives byte-for-byte.
    for heading, expected_body in (
        ("Task", "CODER-20260911-999-example"),
        ("Participant", "Claude"),
        ("Role", "CODER"),
        ("Model", "claude-example-5"),
        ("Artifacts", "- src/example.py"),
        ("Blocked By Agent", "none"),
        ("Blocked Reason", "none"),
        ("Transient Retry", "none"),
        ("Next Recommended Step", "Continue implementation."),
        ("Instruction Conflicts", "none"),
    ):
        pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)"
        m = re.search(pattern, text, flags=re.S | re.M)
        assert m is not None, f"section '## {heading}' missing after write"
        assert m.group(1).strip() == expected_body, (
            f"section '## {heading}' was disturbed: {m.group(1).strip()!r}"
        )


def test_writing_summary_does_not_disturb_blockers_or_needs_human(tmp_path: Path) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(_fully_populated_status_text(), encoding="utf-8")

    write_task_status(status_file, state="WORKING", summary="Updated summary only.")

    text = status_file.read_text(encoding="utf-8")
    assert re.search(r"^## Blockers\s*\nnone\n", text, flags=re.M)
    assert re.search(r"^## Needs Human\s*\nno\n", text, flags=re.M)
    assert re.search(r"^## Summary\s*\nUpdated summary only\.\n", text, flags=re.M)


def test_writing_blockers_does_not_disturb_summary(tmp_path: Path) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(_fully_populated_status_text(), encoding="utf-8")

    write_task_status(status_file, state="BLOCKED", blockers="Waiting on X.")

    text = status_file.read_text(encoding="utf-8")
    assert re.search(r"^## Summary\s*\nOriginal summary text\.\n", text, flags=re.M)
    assert re.search(r"^## Blockers\s*\nWaiting on X\.\n", text, flags=re.M)


# ---------------------------------------------------------------------------
# Case 6 — Golden-path output matches the canonical template shape exactly
# ---------------------------------------------------------------------------


def test_golden_path_output_matches_canonical_template_shape(tmp_path: Path) -> None:
    """Writing through the tool produces output that differs from the
    canonical template ONLY in the State/Summary/Blockers/Needs Human
    bodies -- no extra blank line is introduced anywhere else. This is the
    regression the growth bug (synthesizing a fresh newline on top of an
    already-captured separator) would trip."""
    status_file = _make_status_file(tmp_path)
    template = _template_text()

    write_task_status(
        status_file,
        state="DONE",
        needs_human="no",
        summary="All acceptance criteria met.",
        blockers="none",
    )

    written = status_file.read_text(encoding="utf-8")

    expected = template
    expected = re.sub(
        r"(^## State\s*\n).*?(\n+##)",
        lambda m: m.group(1) + "DONE" + m.group(2),
        expected,
        count=1,
        flags=re.S | re.M,
    )
    expected = re.sub(
        r"(^## Summary\s*\n).*?(\n+##)",
        lambda m: m.group(1) + "All acceptance criteria met." + m.group(2),
        expected,
        count=1,
        flags=re.S | re.M,
    )
    expected = re.sub(
        r"(^## Blockers\s*\n).*?(\n+##)",
        lambda m: m.group(1) + "none" + m.group(2),
        expected,
        count=1,
        flags=re.S | re.M,
    )
    expected = re.sub(
        r"(^## Needs Human\s*\n).*?(\n+##)",
        lambda m: m.group(1) + "no" + m.group(2),
        expected,
        count=1,
        flags=re.S | re.M,
    )

    assert written == expected


def test_golden_path_untouched_file_round_trips_identically(tmp_path: Path) -> None:
    """Writing the SAME state a file already has changes nothing byte-for-
    byte -- proves the substitution never grows the file on a repeated,
    otherwise-idempotent write. The template's own State body still carries
    its explanatory HTML comment on a fresh checkout, so the FIRST write
    through the tool legitimately changes bytes (it replaces the whole body,
    comment included, with the bare canonical value) -- that first write
    establishes the normalized baseline this test then proves is stable."""
    status_file = _make_status_file(tmp_path)
    write_task_status(status_file, state="BACKLOG")
    before = status_file.read_bytes()
    write_task_status(status_file, state="BACKLOG")
    after = status_file.read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# Case 7 — Concurrency: a reader never observes a partial write
# ---------------------------------------------------------------------------


def test_concurrent_reader_never_observes_partial_write(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path)
    # Normalize away the template's placeholder HTML comment under ## State
    # before racing reader/writer threads -- otherwise a reader that wins
    # the race against the very first write legitimately observes the raw
    # multi-line template body, which is a pre-existing-content shape this
    # test's strict single-token check is not exercising atomicity against.
    write_task_status(status_file, state="WORKING")

    stop = threading.Event()
    bad_reads: list[str] = []
    iterations = 200

    def writer() -> None:
        for i in range(iterations):
            state = "WORKING" if i % 2 == 0 else "BLOCKED"
            # A longer body on alternating writes makes a torn read (if the
            # write were not atomic) far more likely to be observable as a
            # malformed / truncated document.
            summary = ("x" * (200 if i % 2 == 0 else 5)) + f" iteration {i}"
            write_task_status(status_file, state=state, summary=summary)
        stop.set()

    def reader() -> None:
        while not stop.is_set():
            try:
                text = status_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                bad_reads.append(f"read failed: {exc}")
                continue
            # A complete file always has a State heading whose body is
            # exactly one recognized token, and always ends with the
            # Instruction Conflicts section -- checking both catches a torn
            # read at either end of the file.
            m = re.search(r"^## State\s*\n(.*?)(\n+##|\Z)", text, flags=re.S | re.M)
            if m is None or m.group(1).strip() not in ("WORKING", "BLOCKED", "BACKLOG"):
                bad_reads.append(f"malformed/torn State section: {text!r}")
            if "## Instruction Conflicts" not in text:
                bad_reads.append(f"truncated file (missing tail section): {text!r}")

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    writer_thread.start()
    writer_thread.join(timeout=30)
    reader_thread.join(timeout=30)

    assert not bad_reads, f"Observed {len(bad_reads)} torn/partial read(s): {bad_reads[:3]}"


# ---------------------------------------------------------------------------
# Case 8 — resolve_status_file path layout
# ---------------------------------------------------------------------------


def test_resolve_status_file_matches_tasks_dir_layout(tmp_path: Path) -> None:
    ctx = OpsContext(kanban_root=tmp_path)
    result = resolve_status_file("demo", "T-1", ctx=ctx)
    assert result == tmp_path / "projects" / "demo" / "tasks" / "T-1" / "status.md"


# ---------------------------------------------------------------------------
# Case 9 — --help exits 0
# ---------------------------------------------------------------------------


def test_help_exits_zero() -> None:
    # The script imports pgai_agent_kanban.* by absolute package name, so a
    # direct subprocess invocation needs team/ (the package's own-tree root)
    # on PYTHONPATH -- the same resolution team/scripts/lib/pp_run_ops.sh
    # provides for the bash wrapper's real invocations.
    env = dict(**os.environ)
    env["PYTHONPATH"] = str(_TEAM_DIR) + (
        (":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
    )
    result = subprocess.run(
        [sys.executable, str(_TASK_STATUS_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"--help exited with non-zero code {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "task_status" in result.stdout
    assert "--state" in result.stdout


# ---------------------------------------------------------------------------
# Internal helper sanity (kept small; the public surface is covered above)
# ---------------------------------------------------------------------------


def test_set_section_body_raises_on_missing_heading() -> None:
    with pytest.raises(TaskStatusError) as excinfo:
        _set_section_body("# Status\n\n## State\nBACKLOG\n", "Summary", "x")
    assert excinfo.value.error_key == "task-status.missing-section"


def test_atomic_write_replaces_target_contents(tmp_path: Path) -> None:
    target = tmp_path / "status.md"
    target.write_text("old", encoding="utf-8")
    _atomic_write(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize(
    ("fixture", "heading", "expected"),
    [
        (
            "## State\nBACKLOG\n\n## Summary\n\n## Blockers\nnone\n\n## Needs Human\nno\n",
            "Summary",
            "## State\nBACKLOG\n\n## Summary\nnew\n\n## Blockers\nnone\n\n## Needs Human\nno\n",
        ),
        (
            "## State\n\n## Summary\nx\n\n## Blockers\nnone\n",
            "State",
            "## State\nnew\n\n## Summary\nx\n\n## Blockers\nnone\n",
        ),
        ("## State\n## Summary\nx\n", "State", "## State\nnew\n## Summary\nx\n"),
        ("## Summary\nx\n\n## Blockers\n", "Blockers", "## Summary\nx\n\n## Blockers\nnew\n"),
    ],
    ids=["empty-summary", "empty-state", "empty-no-blank-line", "empty-last-section"],
)
def test_set_section_body_fills_empty_section_and_keeps_the_rest(
    fixture: str, heading: str, expected: str
) -> None:
    once = _set_section_body(fixture, heading, "new")
    assert once == expected, f"filling an empty ## {heading} must leave every other section unchanged"
    assert _set_section_body(once, heading, "new") == expected, "a repeated write must not grow the file"


def test_write_task_status_fills_empty_summary_without_losing_blockers(tmp_path: Path) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(
        "## State\nBACKLOG\n\n## Summary\n\n## Blockers\nnone\n\n## Needs Human\nno\n", encoding="utf-8"
    )
    write_task_status(status_file, state="WORKING", summary="started")
    assert status_file.read_text(encoding="utf-8") == (
        "## State\nWORKING\n\n## Summary\nstarted\n\n## Blockers\nnone\n\n## Needs Human\nno\n"
    )


# ---------------------------------------------------------------------------
# The State reader: reads back what the writer wrote, liberal on hand edits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", CANONICAL_STATES)
def test_read_task_state_reads_back_every_written_state(tmp_path: Path, state: str) -> None:
    status_file = _make_status_file(tmp_path)
    write_task_status(status_file, state=state)
    assert read_task_state(status_file) == state


@pytest.mark.parametrize(
    "text, expected",
    [
        ("## State\nDONE\n\n## Summary\nx\n", "DONE"),
        ("## State\n\n\n  wont-do  \n\n## Summary\nx\n", "WONT-DO"),
        ("## State \t\nBacklog\n", "BACKLOG"),
        ("## State\n\n## Summary\nDONE\n", ""),
        ("## Summary\nDONE\n", ""),
    ],
    ids=["canonical", "blank-lines-and-padding", "trailing-heading-space", "empty-body", "no-heading"],
)
def test_read_task_state_takes_the_value_beneath_the_heading(
    tmp_path: Path, text: str, expected: str
) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text(text, encoding="utf-8")
    assert read_task_state(status_file) == expected, (
        f"## State must read {expected!r} from:\n{text}"
    )


def test_read_task_state_missing_file_reads_empty(tmp_path: Path) -> None:
    assert read_task_state(tmp_path / "absent" / "status.md") == ""


def test_cli_read_state_prints_value_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text("# Status\n\n## State\nDONE\n\n## Summary\nx\n", encoding="utf-8")
    before = status_file.read_bytes()

    rc = main(["--read-state", str(status_file)])

    assert rc == 0
    assert capsys.readouterr().out == "DONE\n"
    assert status_file.read_bytes() == before


def test_cli_read_state_refuses_a_write_flag_alongside(tmp_path: Path) -> None:
    status_file = tmp_path / "status.md"
    status_file.write_text("## State\nBACKLOG\n", encoding="utf-8")
    before = status_file.read_bytes()
    with pytest.raises(SystemExit) as excinfo:
        main(["--read-state", str(status_file), "--state", "DONE"])
    assert excinfo.value.code == 2
    assert status_file.read_bytes() == before
