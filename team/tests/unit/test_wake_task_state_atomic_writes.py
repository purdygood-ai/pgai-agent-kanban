"""
test_wake_task_state_atomic_writes.py
=====================================
Behavioral fixtures for the atomic write path every bash-side writer of a
task's status.md and the per-agent queue file goes through
(team/scripts/lib/atomic_write.sh, wired into wake_task_state.sh's writers
and wake_task_transition.sh's rollback), and for transition_task_state's
behaviour when its second write fails or a signal lands between its two
writes.

This module exercises the real production function bodies in
wake_task_state.sh and wake_task_transition.sh, extracted via the same awk
brace-matching technique test_wake_task_state_transition.py established,
with the real atomic_write.sh / error_contract.sh / temp.sh sourced. All
fixtures are tmp_path-based; no real project state or the live kanban
install is read or mutated.

Fixtures exercised:
  (A) A reader polling status.md and the queue file while set_state and
      mark_backlog rewrite them in a tight loop never observes an empty or
      truncated file — every read is byte-equal to one of the two complete
      forms the writer alternates between.
  (B) The reader in (A) is not vacuous: the same reader watching a
      deliberate truncate-then-write writer does catch the empty window.
  (C) A failed status write (no ## State block to rewrite) leaves the file
      byte-identical and no scratch file beside it.
  (D) The target's file mode survives a rewrite.
  (E) SIGTERM between the ## State write and the marker write: ## State is
      rolled back, the two surfaces agree, and the interruption is logged
      dual-channel naming both files.
  (F) SIGTERM after the marker landed: nothing is rolled back and the two
      surfaces agree on the new state.
  (G) A rollback that cannot land (task folder made unwritable after the
      ## State write) refuses non-zero with wake.transition.rollback-failed,
      names both files and the retained snapshot, and never claims
      "rolled back".
  (H) promote_waiting_to_backlog commits status.md and derives the marker
      through the same path, leaving no scratch file; a missing [W] entry
      writes nothing at all.
  (I) ensure_status_file creates a missing status.md through the same path,
      leaving no scratch file.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import signal
import stat
import subprocess
import textwrap
import time

import pytest

from tests.fixtures.log_stub import make_log_stub_fragment

# Real, checked-in team/scripts/ directory. This file lives at
# team/tests/unit/, so three parents up is team/.
_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_WAKE_TASK_STATE_LIB = _REAL_REPO_TEAM_SCRIPTS_DIR / "lib" / "wake_task_state.sh"
_WAKE_TASK_TRANSITION_LIB = _REAL_REPO_TEAM_SCRIPTS_DIR / "lib" / "wake_task_transition.sh"

_STATE_TO_MARKER = {
    "BACKLOG": " ",
    "WAITING": "W",
    "WORKING": "A",
    "BLOCKED": "B",
    "DONE": "x",
    "WONT-DO": "x",
}

_EXTRACTED_FUNCTIONS = [
    "ensure_status_file",
    "normalize_status_file",
    "_status_section_re",
    "_read_status_section",
    "get_state",
    "set_state",
    "set_block",
    "mark_backlog",
    "_promote_mark_pending",
    "promote_waiting_to_backlog",
    "_wake_transition_marker_landed",
    "_wake_transition_restore_state",
    "_wake_transition_arm",
    "_wake_transition_disarm",
    "_wake_transition_on_signal",
    "transition_task_state",
]


# ---------------------------------------------------------------------------
# Function extraction and script header
# ---------------------------------------------------------------------------


def _extract_functions_snippet(func_names: list[str]) -> str:
    alternation = "|".join(re.escape(name) for name in func_names)
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0 }}
                    }}
                }}
            }}
        ' '{_WAKE_TASK_STATE_LIB}' '{_WAKE_TASK_TRANSITION_LIB}')"
    """)


