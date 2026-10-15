"""
test_why_verdict_engine.py
============================
Behavioral tests for the shared verdict engine (team/pgai_agent_kanban/ops/verdict.py)
and its CLI surface, team/scripts/why.sh, over its two new invocation
shapes: no --project (the project PGAI_PROJECT_NAME names, or every
registered project when that is unset too) and --project NAME (that
project's verdict only).

Acceptance criteria exercised:

  (1) why.sh with no arguments, against a fixture with two registered
      projects, prints exactly one verdict line per project in
      projects.cfg priority order, and each line's verdict is one of
      RUNNING, BLOCKED, HALTED, IDLE.
  (2) Each printed line names a cause and a runnable remedy in the same
      sentence, for one fixture of each of the four verdicts.
  (3) A WORKING task renders RUNNING and names the task id, its state, and
      its whole minutes in state; a WORKING task past the stall threshold
      does not render as a bare healthy RUNNING and names the stall and
      its remedy.
  (4) A project-scoped HALT file renders HALTED; a project whose only
      non-terminal task is BLOCKED renders BLOCKED; a project with no
      non-terminal tasks renders IDLE.
  (5) why.sh --project NAME prints that project's line only and exits with
      that project's verdict code; the no-arg invocation exits with the
      worst verdict code across all projects.
  (6) Running why.sh twice against a fixture leaves every file under the
      fixture kanban root byte-identical.
  (7) With --project omitted, PGAI_PROJECT_NAME names the one project
      reported; only with both absent is every registered project walked,
      and --project wins over the variable when both are given.

Tests invoke why.sh via subprocess, exercising the real production shell
code rather than a reimplementation of it, matching the pattern in
test_defer_operator_verbs.py. VERDICT_NOW_EPOCH (team/pgai_agent_kanban/ops/verdict.py's
testability seam) pins "now" for stall-threshold tests instead of sleeping
in real wall-clock time.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"

# Mirrors STALL_THRESHOLD_MINUTES in team/pgai_agent_kanban/ops/verdict.py.
# Duplicated here (not sourced) because these are black-box subprocess
# tests of the shipped CLI, matching this file's sibling test files; a
# change to the production constant should make the stall-boundary tests
# below fail loud rather than silently track a moving target.
_STALL_THRESHOLD_MINUTES = 60


def _env(
    kanban_root: pathlib.Path,
    now_epoch: int | None = None,
    *,
    project_name: str | None = None,
    temp_dir: pathlib.Path | None = None,
) -> dict[str, str]:
    """The script's environment, pointed at *kanban_root*.

    PGAI_PROJECT_NAME is cleared unless *project_name* sets it, so an
    ambient project never narrows a walk. The two pre-resolved path exports
    are cleared and both root names repointed; *temp_dir*, when given,
    repoints the temp root too (project README "Live-install safety")."""
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_PROJECT_ROOT", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["KANBAN_ROOT"] = str(kanban_root)
    if temp_dir is not None:
        env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_dir)
    if project_name is not None:
        env["PGAI_PROJECT_NAME"] = project_name
    if now_epoch is not None:
        env["VERDICT_NOW_EPOCH"] = str(now_epoch)
    else:
        env.pop("VERDICT_NOW_EPOCH", None)
    return env


def _run(
    args: list[str],
    kanban_root: pathlib.Path,
    now_epoch: int | None = None,
    timeout: int = 15,
    *,
    project_name: str | None = None,
    temp_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_WHY_SH), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root, now_epoch, project_name=project_name, temp_dir=temp_dir),
        timeout=timeout,
    )


def _write_projects_cfg(kanban_root: pathlib.Path, projects: list[tuple[str, int, bool]]) -> None:
    """projects: list of (name, priority, enabled)."""
    lines = ["# Format: INI-style"]
    for name, priority, enabled in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append(f"enabled={'true' if enabled else 'false'}")
        lines.append("")
    (kanban_root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_project_dir(kanban_root: pathlib.Path, name: str) -> pathlib.Path:
    project_dir = kanban_root / "projects" / name
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    return project_dir


def _write_task(
    project_dir: pathlib.Path,
    task_id: str,
    state: str,
    mtime_epoch: int | None = None,
) -> pathlib.Path:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(f"## State\n{state}\n\n## Summary\nfixture task\n", encoding="utf-8")
    if mtime_epoch is not None:
        os.utime(status_file, (mtime_epoch, mtime_epoch))
    return status_file


def _hash_tree(root: pathlib.Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


# ---------------------------------------------------------------------------
# (1) No-argument walk: priority order, one line per project, valid vocabulary
# ---------------------------------------------------------------------------


def test_no_arguments_walks_every_project_in_priority_order_with_one_of_four_verdicts(
    tmp_path: pathlib.Path,
) -> None:
    # Registered in reverse-priority order in the cfg file to prove the
    # output follows the declared `priority=` field, not registration or
    # alphabetical order.
    _write_projects_cfg(tmp_path, [("zeta", 2, True), ("alpha", 1, True)])
    _make_project_dir(tmp_path, "zeta")
    _make_project_dir(tmp_path, "alpha")

    result = _run([], tmp_path)

    # Both fixture projects have no tasks at all, so both resolve to IDLE
    # (exit 2) -- a valid, expected verdict, not a failure. The walk itself
    # only fails (a pre-flight refusal) at exit 1, which this asserts
    # against rather than requiring the healthy exit 0.
    assert result.returncode != 1, f"why.sh refused: {result.stderr!r}"
    lines = result.stdout.strip("\n").splitlines()
    assert len(lines) == 2, f"expected exactly one line per project, got: {lines!r}"

    assert lines[0].startswith("alpha:"), "priority=1 (alpha) must print before priority=2 (zeta)"
    assert lines[1].startswith("zeta:")

    for line in lines:
        verdict_word = line.split(": ", 1)[1].split(" ", 1)[0].rstrip("—-")
        assert verdict_word in {"RUNNING", "BLOCKED", "HALTED", "IDLE"}, (
            f"line does not open with a canonical verdict word: {line!r}"
        )


def test_no_arguments_with_no_registered_projects_fails_rather_than_printing_nothing(
    tmp_path: pathlib.Path,
) -> None:
    result = _run([], tmp_path)

    assert result.returncode != 0
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# (2)/(4) Each of the four verdicts: cause + remedy in the same sentence
# ---------------------------------------------------------------------------


def test_idle_project_with_no_tasks_renders_idle_with_cause_and_remedy(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 2, f"expected the IDLE exit code, got {result.returncode}: {result.stdout!r}"
    line = result.stdout.strip()
    assert line.startswith("demo: IDLE")
    assert "remedy:" in line
    assert "no non-terminal tasks" in line


def test_disabled_project_renders_idle_naming_the_disabled_cause(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, False)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 2
    line = result.stdout.strip()
    assert line.startswith("demo: IDLE")
    assert "disabled" in line
    assert "enabled=true" in line, "remedy must name the concrete fix (set enabled=true)"


def test_blocked_task_renders_blocked_with_cause_and_remedy(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BLOCKED")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 4, f"expected the BLOCKED exit code, got {result.returncode}: {result.stdout!r}"
    line = result.stdout.strip()
    assert line.startswith("demo: BLOCKED")
    assert "CODER-20260910-001-demo" in line
    assert "remedy:" in line


def test_halted_project_renders_halted_naming_the_halt_file_and_operator_only_remedy(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    halt_file = project_dir / "HALT"
    halt_file.write_text("# CM HALT: repeated release failure\n", encoding="utf-8")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 5, f"expected the HALTED exit code, got {result.returncode}: {result.stdout!r}"
    line = result.stdout.strip()
    assert line.startswith("demo: HALTED")
    assert str(halt_file) in line
    assert "repeated release failure" in line
    assert "remedy:" in line
    assert "operator" in line


def test_halt_precedes_task_state_in_precedence(tmp_path: pathlib.Path) -> None:
    # A project that is both HALTED and has a WORKING task must render
    # HALTED -- HALT outranks task state (team/pgai_agent_kanban/ops/verdict.py
    # precedence order).
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")
    (project_dir / "HALT").touch()

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 5
    assert result.stdout.strip().startswith("demo: HALTED")


# ---------------------------------------------------------------------------
# (3) RUNNING: progress evidence and stall detection
# ---------------------------------------------------------------------------


def test_working_task_renders_running_naming_task_id_state_and_whole_minutes(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    now = 1_800_000_000
    entered_state = now - (12 * 60)  # 12 whole minutes ago
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING", mtime_epoch=entered_state)

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == 0, f"expected the healthy RUNNING exit code, got {result.returncode}"
    line = result.stdout.strip()
    assert line.startswith("demo: RUNNING")
    assert "CODER-20260910-001-demo" in line
    assert "WORKING" in line
    assert "12m" in line, f"expected whole-minutes-in-state '12m' in: {line!r}"


def test_working_task_within_stall_threshold_is_not_flagged_as_stalled(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    now = 1_800_000_000
    entered_state = now - ((_STALL_THRESHOLD_MINUTES - 1) * 60)
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING", mtime_epoch=entered_state)

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert result.returncode == 0
    # A healthy RUNNING line legitimately names the stall *threshold* as
    # context ("... stays within the 60m stall threshold"); what it must
    # NOT do is describe an actual stall having occurred.
    assert "past the" not in result.stdout
    assert "churn or a hang" not in result.stdout


def test_working_task_past_stall_threshold_does_not_render_as_bare_healthy_running(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    now = 1_800_000_000
    entered_state = now - ((_STALL_THRESHOLD_MINUTES + 1) * 60)
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING", mtime_epoch=entered_state)

    result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    # The verdict WORD stays RUNNING (a WORKING task is, definitionally,
    # still running -- the vocabulary forbids a fifth word), but the exit
    # code must differ from the healthy case and the line must name the
    # stall and its remedy: this is the whole point of stall detection
    # being watchable from cron without parsing the line's text.
    assert result.returncode != 0, "a stalled lane must not exit with the healthy code"
    line = result.stdout.strip()
    assert line.startswith("demo: RUNNING")
    assert "stall" in line.lower()
    assert "remedy:" in line
    assert f"{_STALL_THRESHOLD_MINUTES + 1}m" in line


def test_stalled_running_and_healthy_running_use_different_exit_codes(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    now = 1_800_000_000

    healthy_mtime = now - (5 * 60)
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING", mtime_epoch=healthy_mtime)
    healthy_result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    stalled_mtime = now - ((_STALL_THRESHOLD_MINUTES + 5) * 60)
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING", mtime_epoch=stalled_mtime)
    stalled_result = _run(["--project", "demo"], tmp_path, now_epoch=now)

    assert healthy_result.returncode == 0
    assert stalled_result.returncode != 0
    assert healthy_result.returncode != stalled_result.returncode


# ---------------------------------------------------------------------------
# (5) --project scoping and exit-code aggregation
# ---------------------------------------------------------------------------


def test_project_flag_prints_only_that_project_and_exits_with_its_own_code(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("alpha", 1, True), ("beta", 2, True)])
    _make_project_dir(tmp_path, "alpha")
    beta_dir = _make_project_dir(tmp_path, "beta")
    _write_task(beta_dir, "CODER-20260910-002-demo", "BLOCKED")

    result = _run(["--project", "beta"], tmp_path)

    assert result.returncode == 4
    lines = result.stdout.strip("\n").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("beta:")


def test_no_argument_walk_exits_with_the_worst_verdict_code_across_all_projects(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("alpha", 1, True), ("beta", 2, True)])
    _make_project_dir(tmp_path, "alpha")  # alpha: IDLE (exit 2)
    beta_dir = _make_project_dir(tmp_path, "beta")
    _write_task(beta_dir, "CODER-20260910-002-demo", "BLOCKED")  # beta: BLOCKED (exit 4)

    result = _run([], tmp_path)

    assert result.returncode == 4, "the worst (BLOCKED, exit 4) verdict must win over IDLE (exit 2)"
    lines = result.stdout.strip("\n").splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("alpha: IDLE")
    assert lines[1].startswith("beta: BLOCKED")


def test_details_still_requires_a_project(tmp_path: pathlib.Path) -> None:
    result = _run(["--details"], tmp_path)

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "remedy:" in result.stderr
    assert "--project" in result.stderr


# ---------------------------------------------------------------------------
# (6) Read-only / idempotency
# ---------------------------------------------------------------------------


def test_why_run_twice_leaves_every_file_under_the_fixture_root_byte_identical(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("alpha", 1, True), ("beta", 2, True)])
    alpha_dir = _make_project_dir(tmp_path, "alpha")
    _write_task(alpha_dir, "CODER-20260910-001-demo", "WORKING")
    beta_dir = _make_project_dir(tmp_path, "beta")
    _write_task(beta_dir, "CODER-20260910-002-demo", "BLOCKED")

    before = _hash_tree(tmp_path)

    first = _run([], tmp_path)
    second = _run([], tmp_path)

    assert first.returncode == second.returncode
    assert first.stdout == second.stdout

    after = _hash_tree(tmp_path)
    assert before == after, "why.sh must never write, edit, or remove any file under the fixture root"


# ---------------------------------------------------------------------------
# (7) An omitted --project falls back to PGAI_PROJECT_NAME; the walk runs only
#     when neither names a project
# ---------------------------------------------------------------------------


def _two_project_root(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A throwaway root registering aa (priority 1) and bb (priority 2), both
    with no tasks (IDLE, exit 2), plus a temp directory outside it; returns
    (root, temp_dir)."""
    root = tmp_path / "root"
    temp_dir = tmp_path / "scratch"
    root.mkdir()
    temp_dir.mkdir()
    _write_projects_cfg(root, [("aa", 1, True), ("bb", 2, True)])
    _make_project_dir(root, "aa")
    _make_project_dir(root, "bb")
    return root, temp_dir


