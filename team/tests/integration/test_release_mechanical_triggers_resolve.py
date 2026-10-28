"""
test_release_mechanical_triggers_resolve.py
===========================================
The three mechanical release failures resolve without a project HALT,
driven end to end through the real cm/open-rc.sh, cm/release.sh and
cm/push-watchdog.sh against a dev tree on ``ai_main`` and a kanban root
under pytest's tmp_path.

  - Squash conflict (trigger 5): main took a commit that conflicts with the
    RC on two paths. release.sh backs the squash out, the resolution
    matrix's squash-conflict row matches, and the resolution stage's
    CANCEL-AND-REBUNDLE drill runs: the RC is cancelled (Active RC none, the
    RC branch gone), its open tasks are closed WONT-DO, the bundle is closed
    wont-do and its cargo bug is open again for discovery to rebundle. One
    operator inbox entry names both paths and the RC's tip commit; the
    release exits 5, writes no HALT, and leaves ai_main where it was.
  - The same conflict with the RC's bundle family at the retry ceiling: no
    row matches, nothing is cancelled, and the trigger 5 HALT is written
    with the cancel as its remedy; the inbox entry still names both paths.
  - Push failure (trigger 6): the fixture remote rejects every push (a
    pre-receive hook). The release still completes locally -- the tag is
    cut, release-state.md reads Active RC none and Last Released v0.1.0,
    exit 0, no HALT -- and records a Push Lag Event; check-push-lag finds
    ai_main ahead and the tag unpushed. Once the remote accepts again,
    push-watchdog.sh --project pushes ai_main and the release tag and
    deletes the RC branch the Push Lag Event named, leaving every other
    remote branch -- one a hand-added event item names but that is not the
    project's RC branch for that version included.
  - Tag already on the remote (trigger 7): the tag reaches the remote after
    the release's Step 1 fetch (a kanban-side pre-tag hook publishes it, as
    a concurrent run would; a tag already there at Step 1 is fetched and
    meets the local-tag check, trigger 5b, instead). One inbox entry names
    the tag; the release exits 6 with no HALT, no local tag, ai_main at its
    pre-release commit, the RC branch in place and release-state.md
    byte-for-byte unchanged.

The kanban root is a sandbox: KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH
name it, PGAI_AGENT_KANBAN_TEMP_DIR is inside it, and PGAI_PROJECT_ROOT and
PGAI_TASKS_DIR are cleared. Its scripts/ and pgai_agent_kanban/ link to this
tree's own, as install.sh lays them out, so the drill's
<root>/scripts/cm/cancel-rc.sh is this tree's. The only remote any step
talks to is the bare repository the test created under tmp_path.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
from typing import Optional

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_OPEN_RC = _SCRIPTS / "cm" / "open-rc.sh"
_RELEASE = _SCRIPTS / "cm" / "release.sh"
_PUSH_WATCHDOG = _SCRIPTS / "cm" / "push-watchdog.sh"
_CHECK_PUSH_LAG = _SCRIPTS / "lib" / "overwatch-checks" / "check-push-lag.sh"

_PROJECT = "mechanical-lane"
_VERSION = "v0.1.0"
_MAIN = "ai_main"
_RC = f"ai_rc/{_VERSION}"
_TAG = f"ai_{_VERSION}"
_BUG = "BUG-0001-sample-defect"
# provenance-allowlist: synthetic fixture task IDs (test DATA the drill's
# task scan reads), not references to real tasks.
_CODER_TASK = "CODER-20260101-001-sample-fix"
_WRITER_TASK = "WRITER-20260101-002-sample-notes"
_CM_TASK = "CM-20260101-003-ship-rc"

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
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="release-mechanical-triggers-resolve")


def _git(*args: str, env: Optional[dict] = None) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
    ).stdout.strip()


class _Lane:
    """A sandbox kanban root, a dev tree on ai_main and its bare remote."""

    def __init__(self, tmp_path: pathlib.Path, *, push: bool) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "kanban"
        self.proj = self.root / "projects" / _PROJECT
        self.dev = tmp_path / "dev"
        self.bare = tmp_path / "remote.git"
        self.env = self._env()
        self._build_root(push)
        self._build_repos()

    def _env(self) -> dict:
        env = dict(os.environ)
        for name in _AMBIENT_NAMES:
            env.pop(name, None)
        scrub_agent_scoped_env(env)
        env.update(_GIT_IDENTITY)
        env.update({
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "TEAM_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "PGAI_PROJECT_NAME": _PROJECT,
            "REPO_ROOT": str(self.dev),
            "PYTHONPATH": str(_TEAM_DIR),
        })
        return env

    def _build_root(self, push: bool) -> None:
        for sub in ("tmp", "logs", "locks"):
            (self.root / sub).mkdir(parents=True)
        (self.root / "kanban.cfg").write_text(
            "[paths]\n\n[chain]\npm_mode = automatic\n\n[release]\nmax_rc_retries = 3\n", encoding="utf-8"
        )
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8")
        for sub in ("release-state", "tasks/queues", "overwatch", "bugs", "requirements"):
            (self.proj / sub).mkdir(parents=True, exist_ok=True)
        (self.proj / "project.cfg").write_text(
            "[project]\n"
            f"project_name = {_PROJECT}\n"
            f"dev_tree_path = {self.dev}\n"
            "workflow_type = release\n"
            "branch_prefix = ai_\n"
            "git_remote_name = origin\n"
            f"push_to_remote = {'true' if push else 'false'}\n"
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

    def _build_repos(self) -> None:
        _git("init", "-q", "--bare", str(self.bare), env=self.env)
        _git("init", "-q", "-b", _MAIN, str(self.dev), env=self.env)
        (self.dev / "README.md").write_text("# mechanical fixture\n", encoding="utf-8")
        (self.dev / "shared.txt").write_text("line A\nline B\n", encoding="utf-8")
        (self.dev / "config.txt").write_text("mode = one\n", encoding="utf-8")
        (self.dev / "release-notes").mkdir()
        (self.dev / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")
        self.git("remote", "add", "origin", str(self.bare))
        self.git("push", "-q", "origin", _MAIN)
        self.git("fetch", "-q", "origin")

    # -- helpers ------------------------------------------------------------

    def git(self, *args: str) -> str:
        return _git("-C", str(self.dev), *args, env=self.env)

    def run(self, script: pathlib.Path, *args: str, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
        env = dict(self.env)
        env.update(extra_env or {})
        return subprocess.run(
            ["bash", str(script), *args], capture_output=True, text=True, env=env,
            cwd=str(self.root), timeout=240, stdin=subprocess.DEVNULL,
        )

    def open_rc_with_work(self) -> None:
        """Open the RC and land one feature-branch merge on it."""
        opened = self.run(_OPEN_RC, _VERSION)
        assert opened.returncode == 0, _out(opened)
        self.git("checkout", "-q", "-b", "feature/sample", _RC)
        (self.dev / "shared.txt").write_text("line A\nline B (RC)\n", encoding="utf-8")
        (self.dev / "config.txt").write_text("mode = rc\n", encoding="utf-8")
        (self.dev / "feature.txt").write_text("released work\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "feat: sample work")
        self.git("checkout", "-q", _RC)
        self.git("merge", "-q", "--no-ff", "-m", "Merge feature/sample", "feature/sample")
        self.git("branch", "-q", "-D", "feature/sample")
        self.git("checkout", "-q", _MAIN)

    def conflicting_main_commit(self) -> None:
        (self.dev / "shared.txt").write_text("line A\nline B (main)\n", encoding="utf-8")
        (self.dev / "config.txt").write_text("mode = main\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fix: a commit made on main mid-RC")

    def rc_cargo(self) -> None:
        """A bug bundle with one cargo bug, and the RC's task set."""
        bug = self.proj / "bugs" / f"{_BUG}.md"
        bug.write_text(f"# Bug: {_BUG}\n\n## Status\nrunning\n\n## Symptom\nsample defect\n", encoding="utf-8")
        (self.proj / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {_BUG}\n", encoding="utf-8")
        (self.proj / "requirements" / f"{_VERSION}-bugfix-bundle.md").write_text(
            f"# Requirements: {_VERSION} bug bundle\n\n## Status\nrunning\n\n## Target Version\n{_VERSION}\n\n"
            f"## Bundled Items\n\n- {_BUG}.md (`{bug}`)\n",
            encoding="utf-8",
        )
        for task_id, role, state in (
            (_CODER_TASK, "CODER", "DONE"), (_WRITER_TASK, "WRITER", "WAITING"), (_CM_TASK, "CM", "WORKING"),
        ):
            task_dir = self.proj / "tasks" / task_id
            task_dir.mkdir(parents=True)
            (task_dir / "README.md").write_text(
                f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\n{role}\n\n"
                f"## Source Branch\n{_RC}\n\n## Release Version\n{_VERSION}\n",
                encoding="utf-8",
            )
            (task_dir / "status.md").write_text(
                f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Blockers\nnone\n", encoding="utf-8"
            )

    def state(self, task_id: str) -> str:
        text = (self.proj / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
        return re.search(r"^## State\s*\n(\S+)", text, re.MULTILINE).group(1)

    def section(self, path: pathlib.Path, heading: str) -> str:
        match = re.search(rf"^## {re.escape(heading)}\s*\n(\S+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        return match.group(1) if match else ""

    def release_state(self) -> str:
        return (self.proj / "release-state.md").read_text(encoding="utf-8")

    def inbox(self) -> str:
        path = self.proj / "operator-inbox.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def remote_heads(self) -> dict:
        out = _git("-C", str(self.bare), "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads",
                   env=self.env)
        return dict(line.split() for line in out.splitlines())

    def remote_tags(self) -> list:
        return _git("-C", str(self.bare), "tag", "--list", env=self.env).splitlines()

    def remote_branch_refs(self, branch: str) -> str:
        """``git ls-remote`` on the bare remote for exactly refs/heads/<branch>."""
        return _git("ls-remote", str(self.bare), f"refs/heads/{branch}", env=self.env)

    def reject_pushes(self, reject: bool) -> None:
        hook = self.bare / "hooks" / "pre-receive"
        if reject:
            hook.write_text("#!/bin/sh\necho 'fixture remote: pushes are rejected' >&2\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
        elif hook.exists():
            hook.unlink()


def _out(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _inbox_entries(text: str) -> list:
    return [block for block in re.split(r"^### ", text, flags=re.MULTILINE)[1:] if block.startswith("INBOX-")]


# ---------------------------------------------------------------------------
# Trigger 5: squash conflict
# ---------------------------------------------------------------------------


def test_squash_conflict_cancels_and_rebundles_the_rc_without_a_halt(tmp_path: pathlib.Path) -> None:
    lane = _Lane(tmp_path, push=False)
    lane.open_rc_with_work()
    lane.rc_cargo()
    lane.conflicting_main_commit()
    main_before = lane.git("rev-parse", _MAIN)
    rc_tip = lane.git("rev-parse", _RC)

    result = lane.run(_RELEASE, "--project", _PROJECT)

    assert result.returncode == 5, _out(result)
    assert not (lane.proj / "HALT").exists(), _out(result)
    assert not (lane.root / "HALT").exists()
    # The squash was backed out: main is where it was, with a clean tree.
    assert lane.git("rev-parse", _MAIN) == main_before
    assert lane.git("status", "--porcelain") == ""
    assert lane.git("tag", "--list") == ""
    # The RC is cancelled and its cargo returned for rebundling.
    assert re.search(r"^## Active RC\s*\nnone", lane.release_state(), re.MULTILINE), lane.release_state()
    assert lane.git("branch", "--list", _RC) == ""
    assert lane.state(_CODER_TASK) == "DONE"
    assert lane.state(_WRITER_TASK) == "WONT-DO"
    assert lane.state(_CM_TASK) == "WONT-DO"
    assert lane.section(lane.proj / "requirements" / f"{_VERSION}-bugfix-bundle.md", "Status") == "wont-do"
    assert lane.section(lane.proj / "bugs" / f"{_BUG}.md", "Status") == "open"
    ledger = (lane.proj / "auto-resolutions.md").read_text(encoding="utf-8")
    assert ledger.count("## Entry:") == 1 and "squash-conflict" in ledger and "CANCEL-AND-REBUNDLE" in ledger
    assert "## OVERWATCH Decision" in lane.release_state()
    # One inbox entry names every conflicting path and the RC's tip.
    entries = _inbox_entries(lane.inbox())
    assert len(entries) == 1, lane.inbox()
    assert "config.txt" in entries[0] and "shared.txt" in entries[0], entries[0]
    assert rc_tip in entries[0] and f"rc:{_VERSION}" in entries[0]
    assert "cm-release.squash-conflict.rebundled" in result.stderr, _out(result)


def test_squash_conflict_at_the_retry_ceiling_halts_and_cancels_nothing(tmp_path: pathlib.Path) -> None:
    lane = _Lane(tmp_path, push=False)
    lane.open_rc_with_work()
    lane.rc_cargo()
    lane.conflicting_main_commit()
    record = lane.proj / "release-state" / f"{_VERSION}.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["attempt"] = 3
    record.write_text(json.dumps(data), encoding="utf-8")
    main_before = lane.git("rev-parse", _MAIN)

    result = lane.run(_RELEASE, "--project", _PROJECT)

    assert result.returncode == 1, _out(result)
    halt = (lane.proj / "HALT").read_text(encoding="utf-8")
    assert "cm-release.trigger-5.squash-conflict" in halt
    assert "config.txt" in halt and "shared.txt" in halt
    assert f"cm/cancel-rc.sh --project {_PROJECT} --key {_VERSION} --yes" in halt, halt
    assert "ceiling reached" in halt, halt
    # Nothing was cancelled or reverted.
    assert re.search(rf"^## Active RC\s*\n{re.escape(_VERSION)}", lane.release_state(), re.MULTILINE)
    assert lane.git("branch", "--list", _RC).strip() != ""
    assert lane.git("rev-parse", _MAIN) == main_before
    assert lane.state(_WRITER_TASK) == "WAITING"
    assert lane.section(lane.proj / "bugs" / f"{_BUG}.md", "Status") == "running"
    assert not (lane.proj / "auto-resolutions.md").exists()
    entries = _inbox_entries(lane.inbox())
    assert len(entries) == 1 and "config.txt" in entries[0] and "HALTed on trigger 5" in entries[0], lane.inbox()


# ---------------------------------------------------------------------------
# Trigger 6: push failure, then the push-lag check
# ---------------------------------------------------------------------------


def test_push_failure_ships_locally_and_the_push_lag_check_pushes_later(tmp_path: pathlib.Path) -> None:
    lane = _Lane(tmp_path, push=True)
    lane.open_rc_with_work()
    lane.git("push", "-q", "origin", _RC)
    remote_main_before = lane.remote_heads()[_MAIN]
    lane.reject_pushes(True)

    result = lane.run(_RELEASE, "--project", _PROJECT)

    assert result.returncode == 0, _out(result)
    assert not (lane.proj / "HALT").exists(), _out(result)
    assert lane.git("tag", "--list") == _TAG
    state = lane.release_state()
    assert re.search(r"^## Active RC\s*\nnone", state, re.MULTILINE), state
    assert re.search(rf"^## Last Released\s*\n{re.escape(_VERSION)}", state, re.MULTILINE), state
    assert "## Push Lag Event" in state and f"refs/tags/{_TAG} (push failed)" in state, state
    assert f"{_RC} (still on origin; deletion failed)" in state, state
    assert "cm-release.trigger-6.push-lag" in result.stderr, _out(result)
    # Nothing reached the remote, and the RC branch is still there.
    assert lane.remote_heads()[_MAIN] == remote_main_before
    assert _TAG not in lane.remote_tags()
    assert lane.remote_branch_refs(_RC), "the refused delete leaves the RC branch on the remote"

    # The push lag sits where the push-lag check reads it.
    lag = lane.run(_CHECK_PUSH_LAG, "--dry-run", extra_env={"OVERWATCH_PROJECT": _PROJECT})
    assert lag.returncode == 0, _out(lag)
    assert re.search(rf"local {_MAIN} is [1-9]\d* commit\(s\) ahead of origin/{_MAIN}", lag.stderr), _out(lag)
    assert f"unpushed tag: {_TAG}" in lag.stderr, _out(lag)

    # Once the remote accepts, the existing push-lag path pushes main and the
    # tag and deletes the RC branch the Push Lag Event named. Two other remote
    # branches stay: one no event names, and one a hand-added event item names
    # that is not this project's RC branch for that event's version.
    lane.reject_pushes(False)
    lane.git("push", "-q", "origin", f"{_MAIN}:refs/heads/feature/keep", f"{_MAIN}:refs/heads/ai_rc/v0.0.9")
    with (lane.proj / "release-state.md").open("a", encoding="utf-8") as state_file:
        state_file.write(
            f"\n## Push Lag Event\nVersion: {_VERSION}\nRemote: origin\nNot On Remote:\n"
            "  - feature/keep (still on origin; deletion failed)\n"
        )
    pushed = lane.run(_PUSH_WATCHDOG, "--project", _PROJECT)
    assert pushed.returncode == 0, _out(pushed)
    assert lane.remote_heads()[_MAIN] == lane.git("rev-parse", _MAIN), _out(pushed)
    assert _TAG in lane.remote_tags(), _out(pushed)
    assert lane.remote_branch_refs(_RC) == "", f"the RC branch is gone from the remote\n{_out(pushed)}"
    remaining = _git("ls-remote", "--heads", str(lane.bare), env=lane.env)
    assert not [ref for ref in remaining.split() if ref.endswith(f"rc/{_VERSION}")], remaining
    assert lane.remote_branch_refs("feature/keep"), _out(pushed)
    assert lane.remote_branch_refs("ai_rc/v0.0.9"), _out(pushed)
    assert "Push Lag Event item 'feature/keep (still on origin; deletion failed)' is not deleted" in pushed.stderr, (
        _out(pushed)
    )


# ---------------------------------------------------------------------------
# Trigger 7: the release tag is already on the remote
# ---------------------------------------------------------------------------


def test_tag_already_on_the_remote_writes_an_inbox_entry_and_changes_nothing(tmp_path: pathlib.Path) -> None:
    lane = _Lane(tmp_path, push=True)
    lane.open_rc_with_work()
    lane.git("push", "-q", "origin", _RC)
    hook = lane.proj / "hooks" / "cm-release-pre-tag.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text(
        '#!/bin/sh\n# A concurrent run publishes the release tag after this run fetched.\n'
        f'git push -q origin "$PGAI_RC_BRANCH:refs/tags/{_TAG}"\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    main_before = lane.git("rev-parse", _MAIN)
    rc_before = lane.git("rev-parse", _RC)
    state_before = lane.release_state()

    result = lane.run(_RELEASE, "--project", _PROJECT)

    assert result.returncode == 6, _out(result)
    assert not (lane.proj / "HALT").exists(), _out(result)
    assert "cm-release.trigger-7.tag-exists-on-remote" in result.stderr, _out(result)
    assert lane.git("tag", "--list") == "", "no local tag is cut"
    assert lane.git("rev-parse", _MAIN) == main_before, _out(result)
    assert lane.git("rev-parse", _RC) == rc_before
    assert lane.git("status", "--porcelain") == ""
    assert lane.release_state() == state_before
    assert lane.remote_tags() == [_TAG], "the remote keeps the tag it already had"
    entries = _inbox_entries(lane.inbox())
    assert len(entries) == 1 and _TAG in entries[0] and f"rc:{_VERSION}" in entries[0], lane.inbox()
    assert f"cm/cancel-rc.sh --project {_PROJECT} --key {_VERSION} --yes" in entries[0], entries[0]
