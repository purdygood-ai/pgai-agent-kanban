"""
test_bug_status_done.py
=======================
team/scripts/migrate/bug-status-done.sh promotes bug/priority items from
``running`` to ``done`` for every item a shipped requirements bundle names.
Its ``--help`` promises exit 0 on success; these tests pin that for a real
run and a ``--dry-run``, on an empty root and on one with an item to promote.

A bundle counts as shipped when the project's release tag for its Target
Version exists in the dev tree: ``<branch_prefix>vX.Y.Z``, read from the
project's project.cfg. The tests pin that for a prefixed project
(``ai_v1.0.0``) and the unprefixed control (``v1.0.0``); that a dev tree
holding no release tag, or one that is not a git repository, promotes
nothing and says so; and that a missing dev tree keeps its "treated as
shipped" warning and promotes every bundle.

Every kanban root is a throwaway under pytest's tmp_path, with HOME and the
temp dir pointed inside it, and every dev tree is a throwaway git repository
beside it; no live kanban root or dev tree is read or written.
"""

from __future__ import annotations

import pathlib
import subprocess

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_DIR / "scripts" / "migrate" / "bug-status-done.sh"

_DRY_RUN_DONE_LINE = "(DRY RUN — no files were written)\n"
_COMPLETE_LINE = "=== scripts/migrate/bug-status-done.sh: complete ===\n"

# Ambient names that would point the script away from the fixture root.
_UNSET = "unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT PGAI_PROJECT_NAME\n"


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    """An empty kanban root: a projects/ directory and nothing registered."""
    root = tmp_path / "kanban"
    (root / "projects").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "home").mkdir()
    return root


def _run(tmp_path: pathlib.Path, root: pathlib.Path, *args: str):
    return run_bash(
        tmp_path,
        _UNSET + f"'{_SCRIPT}' {' '.join(args)}\n",
        timeout=60,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "HOME": str(root / "home"),
        },
    )


def _seed_shipped_bundle(root: pathlib.Path) -> pathlib.Path:
    """A project whose one bundle names one ``running`` bug; returns the bug file.

    The project has no project.cfg, so the script cannot check tags and
    treats every bundle as shipped.
    """
    project = root / "projects" / "alpha"
    (project / "requirements").mkdir(parents=True)
    (project / "bugs").mkdir()
    bug = project / "bugs" / "BUG-0001-example.md"
    bug.write_text("# BUG-0001-example\n\n## Status\nrunning\n\n## Category\nbugfix\n", encoding="utf-8")
    (project / "requirements" / "v1.0.1-bugfix-bundle-20260101.md").write_text(
        f"# Bundle\n\n## Target Version\nv1.0.1\n\n## Bundled Items\n\n- BUG-0001-example.md (`{bug}`)\n",
        encoding="utf-8",
    )
    return bug


