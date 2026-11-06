"""
nondefault_git_config.py
========================
A throwaway kanban root whose one project names a non-default branch prefix
(``branch_prefix = zz_``) and git remote (``git_remote_name = upstream``),
with a local dev tree and two local bare remotes laid out so that every
reading of the configuration gives a different, recognisable answer.

Use it to check that the bash resolvers (``pp_branch_prefix`` /
``pp_git_remote_name``), the Python ones (``lib/branch_prefix.py`` /
``lib/git_remote.py``) and every caller built on them answer from the
project's own configuration rather than the ``ai_`` / ``origin`` defaults.

Layout
------
::

    <parent>/<subdir>/
        home/                      HOME for every subprocess
        kanban/                    the kanban root
            projects.cfg           registers PROJECT
            tmp/                   PGAI_AGENT_KANBAN_TEMP_DIR
            projects/<PROJECT>/
                project.cfg        branch_prefix = zz_, git_remote_name = upstream,
                                   push_to_remote = false, dev_tree_path = dev/
                release-state.md   Active RC RC_VERSION, Last Released RELEASED_VERSION
                tasks/<TASK_ID>/   a DONE task whose README names a bare
                                   ``rc/<RC_VERSION>`` source branch
                tasks/queues/      coder_backlog.md lists TASK_ID
                bugs/ requirements/ priority/ logs/ overwatch/
        dev/                       the dev tree (a real git repository)
        upstream.git/              bare remote ``upstream`` (the configured one)
        origin.git/                bare remote ``origin`` (the decoy)

The dev tree, on ``zz_main`` at commit B:

- commit A tagged ``zz_v1.0.0``; commit B tagged ``zz_v1.1.0``, plus the
  decoy tags ``v9.0.0`` and ``ai_v9.0.0`` (what an unprefixed or ``ai_``
  reading would report);
- ``refs/remotes/upstream/zz_main`` at B and ``refs/remotes/origin/zz_main``
  at A, so a released-version lookup answers ``v1.1.0`` through the
  configured remote and ``v1.0.0`` through ``origin``;
- branches ``zz_rc/<RC_VERSION>``, ``zz_feature/<TASK_ID>`` and the decoys
  ``feature/<TASK_ID>`` and ``ai_feature/<TASK_ID>``, all at B.

``upstream.git`` alone holds :data:`UPSTREAM_ONLY_TAG` and ``origin.git``
alone holds :data:`ORIGIN_ONLY_TAG`; the dev tree holds neither, so a tag
lookup that asks the remote answers by which remote it asked.

No remote is contacted over a network: both remotes are local bare
repositories, and remote-tracking refs are written with ``update-ref``
rather than fetched.

Usage
-----
::

    from tests.fixtures.nondefault_git_config import build_nondefault_git_config_root

    def test_prefix(tmp_path):
        fx = build_nondefault_git_config_root(tmp_path)
        assert fx.bash('pp_branch_prefix "$1"', fx.project).stdout == "zz_\\n"

Every path is under *parent* (pytest's ``tmp_path``); nothing reads or
writes the live install.
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import sys

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"

#: The project the fixture registers. It is the framework project because the
#: framework-version readers (dashboard/lib/version.sh, lib/released_version.py)
#: read that project's configuration and no other.
PROJECT = "pgai-agent-kanban"

#: The configured, non-default branch prefix.
BRANCH_PREFIX = "zz_"

#: The configured, non-default git remote.
REMOTE_NAME = "upstream"

#: The default remote, present as a decoy that answers differently.
DECOY_REMOTE = "origin"

#: The active RC; ``zz_rc/<RC_VERSION>`` exists in the dev tree.
RC_VERSION = "v1.2.0"

#: The highest ``zz_`` release tag merged into ``upstream/zz_main``.
RELEASED_VERSION = "v1.1.0"

#: The highest ``zz_`` release tag merged into ``origin/zz_main``.
DECOY_RELEASED_VERSION = "v1.0.0"

#: What an unprefixed or ``ai_``-prefixed tag scan of the dev tree finds.
DECOY_PREFIX_VERSION = "v9.0.0"

#: A tag only the configured remote holds.
UPSTREAM_ONLY_TAG = f"{BRANCH_PREFIX}v1.1.1"

#: A tag only the decoy remote holds.
ORIGIN_ONLY_TAG = f"{BRANCH_PREFIX}v1.1.2"

#: The DONE task the fixture carries (for reset and block-requeue callers).
TASK_ID = "CODER-20260101-001-sample-task"


@dataclasses.dataclass(frozen=True)
class NonDefaultGitConfigRoot:
    """The built fixture.

    Attributes:
        root:         The kanban root.
        project:      :data:`PROJECT`.
        project_root: ``<root>/projects/<project>``.
        dev_tree:     The dev tree repository.
        upstream_url: The URL ``git remote get-url upstream`` answers.
        origin_url:   The URL ``git remote get-url origin`` answers.
        home:         HOME for subprocesses.
    """

    root: pathlib.Path
    project: str
    project_root: pathlib.Path
    dev_tree: pathlib.Path
    upstream_url: str
    origin_url: str
    home: pathlib.Path

    @property
    def task_dir(self) -> pathlib.Path:
        """The folder of :data:`TASK_ID`."""
        return self.project_root / "tasks" / TASK_ID

    def env(self, extra: dict | None = None) -> dict:
        """A scrubbed subprocess environment pointing every root variable at :attr:`root`.

        ``PGAI_TASKS_DIR``, ``PGAI_PROJECT_ROOT``, ``PGAI_PROJECT_NAME`` and
        ``PGAI_GIT_LOCAL_ONLY`` are absent, as they are in an operator's
        shell; *extra* is layered on top.
        """
        values = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        values.update(extra or {})
        return build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env=values,
        )

    def bash(
        self,
        body: str,
        *args: str,
        extra_env: dict | None = None,
        cwd: pathlib.Path | None = None,
        prelude: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run *body* under ``bash``, by default with ini_parser.sh and project_paths.sh sourced.

        *args* become ``$1``, ``$2``, ...; the environment is :meth:`env`.
        ``prelude=False`` sources nothing first, for a script that loads
        its own libraries.
        """
        prelude = f'source "{_LIB / "ini_parser.sh"}"\nsource "{_LIB / "project_paths.sh"}"\n' if prelude else ""
        return subprocess.run(
            ["bash", "-c", f"{prelude}{body}\n", "fixture", *args],
            capture_output=True,
            text=True,
            env=self.env(extra_env),
            cwd=str(cwd or self.root),
            timeout=60,
        )

    def git(self, *args: str, repo: pathlib.Path | None = None) -> str:
        """``git -C <repo> <args>`` (default repo: the dev tree); stdout stripped."""
        return _git(self.env(), repo or self.dev_tree, *args)


