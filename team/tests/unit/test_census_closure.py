"""
test_census_closure.py
======================
Unit tests for the completion-census gate in close_intake_on_finalize_report
and the per-wake sweep_running_intake_census function
(team/scripts/lib/wake_common.sh).

These tests exercise:

  (a) One-ticket graph: the sole task is DONE → close_item invoked (all siblings
      terminal, census returns ok:1).

  (b) Two-ticket graph: one task is DONE, sibling is WORKING → close_item NOT
      invoked; census summary logged identifying the non-terminal sibling.

  (c) Two-ticket graph: one task is DONE, sibling is WONT-DO → close_item
      invoked (corpse handling — WONT-DO is terminal by definition).

  (d) Sweep self-healing: stranded intake item (status running, all sibling tasks
      terminal, no task transition occurring) → sweep_running_intake_census closes
      it on the next wake cycle.  This is the production corpse shape addressed by
      BUG-0071.

  (e) Sweep mid-run negative: one sibling is WORKING → sweep logs the census
      attempt and does NOT close the item.

All fixtures are tmp_path-based; no real project state is read or mutated.
close_intake_on_finalize_report and sweep_running_intake_census are exercised
via bash subprocess using a controlled environment that stubs python3
-m pgai_agent_kanban.ops close_item and the log() function so assertions can
observe invocations and log lines.

The census Python snippet is also tested directly (extracted from the heredoc
in wake_common.sh) to confirm logic correctness without the bash integration
surface.  Both levels of testing are intentional:
  - Python-level tests: fast, no bash dep, confirm logic
  - Bash-level tests: confirm the shell function's guard/log/dispatch wiring

Naming: function names describe behavior under test, never bug IDs or task IDs
(SOP.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------

_WAKE_COMMON_LIB = pathlib.Path("scripts/lib/wake_common.sh")
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/


# ---------------------------------------------------------------------------
# Census Python logic (extracted from wake_common.sh heredoc for direct testing)
#
# This is the same Python snippet embedded in close_intake_on_finalize_report.
# Tested here directly so census correctness is validated without the bash
# integration surface.
# ---------------------------------------------------------------------------


def _run_census(tasks_root: pathlib.Path, req_path: str) -> str:
    """Run the completion census over *tasks_root* for *req_path*.

    Mirrors the Python heredoc embedded in close_intake_on_finalize_report.
    Returns:
        "ok:<N>"          — all N siblings are terminal
        "skip:<detail>"   — one or more siblings are non-terminal
        ""                — tasks_root does not exist
    """
    TERMINAL = {"DONE", "WONT-DO"}

    if not tasks_root.is_dir():
        return ""

    total = 0
    non_terminal: list[str] = []

    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir() or task_dir.name == "queues":
            continue
        readme = task_dir / "README.md"
        status_file = task_dir / "status.md"
        if not readme.is_file() or not status_file.is_file():
            continue
        try:
            readme_text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        # Filter to tasks whose ## Inputs cite the same requirements path
        m = re.search(
            r"^##\s+Inputs\s*\n(.*?)(?=\n##|\Z)",
            readme_text,
            flags=re.S | re.M,
        )
        if not m or req_path not in m.group(1):
            continue
        total += 1
        try:
            status_text = status_file.read_text(encoding="utf-8")
        except OSError:
            non_terminal.append(f"{task_dir.name}:UNKNOWN")
            continue
        state_m = re.search(r"^##\s+State\s*\n\s*(\S+)", status_text, re.M)
        state = state_m.group(1).strip().upper() if state_m else "UNKNOWN"
        if state not in TERMINAL:
            non_terminal.append(f"{task_dir.name}:{state}")

    if non_terminal:
        summary = ", ".join(non_terminal)
        return f"skip:{total} sibling(s), {len(non_terminal)} non-terminal: {summary}"
    return f"ok:{total}"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write_task(
    tasks_root: pathlib.Path,
    task_id: str,
    state: str,
    req_path: str,
) -> pathlib.Path:
    """Create a minimal task folder that the census recognises.

    Writes README.md (citing req_path in ## Inputs) and status.md (with state).

    Args:
        tasks_root:  The tasks/ directory under a synthetic project root.
        task_id:     Folder name for the task (e.g. "TESTER-001-verify").
        state:       Value to write into ## State in status.md.
        req_path:    Absolute path string to cite in ## Inputs of README.md.

    Returns:
        pathlib.Path — the created task directory.
    """
    task_dir = tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "README.md").write_text(
        textwrap.dedent(f"""\
            # Task: {task_id}

            ## Task ID
            {task_id}

            ## Inputs
            - {req_path}

            ## Constraints
            - tester_operation: verify-and-report
            - finalize_mode: report
        """),
        encoding="utf-8",
    )

    (task_dir / "status.md").write_text(
        textwrap.dedent(f"""\
            # Status

            ## Task
            {task_id}

            ## State
            {state}

            ## Blockers
            none

            ## Needs Human
            no
        """),
        encoding="utf-8",
    )
    return task_dir


def _write_requirements(project_root: pathlib.Path, filename: str) -> pathlib.Path:
    """Create a minimal requirements file under project_root/requirements/.

    Args:
        project_root:  Root of the synthetic project directory.
        filename:      Requirements filename including .md extension.

    Returns:
        pathlib.Path — the absolute path to the requirements file.
    """
    req_dir = project_root / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / filename
    req_file.write_text(
        textwrap.dedent("""\
            # Requirements

            ## Status
            running

            ## Source Branch
            main
        """),
        encoding="utf-8",
    )
    return req_file


# ===========================================================================
# Python-level census logic tests
# ===========================================================================


def test_census_one_ticket_done_returns_ok(tmp_path: pathlib.Path) -> None:
    """Census returns ok:1 when the sole task is DONE (all siblings terminal)."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-1.md")
    tasks_root = tmp_path / "tasks"
    _write_task(tasks_root, "TESTER-001-verify", "DONE", str(req_file))

    result = _run_census(tasks_root, str(req_file))

    assert result.startswith("ok:"), (
        f"Expected census to return ok:N when sole task is DONE; got {result!r}"
    )
    assert "1" in result, f"Expected total=1 in ok result; got {result!r}"