def _script_header(log_file: pathlib.Path, temp_dir: pathlib.Path, backlog_file: pathlib.Path) -> str:
    """Bash header shared by every test in this module: strict mode, the
    isolated temp root transition_task_state snapshots into, the real
    error_contract.sh / temp.sh / atomic_write.sh, the faithful log() stub,
    and the production functions extracted from their stage files."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_dir}"
        unset PGAI_AGENT_KANBAN_ROOT_PATH
        KANBAN_ROOT="{temp_dir}"
        BACKLOG="{backlog_file}"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/error_contract.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/temp.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/atomic_write.sh"

        {make_log_stub_fragment(log_file)}

        {_extract_functions_snippet(_EXTRACTED_FUNCTIONS)}
    """)


# ---------------------------------------------------------------------------
# Fixture builders and readers
# ---------------------------------------------------------------------------


def _status_text(task_id: str, state: str) -> str:
    return textwrap.dedent(f"""\
        # Status

        ## Task
        {task_id}

        ## State
        {state}

        ## Summary
        TBD

        ## Artifacts
        none

        ## Blockers
        none

        ## Needs Human
        no

        ## Next Recommended Step
        TBD

        ## Instruction Conflicts
        none
    """)


def _queue_text(task_id: str, marker: str) -> str:
    return f"# Coder Backlog\n\n- [{marker}] {task_id} — fixture task\n"


def _build_status_file(tmp_path: pathlib.Path, task_id: str, state: str) -> pathlib.Path:
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(_status_text(task_id, state), encoding="utf-8")
    return status_file


def _build_backlog_file(tmp_path: pathlib.Path, task_id: str, marker: str) -> pathlib.Path:
    backlog_file = tmp_path / "coder_backlog.md"
    backlog_file.write_text(_queue_text(task_id, marker), encoding="utf-8")
    return backlog_file


