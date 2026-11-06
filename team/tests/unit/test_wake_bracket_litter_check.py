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

  (2) Session litter: a session that creates a bare-/tmp file
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

  (8) Every name: the snapshot round-trips a basename holding a line feed,
      and both the snapshot and the scan enumerate dot-named entries — a
      pre-existing entry of either kind is never reported, a new one is
      reported exactly once.

  (9) OVERWATCH backstop: scripts/lib/overwatch-checks/check-bare-tmp-litter.sh
      reads the same snapshot records and keys names the same way — an
      entry in any snapshot is never flagged, LF-named or dot-named entries
      included; a new one is reported once and its dedup line round-trips;
      plain dedup lines still suppress; the caller's shell options survive.

All behavioral tests exercise the helper functions directly via bash, using
synthetic environments that never touch the live kanban root.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import textwrap
import time
import uuid

import pytest

from pgai_agent_kanban.lib.text import one_line
from tests.unit.shell_harness import run_bash


# ---------------------------------------------------------------------------
# Real bare-/tmp fixture helper
# ---------------------------------------------------------------------------
#
# wake_tmp_litter_check_and_report (scripts/lib/temp.sh) scans REAL top-level
# bare /tmp entries and deliberately EXCLUDES both the framework temp root
# and pytest's own tmp_path (which nests under /tmp/pytest-of-*, itself on
# the check's own allowlist) from that scan. A fixture proving the check
# detects real litter therefore cannot route through either sanctioned
# mechanism without testing nothing — landing directly under bare /tmp is
# this module's subject matter, not a bypass of the temp-path contract.
# team/scripts/lint_test_anti_patterns.py carries a narrow, filename-scoped
# allowance for exactly this reasoning (_AP2_BARE_TMP_SUBJECT_MATTER_WHITELIST).
#
# Every call embeds a fresh uuid4 suffix so concurrent test runs (and repeat
# runs against an already-dirty host) never plant the same path — the
# collision a fixed literal name would otherwise make possible.
def _bare_tmp_probe(prefix: str) -> pathlib.Path:
    """Return a fresh, real bare-/tmp path for a litter-check fixture."""
    return pathlib.Path(f"/tmp/{prefix}-{uuid.uuid4().hex[:10]}")


# ---------------------------------------------------------------------------
# Paths relative to team/ (pytest cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_TEMP_SH = pathlib.Path("scripts/lib/temp.sh")
_WAKE_BRACKET_SH = pathlib.Path("scripts/lib/wake_bracket.sh")
# Both provider shims (scripts/wake/claude.sh, scripts/wake/codex.sh) source
# the one shared entry body, lib/wake_entry.sh, which sources wake_bracket.sh.
_CLAUDE_SH = pathlib.Path("scripts/lib/wake_entry.sh")
_CODEX_SH = pathlib.Path("scripts/lib/wake_entry.sh")

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
            f"{sibling} does not source wake_bracket.sh. " "Both siblings must source scripts/lib/wake_bracket.sh."
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
    assert '"DONE"' in content, f"DONE terminal state not referenced in {_WAKE_BRACKET_SH}."
    assert '"BLOCKED"' in content, f"BLOCKED terminal state not referenced in {_WAKE_BRACKET_SH}."
    assert '"WONT-DO"' not in content, (
        f"WONT-DO terminal state must not be included in the post-session guard "
        f"in {_WAKE_BRACKET_SH} — the check must be skipped for WONT-DO."
    )


def test_markers_still_present_in_siblings() -> None:
    """
    Regression guard: the section-header comment markers for both bracket
    regions must still be present in both siblings (the comments remain as
    section labels even though the implementation lives in the shared lib).

    v2.9.20-engine-decomposition split both siblings' own copies of these
    two markers across two different shared-driver stage files, sourced by
    both claude.sh and codex.sh: the pre-dispatch snapshot lives in the
    invoke stage (wake_driver_invoke.sh) and the post-session check lives
    one stage later, in the finalize stage (wake_driver_finalize.sh).
    Both claude.sh and codex.sh were moved onto the shared driver.
    """
    pre_dispatch_sources = (pathlib.Path("scripts/lib/wake_driver_invoke.sh"),)
    for sibling in pre_dispatch_sources:
        content = sibling.read_text(encoding="utf-8")
        assert _PRE_DISPATCH_MARKER in content, f"Pre-dispatch marker '{_PRE_DISPATCH_MARKER}' missing from {sibling}."

    post_session_sources = (pathlib.Path("scripts/lib/wake_driver_finalize.sh"),)
    for sibling in post_session_sources:
        content = sibling.read_text(encoding="utf-8")
        assert _POST_SESSION_MARKER in content, f"Post-session marker '{_POST_SESSION_MARKER}' missing from {sibling}."


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
    extra_env: dict[str, str] | None = None,
) -> BashResult:
    from tests.unit.shell_harness import run_bash

    epoch_arg = str(session_epoch) if session_epoch is not None else ""
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        wake_tmp_litter_take_snapshot "{snapshot_file}" {epoch_arg}
    """)
    return run_bash(tmp_path, script, extra_env=extra_env)


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
    extra_env: dict[str, str] | None = None,
) -> BashResult:
    """Run the check with a log() stub that appends each call's message to
    ``tmp_path / "log_capture.txt"`` NUL-terminated, so a caller can count
    the calls and see any line break inside a message (_read_log_calls).
    *extra_env* is layered onto the bash environment (a locale, say)."""
    log_capture = tmp_path / "log_capture.txt"
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        # Minimal log() stub: one NUL-terminated record per call
        log() {{ printf '%s\\0' "$*" >> "{log_capture}"; }}
        wake_tmp_litter_check_and_report \\
            "{snapshot_file}" \\
            "{task_status_path}" \\
            "{task_id}" \\
            "log" \\
            "{project_name}" \\
            "{kanban_root}"
    """)
    result = run_bash(tmp_path, script, extra_env=extra_env)
    return result


def _read_log_calls(tmp_path: pathlib.Path) -> list[str]:
    """The messages _run_check_and_report's log() stub received, one per call."""
    raw = (tmp_path / "log_capture.txt").read_text(encoding="utf-8")
    assert raw.endswith("\0"), f"log capture is not NUL-terminated: {raw!r}"
    return raw[:-1].split("\0")


