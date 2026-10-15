"""
test_verify_rc_state_route_cli_parity.py
========================================
The "verify-rc-state" operator verb has one implementation,
``pgai_agent_kanban.ops.rc_state.verify_rc_state``:

  * ``GET /verify-rc-state`` calls it in process and starts no process;
  * ``team/scripts/verify-rc-state.sh`` reaches it through ``pp_run_ops``;
  * both answer with the same exit code, stdout and stderr for the same
    board -- a clean report, a report with findings, ``--verbose`` /
    ``verbose=true``, and a pre-flight refusal.

Every board is built under tmp_path with a dev tree that is not a git
repository and no released version, so neither surface needs git; the CLI
runs with an explicit, minimal environment (build_scrubbed_env).
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_DIR / "scripts" / "verify-rc-state.sh"
_PROJECT = "alpha-proj"


def _build_board(root: pathlib.Path, *, findings: bool) -> pathlib.Path:
    """A registered project whose dev tree is a plain directory, with one
    task in the OWNER-ROLE-date-seq id form the queue checks read. With
    *findings*, a queue entry with no folder, a state/marker mismatch, a
    dangling prerequisite and a missing bundled bug."""
    project = root / "projects" / _PROJECT
    tasks = project / "tasks"
    (tasks / "queues").mkdir(parents=True)
    (project / "requirements").mkdir()
    (project / "bugs").mkdir()
    (root / "tmp").mkdir()
    devtree = root / "devtree"
    devtree.mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (project / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {devtree}\n", encoding="utf-8"
    )
    (project / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    task = tasks / "CLAUDE-CODER-20260101-001-alpha"
    task.mkdir()
    (task / "status.md").write_text("## State\nWORKING\n", encoding="utf-8")
    (task / "README.md").write_text("# Task\n\n## Prerequisites\nnone\n", encoding="utf-8")
    (project / "bugs" / "BUG-0001-one.md").write_text("# BUG-0001-one\n", encoding="utf-8")
    (project / "requirements" / "v1.0.1-bugfix-bundle-a.md").write_text(
        f"# Bundle\n\n## Bundled Items\n\n- BUG-0001-one.md (`{project / 'bugs' / 'BUG-0001-one.md'}`)\n",
        encoding="utf-8",
    )
    marker = "A"
    if findings:
        marker = " "
        (task / "README.md").write_text(
            "# Task\n\n## Prerequisites\n- CLAUDE-CODER-20260101-009-missing\n", encoding="utf-8"
        )
        with (project / "requirements" / "v1.0.1-bugfix-bundle-a.md").open("a", encoding="utf-8") as bundle:
            bundle.write("- BUG-0002-gone.md (`/nowhere/BUG-0002-gone.md`)\n")
        (tasks / "queues" / "pm_backlog.md").write_text("- [ ] CLAUDE-PM-20260101-002-ghost — x\n", encoding="utf-8")
    (tasks / "queues" / "coder_backlog.md").write_text(
        f"- [{marker}] CLAUDE-CODER-20260101-001-alpha — alpha\n", encoding="utf-8"
    )
    return root


def _env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict:
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )


def _run_cli(tmp_path: pathlib.Path, root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT), *args], capture_output=True, text=True,
        env=_env(tmp_path, root), cwd=str(root), stdin=subprocess.DEVNULL, timeout=120,
    )


@pytest.mark.parametrize(
    ("findings", "verbose", "exit_code"),
    [(False, False, 0), (False, True, 0), (True, False, 1), (True, True, 1)],
    ids=["clean", "clean-verbose", "findings", "findings-verbose"],
)
def test_route_and_cli_reach_one_function_and_print_the_same_report(
    tmp_path: pathlib.Path, findings: bool, verbose: bool, exit_code: int
) -> None:
    rest_board = _build_board(tmp_path / "rest", findings=findings)
    cli_board = _build_board(tmp_path / "cli", findings=findings)
    params = {"project": _PROJECT, **({"verbose": "true"} if verbose else {})}
    cli_args = [_PROJECT, *(["--verbose"] if verbose else [])]

    result = assert_route_cli_parity(
        "verify-rc-state",
        build_api_client(rest_board),
        params=params,
        cli_args=cli_args,
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.rc_state.verify_rc_state"
    envelope = result.response.json()
    assert envelope["exit_code"] == exit_code, envelope
    assert result.response.status_code == (200 if exit_code == 0 else 500)

    cli = _run_cli(tmp_path, cli_board, *cli_args)
    assert cli.returncode == exit_code, cli.stderr
    assert envelope["stdout"] == cli.stdout.replace(str(cli_board), str(rest_board))
    assert envelope["stderr"] == cli.stderr == ""
    assert cli.stdout.endswith("ok\n") and "verify-rc-state: " in cli.stdout
    if findings:
        assert "[ERROR] Queue entry has no folder on disk: CLAUDE-PM-20260101-002-ghost" in cli.stdout
        assert "[ERROR] status/marker mismatch: CLAUDE-CODER-20260101-001-alpha state=WORKING" in cli.stdout
        assert "[ERROR] Prerequisites: dangling reference" in cli.stdout
        assert "[ERROR] Bundle invariant: v1.0.1-bugfix-bundle-a.md references BUG-0002-gone" in cli.stdout
    if verbose:
        assert "[OK]    Active RC is 'none' (no RC in flight)" in cli.stdout
    else:
        assert "[OK]" not in cli.stdout


def test_unknown_project_is_refused_the_same_way_by_route_and_cli(tmp_path: pathlib.Path) -> None:
    rest_board = _build_board(tmp_path / "rest", findings=False)
    cli_board = _build_board(tmp_path / "cli", findings=False)

    with build_api_client(rest_board) as client:
        response = client.get("/verify-rc-state", params={"project": "nope"})
    cli = _run_cli(tmp_path, cli_board, "nope")

    envelope = response.json()
    assert response.status_code == 404
    assert envelope["exit_code"] == cli.returncode == 2
    assert envelope["stdout"] == cli.stdout == ""
    assert "project_resolve.not_found" in cli.stderr
    assert envelope["stderr"] == cli.stderr.replace(str(cli_board), str(rest_board))
