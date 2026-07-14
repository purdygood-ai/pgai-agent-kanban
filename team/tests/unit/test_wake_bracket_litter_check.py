"""
test_wake_bracket_litter_check.py
==================================
Behavioral fixtures for the wake-bracket /tmp litter snapshot/diff/report
implemented via shared helpers in scripts/lib/temp.sh and dispatched from
both wake provider siblings (scripts/wake/claude.sh and
scripts/wake/codex.sh) through scripts/lib/wake_bracket.sh.

Acceptance criteria exercised:

  (1) Structural gate: both siblings source scripts/lib/wake_bracket.sh;
      neither sibling contains an inline copy of the extracted bracket
      logic (pre-dispatch snapshot block or post-session check block).

  (2) Session litter: a session that creates /tmp/tester_fixture.log
      → status.md gains a `## Temp Litter` section naming the file exactly;
      a one-line wake log entry is present; the file SURVIVES on disk
      (load-bearing negative — report-only, no deletion).

  (3) Clean session: a session that creates no bare /tmp entries
      → no `## Temp Litter` section appears in status.md.

  (4) Pre-existing entry: a /tmp file created BEFORE the session start epoch
      → not flagged (age filter prevents false positives on entries that
        were already there).

  (5) Allowlist: entries created DURING the session that match systemd-*,
      tmux-*, or pytest-of-* → not flagged.

  (6) Both terminal paths: post-check runs after DONE and after BLOCKED
      (not only one of the two); the DONE/BLOCKED guard lives in the
      shared lib (wake_bracket.sh); WONT-DO is excluded.

  (7) Robustness: forced check failure (snapshot file missing/unreadable)
      does NOT change the task's exit code or terminal state.

All behavioral tests exercise the helper functions directly via bash, using
synthetic environments that never touch the live kanban root.
"""

from __future__ import annotations

