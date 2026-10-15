"""
test_cm_scripts_skip_fetch_under_non_cm_role.py
=================================================
Integration coverage for the pre-flight ``git fetch origin`` in
scripts/cm/cancel-rc.sh and scripts/cm/open-rc.sh: under a non-CM
``PGAI_AGENT_ROLE`` the fetch is skipped with a narrated line instead of
being attempted, refused by the boundary guard, and recorded as evidence of
a crossing nobody meant to make; under ``cm`` -- and under no role at all,
an operator's own shell -- the fetch is reached exactly as before.

Each script runs for real, through the real shim (scripts/wake/git-guard/git,
first on PATH, pinned to a network-intercepting stub git behind it) against a
throwaway kanban root whose project has ``push_to_remote = true`` -- the only
setting under which the fetch is attempted at all -- and a dev tree that is a
real repository whose remote is an scp-style URL the guard can never exempt.
The stub (tests/fixtures/git_network_stub.py) logs any fetch/pull/push/
ls-remote/clone it receives and fails offline, so a CM run's fetch is observed
rather than performed and no test ever touches a real remote.

The scripts are expected to stop soon after the fetch step (the fixture has
no RC to cancel, and open-rc's ff-only pull from a never-fetched origin
cannot succeed); their exit status is not the subject. What is asserted:

  - non-CM role: no git-boundary-guard.jsonl record, a stdout line that
    names the role and says the fetch was skipped, and no fetch on the
    stub's log;
  - cm role: the "Fetching origin..." line is printed and the fetch reaches
    the stub, with no evidence record;
  - no role at all: the fetch is reached too -- an operator running the
    script by hand is not a non-CM agent, and their behaviour is unchanged.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.git_network_stub import install_git_network_stub

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_CM_SCRIPTS_DIR = _TEAM_DIR / "scripts" / "cm"
_SHIM_DIR = _TEAM_DIR / "scripts" / "wake" / "git-guard"
_KANBAN_PY_DIR = _TEAM_DIR / "pgai_agent_kanban"

_CANCEL_RC = _CM_SCRIPTS_DIR / "cancel-rc.sh"
_OPEN_RC = _CM_SCRIPTS_DIR / "open-rc.sh"

_PROJECT = "probe"
_SKIP_MARKER = "skipping git fetch origin"
_FETCH_MARKER = "Fetching origin..."

_SCRUBBED_NAMES = (
    "GIT_GUARD_REAL_GIT",
    "PGAI_GIT_GUARD_DEPTH",
    "PGAI_GIT_LOCAL_ONLY",
    "PGAI_AGENT_ROLE",
    "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE",
    "PGAI_TASK_ID",
    "PGAI_TASKS_DIR",
    "PGAI_PROJECT_ROOT",
    "PGAI_DEV_TREE_PATH",
    "REPO_ROOT",
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="cm-scripts-skip-fetch")


def _git(real_git: str, repo: pathlib.Path, *args: str) -> None:
    subprocess.run([real_git, "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _build_board(tmp_path: pathlib.Path, real_git: str) -> tuple[pathlib.Path, pathlib.Path]:
    """A throwaway kanban root (push_to_remote=true) plus its dev tree."""
    root = tmp_path / "board"
    proj = root / "projects" / _PROJECT
    for d in (root / "tmp", root / "logs", root / "locks", proj / "release-state",
              proj / "tasks" / "queues", root / "pgai_agent_kanban" / "cm"):
        d.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    repo.mkdir()
    subprocess.run([real_git, "init", "-q", "--initial-branch=main", str(repo)],
                   capture_output=True, text=True, check=True)
    _git(real_git, repo, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init")
    _git(real_git, repo, "tag", "v1.2.3")
    _git(real_git, repo, "remote", "add", "origin", "git@example.invalid:org/repo.git")

    (root / "kanban.cfg").write_text(
        f"[paths]\ntmp_root = {root}\ntmp_subdir = tmp\n\n[chain]\npm_mode = automatic\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\ndescription=probe\nenabled=true\n", encoding="utf-8"
    )
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_PROJECT}\n"
        f"dev_tree_path = {repo}\n"
        "git_repo_url = git@example.invalid:org/repo.git\n"
        "git_remote_name = origin\n"
        "workflow_type = release\n"
        "is_self_build = false\n"
        "branch_prefix =\n"
        "push_to_remote = true\n"
        "\n[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    # The scripts read release-state.md through the install root's own copy
    # of the field reader, exactly as a live install lays it out.
    for py_file in (_KANBAN_PY_DIR / "cm").glob("*.py"):
        (root / "pgai_agent_kanban" / "cm" / py_file.name).write_text(
            py_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return root, repo


def _run_script(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    args: list[str],
    *,
    role: str | None,
) -> tuple[subprocess.CompletedProcess, pathlib.Path, pathlib.Path]:
    """Run *script* against a fresh board with the real shim first on PATH
    (pinned to the network stub behind it), the guard's evidence file under
    tmp_path, and PGAI_AGENT_ROLE set to *role* (or absent when None).

    Returns (completed process, evidence file path, stub network log path).
    """
    stub_dir = tmp_path / "stub-bin"
    net_log = tmp_path / "network-calls.log"
    stub_env = install_git_network_stub(stub_dir, net_log)
    # Build the fixture through the stub: it hands every non-network verb
    # to a real git it resolved by content, so no guard shim on the
    # session's own PATH (an agent wake puts one first) is ever consulted.
    root, _repo = _build_board(tmp_path, str(stub_dir / "git"))
    evidence = tmp_path / "task-logs" / "git-boundary-guard.jsonl"
    evidence.parent.mkdir(parents=True)

    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_NAMES}
    env["PATH"] = f"{_SHIM_DIR}{os.pathsep}{stub_env['PATH']}"
    env["GIT_GUARD_REAL_GIT"] = str(stub_dir / "git")
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["KANBAN_ROOT"] = str(root)
    env["TEAM_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    env["PGAI_PROJECT_NAME"] = _PROJECT
    env["PGAI_TASK_ID"] = "SYNTH-000-fixture"
    env["PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE"] = str(evidence)
    if role is not None:
        env["PGAI_AGENT_ROLE"] = role

    result = subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, env=env, cwd=str(root), timeout=90,
    )
    return result, evidence, net_log


def _records(evidence: pathlib.Path) -> list[dict]:
    if not evidence.exists():
        return []
    return [json.loads(l) for l in evidence.read_text(encoding="utf-8").splitlines() if l.strip()]


def _network_calls(net_log: pathlib.Path) -> str:
    return net_log.read_text(encoding="utf-8") if net_log.exists() else ""


_SCRIPTS = [
    pytest.param(_CANCEL_RC, ["--project", _PROJECT, "--key", "v1.2.4", "--yes"], id="cancel-rc"),
    pytest.param(_OPEN_RC, ["--project", _PROJECT, "v1.3.0"], id="open-rc"),
]


@pytest.mark.parametrize("script, args", _SCRIPTS)
@pytest.mark.parametrize("role", ["coder", "TESTER", "pm"])
def test_non_cm_role_skips_the_fetch_narrates_it_and_records_nothing(
    tmp_path: pathlib.Path, script: pathlib.Path, args: list[str], role: str
) -> None:
    result, evidence, net_log = _run_script(tmp_path, script, args, role=role)
    combined = result.stdout + result.stderr
    assert _records(evidence) == [], (
        f"{script.name} under PGAI_AGENT_ROLE={role} must write no evidence record; got:\n"
        f"{evidence.read_text(encoding='utf-8')}\n--- script output ---\n{combined}"
    )
    skip_lines = [l for l in result.stdout.splitlines() if _SKIP_MARKER in l]
    assert skip_lines, (
        f"{script.name} under PGAI_AGENT_ROLE={role} must print a line narrating the "
        f"skipped fetch (containing {_SKIP_MARKER!r}).\n--- script output ---\n{combined}"
    )
    assert any(role in l for l in skip_lines), (
        f"the narration must name the role that caused the skip ({role}); got:\n{skip_lines}"
    )
    assert _FETCH_MARKER not in result.stdout, (
        f"{script.name} must not announce a fetch it skipped.\n{result.stdout}"
    )
    assert "fetch origin" not in _network_calls(net_log), (
        f"no fetch may reach the binary behind the shim under {role}; the stub logged:\n"
        f"{_network_calls(net_log)}"
    )


@pytest.mark.parametrize("script, args", _SCRIPTS)
def test_cm_role_still_reaches_the_fetch(
    tmp_path: pathlib.Path, script: pathlib.Path, args: list[str]
) -> None:
    result, evidence, net_log = _run_script(tmp_path, script, args, role="cm")
    combined = result.stdout + result.stderr
    assert _FETCH_MARKER in result.stdout, (
        f"{script.name} under PGAI_AGENT_ROLE=cm must announce and attempt the fetch.\n{combined}"
    )
    assert _SKIP_MARKER not in result.stdout, (
        f"{script.name} under cm must never narrate a skipped fetch.\n{combined}"
    )
    assert "fetch origin" in _network_calls(net_log), (
        "the CM fetch must reach the binary behind PATH (the stub); it logged:\n"
        f"{_network_calls(net_log)!r}\n--- script output ---\n{combined}"
    )
    assert _records(evidence) == [], (
        "a CM invocation is passed through by the guard and records nothing; got:\n"
        f"{evidence.read_text(encoding='utf-8') if evidence.exists() else ''}"
    )


@pytest.mark.parametrize("script, args", _SCRIPTS)
def test_no_role_at_all_keeps_the_fetch(
    tmp_path: pathlib.Path, script: pathlib.Path, args: list[str]
) -> None:
    """An operator's own shell carries no PGAI_AGENT_ROLE and is not a
    non-CM agent: the fetch is attempted exactly as before."""
    result, _evidence, net_log = _run_script(tmp_path, script, args, role=None)
    combined = result.stdout + result.stderr
    assert _FETCH_MARKER in result.stdout, (
        f"{script.name} with no PGAI_AGENT_ROLE must still attempt the fetch.\n{combined}"
    )
    assert _SKIP_MARKER not in result.stdout, combined
    assert "fetch origin" in _network_calls(net_log), (
        f"the fetch must reach the binary behind PATH; the stub logged:\n{_network_calls(net_log)!r}"
    )
