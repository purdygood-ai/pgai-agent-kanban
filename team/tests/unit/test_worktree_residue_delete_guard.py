"""
test_worktree_residue_delete_guard.py
=====================================
Behavioral coverage for the recursive-delete guard in worktree.sh: the only
rm -rf in that file (create_task_worktree's orphaned-residue removal) runs
only when pgai_worktree_path_strictly_inside accepts the candidate -- both
paths canonicalised with realpath (symlinks resolved, '.' and '..'
collapsed) and the candidate a strict descendant of the worktree base. A
candidate that fails is refused dual-channel (worktree.residue.outside-base)
with nothing deleted and the create exits non-zero.

Every fixture plants sentinel files at the places an unguarded delete would
reach (the base's parent, the symlink target, the base itself) and asserts
they survive, so a guard that fell back to a string-prefix comparison of
the unresolved text -- which <base>/.. passes -- would fail visibly here.

This module drives the real worktree.sh against a real git repository with
PGAI_WORKTREE_BASE pinned under tmp_path, the same way
test_reconcile_terminal_worktrees.py does. All fixtures are tmp_path-based;
no real project state or the live kanban install is read or mutated.

Fixtures exercised:
  (A) A candidate of <base>/.. (task id '..') is refused; the parent's
      sentinel and the base survive; nothing is created.
  (B) A symlink inside the base whose target lies outside it is refused;
      the target's sentinel and the symlink itself survive.
  (C) A candidate equal to the base itself (task id '.') is refused; the
      base and its contents survive.
  (D) A genuine orphaned residue directory directly under the base (a
      stale, unregistered directory at the task's own path) is still
      removed and the worktree is created there.
  (E) The predicate itself, over the canonical cases: strict descendant
      accepted; base, parent, sibling-with-shared-prefix, out-of-base
      symlink, and a nonexistent path refused.
  (F) The file carries no rm -rf that is not gated on the predicate: every
      non-comment rm -rf line in worktree.sh sits inside an
      `if pgai_worktree_path_strictly_inside ...; then` block.

Naming: function names describe behavior under test, never a bug ID or
task ID (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

from tests.fixtures.binary_guard import require_binary

_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_WORKTREE_LIB = _REAL_REPO_TEAM_SCRIPTS_DIR / "lib" / "worktree.sh"
_TEMP_LIB = _REAL_REPO_TEAM_SCRIPTS_DIR / "lib" / "temp.sh"

_REFUSAL_KEY = "error:   worktree.residue.outside-base"
_RC_BRANCH = "rc/v1.0.0"


def _git_path() -> str:
    return require_binary("git", label="worktree-residue-delete-guard").path


def _init_dev_tree(git: str, dev_tree: pathlib.Path) -> None:
    dev_tree.mkdir(parents=True, exist_ok=True)

    def run(*args: str) -> None:
        result = subprocess.run([git, "-C", str(dev_tree), *args], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"git {args} failed: {result.stderr}"

    run("init", "-q", "-b", "ai_main")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "test")
    (dev_tree / "README.md").write_text("fixture\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-q", "-m", "initial")
    run("branch", _RC_BRANCH)


def _script_header(tmp_path: pathlib.Path, base: pathlib.Path) -> str:
    """Sources the real temp.sh and worktree.sh with the worktree base pinned
    to `base` (Tier 1 override) and the temp root under tmp_path.
    PGAI_PROJECT_NAME is cleared so the emitter's refusal-ledger recording
    has no project to write into."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -uo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path / 'kanban_temp'}"
        export PGAI_WORKTREE_BASE="{base}"
        unset PP_project_name PGAI_AGENT_KANBAN_ROOT_PATH PGAI_PROJECT_NAME
        source "{_TEMP_LIB}"
        source "{_WORKTREE_LIB}"
    """)


def _run(script: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    script_path = tmp_path / "run_script.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(["bash", str(script_path)], capture_output=True, text=True, timeout=60, cwd=str(tmp_path))


def _create_call(task_id: str, dev_tree: pathlib.Path) -> str:
    return textwrap.dedent(f"""\
        create_task_worktree "{task_id}" "{_RC_BRANCH}" "feature/{task_id}" "{dev_tree}"
        printf 'RC=[%s]\\n' "$?"
    """)


def _plant(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Return (dev_tree, base, parent_sentinel) with the base created and a
    sentinel file in its parent directory."""
    dev_tree = tmp_path / "dev_tree"
    base = tmp_path / "scratch" / "worktrees"
    base.mkdir(parents=True)
    parent_sentinel = base.parent / "parent-sentinel"
    parent_sentinel.write_text("must survive\n", encoding="utf-8")
    return dev_tree, base, parent_sentinel


