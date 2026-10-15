"""
test_overwatch_sweep_stays_off_remotes.py
=========================================
A full OVERWATCH sweep run from the test suite never reaches a dev tree it
was not given by its own sandbox, and never contacts a remote.

The hazard: ``overwatch-sweep.sh`` with no ``--only`` runs every check
module, ``check-push-lag.sh`` among them. That check pushes every local tag
origin lacks. An inherited ``PGAI_DEV_TREE_PATH`` names the operator's real
checkout (the gated runners export it), so a check that falls back to it
when the sandbox project's own ``project.cfg`` names no ``dev_tree_path``
runs ``ls-remote`` and one ``push`` per local tag against the real origin.

Covered:
  1. The full sweep, with the sanctioned network stub ahead of ``git`` on
     PATH and ``PGAI_DEV_TREE_PATH`` pointing at a throwaway repo that holds
     a local-only tag, makes no ``push`` and no ``ls-remote`` call, and the
     throwaway origin still lacks the tag.
  2. ``check-push-lag.sh`` run directly in the same setup declines with a
     line naming the project config, makes no network call, and exits 0.
  3. The suite-wide isolation fixture leaves no ``PGAI_DEV_TREE_PATH`` in a
     test's environment, so no subprocess a test starts inherits it.

Every fixture lives under pytest's tmp_path: a sandbox kanban root, a bare
throwaway origin and a throwaway dev tree. Nothing touches the live install,
the operator's dev tree, or a real remote.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

from tests.fixtures.git_network_stub import install_git_network_stub
from tests.unit.shell_harness import run_bash
from tests.unit.test_overwatch_block_triage import Sandbox, _record, _status

_SWEEP = "scripts/overwatch-sweep.sh"
_PUSH_LAG = "scripts/lib/overwatch-checks/check-push-lag.sh"

#: A release-shaped tag that exists in the throwaway dev tree and not on its origin.
_LOCAL_ONLY_TAG = "ai_v9.0.0"

#: Git subcommands that would reach a remote in this scenario.
_REMOTE_VERBS = ("push", "ls-remote")


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=sweep-test", "-c", "user.email=sweep-test@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _throwaway_dev_tree(base: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A dev tree with one commit, an ``origin`` remote that is a local bare
    repo, and one tag that exists only locally. Returns ``(dev, origin)``."""
    origin = base / "origin.git"
    dev = base / "dev"
    _git("init", "-q", "--bare", str(origin))
    _git("init", "-q", "-b", "main", str(dev))
    _git("-C", str(dev), "commit", "-q", "--allow-empty", "-m", "initial")
    _git("-C", str(dev), "remote", "add", "origin", str(origin))
    _git("-C", str(dev), "tag", _LOCAL_ONLY_TAG)
    return dev, origin


def _remote_calls(log_file: pathlib.Path) -> list[str]:
    """Lines of the network stub's log that name a push or ls-remote."""
    if not log_file.is_file():
        return []
    return [
        line
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if any(verb in line.split() for verb in _REMOTE_VERBS)
    ]


def _origin_tags(origin: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-C", str(origin), "tag", "--list"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _stubbed_env(sb: Sandbox, tmp_path: pathlib.Path, dev: pathlib.Path) -> tuple[dict, pathlib.Path]:
    """The sandbox env plus the network stub on PATH and an inherited-looking
    PGAI_DEV_TREE_PATH that names the throwaway dev tree."""
    log_file = tmp_path / "git-network-calls.log"
    stub = install_git_network_stub(tmp_path / "stub-bin", log_file)
    return sb.env(PGAI_DEV_TREE_PATH=str(dev), **stub), log_file


def test_full_sweep_makes_no_push_or_ls_remote_call(tmp_path):
    sb = Sandbox(tmp_path / "kanban")
    (sb.root / "tmp").mkdir()
    sb.seed(_status(_record("plugin_load_failed")))
    dev, origin = _throwaway_dev_tree(tmp_path / "repos")
    env, log_file = _stubbed_env(sb, tmp_path, dev)

    result = run_bash(tmp_path, f"bash {_SWEEP}", extra_env=env, timeout=300)

    assert result.returncode == 0, result.stderr
    assert "running push-lag" in result.stderr, "the full sweep did not reach the push-lag check:\n" + result.stderr
    assert "running block-triage" in result.stderr, result.stderr
    calls = _remote_calls(log_file)
    assert calls == [], (
        "the full sweep reached for a remote from a test sandbox whose project.cfg names no dev tree:\n"
        + "\n".join(calls)
    )
    assert _origin_tags(origin) == "", "a local-only tag reached the throwaway origin"


def test_push_lag_check_declines_when_the_project_names_no_dev_tree(tmp_path):
    sb = Sandbox(tmp_path / "kanban")
    (sb.root / "tmp").mkdir()
    dev, origin = _throwaway_dev_tree(tmp_path / "repos")
    env, log_file = _stubbed_env(sb, tmp_path, dev)

    result = run_bash(tmp_path, f"bash {_PUSH_LAG}", extra_env=env, timeout=120)

    assert result.returncode == 0, result.stderr
    assert "names no dev_tree_path" in result.stderr, result.stderr
    assert str(sb.project_root / "project.cfg") in result.stderr, result.stderr
    assert _remote_calls(log_file) == []
    assert _origin_tags(origin) == ""


def test_suite_isolation_clears_the_inherited_dev_tree():
    assert "PGAI_DEV_TREE_PATH" not in os.environ, (
        "the suite-wide isolation fixture in tests/conftest.py must remove PGAI_DEV_TREE_PATH, "
        "or every subprocess a test starts inherits the operator's real dev tree"
    )