def _git(env: dict, repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=fixture@example.invalid", "-c", "user.name=Fixture", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git -C {repo} {' '.join(args)} failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout.strip()


def _commit(env: dict, repo: pathlib.Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    _git(env, repo, "add", f"{name}.txt")
    _git(env, repo, "commit", "--quiet", "-m", name)
    return _git(env, repo, "rev-parse", "HEAD")


def _write_project(project_root: pathlib.Path, dev_tree: pathlib.Path) -> None:
    for sub in ("tasks/queues", "bugs", "requirements/priority", "priority", "logs", "overwatch", "release-state"):
        (project_root / sub).mkdir(parents=True, exist_ok=True)
    (project_root / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {PROJECT}\n"
        f"dev_tree_path = {dev_tree}\n"
        f"branch_prefix = {BRANCH_PREFIX}\n"
        f"git_remote_name = {REMOTE_NAME}\n"
        "push_to_remote = false\n"
        "workflow_type = release\n",
        encoding="utf-8",
    )
    (project_root / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{RC_VERSION}\n\n"
        "## RC Opened At\n2026-01-01T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20260101-001-open-rc\n\n"
        f"## Last Released\n{RELEASED_VERSION}\n",
        encoding="utf-8",
    )
    task_dir = project_root / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# Task: sample\n\n## Task ID\n{TASK_ID}\n\n## Owner\nClaude\n\n## Role\nCODER\n\n"
        f"## Source Branch\nrc/{RC_VERSION}\n\n## Feature Branch\n{BRANCH_PREFIX}feature/{TASK_ID}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{TASK_ID}\n\n## Role\nCODER\n\n## State\nDONE\n\n"
        "## Blockers\nnone\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )
    (project_root / "tasks" / "queues" / "coder_backlog.md").write_text(f"- [x] {TASK_ID}\n", encoding="utf-8")


def _build_repositories(env: dict, base: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """Build the dev tree and both bare remotes; return (dev_tree, upstream_url, origin_url)."""
    dev = base / "dev"
    dev.mkdir()
    _git(env, dev, "init", "--quiet", "-b", f"{BRANCH_PREFIX}main")
    first = _commit(env, dev, "first")
    _git(env, dev, "tag", f"{BRANCH_PREFIX}{DECOY_RELEASED_VERSION}")
    second = _commit(env, dev, "second")
    for tag in (f"{BRANCH_PREFIX}{RELEASED_VERSION}", DECOY_PREFIX_VERSION, f"ai_{DECOY_PREFIX_VERSION}"):
        _git(env, dev, "tag", tag)
    for branch in (
        f"{BRANCH_PREFIX}rc/{RC_VERSION}",
        f"{BRANCH_PREFIX}feature/{TASK_ID}",
        f"feature/{TASK_ID}",
        f"ai_feature/{TASK_ID}",
    ):
        _git(env, dev, "branch", branch)

    urls = {}
    for remote, only_tag in ((REMOTE_NAME, UPSTREAM_ONLY_TAG), (DECOY_REMOTE, ORIGIN_ONLY_TAG)):
        bare = base / f"{remote}.git"
        _git(env, base, "clone", "--quiet", "--bare", str(dev), str(bare))
        _git(env, bare, "tag", only_tag, second)
        _git(env, dev, "remote", "add", remote, str(bare))
        urls[remote] = str(bare)
    _git(env, dev, "update-ref", f"refs/remotes/{REMOTE_NAME}/{BRANCH_PREFIX}main", second)
    _git(env, dev, "update-ref", f"refs/remotes/{DECOY_REMOTE}/{BRANCH_PREFIX}main", first)
    return dev, urls[REMOTE_NAME], urls[DECOY_REMOTE]


def build_nondefault_git_config_root(
    parent: pathlib.Path, subdir: str = "nondefault_git_config"
) -> NonDefaultGitConfigRoot:
    """Build the fixture described in the module docstring under *parent*/*subdir*.

    Args:
        parent: A throwaway directory (pytest's ``tmp_path``).
        subdir: The directory created under *parent* to hold everything.

    Returns:
        The :class:`NonDefaultGitConfigRoot`.

    Raises:
        RuntimeError: a git command failed while building the repositories.
    """
    base = parent / subdir
    home = base / "home"
    root = base / "kanban"
    for directory in (home, root / "tmp"):
        directory.mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{PROJECT}]\npriority=1\n", encoding="utf-8")
    env = build_scrubbed_env(home_dir=home, extra_env={"GIT_CONFIG_NOSYSTEM": "1"})
    dev, upstream_url, origin_url = _build_repositories(env, base)
    project_root = root / "projects" / PROJECT
    _write_project(project_root, dev)
    return NonDefaultGitConfigRoot(
        root=root,
        project=PROJECT,
        project_root=project_root,
        dev_tree=dev,
        upstream_url=upstream_url,
        origin_url=origin_url,
        home=home,
    )
