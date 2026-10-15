"""
test_verdict_report.py
=======================
Behavioral tests for team/scripts/verdict-report.sh -- the machine-readable,
multi-project counterpart to why.sh that the REST endpoint (GET /verdicts,
team/pgai_agent_kanban/api/reads.py) shells out to.

Acceptance criteria exercised:

  (1) With neither --project nor PGAI_PROJECT_NAME, walks every
      registered project (projects.cfg priority order) and prints one
      "key=value" block per project, blank-line separated.
  (2) A resolved block carries verdict/exit_code/cause/remedy/line for
      every one of the four verdicts.
  (3) A RUNNING block additionally carries task_id, task_state ("WORKING"),
      and minutes_in_state; a BLOCKED block carries task_id and task_state
      ("BLOCKED") but no minutes_in_state; HALTED and IDLE blocks carry
      none of the three.
  (4) A project registered in projects.cfg but with no directory on disk
      renders an "unresolved" block (error=project directory not found)
      instead of a false IDLE, and the script still exits 0.
  (5) --project NAME prints that project's block only, whatever its shape.
  (6) Running the script twice against the same fixture leaves every file
      under the fixture root byte-identical (read-only).
  (7) --details' release_lock block carries held_seconds computed from the
      lock record's acquired_at (a lock acquired 3h ago and refreshed just
      now reads about 10800, while age_seconds -- the refresh age -- reads
      about 0), and reads the literal "none" for a record without
      acquired_at, never an mtime-derived number.
  (8) With --project omitted, PGAI_PROJECT_NAME names the one project
      reported; only with both absent is every registered project walked,
      and --project wins over the variable when both are given.

Tests invoke verdict-report.sh via subprocess, exercising the real
production shell code, matching the pattern in test_why_verdict_engine.py.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import datetime
import hashlib
import os
import pathlib
import subprocess

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_VERDICT_REPORT_SH = _TEAM_DIR / "scripts" / "verdict-report.sh"


def _env(
    kanban_root: pathlib.Path,
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
    return env


def _run(
    args: list[str],
    kanban_root: pathlib.Path,
    timeout: int = 15,
    *,
    project_name: str | None = None,
    temp_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_VERDICT_REPORT_SH), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root, project_name=project_name, temp_dir=temp_dir),
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


def _write_task(project_dir: pathlib.Path, task_id: str, state: str) -> pathlib.Path:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(f"## State\n{state}\n\n## Summary\nfixture task\n", encoding="utf-8")
    return status_file


def _parse_blocks(stdout: str) -> list[dict[str, str]]:
    """Parse blank-line-separated key=value blocks: the documented output
    shape (team/scripts/verdict-report.sh's "Output shape" header)."""
    trimmed = stdout.strip("\n")
    if not trimmed:
        return []
    blocks: list[dict[str, str]] = []
    for raw in trimmed.split("\n\n"):
        fields: dict[str, str] = {}
        for line in raw.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            fields[key] = value
        if fields:
            blocks.append(fields)
    return blocks


def _hash_tree(root: pathlib.Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


# ---------------------------------------------------------------------------
# (1) No-argument walk: priority order, one block per project
# ---------------------------------------------------------------------------


def test_no_arguments_walks_every_project_in_priority_order(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("zeta", 2, True), ("alpha", 1, True)])
    _make_project_dir(tmp_path, "zeta")
    _make_project_dir(tmp_path, "alpha")

    result = _run([], tmp_path)

    assert result.returncode == 0, f"verdict-report.sh failed: {result.stderr!r}"
    blocks = _parse_blocks(result.stdout)
    assert len(blocks) == 2
    assert blocks[0]["project"] == "alpha", "priority=1 (alpha) must print before priority=2 (zeta)"
    assert blocks[1]["project"] == "zeta"
    for block in blocks:
        assert block["verdict"] in {"RUNNING", "BLOCKED", "HALTED", "IDLE"}


def test_no_registered_projects_fails_rather_than_printing_nothing(tmp_path: pathlib.Path) -> None:
    result = _run([], tmp_path)

    assert result.returncode != 0
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# (2) Every one of the four verdicts carries verdict/exit_code/cause/remedy/line
# ---------------------------------------------------------------------------


def test_idle_project_block_carries_verdict_cause_remedy_line(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 0
    blocks = _parse_blocks(result.stdout)
    assert len(blocks) == 1
    block = blocks[0]
    assert block["verdict"] == "IDLE"
    assert block["exit_code"] == "2"
    assert "DONE or WONT-DO" in block["cause"]
    assert block["remedy"]
    assert block["line"].startswith("demo: IDLE")
    assert "no non-terminal tasks" in block["line"]
    assert "task_id" not in block
    assert "task_state" not in block
    assert "minutes_in_state" not in block


def test_halted_project_block_carries_verdict_cause_remedy_line(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    halt_file = project_dir / "HALT"
    halt_file.write_text("# CM HALT: repeated release failure\n", encoding="utf-8")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 0
    block = _parse_blocks(result.stdout)[0]
    assert block["verdict"] == "HALTED"
    assert block["exit_code"] == "5"
    assert str(halt_file) in block["cause"]
    assert "operator" in block["remedy"]
    assert block["line"].startswith("demo: HALTED")
    assert "task_id" not in block
    assert "minutes_in_state" not in block


def test_disabled_project_block_renders_idle_naming_disabled_cause(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, False)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 0
    block = _parse_blocks(result.stdout)[0]
    assert block["verdict"] == "IDLE"
    assert "disabled" in block["cause"]
    assert "enabled=true" in block["remedy"]


# ---------------------------------------------------------------------------
# (3) RUNNING and BLOCKED carry task fields; HALTED/IDLE do not
# ---------------------------------------------------------------------------


def test_running_block_carries_task_id_state_and_minutes(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 0
    block = _parse_blocks(result.stdout)[0]
    assert block["verdict"] == "RUNNING"
    assert block["task_id"] == "CODER-20260910-001-demo"
    assert block["task_state"] == "WORKING"
    assert "minutes_in_state" in block
    int(block["minutes_in_state"])  # parses as an integer


def test_blocked_block_carries_task_id_and_state_but_no_minutes(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BLOCKED")

    result = _run(["--project", "demo"], tmp_path)

    assert result.returncode == 0
    block = _parse_blocks(result.stdout)[0]
    assert block["verdict"] == "BLOCKED"
    assert block["task_id"] == "CODER-20260910-001-demo"
    assert block["task_state"] == "BLOCKED"
    assert "minutes_in_state" not in block


# ---------------------------------------------------------------------------
# (4) Registered-but-unresolvable project directory
# ---------------------------------------------------------------------------


def test_registered_project_with_no_directory_yields_unresolved_block_not_false_idle(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("ghost", 1, True)])
    # Deliberately do NOT create projects/ghost/ at all.

    result = _run(["--project", "ghost"], tmp_path)

    assert result.returncode == 0, "an unresolved project is data, not a script failure"
    block = _parse_blocks(result.stdout)[0]
    assert block["project"] == "ghost"
    assert block.get("error") == "project directory not found"
    assert "verdict" not in block, "an unresolved block must never carry a verdict word"
    assert "ghost" in block["cause"]
    assert block["remedy"]


def test_walk_includes_unresolved_project_alongside_resolved_ones(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("alpha", 1, True), ("ghost", 2, True)])
    _make_project_dir(tmp_path, "alpha")
    # ghost has no directory.

    result = _run([], tmp_path)

    assert result.returncode == 0
    blocks = _parse_blocks(result.stdout)
    assert len(blocks) == 2
    assert blocks[0]["project"] == "alpha"
    assert blocks[0]["verdict"] == "IDLE"
    assert blocks[1]["project"] == "ghost"
    assert blocks[1].get("error") == "project directory not found"


# ---------------------------------------------------------------------------
# (5) --project scoping
# ---------------------------------------------------------------------------


def test_project_flag_prints_only_that_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("alpha", 1, True), ("beta", 2, True)])
    _make_project_dir(tmp_path, "alpha")
    beta_dir = _make_project_dir(tmp_path, "beta")
    _write_task(beta_dir, "CODER-20260910-002-demo", "BLOCKED")

    result = _run(["--project", "beta"], tmp_path)

    assert result.returncode == 0
    blocks = _parse_blocks(result.stdout)
    assert len(blocks) == 1
    assert blocks[0]["project"] == "beta"
    assert blocks[0]["verdict"] == "BLOCKED"


# ---------------------------------------------------------------------------
# (6) Read-only / idempotency
# ---------------------------------------------------------------------------


def test_run_twice_leaves_every_file_under_fixture_root_byte_identical(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("alpha", 1, True), ("beta", 2, True)])
    alpha_dir = _make_project_dir(tmp_path, "alpha")
    _write_task(alpha_dir, "CODER-20260910-001-demo", "WORKING")
    beta_dir = _make_project_dir(tmp_path, "beta")
    _write_task(beta_dir, "CODER-20260910-002-demo", "BLOCKED")

    before = _hash_tree(tmp_path)

    first = _run([], tmp_path)
    second = _run([], tmp_path)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout

    after = _hash_tree(tmp_path)
    assert before == after, "verdict-report.sh must never write, edit, or remove any file under the fixture root"


