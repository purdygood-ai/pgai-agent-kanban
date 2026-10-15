"""
test_git_remote_name_release_flow.py
====================================
project.cfg ``[project] git_remote_name`` is the remote every CM push, fetch,
``ls-remote`` and remote-tracking ref uses, end to end.

The fixture is a dev tree on ``ai_main`` (``branch_prefix = ai_``) whose only
remote is a local bare repository, with ``push_to_remote = true``. It runs in
two shapes:

  - ``upstream``: the remote is added as ``upstream``, there is **no**
    ``origin``, and project.cfg says ``git_remote_name = upstream``;
  - ``origin-default``: the remote is added as ``origin`` and project.cfg has
    no ``git_remote_name`` line at all -- the behaviour every project had
    before the key was read.

Against each shape:

  1. ``cm/open-rc.sh v0.1.0`` puts ``ai_rc/v0.1.0`` on the remote;
  2. ``ship-rc.sh`` ships it: the remote ends up holding ``ai_main`` with the
     release commit and the ``ai_v0.1.0`` tag, and no longer holds the RC
     branch;
  3. ``cm/open-rc.sh v0.2.0`` then ``cm/cancel-rc.sh --key v0.2.0 --yes``
     deletes ``ai_rc/v0.2.0`` from the remote again;
  4. ``pgai_agent_kanban.lib.released_version.last_released_version`` and
     ``pp_last_released_version`` read the release from
     ``<remote>/ai_main``: a local-only tag on the local ``ai_main``
     (``ai_v0.5.0``) is what the local tier would answer, so ``v0.1.0``
     proves the remote tier read the configured remote;
  5. ``check-push-lag.sh --dry-run`` measures lag against ``<remote>/ai_main``
     and finds the unpushed ``ai_v0.5.0`` tag through ``ls-remote <remote>``.

A third case runs the ``upstream`` shape with ``push_to_remote = false``:
open-rc and cancel-rc narrate the skipped steps naming ``upstream`` and the
remote is never written to.

The autonomous release path, ``cm/release.sh``, is driven end to end against
both shapes as well: its preflight fetches from and validates the RC branch on
the configured remote, and its finalizer lands the ``ai_v0.1.0`` tag and
``ai_main`` there and deletes the RC branch from it, with every remote-naming
line of its narration naming that remote. Three more ``upstream`` cases pin
preflight's Step 3 re-push of an RC branch missing from the remote, its
refusal of an invalid ``git_remote_name`` before any git step, and a
``--decide-only`` run, which contacts no remote, narrating a placeholder for
that invalid name instead of refusing it.

Every repository and kanban root lives under pytest's tmp_path, which the
runners place under ``PGAI_AGENT_KANBAN_TEMP_DIR``; the only remote any step
talks to is the bare repository the test created there, which the git
boundary guard exempts as fixture-local.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
from typing import Optional

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import released_version  # noqa: E402
from tests.fixtures.binary_guard import require_binary  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env  # noqa: E402

_SCRIPTS = _TEAM_DIR / "scripts"
_OPEN_RC = _SCRIPTS / "cm" / "open-rc.sh"
_CANCEL_RC = _SCRIPTS / "cm" / "cancel-rc.sh"
_RELEASE = _SCRIPTS / "cm" / "release.sh"
_SHIP_RC = _SCRIPTS / "ship-rc.sh"
_CHECK_PUSH_LAG = _SCRIPTS / "lib" / "overwatch-checks" / "check-push-lag.sh"
_PROJECT_PATHS = _SCRIPTS / "lib" / "project_paths.sh"
_INI_PARSER = _SCRIPTS / "lib" / "ini_parser.sh"
_KANBAN_PY = _TEAM_DIR / "pgai_agent_kanban"

_PROJECT = "remote-name-flow"
_PREFIX = "ai_"
_MAIN = f"{_PREFIX}main"

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "PGAI Test",
    "GIT_AUTHOR_EMAIL": "test@pgai.local",
    "GIT_COMMITTER_NAME": "PGAI Test",
    "GIT_COMMITTER_EMAIL": "test@pgai.local",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

# Names that would point a script at a live install, make a non-CM role skip
# the fetch under test, or route the fixture's git through the agent guard.
_AMBIENT_NAMES = (
    "PGAI_AGENT_ROLE",
    "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE",
    "PGAI_GIT_LOCAL_ONLY",
    "PGAI_DEV_TREE_PATH",
    "PGAI_PROJECT_ROOT",
    "PGAI_TASKS_DIR",
    "REPO_ROOT",
    "GIT_REMOTE_NAME",
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="git-remote-name-release-flow")


def _git(*args: str, env: Optional[dict] = None) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=env,
    ).stdout.strip()


class _Fixture:
    """A throwaway kanban root and dev tree whose only remote is *remote_name*."""

    def __init__(self, tmp_path: pathlib.Path, *, remote_name: str, configure_key: bool, push: bool) -> None:
        self.tmp = tmp_path
        self.remote = remote_name
        self.root = tmp_path / "kanban"
        self.project_dir = self.root / "projects" / _PROJECT
        self.dev = tmp_path / "dev"
        self.bare = tmp_path / "remote.git"
        self.env = self._env()
        self._build_root(configure_key=configure_key, push=push)
        self._build_repos()

    # -- construction -------------------------------------------------------

    def _env(self) -> dict:
        env = dict(os.environ)
        for name in _AMBIENT_NAMES:
            env.pop(name, None)
        scrub_agent_scoped_env(env)
        env.update(_GIT_IDENTITY)
        env.update(
            {
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "TEAM_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.tmp / "tmp"),
                "PGAI_PROJECT_NAME": _PROJECT,
            }
        )
        return env

    def _build_root(self, *, configure_key: bool, push: bool) -> None:
        (self.tmp / "tmp").mkdir()
        self.project_dir.mkdir(parents=True)
        (self.root / "kanban.cfg").write_text(
            "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\n", encoding="utf-8"
        )
        (self.root / "projects.cfg").write_text(
            f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
        )
        cfg = (
            "[project]\n"
            f"project_name = {_PROJECT}\n"
            f"dev_tree_path = {self.dev}\n"
            "workflow_type = release\n"
            f"branch_prefix = {_PREFIX}\n"
            f"push_to_remote = {'true' if push else 'false'}\n"
        )
        if configure_key:
            cfg += f"git_remote_name = {self.remote}\n"
        cfg += "\n[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n"
        (self.project_dir / "project.cfg").write_text(cfg, encoding="utf-8")
        (self.project_dir / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
            "## RC Opened By Task\nnone\n\n## Last Released\nnone\n",
            encoding="utf-8",
        )
        for sub in ("release-state", "tasks/queues", "overwatch"):
            (self.project_dir / sub).mkdir(parents=True, exist_ok=True)
        for sub in ("logs", "locks"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        # open-rc.sh runs the per-RC state writer from the kanban root's own
        # package directory, as in the live install's flattened layout.
        cm_dir = self.root / "pgai_agent_kanban" / "cm"
        cm_dir.mkdir(parents=True)
        for source in (_KANBAN_PY / "cm").glob("*.py"):
            (cm_dir / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _build_repos(self) -> None:
        _git("init", "-q", "--bare", str(self.bare), env=self.env)
        _git("init", "-q", "-b", _MAIN, str(self.dev), env=self.env)
        (self.dev / "README.md").write_text("# remote-name fixture\n", encoding="utf-8")
        (self.dev / "release-notes").mkdir()
        (self.dev / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
        self.git("add", "README.md", "release-notes/PUBLISHED")
        self.git("commit", "-q", "-m", "initial")
        self.git("remote", "add", self.remote, str(self.bare))
        self.git("push", "-q", self.remote, _MAIN)
        self.git("fetch", "-q", self.remote)

    # -- helpers ------------------------------------------------------------

    def git(self, *args: str) -> str:
        return _git("-C", str(self.dev), *args, env=self.env)

    def run(self, script: pathlib.Path, *args: str, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(script), *args], capture_output=True, text=True, env=env,
            cwd=str(self.root), timeout=180, stdin=subprocess.DEVNULL,
        )

    def remote_heads(self) -> list[str]:
        return _git("-C", str(self.bare), "for-each-ref", "--format=%(refname:short)", "refs/heads", env=self.env).splitlines()

    def remote_tags(self) -> list[str]:
        return _git("-C", str(self.bare), "tag", "--list", env=self.env).splitlines()

    def remotes(self) -> list[str]:
        return self.git("remote").splitlines()

    def commit_on(self, branch: str, name: str, text: str) -> None:
        self.git("checkout", "-q", branch)
        (self.dev / name).write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", f"work: {name}")
        self.git("checkout", "-q", _MAIN)


def _out(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _ok(result: subprocess.CompletedProcess, what: str) -> str:
    assert result.returncode == 0, f"{what} failed\n{_out(result)}"
    return result.stdout + result.stderr


@pytest.mark.parametrize(
    ("remote_name", "configure_key"),
    [("upstream", True), ("origin", False)],
    ids=["upstream", "origin-default"],
)
def test_open_ship_cancel_and_lag_all_use_the_configured_remote(
    tmp_path: pathlib.Path, remote_name: str, configure_key: bool
) -> None:
    box = _Fixture(tmp_path, remote_name=remote_name, configure_key=configure_key, push=True)
    assert box.remotes() == [remote_name], "the fixture's dev tree has exactly one remote"

    # 1. open-rc pushes the RC branch to the configured remote.
    opened = _ok(box.run(_OPEN_RC, "v0.1.0"), "open-rc v0.1.0")
    assert "ai_rc/v0.1.0" in box.remote_heads(), opened
    assert f"Pushing ai_rc/v0.1.0 to {remote_name}..." in opened
    assert f"Fetching {remote_name}..." in opened

    # 2. ship-rc lands main and the tag on the remote and deletes the RC branch there.
    box.commit_on("ai_rc/v0.1.0", "feature.txt", "released work\n")
    box.git("push", "-q", remote_name, "ai_rc/v0.1.0")
    shipped = _ok(box.run(_SHIP_RC, "--project", _PROJECT, "--key", "v0.1.0"), "ship-rc v0.1.0")
    assert box.remote_tags() == ["ai_v0.1.0"], shipped
    assert box.remote_heads() == [_MAIN], shipped
    assert _git("-C", str(box.bare), "rev-parse", _MAIN, env=box.env) == box.git("rev-parse", _MAIN)
    assert f"[Step 7] Pushing ai_main and refs/tags/ai_v0.1.0 to {remote_name}..." in shipped
    assert f"[Step 8] Deleting ai_rc/v0.1.0 on {remote_name}..." in shipped

    # 3. a second RC opened and cancelled: the cancel deletes it from the remote.
    _ok(box.run(_OPEN_RC, "v0.2.0"), "open-rc v0.2.0")
    assert "ai_rc/v0.2.0" in box.remote_heads()
    cancelled = _ok(box.run(_CANCEL_RC, "--key", "v0.2.0", "--yes"), "cancel-rc v0.2.0")
    assert "ai_rc/v0.2.0" not in box.remote_heads(), cancelled
    assert f"Deleted ai_rc/v0.2.0 from {remote_name}." in cancelled

    # 4. released-version reads <remote>/ai_main: the local ai_main carries a
    #    later, unpushed release tag that only the local tier would answer.
    box.commit_on(_MAIN, "local-only.txt", "not on the remote\n")
    box.git("tag", "ai_v0.5.0", _MAIN)
    assert released_version.last_released_version(box.root, _PROJECT) == "v0.1.0"
    bash = subprocess.run(
        ["bash", "-c", f'source "{_INI_PARSER}"; source "{_PROJECT_PATHS}"; pp_last_released_version "{_PROJECT}"'],
        capture_output=True, text=True, env={**box.env, "PGAI_GIT_LOCAL_ONLY": "1"}, timeout=60,
    )
    assert bash.stdout.strip() == "v0.1.0", _out(bash)

    # 5. the push-lag check measures against <remote>/ai_main and asks the remote for tags.
    lag = box.run(_CHECK_PUSH_LAG, "--dry-run", extra_env={"OVERWATCH_PROJECT": _PROJECT})
    lag_text = _ok(lag, "check-push-lag --dry-run")
    assert f"local ai_main is 1 commit(s) ahead of {remote_name}/ai_main" in lag_text
    assert "unpushed tag: ai_v0.5.0" in lag_text
    assert "ai_v0.1.0" not in [line.rsplit(" ", 1)[-1] for line in lag_text.splitlines() if "unpushed tag:" in line]
    assert f"to {remote_name}" in lag_text
    assert box.remote_tags() == ["ai_v0.1.0"], "a dry run pushes nothing"


def test_local_only_project_names_the_configured_remote_and_never_writes_it(tmp_path: pathlib.Path) -> None:
    box = _Fixture(tmp_path, remote_name="upstream", configure_key=True, push=False)
    before = (box.remote_heads(), box.remote_tags())

    opened = _ok(box.run(_OPEN_RC, "v0.1.0"), "open-rc v0.1.0")
    assert "push_to_remote=false — skipping git fetch upstream (local-only install)." in opened
    assert "[push_to_remote=false] skipping upstream push for remote-name-flow: git push -u upstream ai_rc/v0.1.0" in opened

    cancelled = _ok(box.run(_CANCEL_RC, "--key", "v0.1.0", "--yes"), "cancel-rc v0.1.0")
    assert "Push policy: push_to_remote=false — skipping upstream operations." in cancelled
    assert "[push_to_remote=false] skipping upstream push for remote-name-flow: git push upstream --delete ai_rc/v0.1.0 (Step 8)" in cancelled

    assert "origin" not in opened + cancelled, "a project on another remote is never narrated as origin"
    assert (box.remote_heads(), box.remote_tags()) == before, "a local-only project never writes its remote"


def test_open_rc_refuses_an_invalid_remote_name_before_any_git_step(tmp_path: pathlib.Path) -> None:
    box = _Fixture(tmp_path, remote_name="upstream", configure_key=False, push=True)
    cfg = box.project_dir / "project.cfg"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            "push_to_remote = true\n", "push_to_remote = true\ngit_remote_name = --upload-pack=x\n"
        ),
        encoding="utf-8",
    )
    heads_before = box.git("for-each-ref", "--format=%(refname)", "refs/heads")

    refused = box.run(_OPEN_RC, "v0.1.0")

    assert refused.returncode == 1, _out(refused)
    assert "error:   git_remote_name.invalid" in refused.stderr, _out(refused)
    assert "[project] git_remote_name is '--upload-pack=x'" in refused.stderr
    assert f"remedy:  Set git_remote_name in the [project] section of {cfg}" in refused.stderr
    assert box.git("for-each-ref", "--format=%(refname)", "refs/heads") == heads_before, "no branch was created"
    assert box.remote_heads() == [_MAIN], "nothing reached the remote"


def _open_rc_with_work(box: _Fixture, version: str) -> str:
    """Open *version*'s RC on the configured remote and commit work on it locally."""
    opened = _ok(box.run(_OPEN_RC, version), f"open-rc {version}")
    rc_branch = f"ai_rc/{version}"
    assert rc_branch in box.remote_heads(), opened
    box.commit_on(rc_branch, "feature.txt", "released work\n")
    return rc_branch