# ---------------------------------------------------------------------------
# (A) <base>/..
# ---------------------------------------------------------------------------


def test_candidate_resolving_to_parent_of_base_is_refused_with_nothing_deleted(tmp_path: pathlib.Path) -> None:
    git = _git_path()
    dev_tree, base, parent_sentinel = _plant(tmp_path)
    _init_dev_tree(git, dev_tree)

    result = _run(_script_header(tmp_path, base) + _create_call("..", dev_tree), tmp_path)

    assert "RC=[1]" in result.stdout, f"create must exit non-zero on a refused candidate\nstdout={result.stdout}\nstderr={result.stderr}"
    assert _REFUSAL_KEY in result.stderr, f"refusal must carry the house-shape error key\nstderr={result.stderr}"
    assert f"'{base}/..'" in result.stderr and f"'{base}'" in result.stderr, (
        f"message must name the candidate and the base\nstderr={result.stderr}"
    )
    assert "remedy:  ls -ld -- " in result.stderr and "<" not in result.stderr.split("remedy:")[1].splitlines()[0]
    assert parent_sentinel.exists(), "the base's parent was recursively deleted"
    assert base.is_dir(), "the base itself was deleted"
    assert dev_tree.is_dir() and (dev_tree / "README.md").exists()
    assert "worktree" not in subprocess.run(
        [git, "-C", str(dev_tree), "worktree", "list", "--porcelain"], capture_output=True, text=True, timeout=30
    ).stdout.replace(f"worktree {dev_tree}", ""), "no worktree may be created on a refused candidate"


# ---------------------------------------------------------------------------
# (B) symlink inside the base resolving outside it
# ---------------------------------------------------------------------------


def test_symlink_inside_base_escaping_it_is_refused_with_target_intact(tmp_path: pathlib.Path) -> None:
    git = _git_path()
    dev_tree, base, parent_sentinel = _plant(tmp_path)
    _init_dev_tree(git, dev_tree)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_sentinel = outside / "outside-sentinel"
    outside_sentinel.write_text("must survive\n", encoding="utf-8")
    link = base / "CODER-20260101-001-escape-link"
    link.symlink_to(outside, target_is_directory=True)

    result = _run(_script_header(tmp_path, base) + _create_call(link.name, dev_tree), tmp_path)

    assert "RC=[1]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _REFUSAL_KEY in result.stderr, result.stderr
    assert outside_sentinel.exists(), "the symlink's out-of-base target was recursively deleted"
    assert link.is_symlink(), "even the symlink itself is left for the operator to inspect"
    assert parent_sentinel.exists()


# ---------------------------------------------------------------------------
# (C) candidate equal to the base itself
# ---------------------------------------------------------------------------


def test_candidate_equal_to_base_is_refused_with_base_contents_intact(tmp_path: pathlib.Path) -> None:
    git = _git_path()
    dev_tree, base, parent_sentinel = _plant(tmp_path)
    _init_dev_tree(git, dev_tree)
    base_sentinel = base / "base-sentinel"
    base_sentinel.write_text("must survive\n", encoding="utf-8")

    result = _run(_script_header(tmp_path, base) + _create_call(".", dev_tree), tmp_path)

    assert "RC=[1]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _REFUSAL_KEY in result.stderr, result.stderr
    assert base.is_dir() and base_sentinel.exists(), "the base or its contents were deleted"
    assert parent_sentinel.exists()


# ---------------------------------------------------------------------------
# (D) genuine orphaned residue directly under the base is still removed
# ---------------------------------------------------------------------------