def test_real_run_on_an_empty_root_exits_zero(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, _root(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.endswith(_COMPLETE_LINE)
    assert "DRY RUN" not in result.stdout


def test_dry_run_on_an_empty_root_exits_zero_and_says_nothing_was_written(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, _root(tmp_path), "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.endswith(_COMPLETE_LINE + _DRY_RUN_DONE_LINE)


def test_real_run_that_promotes_an_item_exits_zero(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    bug = _seed_shipped_bundle(root)
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "    promoted (running -> done): BUG-0001-example.md\n" in result.stdout
    assert "## Status\ndone\n" in bug.read_text(encoding="utf-8")


def test_dry_run_that_would_promote_an_item_leaves_it_running(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    bug = _seed_shipped_bundle(root)
    result = _run(tmp_path, root, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "    [DRY RUN] would promote (running -> done): BUG-0001-example.md\n" in result.stdout
    assert result.stdout.endswith(_DRY_RUN_DONE_LINE)
    assert "## Status\nrunning\n" in bug.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Shipped-tag scan: the project's branch_prefix and the dev tree's tags
# ---------------------------------------------------------------------------

_TREATED_AS_SHIPPED = "All 'running' items in any bundle will be treated as shipped."
_NOTHING_SHIPPED_LINE = "  no shipped release tag given: no bundle is treated as shipped, nothing is promoted\n"


def _dev_tree(tmp_path: pathlib.Path, *tags: str) -> pathlib.Path:
    """A throwaway git repository with one commit carrying *tags*."""
    require_binary("git", label="bug-status-done")
    repo = tmp_path / "dev"
    repo.mkdir()
    home = tmp_path / "git-home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(home_dir=home, extra_env={"GIT_CONFIG_NOSYSTEM": "1"})

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=env)

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "README").write_text("dev tree\n", encoding="utf-8")
    git("add", "README")
    git("commit", "-m", "initial")
    for tag in tags:
        git("tag", tag)
    return repo


def _seed_two_bundles(root: pathlib.Path, cfg: str | None) -> tuple[pathlib.Path, pathlib.Path]:
    """Project ``pref`` with a v1.0.0 and a v1.1.0 bundle, each naming one
    ``running`` bug; *cfg* is the project.cfg body (None writes none).
    Returns (v1.0.0 bug, v1.1.0 bug)."""
    project = root / "projects" / "pref"
    (project / "requirements").mkdir(parents=True)
    (project / "bugs").mkdir()
    if cfg is not None:
        (project / "project.cfg").write_text(cfg, encoding="utf-8")
    bugs = []
    for version, name in (("v1.0.0", "BUG-0001-a.md"), ("v1.1.0", "BUG-0002-b.md")):
        bug = project / "bugs" / name
        bug.write_text(f"# {name}\n\n## Status\nrunning\n\n## Category\nbugfix\n", encoding="utf-8")
        (project / "requirements" / f"{version}-bugfix-bundle-x.md").write_text(
            f"# Bundle\n\n## Target Version\n{version}\n\n## Bundled Items\n\n- {name} (`{bug}`)\n",
            encoding="utf-8",
        )
        bugs.append(bug)
    return bugs[0], bugs[1]


def _cfg(dev_tree: pathlib.Path, prefix_line: str = "") -> str:
    return f"[project]\nproject_name = pref\ndev_tree_path = {dev_tree}\n{prefix_line}"


def _status(bug: pathlib.Path) -> str:
    return bug.read_text(encoding="utf-8").split("## Status\n", 1)[1].split("\n", 1)[0]


def test_prefixed_project_promotes_only_the_bundle_whose_prefixed_tag_exists(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    dev = _dev_tree(tmp_path, "ai_v1.0.0")
    shipped, unshipped = _seed_two_bundles(root, _cfg(dev, "branch_prefix = ai_\n"))
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_status(shipped), _status(unshipped)) == ("done", "running")
    assert "  bundle: v1.0.0-bugfix-bundle-x.md (version=v1.0.0, SHIPPED)\n" in result.stdout
    assert "v1.1.0-bugfix-bundle-x.md" not in result.stdout
    assert _TREATED_AS_SHIPPED not in result.stderr


def test_prefixed_project_does_not_count_a_bare_tag_as_its_release(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    dev = _dev_tree(tmp_path, "ai_v1.0.0", "v1.1.0")
    shipped, unshipped = _seed_two_bundles(root, _cfg(dev, "branch_prefix = ai_\n"))
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_status(shipped), _status(unshipped)) == ("done", "running")


def test_unprefixed_project_promotes_only_the_bundle_whose_bare_tag_exists(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    dev = _dev_tree(tmp_path, "v1.0.0", "ai_v1.1.0")
    shipped, unshipped = _seed_two_bundles(root, _cfg(dev))
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_status(shipped), _status(unshipped)) == ("done", "running")
    assert "  bundle: v1.0.0-bugfix-bundle-x.md (version=v1.0.0, SHIPPED)\n" in result.stdout
    assert "v1.1.0-bugfix-bundle-x.md" not in result.stdout


def test_dev_tree_without_a_release_tag_promotes_nothing_and_says_so(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    dev = _dev_tree(tmp_path, "v1.0.0", "v1.1.0")
    first, second = _seed_two_bundles(root, _cfg(dev, "branch_prefix = ai_\n"))
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_status(first), _status(second)) == ("running", "running")
    assert f"  no release tag matching ai_vX.Y.Z in dev tree {dev}\n" in result.stdout
    assert _NOTHING_SHIPPED_LINE in result.stdout
    assert "SHIPPED" not in result.stdout
    assert _TREATED_AS_SHIPPED not in result.stderr


def test_dev_tree_that_is_not_a_git_repository_promotes_nothing(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    dev = tmp_path / "not-a-repo"
    dev.mkdir()
    first, second = _seed_two_bundles(root, _cfg(dev))
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_status(first), _status(second)) == ("running", "running")
    assert f"dev tree {dev} is not a git repository" in result.stderr
    assert _NOTHING_SHIPPED_LINE in result.stdout
    assert _TREATED_AS_SHIPPED not in result.stderr


def test_missing_dev_tree_warns_and_treats_every_bundle_as_shipped(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    first, second = _seed_two_bundles(root, _cfg(tmp_path / "no-such-dev-tree", "branch_prefix = ai_\n"))
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_status(first), _status(second)) == ("done", "done")
    assert _TREATED_AS_SHIPPED in result.stderr


def test_invalid_branch_prefix_promotes_nothing_and_names_the_config(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    dev = _dev_tree(tmp_path, "v1.0.0")
    first, second = _seed_two_bundles(root, _cfg(dev, "branch_prefix = ai/x\n"))
    result = _run(tmp_path, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (_status(first), _status(second)) == ("running", "running")
    assert str(root / "projects" / "pref" / "project.cfg") in result.stderr
    assert _NOTHING_SHIPPED_LINE in result.stdout
