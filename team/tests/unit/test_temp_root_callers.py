"""
test_temp_root_callers.py
=========================
The package callers that place files under the framework temp root take it
from ``pgai_agent_kanban.lib.temp_root.resolve_temp_root`` and nowhere else:

  1. ``cm.unwind`` -- the RC backup directory goes under the temp root of the
     unwind's own kanban root (``ctx.kanban_root``'s kanban.cfg), which is
     created when missing.
  2. ``ops.write._detect_stale_worktree`` -- the default worktree base is
     ``<temp root>/projects/<PP_project_name>/worktrees`` (or
     ``<temp root>/worktrees`` with no project), and ``PGAI_WORKTREE_BASE``
     still outranks it.
  3. ``testing.pre_merge`` -- a run's scratch directory is made under the
     temp root, which is created when missing, and removed when the run ends.
  4. ``green_ledger.sh``'s ``green_ledger_path`` -- the ledger sits under
     ``pgai_temp_dir``'s answer, so a whitespace-only override reads as unset
     rather than becoming a cwd-relative path.

Each case points ``PGAI_AGENT_KANBAN_TEMP_DIR`` (or kanban.cfg ``[paths]``,
for the configuration tier) at a sandbox under tmp_path and asserts the path
the caller produced lies under it. Every kanban root is a throwaway
directory under tmp_path, and the live-install variables are cleared or
repointed for every case.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from pgai_agent_kanban.cm import unwind
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.testing import pre_merge
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.test_cm_unwind import _PROJECT, _VERSION, FakeGit, _seed

_TASK_ID = "CODER-20260101-001-task"
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def sandboxed_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Clear every live-install variable and the worktree inputs; point the
    kanban root at an empty sandbox so no kanban.cfg outside tmp_path is read."""
    for name in (
        "PGAI_AGENT_KANBAN_TEMP_DIR", "PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT",
        "PGAI_WORKTREE_BASE", "PP_project_name",
    ):
        monkeypatch.delenv(name, raising=False)
    empty_root = tmp_path / "empty-kanban-root"
    empty_root.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(empty_root))
    monkeypatch.setenv("KANBAN_ROOT", str(empty_root))
    return monkeypatch


def _write_paths(root: pathlib.Path, tmp_root: pathlib.Path, tmp_subdir: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        f"[paths]\ntmp_root = {tmp_root}\ntmp_subdir = {tmp_subdir}\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 1. cm.unwind: the backup directory
# ---------------------------------------------------------------------------


def _unwind_without_a_temp_root(ctx, monkeypatch: pytest.MonkeyPatch) -> unwind.UnwindOutcome:
    monkeypatch.setattr(unwind, "_git", FakeGit())
    out: list[str] = []
    err: list[str] = []
    result = unwind.unwind_rc(ctx, _PROJECT, _VERSION, out=out.append, err=err.append, confirm=True)
    assert isinstance(result, unwind.UnwindOutcome), "".join(out) + "".join(err)
    return result


def test_unwind_backup_lands_under_the_temp_dir_override(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "override-sandbox" / "not-yet-created"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))
    ctx = _seed(tmp_path)

    assert unwind._temp_root(ctx) == str(sandbox)
    assert sandbox.is_dir(), "the unwind temp root is created when missing"

    backup = pathlib.Path(_unwind_without_a_temp_root(ctx, sandboxed_env).backup_dir)
    assert backup.parent == sandbox
    assert backup.name.startswith(f"unwind-rc-{_VERSION}-backup-")


def test_unwind_reads_paths_from_its_own_kanban_root_when_no_override_is_set(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    ctx = _seed(tmp_path)
    cfg_root = tmp_path / "configured-root"
    _write_paths(ctx.kanban_root, cfg_root, "kanban-scratch")
    expected = cfg_root / "kanban-scratch"

    assert unwind._temp_root(ctx) == str(expected)
    assert expected.is_dir()
    backup = pathlib.Path(_unwind_without_a_temp_root(ctx, sandboxed_env).backup_dir)
    assert backup.parent == expected


# ---------------------------------------------------------------------------
# 2. ops.write._detect_stale_worktree: the default worktree base
# ---------------------------------------------------------------------------


def _detect(tmp_path: pathlib.Path) -> ops_write.StaleWorktreeInfo:
    dev_tree = tmp_path / "dev-tree"
    dev_tree.mkdir(exist_ok=True)
    return ops_write._detect_stale_worktree(dev_tree, _TASK_ID, "ai_")


def test_stale_worktree_default_base_is_the_project_subtree_of_the_temp_dir_override(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "override-sandbox"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))
    sandboxed_env.setenv("PP_project_name", "demo-project")
    expected = sandbox / "projects" / "demo-project" / "worktrees" / _TASK_ID

    info = _detect(tmp_path)
    assert info.worktree_path == str(expected)
    assert info.path_exists is False

    expected.mkdir(parents=True)
    assert _detect(tmp_path).path_exists is True