import os
import pathlib
import textwrap
import time

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths relative to team/ (pytest cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_TEMP_SH = pathlib.Path("scripts/lib/temp.sh")
_WAKE_BRACKET_SH = pathlib.Path("scripts/lib/wake_bracket.sh")
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")

# Markers used to locate the bracket sections.
_PRE_DISPATCH_MARKER = "Pre-dispatch /tmp litter snapshot"
_POST_SESSION_MARKER = "Post-session /tmp litter check"

# Expressions that identify the extracted function bodies; these must appear
# in the shared lib and be absent from both siblings.
_SNAPSHOT_BODY_PATTERN = "wake_tmp_litter_take_snapshot"
_TERMINAL_STATE_GUARD = 'DONE" || "$_final_state" == "BLOCKED'


# ---------------------------------------------------------------------------
# (1) Structural gate: siblings source wake_bracket.sh; no inline copies
# ---------------------------------------------------------------------------


def test_siblings_source_wake_bracket_lib() -> None:
    """
    Both wake provider siblings must source scripts/lib/wake_bracket.sh.
    The shared lib is the single home for the bracket functions; sourcing it
    is the structural proof that neither sibling carries its own copy.
    """
    for sibling in (_CLAUDE_SH, _CODEX_SH):
        content = sibling.read_text(encoding="utf-8")
        assert "wake_bracket.sh" in content, (
            f"{sibling} does not source wake_bracket.sh. "
            "Both siblings must source scripts/lib/wake_bracket.sh."
        )


def test_siblings_have_no_inline_snapshot_call() -> None:
    """
    Neither sibling may contain a direct call to wake_tmp_litter_take_snapshot.
    The raw helper is invoked exclusively via wake_bracket_pre_dispatch in
    the shared lib.  Any direct hit in a sibling is an incomplete extraction.
    """
    for sibling in (_CLAUDE_SH, _CODEX_SH):
        content = sibling.read_text(encoding="utf-8")
        assert _SNAPSHOT_BODY_PATTERN not in content, (
            f"{sibling} contains a direct call to {_SNAPSHOT_BODY_PATTERN!r}. "
            "The raw helper must be invoked only via wake_bracket_pre_dispatch "
            "in scripts/lib/wake_bracket.sh — the inline copy was not fully removed."
        )


def test_lib_contains_snapshot_call() -> None:
    """
    The shared lib (wake_bracket.sh) must call wake_tmp_litter_take_snapshot
    inside wake_bracket_pre_dispatch.
    """
    content = _WAKE_BRACKET_SH.read_text(encoding="utf-8")
    assert _SNAPSHOT_BODY_PATTERN in content, (
        f"{_WAKE_BRACKET_SH} does not call {_SNAPSHOT_BODY_PATTERN!r}. "
        "wake_bracket_pre_dispatch must invoke the snapshot helper."
    )


def test_siblings_have_no_inline_terminal_state_guard() -> None:
    """
    Neither sibling may contain the DONE/BLOCKED terminal-state guard expression
    inline.  That guard lives exclusively in wake_bracket_post_session in the
    shared lib.  Any hit in a sibling is an incomplete extraction.
    """
    for sibling in (_CLAUDE_SH, _CODEX_SH):
        content = sibling.read_text(encoding="utf-8")
        assert _TERMINAL_STATE_GUARD not in content, (
            f"{sibling} contains the terminal-state guard {_TERMINAL_STATE_GUARD!r} "
            "inline.  The guard must live only in wake_bracket_post_session in "
            "scripts/lib/wake_bracket.sh."
        )


def test_lib_contains_terminal_state_guard() -> None:
    """
    The shared lib must contain the DONE/BLOCKED terminal-state guard — confirming
    that wake_bracket_post_session embeds the guard rather than relying on the
    caller to gate its invocation.
    """
    content = _WAKE_BRACKET_SH.read_text(encoding="utf-8")
    assert _TERMINAL_STATE_GUARD in content, (
        f"Terminal-state guard {_TERMINAL_STATE_GUARD!r} not found in "
        f"{_WAKE_BRACKET_SH}.  wake_bracket_post_session must contain the "
        "DONE/BLOCKED guard so callers need not replicate it."
    )


def test_both_terminal_paths_guarded_in_lib() -> None:
    """
    The shared lib's post-session function must reference both DONE and BLOCKED
    terminal states in its guard expression, and must NOT reference WONT-DO.
    """
    content = _WAKE_BRACKET_SH.read_text(encoding="utf-8")
    assert '"DONE"' in content, (
        f"DONE terminal state not referenced in {_WAKE_BRACKET_SH}."
    )
    assert '"BLOCKED"' in content, (
        f"BLOCKED terminal state not referenced in {_WAKE_BRACKET_SH}."
    )
    assert '"WONT-DO"' not in content, (
        f"WONT-DO terminal state must not be included in the post-session guard "
        f"in {_WAKE_BRACKET_SH} — the check must be skipped for WONT-DO."
    )


def test_markers_still_present_in_siblings() -> None:
    """
    Regression guard: the section-header comment markers for both bracket
    regions must still be present in both siblings (the comments remain as
    section labels even though the implementation lives in the shared lib).
    """
    for sibling in (_CLAUDE_SH, _CODEX_SH):
        content = sibling.read_text(encoding="utf-8")
        assert _PRE_DISPATCH_MARKER in content, (
            f"Pre-dispatch marker '{_PRE_DISPATCH_MARKER}' missing from {sibling}."
        )
        assert _POST_SESSION_MARKER in content, (
            f"Post-session marker '{_POST_SESSION_MARKER}' missing from {sibling}."
        )


# ---------------------------------------------------------------------------
# Helper: build a minimal task status.md
# ---------------------------------------------------------------------------

def _write_status_md(path: pathlib.Path, state: str = "WORKING") -> None:
    """Write a minimal status.md at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(f"""\
            # Status

            ## Task
            TEST-TASK-001

            ## State
            {state}

            ## Summary
            test

            ## Blockers
            none

            ## Needs Human
            no
        """),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Helper: bash preamble that sources temp.sh with a controlled temp root
# ---------------------------------------------------------------------------

def _temp_sh_preamble(tmp_path: pathlib.Path) -> str:
    """Return a bash preamble that sources temp.sh with a safe temp root."""
    temp_root = tmp_path / "pgai_tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    return textwrap.dedent(f"""\
        export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_root}"
        source {_TEMP_SH!s}
    """)


# ---------------------------------------------------------------------------
# Helper: run wake_tmp_litter_take_snapshot via bash
# ---------------------------------------------------------------------------

def _run_take_snapshot(
    tmp_path: pathlib.Path,
    snapshot_file: pathlib.Path,
    session_epoch: int | None = None,
) -> "BashResult":
    from tests.unit.shell_harness import run_bash
    epoch_arg = str(session_epoch) if session_epoch is not None else ""
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        wake_tmp_litter_take_snapshot "{snapshot_file}" {epoch_arg}
    """)
    return run_bash(tmp_path, script)