def test_census_two_tickets_working_sibling_returns_skip(
    tmp_path: pathlib.Path,
) -> None:
    """Census returns skip when a sibling is WORKING (non-terminal)."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-2.md")
    tasks_root = tmp_path / "tasks"
    _write_task(tasks_root, "TESTER-001-primary", "DONE", str(req_file))
    _write_task(tasks_root, "TESTER-002-sibling", "WORKING", str(req_file))

    result = _run_census(tasks_root, str(req_file))

    assert result.startswith("skip:"), (
        f"Expected census to skip when sibling is WORKING; got {result!r}"
    )


def test_census_skip_result_names_non_terminal_sibling(
    tmp_path: pathlib.Path,
) -> None:
    """Census skip result names the non-terminal sibling's ID and state."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-3.md")
    tasks_root = tmp_path / "tasks"
    _write_task(tasks_root, "TESTER-001-primary", "DONE", str(req_file))
    _write_task(tasks_root, "TESTER-002-sibling", "WORKING", str(req_file))

    result = _run_census(tasks_root, str(req_file))

    assert "TESTER-002-sibling" in result, (
        f"Census skip result must name the non-terminal sibling task ID; got {result!r}"
    )
    assert "WORKING" in result, (
        f"Census skip result must include the sibling's state; got {result!r}"
    )


def test_census_two_tickets_wontdo_sibling_returns_ok(
    tmp_path: pathlib.Path,
) -> None:
    """Census returns ok when sibling is WONT-DO (corpse handling — WONT-DO is terminal)."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-4.md")
    tasks_root = tmp_path / "tasks"
    _write_task(tasks_root, "TESTER-001-primary", "DONE", str(req_file))
    _write_task(tasks_root, "TESTER-002-corpse", "WONT-DO", str(req_file))

    result = _run_census(tasks_root, str(req_file))

    assert result.startswith("ok:"), (
        f"WONT-DO sibling is terminal; census must return ok:N; got {result!r}"
    )


def test_census_two_tickets_wontdo_sibling_total_is_two(
    tmp_path: pathlib.Path,
) -> None:
    """Census ok result counts both DONE and WONT-DO tasks in the total."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-5.md")
    tasks_root = tmp_path / "tasks"
    _write_task(tasks_root, "TESTER-001-primary", "DONE", str(req_file))
    _write_task(tasks_root, "TESTER-002-corpse", "WONT-DO", str(req_file))

    result = _run_census(tasks_root, str(req_file))

    assert "2" in result, (
        f"Census ok result must report total=2 for two-ticket graph; got {result!r}"
    )


def test_census_excludes_tasks_without_matching_req_path(
    tmp_path: pathlib.Path,
) -> None:
    """Census ignores tasks whose ## Inputs do not cite the requirements path."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-6.md")
    other_req = _write_requirements(tmp_path, "v1.0.0-other.md")
    tasks_root = tmp_path / "tasks"
    _write_task(tasks_root, "TESTER-001-primary", "DONE", str(req_file))
    # This task cites a different requirements file — must not be counted
    _write_task(tasks_root, "TESTER-002-different-req", "WORKING", str(other_req))

    result = _run_census(tasks_root, str(req_file))

    assert result.startswith("ok:"), (
        "Census must exclude tasks with a different requirements path; "
        f"WORKING sibling with different req must not cause skip; got {result!r}"
    )


def test_census_empty_tasks_root_returns_ok_zero(tmp_path: pathlib.Path) -> None:
    """Census returns ok:0 when tasks_root has no task folders for the req path."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-7.md")
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir(parents=True)

    result = _run_census(tasks_root, str(req_file))

    assert result.startswith("ok:"), (
        f"Census with no matching tasks must return ok:0; got {result!r}"
    )


def test_census_nonexistent_tasks_root_returns_empty_string(
    tmp_path: pathlib.Path,
) -> None:
    """Census returns empty string when tasks_root does not exist."""
    req_file = _write_requirements(tmp_path, "v1.0.0-run-8.md")
    tasks_root = tmp_path / "nonexistent_tasks"

    result = _run_census(tasks_root, str(req_file))

    assert result == "", (
        f"Census must return '' when tasks_root does not exist; got {result!r}"
    )


# ===========================================================================
# Bash-level integration tests (close_intake_on_finalize_report wiring)
#
# These tests call close_intake_on_finalize_report via bash subprocess with:
#   - A faithful log() stub that writes to stdout (for assertion) + log file
#   - A stub python3 interceptor that records close_item calls
#   - Synthetic task folders in tmp_path
# ===========================================================================


def _build_stub_python3(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a stub python3 wrapper under tmp_path/bin/ that intercepts close_item.

    The stub:
    - Detects 'python3 -m pgai_agent_kanban.ops close_item ...' invocations
    - Records the invocation to a sentinel file: tmp_path/close_item_calls.txt
    - Exits 0 (success) so the shell function proceeds normally
    - For all other python3 invocations, delegates to the real python3

    Returns:
        pathlib.Path — the directory containing the stub (for prepending to PATH).
    """
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_path / "close_item_calls.txt"
    stub = bin_dir / "python3"
    stub.write_text(
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # Stub python3: intercepts 'python3 -m pgai_agent_kanban.ops close_item'
            # Records call arguments; delegates everything else to the real python3.
            SENTINEL="{sentinel}"
            args=("$@")
            if [[ "${{args[0]}}" == "-m" && \
                  "${{args[1]}}" == "pgai_agent_kanban.ops" && \
                  "${{args[2]}}" == "close_item" ]]; then
                # Record: project_root key [state]
                echo "${{args[*]}}" >> "$SENTINEL"
                exit 0
            fi
            # Fall through to real python3 for everything else (census heredoc, etc.)
            exec /usr/bin/python3 "$@"
        """),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def _build_log_stub(log_file: pathlib.Path) -> str:
    """Return a Bash fragment defining a log() stub that writes to stdout + log_file.

    Mirrors the faithful stub from tests/fixtures/log_stub.py so tests can
    assert on wake log lines without importing from that module.

    Args:
        log_file:  Absolute path to the stub log file.

    Returns:
        Bash fragment string (no trailing newline).
    """
    return textwrap.dedent(f"""\
        __LOG_FILE="{log_file}"
        touch "${{__LOG_FILE}}"
        log() {{
            local _msg="[log-stub] $*"
            printf '%s\\n' "$_msg" | tee -a "${{__LOG_FILE}}"
        }}
    """)


