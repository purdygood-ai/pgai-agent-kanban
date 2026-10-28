"""
test_release_integrity_stops_inbox.py
=====================================
The five integrity-invariant stops of cm/release.sh stay real stops and
each also leaves one operator inbox entry carrying the stop's error key,
reason and remedy. Driven end to end through the real cm/open-rc.sh and
cm/release.sh against a dev tree on ``ai_main`` and a sandbox kanban root
under pytest's tmp_path, from a working directory outside the repository.

  - Pre-squash hook failure (trigger 4): a kanban-side pre-squash hook exits
    non-zero.
  - Strict pollution guard (trigger 10): CM_SQUASH_STRICT_POLLUTION_GUARD is
    true and the RC carries a repo-root ``artifacts/`` path.
  - Fidelity divergence (trigger 11): main took a non-conflicting commit
    mid-RC, so main and the RC differ after the squash.
  - Pre-tag hook failure: a kanban-side pre-tag hook exits non-zero.
  - Pre-tag VERSION mismatch: a pre-tag hook rewrites VERSION after the
    release stamped it.

The first three write the project HALT file and a HALT Event (cm_halt); the
two pre-tag stops exit 1 with no HALT file, as they always have. Every one
writes exactly one inbox entry, after the HALT. With the inbox write forced
to fail (``operator-inbox.md`` is a directory) each stop keeps its exit
status and HALT body and narrates the failed write.

The kanban root is a sandbox: KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH
name it, PGAI_AGENT_KANBAN_TEMP_DIR is inside it, and PGAI_PROJECT_ROOT and
PGAI_TASKS_DIR are cleared. Its scripts/ and pgai_agent_kanban/ link to this
tree's own, as install.sh lays them out. push_to_remote is false, so no
step talks to a remote.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_OPEN_RC = _SCRIPTS / "cm" / "open-rc.sh"
_RELEASE = _SCRIPTS / "cm" / "release.sh"

_PROJECT = "integrity-lane"
_VERSION = "v0.1.0"
_MAIN = "ai_main"
_RC = f"ai_rc/{_VERSION}"

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "PGAI Test",
    "GIT_AUTHOR_EMAIL": "test@pgai.local",
    "GIT_COMMITTER_NAME": "PGAI Test",
    "GIT_COMMITTER_EMAIL": "test@pgai.local",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

# Names that would point a script at a live install or route the fixture's
# git through the agent guard.
_AMBIENT_NAMES = (
    "PGAI_AGENT_ROLE",
    "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE",
    "PGAI_GIT_LOCAL_ONLY",
    "PGAI_DEV_TREE_PATH",
    "PGAI_PROJECT_ROOT",
    "PGAI_TASKS_DIR",
    "PGAI_TASK_ID",
    "REPO_ROOT",
    "GIT_REMOTE_NAME",
    "CM_SQUASH_STRICT_POLLUTION_GUARD",
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="release-integrity-stops-inbox")


def _git(*args: str, env: dict) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
    ).stdout.strip()


class _Lane:
    """A sandbox kanban root and a dev tree on ai_main with one open RC."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban"
        self.proj = self.root / "projects" / _PROJECT
        self.dev = tmp_path / "dev"
        self.home = tmp_path / "home"
        self.home.mkdir(parents=True)
        self.env = self._env()
        self._build_root()
        self._build_repo()

    def _env(self) -> dict:
        env = {name: os.environ[name] for name in ("PATH", "LANG", "LC_ALL") if name in os.environ}
        scrub_agent_scoped_env(env)
        for name in _AMBIENT_NAMES:
            env.pop(name, None)
        env.update(_GIT_IDENTITY)
        env.update({
            "HOME": str(self.home),
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "TEAM_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "PGAI_PROJECT_NAME": _PROJECT,
            "PYTHONPATH": str(_TEAM_DIR),
        })
        return env

    def _build_root(self) -> None:
        for sub in ("tmp", "logs", "locks"):
            (self.root / sub).mkdir(parents=True)
        (self.root / "kanban.cfg").write_text("[paths]\n\n[chain]\npm_mode = automatic\n", encoding="utf-8")
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8")
        for sub in ("release-state", "tasks/queues", "overwatch", "bugs", "requirements", "hooks"):
            (self.proj / sub).mkdir(parents=True, exist_ok=True)
        (self.proj / "project.cfg").write_text(
            "[project]\n"
            f"project_name = {_PROJECT}\n"
            f"dev_tree_path = {self.dev}\n"
            "workflow_type = release\n"
            "branch_prefix = ai_\n"
            "git_remote_name = origin\n"
            "push_to_remote = false\n"
            "\n[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n",
            encoding="utf-8",
        )
        (self.proj / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
            "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
            encoding="utf-8",
        )
        # The installed layout: <root>/scripts and <root>/pgai_agent_kanban.
        (self.root / "scripts").symlink_to(_SCRIPTS)
        (self.root / "pgai_agent_kanban").symlink_to(_TEAM_DIR / "pgai_agent_kanban")

    def _build_repo(self) -> None:
        _git("init", "-q", "-b", _MAIN, str(self.dev), env=self.env)
        (self.dev / "README.md").write_text("# integrity fixture\n", encoding="utf-8")
        (self.dev / "release-notes").mkdir()
        (self.dev / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")

    def git(self, *args: str) -> str:
        return _git("-C", str(self.dev), *args, env=self.env)

    def run(self, script: pathlib.Path, *args: str, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
        env = dict(self.env)
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", str(script), *args], capture_output=True, text=True, env=env,
            cwd=str(self.root), timeout=240, stdin=subprocess.DEVNULL,
        )

    def open_rc_with_work(self, extra_files: Optional[dict] = None) -> None:
        """Open the RC and land one feature-branch merge on it."""
        opened = self.run(_OPEN_RC, _VERSION)
        assert opened.returncode == 0, _out(opened)
        self.git("checkout", "-q", "-b", "feature/sample", _RC)
        files = {"feature.txt": "released work\n", **(extra_files or {})}
        for rel, text in files.items():
            path = self.dev / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: sample work")
        self.git("checkout", "-q", _RC)
        self.git("merge", "-q", "--no-ff", "-m", "Merge feature/sample", "feature/sample")
        self.git("branch", "-q", "-D", "feature/sample")
        self.git("checkout", "-q", _MAIN)

    def hook(self, phase: str, body: str) -> pathlib.Path:
        path = self.proj / "hooks" / f"cm-release-{phase}.sh"
        path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        path.chmod(0o755)
        return path

    def break_inbox(self) -> None:
        """Force every inbox write to fail: the inbox path is a directory."""
        (self.proj / "operator-inbox.md").mkdir()

    def halt_body(self) -> Optional[str]:
        halt = self.proj / "HALT"
        return halt.read_text(encoding="utf-8") if halt.exists() else None

    def release_state(self) -> str:
        return (self.proj / "release-state.md").read_text(encoding="utf-8")

    def inbox(self) -> str:
        path = self.proj / "operator-inbox.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""


def _out(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _inbox_entries(text: str) -> list:
    return [block for block in re.split(r"^### ", text, flags=re.MULTILINE)[1:] if block.startswith("INBOX-")]


def _unwrapped(text: str) -> str:
    """Inbox text with its two-space continuation lines joined back."""
    return re.sub(r"\n  ", "\n", text)


def _without_timestamp(halt_body: str) -> str:
    """The HALT body minus its first (timestamp) line."""
    return "\n".join(halt_body.splitlines()[1:])


def _contract(output: str, key: str) -> tuple:
    """The (message, remedy) the stop emitted for *key* on stderr."""
    match = re.search(
        rf"^error:   {re.escape(key)}\nmessage: (.*)\nremedy:  (.*)$", output, re.MULTILINE,
    )
    assert match, f"no dual-channel block for {key} in:\n{output}"
    return match.group(1), match.group(2)


# ---------------------------------------------------------------------------
# The five fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Stop:
    name: str
    key: str
    halts: bool
    arrange: Callable[[_Lane], dict]


def _pre_squash_hook_failure(lane: _Lane) -> dict:
    lane.open_rc_with_work()
    lane.hook("pre-squash", "echo 'pre-squash check failed'\nexit 3\n")
    return {}


def _pollution_guard_strict(lane: _Lane) -> dict:
    lane.open_rc_with_work({"artifacts/stray-report.md": "stray\n"})
    return {"CM_SQUASH_STRICT_POLLUTION_GUARD": "true"}


def _fidelity_divergence(lane: _Lane) -> dict:
    lane.open_rc_with_work()
    (lane.dev / "main-only.txt").write_text("landed on main mid-RC\n", encoding="utf-8")
    lane.git("add", "-A")
    lane.git("commit", "-q", "-m", "chore: a commit made on main mid-RC")
    return {}


def _pre_tag_hook_failure(lane: _Lane) -> dict:
    lane.open_rc_with_work()
    lane.hook("pre-tag", "echo 'pre-tag check failed'\nexit 4\n")
    return {}


def _version_mismatch(lane: _Lane) -> dict:
    lane.open_rc_with_work()
    lane.hook("pre-tag", "printf 'v9.9.9\\n' > VERSION\n")
    return {}


_STOPS = (
    _Stop("pre-squash-hook", "cm-release.trigger-4.pre-squash-hook-failed", True, _pre_squash_hook_failure),
    _Stop("pollution-guard", "cm-release.trigger-10.pollution-guard-strict", True, _pollution_guard_strict),
    _Stop("fidelity-gate", "cm-release.trigger-11.fidelity-gate-divergence", True, _fidelity_divergence),
    _Stop("pre-tag-hook", "cm-release.hook-failure.cm-release-pre-tag", False, _pre_tag_hook_failure),
    _Stop("version-gate", "cm-release.pre-tag-version-mismatch", False, _version_mismatch),
)


def _run_stop(tmp_path: pathlib.Path, stop: _Stop, *, inbox_broken: bool) -> tuple:
    lane = _Lane(tmp_path)
    extra_env = stop.arrange(lane)
    if inbox_broken:
        lane.break_inbox()
    main_before = lane.git("rev-parse", _MAIN)
    result = lane.run(_RELEASE, "--project", _PROJECT, extra_env=extra_env)
    return lane, result, main_before


@pytest.mark.parametrize("stop", _STOPS, ids=[s.name for s in _STOPS])
def test_integrity_stop_writes_one_inbox_entry_with_its_key_reason_and_remedy(tmp_path, stop):
    lane, result, main_before = _run_stop(tmp_path, stop, inbox_broken=False)

    assert result.returncode == 1, _out(result)
    message, remedy = _contract(result.stderr, stop.key)
    halt = lane.halt_body()
    if stop.halts:
        # The HALT is the one cm_halt has always written: provenance block
        # and the same triple the stop emitted.
        assert halt is not None, _out(result)
        assert halt.splitlines()[0].startswith("# HALT created by CM at "), halt
        assert f"# halt-reason: {stop.key}\n" in halt, halt
        assert f"# error:   {stop.key}\n# message: {message}\n# remedy:  {remedy}\n" in halt, halt
        assert "## HALT Event" in lane.release_state()
        assert f"error:   {stop.key}" in lane.release_state()
    else:
        # The two pre-tag stops exit before the tag without a HALT file.
        assert halt is None, halt
        assert "## HALT Event" not in lane.release_state()
    assert lane.git("tag", "--list") == "", "an integrity stop cuts no tag"
    assert lane.git("rev-parse", _MAIN) == main_before, "main returns to its pre-release commit"

    entries = _inbox_entries(lane.inbox())
    assert len(entries) == 1, lane.inbox()
    entry = _unwrapped(entries[0])
    assert f"- **Source:** rc:{_VERSION}" in entry, entry
    assert stop.key in entry and message in entry and f"Remedy: {remedy}" in entry, entry
    resume = "scripts/unhalt.sh --project" if stop.halts else "scripts/cm/release.sh --project"
    assert f"{lane.root}/{resume} {_PROJECT}" in entry, entry
    assert re.search(rf"Operator inbox: INBOX-0001 records this stop \({re.escape(stop.key)}\)", result.stderr), \
        _out(result)


@pytest.mark.parametrize("stop", _STOPS, ids=[s.name for s in _STOPS])
def test_integrity_stop_keeps_its_exit_and_halt_when_the_inbox_write_fails(tmp_path, stop):
    lane_ok, result_ok, _ = _run_stop(tmp_path / "ok", stop, inbox_broken=False)
    lane, result, main_before = _run_stop(tmp_path / "broken", stop, inbox_broken=True)

    assert result.returncode == result_ok.returncode == 1, _out(result)
    halt_ok, halt = lane_ok.halt_body(), lane.halt_body()
    assert (halt is None) == (halt_ok is None) == (not stop.halts), _out(result)
    if stop.halts:
        # Same body apart from the timestamp line; the sandbox paths differ
        # only by the ok/broken directory.
        assert _without_timestamp(halt).replace("/broken/", "/ok/") == _without_timestamp(halt_ok), halt
        assert "## HALT Event" in lane.release_state()
    assert lane.git("tag", "--list") == ""
    assert lane.git("rev-parse", _MAIN) == main_before
    assert (lane.proj / "operator-inbox.md").is_dir(), "the failed write left the inbox path as it was"
    assert f"WARNING: the operator inbox entry for this stop ({stop.key}) could not be written" in result.stderr, \
        _out(result)
    assert "/scripts/lib/pp_run_ops.sh pgai_agent_kanban.overwatch.inbox write_entry" in result.stderr, _out(result)
    # The narrated by-hand command runs as printed once the inbox is fixed.
    by_hand = re.search(r"Write it by hand: (.*)$", result.stderr, re.MULTILINE).group(1)
    (lane.proj / "operator-inbox.md").rmdir()
    written = subprocess.run(
        ["bash", "-c", by_hand], capture_output=True, text=True, env=lane.env, cwd=str(lane.root),
        timeout=60, stdin=subprocess.DEVNULL,
    )
    assert written.returncode == 0, f"{written.stdout}\n{written.stderr}"
    entries = _inbox_entries(lane.inbox())
    assert len(entries) == 1 and stop.key in _unwrapped(entries[0]), lane.inbox()
    if stop.halts:
        # The narration sits between the HALT's own error block and its
        # closing footer (the EXIT trap's rollback lines may follow that).
        warning = result.stderr.index("WARNING: the operator inbox")
        assert result.stderr.index(f"error:   {stop.key}") < warning, _out(result)
        assert warning < result.stderr.index("HALT: scripts/cm/release.sh stopped."), _out(result)