# ---------------------------------------------------------------------------
# Helper: run wake_tmp_litter_check_and_report via bash
# ---------------------------------------------------------------------------

def _run_check_and_report(
    tmp_path: pathlib.Path,
    snapshot_file: pathlib.Path,
    task_status_path: pathlib.Path,
    task_id: str,
    project_name: str,
    kanban_root: pathlib.Path,
) -> "BashResult":
    log_capture = tmp_path / "log_capture.txt"
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        # Minimal log() stub that writes to a capture file
        log() {{ echo "$*" >> "{log_capture}"; }}
        wake_tmp_litter_check_and_report \\
            "{snapshot_file}" \\
            "{task_status_path}" \\
            "{task_id}" \\
            "log" \\
            "{project_name}" \\
            "{kanban_root}"
    """)
    result = run_bash(tmp_path, script)
    return result


# ---------------------------------------------------------------------------
# (2) Session litter: new /tmp entry is reported; file survives
# ---------------------------------------------------------------------------


def test_session_litter_reported_in_status_md(tmp_path: pathlib.Path) -> None:
    """
    Fixture (2a): a session that creates /tmp/tester_fixture.log during the
    session → status.md gains a `## Temp Litter` section naming the file.
    """
    task_id = "TEST-LITTER-001"
    kanban_root = tmp_path / "kanban"
    project_name = "test-project"
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "litter_snap" / "pre_dispatch"

    _write_status_md(task_status)

    # Pre-session snapshot (before the "litter" file exists).
    epoch_before = int(time.time()) - 1
    result = _run_take_snapshot(tmp_path, snapshot_file, epoch_before)
    assert result.returncode == 0, f"take_snapshot failed: {result.stderr}"
    assert snapshot_file.exists(), "Snapshot file was not created."

    # Simulate a /tmp entry created during the session.
    # anti-pattern-allowlist: 2 (justification: intentional bare /tmp fixture — the wake-bracket litter check only fires on bare /tmp entries; verifying its behavior requires planting one)
    litter_file = pathlib.Path("/tmp/tester_fixture.log")
    try:
        litter_file.write_text("test litter\n", encoding="utf-8")
        # Post-session check.
        log_capture = tmp_path / "log_capture.txt"
        script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_capture}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" \\
                "{task_status}" \\
                "{task_id}" \\
                "log" \\
                "{project_name}" \\
                "{kanban_root}"
        """))
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        # Verify status.md has ## Temp Litter section naming the file.
        status_text = task_status.read_text(encoding="utf-8")
        assert "## Temp Litter" in status_text, (
            "status.md does not contain '## Temp Litter' section.\n"
            f"status.md contents:\n{status_text}"
        )
        assert "/tmp/tester_fixture.log" in status_text, (
            "status.md does not name /tmp/tester_fixture.log in the Temp Litter section.\n"
            f"status.md contents:\n{status_text}"
        )

        # Verify wake log line is present.
        assert log_capture.exists(), "Log capture file was not written."
        log_text = log_capture.read_text(encoding="utf-8")
        assert "tester_fixture.log" in log_text, (
            "Wake log does not mention the litter file.\n"
            f"Log capture:\n{log_text}"
        )

    finally:
        # CRITICAL: the file must survive — report-only, never deleted.
        assert litter_file.exists(), (
            "VIOLATION: /tmp/tester_fixture.log was deleted by the litter check. "
            "The check must be report-only and must NEVER delete files."
        )
        litter_file.unlink(missing_ok=True)


