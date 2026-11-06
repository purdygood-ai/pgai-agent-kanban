"""
test_push_sites_project_gate.py
===============================
Behavioural tests: the OVERWATCH push-lag check
(``scripts/lib/overwatch-checks/check-push-lag.sh``) and the CM push watchdog
(``scripts/cm/push-watchdog.sh``) push only for a registered project whose
own project.cfg names its dev tree.

For each site:

  - registered project, project.cfg sets dev_tree_path, no push_to_remote
    key: the watchdog pushes the lagging tag to the stubbed remote (its
    default unchanged); the push-lag check makes no git network call and
    says that tag lag is not measured -- it pushes only on an explicit
    push_to_remote = true, which pushes the lagging tag;
  - push_to_remote = true with a remote whose ls-remote fails: the push-lag
    check writes one remote-unreachable row carrying the remote and git's
    error text, pushes nothing and exits 0 (live and --dry-run alike);
  - the same with push_to_remote = false: no git network call is made (the
    push-lag check says tag lag is not measured, the watchdog writes its
    local-only line);
  - (a) no project.cfg, (b) project not in projects.cfg, (c) project.cfg
    without dev_tree_path while PGAI_DEV_TREE_PATH names a real repo: the
    git network stub records zero calls (no ls-remote, no fetch, no push),
    and exactly one skipped-unresolved-project line names the missing piece.

Every git network verb goes to the sanctioned git network stub
(tests/fixtures/git_network_stub.py); nothing contacts a real remote. Each
subprocess environment is built from an explicit set (no inherited
PGAI_DEV_TREE_PATH, kanban.cfg or live root), and every fixture lives under
pytest's tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.git_network_stub import install_git_network_stub
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_CHECK_PUSH_LAG = "scripts/lib/overwatch-checks/check-push-lag.sh"
_WATCHDOG = "scripts/cm/push-watchdog.sh"
_PROJECT = "gate-project"
_LAGGING_TAG = "v1.0.0"
_SKIP = "skipped-unresolved-project"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="push-sites-project-gate")


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
    """A dev tree on ``main`` with one commit and one local-only tag, whose
    ``origin`` is a throwaway bare repo. Returns ``(dev, origin)``."""
    origin = base / "origin.git"
    dev = base / "dev"
    _git("init", "-q", "--bare", str(origin))
    _git("init", "-q", "-b", "main", str(dev))
    _git("-C", str(dev), "commit", "-q", "--allow-empty", "-m", "initial")
    _git("-C", str(dev), "remote", "add", "origin", str(origin))
    _git("-C", str(dev), "tag", _LAGGING_TAG)
    return dev, origin


class Case:
    """A throwaway kanban root, dev tree, stubbed git and explicit env."""

    def __init__(
        self,
        tmp_path: pathlib.Path,
        *,
        registered: bool = True,
        cfg: str | None = None,
        write_cfg: bool = True,
        succeed_verbs: tuple[str, ...] = (),
        fail_stderr: str = "",
    ) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.project_dir = self.root / "projects" / _PROJECT
        (self.project_dir / "overwatch").mkdir(parents=True)
        (self.project_dir / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.root / "tmp").mkdir()
        registered_name = _PROJECT if registered else "other-project"
        (self.root / "projects.cfg").write_text(f"[project:{registered_name}]\npriority = 1\n", encoding="utf-8")
        self.dev, self.origin = _dev_tree(tmp_path / "repos")
        if write_cfg:
            text = cfg if cfg is not None else f"[project]\ndev_tree_path = {self.dev}\n"
            (self.project_dir / "project.cfg").write_text(text, encoding="utf-8")
        self.stub_log = tmp_path / "git-network-calls.log"
        self.stub_bin = tmp_path / "stub-bin"
        install_git_network_stub(self.stub_bin, self.stub_log, succeed_verbs=succeed_verbs, fail_stderr=fail_stderr)
        (tmp_path / "home").mkdir()

    def env(self, **extra: str) -> dict:
        values = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "OVERWATCH_PROJECT": _PROJECT,
            "HALT_OVERWATCH": "",
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

    @property
    def actions(self) -> str:
        return (self.project_dir / "overwatch" / "actions.log").read_text(encoding="utf-8")

    def origin_tags(self) -> str:
        return _git("-C", str(self.origin), "tag", "--list")

    def run_push_lag(self, *check_args: str, **extra_env: str):
        """Run the check (live mode unless *check_args* says otherwise); the
        fix wrapper simply calls through."""
        script = textwrap.dedent(f"""\
            set -euo pipefail
            overwatch_log_action() {{
                printf '%s\\t%s\\t%s\\n' "$1" "$3" "$5" >> "{self.project_dir}/overwatch/actions.log"
            }}
            overwatch_halt_first_fix() {{ "$@"; }}
            source {_CHECK_PUSH_LAG}
            overwatch_check_push_lag {" ".join(check_args)}
        """)
        return run_bash(self.tmp_path, script, base_env=self.env(**extra_env), timeout=120)

    def run_watchdog(self, **extra_env: str):
        return run_bash(
            self.tmp_path,
            f"bash {_WATCHDOG} --project {_PROJECT}",
            base_env=self.env(**extra_env),
            timeout=120,
        )

    def watchdog_log(self) -> str:
        log = self.project_dir / "logs" / "cm-push-watchdog.log"
        return log.read_text(encoding="utf-8") if log.is_file() else ""


def _skip_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if _SKIP in line]


_NO_DEV_TREE_CFG = f"[project]\nproject_name = {_PROJECT}\n"

#: (case id, Case kwargs, missing-piece token)
_UNRESOLVED = [
    ("no-project-cfg", {"write_cfg": False}, "missing project-cfg"),
    ("not-registered", {"registered": False}, "missing registration"),
    ("no-dev-tree-path", {"cfg": _NO_DEV_TREE_CFG}, "missing dev-tree-path"),
]


# ---------------------------------------------------------------------------
# check-push-lag.sh
# ---------------------------------------------------------------------------


def test_push_lag_pushes_lagging_tag_when_push_to_remote_is_true(tmp_path):
    # A reachable remote: the stub answers ls-remote with an empty listing.
    case = Case(tmp_path, succeed_verbs=("ls-remote",))
    cfg = case.project_dir / "project.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "push_to_remote = true\n", encoding="utf-8")
    case.run_push_lag()

    pushes = case.pushes()
    assert any(
        f"refs/tags/{_LAGGING_TAG}" in line for line in pushes
    ), f"expected a push of {_LAGGING_TAG} to the stubbed remote; stub log:\n" + "\n".join(case.network_calls())
    assert "staged-by-design" not in case.actions


_UNREACHABLE_ERROR = "fatal: unable to access 'https://example.invalid/repo.git/': Could not resolve host"


@pytest.mark.parametrize("check_args", [(), ("--dry-run",)], ids=["live", "dry-run"])
def test_push_lag_unreachable_remote_is_one_row_and_no_push(tmp_path, check_args):
    # ls-remote fails: the remote's tags are unknown, not absent, so the
    # local tag is not counted unpushed and nothing is pushed.
    case = Case(tmp_path, fail_stderr=_UNREACHABLE_ERROR)
    cfg = case.project_dir / "project.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "push_to_remote = true\n", encoding="utf-8")

    result = case.run_push_lag(*check_args)

    assert result.returncode == 0, result.stderr
    assert any("ls-remote" in line.split() for line in case.network_calls()), case.network_calls()
    assert case.pushes() == [], "an unreachable remote must not be pushed to"
    rows = [line for line in case.actions.splitlines() if line]
    assert len(rows) == 1, case.actions
    name, action, detail = rows[0].split("\t")
    assert (name, action) == ("check-push-lag", "remote-unreachable"), rows[0]
    assert "remote origin" in detail and _UNREACHABLE_ERROR in detail, detail
    assert "push-tag-failed" not in case.actions and "unpushed tags=" not in case.actions, case.actions
    assert case.origin_tags() == ""


def test_push_lag_without_push_key_pushes_nothing(tmp_path):
    # The check pushes only on an explicit push_to_remote = true; an absent
    # key is treated like false (the watchdog's default is unchanged below).
    # The dev tree has no remote-tracking ref, so nothing lags locally and
    # no staged-by-design row is written.
    case = Case(tmp_path)

    result = case.run_push_lag()

    assert result.returncode == 0, result.stderr
    assert case.network_calls() == [], "no-push must contact no remote"
    assert case.pushes() == []
    assert "push_to_remote=unset" in result.stderr, result.stderr
    assert "tag lag against origin is not measured" in result.stderr, result.stderr
    assert "unpushed_tags=" not in case.actions, case.actions
    assert case.origin_tags() == ""


def test_push_lag_push_to_remote_false_is_staged_by_design(tmp_path):
    case = Case(tmp_path)
    cfg = case.project_dir / "project.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "push_to_remote = false\n", encoding="utf-8")

    result = case.run_push_lag()

    assert result.returncode == 0, result.stderr
    assert case.network_calls() == [], "no-push must contact no remote"
    assert case.pushes() == []
    assert "push_to_remote=false" in result.stderr, result.stderr
    assert "tag lag against origin is not measured" in result.stderr, result.stderr
    assert "unpushed_tags=" not in case.actions, case.actions
    assert case.origin_tags() == ""


@pytest.mark.parametrize("setting", [None, "false"], ids=["unset", "false"])
def test_push_lag_no_push_main_ahead_is_staged_without_reading_the_remote(tmp_path, setting):
    # Local main one commit ahead of its remote-tracking ref: measured from
    # local refs alone, so the row is written with no git network call and
    # carries no tag count.
    case = Case(tmp_path)
    if setting is not None:
        cfg = case.project_dir / "project.cfg"
        cfg.write_text(cfg.read_text(encoding="utf-8") + f"push_to_remote = {setting}\n", encoding="utf-8")
    _git("-C", str(case.dev), "update-ref", "refs/remotes/origin/main", "HEAD")
    _git("-C", str(case.dev), "commit", "-q", "--allow-empty", "-m", "local-only")

    result = case.run_push_lag()

    assert result.returncode == 0, result.stderr
    assert case.network_calls() == [], "no-push must contact no remote"
    assert "staged-by-design" in case.actions, case.actions
    assert f"push_to_remote={setting or 'unset'}" in case.actions, case.actions
    assert "main ahead=1, tag lag against origin not measured" in case.actions, case.actions
    assert "unpushed_tags=" not in case.actions, case.actions


@pytest.mark.parametrize(("label", "kwargs", "token"), _UNRESOLVED, ids=[c[0] for c in _UNRESOLVED])
def test_push_lag_unresolved_project_contacts_no_remote(tmp_path, label, kwargs, token):
    case = Case(tmp_path, **kwargs)

    result = case.run_push_lag(PGAI_DEV_TREE_PATH=str(case.dev))

    assert result.returncode == 0, result.stderr
    assert case.network_calls() == [], f"{label}: a git network call was made"
    lines = _skip_lines(result.stderr)
    assert len(lines) == 1, f"{label}: expected one {_SKIP} line, got:\n{result.stderr}"
    assert token in lines[0], lines[0]
    assert case.origin_tags() == ""


# ---------------------------------------------------------------------------
# cm/push-watchdog.sh
# ---------------------------------------------------------------------------


def test_watchdog_pushes_for_registered_project_without_push_key(tmp_path):
    case = Case(tmp_path)

    result = case.run_watchdog()

    assert result.returncode == 0, result.stderr
    pushes = case.pushes()
    assert any(
        f"refs/tags/{_LAGGING_TAG}" in line.split() for line in pushes
    ), f"expected the watchdog to push {_LAGGING_TAG} by its ref to the stubbed remote; stub log:\n" + "\n".join(
        case.network_calls()
    )


def test_watchdog_push_to_remote_false_pushes_nothing(tmp_path):
    case = Case(tmp_path)
    cfg = case.project_dir / "project.cfg"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "push_to_remote = false\n", encoding="utf-8")

    result = case.run_watchdog()

    assert result.returncode == 0, result.stderr
    assert case.network_calls() == []
    assert "[push_to_remote=false]" in case.watchdog_log(), case.watchdog_log()


@pytest.mark.parametrize(("label", "kwargs", "token"), _UNRESOLVED, ids=[c[0] for c in _UNRESOLVED])
def test_watchdog_unresolved_project_contacts_no_remote(tmp_path, label, kwargs, token):
    case = Case(tmp_path, **kwargs)

    result = case.run_watchdog(PGAI_DEV_TREE_PATH=str(case.dev))

    assert result.returncode == 0, result.stderr
    assert case.network_calls() == [], f"{label}: a git network call was made"
    lines = _skip_lines(case.watchdog_log())
    assert len(lines) == 1, f"{label}: expected one {_SKIP} line, got:\n{case.watchdog_log()}"
    assert token in lines[0], lines[0]
    assert case.origin_tags() == ""