def _run_close_intake(
    tmp_path: pathlib.Path,
    tasks_root: pathlib.Path,
    project_root: pathlib.Path,
    task_id: str,
    task_readme: pathlib.Path,
    wf_finalize: str = "report",
) -> tuple[str, str, int, pathlib.Path]:
    """Invoke close_intake_on_finalize_report in an isolated bash subprocess.

    Stubs log() (writes to stdout + log file) and python3 close_item
    (records to sentinel).  Sources only the wake_common.sh library after
    providing all required stubs for its dependencies.

    Args:
        tmp_path:      Pytest tmp_path directory.
        tasks_root:    Synthetic tasks/ directory.
        project_root:  Synthetic project root directory.
        task_id:       Task ID argument for the function.
        task_readme:   Path to the task's README.md.
        wf_finalize:   Value for the wf_finalize argument (default: "report").

    Returns:
        (stdout, stderr, returncode, sentinel_path) where sentinel_path holds
        recorded close_item calls (empty if not invoked).
    """
    log_file = tmp_path / "wake.log"
    stub_bin = _build_stub_python3(tmp_path)
    sentinel = tmp_path / "close_item_calls.txt"

    log_stub = _build_log_stub(log_file)

    # We source only the close_intake_on_finalize_report function from
    # wake_common.sh by extracting it with sed (avoiding the module-level
    # side-effects of sourcing the full file).  The function itself has no
    # external bash-function dependencies beyond log(), which we stub.
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PATH="{stub_bin}:$PATH"

        {log_stub}

        # Stub pp_run_ops: delegates to stubbed python3 via "python3 -m <module> [args...]"
        # so the python3 interceptor in stub_bin continues to intercept close_item calls.
        pp_run_ops() {{
            local _mod="$1"; shift
            python3 -m "$_mod" "$@"
        }}

        # Extract and load close_intake_on_finalize_report from wake_common.sh.
        # We use awk to grab the function body (from the function header to its
        # matching closing brace) so we avoid sourcing the full file's bootstrap
        # side-effects while still exercising the real production function.
        eval "$(awk '
            /^close_intake_on_finalize_report\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                # Count braces to detect the end of the function
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0; exit }}
                    }}
                }}
            }}
        ' '{_WAKE_COMMON_LIB}')"

        close_intake_on_finalize_report \
            "{task_id}" \
            "{task_readme}" \
            "{project_root}" \
            "{tasks_root}" \
            "{wf_finalize}"
    """)

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout, result.stderr, result.returncode, sentinel


# ---------------------------------------------------------------------------
# (a) One-ticket DONE: close_item must be invoked
# ---------------------------------------------------------------------------


def test_one_ticket_done_close_item_invoked(tmp_path: pathlib.Path) -> None:
    """One-ticket DONE graph: close_intake_on_finalize_report calls close_item.

    A single TESTER task is DONE.  The census sees all siblings (just this one)
    as terminal.  close_item must be invoked for the requirements intake item.
    """
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-1.md")
    tasks_root = project_root / "tasks"
    task_readme = _write_task(
        tasks_root, "TESTER-001-verify", "DONE", str(req_file)
    ) / "README.md"

    stdout, stderr, rc, sentinel = _run_close_intake(
        tmp_path, tasks_root, project_root, "TESTER-001-verify", task_readme
    )

    assert rc == 0, (
        f"close_intake_on_finalize_report must exit 0; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert sentinel.exists(), (
        "close_item must be invoked when the sole task is DONE; "
        f"sentinel file not created.\nstdout: {stdout!r}\nstderr: {stderr!r}"
    )
    calls = sentinel.read_text(encoding="utf-8")
    assert "close_item" in calls, (
        f"Sentinel must contain 'close_item' invocation; got {calls!r}"
    )
    assert "v1.0.0-run-1" in calls, (
        f"close_item must be called with the requirements key; sentinel: {calls!r}"
    )


def test_one_ticket_done_closed_log_line_present(tmp_path: pathlib.Path) -> None:
    """One-ticket DONE graph: the 'closing intake item' log line is emitted.

    Per acceptance criteria, the log line for the closed case must contain
    enough context for a wake-log reader to identify the intake item closed.
    """
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-1b.md")
    tasks_root = project_root / "tasks"
    task_readme = _write_task(
        tasks_root, "TESTER-001-verify", "DONE", str(req_file)
    ) / "README.md"

    stdout, stderr, rc, sentinel = _run_close_intake(
        tmp_path, tasks_root, project_root, "TESTER-001-verify", task_readme
    )

    assert "closing intake item" in stdout.lower() or "closed done" in stdout.lower(), (
        "Wake log must contain a 'closing intake item' or 'closed done' line when closure fires.\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )


# ---------------------------------------------------------------------------
# (b) Two-ticket WORKING sibling: close_item must NOT be invoked
# ---------------------------------------------------------------------------


def test_two_ticket_working_sibling_close_item_not_invoked(
    tmp_path: pathlib.Path,
) -> None:
    """Two-ticket WORKING-sibling graph: close_item is not called.

    One task is DONE; sibling is WORKING (non-terminal).  The census must
    detect the non-terminal sibling and skip closure.
    """
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-2.md")
    tasks_root = project_root / "tasks"
    # The DONE task whose finalize fires
    task_readme = _write_task(
        tasks_root, "TESTER-001-primary", "DONE", str(req_file)
    ) / "README.md"
    # The non-terminal sibling
    _write_task(tasks_root, "TESTER-002-sibling", "WORKING", str(req_file))

    stdout, stderr, rc, sentinel = _run_close_intake(
        tmp_path, tasks_root, project_root, "TESTER-001-primary", task_readme
    )

    assert rc == 0, (
        f"close_intake_on_finalize_report must exit 0 even when skipping; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert not sentinel.exists(), (
        "close_item must NOT be invoked when a sibling is WORKING;\n"
        f"sentinel was unexpectedly created.\nstdout: {stdout!r}\nstderr: {stderr!r}"
    )


def test_two_ticket_working_sibling_census_log_names_sibling(
    tmp_path: pathlib.Path,
) -> None:
    """Two-ticket WORKING-sibling graph: census log line names the non-terminal sibling.

    Per acceptance criteria, the 'skipped, census summary' log line must contain
    the sibling breakdown so future regressions are visible in the wake log.
    The log output must identify both the non-terminal sibling's task ID and state.
    """
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-2b.md")
    tasks_root = project_root / "tasks"
    task_readme = _write_task(
        tasks_root, "TESTER-001-primary", "DONE", str(req_file)
    ) / "README.md"
    _write_task(tasks_root, "TESTER-002-sibling", "WORKING", str(req_file))

    stdout, _stderr, rc, _sentinel = _run_close_intake(
        tmp_path, tasks_root, project_root, "TESTER-001-primary", task_readme
    )

    assert rc == 0
    # The skip log line must name the non-terminal sibling
    assert "TESTER-002-sibling" in stdout, (
        "Census log line must name the non-terminal sibling task ID.\n"
        f"stdout: {stdout!r}"
    )
    assert "WORKING" in stdout, (
        "Census log line must include the sibling's state.\n"
        f"stdout: {stdout!r}"
    )


def test_two_ticket_working_sibling_census_log_contains_skip_summary(
    tmp_path: pathlib.Path,
) -> None:
    """Two-ticket WORKING-sibling graph: log line contains sibling count breakdown."""
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-2c.md")
    tasks_root = project_root / "tasks"
    task_readme = _write_task(
        tasks_root, "TESTER-001-primary", "DONE", str(req_file)
    ) / "README.md"
    _write_task(tasks_root, "TESTER-002-sibling", "WORKING", str(req_file))

    stdout, _stderr, rc, _sentinel = _run_close_intake(
        tmp_path, tasks_root, project_root, "TESTER-001-primary", task_readme
    )

    assert rc == 0
    # The log line must contain "census incomplete" or similar skip signal
    assert "census" in stdout.lower(), (
        "Log output must contain a census-related skip message when a sibling is non-terminal.\n"
        f"stdout: {stdout!r}"
    )
    # sibling count context
    assert "sibling" in stdout.lower(), (
        "Log output must describe the sibling breakdown.\n"
        f"stdout: {stdout!r}"
    )


# ---------------------------------------------------------------------------
# (c) Two-ticket WONT-DO sibling: close_item MUST be invoked (corpse handling)
# ---------------------------------------------------------------------------


def test_two_ticket_wontdo_sibling_close_item_invoked(
    tmp_path: pathlib.Path,
) -> None:
    """Two-ticket WONT-DO-sibling graph: close_item is called (corpse is terminal).

    One task is DONE; sibling is WONT-DO.  WONT-DO is explicitly terminal in
    the census definition.  The census must see both as terminal and fire closure.
    """
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-3.md")
    tasks_root = project_root / "tasks"
    task_readme = _write_task(
        tasks_root, "TESTER-001-primary", "DONE", str(req_file)
    ) / "README.md"
    _write_task(tasks_root, "TESTER-002-corpse", "WONT-DO", str(req_file))

    stdout, stderr, rc, sentinel = _run_close_intake(
        tmp_path, tasks_root, project_root, "TESTER-001-primary", task_readme
    )

    assert rc == 0, (
        f"close_intake_on_finalize_report must exit 0; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert sentinel.exists(), (
        "close_item must be invoked when all siblings are terminal (DONE + WONT-DO);\n"
        f"sentinel not created.\nstdout: {stdout!r}\nstderr: {stderr!r}"
    )
    calls = sentinel.read_text(encoding="utf-8")
    assert "close_item" in calls, (
        f"Sentinel must contain 'close_item'; got {calls!r}"
    )


def test_two_ticket_wontdo_sibling_closed_log_line_present(
    tmp_path: pathlib.Path,
) -> None:
    """Two-ticket WONT-DO-sibling graph: closed log line is emitted.

    Per acceptance criteria, the log line for the closed case must identify
    the intake item so the outcome is visible in the wake log.
    """
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-3b.md")
    tasks_root = project_root / "tasks"
    task_readme = _write_task(
        tasks_root, "TESTER-001-primary", "DONE", str(req_file)
    ) / "README.md"
    _write_task(tasks_root, "TESTER-002-corpse", "WONT-DO", str(req_file))

    stdout, stderr, rc, sentinel = _run_close_intake(
        tmp_path, tasks_root, project_root, "TESTER-001-primary", task_readme
    )

    assert rc == 0
    assert "closing intake item" in stdout.lower() or "closed done" in stdout.lower(), (
        "Wake log must contain a closing/closed line when WONT-DO-sibling corpus is present.\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )


# ---------------------------------------------------------------------------
# Guard 1: non-report finalize value is a no-op
# ---------------------------------------------------------------------------


def test_non_report_finalize_skips_entire_function(tmp_path: pathlib.Path) -> None:
    """Guard 1: when wf_finalize != 'report', the function exits immediately (no-op).

    Release workflows use wf_finalize='tag'.  The function must exit 0 without
    reading README, running the census, or calling close_item.
    """
    project_root = tmp_path / "project"
    req_file = _write_requirements(project_root, "v1.0.0-run-4.md")
    tasks_root = project_root / "tasks"
    task_readme = _write_task(
        tasks_root, "CODER-001-impl", "DONE", str(req_file)
    ) / "README.md"

    stdout, stderr, rc, sentinel = _run_close_intake(
        tmp_path,
        tasks_root,
        project_root,
        "CODER-001-impl",
        task_readme,
        wf_finalize="tag",  # release workflow, not finalize=report
    )

    assert rc == 0, (
        f"Non-report finalize must exit 0 (silent no-op); got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert not sentinel.exists(), (
        "close_item must NOT be invoked when wf_finalize != 'report';\n"
        f"sentinel unexpectedly created.\nstdout: {stdout!r}"
    )


# ===========================================================================
# Bash-level integration tests for sweep_running_intake_census
#
# These tests exercise the per-wake self-healing sweep that closes stranded
# intake items (all sibling tasks terminal but item status still running).
#
# sweep_running_intake_census is extracted from wake_common.sh via awk and
# called with a minimal stub environment:
#   - log() writes to stdout + log file
#   - WF_MANIFEST_FINALIZE="report" (pre-set so Guard 1 passes without
#     wf_load_plugin)
#   - pp_requirements_dir echoes the synthetic requirements dir
#   - _CURRENT_PROJECT, PGAI_PROJECT_ROOT, TASKS_ROOT set to fixture dirs
#   - python3 stub intercepts close_item invocations
#
# These tests cover the two new acceptance criteria shapes:
#   (d) Stranded-item closure (all siblings terminal, item status running)
#   (e) Mid-run negative (one sibling WORKING → no closure)
# ===========================================================================


def _write_requirements_running(
    req_dir: pathlib.Path,
    filename: str,
) -> pathlib.Path:
    """Create a requirements file with ## Status: running.

    Args:
        req_dir:   Requirements directory (will be created if absent).
        filename:  Filename including .md extension.

    Returns:
        pathlib.Path — the created requirements file.
    """
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / filename
    req_file.write_text(
        textwrap.dedent("""\
            # Requirements

            ## Status
            running

            ## Source Branch
            main
        """),
        encoding="utf-8",
    )
    return req_file


def _run_sweep_census(
    tmp_path: pathlib.Path,
    project_root: pathlib.Path,
    tasks_root: pathlib.Path,
    req_dir: pathlib.Path,
    wf_manifest_finalize: str = "report",
) -> tuple[str, str, int, pathlib.Path]:
    """Invoke sweep_running_intake_census in an isolated bash subprocess.

    Stubs:
      - log() — writes to stdout + log file
      - wf_load_plugin — no-op (WF_MANIFEST_FINALIZE pre-set)
      - pp_requirements_dir — echoes req_dir
      - python3 close_item — records invocations to sentinel file
      - close_intake_on_finalize_report — sourced from the real wake_common.sh

    Both sweep_running_intake_census and close_intake_on_finalize_report are
    extracted from wake_common.sh via awk, so the sweep logic remains coupled
    to the real implementation.

    Args:
        tmp_path:             Pytest tmp_path directory.
        project_root:         Synthetic project root.
        tasks_root:           Synthetic tasks/ directory.
        req_dir:              Requirements directory for this project.
        wf_manifest_finalize: Value for WF_MANIFEST_FINALIZE (default: "report").

    Returns:
        (stdout, stderr, returncode, sentinel_path).
    """
    log_file = tmp_path / "sweep_wake.log"
    stub_bin = _build_stub_python3(tmp_path)
    sentinel = tmp_path / "close_item_calls.txt"

    log_stub = _build_log_stub(log_file)

    # Build the script in pieces to avoid Python f-string / .format() brace-
    # escaping issues with the awk body.  All path arguments are converted to
    # str explicitly to avoid PosixPath repr in the bash source.
    stub_bin_s = str(stub_bin)
    req_dir_s = str(req_dir)
    project_root_s = str(project_root)
    tasks_root_s = str(tasks_root)
    wake_common_s = str(_WAKE_COMMON_LIB)

    script_parts: list[str] = [
        "#!/usr/bin/env bash\n",
        "set -euo pipefail\n",
        f"export PATH=\"{stub_bin_s}:$PATH\"\n",
        "\n",
        log_stub,
        "\n",
        "# Stub pp_run_ops: delegates to stubbed python3 so close_item interception works.\n",
        "pp_run_ops() { local _mod=\"$1\"; shift; python3 -m \"$_mod\" \"$@\"; }\n",
        "\n",
        "# Stub wf_load_plugin: WF_MANIFEST_FINALIZE pre-set so Guard 1 passes.\n",
        "wf_load_plugin() { return 0; }\n",
        f'WF_MANIFEST_FINALIZE="{wf_manifest_finalize}"\n',
        "\n",
        "# Stub pp_requirements_dir: return the synthetic requirements dir.\n",
        f'pp_requirements_dir() {{ echo "{req_dir_s}"; }}\n',
        "\n",
        "# Context variables expected by sweep_running_intake_census.\n",
        '_CURRENT_PROJECT="cert-sweep-proj"\n',
        f'PGAI_PROJECT_ROOT="{project_root_s}"\n',
        f'TASKS_ROOT="{tasks_root_s}"\n',
        'PP_workflow_type="testing-only"\n',
        "\n",
        # Awk extractor: each line is a plain string to avoid brace issues.
        "extract_fn() {\n",
        '    local fn_name="$1"\n',
        '    awk -v fn="$fn_name" \'\n',
        '        $0 ~ "^"fn"\\(\\)" { found=1; depth=0 }\n',
        "        found {\n",
        "            print\n",
        '            n = split($0, a, "")\n',
        "            for (i = 1; i <= n; i++) {\n",
        '                if (a[i] == "{") depth++\n',
        '                if (a[i] == "}") {\n',
        "                    depth--\n",
        "                    if (depth == 0) { found=0; exit }\n",
        "                }\n",
        "            }\n",
        "        }\n",
        f"    ' '{wake_common_s}'\n",
        "}\n",
        "\n",
        "# Load close_intake_on_finalize_report (dependency of sweep).\n",
        'eval "$(extract_fn close_intake_on_finalize_report)"\n',
        "\n",
        "# Load sweep_running_intake_census (the function under test).\n",
        'eval "$(extract_fn sweep_running_intake_census)"\n',
        "\n",
        "sweep_running_intake_census\n",
    ]

    script = "".join(script_parts)

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_TEAM_DIR),
    )
    return result.stdout, result.stderr, result.returncode, sentinel


# ---------------------------------------------------------------------------
# (d) Stranded-item fixture: running item, all sibling tasks terminal
# ---------------------------------------------------------------------------


def test_sweep_closes_stranded_item_when_all_siblings_terminal(
    tmp_path: pathlib.Path,
) -> None:
    """Sweep closes a stranded intake item when all sibling tasks are terminal.

    Fixture (the production corpse shape):
      - requirements/v1.0.0-stranded.md  ## Status: running
      - tasks/TESTER-001-verify/          ## State: DONE
      - tasks/TESTER-002-companion/       ## State: DONE
      No task is transitioning — the closing moment was missed.

    Expected: sweep_running_intake_census invokes close_item for
    v1.0.0-stranded (the intake item key).
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_file = _write_requirements_running(req_dir, "v1.0.0-stranded.md")
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-verify", "DONE", str(req_file))
    _write_task(tasks_root, "TESTER-002-companion", "DONE", str(req_file))

    stdout, stderr, rc, sentinel = _run_sweep_census(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0, (
        f"sweep_running_intake_census must exit 0; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert sentinel.exists(), (
        "close_item must be invoked when all sibling tasks are terminal "
        "and the intake item is still running (stranded-item shape).\n"
        f"sentinel not created.\nstdout: {stdout!r}\nstderr: {stderr!r}"
    )
    calls = sentinel.read_text(encoding="utf-8")
    assert "close_item" in calls, (
        f"Sentinel must record a close_item invocation; got {calls!r}"
    )
    assert "v1.0.0-stranded" in calls, (
        f"close_item must be called with the requirements key; sentinel: {calls!r}"
    )


def test_sweep_stranded_item_log_mentions_running_intake(
    tmp_path: pathlib.Path,
) -> None:
    """Sweep logs that it found a running intake item before attempting census.

    The log line must confirm the sweep recognized the stranded item so the
    closure is traceable in wake logs.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_file = _write_requirements_running(req_dir, "v1.0.0-stranded-log.md")
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-verify", "DONE", str(req_file))

    stdout, _stderr, rc, _sentinel = _run_sweep_census(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0
    assert "running intake item" in stdout.lower() or "census" in stdout.lower(), (
        "Sweep must log that it found a running intake item.\n"
        f"stdout: {stdout!r}"
    )


# ---------------------------------------------------------------------------
# (e) Mid-run negative: one sibling WORKING → sweep logs and does not close
# ---------------------------------------------------------------------------


def test_sweep_does_not_close_when_sibling_is_working(
    tmp_path: pathlib.Path,
) -> None:
    """Mid-run negative: sweep does not close when a sibling task is WORKING.

    One intake item is running; one sibling task is DONE while another is
    WORKING (mid-run shape).  The census must detect the non-terminal sibling
    and skip closure.  close_item must NOT be invoked.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_file = _write_requirements_running(req_dir, "v1.0.0-midrun.md")
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-done", "DONE", str(req_file))
    _write_task(tasks_root, "TESTER-002-working", "WORKING", str(req_file))

    stdout, stderr, rc, sentinel = _run_sweep_census(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0, (
        f"sweep_running_intake_census must exit 0 even when skipping; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert not sentinel.exists(), (
        "close_item must NOT be invoked when a sibling task is WORKING.\n"
        f"sentinel unexpectedly created.\nstdout: {stdout!r}\nstderr: {stderr!r}"
    )


def test_sweep_logs_census_attempt_when_sibling_is_working(
    tmp_path: pathlib.Path,
) -> None:
    """Mid-run negative: sweep logs the census attempt when a sibling is WORKING.

    The log output must confirm the sweep attempted the census and found a
    non-terminal sibling, so the skip decision is visible in wake logs.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_file = _write_requirements_running(req_dir, "v1.0.0-midrun-log.md")
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-done", "DONE", str(req_file))
    _write_task(tasks_root, "TESTER-002-working", "WORKING", str(req_file))

    stdout, _stderr, rc, _sentinel = _run_sweep_census(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0
    # The census log line must appear (either from sweep or from close_intake)
    assert "census" in stdout.lower(), (
        "Sweep must log the census attempt when a sibling is non-terminal.\n"
        f"stdout: {stdout!r}"
    )


def test_sweep_no_running_items_logs_none_found(
    tmp_path: pathlib.Path,
) -> None:
    """Guard 2: sweep logs when no running intake items exist (one-glob cost).

    When the requirements directory contains no files with ## Status: running,
    the sweep must log that no running items were found and return 0 without
    invoking close_item.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    # Write a done requirements file (not running)
    req_file = req_dir / "v1.0.0-done.md"
    req_file.write_text(
        textwrap.dedent("""\
            # Requirements

            ## Status
            done

            ## Source Branch
            main
        """),
        encoding="utf-8",
    )
    tasks_root = project_root / "tasks"

    stdout, stderr, rc, sentinel = _run_sweep_census(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0, (
        f"sweep_running_intake_census must exit 0 when no items are running; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert not sentinel.exists(), (
        "close_item must NOT be invoked when no items are running.\n"
        f"sentinel unexpectedly created.\nstdout: {stdout!r}"
    )
    assert "no running" in stdout.lower(), (
        "Sweep must log that no running intake items were found.\n"
        f"stdout: {stdout!r}"
    )


# ===========================================================================
# BUG-0079: Per-item workflow type resolution and narrated skips
#
# These tests cover the three new acceptance criteria shapes:
#   (f) Cfg-less fixture: item has ## Workflow Type: testing-only in the
#       requirements doc, project.cfg has no workflow_type → sweep closes it.
#   (g) Mixed-type fixture: a release-type running item in the same project
#       is narrated-skipped with "finalize=tag — not report"; the testing-only
#       item proceeds normally.
#   (h) Log contract: a wake over a project with ≥1 running item ALWAYS emits
#       ≥1 "sweep-census:" line, regardless of workflow type.
#
# The helper below uses a type-dispatching wf_load_plugin stub instead of
# pre-setting WF_MANIFEST_FINALIZE, so the per-item lookup code path is
# exercised.  The stub mimics the real plugin contracts:
#   testing-only → finalize=report
#   release      → finalize=tag
#   document     → finalize=publish
# ===========================================================================


def _write_requirements_with_workflow_type(
    req_dir: pathlib.Path,
    filename: str,
    status: str,
    workflow_type: str,
) -> pathlib.Path:
    """Create a requirements file with explicit ## Workflow Type and ## Status.

    Args:
        req_dir:        Requirements directory (created if absent).
        filename:       Filename including .md extension.
        status:         Value for ## Status (e.g. "running", "done").
        workflow_type:  Value for ## Workflow Type (e.g. "testing-only", "release").

    Returns:
        pathlib.Path — the created requirements file.
    """
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / filename
    req_file.write_text(
        textwrap.dedent(f"""\
            # Requirements

            ## Status
            {status}

            ## Workflow Type
            {workflow_type}

            ## Source Branch
            main
        """),
        encoding="utf-8",
    )
    return req_file


def _run_sweep_census_with_type_dispatch(
    tmp_path: pathlib.Path,
    project_root: pathlib.Path,
    tasks_root: pathlib.Path,
    req_dir: pathlib.Path,
) -> tuple[str, str, int, pathlib.Path]:
    """Invoke sweep_running_intake_census with a type-dispatching wf_load_plugin stub.

    Unlike _run_sweep_census (which pre-sets WF_MANIFEST_FINALIZE globally),
    this helper stubs wf_load_plugin to return different finalize values based
    on the workflow type argument.  This exercises the per-item lookup code path
    introduced by the BUG-0079 fix.

    Stub dispatch:
      testing-only → WF_MANIFEST_FINALIZE=report (exit 0)
      release      → WF_MANIFEST_FINALIZE=tag    (exit 0)
      document     → WF_MANIFEST_FINALIZE=publish (exit 0)
      *            → exit 1 (unknown type)

    PP_workflow_type is deliberately left unset to simulate a project.cfg that
    lacks a workflow_type entry (the cfg-less scenario from BUG-0079).

    Args:
        tmp_path:      Pytest tmp_path directory.
        project_root:  Synthetic project root.
        tasks_root:    Synthetic tasks/ directory.
        req_dir:       Requirements directory for this project.

    Returns:
        (stdout, stderr, returncode, sentinel_path).
    """
    log_file = tmp_path / "sweep_type_dispatch.log"
    stub_bin = _build_stub_python3(tmp_path)
    sentinel = tmp_path / "close_item_calls.txt"
    log_stub = _build_log_stub(log_file)

    stub_bin_s = str(stub_bin)
    req_dir_s = str(req_dir)
    project_root_s = str(project_root)
    tasks_root_s = str(tasks_root)
    wake_common_s = str(_WAKE_COMMON_LIB)

    script_parts: list[str] = [
        "#!/usr/bin/env bash\n",
        "set -euo pipefail\n",
        f"export PATH=\"{stub_bin_s}:$PATH\"\n",
        "\n",
        log_stub,
        "\n",
        "# Stub pp_run_ops: delegates to stubbed python3 so close_item interception works.\n",
        "pp_run_ops() { local _mod=\"$1\"; shift; python3 -m \"$_mod\" \"$@\"; }\n",
        "\n",
        # Type-dispatching wf_load_plugin stub.
        "wf_load_plugin() {\n",
        "    WF_MANIFEST_FINALIZE=''\n",
        "    case \"$1\" in\n",
        "        testing-only) WF_MANIFEST_FINALIZE='report'; return 0 ;;\n",
        "        release)      WF_MANIFEST_FINALIZE='tag';    return 0 ;;\n",
        "        document)     WF_MANIFEST_FINALIZE='publish'; return 0 ;;\n",
        "        *) return 1 ;;\n",
        "    esac\n",
        "}\n",
        # WF_MANIFEST_FINALIZE starts unset; each item call populates it.
        "WF_MANIFEST_FINALIZE=''\n",
        "\n",
        "# Stub pp_requirements_dir: return the synthetic requirements dir.\n",
        f'pp_requirements_dir() {{ echo "{req_dir_s}"; }}\n',
        "\n",
        "# Context variables; PP_workflow_type deliberately absent (cfg-less).\n",
        '_CURRENT_PROJECT="cfg-less-proj"\n',
        f'PGAI_PROJECT_ROOT="{project_root_s}"\n',
        f'TASKS_ROOT="{tasks_root_s}"\n',
        "\n",
        "extract_fn() {\n",
        '    local fn_name="$1"\n',
        '    awk -v fn="$fn_name" \'\n',
        '        $0 ~ "^"fn"\\(\\)" { found=1; depth=0 }\n',
        "        found {\n",
        "            print\n",
        '            n = split($0, a, "")\n',
        "            for (i = 1; i <= n; i++) {\n",
        '                if (a[i] == "{") depth++\n',
        '                if (a[i] == "}") {\n',
        "                    depth--\n",
        "                    if (depth == 0) { found=0; exit }\n",
        "                }\n",
        "            }\n",
        "        }\n",
        f"    ' '{wake_common_s}'\n",
        "}\n",
        "\n",
        'eval "$(extract_fn close_intake_on_finalize_report)"\n',
        'eval "$(extract_fn sweep_running_intake_census)"\n',
        "\n",
        "sweep_running_intake_census\n",
    ]

    script = "".join(script_parts)

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_TEAM_DIR),
    )
    return result.stdout, result.stderr, result.returncode, sentinel


# ---------------------------------------------------------------------------
# (f) Cfg-less fixture: item declares ## Workflow Type in requirements doc;
#     project.cfg has no workflow_type.  Sweep must resolve type per-item and
#     close the stranded intake item.
# ---------------------------------------------------------------------------


def test_sweep_cfgless_project_closes_testing_only_item(
    tmp_path: pathlib.Path,
) -> None:
    """Cfg-less fixture: per-item type resolution closes a stranded testing-only item.

    The requirements doc carries ## Workflow Type: testing-only; PP_workflow_type
    is unset (no project.cfg entry).  The sweep must read the item's workflow type
    from the doc, resolve finalize=report via wf_load_plugin, and close the item
    when all sibling tasks are terminal.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_file = _write_requirements_with_workflow_type(
        req_dir, "v1.0.0-cfgless.md", "running", "testing-only"
    )
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-verify", "DONE", str(req_file))

    stdout, stderr, rc, sentinel = _run_sweep_census_with_type_dispatch(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0, (
        f"sweep_running_intake_census must exit 0; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    assert sentinel.exists(), (
        "close_item must be invoked for a cfg-less testing-only item when all "
        "siblings are terminal; sentinel not created.\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    calls = sentinel.read_text(encoding="utf-8")
    assert "close_item" in calls, (
        f"Sentinel must record close_item; got {calls!r}"
    )
    assert "v1.0.0-cfgless" in calls, (
        f"close_item must be called with the requirements key; sentinel: {calls!r}"
    )


def test_sweep_cfgless_project_emits_sweep_census_line(
    tmp_path: pathlib.Path,
) -> None:
    """Cfg-less fixture: sweep emits a sweep-census: line for the running item.

    Per the log-contract acceptance criterion, at least one sweep-census: line
    must appear in the wake log whenever running items are present.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_file = _write_requirements_with_workflow_type(
        req_dir, "v1.0.0-cfgless-log.md", "running", "testing-only"
    )
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-verify", "DONE", str(req_file))

    stdout, _stderr, rc, _sentinel = _run_sweep_census_with_type_dispatch(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0
    assert "sweep-census:" in stdout, (
        "Sweep must emit at least one sweep-census: line for a running item.\n"
        f"stdout: {stdout!r}"
    )


# ---------------------------------------------------------------------------
# (g) Mixed-type fixture: a release-type running item is narrated-skipped while
#     a testing-only running item proceeds through the census.
# ---------------------------------------------------------------------------


def test_sweep_mixed_type_narrates_release_item_skip(
    tmp_path: pathlib.Path,
) -> None:
    """Mixed-type fixture: release-type running item is narrated-skipped.

    A project holds two running intake items:
      - testing-only (finalize=report) → census fires, close_item called
      - release (finalize=tag)         → narrated-skip: "finalize=tag — not report"

    The release item must NOT trigger close_item.  Its skip must be logged.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"

    req_testing = _write_requirements_with_workflow_type(
        req_dir, "v1.0.0-testing.md", "running", "testing-only"
    )
    req_release = _write_requirements_with_workflow_type(
        req_dir, "v1.1.0-release.md", "running", "release"
    )
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-verify", "DONE", str(req_testing))

    stdout, stderr, rc, sentinel = _run_sweep_census_with_type_dispatch(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0, (
        f"sweep_running_intake_census must exit 0; got {rc}\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )

    # Release item must produce a narrated-skip line with finalize=tag.
    assert "finalize=tag" in stdout, (
        "Sweep must log 'finalize=tag' when skipping a release-type running item.\n"
        f"stdout: {stdout!r}"
    )
    assert "not report" in stdout.lower(), (
        "Sweep skip line must include 'not report' reason.\n"
        f"stdout: {stdout!r}"
    )

    # Testing-only item must trigger close_item.
    assert sentinel.exists(), (
        "close_item must be invoked for the testing-only item; sentinel not created.\n"
        f"stdout: {stdout!r}\nstderr: {stderr!r}"
    )
    calls = sentinel.read_text(encoding="utf-8")
    assert "v1.0.0-testing" in calls, (
        f"close_item must be called with the testing-only requirements key; "
        f"sentinel: {calls!r}"
    )


def test_sweep_mixed_type_release_item_not_closed(
    tmp_path: pathlib.Path,
) -> None:
    """Mixed-type fixture: release-type item is not closed by the sweep.

    The sentinel must not contain the release requirements key, confirming that
    close_item was not invoked for the non-report workflow item.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"

    _write_requirements_with_workflow_type(
        req_dir, "v1.0.0-testing-b.md", "running", "testing-only"
    )
    req_release = _write_requirements_with_workflow_type(
        req_dir, "v1.1.0-release-b.md", "running", "release"
    )
    tasks_root = project_root / "tasks"
    # No tasks for either item (empty census → testing-only skips closure too,
    # but the release item must still not call close_item).
    tasks_root.mkdir(parents=True, exist_ok=True)

    stdout, stderr, rc, sentinel = _run_sweep_census_with_type_dispatch(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0
    # Release item's narrated-skip must appear.
    assert "finalize=tag" in stdout, (
        "Narrated-skip for release item must appear in output.\n"
        f"stdout: {stdout!r}"
    )
    # close_item must not have been called with the release key.
    if sentinel.exists():
        calls = sentinel.read_text(encoding="utf-8")
        assert "v1.1.0-release-b" not in calls, (
            f"close_item must NOT be called for the release-type item; "
            f"sentinel: {calls!r}"
        )


# ---------------------------------------------------------------------------
# (h) Log contract: wake with ≥1 running items always emits ≥1 sweep-census: line
# ---------------------------------------------------------------------------


def test_sweep_log_contract_report_type_emits_census_line(
    tmp_path: pathlib.Path,
) -> None:
    """Log contract: a running testing-only item emits ≥1 sweep-census: line.

    Per the no-silent-anything rule, every path through the sweep that encounters
    a running item must produce at least one sweep-census: line at the standard
    log level.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    req_file = _write_requirements_with_workflow_type(
        req_dir, "v1.0.0-logcontract.md", "running", "testing-only"
    )
    tasks_root = project_root / "tasks"
    _write_task(tasks_root, "TESTER-001-verify", "DONE", str(req_file))

    stdout, _stderr, rc, _sentinel = _run_sweep_census_with_type_dispatch(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0
    census_lines = [ln for ln in stdout.splitlines() if "sweep-census:" in ln]
    assert len(census_lines) >= 1, (
        "Wake with ≥1 running item must emit ≥1 sweep-census: line.\n"
        f"stdout: {stdout!r}"
    )


def test_sweep_log_contract_non_report_type_emits_census_line(
    tmp_path: pathlib.Path,
) -> None:
    """Log contract: a running release-type item still emits a sweep-census: skip line.

    Even when the item's workflow type is not report-capable, the sweep must
    narrate the skip rather than returning silently.  The log-contract requirement
    is that ≥1 sweep-census: line appears whenever running items exist.
    """
    project_root = tmp_path / "project"
    req_dir = project_root / "requirements"
    _write_requirements_with_workflow_type(
        req_dir, "v1.0.0-logcontract-release.md", "running", "release"
    )
    tasks_root = project_root / "tasks"
    tasks_root.mkdir(parents=True, exist_ok=True)

    stdout, _stderr, rc, _sentinel = _run_sweep_census_with_type_dispatch(
        tmp_path, project_root, tasks_root, req_dir
    )

    assert rc == 0
    census_lines = [ln for ln in stdout.splitlines() if "sweep-census:" in ln]
    assert len(census_lines) >= 1, (
        "Wake with a running non-report item must emit ≥1 sweep-census: skip line "
        "(no silent early returns).\n"
        f"stdout: {stdout!r}"
    )