def test_litter_file_survives_on_disk(tmp_path: pathlib.Path) -> None:
    """
    Fixture (2b, load-bearing negative): the litter file must survive on disk
    after the check runs. The check is report-only. Deletion is a hard failure.
    """
    task_id = "TEST-LITTER-SURVIVE"
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    _write_status_md(task_status)

    epoch_before = int(time.time()) - 1
    _run_take_snapshot(tmp_path, snapshot_file, epoch_before)

    # anti-pattern-allowlist: 2 (justification: intentional bare /tmp fixture — the wake-bracket litter check only fires on bare /tmp entries; verifying its behavior requires planting one)
    litter_file = pathlib.Path("/tmp/tester_fixture_survive.log")
    try:
        litter_file.write_text("survive test\n", encoding="utf-8")
        log_cap = tmp_path / "lc.txt"
        script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" "proj" "{tmp_path / 'kanban'}"
        """))
        run_bash(tmp_path, script)

        # File must still exist.
        assert litter_file.exists(), (
            "VIOLATION: litter file was deleted by the check. "
            "wake_tmp_litter_check_and_report must never delete /tmp files."
        )
    finally:
        litter_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# (3) Clean session: no new /tmp entries → no ## Temp Litter section
# ---------------------------------------------------------------------------


def test_clean_session_produces_no_litter_section(tmp_path: pathlib.Path) -> None:
    """
    Fixture (3): a session that creates no bare /tmp entries → status.md
    does NOT gain a `## Temp Litter` section.
    """
    task_id = "TEST-CLEAN-001"
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    _write_status_md(task_status)

    # Snapshot AFTER any existing /tmp contents are captured.
    epoch_now = int(time.time()) + 1  # 1s in the future so age-filter kills pre-existing
    _run_take_snapshot(tmp_path, snapshot_file, epoch_now)

    # No new /tmp file created; run the post-session check.
    log_cap = tmp_path / "lc.txt"
    script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        log() {{ echo "$*" >> "{log_cap}"; }}
        wake_tmp_litter_check_and_report \\
            "{snapshot_file}" "{task_status}" "{task_id}" "log" "proj" "{tmp_path / 'kanban'}"
    """))
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

    status_text = task_status.read_text(encoding="utf-8")
    assert "## Temp Litter" not in status_text, (
        "status.md gained a '## Temp Litter' section during a clean session.\n"
        f"status.md contents:\n{status_text}"
    )


# ---------------------------------------------------------------------------
# (4) Pre-existing /tmp entry: not flagged (age filter)
# ---------------------------------------------------------------------------


def test_pre_existing_tmp_entry_not_flagged(tmp_path: pathlib.Path) -> None:
    """
    Fixture (4): a /tmp file created BEFORE the session start epoch is not
    flagged — the age filter excludes entries that predated the session.
    """
    task_id = "TEST-PRE-EXIST"
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    _write_status_md(task_status)

    # anti-pattern-allowlist: 2 (justification: intentional bare /tmp fixture — the wake-bracket litter check only fires on bare /tmp entries; verifying its behavior requires planting one)
    pre_existing = pathlib.Path("/tmp/tester_pre_existing_check.log")
    try:
        pre_existing.write_text("pre-existing\n", encoding="utf-8")
        # Snapshot AFTER the pre-existing file is created; epoch set AFTER
        # the file's mtime so the age filter will exclude it.
        epoch_after_file = int(time.time()) + 2
        _run_take_snapshot(tmp_path, snapshot_file, epoch_after_file)

        log_cap = tmp_path / "lc.txt"
        script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" \\
                "proj" "{tmp_path / 'kanban'}"
        """))
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        # The pre-existing file was captured in the snapshot; it should not appear
        # in status.md (the snapshot records it, so comm excludes it).
        status_text = task_status.read_text(encoding="utf-8")
        # Verify it was either captured in snapshot OR the age-filter excluded it.
        # Either way, no Temp Litter section should reference it.
        if "## Temp Litter" in status_text:
            assert "tester_pre_existing_check.log" not in status_text, (
                "Pre-existing /tmp entry was incorrectly flagged as litter.\n"
                f"status.md:\n{status_text}"
            )
    finally:
        pre_existing.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# (5) Allowlist entries created during the session → not flagged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", ["systemd-private-test", "tmux-42-test", "pytest-of-rocky"])
def test_allowlist_entries_not_flagged(tmp_path: pathlib.Path, pattern: str) -> None:
    """
    Fixture (5): entries matching the allowlist (systemd-*, tmux-*, pytest-of-*)
    created during the session → not flagged by the litter check.
    """
    task_id = f"TEST-ALLOWLIST-{pattern[:8].upper()}"
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    _write_status_md(task_status)

    epoch_before = int(time.time()) - 1
    _run_take_snapshot(tmp_path, snapshot_file, epoch_before)

    allowlist_entry = pathlib.Path(f"/tmp/{pattern}")
    try:
        allowlist_entry.mkdir(exist_ok=True)

        log_cap = tmp_path / "lc.txt"
        script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" \\
                "proj" "{tmp_path / 'kanban'}"
        """))
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        status_text = task_status.read_text(encoding="utf-8")
        if "## Temp Litter" in status_text:
            assert pattern not in status_text, (
                f"Allowlist entry '{pattern}' was incorrectly flagged as litter.\n"
                f"status.md:\n{status_text}"
            )
    finally:
        if allowlist_entry.exists():
            allowlist_entry.rmdir()