def _lines_naming_origin(text: str) -> list[str]:
    """Every output line that names a remote called origin (``origin`` or ``origin/...``)."""
    return [line for line in text.splitlines() if re.search(r"\borigin\b", line)]


@pytest.mark.parametrize(
    ("remote_name", "configure_key"),
    [("upstream", True), ("origin", False)],
    ids=["upstream", "origin-default"],
)
def test_release_sh_ships_to_the_configured_remote(
    tmp_path: pathlib.Path, remote_name: str, configure_key: bool
) -> None:
    box = _Fixture(tmp_path, remote_name=remote_name, configure_key=configure_key, push=True)
    assert box.remotes() == [remote_name], "the fixture's dev tree has exactly one remote"
    rc_branch = _open_rc_with_work(box, "v0.1.0")
    box.git("push", "-q", remote_name, rc_branch)

    released = _ok(box.run(_RELEASE, "--project", _PROJECT), "release.sh")

    # The release landed on the configured remote: tag, main, RC branch gone.
    assert box.remote_tags() == ["ai_v0.1.0"], released
    assert box.remote_heads() == [_MAIN], released
    assert _git("-C", str(box.bare), "rev-parse", _MAIN, env=box.env) == box.git("rev-parse", _MAIN), released

    # Preflight and the closing summary name that remote.
    assert f"Push policy: push_to_remote=true — branches and tag will be pushed to {remote_name}." in released
    assert f"[Step 1] Fetching {remote_name} and tags..." in released
    assert f"[Step 3] Validating {rc_branch} exists locally and on {remote_name}..." in released
    assert f"Confirmed: {rc_branch} exists locally and on {remote_name}." in released
    assert f"Tag ai_v0.1.0 created locally and pushed to {remote_name}" in released
    assert f"{rc_branch} deleted from {remote_name}; deleted locally" in released
    if remote_name != "origin":
        assert _lines_naming_origin(released) == [], "a project on another remote is never narrated as origin"


