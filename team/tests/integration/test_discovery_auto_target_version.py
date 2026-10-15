"""
test_discovery_auto_target_version.py
=====================================
An auto-sentinel ``## Target Version`` (auto, next-patch, empty or absent)
is resolved once, by discovery Step 3, under the repo lock, and written into
the requirements file before pm-agent.sh is launched:

  - on a prefixed lane (branch_prefix = ai_) whose dev tree carries a bare
    v1.0.0 reachable from origin/main and ai_v2.5.0 on ai_main, the file
    reads v2.5.1 at the moment PM is launched and the queued PM ticket's
    ## Release Version is v2.5.1 -- even with PGAI_DEV_TREE_PATH pointing
    at another repository;
  - the next free slot is judged by the lane's prefixed tags: an
    unmerged ai_v2.5.1 takes its slot, a bare v2.5.2 from another lane
    does not;
  - a resolved version above a max_* ceiling is skipped with the ceiling
    rejection log line, the file keeps its sentinel and PM is not launched;
  - pm-agent.sh run by hand refuses a sentinel on a semver project
    dual-channel and queues nothing, and carries an explicit version
    written inline (``## Target Version: v2.5.1``) into the ticket.

Discovery launches ``$TEAM_ROOT/scripts/pm-agent.sh``; the fixture root's
copy is a two-line wrapper that snapshots the requirements file and then
execs this tree's real pm-agent.sh, so the ticket is written by the real
script into the throwaway root.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.lib.md_fields import field_value
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_LIB = _TEAM_DIR / "scripts" / "lib"
_PM_AGENT = _TEAM_DIR / "scripts" / "pm-agent.sh"
_PROJECT = "project_a"
_REQ_NAME = "v2.5.1-auto-feature.md"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env=build_scrubbed_env(home_dir=repo.parent / "githome", extra_env={
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }),
    )
    return result.stdout.strip()


def _commit(repo: pathlib.Path, content: str) -> None:
    (repo / "file").write_text(content, encoding="utf-8")
    _git(repo, "add", "file")
    _git(repo, "commit", "-qm", content)


def _lane_repo(base: pathlib.Path) -> pathlib.Path:
    """A dev tree whose bare v1.0.0 is reachable from origin/main and whose
    prefixed lane released ai_v2.5.0 on ai_main."""
    repo = base / "lane-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "one")
    _git(repo, "tag", "v1.0.0")
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "HEAD"))
    _git(repo, "checkout", "-qb", "ai_main")
    _commit(repo, "two")
    _git(repo, "tag", "ai_v2.5.0")
    return repo


def _decoy_repo(base: pathlib.Path) -> pathlib.Path:
    """Another repository, the kind the global PGAI_DEV_TREE_PATH names."""
    repo = base / "decoy-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "decoy")
    _git(repo, "tag", "v9.0.0")
    return repo


def _project(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _configure(root: pathlib.Path, repo: pathlib.Path, max_major: int = 9) -> None:
    cfg = _project(root) / "project.cfg"
    text = cfg.read_text(encoding="utf-8")
    text = text.replace("dev_tree_path = /dev/null", f"dev_tree_path = {repo}\nbranch_prefix = ai_")
    text = text.replace("max_major = 0", f"max_major = {max_major}")
    cfg.write_text(text, encoding="utf-8")


def _requirements(root: pathlib.Path, target_block: str) -> pathlib.Path:
    req_dir = _project(root) / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    path = req_dir / _REQ_NAME
    path.write_text(
        "# Requirements: auto feature\n\n## Status\nopen\n\n"
        f"{target_block}"
        "## Workflow Type\n\nrelease\n\n## Summary\nAdd the feature.\n",
        encoding="utf-8",
    )
    return path


def _install_pm_wrapper(root: pathlib.Path, snapshot: pathlib.Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    wrapper = scripts / "pm-agent.sh"
    wrapper.write_text(
        f'#!/usr/bin/env bash\ncp "$1" "{snapshot}"\nexec "{_PM_AGENT}" "$@"\n', encoding="utf-8"
    )
    wrapper.chmod(0o755)


def _env(root: pathlib.Path, tmp_path: pathlib.Path, decoy: pathlib.Path) -> dict:
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
            "PGAI_PROJECT_NAME": _PROJECT,
            "PGAI_GIT_LOCAL_ONLY": "1",
            "PGAI_DEV_TREE_PATH": str(decoy),
        },
    )


def _run_discovery(root: pathlib.Path, tmp_path: pathlib.Path, decoy: pathlib.Path):
    script = (
        "set -euo pipefail\n"
        f'source "{_LIB}/ini_parser.sh"\n'
        f'source "{_LIB}/temp.sh"\n'
        f'source "{_LIB}/semver.sh"\n'
        f'source "{_LIB}/project_paths.sh"\n'
        f'source "{_LIB}/discovery.sh"\n'
        f'discovery_run_pipeline "{_PROJECT}"\n'
        'echo "DISCOVERY_STATUS=${DISCOVERY_LAST_STATUS}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=str(root),
        env=_env(root, tmp_path, decoy), timeout=180,
    )


def _pm_tickets(root: pathlib.Path) -> list:
    tasks = _project(root) / "tasks"
    return sorted(d for d in tasks.iterdir() if d.is_dir() and d.name.startswith("PM-"))


def _pm_backlog_entries(root: pathlib.Path) -> list:
    backlog = _project(root) / "tasks" / "queues" / "pm_backlog.md"
    return [l for l in backlog.read_text(encoding="utf-8").splitlines() if l.lstrip().startswith("- [")]


@pytest.fixture
def lane(two_project_root, tmp_path):
    """(root, decoy, snapshot) for a prefixed lane wired to the real pm-agent.sh."""
    repos = tmp_path / "repos"
    repos.mkdir()
    repo = _lane_repo(repos)
    decoy = _decoy_repo(repos)
    _configure(two_project_root, repo)
    snapshot = tmp_path / "launch-snapshot.md"
    _install_pm_wrapper(two_project_root, snapshot)
    return two_project_root, repo, decoy, snapshot


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_block", [
    "## Target Version\n\nauto\n\n",
    "## Target Version\nnext-patch\n\n",
    "## Target Version\n\n",
    "",
], ids=["auto", "next-patch", "empty", "absent"])
def test_auto_version_is_written_before_pm_and_carried_into_the_ticket(lane, tmp_path, target_block):
    root, _repo, decoy, snapshot = lane
    req = _requirements(root, target_block)

    result = _run_discovery(root, tmp_path, decoy)

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=produced_work" in result.stdout, result.stderr
    assert snapshot.is_file(), f"PM was never launched:\n{result.stderr}"
    assert field_value(snapshot.read_text(encoding="utf-8"), "Target Version") == "v2.5.1", (
        "the requirements file must carry the resolved version at the moment PM is launched"
    )
    assert field_value(req.read_text(encoding="utf-8"), "Target Version") == "v2.5.1"
    tickets = _pm_tickets(root)
    assert len(tickets) == 1, tickets
    readme = (tickets[0] / "README.md").read_text(encoding="utf-8")
    assert field_value(readme, "Release Version") == "v2.5.1", readme


def test_next_slot_is_judged_by_the_lanes_prefixed_tags(lane, tmp_path):
    root, repo, decoy, snapshot = lane
    _git(repo, "checkout", "-qb", "side", "main")
    _commit(repo, "side")
    _git(repo, "tag", "ai_v2.5.1")  # this lane's tag, not merged into ai_main: slot taken
    _git(repo, "tag", "v2.5.2")     # another lane's bare tag: not this lane's slot
    _git(repo, "checkout", "-q", "ai_main")
    req = _requirements(root, "## Target Version\n\nauto\n\n")

    result = _run_discovery(root, tmp_path, decoy)

    assert result.returncode == 0, result.stderr
    assert field_value(req.read_text(encoding="utf-8"), "Target Version") == "v2.5.2", result.stderr


def test_auto_version_above_the_ceiling_is_skipped_with_the_rejection_line(lane, tmp_path):
    root, _repo, decoy, snapshot = lane
    cfg = _project(root) / "project.cfg"  # lower the major ceiling below the resolved 2
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_major = 9", "max_major = 1"), encoding="utf-8")
    req = _requirements(root, "## Target Version\n\nauto\n\n")

    result = _run_discovery(root, tmp_path, decoy)

    assert result.returncode == 0, result.stderr
    assert "PM not queued for v2.5.1: major version 2 exceeds max_major=1" in result.stderr, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    assert not snapshot.exists(), "PM must not be launched for a ceiling-blocked version"
    assert field_value(req.read_text(encoding="utf-8"), "Target Version") == "auto"
    assert _pm_tickets(root) == []


# ---------------------------------------------------------------------------
# pm-agent.sh by hand
# ---------------------------------------------------------------------------


def _run_pm_agent(root: pathlib.Path, tmp_path: pathlib.Path, decoy: pathlib.Path, req: pathlib.Path):
    return subprocess.run(
        [str(_PM_AGENT), str(req), "--project", _PROJECT], capture_output=True, text=True,
        cwd=str(root), env=_env(root, tmp_path, decoy), timeout=180,
    )


@pytest.mark.parametrize("target_block", [
    "## Target Version\n\nauto\n\n",
    "",
], ids=["auto", "absent"])
def test_manual_pm_agent_refuses_a_sentinel_and_queues_nothing(lane, tmp_path, target_block):
    root, _repo, decoy, _snapshot = lane
    req = _requirements(root, target_block)

    result = _run_pm_agent(root, tmp_path, decoy, req)

    assert result.returncode != 0, result.stdout + result.stderr
    refusal = parse_error_contract(result.stderr[result.stderr.find("error:"):])
    assert refusal is not None, result.stderr
    assert refusal["error"] == "pm-agent.target-version-not-explicit"
    assert "--auto --project project_a" in refusal["remedy"]
    assert "<" not in refusal["remedy"]
    assert _pm_tickets(root) == []
    assert _pm_backlog_entries(root) == []


def test_manual_pm_agent_carries_an_inline_explicit_version(lane, tmp_path):
    root, _repo, decoy, _snapshot = lane
    req = _requirements(root, "## Target Version: v2.5.1\n\n")

    result = _run_pm_agent(root, tmp_path, decoy, req)

    assert result.returncode == 0, result.stdout + result.stderr
    tickets = _pm_tickets(root)
    assert len(tickets) == 1, tickets
    readme = (tickets[0] / "README.md").read_text(encoding="utf-8")
    assert field_value(readme, "Release Version") == "v2.5.1", readme