# ---------------------------------------------------------------------------
# (6) Both terminal paths: DONE and BLOCKED
# ---------------------------------------------------------------------------


def test_post_check_runs_on_done_path(tmp_path: pathlib.Path) -> None:
    """
    Fixture (6a): the post-session litter check runs on the DONE terminal path.
    The guard lives in wake_bracket_post_session in the shared lib; verify it
    references DONE.
    """
    lib_content = _WAKE_BRACKET_SH.read_text(encoding="utf-8")
    assert '"DONE"' in lib_content, (
        f"wake_bracket_post_session in {_WAKE_BRACKET_SH} does not reference "
        "the DONE terminal state in its guard."
    )


def test_post_check_runs_on_blocked_path(tmp_path: pathlib.Path) -> None:
    """
    Fixture (6b): the post-session litter check runs on the BLOCKED terminal path.
    The guard lives in wake_bracket_post_session in the shared lib; verify it
    references BLOCKED and not WONT-DO.
    """
    lib_content = _WAKE_BRACKET_SH.read_text(encoding="utf-8")
    assert '"BLOCKED"' in lib_content, (
        f"wake_bracket_post_session in {_WAKE_BRACKET_SH} does not reference "
        "the BLOCKED terminal state in its guard."
    )
    assert '"WONT-DO"' not in lib_content, (
        f"wake_bracket_post_session in {_WAKE_BRACKET_SH} references WONT-DO — "
        "the check must be skipped for WONT-DO terminal state."
    )


# ---------------------------------------------------------------------------
# (7) Robustness: forced check failure leaves task exit path unchanged
# ---------------------------------------------------------------------------