def _appended_litter_section(status_text: str) -> list[str]:
    """The lines the check appended to status.md, from its own heading on."""
    _, sep, tail = status_text.partition("\n## Temp Litter\n")
    assert sep, f"status.md has no '## Temp Litter' section:\n{status_text}"
    return ["## Temp Litter", *tail.splitlines()]


def _remedy_argv(tmp_path: pathlib.Path, remedy: str) -> list[str]:
    """The argument vector bash builds from *remedy*, without running it."""
    script = 'eval "set -- $REMEDY"; printf \'%s\\0\' "$@"'
    result = run_bash(tmp_path, script, extra_env={"REMEDY": remedy})
    assert result.returncode == 0, f"bash could not parse remedy {remedy!r}: {result.stderr}"
    assert result.stdout.endswith("\0"), f"remedy {remedy!r} parsed to nothing"
    return result.stdout[:-1].split("\0")


# ---------------------------------------------------------------------------
# (2) Session litter: new /tmp entry is reported; file survives
# ---------------------------------------------------------------------------


def test_session_litter_reported_in_status_md(tmp_path: pathlib.Path) -> None:
    """
    Fixture (2a): a session that creates a bare-/tmp file during the
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
    litter_file = _bare_tmp_probe("tester_fixture")
    litter_path_str = str(litter_file)
    try:
        litter_file.write_text("test litter\n", encoding="utf-8")
        # Post-session check.
        log_capture = tmp_path / "log_capture.txt"
        script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_capture}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" \\
                "{task_status}" \\
                "{task_id}" \\
                "log" \\
                "{project_name}" \\
                "{kanban_root}"
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        # Verify status.md has ## Temp Litter section naming the file.
        status_text = task_status.read_text(encoding="utf-8")
        assert "## Temp Litter" in status_text, (
            "status.md does not contain '## Temp Litter' section.\n" f"status.md contents:\n{status_text}"
        )
        assert litter_path_str in status_text, (
            f"status.md does not name {litter_path_str} in the Temp Litter section.\n"
            f"status.md contents:\n{status_text}"
        )

        # Verify wake log line is present.
        assert log_capture.exists(), "Log capture file was not written."
        log_text = log_capture.read_text(encoding="utf-8")
        assert litter_file.name in log_text, "Wake log does not mention the litter file.\n" f"Log capture:\n{log_text}"

    finally:
        # CRITICAL: the file must survive — report-only, never deleted.
        assert litter_file.exists(), (
            f"VIOLATION: {litter_path_str} was deleted by the litter check. "
            "The check must be report-only and must NEVER delete files."
        )
        litter_file.unlink(missing_ok=True)


def test_litter_report_carries_a_runnable_removal_remedy(tmp_path: pathlib.Path) -> None:
    """
    The litter check must not merely narrate what it found and leave it in
    place forever -- it must also emit a runnable command that removes the
    entry. This fixture plants a bare-/tmp file, drives the teardown path,
    and asserts both status.md and the wake log line carry a `rm -f`
    command naming the exact litter path. The file itself must still
    survive the check (report-only, per the (2)/(2b) fixtures above);
    running the emitted remedy is what actually clears it, deliberately
    left to the operator or the next session rather than auto-deleted here.
    """
    task_id = "TEST-LITTER-REMEDY"
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    _write_status_md(task_status)

    epoch_before = int(time.time()) - 1
    _run_take_snapshot(tmp_path, snapshot_file, epoch_before)

    litter_file = _bare_tmp_probe("tester_fixture_remedy")
    try:
        litter_file.write_text("remedy test\n", encoding="utf-8")
        log_cap = tmp_path / "lc.txt"
        script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" \\
                "proj" "{tmp_path / 'kanban'}"
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        litter_path_str = str(litter_file)
        expected_remedy = f"rm -f {litter_path_str}"

        status_text = task_status.read_text(encoding="utf-8")
        assert expected_remedy in status_text, (
            f"status.md '## Temp Litter' section does not carry the runnable "
            f"remedy {expected_remedy!r}.\nstatus.md contents:\n{status_text}"
        )

        log_text = log_cap.read_text(encoding="utf-8")
        assert expected_remedy in log_text, (
            f"wake log line does not carry the runnable remedy {expected_remedy!r}.\n" f"Log capture:\n{log_text}"
        )

        # The remedy is genuinely runnable and genuinely removes the entry --
        # exercised here directly rather than assumed from the string shape.
        remedy_result = run_bash(tmp_path, expected_remedy)
        assert (
            remedy_result.returncode == 0
        ), f"remedy command {expected_remedy!r} did not exit 0: {remedy_result.stderr}"
        assert not litter_file.exists(), "remedy command ran but the litter file still exists on disk."
    finally:
        litter_file.unlink(missing_ok=True)


@pytest.mark.parametrize("separator", [" ", "\t", "\n"], ids=["space", "tab", "newline"])
def test_remedy_removes_exactly_the_entry_whose_name_holds_whitespace(tmp_path: pathlib.Path, separator: str) -> None:
    """
    The remedy for an entry named '<probe><whitespace>data.csv' is one
    physical line, identical in status.md and the log line, and bash reads
    it as rm -f with that one path. Run from an operator cwd, it removes the
    entry and nothing else: a pre-existing /tmp file named like the first
    whitespace-split fragment and a cwd file named like the second survive.
    """
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    operator_cwd = tmp_path / "operator_cwd"
    operator_cwd.mkdir()
    cwd_fragment = operator_cwd / "data.csv"
    cwd_fragment.write_text("operator file\n", encoding="utf-8")
    _write_status_md(task_status)

    pre_existing = _bare_tmp_probe("tester_fixture_remedy_ws")
    litter_file = pathlib.Path(f"{pre_existing}{separator}data.csv")
    try:
        pre_existing.write_text("not litter\n", encoding="utf-8")
        _run_take_snapshot(tmp_path, snapshot_file, int(time.time()) - 1)
        litter_file.write_text("litter\n", encoding="utf-8")
        result = _run_check_and_report(
            tmp_path, snapshot_file, task_status, "TEST-REMEDY-WS", "proj", tmp_path / "kanban"
        )
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        section = _appended_litter_section(task_status.read_text(encoding="utf-8"))
        remedy_lines = [line for line in section if line.startswith("remedy: ")]
        assert len(remedy_lines) == 1, f"Expected one 'remedy:' line; got {remedy_lines!r}"
        remedy = remedy_lines[0].removeprefix("remedy: ")
        assert "\n" not in remedy and "\r" not in remedy and "\t" not in remedy, remedy

        log_calls = _read_log_calls(tmp_path)
        assert len(log_calls) == 1, f"Expected one log call; got {log_calls!r}"
        assert log_calls[0].endswith(
            f" — remedy: {remedy}"
        ), f"log line does not carry the status.md remedy {remedy!r}: {log_calls[0]!r}"

        argv = _remedy_argv(tmp_path, remedy)
        assert argv[:2] == ["rm", "-f"], argv
        assert str(litter_file) in argv[2:], f"remedy does not name {str(litter_file)!r} as one word: {argv!r}"
        assert str(pre_existing) not in argv and "data.csv" not in argv, argv

        # Run it as printed only when it names nothing but this test's entry:
        # an entry another process created in the same window is reported too,
        # and removing it is that process's business, not this test's.
        if argv[2:] == [str(litter_file)]:
            remedy_result = run_bash(tmp_path, remedy, cwd=operator_cwd)
            assert remedy_result.returncode == 0, f"remedy {remedy!r} failed: {remedy_result.stderr}"
            assert not litter_file.exists(), f"remedy {remedy!r} left the litter entry in place."
            assert pre_existing.exists(), f"remedy {remedy!r} deleted the pre-existing {pre_existing}."
            assert cwd_fragment.exists(), f"remedy {remedy!r} deleted the operator's {cwd_fragment}."
    finally:
        litter_file.unlink(missing_ok=True)
        pre_existing.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "suffix",
    ["-plain", "\n## Summary\ninjected", "\r## State\rDONE", "\tname\t## Blockers"],
    ids=["plain", "newline", "cr", "tab"],
)
def test_litter_section_and_summary_are_one_line_per_entry(tmp_path: pathlib.Path, suffix: str) -> None:
    """
    Whatever an entry name holds, the appended '## Temp Litter' section has
    one 'created ...' line naming it (a tab, CR or LF flattened to a space),
    no appended line but the heading starts with '## ', and the log
    function is called once with a one-line summary. A plain name's line is
    the same text as before flattening existed.
    """
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    _write_status_md(task_status)
    _run_take_snapshot(tmp_path, snapshot_file, int(time.time()) - 1)

    litter_file = pathlib.Path(f"{_bare_tmp_probe('tester_fixture_one_line')}{suffix}")
    shown = " ".join(str(litter_file).split())
    try:
        litter_file.write_text("litter\n", encoding="utf-8")
        result = _run_check_and_report(
            tmp_path, snapshot_file, task_status, "TEST-ONE-LINE", "proj", tmp_path / "kanban"
        )
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        status_text = task_status.read_text(encoding="utf-8")
        section = _appended_litter_section(status_text)
        headings = [line for line in section if line.startswith("## ")]
        assert headings == ["## Temp Litter"], f"The appended section planted headings {headings!r}:\n{status_text}"
        assert all(line.startswith(("created ", "remedy: ")) for line in section[1:]), section
        assert section.count(f"created {shown} — SOP Temporary File Convention") == 1, section
        assert status_text.count("\n## Summary\n") == 1, status_text

        log_calls = _read_log_calls(tmp_path)
        assert len(log_calls) == 1, f"Expected one log call; got {log_calls!r}"
        assert not any(ch in log_calls[0] for ch in "\n\r\t"), f"summary is not one line: {log_calls[0]!r}"
        assert shown in log_calls[0], f"summary does not name {shown!r}: {log_calls[0]!r}"
    finally:
        litter_file.unlink(missing_ok=True)


@pytest.mark.parametrize("locale", ["C", "C.UTF-8"])
def test_litter_report_shows_a_name_holding_a_no_break_space_and_a_lf_as_one_line_does(
    tmp_path: pathlib.Path, locale: str
) -> None:
    """
    A new entry whose name holds U+00A0 and a LF is shown on its status.md
    'created ...' line and in the wake-log summary exactly as one_line
    flattens it -- the no-break space collapsed to a space like the LF --
    whether bash runs under the C locale or a UTF-8 one.
    """
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    env = {"LC_ALL": locale}
    _write_status_md(task_status)
    _run_take_snapshot(tmp_path, snapshot_file, int(time.time()) - 1, extra_env=env)

    litter_file = pathlib.Path(f"{_bare_tmp_probe('tester_fixture_nbsp')}\u00a0name\nsecond")
    shown = one_line(str(litter_file))
    try:
        litter_file.write_text("litter\n", encoding="utf-8")
        result = _run_check_and_report(
            tmp_path, snapshot_file, task_status, "TEST-NBSP", "proj", tmp_path / "kanban", extra_env=env
        )
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        section = _appended_litter_section(task_status.read_text(encoding="utf-8"))
        own = [line for line in section if litter_file.name.split("\u00a0")[0] in line and line.startswith("created ")]
        assert own == [f"created {shown} — SOP Temporary File Convention"], f"LC_ALL={locale}: {section!r}"

        log_calls = _read_log_calls(tmp_path)
        assert len(log_calls) == 1, f"Expected one log call; got {log_calls!r}"
        log_names, _, _ = log_calls[0].partition(" — remedy: ")
        assert log_names.count(shown) == 1, f"LC_ALL={locale}: summary does not show {shown!r}: {log_names!r}"
        assert "\u00a0" not in log_names, f"LC_ALL={locale}: summary kept the no-break space: {log_names!r}"
    finally:
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

    litter_file = _bare_tmp_probe("tester_fixture_survive")
    try:
        litter_file.write_text("survive test\n", encoding="utf-8")
        log_cap = tmp_path / "lc.txt"
        script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" "proj" "{tmp_path / 'kanban'}"
        """)
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
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        log() {{ echo "$*" >> "{log_cap}"; }}
        wake_tmp_litter_check_and_report \\
            "{snapshot_file}" "{task_status}" "{task_id}" "log" "proj" "{tmp_path / 'kanban'}"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

    status_text = task_status.read_text(encoding="utf-8")
    assert "## Temp Litter" not in status_text, (
        "status.md gained a '## Temp Litter' section during a clean session.\n" f"status.md contents:\n{status_text}"
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

    pre_existing = _bare_tmp_probe("tester_pre_existing_check")
    try:
        pre_existing.write_text("pre-existing\n", encoding="utf-8")
        # Snapshot AFTER the pre-existing file is created; epoch set AFTER
        # the file's mtime so the age filter will exclude it.
        epoch_after_file = int(time.time()) + 2
        _run_take_snapshot(tmp_path, snapshot_file, epoch_after_file)

        log_cap = tmp_path / "lc.txt"
        script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" \\
                "proj" "{tmp_path / 'kanban'}"
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        # The pre-existing file was captured in the snapshot; it should not appear
        # in status.md (the snapshot records it, so comm excludes it).
        status_text = task_status.read_text(encoding="utf-8")
        # Verify it was either captured in snapshot OR the age-filter excluded it.
        # Either way, no Temp Litter section should reference it.
        if "## Temp Litter" in status_text:
            assert pre_existing.name not in status_text, (
                "Pre-existing /tmp entry was incorrectly flagged as litter.\n" f"status.md:\n{status_text}"
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

    # _bare_tmp_probe appends a uuid4 suffix, so the directory still starts
    # with the allowlisted prefix (systemd-/tmux-/pytest-of-, which the
    # production check matches via a glob prefix, not an exact name) while
    # remaining collision-safe across concurrent runs of this parametrized test.
    allowlist_entry = _bare_tmp_probe(pattern)
    try:
        allowlist_entry.mkdir(exist_ok=True)

        log_cap = tmp_path / "lc.txt"
        script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" \\
                "proj" "{tmp_path / 'kanban'}"
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        status_text = task_status.read_text(encoding="utf-8")
        if "## Temp Litter" in status_text:
            assert pattern not in status_text, (
                f"Allowlist entry '{pattern}' was incorrectly flagged as litter.\n" f"status.md:\n{status_text}"
            )
    finally:
        if allowlist_entry.exists():
            allowlist_entry.rmdir()


# ---------------------------------------------------------------------------
# (8) Every name: LF-holding and dot-named entries, pre-existing and new
# ---------------------------------------------------------------------------


def _probe_named(kind: str, role: str) -> pathlib.Path:
    """A fresh bare-/tmp probe whose basename is of *kind*, tagged with *role*."""
    match kind:
        case "lf-inside":
            return pathlib.Path(f"{_bare_tmp_probe(f'tester_fixture_lf_{role}')}\nsecond")
        case "lf-trailing":
            return pathlib.Path(f"{_bare_tmp_probe(f'tester_fixture_lf_{role}')}\n")
        case "dot":
            return _bare_tmp_probe(f".tester_fixture_dot_{role}")
        case "dot-lf":
            return pathlib.Path(f"{_bare_tmp_probe(f'.tester_fixture_dot_{role}')}\nsecond")
    raise ValueError(kind)


@pytest.mark.parametrize("kind", ["lf-inside", "lf-trailing", "dot", "dot-lf"])
def test_pre_existing_entry_skipped_and_new_entry_reported_once(tmp_path: pathlib.Path, kind: str) -> None:
    """
    A pre-existing entry of *kind*, touched after the snapshot so the age
    filter cannot hide it, is named nowhere: not on a status.md line, not in
    the log line, not in the remedy, not in the actions.log record. A new
    entry of the same kind is named exactly once in each, as a plain name is.
    """
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    kanban_root = tmp_path / "kanban"
    _write_status_md(task_status)

    session_epoch = int(time.time()) - 5
    pre_existing = _probe_named(kind, "pre")
    new_entry = _probe_named(kind, "new")
    pre_shown = " ".join(str(pre_existing).split())
    new_shown = " ".join(str(new_entry).split())
    try:
        pre_existing.write_text("present before dispatch\n", encoding="utf-8")
        result = _run_take_snapshot(tmp_path, snapshot_file, session_epoch)
        assert result.returncode == 0, f"take_snapshot failed: {result.stderr}"
        os.utime(pre_existing, (session_epoch + 60, session_epoch + 60))
        new_entry.write_text("created during the session\n", encoding="utf-8")

        result = _run_check_and_report(tmp_path, snapshot_file, task_status, "TEST-NAMES", "proj", kanban_root)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        section = _appended_litter_section(task_status.read_text(encoding="utf-8"))
        created = [line.removeprefix("created ").removesuffix(" — SOP Temporary File Convention") for line in section]
        assert created.count(new_shown) == 1, f"new entry {new_shown!r} not listed once: {section!r}"
        assert pre_shown not in created, f"pre-existing entry {pre_shown!r} reported: {section!r}"

        log_calls = _read_log_calls(tmp_path)
        assert len(log_calls) == 1, f"Expected one log call; got {log_calls!r}"
        log_names, _, _ = log_calls[0].partition(" — remedy: ")
        assert log_names.count(new_shown) == 1, log_calls[0]
        assert pre_shown not in log_calls[0], log_calls[0]

        remedy = next(line for line in section if line.startswith("remedy: ")).removeprefix("remedy: ")
        argv = _remedy_argv(tmp_path, remedy)
        assert argv.count(str(new_entry)) == 1, f"remedy does not name {str(new_entry)!r} once: {argv!r}"
        assert str(pre_existing) not in argv, f"remedy would remove the pre-existing entry: {argv!r}"

        ow_log = kanban_root / "projects" / "proj" / "overwatch" / "actions.log"
        reason = ow_log.read_text(encoding="utf-8").rstrip("\n").split("\t")[5]
        assert reason.count(new_shown) == 1, reason
        assert pre_shown not in reason, reason
    finally:
        new_entry.unlink(missing_ok=True)
        pre_existing.unlink(missing_ok=True)


def test_dot_named_framework_temp_root_not_flagged(tmp_path: pathlib.Path) -> None:
    """
    A dot-named framework temp root created during the session is excluded
    like any other framework temp root, while a dot-named sibling is reported.
    """
    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    _write_status_md(task_status)
    _run_take_snapshot(tmp_path, snapshot_file, int(time.time()) - 1)

    fw_root = _bare_tmp_probe(".tester_fixture_fw_root")
    sibling = _bare_tmp_probe(".tester_fixture_dot_sibling")
    try:
        fw_root.mkdir()
        sibling.write_text("litter\n", encoding="utf-8")
        log_capture = tmp_path / "log_capture.txt"
        script = textwrap.dedent(f"""\
            source {_TEMP_SH!s}
            export PGAI_AGENT_KANBAN_TEMP_DIR="{fw_root}"
            log() {{ printf '%s\\0' "$*" >> "{log_capture}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "TEST-DOT-FW" "log" "proj" "{tmp_path / 'kanban'}"
        """)
        result = run_bash(tmp_path, script)
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        section = _appended_litter_section(task_status.read_text(encoding="utf-8"))
        assert f"created {sibling} — SOP Temporary File Convention" in section, section
        assert not any(str(fw_root) in line for line in section), f"framework temp root reported: {section!r}"
    finally:
        sibling.unlink(missing_ok=True)
        if fw_root.exists():
            fw_root.rmdir()


# ---------------------------------------------------------------------------
# (9) OVERWATCH backstop: check-bare-tmp-litter.sh agrees with the bracket
# ---------------------------------------------------------------------------

_BARE_TMP_LITTER_CHECK_SH = pathlib.Path("scripts/lib/overwatch-checks/check-bare-tmp-litter.sh")
_PROJECT_PATHS_SH = pathlib.Path("scripts/lib/project_paths.sh")
_OW_PROJECT = "proj"


def _ow_snapshot_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Where the wake writes, and the OVERWATCH check discovers, a snapshot for
    the swept project under _temp_sh_preamble's temp root in the default layout
    (its project.cfg sets no temp_dir)."""
    task_dir = tmp_path / "pgai_tmp" / "projects" / _OW_PROJECT / "tasks" / "TEST-OW-BACKSTOP"
    return task_dir / "litter" / "pre_dispatch_tmp_snapshot"


def _ow_project_cfg(tmp_path: pathlib.Path, temp_dir: str | None = None) -> pathlib.Path:
    """Write the swept project's project.cfg in the throwaway kanban root,
    setting [project] temp_dir only when *temp_dir* is given."""
    cfg = tmp_path / "kanban" / "projects" / _OW_PROJECT / "project.cfg"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[project]", f"project_name = {_OW_PROJECT}"]
    if temp_dir is not None:
        lines.append(f"temp_dir = {temp_dir}")
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg


def _ow_live_install_env(tmp_path: pathlib.Path, temp_root: pathlib.Path) -> dict[str, str]:
    """The throwaway kanban root (tmp_path/kanban) and *temp_root*, set for the
    swept project; the harness scripts unset the two pre-resolved names."""
    kanban_root = tmp_path / "kanban"
    return {
        "KANBAN_ROOT": str(kanban_root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root),
        "OVERWATCH_PROJECT": _OW_PROJECT,
    }


def _ow_wake_snapshot(tmp_path: pathlib.Path, task_id: str) -> pathlib.Path:
    """Take *task_id*'s pre-dispatch snapshot the way the wake does: load the
    swept project's config with pp_load_config, which exports PP_TEMP_DIR, then
    call wake_bracket_pre_dispatch. Returns the snapshot path the wake printed."""
    script = (
        "unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT PP_TEMP_DIR\n"
        f"source {_TEMP_SH!s}\n"
        f"source {_PROJECT_PATHS_SH!s}\n"
        f"pp_load_config {_OW_PROJECT} || exit 1\n"
        "log() { printf '%s\\n' \"$*\" >&2; }\n"
        f"source {_WAKE_BRACKET_SH!s}\n"
        f"wake_bracket_pre_dispatch {task_id}\n"
    )
    result = run_bash(tmp_path, script, extra_env=_ow_live_install_env(tmp_path, tmp_path / "pgai_tmp"))
    assert result.returncode == 0, f"wake snapshot failed: {result.stderr}"
    snapshot = pathlib.Path(result.stdout.strip())
    assert snapshot.is_file(), f"wake printed {result.stdout!r}, no snapshot there: {result.stderr}"
    return snapshot


def _ow_state_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """The throwaway kanban root's OVERWATCH state dir, created on first call."""
    state_dir = tmp_path / "kanban" / "projects" / _OW_PROJECT / "overwatch"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _run_overwatch_litter_check(
    tmp_path: pathlib.Path, *, temp_root: pathlib.Path | None = None, before: str = "", after: str = ""
) -> BashResult:
    """Source check-bare-tmp-litter.sh and run one sweep of it under the sweep
    runner's strict mode, against a throwaway kanban root (tmp_path/kanban)
    and *temp_root* (default: _temp_sh_preamble's tmp_path/pgai_tmp). The
    swept project gets a default-layout project.cfg unless one is already
    written (_ow_project_cfg). The live-install names are set or cleared
    together; *before* and *after* are bash run around the sweep in the same
    shell."""
    _ow_state_dir(tmp_path)
    if not (tmp_path / "kanban" / "projects" / _OW_PROJECT / "project.cfg").exists():
        _ow_project_cfg(tmp_path)
    temp_root = temp_root or tmp_path / "pgai_tmp"
    script = (
        "set -euo pipefail\n"
        "unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT\n"
        f"{before}\n"
        f"source {_BARE_TMP_LITTER_CHECK_SH!s}\n"
        "overwatch_check_bare_tmp_litter\n"
        f"{after}\n"
    )
    return run_bash(tmp_path, script, extra_env=_ow_live_install_env(tmp_path, temp_root))


def _ow_litter_found_targets(tmp_path: pathlib.Path) -> list[str]:
    """Every report:litter-found record's target in the throwaway actions.log, in order."""
    ow_log = _ow_state_dir(tmp_path) / "actions.log"
    if not ow_log.exists():
        return []
    fields = [line.split("\t") for line in ow_log.read_text(encoding="utf-8").splitlines()]
    return [f[2] for f in fields if len(f) == 6 and f[3] == "report:litter-found"]


def _ow_litter_found_reason(tmp_path: pathlib.Path, target: str) -> str:
    """The reason field of the report:litter-found record for *target*."""
    ow_log = _ow_state_dir(tmp_path) / "actions.log"
    for line in ow_log.read_text(encoding="utf-8").splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[3] == "report:litter-found" and f[2] == target:
            return f[5]
    raise AssertionError(f"no report:litter-found record for {target!r}")


@pytest.mark.parametrize("kind", ["lf-inside", "lf-trailing", "dot", "dot-lf"])
def test_overwatch_check_skips_snapshot_entries_and_reports_new_ones_once(tmp_path: pathlib.Path, kind: str) -> None:
    """
    The OVERWATCH backstop never flags an entry of *kind* recorded in a
    pre-dispatch snapshot, though it was touched after the snapshot. A new
    entry of the same kind is reported exactly as a plain new entry is, and
    once only: its dedup line round-trips, so a second sweep is silent.
    """
    session_epoch = int(time.time()) - 5
    pre_existing = _probe_named(kind, "ow_pre")
    new_entry = _probe_named(kind, "ow_new")
    plain_entry = _bare_tmp_probe("tester_fixture_ow_plain")
    try:
        pre_existing.write_text("present before dispatch\n", encoding="utf-8")
        result = _run_take_snapshot(tmp_path, _ow_snapshot_path(tmp_path), session_epoch)
        assert result.returncode == 0, f"take_snapshot failed: {result.stderr}"
        os.utime(pre_existing, (session_epoch + 60, session_epoch + 60))
        new_entry.write_text("created after dispatch\n", encoding="utf-8")
        plain_entry.write_text("created after dispatch\n", encoding="utf-8")

        result = _run_overwatch_litter_check(tmp_path)
        assert result.returncode == 0, f"check failed: {result.stderr}"
        targets = _ow_litter_found_targets(tmp_path)
        assert one_line(str(pre_existing)) not in targets, f"snapshot entry {pre_existing!r} reported: {targets!r}"
        assert targets.count(one_line(str(new_entry))) == 1, f"{new_entry!r} not reported once: {targets!r}"
        assert targets.count(str(plain_entry)) == 1, f"{plain_entry!r} not reported once: {targets!r}"
        new_reason = _ow_litter_found_reason(tmp_path, one_line(str(new_entry)))
        plain_reason = _ow_litter_found_reason(tmp_path, str(plain_entry))
        assert new_reason.replace(one_line(str(new_entry)), "<entry>") == plain_reason.replace(
            str(plain_entry), "<entry>"
        ), (new_reason, plain_reason)

        name = new_entry.name.encode()
        expected_line = b"esc=" + name.replace(b"\\", b"\\\\").replace(b"\n", b"\\n") if b"\n" in name else name
        dedup = (_ow_state_dir(tmp_path) / "litter-reported.txt").read_bytes().split(b"\n")
        own = [line for line in dedup if name.split(b"\n")[0] in line]
        assert own == [expected_line], f"dedup lines for {new_entry.name!r}: {own!r}"

        result = _run_overwatch_litter_check(tmp_path)
        assert result.returncode == 0, f"second check failed: {result.stderr}"
        assert _ow_litter_found_targets(tmp_path) == targets, "a second sweep re-reported an entry"
    finally:
        for path in (pre_existing, new_entry, plain_entry):
            path.unlink(missing_ok=True)


@pytest.mark.parametrize("prefix", ["tester_fixture_ow_legacy", ".tester_fixture_ow_legacy", "esc=tester_fixture_ow"])
def test_overwatch_check_honours_plain_dedup_lines(tmp_path: pathlib.Path, prefix: str) -> None:
    """
    A plain-name line already in litter-reported.txt -- written before the
    file could hold escaped lines -- still suppresses re-reporting its entry,
    including a name that happens to start with `esc=`.
    """
    entry = _bare_tmp_probe(prefix)
    dedup_file = _ow_state_dir(tmp_path) / "litter-reported.txt"
    try:
        _run_take_snapshot(tmp_path, _ow_snapshot_path(tmp_path), int(time.time()) - 5)
        entry.write_text("reported by an earlier sweep\n", encoding="utf-8")
        dedup_file.write_text(f"{entry.name}\n", encoding="utf-8")

        result = _run_overwatch_litter_check(tmp_path)
        assert result.returncode == 0, f"check failed: {result.stderr}"
        assert str(entry) not in _ow_litter_found_targets(tmp_path), f"{entry} re-reported despite its dedup line"
        assert dedup_file.read_text(encoding="utf-8").count(entry.name) == 1, dedup_file.read_text(encoding="utf-8")
    finally:
        entry.unlink(missing_ok=True)


def test_overwatch_check_excludes_a_dot_named_framework_temp_root(tmp_path: pathlib.Path) -> None:
    """
    A dot-named framework temp root created after the snapshot is excluded
    by the temp-root rule alone (the snapshot predates it), while a
    dot-named sibling created alongside it is reported.
    """
    session_epoch = int(time.time()) - 5
    staged_snapshot = tmp_path / "staged" / "pre_dispatch_tmp_snapshot"
    _run_take_snapshot(tmp_path, staged_snapshot, session_epoch)

    fw_root = _bare_tmp_probe(".tester_fixture_ow_fw_root")
    sibling = _bare_tmp_probe(".tester_fixture_ow_dot_sibling")
    snapshot = fw_root / "projects" / _OW_PROJECT / "tasks" / "TEST-OW-DOT-FW" / "litter" / "pre_dispatch_tmp_snapshot"
    try:
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(staged_snapshot.read_bytes())
        sibling.write_text("litter\n", encoding="utf-8")

        result = _run_overwatch_litter_check(tmp_path, temp_root=fw_root)
        assert result.returncode == 0, f"check failed: {result.stderr}"
        targets = _ow_litter_found_targets(tmp_path)
        assert str(sibling) in targets, f"dot-named sibling not reported: {targets!r}"
        assert str(fw_root) not in targets, f"framework temp root reported: {targets!r}"
    finally:
        sibling.unlink(missing_ok=True)
        shutil.rmtree(fw_root, ignore_errors=True)


@pytest.mark.parametrize("temp_dir", [None, "custom-ow-temp"], ids=["default-layout", "configured-temp-dir"])
def test_overwatch_check_finds_the_snapshot_the_wake_takes(tmp_path: pathlib.Path, temp_dir: str | None) -> None:
    """
    A snapshot wake_bracket_pre_dispatch takes for the swept project's task --
    under <temp root>/projects/<project> when project.cfg sets no temp_dir,
    under <temp root>/<temp_dir> when it does -- is discovered by the check. An
    entry created after it is reported once as report:litter-found, and a
    second sweep does not report it again.
    """
    _ow_project_cfg(tmp_path, temp_dir)
    project_temp_dir = tmp_path / "pgai_tmp" / (temp_dir or f"projects/{_OW_PROJECT}")
    snapshot = _ow_wake_snapshot(tmp_path, "TEST-OW-WAKE")
    assert snapshot == project_temp_dir / "tasks" / "TEST-OW-WAKE" / "litter" / "pre_dispatch_tmp_snapshot"
    assert not (tmp_path / "pgai_tmp" / "tasks").exists(), "the wake wrote under the bare temp root"
    session_epoch = int(snapshot.read_text(encoding="utf-8").splitlines()[0].removeprefix("epoch="))

    entry = _bare_tmp_probe("tester_fixture_ow_wake")
    try:
        entry.write_text("created after dispatch\n", encoding="utf-8")
        os.utime(entry, (session_epoch + 60, session_epoch + 60))

        result = _run_overwatch_litter_check(tmp_path)
        assert result.returncode == 0, f"check failed: {result.stderr}"
        assert f"discovered 1 snapshot(s) under {project_temp_dir}/tasks/*/litter/" in result.stderr, result.stderr
        assert _ow_litter_found_targets(tmp_path).count(str(entry)) == 1, _ow_litter_found_targets(tmp_path)

        result = _run_overwatch_litter_check(tmp_path)
        assert result.returncode == 0, f"second check failed: {result.stderr}"
        assert _ow_litter_found_targets(tmp_path).count(str(entry)) == 1, "a second sweep re-reported the entry"
    finally:
        entry.unlink(missing_ok=True)


@pytest.mark.parametrize("dotglob", ["-s", "-u"], ids=["dotglob-set", "dotglob-unset"])
def test_overwatch_check_leaves_caller_shell_options_unchanged(tmp_path: pathlib.Path, dotglob: str) -> None:
    """
    A sweep enumerates dot-named entries without changing the caller's
    shell: dotglob, nullglob and every other shopt and set -o option read the
    same after the sweep as before, whether dotglob was set or unset.
    """
    _run_take_snapshot(tmp_path, _ow_snapshot_path(tmp_path), int(time.time()) - 5)
    dot_entry = _bare_tmp_probe(".tester_fixture_ow_shopt")
    options = tmp_path / "options"
    try:
        dot_entry.write_text("litter\n", encoding="utf-8")
        result = _run_overwatch_litter_check(
            tmp_path,
            before=f'shopt {dotglob} dotglob\n{{ shopt -p; set +o; }} > "{options}.before"',
            after=f'{{ shopt -p; set +o; }} > "{options}.after"',
        )
        assert result.returncode == 0, f"check failed: {result.stderr}"
        before = pathlib.Path(f"{options}.before").read_text(encoding="utf-8")
        after = pathlib.Path(f"{options}.after").read_text(encoding="utf-8")
        assert f"shopt {dotglob} dotglob" in before.splitlines(), before
        assert after == before, f"shell options changed by the sweep:\nbefore:\n{before}\nafter:\n{after}"
        assert str(dot_entry) in _ow_litter_found_targets(tmp_path), "dot-named entry not reported"
    finally:
        dot_entry.unlink(missing_ok=True)


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
        f"wake_bracket_post_session in {_WAKE_BRACKET_SH} does not reference " "the DONE terminal state in its guard."
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
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
        log() {{ echo "$*" >> "{log_cap}"; }}
        wake_tmp_litter_check_and_report \\
            "{missing_snapshot}" \\
            "{task_status}" \\
            "{task_id}" \\
            "log" \\
            "proj" \\
            "{kanban_root}"
    """)
    result = run_bash(tmp_path, script)

    # Must return 0 regardless of the missing snapshot.
    assert result.returncode == 0, (
        "wake_tmp_litter_check_and_report returned non-zero when snapshot was missing. "
        "Fire-and-forget discipline requires it always returns 0 to callers.\n"
        f"stderr: {result.stderr}"
    )

    # status.md must be unchanged — no sections were appended.
    status_after = task_status.read_text(encoding="utf-8")
    assert (
        "## Temp Litter" not in status_after
    ), "status.md gained a Temp Litter section despite the snapshot being missing."


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
    script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
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
    """)
    result = run_bash(tmp_path, script)

    # The script must reach "EXIT_CODE=0" even with strict mode (-euo pipefail).
    assert "EXIT_CODE=0" in result.stdout, (
        "Script aborted before completion — check function propagated non-zero exit "
        "under set -euo pipefail.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.returncode == 0, f"Script exited {result.returncode}; expected 0.\n" f"stderr: {result.stderr}"


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
    assert lines[0] == f"epoch={epoch}", f"First line of snapshot was '{lines[0]}'; expected 'epoch={epoch}'."


def test_take_snapshot_writes_one_line_per_entry(tmp_path: pathlib.Path) -> None:
    """
    Every snapshot line after the header is one whole record — a mtime field,
    a tab, a name — even for an entry whose basename holds a line feed, and
    dot-named entries are recorded.
    """
    snapshot_file = tmp_path / "snap" / "snap"
    lf_entry = pathlib.Path(f"{_bare_tmp_probe('tester_fixture_snap_lf')}\nsecond")
    dot_entry = _bare_tmp_probe(".tester_fixture_snap_dot")
    try:
        lf_entry.write_text("lf\n", encoding="utf-8")
        dot_entry.write_text("dot\n", encoding="utf-8")
        result = _run_take_snapshot(tmp_path, snapshot_file, 1700000000)
        assert result.returncode == 0, f"take_snapshot failed: {result.stderr}"

        records = snapshot_file.read_bytes().split(b"\n")[1:-1]
        assert records, "snapshot holds no records"
        bad = [r for r in records if not re.match(rb"^(esc=)?\d+\t.", r)]
        assert not bad, f"snapshot lines that are not whole records: {bad!r}"
        names = {r.split(b"\t", 1)[1] for r in records}
        assert dot_entry.name.encode() in names, f"dot-named entry {dot_entry.name!r} not recorded"
        assert lf_entry.name.encode().replace(b"\n", b"\\n") in names, f"{lf_entry.name!r} not recorded escaped"
    finally:
        lf_entry.unlink(missing_ok=True)
        dot_entry.unlink(missing_ok=True)


def test_take_snapshot_refuses_bare_tmp_path(tmp_path: pathlib.Path) -> None:
    """wake_tmp_litter_take_snapshot refuses to write directly to /tmp."""
    # The refusal is keyed on the PARENT directory being /tmp itself, so the
    # probe's basename does not need to be unique here — the function refuses
    # before anything is ever written. _bare_tmp_probe is still used for
    # consistency with every other real bare-/tmp path in this module.
    snapshot_file = _bare_tmp_probe("bad_snapshot_file")
    result = _run_take_snapshot(tmp_path, snapshot_file)
    # Should fail (non-zero) when the parent is /tmp itself.
    assert result.returncode != 0, (
        "take_snapshot should refuse to write a snapshot directly under /tmp "
        "(parent must be a subdirectory of /tmp to be safe)."
    )
    assert not snapshot_file.exists(), "Snapshot was written directly to /tmp — this violates the safety invariant."


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

    litter_file = _bare_tmp_probe("tester_ow_log")
    try:
        litter_file.write_text("ow log test\n", encoding="utf-8")
        log_cap = tmp_path / "lc.txt"
        script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ echo "$*" >> "{log_cap}"; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "{task_id}" "log" \\
                "{project_name}" "{kanban_root}"
        """)
        run_bash(tmp_path, script)

        ow_log = kanban_root / "projects" / project_name / "overwatch" / "actions.log"
        assert ow_log.exists(), (
            f"OVERWATCH actions.log was not created at {ow_log}.\n"
            "wake_tmp_litter_check_and_report must write one line to actions.log "
            "when litter is found."
        )
        ow_text = ow_log.read_text(encoding="utf-8")
        assert "check-bare-tmp-litter" in ow_text, (
            "OVERWATCH actions.log does not contain 'check-bare-tmp-litter'.\n" f"actions.log:\n{ow_text}"
        )
        assert task_id in ow_text, (
            f"OVERWATCH actions.log does not name the task ID '{task_id}'.\n" f"actions.log:\n{ow_text}"
        )
    finally:
        litter_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# actions.log record contract: one line, six fields, whatever the inputs hold
# ---------------------------------------------------------------------------

_LITTER_REASON_RE = re.compile(r"^(\d+) bare /tmp entry\(ies\) created during session: (.+)$")


@pytest.mark.parametrize(
    ("task_id", "expected_target", "entry_suffixes"),
    [
        pytest.param("TEST-OW-PLAIN", "TEST-OW-PLAIN", ("",), id="plain-id-plain-name"),
        pytest.param("TEST-OW-NAMES", "TEST-OW-NAMES", ("\nsecond-line", "\tname"), id="newline-and-tab-names"),
        pytest.param("TEST-OW\tTAB", "TEST-OW TAB", ("",), id="tab-in-task-id"),
        pytest.param("TEST-OW\nNEWLINE", "TEST-OW NEWLINE", ("",), id="newline-in-task-id"),
    ],
)
def test_overwatch_actions_log_record_is_one_line_of_six_fields(
    tmp_path: pathlib.Path,
    task_id: str,
    expected_target: str,
    entry_suffixes: tuple[str, ...],
) -> None:
    """
    One call appends exactly one LF-terminated line of six tab-separated
    fields to actions.log, whatever the task ID or the /tmp entry names hold:
    a tab or newline in either is flattened to one space, so the dashboard
    parser reads the record back as one entry naming every litter entry.
    """
    from pgai_agent_kanban.dashboard.scan_attention import _parse_overwatch_action_log

    task_status = tmp_path / "status.md"
    snapshot_file = tmp_path / "snap" / "pre_dispatch"
    kanban_root = tmp_path / "kanban"
    project_name = "test-project"
    _write_status_md(task_status)
    _run_take_snapshot(tmp_path, snapshot_file, int(time.time()) - 1)

    litter_files = [pathlib.Path(f"{_bare_tmp_probe('tester_ow_record')}{suffix}") for suffix in entry_suffixes]
    try:
        for litter_file in litter_files:
            litter_file.write_text("ow record test\n", encoding="utf-8")
        script = _temp_sh_preamble(tmp_path) + textwrap.dedent(f"""\
            log() {{ :; }}
            wake_tmp_litter_check_and_report \\
                "{snapshot_file}" "{task_status}" "$LITTER_TASK_ID" "log" \\
                "{project_name}" "{kanban_root}"
        """)
        result = run_bash(tmp_path, script, extra_env={"LITTER_TASK_ID": task_id})
        assert result.returncode == 0, f"check_and_report failed: {result.stderr}"

        ow_log = kanban_root / "projects" / project_name / "overwatch" / "actions.log"
        raw = ow_log.read_bytes()
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1, f"Expected exactly one LF-terminated line; got {raw!r}"
        fields = raw[:-1].decode("utf-8").split("\t")
        assert len(fields) == 6, f"Expected six tab-separated fields; got {len(fields)}: {fields!r}"
        assert fields[1:5] == ["check-bare-tmp-litter", expected_target, "litter-reported", "none"], fields

        match = _LITTER_REASON_RE.match(fields[5])
        assert match, f"Reason does not match the litter-report format: {fields[5]!r}"
        expected_names = [" ".join(str(f).split()) for f in litter_files]
        for name in expected_names:
            assert name in match.group(2), f"Reason does not name {name!r}: {fields[5]!r}"

        entries = _parse_overwatch_action_log(ow_log, max_entries=0)
        assert len(entries) == 1, f"Parser returned {len(entries)} entries; expected one: {entries!r}"
        assert entries[0]["target"] == expected_target, entries[0]
        for name in expected_names:
            assert name in entries[0]["reason"], f"Parsed reason does not name {name!r}: {entries[0]!r}"
    finally:
        for litter_file in litter_files:
            litter_file.unlink(missing_ok=True)