def test_orphaned_residue_directly_under_base_is_removed_and_worktree_created(tmp_path: pathlib.Path) -> None:
    git = _git_path()
    dev_tree, base, parent_sentinel = _plant(tmp_path)
    _init_dev_tree(git, dev_tree)
    task_id = "CODER-20260101-002-stale-residue"
    residue = base / task_id
    residue.mkdir()
    (residue / "junk").write_text("left by an interrupted run\n", encoding="utf-8")
    (residue / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")

    result = _run(_script_header(tmp_path, base) + _create_call(task_id, dev_tree), tmp_path)

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _REFUSAL_KEY not in result.stderr
    assert "orphaned residue removed" in result.stderr
    assert not (residue / "junk").exists(), "the stale residue was not removed"
    assert (residue / "README.md").exists(), "the worktree was not created at the task's path"
    registered = subprocess.run(
        [git, "-C", str(dev_tree), "worktree", "list", "--porcelain"], capture_output=True, text=True, timeout=30
    ).stdout
    assert str(residue) in registered, f"expected {residue} registered as a worktree; got:\n{registered}"
    assert parent_sentinel.exists()


# ---------------------------------------------------------------------------
# (E) the predicate over its canonical cases
# ---------------------------------------------------------------------------


def test_strictly_inside_predicate_accepts_only_strict_descendants_of_canonical_base(tmp_path: pathlib.Path) -> None:
    _, base, _ = _plant(tmp_path)
    inside = base / "CODER-20260101-003-inside"
    inside.mkdir()
    sibling_with_prefix = base.parent / "worktrees-sibling"
    sibling_with_prefix.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = base / "escape-link"
    link.symlink_to(outside, target_is_directory=True)
    link_inside = base / "inside-link"
    link_inside.symlink_to(inside, target_is_directory=True)

    cases = {
        "inside": (inside, 0),
        "inside-via-symlink": (link_inside, 0),
        "base": (base, 1),
        "base-dot": (base / ".", 1),
        "parent": (base / "..", 1),
        "sibling-with-prefix": (sibling_with_prefix, 1),
        "escape-symlink": (link, 1),
        "nonexistent": (base / "CODER-20260101-004-missing", 1),
        "base-dot-dot-inside": (base / ".." / "worktrees" / "CODER-20260101-003-inside", 0),
    }
    script = _script_header(tmp_path, base)
    for name, (candidate, _) in cases.items():
        script += textwrap.dedent(f"""\
            pgai_worktree_path_strictly_inside "{candidate}" "{base}"
            printf '{name}=%s\\n' "$?"
        """)
    result = _run(script, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    verdicts = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    for name, (_, expected) in cases.items():
        assert verdicts.get(name) == str(expected), f"{name}: expected exit {expected}, got {verdicts.get(name)}\nstdout={result.stdout}"


# ---------------------------------------------------------------------------
# (F) no rm -rf in worktree.sh is ungated
# ---------------------------------------------------------------------------


def test_every_recursive_delete_in_worktree_lib_is_gated_on_the_predicate() -> None:
    lines = _WORKTREE_LIB.read_text(encoding="utf-8").splitlines()
    delete_sites = [
        i for i, line in enumerate(lines)
        if re.search(r"\brm\s+-rf\b", line) and not line.lstrip().startswith("#") and not line.lstrip().startswith("echo")
    ]
    assert delete_sites, "expected at least the orphaned-residue delete site in worktree.sh"
    for i in delete_sites:
        window = "\n".join(lines[max(0, i - 8): i + 1])
        assert re.search(r"if\s+pgai_worktree_path_strictly_inside\s", window), (
            f"worktree.sh:{i + 1}: rm -rf is not gated on pgai_worktree_path_strictly_inside within the preceding lines:\n{window}"
        )
        assert not re.search(r'==\s*"\$\{?_?\w*(root|base)\w*\}?/"\*', window), (
            f"worktree.sh:{i + 1}: a string-prefix comparison sits beside the delete; the canonicalised predicate is the only guard"
        )
