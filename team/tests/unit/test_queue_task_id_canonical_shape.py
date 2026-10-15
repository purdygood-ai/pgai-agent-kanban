"""
test_queue_task_id_canonical_shape.py
=====================================
Behavioral coverage for the canonical task-id gate in wake_task_state.sh:
every site that reads a task id out of a queue line or a ## Prerequisites
token and would join it onto TASKS_ROOT validates the id against the one
shape task_ids.sh's kanban_task_id_pattern prints. An id outside that shape
is refused dual-channel, naming the file and line, and is skipped -- it is
never used as a path -- while every well-formed entry is still served.

Each fixture plants a decoy status.md OUTSIDE tasks_root at exactly the
place a malformed id would resolve to (tasks_root/../x), in the state that
would make the unguarded scan act on it. A guard that let the id become a
path would therefore produce a visible wrong outcome (the decoy id served,
a task promoted, a prerequisite satisfied), so each test discriminates the
guard from a scan that merely happens not to find the file.

This module sources the real wake_task_state.sh (which self-sources
atomic_write.sh and the wake_task_id_gate.sh stage file that carries the
refusal sites and check_prerequisites, and through it task_ids.sh and
error_contract.sh) and drives the real function bodies. All fixtures are tmp_path-based; no
real project state or the live kanban install is read or mutated.

Fixtures exercised:
  (A) Pending scan: '..', '../x' and 'a.b' queue lines are each refused
      with wake.queue.malformed-task-id naming the queue file and line,
      are never printed as the pending task, and the first well-formed
      BACKLOG entry behind them is still served.
  (B) Prerequisite parse: '..', '../x' and 'a.b' tokens are each reported
      as <token>:MALFORMED, refused with wake.prerequisite.malformed-task-id
      naming the README, and hold the check unsatisfied even though a decoy
      status.md at tasks_root/../x reads DONE.
  (C) WAITING scan: a '[W] ..' line and a '[ ] ../x' line whose decoy
      status reads WAITING are refused and never promoted, while a
      well-formed [W] entry with a satisfied prerequisite is promoted to
      BACKLOG in the same pass.
  (D) A queue in the live shape -- materializer-minted ids, a legacy
      CLAUDE- prefixed id, [x] entries with descriptions, surrounding
      whitespace, the '[]' marker alias -- produces zero refusals at any of
      the three sites, and every '[ ]' BACKLOG entry is served by the
      pending scan. (The '[]' alias line proves only that a well-formed id
      behind it is not refused; which markers the pending scan dispatches
      is that scan's own contract, unchanged here.)
  (E) Stale-active scan: '[A] ..', '[A] ../x' and '[A] a.b' lines are each
      refused with wake.queue.malformed-task-id naming the queue file and
      line, and reconcile_stale_active makes no liveness, teardown,
      branch-delete, state-writer or marker call for any of them, while a
      well-formed [A] entry in the same file is still reconciled.

Naming: function names describe behavior under test, never a bug ID or
task ID (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

# Real, checked-in team/scripts/ directory. This file lives at
# team/tests/unit/, so three parents up is team/.
_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_WAKE_TASK_STATE_LIB = _REAL_REPO_TEAM_SCRIPTS_DIR / "lib" / "wake_task_state.sh"
_WAKE_RECONCILE_SWEEPS_LIB = _REAL_REPO_TEAM_SCRIPTS_DIR / "lib" / "wake_reconcile_sweeps.sh"

_QUEUE_REFUSAL_KEY = "error:   wake.queue.malformed-task-id"
_PREREQ_REFUSAL_KEY = "error:   wake.prerequisite.malformed-task-id"


def _write_status(task_dir: pathlib.Path, state: str) -> pathlib.Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(
        textwrap.dedent(f"""\
            # Status

            ## State
            {state}

            ## Summary
            fixture.

            ## Blockers
            none

            ## Needs Human
            no
        """),
        encoding="utf-8",
    )
    return status_file


def _write_readme(task_dir: pathlib.Path, prerequisites: list[str]) -> pathlib.Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    readme = task_dir / "README.md"
    body = "\n".join(f"- {p}" for p in prerequisites) if prerequisites else "none"
    readme.write_text(f"# Task\n\n## Prerequisites\n{body}\n\n## Goal\nfixture\n", encoding="utf-8")
    return readme


def _script_header(tmp_path: pathlib.Path, backlog: pathlib.Path, tasks_root: pathlib.Path) -> str:
    """Bash header shared by every test: sources the real library with the
    globals it expects (BACKLOG, TASKS_ROOT, log) and a temp dir under
    tmp_path. PGAI_PROJECT_NAME is cleared so the emitter's refusal-ledger
    recording has no project to write into (it records nowhere), and
    get_release_state_field is stubbed for the BLOCKED-scan path."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path / 'kanban_temp'}"
        mkdir -p "$PGAI_AGENT_KANBAN_TEMP_DIR"
        unset PGAI_PROJECT_NAME PGAI_AGENT_KANBAN_ROOT_PATH
        BACKLOG="{backlog}"
        TASKS_ROOT="{tasks_root}"
        log() {{ printf 'LOG: %s\\n' "$*"; }}
        get_release_state_field() {{ echo none; }}
        source "{_WAKE_TASK_STATE_LIB}"
    """)


def _run(script: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    script_path = tmp_path / "run_script.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )


def _refusal_lines_naming(stderr: str, key_line: str) -> list[str]:
    """Return the message lines that follow each occurrence of key_line."""
    lines = stderr.splitlines()
    return [lines[i + 1] for i, line in enumerate(lines) if line == key_line and i + 1 < len(lines)]


# ---------------------------------------------------------------------------
# (A) Pending scan.
# ---------------------------------------------------------------------------


def test_pending_scan_refuses_malformed_ids_and_serves_first_well_formed_entry(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    backlog = tmp_path / "coder_backlog.md"
    good_id = "CODER-20260101-001-well-formed"
    _write_status(tasks_root / good_id, "BACKLOG")
    # Decoy at exactly tasks_root/../x, BACKLOG: an unguarded scan would
    # serve '../x' as the pending task.
    _write_status(tmp_path / "x", "BACKLOG")
    backlog.write_text(
        textwrap.dedent(f"""\
            # Coder Backlog

            - [ ] .. — parent escape
            - [ ] ../x — sibling escape
            - [ ] a.b — dotted id
            - [ ] {good_id} — a real task
        """),
        encoding="utf-8",
    )

    script = _script_header(tmp_path, backlog, tasks_root) + 'printf "PENDING=[%s]\\n" "$(get_first_pending_task)"\n'
    result = _run(script, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"PENDING=[{good_id}]" in result.stdout, (
        f"the first well-formed BACKLOG entry must still be served\nstdout={result.stdout}"
    )
    for malformed in ("..", "../x", "a.b"):
        assert f"PENDING=[{malformed}]" not in result.stdout
    messages = _refusal_lines_naming(result.stderr, _QUEUE_REFUSAL_KEY)
    assert len(messages) == 3, f"expected one refusal per malformed line\nstderr={result.stderr}"
    for line_no, text in ((3, ".."), (4, "../x"), (5, "a.b")):
        assert any(f"{backlog} line {line_no} " in m and text in m for m in messages), (
            f"refusal must name the queue file and line {line_no} for {text!r}\nstderr={result.stderr}"
        )
    assert "remedy:  sed -n '3p' -- " in result.stderr


# ---------------------------------------------------------------------------
# (B) Prerequisite parse.
# ---------------------------------------------------------------------------


def test_prerequisite_parse_holds_malformed_tokens_unsatisfied_and_refuses_them(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    backlog = tmp_path / "coder_backlog.md"
    backlog.write_text("# Coder Backlog\n", encoding="utf-8")
    task_id = "CODER-20260101-002-has-prereqs"
    readme = _write_readme(tasks_root / task_id, ["..", "../x", "a.b"])
    # Decoy at tasks_root/../x reads DONE: an unguarded parse would treat
    # '../x' as a satisfied prerequisite.
    _write_status(tmp_path / "x", "DONE")

    script = _script_header(tmp_path, backlog, tasks_root) + textwrap.dedent(f"""\
        set +e
        out="$(check_prerequisites "{readme}")"
        rc=$?
        set -e
        printf 'RC=[%s]\\n' "$rc"
        printf 'OUT=[%s]\\n' "$out"
    """)
    result = _run(script, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "RC=[1]" in result.stdout, f"malformed tokens must leave the check unsatisfied\nstdout={result.stdout}"
    for token in ("..", "../x", "a.b"):
        assert f"{token}:MALFORMED" in result.stdout, f"{token!r} must be reported MALFORMED\nstdout={result.stdout}"
    messages = _refusal_lines_naming(result.stderr, _PREREQ_REFUSAL_KEY)
    assert len(messages) == 3, f"expected one refusal per malformed token\nstderr={result.stderr}"
    assert all(str(readme) in m for m in messages), f"each refusal must name the README\nstderr={result.stderr}"
    assert "remedy:  grep -n -A 3 -- '^## Prerequisites' " in result.stderr


def test_prerequisite_parse_still_satisfies_well_formed_done_prerequisite(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    backlog = tmp_path / "coder_backlog.md"
    backlog.write_text("# Coder Backlog\n", encoding="utf-8")
    done_id = "CM-20260101-010-open-rc"
    _write_status(tasks_root / done_id, "DONE")
    readme = _write_readme(tasks_root / "CODER-20260101-003-waits-on-cm", [done_id])

    script = _script_header(tmp_path, backlog, tasks_root) + textwrap.dedent(f"""\
        check_prerequisites "{readme}"
        printf 'RC=[%s]\\n' "$?"
    """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "RC=[0]" in result.stdout
    assert "error:" not in result.stderr, f"a well-formed satisfied prerequisite must not be refused\nstderr={result.stderr}"


# ---------------------------------------------------------------------------
# (C) WAITING scan.
# ---------------------------------------------------------------------------


def test_waiting_scan_refuses_malformed_ids_and_promotes_well_formed_entry(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    backlog = tmp_path / "coder_backlog.md"
    done_id = "CM-20260101-010-open-rc"
    waiting_id = "CODER-20260101-004-waiting"
    _write_status(tasks_root / done_id, "DONE")
    waiting_status = _write_status(tasks_root / waiting_id, "WAITING")
    _write_readme(tasks_root / waiting_id, [done_id])
    # Decoy at tasks_root/../x: WAITING with no prerequisites. An unguarded
    # scan would list '../x' and promote it to BACKLOG.
    decoy_status = _write_status(tmp_path / "x", "WAITING")
    _write_readme(tmp_path / "x", [])
    backlog.write_text(
        textwrap.dedent(f"""\
            # Coder Backlog

            - [W] .. — parent escape
            - [ ] ../x — sibling escape
            - [W] {waiting_id} — a real waiting task
        """),
        encoding="utf-8",
    )

    script = _script_header(tmp_path, backlog, tasks_root) + "recheck_waiting_tasks\n"
    result = _run(script, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    messages = _refusal_lines_naming(result.stderr, _QUEUE_REFUSAL_KEY)
    assert any(f"{backlog} line 3 " in m and ".." in m for m in messages), result.stderr
    assert any(f"{backlog} line 4 " in m and "../x" in m for m in messages), result.stderr
    assert "## State\nWAITING" in decoy_status.read_text(encoding="utf-8"), (
        "a malformed id must never resolve to a path: the decoy outside tasks_root was promoted"
    )
    assert "## State\nBACKLOG" in waiting_status.read_text(encoding="utf-8"), (
        f"the well-formed WAITING entry with a DONE prerequisite must still be promoted\nstdout={result.stdout}"
    )
    queue_text = backlog.read_text(encoding="utf-8")
    assert f"- [ ] {waiting_id}" in queue_text
    assert "- [W] .. — parent escape" in queue_text and "- [ ] ../x — sibling escape" in queue_text, (
        "malformed lines are skipped, never rewritten or removed"
    )
    assert "promoted 1 task(s)" in result.stdout


# ---------------------------------------------------------------------------
# (D) Live-shape queue: nothing real is refused.
# ---------------------------------------------------------------------------


def test_live_shape_queue_parses_every_entry_without_refusal(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    backlog = tmp_path / "coder_backlog.md"
    backlog_ids = [
        "CODER-20260930-006-task-id-worktree-guard",
        "CLAUDE-CODER-20260315-002-legacy-participant-prefix",
        "PM-20260929-001-decompose-v2-26-4-bugfix-bundle-20260929",
        "WRITER-20260930-012-release-notes-2-26-4",
    ]
    for task_id in backlog_ids:
        _write_status(tasks_root / task_id, "BACKLOG")
    _write_status(tasks_root / "CODER-20260930-005-api-server-env-stop-guard", "DONE")
    _write_status(tasks_root / "CM-20260930-010-open-rc-2-26-4", "DONE")
    _write_status(tasks_root / "TESTER-20260930-020-verify-rc-2-26-4", "BACKLOG")
    queue_lines = [
        "# Coder Backlog",
        "",
        "- [x] CM-20260930-010-open-rc-2-26-4 — Open RC branch ai_rc/v2.26.4",
        "- [x] CODER-20260930-005-api-server-env-stop-guard — api-server.sh env and stop guard",
        f"- [ ] {backlog_ids[0]} — Strict task-id parsing and worktree delete guard",
        f"- [ ] {backlog_ids[1]} — legacy entry",
        f"- [ ] {backlog_ids[2]} — materializer-minted decomposition ticket",
        "- [] TESTER-20260930-020-verify-rc-2-26-4 — marker alias, well-formed id",
        f"-   [ ]   {backlog_ids[3]}   — extra whitespace",
    ]
    backlog.write_text("\n".join(queue_lines) + "\n", encoding="utf-8")

    script = _script_header(tmp_path, backlog, tasks_root) + textwrap.dedent("""\
        _narrate_malformed_queue_task_ids "$BACKLOG" ' B' "pending-task scan"
        _narrate_malformed_queue_task_ids "$BACKLOG" 'W ' "WAITING scan"
        _narrate_malformed_queue_task_ids "$BACKLOG" 'B' "BLOCKED scan"
        while :; do
            next="$(get_first_pending_task)" || break
            printf 'SERVED=[%s]\\n' "$next"
            mark_backlog "$next" "x"
        done
        recheck_waiting_tasks
    """)
    result = _run(script, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "error:" not in result.stderr, f"a live-shaped queue must produce no refusal\nstderr={result.stderr}"
    served = [line[len("SERVED=["):-1] for line in result.stdout.splitlines() if line.startswith("SERVED=[")]
    assert served == backlog_ids, f"every BACKLOG entry must be served in order; got {served}"


# ---------------------------------------------------------------------------
# (E) Stale-active scan.
# ---------------------------------------------------------------------------


def test_stale_active_scan_refuses_malformed_ids_and_reconciles_well_formed_entry(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    backlog = tmp_path / "coder_backlog.md"
    worktree_base = tmp_path / "wtbase"
    dev_tree = tmp_path / "dev"
    (dev_tree / ".git").mkdir(parents=True)
    good_id = "CODER-20260101-005-orphaned-active"
    good_status = _write_status(tasks_root / good_id, "WORKING")
    (worktree_base / good_id).mkdir(parents=True)
    # Decoys at every place a malformed id would resolve to, each WORKING so
    # an unguarded sweep would treat it as an orphan and reset it:
    # '..' -> tasks_root/../status.md, '../x' -> tasks_root/../x/status.md,
    # 'a.b' -> tasks_root/a.b/status.md.
    _write_status(tmp_path, "WORKING")
    _write_status(tmp_path / "x", "WORKING")
    _write_status(tasks_root / "a.b", "WORKING")
    backlog.write_text(
        textwrap.dedent(f"""\
            # Coder Backlog

            - [A] .. — parent escape
            - [A] ../x — sibling escape
            - [A] a.b — dotted id
            - [A] {good_id} — a real orphaned task
        """),
        encoding="utf-8",
    )

    # Every call reconcile_stale_active can make with a task id is stubbed to
    # record its arguments in calls_log (a file, because several of them run
    # inside $(...) captures), so the test sees exactly which ids reached a
    # path-building or state-changing step. The stubs are defined after the
    # real libraries are sourced so they replace the real bodies.
    calls_log = tmp_path / "calls.log"
    script = _script_header(tmp_path, backlog, tasks_root) + textwrap.dedent(f"""\
        source "{_WAKE_RECONCILE_SWEEPS_LIB}"
        PGAI_DEV_TREE_PATH="{dev_tree}"
        unset PP_dev_tree_path
        call() {{ printf '%s\\n' "$*" >> "{calls_log}"; }}
        task_liveness_verdict() {{ call liveness "$1"; echo absent; }}
        task_liveness_record_path() {{ echo /nonexistent; }}
        pgai_worktree_path() {{ call worktree-path "$1"; echo "{worktree_base}/$1"; }}
        teardown_task_worktree() {{ call teardown "$1"; }}
        pp_prefix_branch() {{ echo "ai_$2"; }}
        git() {{ call git "$*"; }}
        mark_backlog() {{ call mark "$1"; }}
        zero_progress_advance_or_escalate() {{ call state-writer "$1" "$2"; }}
        reconcile_stale_active
    """)
    result = _run(script, tmp_path)

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    messages = _refusal_lines_naming(result.stderr, _QUEUE_REFUSAL_KEY)
    assert len(messages) == 3, f"expected one refusal per malformed [A] line\nstderr={result.stderr}"
    for line_no, text in ((3, ".."), (4, "../x"), (5, "a.b")):
        assert any(f"{backlog} line {line_no} " in m and text in m and "stale-active scan" in m for m in messages), (
            f"refusal must name the queue file, line {line_no} and the scan for {text!r}\nstderr={result.stderr}"
        )
    assert "remedy:  sed -n '3p' -- " in result.stderr

    calls = calls_log.read_text(encoding="utf-8").splitlines() if calls_log.exists() else []
    for malformed in ("..", "../x", "a.b"):
        leaked = [c for c in calls if malformed in c.split()]
        assert not leaked, f"a malformed id must never reach a teardown/branch/state/marker call: {leaked}"
    assert not any("a.b" in c or "/.." in c or "../" in c for c in calls), f"calls={calls}"

    assert f"liveness {good_id}" in calls, f"calls={calls}"
    assert f"teardown {good_id}" in calls, f"the well-formed orphan must still be torn down\ncalls={calls}"
    assert any(c.startswith("git ") and f"refs/heads/ai_feature/{good_id}" in c for c in calls), f"calls={calls}"
    assert any(c.startswith("git ") and f"branch -D ai_feature/{good_id}" in c for c in calls), f"calls={calls}"
    assert f"state-writer {good_id} {good_status}" in calls, (
        f"the well-formed orphan must still be reset through the state writer\ncalls={calls}"
    )
    assert "reset 1 orphaned [A] task(s)" in result.stdout, f"stdout={result.stdout}"
