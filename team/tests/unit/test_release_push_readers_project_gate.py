"""
test_release_push_readers_project_gate.py
=========================================
Behavioural tests: the CM release path's push readers decide through the
shared push-decision resolver (``scripts/lib/push_decision.sh``), so they
push only for a registered project whose own project.cfg names its dev tree.

Covered here:

  - ``pp_push_to_remote`` (``scripts/lib/project_paths.sh``): ``true`` for a
    registered project whose project.cfg names dev_tree_path and has no
    push_to_remote key; ``false`` for ``push_to_remote = false``; ``false``
    for (a) no project.cfg, (b) a project not in projects.cfg, and (c) a
    project.cfg without dev_tree_path while PGAI_DEV_TREE_PATH names a real
    repository.
  - ``pp_push_policy``: the same decision for a CM entry point, printing
    exactly one ``skipped-unresolved-project`` line (naming the missing
    piece) for an unresolved project and nothing for push or no-push.
  - ``scripts/cm/finalize-release.sh``: the registered project with no
    push_to_remote key pushes main to the stubbed remote; push_to_remote =
    false contacts no remote; each unresolved case makes zero git network
    calls, asks gh for nothing, and logs one skipped-unresolved-project line.

open-rc.sh and release.sh are driven end-to-end in
tests/integration/test_cm_release_synthetic.py (TestReleasePathPushGate).

Every git network verb goes to the git network stub
(tests/fixtures/git_network_stub.py) and gh to a recording stub; nothing
contacts a real remote. Each subprocess environment is built from an
explicit set (build_scrubbed_env), so no PGAI_DEV_TREE_PATH, effort knob or
live kanban.cfg is inherited. Every fixture lives under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.git_network_stub import install_git_network_stub
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/project_paths.sh"
_FINALIZE = "scripts/cm/finalize-release.sh"
_PROJECT = "release-gate-project"
_VERSION = "v0.1.0"
_SKIP = "skipped-unresolved-project"

# A gh stand-in: record each argv, succeed (so `gh auth status` reads as
# authenticated and a create would go ahead if the script asked for one).
_GH_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${GH_STUB_LOG:?GH_STUB_LOG must be set}"
exit 0
"""


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="release-push-readers-project-gate")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=gate-test", "-c", "user.email=gate-test@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _dev_tree(base: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A dev tree on ``main`` tagged _VERSION (release notes committed), whose
    ``origin`` is a throwaway bare repo never written to. Returns ``(dev, origin)``."""
    origin = base / "origin.git"
    dev = base / "dev"
    _git("init", "-q", "--bare", str(origin))
    _git("init", "-q", "-b", "main", str(dev))
    notes = dev / "release-notes"
    notes.mkdir()
    (notes / f"{_VERSION}.md").write_text(f"# {_VERSION}\n", encoding="utf-8")
    _git("-C", str(dev), "add", "release-notes")
    _git("-C", str(dev), "commit", "-q", "-m", "initial")
    _git("-C", str(dev), "tag", _VERSION)
    _git("-C", str(dev), "remote", "add", "origin", str(origin))
    return dev, origin


class Case:
    """A throwaway kanban root, dev tree, stubbed git and gh, explicit env."""

    def __init__(
        self,
        tmp_path: pathlib.Path,
        *,
        registered: bool = True,
        cfg: "str | None" = None,
        write_cfg: bool = True,
    ) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.project_dir = self.root / "projects" / _PROJECT
        (self.project_dir / "tasks" / "queues").mkdir(parents=True)
        (self.root / "logs").mkdir()
        (self.root / "tmp").mkdir()
        (self.root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
        registered_name = _PROJECT if registered else "other-project"
        (self.root / "projects.cfg").write_text(
            f"[project:{registered_name}]\npriority = 1\n", encoding="utf-8"
        )
        self.dev, self.origin = _dev_tree(tmp_path / "repos")
        if write_cfg:
            text = cfg if cfg is not None else f"[project]\ndev_tree_path = {self.dev}\n"
            (self.project_dir / "project.cfg").write_text(text, encoding="utf-8")
        self.stub_log = tmp_path / "git-network-calls.log"
        self.stub_bin = tmp_path / "stub-bin"
        install_git_network_stub(self.stub_bin, self.stub_log)
        self.gh_log = tmp_path / "gh-calls.log"
        self.gh_log.touch()
        gh = self.stub_bin / "gh"
        gh.write_text(_GH_STUB, encoding="utf-8")
        gh.chmod(0o755)
        (tmp_path / "home").mkdir()
        (tmp_path / "elsewhere").mkdir()

    def append_cfg(self, text: str) -> None:
        cfg = self.project_dir / "project.cfg"
        cfg.write_text(cfg.read_text(encoding="utf-8") + text, encoding="utf-8")

    def env(self, **extra: str) -> dict:
        values = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "GH_STUB_LOG": str(self.gh_log),
        }
        values.update(extra)
        return build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[self.stub_bin, pathlib.Path(sys.executable).parent],
            extra_env=values,
        )

    def network_calls(self) -> list[str]:
        if not self.stub_log.is_file():
            return []
        return [line for line in self.stub_log.read_text(encoding="utf-8").splitlines() if line]

    def pushes(self) -> list[str]:
        return [line for line in self.network_calls() if "push" in line.split()]

    def gh_calls(self) -> list[str]:
        return [line for line in self.gh_log.read_text(encoding="utf-8").splitlines() if line]

    def run_lib(self, call: str, **extra_env: str):
        return run_bash(
            self.tmp_path, f"source {_LIB} && {call}", base_env=self.env(**extra_env), timeout=60
        )

    def run_finalize(self, **extra_env: str):
        result = run_bash(
            self.tmp_path,
            f"bash {_FINALIZE} --project {_PROJECT} --yes {_VERSION} </dev/null",
            base_env=self.env(**extra_env),
            timeout=120,
        )
        return result, result.stdout + result.stderr


def _skip_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if _SKIP in line]


_NO_DEV_TREE_CFG = f"[project]\nproject_name = {_PROJECT}\n"

#: (case id, Case kwargs, missing-piece token)
_UNRESOLVED = [
    ("no-project-cfg", {"write_cfg": False}, "missing project-cfg"),
    ("not-registered", {"registered": False}, "missing registration"),
    ("no-dev-tree-path", {"cfg": _NO_DEV_TREE_CFG}, "missing dev-tree-path"),
]
_IDS = [c[0] for c in _UNRESOLVED]


# ---------------------------------------------------------------------------
# pp_push_to_remote / pp_push_policy
# ---------------------------------------------------------------------------


def test_push_to_remote_true_for_registered_project_without_push_key(tmp_path):
    case = Case(tmp_path)
    result = case.run_lib(f"pp_push_to_remote {_PROJECT}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"


def test_push_to_remote_false_when_configured_false(tmp_path):
    case = Case(tmp_path)
    case.append_cfg("push_to_remote = false\n")
    result = case.run_lib(f"pp_push_to_remote {_PROJECT}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"


@pytest.mark.parametrize(("label", "kwargs", "token"), _UNRESOLVED, ids=_IDS)
def test_push_to_remote_false_for_unresolved_project(tmp_path, label, kwargs, token):
    case = Case(tmp_path, **kwargs)
    result = case.run_lib(f"pp_push_to_remote {_PROJECT}", PGAI_DEV_TREE_PATH=str(case.dev))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false", f"{label}: an unresolved project must not push"
    assert _skip_lines(result.stdout + result.stderr) == [], "pp_push_to_remote stays quiet"


@pytest.mark.parametrize(("label", "kwargs", "token"), _UNRESOLVED, ids=_IDS)
def test_push_policy_logs_one_skip_line_for_unresolved_project(tmp_path, label, kwargs, token):
    case = Case(tmp_path, **kwargs)
    result = case.run_lib(
        f'pp_push_policy {_PROJECT} "[site]"; echo "decision=$PP_PUSH_DECISION push=$PP_PUSH_TO_REMOTE"',
        PGAI_DEV_TREE_PATH=str(case.dev),
    )
    assert result.returncode == 0, result.stderr
    lines = _skip_lines(result.stdout)
    assert len(lines) == 1, f"{label}: expected one {_SKIP} line, got:\n{result.stdout}"
    assert lines[0].startswith(f"[site] {_SKIP}: project '{_PROJECT}': {token}:"), lines[0]
    assert "decision=unresolved push=false" in result.stdout


@pytest.mark.parametrize(
    ("extra_cfg", "expected"),
    [("", "decision=push push=true"), ("push_to_remote = false\n", "decision=no-push push=false")],
    ids=["push", "no-push"],
)
def test_push_policy_is_silent_for_resolved_project(tmp_path, extra_cfg, expected):
    case = Case(tmp_path)
    case.append_cfg(extra_cfg)
    result = case.run_lib(
        f'pp_push_policy {_PROJECT} "[site]"; echo "decision=$PP_PUSH_DECISION push=$PP_PUSH_TO_REMOTE"'
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# cm/finalize-release.sh
# ---------------------------------------------------------------------------


def test_finalize_release_pushes_for_registered_project_without_push_key(tmp_path):
    case = Case(tmp_path)

    _result, output = case.run_finalize()

    # The stub refuses the push (exit 1), so the script stops there; what
    # matters is that the gate let the push through to the remote.
    assert any("main" in line.split() for line in case.pushes()), (
        "expected finalize-release to push main to the stubbed remote; stub log:\n"
        + "\n".join(case.network_calls()) + f"\n--- output ---\n{output}"
    )
    assert _skip_lines(output) == []


def test_finalize_release_push_to_remote_false_contacts_no_remote(tmp_path):
    case = Case(tmp_path)
    case.append_cfg("push_to_remote = false\n")

    result, output = case.run_finalize()

    assert result.returncode == 0, output
    assert case.network_calls() == [], output
    assert [c for c in case.gh_calls() if c.startswith("release create")] == []
    assert _skip_lines(output) == []
    assert "Push policy: push_to_remote=false" in output


@pytest.mark.parametrize(("label", "kwargs", "token"), _UNRESOLVED, ids=_IDS)
def test_finalize_release_unresolved_project_contacts_no_remote(tmp_path, label, kwargs, token):
    case = Case(tmp_path, **kwargs)

    # The environment points at a real checkout both ways a script could
    # inherit one; neither may make an unresolved project pushable.
    result, output = case.run_finalize(PGAI_DEV_TREE_PATH=str(case.dev), REPO_ROOT=str(case.dev))

    assert result.returncode == 0, f"{label}: finalize should complete locally\n{output}"
    assert case.network_calls() == [], f"{label}: a git network call was made\n{output}"
    assert case.gh_calls() == [], f"{label}: gh was invoked\n{output}"
    lines = _skip_lines(output)
    assert len(lines) == 1, f"{label}: expected one {_SKIP} line, got:\n{output}"
    assert token in lines[0], lines[0]
    assert "Push policy: push_to_remote=true" not in output
    assert _git("-C", str(case.origin), "for-each-ref") == ""


def test_finalize_release_logs_skip_line_without_repo_root(tmp_path):
    """Only PGAI_DEV_TREE_PATH set, project.cfg naming no dev_tree_path: the
    skip line is logged before the dev tree is resolved, and no remote is
    contacted even though the run then stops for want of a dev tree."""
    case = Case(tmp_path, cfg=_NO_DEV_TREE_CFG)

    _result, output = case.run_finalize(PGAI_DEV_TREE_PATH=str(case.dev))

    assert case.network_calls() == [], output
    lines = _skip_lines(output)
    assert len(lines) == 1, output
    assert "missing dev-tree-path" in lines[0], lines[0]