def _line_projects(stdout: str) -> list[str]:
    return [line.split(":", 1)[0] for line in stdout.strip("\n").splitlines()]


def test_omitted_project_with_env_unset_walks_every_registered_project(tmp_path: pathlib.Path) -> None:
    root, temp_dir = _two_project_root(tmp_path)

    result = _run([], root, temp_dir=temp_dir)

    assert result.returncode == 2, f"expected IDLE (exit 2) for both projects: {result.stderr!r}"
    assert _line_projects(result.stdout) == ["aa", "bb"]


def test_omitted_project_with_env_set_reports_only_the_env_named_project(tmp_path: pathlib.Path) -> None:
    root, temp_dir = _two_project_root(tmp_path)

    # bb is second in the walk, so a line for it alone cannot be the walk cut short.
    result = _run([], root, project_name="bb", temp_dir=temp_dir)

    assert result.returncode == 2, f"expected bb's IDLE (exit 2): {result.stderr!r}"
    assert _line_projects(result.stdout) == ["bb"]


def test_project_flag_wins_over_env_named_project(tmp_path: pathlib.Path) -> None:
    root, temp_dir = _two_project_root(tmp_path)

    result = _run(["--project", "aa"], root, project_name="bb", temp_dir=temp_dir)

    assert result.returncode == 2, f"expected aa's IDLE (exit 2): {result.stderr!r}"
    assert _line_projects(result.stdout) == ["aa"]