# ---------------------------------------------------------------------------
# (7) --details: held_seconds from acquired_at, age_seconds is refresh age
# ---------------------------------------------------------------------------

_HELD_THREE_HOURS = 3 * 3600
# Seconds of slack for a slow test run between seeding the lock and the
# script reading the clock.
_CLOCK_SLACK_SECONDS = 30


def _iso_ago(seconds: int) -> str:
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return then.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_fresh_lock(kanban_root: pathlib.Path, name: str, record: str) -> pathlib.Path:
    """Write a lock record and touch it so its mtime is now -- what the
    held-lock refresh leaves behind on a lock held for hours."""
    locks_dir = kanban_root / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_file = locks_dir / name
    lock_file.write_text(record, encoding="utf-8")
    os.utime(lock_file, None)
    return lock_file


def _release_lock_blocks(stdout: str) -> dict[str, dict[str, str]]:
    return {
        pathlib.Path(b["path"]).name: b
        for b in _parse_blocks(stdout)
        if b.get("kind") == "release_lock" and "path" in b
    }


def test_details_release_lock_reports_held_seconds_from_acquired_at_not_mtime(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")
    _seed_fresh_lock(
        tmp_path, "repo-wake-demo.lock",
        f"holder=coder\npid={os.getpid()}\nop=wake-chain:demo\ntask=none\n"
        f"acquired_at={_iso_ago(_HELD_THREE_HOURS)}\n",
    )

    result = _run(["--project", "demo", "--details"], tmp_path)

    assert result.returncode == 0, f"verdict-report.sh --details failed: {result.stderr!r}"
    block = _release_lock_blocks(result.stdout)["repo-wake-demo.lock"]
    held = int(block["held_seconds"])
    assert _HELD_THREE_HOURS <= held <= _HELD_THREE_HOURS + _CLOCK_SLACK_SECONDS, block
    assert 0 <= int(block["age_seconds"]) <= _CLOCK_SLACK_SECONDS, (
        f"age_seconds is the refresh age (mtime), which was just touched: {block}"
    )


def test_details_release_lock_without_acquired_at_reports_held_none(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")
    for name, extra in (("repo-legacy-demo.lock", ""), ("repo-torn-demo.lock", "acquired_at=2026-10-01T04:2\n")):
        _seed_fresh_lock(
            tmp_path, name,
            f"holder=coder\npid={os.getpid()}\nop=wake-chain:demo\ntask=T-1\n{extra}",
        )

    result = _run(["--project", "demo", "--details"], tmp_path)

    assert result.returncode == 0, f"verdict-report.sh --details failed: {result.stderr!r}"
    blocks = _release_lock_blocks(result.stdout)
    for name in ("repo-legacy-demo.lock", "repo-torn-demo.lock"):
        assert blocks[name]["held_seconds"] == "none", blocks[name]
        assert blocks[name]["age_seconds"].isdigit(), blocks[name]


# ---------------------------------------------------------------------------
# (8) An omitted --project falls back to PGAI_PROJECT_NAME; the walk runs only
#     when neither names a project
# ---------------------------------------------------------------------------


def _two_project_root(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A throwaway root registering aa (priority 1) and bb (priority 2),
    plus a temp directory outside it; returns (root, temp_dir)."""
    root = tmp_path / "root"
    temp_dir = tmp_path / "scratch"
    root.mkdir()
    temp_dir.mkdir()
    _write_projects_cfg(root, [("aa", 1, True), ("bb", 2, True)])
    _make_project_dir(root, "aa")
    _make_project_dir(root, "bb")
    return root, temp_dir


def test_omitted_project_with_env_unset_walks_every_registered_project(tmp_path: pathlib.Path) -> None:
    root, temp_dir = _two_project_root(tmp_path)

    result = _run([], root, temp_dir=temp_dir)

    assert result.returncode == 0, f"verdict-report.sh failed: {result.stderr!r}"
    assert [b["project"] for b in _parse_blocks(result.stdout)] == ["aa", "bb"]


def test_omitted_project_with_env_set_reports_only_the_env_named_project(tmp_path: pathlib.Path) -> None:
    root, temp_dir = _two_project_root(tmp_path)

    # bb is second in the walk, so a block for it alone cannot be the walk cut short.
    result = _run([], root, project_name="bb", temp_dir=temp_dir)

    assert result.returncode == 0, f"verdict-report.sh failed: {result.stderr!r}"
    assert [b["project"] for b in _parse_blocks(result.stdout)] == ["bb"]


def test_project_flag_wins_over_env_named_project(tmp_path: pathlib.Path) -> None:
    root, temp_dir = _two_project_root(tmp_path)

    result = _run(["--project", "aa"], root, project_name="bb", temp_dir=temp_dir)

    assert result.returncode == 0, f"verdict-report.sh failed: {result.stderr!r}"
    assert [b["project"] for b in _parse_blocks(result.stdout)] == ["aa"]