def test_release_sh_re_pushes_an_rc_branch_missing_from_the_configured_remote(tmp_path: pathlib.Path) -> None:
    box = _Fixture(tmp_path, remote_name="upstream", configure_key=True, push=True)
    rc_branch = _open_rc_with_work(box, "v0.1.0")
    box.git("push", "-q", "upstream", "--delete", rc_branch)
    assert box.remote_heads() == [_MAIN]

    released = _ok(box.run(_RELEASE, "--project", _PROJECT), "release.sh")

    assert (
        f"[Step 3] upstream RC absent and tag absent — local RC branch present; "
        f"re-establishing {rc_branch} on upstream..."
    ) in released
    assert f"[Step 3] {rc_branch} re-pushed to upstream successfully. Resuming release." in released
    assert box.remote_tags() == ["ai_v0.1.0"], released
    assert box.remote_heads() == [_MAIN], released


def test_release_sh_refuses_an_invalid_remote_name_before_any_git_step(tmp_path: pathlib.Path) -> None:
    box = _Fixture(tmp_path, remote_name="upstream", configure_key=True, push=True)
    _open_rc_with_work(box, "v0.1.0")
    cfg = box.project_dir / "project.cfg"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace("git_remote_name = upstream\n", "git_remote_name = --upload-pack=x\n"),
        encoding="utf-8",
    )
    head_before = box.git("rev-parse", "--abbrev-ref", "HEAD")
    remote_before = (box.remote_heads(), box.remote_tags())

    refused = box.run(_RELEASE, "--project", _PROJECT)

    assert refused.returncode == 1, _out(refused)
    assert "error:   git_remote_name.invalid" in refused.stderr, _out(refused)
    assert "[project] git_remote_name is '--upload-pack=x'" in refused.stderr
    assert "[Step 1]" not in refused.stdout, _out(refused)
    assert box.git("rev-parse", "--abbrev-ref", "HEAD") == head_before, "the RC branch was not checked out"
    assert box.git("tag", "--list") == "", "no tag was created"
    assert (box.remote_heads(), box.remote_tags()) == remote_before, "nothing reached the remote"


def test_release_sh_decide_only_contacts_no_remote_so_never_refuses_the_name(tmp_path: pathlib.Path) -> None:
    box = _Fixture(tmp_path, remote_name="upstream", configure_key=True, push=True)
    _open_rc_with_work(box, "v0.1.0")
    cfg = box.project_dir / "project.cfg"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace("git_remote_name = upstream\n", "git_remote_name = --upload-pack=x\n"),
        encoding="utf-8",
    )

    decided = box.run(_RELEASE, "--project", _PROJECT, "--decide-only")
    text = decided.stdout + decided.stderr

    assert decided.returncode == 0, _out(decided)
    assert "git_remote_name.invalid" not in text, _out(decided)
    assert "[Step 1] decide-only — skipping git fetch <invalid git_remote_name>" in text, _out(decided)
