"""
dashboard_render_fixture.py
============================
Single-project kanban-root builder for BUG-0112 dashboard render-path
fixtures (the network-stub fixture and the missing-tree fixture).

WHY THIS EXISTS
---------------
``tests/fixtures/two_project.py`` builds a full multi-project kanban root but
always points ``dev_tree_path`` at ``/dev/null`` — adequate for cross-project
isolation tests, but useless for BUG-0112's fixtures, which need to
distinguish four concrete dev-tree states:

  - a REAL local git repository with at least one tag (the happy path a
    dashboard refresh renders against), and
  - three distinct missing/invalid states the render-path guard must degrade
    cleanly against (unconfigured, missing directory, present-but-not-a-repo)
    — the three degrade branches ``git-status.sh`` / ``git-recent-tags.sh``
    already document and unify under "git tree not available: <path>".

This module builds a single-project kanban root — same shape as
``two_project.py``'s per-project tree (project.cfg, release-state.md,
tasks/queues/*, logs/, overwatch/, metrics/) — parameterized by
``dev_tree_mode`` so both fixture test files (network-stub, missing-tree)
share one builder instead of hand-rolling the tree twice.

DEV_TREE_MODE VALUES
---------------------
  "valid"          — a real git repo is created under the fixture root with
                     one commit and one annotated tag (v0.1.0).  This is the
                     mode the network-stub fixture uses for its happy-path
                     full-refresh run.
  "missing"        — dev_tree_path is set to a path that does not exist.
  "not_a_repo"     — dev_tree_path exists as a plain directory with no
                     ``.git`` subdirectory.
  "unconfigured"   — no dev_tree_path key is written into project.cfg at all
                     (empty string default).

USAGE
-----
    from tests.fixtures.dashboard_render_fixture import build_dashboard_project_root

    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode="valid")
    # fixture.kanban_root   — pathlib.Path, the synthetic kanban root
    # fixture.project_name  — the single registered project's name
    # fixture.dev_tree_path — pathlib.Path or None (None for "unconfigured")
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

# Environment variables that leak project/dev-tree identity from the CALLING
# agent's own ambient shell (e.g. a CODER task's wake-script environment,
# which legitimately sets PGAI_PROJECT_NAME=pgai-agent-kanban and
# PGAI_DEV_TREE_PATH for ITS OWN work) into a dashboard-script subprocess
# under test.  Dashboard render-path scripts fall back to several of these
# as ambient defaults when no explicit --project/--kanban-root flag is
# given, so leaving them set would silently point a fixture-driven test at
# the real live project/dev-tree instead of the synthetic fixture — a
# false-positive risk that has nothing to do with BUG-0112 correctness.
# Callers building a subprocess environment for a dashboard fixture test
# should start from AMBIENT_LEAK_ENV_VARS_TO_CLEAR and delete each key.
AMBIENT_LEAK_ENV_VARS_TO_CLEAR: tuple[str, ...] = (
    "PGAI_PROJECT_NAME",
    "PGAI_DEV_TREE_PATH",
    "PGAI_PROJECT_ROOT",
    "PGAI_TASKS_DIR",
    "PGAI_QUEUE_DIR",
    "PGAI_RELEASE_STATE_PATH",
    "PP_project_name",
    "PP_dev_tree_path",
    "PP_git_repo_url",
    "PP_git_remote_name",
    "PP_workflow_type",
    "PP_branch_prefix",
    "PP_TEMP_DIR",
    "PP_max_patch",
    "PP_max_minor",
    "PP_max_major",
    "PP_max_priorities_per_run",
)


def clean_subprocess_env(*, kanban_root: pathlib.Path) -> dict[str, str]:
    """Return a subprocess environment isolated from ambient project identity.

    Starts from a copy of the current process's environment, deletes every
    key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR, then sets
    PGAI_AGENT_KANBAN_ROOT_PATH to *kanban_root* plus NO_COLOR/TERM=dumb for
    deterministic, colorless output.  Callers still need to pass an explicit
    --project (or positional project name) argument to any dashboard script
    that requires one — this function only prevents ambient fallback
    defaults from masking that requirement.
    """
    env = dict(os.environ)
    for key in AMBIENT_LEAK_ENV_VARS_TO_CLEAR:
        env.pop(key, None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"
_REAL_PGAI_AGENT_KANBAN_PKG = _TEAM_DIR / "pgai_agent_kanban"
_WORKFLOW_PLUGIN_DIRS = ["release", "document"]

_QUEUE_FILES = [
    "coder_backlog.md",
    "pm_backlog.md",
    "writer_backlog.md",
    "tester_backlog.md",
    "cm_backlog.md",
    "bug_backlog.md",
    "priority_backlog.md",
]

DEFAULT_PROJECT_NAME = "dash_fixture_project"


@dataclass(frozen=True)
class DashboardFixture:
    """Result of build_dashboard_project_root()."""

    kanban_root: pathlib.Path
    project_name: str
    dev_tree_path: Optional[pathlib.Path]


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _make_valid_dev_tree(dev_tree: pathlib.Path) -> None:
    """Create a real, minimal git repo with one commit and one tag."""
    dev_tree.mkdir(parents=True, exist_ok=True)
    _git(dev_tree, "init", "--initial-branch=main")
    _git(dev_tree, "config", "user.email", "fixture@example.com")
    _git(dev_tree, "config", "user.name", "Dashboard Fixture")
    (dev_tree / "README.md").write_text("dashboard fixture repo\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-m", "initial commit")
    _git(dev_tree, "tag", "-a", "v0.1.0", "-m", "release v0.1.0")


def _resolve_dev_tree_path(
    fixture_root: pathlib.Path,
    dev_tree_mode: str,
) -> Optional[pathlib.Path]:
    """Build (or point at) a dev tree per dev_tree_mode; return its path or None."""
    if dev_tree_mode == "valid":
        dev_tree = fixture_root / "dev_tree"
        _make_valid_dev_tree(dev_tree)
        return dev_tree

    if dev_tree_mode == "missing":
        # A syntactically valid but never-created path.
        return fixture_root / "dev_tree_does_not_exist"

    if dev_tree_mode == "not_a_repo":
        dev_tree = fixture_root / "dev_tree_not_a_repo"
        dev_tree.mkdir(parents=True, exist_ok=True)
        (dev_tree / "placeholder.txt").write_text("not a git repo\n", encoding="utf-8")
        return dev_tree

    if dev_tree_mode == "unconfigured":
        return None

    raise ValueError(
        f"build_dashboard_project_root: unknown dev_tree_mode {dev_tree_mode!r}; "
        "expected one of: valid, missing, not_a_repo, unconfigured"
    )


def build_dashboard_project_root(
    parent: pathlib.Path,
    *,
    dev_tree_mode: str = "valid",
    project_name: str = DEFAULT_PROJECT_NAME,
    subdir: str = "kanban_dashboard_fixture",
) -> DashboardFixture:
    """Build a single-project kanban root suitable for full dashboard renders.

    Parameters
    ----------
    parent:
        Parent directory for the new root (pass pytest's ``tmp_path``).
    dev_tree_mode:
        One of "valid", "missing", "not_a_repo", "unconfigured" — see module
        docstring.
    project_name:
        Name of the single registered project.
    subdir:
        Subdirectory name to create under *parent*.

    Returns
    -------
    DashboardFixture
    """
    root = parent / subdir
    root.mkdir(parents=True, exist_ok=True)

    dev_tree_path = _resolve_dev_tree_path(root, dev_tree_mode)

    # --- kanban.cfg ---
    (root / "kanban.cfg").write_text(
        "# kanban.cfg — BUG-0112 dashboard render-path fixture\n"
        "\n"
        "[paths]\n"
        "\n"
        "[chain]\n"
        "pm_mode = automatic\n",
        encoding="utf-8",
    )

    # --- projects.cfg: register the single project ---
    (root / "projects.cfg").write_text(
        "# projects.cfg — BUG-0112 dashboard render-path fixture\n"
        "\n"
        f"[project:{project_name}]\n"
        "priority=1\n"
        "description=BUG-0112 dashboard render-path fixture project\n"
        "enabled=true\n",
        encoding="utf-8",
    )

    # --- kanban-root runtime directories ---
    (root / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "debug" / "archive").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "queues" / "plans").mkdir(parents=True, exist_ok=True)

    # --- workflows/ plugin dirs (release + document) ---
    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGIN_DIRS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    # --- pgai_agent_kanban/ Python helper package (live-install anchor) ---
    # Several dashboard scripts (attention.sh's scan_attention.py, etc.)
    # resolve their Python helpers via
    # tests/fixtures/dashboard_render_fixture... -> dashboard/lib/helper_resolver.sh's
    # "live-install anchor" probe: $KANBAN_ROOT/pgai_agent_kanban/<rel_path>.
    # install.sh copies team/pgai_agent_kanban/ there on a real install; this
    # fixture mirrors that so dashboard scripts resolve their helpers without
    # depending on PGAI_DEV_TREE_PATH (a global framework dev-tree pointer
    # unrelated to BUG-0112's per-project dev_tree_path).
    if _REAL_PGAI_AGENT_KANBAN_PKG.is_dir():
        dest_pkg = root / "pgai_agent_kanban"
        if dest_pkg.exists():
            shutil.rmtree(dest_pkg)
        shutil.copytree(_REAL_PGAI_AGENT_KANBAN_PKG, dest_pkg)

    # --- Project directory ---
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    dev_tree_line = (
        f"dev_tree_path = {dev_tree_path}\n" if dev_tree_path is not None else ""
    )
    (proj / "project.cfg").write_text(
        "# project.cfg — BUG-0112 dashboard render-path fixture\n"
        "\n"
        "[project]\n"
        f"project_name = {project_name}\n"
        f"{dev_tree_line}"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        "git_remote_name = origin\n"
        "workflow_type = release\n"
        "is_self_build = false\n"
        "\n"
        "[versioning]\n"
        "max_patch = 99\n"
        "max_minor = 9\n"
        "max_major = 0\n",
        encoding="utf-8",
    )

    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n\n"
        "## Last Released\n"
        "none\n",
        encoding="utf-8",
    )

    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "plans").mkdir(parents=True, exist_ok=True)
    for queue_file in _QUEUE_FILES:
        agent = queue_file.replace("_backlog.md", "")
        (queues_dir / queue_file).write_text(
            f"# {agent.upper()} Backlog\n\n"
            "<!-- Tasks are listed below. One task per line. -->\n"
            "<!-- Format: - [ ] TASK-ID -->\n",
            encoding="utf-8",
        )

    (proj / "logs").mkdir(parents=True, exist_ok=True)
    (proj / "overwatch" / "backups").mkdir(parents=True, exist_ok=True)
    (proj / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (proj / "metrics").mkdir(parents=True, exist_ok=True)

    for req_dir in ("requirements", "requirements/priority", "bugs"):
        (proj / req_dir).mkdir(parents=True, exist_ok=True)

    return DashboardFixture(
        kanban_root=root,
        project_name=project_name,
        dev_tree_path=dev_tree_path,
    )