def _run(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


def _marker_for_task(queue_text: str, task_id: str) -> str:
    m = re.search(rf"^\s*-\s*\[(.)\]\s+{re.escape(task_id)}\b", queue_text, flags=re.M)
    assert m is not None, f"queue entry for {task_id} not found in:\n{queue_text}"
    return m.group(1)


def _assert_state_and_marker_agree(status_text: str, queue_text: str, task_id: str) -> None:
    state = _field(status_text, "State")
    marker = _marker_for_task(queue_text, task_id)
    expected = _STATE_TO_MARKER[state]
    assert marker == expected, (
        f"status.md ## State={state!r} maps to marker {expected!r}, but the queue "
        f"marker for {task_id} reads {marker!r} — the two surfaces disagree"
    )


def _scratch_files(directory: pathlib.Path) -> list[str]:
    """Names of atomic-write scratch files left beside a target."""
    return sorted(p.name for p in directory.iterdir() if p.name.startswith(".") and ".tmp." in p.name)


def _poll_until_exit(proc: subprocess.Popen, watched: dict[pathlib.Path, set[str]]) -> tuple[int, list[str]]:
    """Read every watched file as fast as possible until *proc* exits.

    Returns (total reads, list of offending observations). A read offends
    when its bytes equal none of the complete forms recorded for that
    file — an empty file, a truncated file, or a half-written one all land
    there.
    """
    reads = 0
    offending: list[str] = []
    while proc.poll() is None:
        for path, complete_forms in watched.items():
            try:
                observed = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                offending.append(f"{path.name}: file missing")
                continue
            reads += 1
            if observed not in complete_forms:
                offending.append(f"{path.name}: {observed!r}")
    return reads, offending


# ---------------------------------------------------------------------------
# (A) A concurrent reader never sees an empty or truncated file.
# ---------------------------------------------------------------------------


def test_concurrent_reader_never_observes_empty_or_truncated_file(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-030-fixture-a"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)

    iterations = 20
    script = header + textwrap.dedent(f"""\
        for _ in $(seq 1 {iterations}); do
          set_state "{status_file}" BLOCKED
          mark_backlog "{task_id}" B
          set_state "{status_file}" WORKING
          mark_backlog "{task_id}" A
        done
    """)
    watched = {
        status_file: {_status_text(task_id, "WORKING"), _status_text(task_id, "BLOCKED")},
        backlog_file: {_queue_text(task_id, "A"), _queue_text(task_id, "B")},
    }
    proc = subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        reads, offending = _poll_until_exit(proc, watched)
    finally:
        stdout, stderr = proc.communicate(timeout=60)
    assert proc.returncode == 0, f"writer loop must succeed\nstdout={stdout}\nstderr={stderr}"
    assert reads >= 200, f"the reader must have raced the {iterations * 4} rewrites many times, got {reads} reads"
    assert not offending, (
        f"a reader saw a file that was not one of the two complete forms "
        f"({len(offending)} of {reads} reads); first offenders:\n" + "\n".join(offending[:5])
    )
    assert _scratch_files(status_file.parent) == [], "no scratch file may outlive a completed write"
    assert _scratch_files(backlog_file.parent) == [], "no scratch file may outlive a completed write"
    assert _read(status_file) == _status_text(task_id, "WORKING")
    assert _read(backlog_file) == _queue_text(task_id, "A")


# ---------------------------------------------------------------------------
# (B) The reader above is not vacuous: a truncate-then-write writer is caught.
# ---------------------------------------------------------------------------


def test_reader_catches_a_truncate_then_write_writer(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-031-fixture-b"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    blocked_form = tmp_path / "blocked.md"
    working_form = tmp_path / "working.md"
    blocked_form.write_text(_status_text(task_id, "BLOCKED"), encoding="utf-8")
    working_form.write_text(_status_text(task_id, "WORKING"), encoding="utf-8")

    # The exact in-place shape the atomic path replaces: truncate, then fill.
    # The pause widens the empty window so the check is deterministic here;
    # in production the window is the whole duration of the write.
    script = textwrap.dedent(f"""\
        set -euo pipefail
        for _ in $(seq 1 30); do
          : > "{status_file}"; sleep 0.005; cat "{blocked_form}" > "{status_file}"
          : > "{status_file}"; sleep 0.005; cat "{working_form}" > "{status_file}"
        done
    """)
    watched = {status_file: {_status_text(task_id, "WORKING"), _status_text(task_id, "BLOCKED")}}
    proc = subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _reads, offending = _poll_until_exit(proc, watched)
    finally:
        proc.communicate(timeout=60)
    assert proc.returncode == 0
    assert offending, "the reader must catch the empty window a truncate-then-write writer exposes"
    assert any(": ''" in entry for entry in offending), f"expected an empty read among the offenders, got:\n{offending[:5]}"


# ---------------------------------------------------------------------------
# (C) A failed write leaves the target byte-identical and no scratch file.
# ---------------------------------------------------------------------------


def test_failed_status_write_leaves_file_and_directory_untouched(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-032-fixture-c"
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True)
    status_file = task_dir / "status.md"
    status_file.write_text("# Status\n\n## Task\n" + task_id + "\n\n## Summary\nno state block here\n", encoding="utf-8")
    before = _read(status_file)
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)

    result = _run(header + f'set_state "{status_file}" BLOCKED\n')
    assert result.returncode != 0, "set_state must fail when there is no ## State block to rewrite"
    assert "Could not update ## State block" in result.stderr, f"the producer's own message must reach the caller\n{result.stderr}"
    assert _read(status_file) == before, "a failed write must leave the target byte-identical"
    assert _scratch_files(task_dir) == [], f"a failed write must remove its scratch file, found {_scratch_files(task_dir)}"


# ---------------------------------------------------------------------------
# (D) The target's mode survives a rewrite.
# ---------------------------------------------------------------------------


def test_rewrite_preserves_target_file_mode(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-033-fixture-d"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    os.chmod(status_file, 0o640)
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)

    result = _run(header + f'set_state "{status_file}" BLOCKED\n')
    assert result.returncode == 0, result.stderr
    assert _field(_read(status_file), "State") == "BLOCKED"
    assert stat.S_IMODE(status_file.stat().st_mode) == 0o640, "the rename must carry the target's own mode, not mktemp's 0600"


# ---------------------------------------------------------------------------
# (E) SIGTERM between the two writes rolls ## State back and names both files.
# ---------------------------------------------------------------------------


def test_signal_between_writes_rolls_state_back_and_names_both_files(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-034-fixture-e"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)
    before_status = _read(status_file)
    before_queue = _read(backlog_file)

    # The marker writer is replaced AFTER the real functions are defined, so
    # the transition runs the real ## State write and is then terminated
    # from inside the window before its marker write — the exact kill the
    # rollback exists for.
    script = header + textwrap.dedent(f"""\
        mark_backlog() {{ kill -TERM $$; }}
        transition_task_state "{task_id}" "{status_file}" "BLOCKED"
        echo "UNREACHED"
    """)
    result = _run(script)
    assert result.returncode == -signal.SIGTERM, (
        f"the shell must terminate by the re-raised SIGTERM, got {result.returncode}\nstderr={result.stderr}"
    )
    assert "UNREACHED" not in result.stdout, "the interrupted transition must not return to its caller"
    assert "error:   wake.transition.interrupted" in result.stderr, result.stderr
    assert str(status_file) in result.stderr and str(backlog_file) in result.stderr, (
        f"the interruption must name both files\nstderr={result.stderr}"
    )
    assert _read(status_file) == before_status, "## State must be rolled back byte-for-byte"
    assert _read(backlog_file) == before_queue, "the queue must be untouched"
    _assert_state_and_marker_agree(_read(status_file), _read(backlog_file), task_id)
    assert list((tmp_path / "kanban_temp").glob("wake_transition_status*")) == [], "the snapshot is consumed by a landed rollback"


# ---------------------------------------------------------------------------
# (F) SIGTERM after the marker landed rolls nothing back.
# ---------------------------------------------------------------------------


def test_signal_after_marker_landed_keeps_both_surfaces_on_new_state(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-035-fixture-f"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)

    script = header + textwrap.dedent(f"""\
        eval "$(declare -f mark_backlog | sed '1s/^mark_backlog/_real_mark_backlog/')"
        mark_backlog() {{ _real_mark_backlog "$@"; kill -TERM $$; }}
        transition_task_state "{task_id}" "{status_file}" "BLOCKED"
        echo "UNREACHED"
    """)
    result = _run(script)
    assert result.returncode == -signal.SIGTERM, f"got {result.returncode}\nstderr={result.stderr}"
    assert "UNREACHED" not in result.stdout
    assert "rolled back" not in result.stderr, f"nothing may be rolled back once the marker landed\n{result.stderr}"
    assert "wake.transition." not in result.stderr, f"no refusal is due when both writes landed\n{result.stderr}"
    assert _field(_read(status_file), "State") == "BLOCKED"
    assert _marker_for_task(_read(backlog_file), task_id) == "B"
    _assert_state_and_marker_agree(_read(status_file), _read(backlog_file), task_id)


# ---------------------------------------------------------------------------
# (G) A rollback that cannot land refuses loud and never claims "rolled back".
# ---------------------------------------------------------------------------


def test_rollback_that_cannot_land_refuses_and_names_the_snapshot(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-036-fixture-g"
    status_file = _build_status_file(tmp_path, task_id, state="WORKING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="A")
    task_dir = status_file.parent
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)
    before_status = _read(status_file)

    # The marker writer fails AND makes the task folder unwritable first, so
    # the restore's own scratch file cannot be created beside status.md.
    script = header + textwrap.dedent(f"""\
        mark_backlog() {{ chmod 0555 "{task_dir}"; return 1; }}
        rc=0
        transition_task_state "{task_id}" "{status_file}" "BLOCKED" || rc=$?
        chmod 0755 "{task_dir}"
        printf 'EXIT=[%s]\\n' "$rc"
    """)
    result = _run(script)
    assert result.returncode == 0, f"harness script itself must complete\nstderr={result.stderr}"
    assert "EXIT=[1]" in result.stdout, f"a failed rollback must return non-zero\nstdout={result.stdout}"
    assert "error:   wake.transition.rollback-failed" in result.stderr, result.stderr
    assert "message: " in result.stderr and "remedy:  " in result.stderr
    assert str(status_file) in result.stderr and str(backlog_file) in result.stderr, (
        f"the refusal must name both files\nstderr={result.stderr}"
    )
    assert "rolled back" not in result.stderr, f"no run may log 'rolled back' after a rollback that did not land\n{result.stderr}"
    assert "wake.transition.marker-write-failed" not in result.stderr, "the marker-write refusal is only reached once the restore landed"

    m = re.search(r"snapshot is kept at (\S+)\.", result.stderr)
    assert m is not None, f"the message must name the retained snapshot\n{result.stderr}"
    snapshot = pathlib.Path(m.group(1))
    assert snapshot.is_file(), f"the snapshot named in the refusal must still exist: {snapshot}"
    assert _read(snapshot) == before_status, "the retained snapshot must hold the pre-call bytes"
    assert f"cp -- '{snapshot}' '{status_file}'" in result.stderr, "the remedy must be the runnable restore command"
    # Honest on-disk state: the ## State write landed and could not be undone.
    assert _field(_read(status_file), "State") == "BLOCKED"
    assert _marker_for_task(_read(backlog_file), task_id) == "A"


# ---------------------------------------------------------------------------
# (H) promote_waiting_to_backlog writes both surfaces through the same path.
# ---------------------------------------------------------------------------


def test_promote_waiting_commits_status_then_marker_without_scratch_litter(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-037-fixture-h"
    status_file = _build_status_file(tmp_path, task_id, state="WAITING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="W")
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)

    result = _run(header + f'promote_waiting_to_backlog "{task_id}" "{status_file}"\nprintf \'EXIT=[%s]\\n\' "$?"\n')
    assert result.returncode == 0, result.stderr
    assert "EXIT=[0]" in result.stdout
    status_text = _read(status_file)
    assert _field(status_text, "State") == "BACKLOG"
    assert _field(status_text, "Blockers") == "none"
    assert _field(status_text, "Summary").startswith("Promoted from WAITING to BACKLOG")
    assert _marker_for_task(_read(backlog_file), task_id) == " "
    _assert_state_and_marker_agree(status_text, _read(backlog_file), task_id)
    assert _scratch_files(status_file.parent) == []
    assert _scratch_files(backlog_file.parent) == []


def test_promote_waiting_without_waiting_entry_writes_nothing(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-038-fixture-h2"
    status_file = _build_status_file(tmp_path, task_id, state="WAITING")
    backlog_file = _build_backlog_file(tmp_path, task_id, marker="B")
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)
    before_status = _read(status_file)
    before_queue = _read(backlog_file)

    result = _run(header + f'promote_waiting_to_backlog "{task_id}" "{status_file}"\n')
    assert result.returncode != 0, "a queue entry that is not [W] or [ ] must refuse the promotion"
    assert "Could not find [W] or [ ] queue entry" in result.stderr, result.stderr
    assert _read(status_file) == before_status, "status.md must be untouched when the queue entry does not qualify"
    assert _read(backlog_file) == before_queue
    assert _scratch_files(status_file.parent) == []
    assert _scratch_files(backlog_file.parent) == []


# ---------------------------------------------------------------------------
# (I) ensure_status_file creates a missing status.md through the same path.
# ---------------------------------------------------------------------------


def test_ensure_status_file_creates_template_without_scratch_litter(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-039-fixture-i"
    backlog_file = _build_backlog_file(tmp_path, task_id, marker=" ")
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True)
    status_file = task_dir / "status.md"
    header = _script_header(tmp_path / "wake.log", tmp_path / "kanban_temp", backlog_file)

    result = _run(header + f'ensure_status_file "{status_file}"\n')
    assert result.returncode == 0, result.stderr
    assert status_file.is_file()
    assert _field(_read(status_file), "State") == "BACKLOG"
    assert "created status.md template for task" in result.stdout
    assert _scratch_files(task_dir) == []
    assert stat.S_IMODE(status_file.stat().st_mode) & 0o044 == 0o044, "a new status.md must be readable beyond its owner (umask default, not mktemp's 0600)"