def test_missing_snapshot_does_not_change_exit_path(tmp_path: pathlib.Path) -> None:
    """
    Fixture (7): when the snapshot file is missing (check cannot run),
    wake_tmp_litter_check_and_report returns 0 and leaves status.md unchanged.
    This proves fire-and-forget — the check cannot add a failure mode.
    """
    task_id = "TEST-ROBUST-001"
    task_status = tmp_path / "status.md"
    _write_status_md(task_status, state="DONE")
    status_before = task_status.read_text(encoding="utf-8")

    # Pass a snapshot path that does not exist.
    missing_snapshot = tmp_path / "nonexistent" / "snapshot"

    log_cap = tmp_path / "lc.txt"
    kanban_root = tmp_path / "kanban"
    script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        log() {{ echo "$*" >> "{log_cap}"; }}
        wake_tmp_litter_check_and_report \\
            "{missing_snapshot}" \\
            "{task_status}" \\
            "{task_id}" \\
            "log" \\
            "proj" \\
            "{kanban_root}"
    """))
    result = run_bash(tmp_path, script)

    # Must return 0 regardless of the missing snapshot.
    assert result.returncode == 0, (
        "wake_tmp_litter_check_and_report returned non-zero when snapshot was missing. "
        "Fire-and-forget discipline requires it always returns 0 to callers.\n"
        f"stderr: {result.stderr}"
    )

    # status.md must be unchanged — no sections were appended.
    status_after = task_status.read_text(encoding="utf-8")
    assert "## Temp Litter" not in status_after, (
        "status.md gained a Temp Litter section despite the snapshot being missing."
    )


def test_check_failure_is_fire_and_forget(tmp_path: pathlib.Path) -> None:
    """
    Fixture (7b): even if wake_tmp_litter_check_and_report is wrapped in a
    subshell that errors internally, the outer call still returns 0.

    Validates the (set +e; ...) guard inside the function.
    """
    task_status = tmp_path / "status.md"
    _write_status_md(task_status, state="BLOCKED")
    status_before = task_status.read_text(encoding="utf-8")

    # Intentionally corrupt: pass snapshot=/dev/null which has no readable
    # content in the expected format — the function should degrade gracefully.
    log_cap = tmp_path / "lc.txt"
    kanban_root = tmp_path / "kanban"
    script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        set -euo pipefail
        log() {{ echo "$*" >> "{log_cap}"; }}
        wake_tmp_litter_check_and_report \\
            "/dev/null" \\
            "{task_status}" \\
            "TEST-ROBUST-002" \\
            "log" \\
            "proj" \\
            "{kanban_root}"
        echo "EXIT_CODE=0"
    """))
    result = run_bash(tmp_path, script)

    # The script must reach "EXIT_CODE=0" even with strict mode (-euo pipefail).
    assert "EXIT_CODE=0" in result.stdout, (
        "Script aborted before completion — check function propagated non-zero exit "
        "under set -euo pipefail.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.returncode == 0, (
        f"Script exited {result.returncode}; expected 0.\n"
        f"stderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


def test_take_snapshot_creates_file(tmp_path: pathlib.Path) -> None:
    """wake_tmp_litter_take_snapshot creates the snapshot file at the given path."""
    snapshot_file = tmp_path / "litter_snap" / "snapshot"
    result = _run_take_snapshot(tmp_path, snapshot_file)
    assert result.returncode == 0, f"take_snapshot failed: {result.stderr}"
    assert snapshot_file.exists(), "Snapshot file was not created."


def test_take_snapshot_has_epoch_header(tmp_path: pathlib.Path) -> None:
    """Snapshot file starts with an epoch= header line."""
    snapshot_file = tmp_path / "snap" / "snap"
    epoch = 1700000000
    _run_take_snapshot(tmp_path, snapshot_file, epoch)
    lines = snapshot_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"epoch={epoch}", (
        f"First line of snapshot was '{lines[0]}'; expected 'epoch={epoch}'."
    )


def test_take_snapshot_refuses_bare_tmp_path(tmp_path: pathlib.Path) -> None:
    """wake_tmp_litter_take_snapshot refuses to write directly to /tmp."""
    # anti-pattern-allowlist: 2 (justification: intentional bare /tmp fixture — the wake-bracket litter check only fires on bare /tmp entries; verifying its behavior requires planting one)
    snapshot_file = pathlib.Path("/tmp/bad_snapshot_file")
    result = _run_take_snapshot(tmp_path, snapshot_file)
    # Should fail (non-zero) when the parent is /tmp itself.
    assert result.returncode != 0, (
        "take_snapshot should refuse to write a snapshot directly under /tmp "
        "(parent must be a subdirectory of /tmp to be safe)."
    )
    assert not snapshot_file.exists(), (
        "Snapshot was written directly to /tmp — this violates the safety invariant."
    )


def test_overwatch_actions_log_written_on_litter(tmp_path: pathlib.Path) -> None:
    """
    When litter is found, wake_tmp_litter_check_and_report appends a line to
    <kanban_root>/projects/<project_name>/overwatch/actions.log.
    """
    task_id = "TEST-OW-LOG"
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    kanban_root = tmp_path / "kanban"
    project_name = "test-project"
    _write_status_md(task_status)

    epoch_before = int(time.time()) - 1
    _run_take_snapshot(tmp_path, snapshot_file, epoch_before)

    # anti-pattern-allowlist: 2 (justification: intentional bare /tmp fixture — the wake-bracket litter check only fires on bare /tmp entries; verifying its behavior requires planting one)
    litter_file = pathlib.Path("/tmp/tester_ow_log.log")
    try:
        litter_file.write_text("ow log test\n", encoding="utf-8")
        log_cap = tmp_path / "lc.txt"
        script = (_temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" \\
                "{project_name}" "{kanban_root}"
        """))
        run_bash(tmp_path, script)

        ow_log = kanban_root / "projects" / project_name / "overwatch" / "actions.log"
        assert ow_log.exists(), (
            f"OVERWATCH actions.log was not created at {ow_log}.\n"
            "wake_tmp_litter_check_and_report must write one line to actions.log "
            "when litter is found."
        )
        ow_text = ow_log.read_text(encoding="utf-8")
        assert "check-bare-tmp-litter" in ow_text, (
            "OVERWATCH actions.log does not contain 'check-bare-tmp-litter'.\n"
            f"actions.log:\n{ow_text}"
        )
        assert task_id in ow_text, (
            f"OVERWATCH actions.log does not name the task ID '{task_id}'.\n"
            f"actions.log:\n{ow_text}"
        )
    finally:
        litter_file.unlink(missing_ok=True)