def test_stale_worktree_default_base_without_a_project_is_the_temp_dir_worktrees(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "override-sandbox"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))

    assert _detect(tmp_path).worktree_path == str(sandbox / "worktrees" / _TASK_ID)


def test_stale_worktree_default_base_honours_configured_paths(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    kanban_root = tmp_path / "configured-kanban"
    cfg_root = tmp_path / "configured-root"
    _write_paths(kanban_root, cfg_root, "kanban-scratch")
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))
    sandboxed_env.setenv("PP_project_name", "demo-project")

    info = _detect(tmp_path)
    assert info.worktree_path == str(cfg_root / "kanban-scratch" / "projects" / "demo-project" / "worktrees" / _TASK_ID)


def test_worktree_base_variable_still_outranks_the_temp_root(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "override-sandbox"))
    sandboxed_env.setenv("PP_project_name", "demo-project")
    explicit_base = tmp_path / "explicit-worktree-base"
    sandboxed_env.setenv("PGAI_WORKTREE_BASE", str(explicit_base))

    assert _detect(tmp_path).worktree_path == str(explicit_base / _TASK_ID)


# ---------------------------------------------------------------------------
# 3. testing.pre_merge: the run's scratch directory
# ---------------------------------------------------------------------------


def test_pre_merge_scratch_root_is_the_temp_dir_override_created(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    sandbox = tmp_path / "override-sandbox" / "not-yet-created"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))

    assert pre_merge._scratch_root() == str(sandbox)
    assert sandbox.is_dir()


def test_pre_merge_scratch_root_without_an_override_is_the_configured_root(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    kanban_root = tmp_path / "configured-kanban"
    cfg_root = tmp_path / "configured-root"
    _write_paths(kanban_root, cfg_root, "kanban-scratch")
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))

    assert pre_merge._scratch_root() == str(cfg_root / "kanban-scratch")
    assert (cfg_root / "kanban-scratch").is_dir()


def test_a_pre_merge_run_makes_and_removes_its_scratch_dir_under_the_temp_dir(
    tmp_path: pathlib.Path, sandboxed_env: pytest.MonkeyPatch
) -> None:
    require_binary("git", label="pre-merge scratch directory")
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "root")):
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True,
        )
    sandbox = tmp_path / "override-sandbox"
    sandboxed_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox))
    seen: list[pathlib.Path] = []

    def record_scratch(team_dir, names, *, scratch, **_kwargs) -> pre_merge.CheckResult:
        seen.append(scratch)
        assert scratch.is_dir(), "the scratch directory exists while the checks run"
        return pre_merge.CheckResult(name="affected tests", passed=True)

    sandboxed_env.setattr(pre_merge, "check_affected_tests", record_scratch)
    run = pre_merge.run_pre_merge(repo, suite="integration", base="main", narrate=lambda _line: None)

    assert not run.failed
    assert len(seen) == 1
    assert seen[0].parent == sandbox and seen[0].name.startswith("pre-merge-")
    assert not seen[0].exists(), "the scratch directory is removed when the run ends"


# ---------------------------------------------------------------------------
# 4. green_ledger.sh: green_ledger_path
# ---------------------------------------------------------------------------

_PRINT_LEDGER_AND_TEMP_ROOT = (
    'source "$1"; printf "%s\\n" "$(green_ledger_path)"; pgai_temp_dir'
)


def test_green_ledger_path_with_a_whitespace_only_override_is_under_pgai_temp_dir(
    tmp_path: pathlib.Path,
) -> None:
    require_binary("bash", label="green_ledger_path")
    kanban_root = tmp_path / "configured-kanban"
    cfg_root = tmp_path / "configured-root"
    _write_paths(kanban_root, cfg_root, "kanban-scratch")
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "KANBAN_ROOT": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": "   ",
            "PGAI_PROJECT_NAME": "x",
        },
    )
    result = subprocess.run(
        ["bash", "-c", _PRINT_LEDGER_AND_TEMP_ROOT, "bash",
         str(_TEAM_DIR / "scripts" / "lib" / "green_ledger.sh")],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    ledger, temp_root = result.stdout.splitlines()
    assert temp_root == str(cfg_root / "kanban-scratch")
    assert ledger == f"{temp_root}/projects/x/green-ledger/no-active-rc/ledger.jsonl"
