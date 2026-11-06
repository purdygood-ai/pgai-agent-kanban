"""
test_reset_force.py
===================
Integration tests for the reset --force / stale-worktree behaviour.

These tests cover the four reset --force stale-worktree acceptance fixtures:

  stale-bare-warn       — bare reset (no --force) when a stale worktree
                          registration or on-disk path exists: warns to stderr
                          with a removal recipe and exits non-zero (code 2).
                          No task state or backlog marker changes.

  stale-force-clears    — reset --force when a stale worktree exists: performs
                          cleanup narrated to stderr, worktree path is gone
                          after the call, backlog marker flipped to [ ], and
                          status.md regenerated to BACKLOG (exit 0).

  clean-force-noop      — reset --force on a task with NO stale worktree is
                          identical to a bare reset: exit 0, status.md BACKLOG,
                          backlog marker flipped.  --force is a silent no-op
                          when no stale state exists.

  mount-pinned-fails-loud — reset --force when the on-disk worktree path is a
                            mount target: exit code 4, the blocking mount name
                            appears in stderr, and the task state is unchanged
                            (no partial reset).  The mount table is answered by
                            a ``findmnt`` stub first on PATH that reports the
                            worktree path as a mount point, so the test runs
                            unprivileged; nothing is mounted.

  unmerged-branch-kept  — a reset of a task whose feature branch holds commits
                          no other branch has keeps the branch, names its tip
                          and commit count, keeps logs/progress.log and writes
                          ``## Resume Mode`` ``branch``; the next dispatch's
                          worktree stage (the real wd_setup_task_worktree)
                          attaches the branch at that tip.  --discard-branch
                          deletes it instead ("discarding N commits at
                          <hash>"); a fully merged branch is deleted either
                          way; --force keeps an unmerged branch its stale
                          worktree pinned.

Design notes:
  - All four tests extend the two_project_root fixture with a real git dev-tree
    (a minimal init repository under tmp_path) so that _detect_stale_worktree
    can enter its detection block.  The two_project_root fixture's project.cfg
    uses dev_tree_path = /dev/null, which is not a directory; these tests
    override project.cfg to point at a real git repository.
  - For the stale-bare-warn test, worktree state is seeded by manually creating
    the .git/worktrees/<task_id>/ registration directory — sufficient for
    detection (the warn path never calls git).
  - For the stale-force-clears test, a REAL git worktree is created via
    ``git worktree add`` so that ``git worktree remove --force`` succeeds.
  - The expected on-disk worktree path is controlled via the PGAI_WORKTREE_BASE
    env var, passed as extra_env to _run_operator.  This lets the test place
    the stale directory at a known path without relying on the default
    temp-root resolution logic.
  - No bare /tmp paths — all scratch is under pytest's tmp_path.
  - Test names describe behaviour; no bug IDs, version numbers, or scaffolding
    labels appear in function names (roles/TESTER.md Anti-pattern 6).
  - Each test is fully self-contained and isolated; no order dependence.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Re-use the private helpers from the sibling operator-commands test module.
# The helpers (_run_operator, _build_task_folder, _read_status_state,
# _read_backlog_marker) are stable integration test utilities — importing them
# here avoids duplication without changing public API surface.
# ---------------------------------------------------------------------------
from tests.integration.test_operator_commands import (
    _RESET_SCRIPT,
    _TEAM_DIR,
    _build_task_folder,
    _operator_env,
    _read_backlog_marker,
    _read_status_state,
    _run_operator,
)
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="reset-force")


# ---------------------------------------------------------------------------
# Shared fixture-builder helpers
# ---------------------------------------------------------------------------


def _init_git_dev_tree(parent: pathlib.Path, name: str = "dev_tree") -> pathlib.Path:
    """Create a minimal git repository that stands in for the project dev tree.

    The repository must have at least one commit so that ``git worktree add``
    and branch operations succeed.

    Args:
        parent:  Parent directory under which to create the git repo.
        name:    Subdirectory name for the repo.

    Returns:
        pathlib.Path — the root of the newly initialised git repository.
    """
    dev_tree = parent / name
    dev_tree.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", str(dev_tree)],
        check=True,
        capture_output=True,
    )
    (dev_tree / "README").write_text("init\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(dev_tree), "add", "README"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(dev_tree),
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "--quiet",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    return dev_tree


def _seed_stale_registration_only(
    dev_tree: pathlib.Path,
    task_id: str,
    feature_branch: str,
    worktree_path: pathlib.Path,
) -> None:
    """Manually seed a git worktree registration without creating a real worktree.

    Creates <dev_tree>/.git/worktrees/<task_id>/ with a HEAD file referencing
    feature_branch.  This is sufficient to make ``_detect_stale_worktree``
    report ``registration_exists=True`` — the detection code only checks for
    the directory's presence and reads HEAD; it does not run git commands.

    Use this helper for the bare-reset-warns tests where the warning path
    never calls ``git worktree remove`` (so git does not need a real worktree).

    For tests that exercise the --force cleanup path (which calls
    ``git worktree remove --force``), use _create_real_worktree instead.

    Args:
        dev_tree:       Root of the git repository.
        task_id:        Task folder basename (used as the registration subdir name).
        feature_branch: Name of the feature branch the HEAD should reference.
        worktree_path:  On-disk worktree path (written to gitdir file for
                        completeness; not read by the detection code).
    """
    reg_dir = dev_tree / ".git" / "worktrees" / task_id
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "HEAD").write_text(
        f"ref: refs/heads/{feature_branch}\n",
        encoding="utf-8",
    )
    (reg_dir / "gitdir").write_text(
        str(worktree_path / ".git") + "\n",
        encoding="utf-8",
    )
    (reg_dir / "commondir").write_text(
        "../..\n",
        encoding="utf-8",
    )


def _create_real_worktree(
    dev_tree: pathlib.Path,
    branch_name: str,
    worktree_path: pathlib.Path,
) -> None:
    """Create a real git worktree at worktree_path via ``git worktree add``.

    After this call, ``git worktree list`` in dev_tree will show worktree_path,
    and ``git worktree remove --force`` can safely remove it.

    The parent directory of worktree_path is created automatically.  The
    worktree_path itself must NOT exist — git creates it.

    Args:
        dev_tree:       Root of the git repository.
        branch_name:    Name for the new branch in the worktree.
        worktree_path:  Absolute path where git should create the worktree.
    """
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(dev_tree),
            "worktree",
            "add",
            "--quiet",
            "-b",
            branch_name,
            str(worktree_path),
        ],
        check=True,
        capture_output=True,
    )


def _patch_project_cfg_dev_tree(
    project_dir: pathlib.Path,
    dev_tree_path: pathlib.Path,
) -> None:
    """Update project.cfg to point dev_tree_path at a real git repository.

    The two_project_root fixture writes ``dev_tree_path = /dev/null`` into
    project.cfg.  Replacing this with a real git directory allows the
    stale-worktree detection block in reset_item to fire.

    Args:
        project_dir:   The project directory (contains project.cfg).
        dev_tree_path: Absolute path to the git repository to use as dev tree.
    """
    cfg_path = project_dir / "project.cfg"
    text = cfg_path.read_text(encoding="utf-8")
    updated = re.sub(
        r"(dev_tree_path\s*=\s*).*",
        lambda m: m.group(1) + str(dev_tree_path),
        text,
    )
    cfg_path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResetForceWorktreeCleanup:
    """reset.sh --force stale-worktree cleanup fixtures."""

    # -----------------------------------------------------------------------
    # Fixture 1: stale-bare-warn
    # -----------------------------------------------------------------------

    def test_bare_reset_warns_and_exits_nonzero_when_stale_worktree_exists(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """Bare reset exits 2 and prints a removal recipe to stderr when a stale worktree exists.

        Without --force, reset.sh must detect a stale worktree (both the git
        registration and an on-disk path), print a human-readable recipe to
        stderr naming the cleanup commands, and refuse with exit code 2.
        The task's status.md and backlog marker must be completely unchanged.
        """
        root = two_project_root
        task_id = "CODER-20260722-T100-stale-bare-warn"
        task_dir = _build_task_folder(root, "project-a", task_id, state="DONE")

        dev_tree = _init_git_dev_tree(tmp_path, "dev_tree_bare_warn")

        # Create the on-disk worktree path so path_exists=True.
        wt_base = tmp_path / "wt_base_bare_warn"
        wt_path = wt_base / task_id
        wt_path.mkdir(parents=True, exist_ok=True)

        # Seed registration-only (warn path never calls git worktree remove).
        _seed_stale_registration_only(
            dev_tree,
            task_id,
            feature_branch=f"feature/{task_id}",
            worktree_path=wt_path,
        )

        _patch_project_cfg_dev_tree(
            root / "projects" / "project-a",
            dev_tree,
        )

        status_before = (task_dir / "status.md").read_text(encoding="utf-8")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        # Must refuse with exit code 2 (stale-worktree refusal).
        assert result.returncode == 2, (
            f"Expected exit code 2 (stale-worktree refusal) but got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Removal recipe must appear on stderr.
        assert "git worktree" in result.stderr.lower() or "stale" in result.stderr.lower(), (
            f"Expected a stale-worktree warning with removal recipe on stderr.\n" f"stderr: {result.stderr}"
        )

        # Task state must be unchanged — no reset was performed.
        status_after = (task_dir / "status.md").read_text(encoding="utf-8")
        assert status_after == status_before, (
            "Bare reset (no --force) must not modify status.md when a stale worktree is present.\n"
            f"Before:\n{status_before}\nAfter:\n{status_after}"
        )

        # On-disk worktree path must still exist — no cleanup occurred.
        assert wt_path.is_dir(), "Bare reset (no --force) must not remove the stale on-disk worktree path."

    # -----------------------------------------------------------------------
    # Fixture 2: stale-force-clears
    # -----------------------------------------------------------------------

    def test_force_reset_clears_stale_worktree_and_completes_reset(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """reset --force clears stale worktree, narrates to stderr, and completes the reset.

        With --force, reset.sh must: narrate the cleanup steps to stderr, remove
        the stale on-disk worktree path, complete the normal reset (status.md
        regenerated to BACKLOG, backlog marker flipped to [ ]), and exit 0.

        A real git worktree (via git worktree add) is used so that
        ``git worktree remove --force`` succeeds during cleanup.
        """
        root = two_project_root
        task_id = "CODER-20260722-T101-stale-force-clears"
        _build_task_folder(root, "project-a", task_id, state="DONE")

        # Pre-condition: flip backlog marker to [x] to simulate a prior close.
        backlog_file = root / "projects" / "project-a" / "tasks" / "queues" / "coder_backlog.md"
        if backlog_file.exists():
            text = backlog_file.read_text(encoding="utf-8")
            backlog_file.write_text(
                text.replace(f"- [ ] {task_id}", f"- [x] {task_id}"),
                encoding="utf-8",
            )

        dev_tree = _init_git_dev_tree(tmp_path, "dev_tree_force_clears")

        # The PGAI_WORKTREE_BASE controls the expected on-disk worktree path.
        # wt_path must NOT exist before _create_real_worktree (git creates it).
        wt_base = tmp_path / "wt_base_force_clears"
        wt_base.mkdir(parents=True, exist_ok=True)
        wt_path = wt_base / task_id

        # Create a real git worktree so git worktree remove --force can clean it.
        _create_real_worktree(
            dev_tree,
            branch_name=f"feature/{task_id}",
            worktree_path=wt_path,
        )

        _patch_project_cfg_dev_tree(
            root / "projects" / "project-a",
            dev_tree,
        )

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id, "--force"],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        # Must succeed.
        assert (
            result.returncode == 0
        ), f"reset --force exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"

        # Cleanup narration must appear on stderr.
        assert (
            "worktree cleanup" in result.stderr.lower() or "stale-worktree" in result.stderr.lower()
        ), f"Expected worktree cleanup narration on stderr.\nstderr: {result.stderr}"

        # Stale on-disk path must be gone.
        assert not wt_path.is_dir(), (
            f"Expected stale worktree path {wt_path} to be removed after --force, " "but it still exists."
        )

        # status.md must be regenerated to BACKLOG.
        task_dir = root / "projects" / "project-a" / "tasks" / task_id
        assert _read_status_state(task_dir) == "BACKLOG", (
            f"Expected status.md ## State to be BACKLOG after reset --force.\n"
            f"status.md:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )

        # Backlog marker must be flipped to open.
        marker = _read_backlog_marker(root, "project-a", task_id)
        assert marker == "" or marker == " ", (
            f"Expected backlog marker to be open (space or empty) after reset --force; "
            f"got {marker!r}.\n"
            f"coder_backlog.md:\n{backlog_file.read_text(encoding='utf-8')}"
        )

    # -----------------------------------------------------------------------
    # Fixture 3: clean-force-noop
    # -----------------------------------------------------------------------

    def test_force_reset_on_clean_task_behaves_identically_to_bare_reset(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """reset --force on a task with no stale worktree is identical to a bare reset.

        When no stale worktree registration or on-disk path exists, --force is a
        silent no-op for the cleanup phase: exit 0, status.md regenerated to
        BACKLOG, backlog marker flipped to [ ].  Behavior is indistinguishable
        from a bare reset on the same clean task.
        """
        root = two_project_root

        # Two tasks — one reset with --force, one without — both should land in BACKLOG.
        task_id_force = "CODER-20260722-T102-clean-force-result"
        task_id_bare = "CODER-20260722-T102-clean-bare-result"
        task_dir_force = _build_task_folder(root, "project-a", task_id_force, state="DONE")
        task_dir_bare = _build_task_folder(root, "project-a", task_id_bare, state="DONE")

        dev_tree = _init_git_dev_tree(tmp_path, "dev_tree_clean_force")

        # wt_base exists but contains no task_id subdirectories — detection finds nothing.
        wt_base = tmp_path / "wt_base_clean_force"
        wt_base.mkdir(parents=True, exist_ok=True)

        _patch_project_cfg_dev_tree(
            root / "projects" / "project-a",
            dev_tree,
        )

        result_force = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id_force, "--force"],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        result_bare = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id_bare],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        # Both must succeed.
        assert result_force.returncode == 0, (
            f"reset --force on clean task exited non-zero.\n"
            f"stdout: {result_force.stdout}\nstderr: {result_force.stderr}"
        )
        assert result_bare.returncode == 0, (
            f"Bare reset on clean task exited non-zero.\n" f"stdout: {result_bare.stdout}\nstderr: {result_bare.stderr}"
        )

        # Both tasks must be regenerated to BACKLOG.
        assert _read_status_state(task_dir_force) == "BACKLOG", (
            f"Expected --force task to be BACKLOG after clean reset.\n"
            f"status.md:\n{(task_dir_force / 'status.md').read_text(encoding='utf-8')}"
        )
        assert _read_status_state(task_dir_bare) == "BACKLOG", (
            f"Expected bare task to be BACKLOG after clean reset.\n"
            f"status.md:\n{(task_dir_bare / 'status.md').read_text(encoding='utf-8')}"
        )

        # Both backlog markers must be open.
        marker_force = _read_backlog_marker(root, "project-a", task_id_force)
        marker_bare = _read_backlog_marker(root, "project-a", task_id_bare)
        assert (
            marker_force == "" or marker_force == " "
        ), f"Expected --force task backlog marker to be open; got {marker_force!r}."
        assert (
            marker_bare == "" or marker_bare == " "
        ), f"Expected bare task backlog marker to be open; got {marker_bare!r}."

    # -----------------------------------------------------------------------
    # Fixture 4: mount-pinned-fails-loud
    # -----------------------------------------------------------------------

    def test_force_reset_aborts_loud_when_worktree_path_is_mount_target(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """reset --force exits 4 and names the blocking mount when the worktree path is pinned.

        When the on-disk worktree path is an active mount target, --force must
        abort before any cleanup, print the blocking mount name to stderr, and
        leave the task state, the backlog marker and the worktree directory
        completely unchanged (no partial reset).

        The mount probe asks ``findmnt --mountpoint <path>``; a stub first on
        PATH answers it, reporting the worktree path (and only it) as a mount
        point, so no privilege and no real mount is needed.
        """
        root = two_project_root
        task_id = "CODER-20260722-T103-mount-pinned-fails"
        task_dir = _build_task_folder(root, "project-a", task_id, state="DONE")

        dev_tree = _init_git_dev_tree(tmp_path, "dev_tree_mount_pinned")

        wt_base = tmp_path / "wt_base_mount_pinned"
        wt_path = wt_base / task_id
        wt_path.mkdir(parents=True, exist_ok=True)
        (wt_path / "sentinel").write_text("pinned\n", encoding="utf-8")

        stub_bin = tmp_path / "stub-bin"
        stub_bin.mkdir()
        findmnt = stub_bin / "findmnt"
        findmnt.write_text(
            "#!/usr/bin/env bash\n"
            "# Test stand-in for findmnt: one path is a mount point, nothing else is.\n"
            "while [[ $# -gt 0 ]]; do\n"
            '  if [[ "$1" == --mountpoint && "$2" == "$PINNED_PATH" ]]; then\n'
            '    echo "stubdev $PINNED_PATH tmpfs"; exit 0\n'
            "  fi\n"
            "  shift\n"
            "done\n"
            "exit 1\n",
            encoding="utf-8",
        )
        findmnt.chmod(0o755)

        _seed_stale_registration_only(
            dev_tree,
            task_id,
            feature_branch=f"feature/{task_id}",
            worktree_path=wt_path,
        )
        _patch_project_cfg_dev_tree(root / "projects" / "project-a", dev_tree)

        status_before = (task_dir / "status.md").read_text(encoding="utf-8")
        marker_before = _read_backlog_marker(root, "project-a", task_id)

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id, "--force"],
            root,
            extra_env={
                "PGAI_WORKTREE_BASE": str(wt_base),
                "PINNED_PATH": str(wt_path),
                "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            },
        )

        assert result.returncode == 4, (
            f"Expected exit code 4 (mount-pinned abort) but got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert f"Blocking mount: stubdev {wt_path} tmpfs" in result.stderr, result.stderr
        assert (task_dir / "status.md").read_text(
            encoding="utf-8"
        ) == status_before, "reset --force must not modify status.md when aborted by a mount pin."
        assert _read_backlog_marker(root, "project-a", task_id) == marker_before
        assert (wt_path / "sentinel").is_file(), "the pinned worktree directory was cleaned up"


# ---------------------------------------------------------------------------
# The default worktree base, end to end through reset.sh
# ---------------------------------------------------------------------------


_ON_DISK_PATH_RE = re.compile(r"On-disk path:\s+(\S+)")


class TestResetStaleWorktreeDefaultBase:
    """With PGAI_WORKTREE_BASE unset, reset.sh looks for the stale worktree
    under the framework temp root: the PGAI_AGENT_KANBAN_TEMP_DIR override
    when set, else kanban.cfg [paths] tmp_root/tmp_subdir."""

    def _refused_path(
        self,
        root: pathlib.Path,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        extra_env: dict,
    ) -> pathlib.Path:
        """Seed a stale registration, run a bare reset, and return the
        on-disk worktree path the refusal names."""
        task_id = "CODER-20260722-T104-default-base"
        _build_task_folder(root, "project-a", task_id, state="DONE")
        dev_tree = _init_git_dev_tree(tmp_path, "dev_tree_default_base")
        _seed_stale_registration_only(
            dev_tree,
            task_id,
            feature_branch=f"feature/{task_id}",
            worktree_path=tmp_path / "registered-elsewhere" / task_id,
        )
        _patch_project_cfg_dev_tree(root / "projects" / "project-a", dev_tree)
        for name in (
            "PGAI_WORKTREE_BASE",
            "PGAI_AGENT_KANBAN_TEMP_DIR",
            "PGAI_TASKS_DIR",
            "PGAI_PROJECT_ROOT",
            "PP_project_name",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("KANBAN_ROOT", str(root))

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id],
            root,
            extra_env=extra_env,
        )

        assert result.returncode == 2, (
            f"Expected the stale-worktree refusal (exit 2), got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        match = _ON_DISK_PATH_RE.search(result.stderr)
        assert match, f"The refusal names no on-disk path.\nstderr: {result.stderr}"
        named = pathlib.Path(match.group(1))
        assert named.name == task_id and named.parent.name == "worktrees", named
        return named

    def test_default_base_is_under_the_temp_dir_override(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sandbox = tmp_path / "temp-dir-override"
        named = self._refused_path(
            two_project_root,
            tmp_path,
            monkeypatch,
            {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)},
        )
        assert sandbox in named.parents, f"{named} is not under the override {sandbox}"

    def test_default_base_is_under_configured_paths_without_an_override(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        configured = tmp_path / "configured-root"
        cfg = two_project_root / "kanban.cfg"
        cfg.write_text(
            cfg.read_text(encoding="utf-8").replace(
                "[paths]\n",
                f"[paths]\ntmp_root = {configured}\ntmp_subdir = kanban-scratch\n",
                1,
            ),
            encoding="utf-8",
        )
        named = self._refused_path(two_project_root, tmp_path, monkeypatch, {})
        expected_root = configured / "kanban-scratch"
        assert expected_root in named.parents, f"{named} is not under the configured root {expected_root}"


# ---------------------------------------------------------------------------
# A feature branch holding unmerged commits survives a default reset
# ---------------------------------------------------------------------------


_RC_BRANCH = "rc/v9.9.9"
_LIB = _TEAM_DIR / "scripts" / "lib"


def _git_out(repo: pathlib.Path, *args: str) -> str:
    """Run git in *repo* and return its stripped stdout (raises on failure)."""
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=test@test.com", "-c", "user.name=Test", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _section(text: str, heading: str) -> str | None:
    """Body of ``## <heading>`` stripped, or None when the heading is absent."""
    m = re.search(rf"^##\s+{re.escape(heading)}[ \t]*\n((?:(?!##).*\n?)*)", text, re.M)
    return m.group(1).strip() if m else None


def _dev_tree_with_feature_work(
    tmp_path: pathlib.Path,
    feature_branch: str,
    commits: int,
    *,
    merged_into: str = "",
) -> pathlib.Path:
    """A dev tree parked on ``main`` with the RC branch and *feature_branch*
    *commits* commits ahead of it, committed through a temporary worktree
    that is removed again (no stale worktree is left behind).

    *merged_into* ``"rc"`` merges the feature branch into the RC branch
    (``git branch -d`` still refuses it: the dev tree's HEAD is ``main``);
    ``"head"`` merges it into ``main`` as well."""
    dev_tree = _init_git_dev_tree(tmp_path, "dev_tree")
    _git_out(dev_tree, "branch", _RC_BRANCH)
    _git_out(dev_tree, "branch", feature_branch, _RC_BRANCH)
    scratch = tmp_path / "scratch-wt"
    _git_out(dev_tree, "worktree", "add", "-q", str(scratch), feature_branch)
    for n in range(commits):
        (scratch / f"work{n}.txt").write_text(f"{n}\n", encoding="utf-8")
        _git_out(scratch, "add", f"work{n}.txt")
        _git_out(scratch, "commit", "-q", "-m", f"work {n}")
    if merged_into:
        _git_out(scratch, "checkout", "-q", _RC_BRANCH)
        _git_out(scratch, "merge", "-q", "--no-ff", "-m", "merge", feature_branch)
        if merged_into == "head":
            _git_out(dev_tree, "merge", "-q", "--no-ff", "-m", "merge", _RC_BRANCH)
    _git_out(dev_tree, "worktree", "remove", "--force", str(scratch))
    return dev_tree


def _task_with_progress(root: pathlib.Path, task_id: str) -> pathlib.Path:
    """A BLOCKED agent task with a progress log, a second log file, and a
    README naming the RC branch as its source branch."""
    task_dir = _build_task_folder(root, "project-a", task_id, state="BLOCKED")
    readme = task_dir / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + f"\n## Workflow Type\nrelease\n\n## Source Branch\n{_RC_BRANCH}\n",
        encoding="utf-8",
    )
    (task_dir / "logs" / "progress.log").write_text(
        "2026-01-01T00:00:00+00:00 Edit 1 of 2 committed\n", encoding="utf-8"
    )
    (task_dir / "logs" / "dispatch.jsonl").write_text("{}\n", encoding="utf-8")
    return task_dir


def _branch_exists(dev_tree: pathlib.Path, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(dev_tree), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
        ).returncode
        == 0
    )


def _run_worktree_stage(
    root: pathlib.Path,
    task_dir: pathlib.Path,
    dev_tree: pathlib.Path,
    wt_base: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run the next dispatch's worktree stage: the real wd_setup_task_worktree
    with the real create_task_worktree, Block Record reader and
    ``## Resume Mode`` reader.  Only collaborators outside the sandbox are
    stubbed; TEAM_ROOT under tmp_path makes the stage skip dev-tree parking."""
    task_id = task_dir.name
    script = f"""\
set -uo pipefail
cd '{_TEAM_DIR}'
source '{_LIB}/temp.sh'
source '{_LIB}/worktree.sh'
set -e
export LOG_FILE='{root}/wake.log'
log() {{ echo "[test] wake: $*" | tee -a "$LOG_FILE" >&2; }}
source '{_LIB}/pp_run_ops.sh'
source '{_LIB}/block_record.sh'
source '{_LIB}/wake_driver_task_setup.sh'
source '{_LIB}/wake_driver_worktree.sh'
task_id='{task_id}'; task_readme='{task_dir}/README.md'; task_status='{task_dir}/status.md'
TEAM_ROOT='{root}'; KANBAN_ROOT='{root}'; LAST_TASK_FINAL_STATE=""
SUBAGENT=coder; role=CODER; _CURRENT_PROJECT=project-a
PP_dev_tree_path='{dev_tree}'
_task_feature_branch=""; _task_worktree_path=""; _task_workflow_type=""
_task_source_branch=""; _canonical_dev_tree_path='{dev_tree}'; _park_resting_branch=""; role_reminder=""
pp_prefix_branch() {{ echo "$2"; }}
_update_readme_feature_branch() {{ :; }}
wf_load_plugin() {{ return 0; }}
wf_git_mode() {{ echo branch; }}
set_state() {{ echo "UNEXPECTED set_state $*" >&2; }}
set_block() {{ :; }}; normalize_status_file() {{ :; }}; mark_backlog() {{ :; }}
wd_setup_task_worktree
printf 'FINAL=[%s]\\nPATH_OUT=[%s]\\n' "$LAST_TASK_FINAL_STATE" "$_task_worktree_path"
"""
    env = _operator_env(
        root,
        {
            "PGAI_WORKTREE_BASE": str(wt_base),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120, env=env)


class TestResetKeepsUnmergedFeatureBranch:
    """A reset keeps committed work on the feature branch unless told to discard it."""

    def test_default_reset_keeps_the_branch_and_the_next_worktree_attaches_its_tip(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """commit -> reset -> worktree stage -> worktree on the old tip."""
        root = two_project_root
        task_id = "CODER-20260722-T110-keeps-unmerged"
        feature_branch = f"feature/{task_id}"
        task_dir = _task_with_progress(root, task_id)
        dev_tree = _dev_tree_with_feature_work(tmp_path, feature_branch, commits=2)
        _patch_project_cfg_dev_tree(root / "projects" / "project-a", dev_tree)
        wt_base = tmp_path / "wt_base"
        wt_base.mkdir()
        tip = _git_out(dev_tree, "rev-parse", f"refs/heads/{feature_branch}")
        short_tip = _git_out(dev_tree, "rev-parse", "--short", f"refs/heads/{feature_branch}")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert f"keeping feature branch '{feature_branch}'" in result.stderr, result.stderr
        assert f"2 commits at {short_tip}" in result.stderr, result.stderr
        assert "--discard-branch" in result.stderr, result.stderr
        assert _git_out(dev_tree, "rev-parse", f"refs/heads/{feature_branch}") == tip, "the branch moved or vanished"

        status = (task_dir / "status.md").read_text(encoding="utf-8")
        assert _section(status, "State") == "BACKLOG", status
        assert _section(status, "Resume Mode") == "branch", status
        progress = (task_dir / "logs" / "progress.log").read_text(encoding="utf-8")
        assert "Edit 1 of 2 committed" in progress, "the earlier progress lines were lost"
        assert f"kept at {short_tip}" in progress, progress
        assert not (task_dir / "logs" / "dispatch.jsonl").exists(), "other task logs are still cleared"

        stage = _run_worktree_stage(root, task_dir, dev_tree, wt_base)

        worktree = wt_base / task_id
        assert (
            stage.returncode == 0 and "FINAL=[]" in stage.stdout
        ), f"rc={stage.returncode}\nstdout={stage.stdout}\nstderr={stage.stderr}"
        assert f"PATH_OUT=[{worktree}]" in stage.stdout, stage.stdout
        assert "## Resume Mode: branch" in stage.stderr, stage.stderr
        assert _git_out(worktree, "rev-parse", "HEAD") == tip, "the worktree is not on the kept tip"
        assert _git_out(worktree, "rev-parse", "--abbrev-ref", "HEAD") == feature_branch

    def test_discard_branch_deletes_the_branch_naming_its_commit_count_and_tip(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        root = two_project_root
        task_id = "CODER-20260722-T111-discards-unmerged"
        feature_branch = f"feature/{task_id}"
        task_dir = _task_with_progress(root, task_id)
        dev_tree = _dev_tree_with_feature_work(tmp_path, feature_branch, commits=3)
        _patch_project_cfg_dev_tree(root / "projects" / "project-a", dev_tree)
        wt_base = tmp_path / "wt_base"
        wt_base.mkdir()
        short_tip = _git_out(dev_tree, "rev-parse", "--short", f"refs/heads/{feature_branch}")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id, "--discard-branch"],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert f"discarding 3 commits at {short_tip}" in result.stderr, result.stderr
        assert not _branch_exists(dev_tree, feature_branch), "--discard-branch left the branch in place"
        status = (task_dir / "status.md").read_text(encoding="utf-8")
        assert _section(status, "Resume Mode") is None, status
        assert list((task_dir / "logs").iterdir()) == [], "a discarding reset clears the task logs"

    @pytest.mark.parametrize("merged_into", ["head", "rc"])
    def test_fully_merged_branch_is_deleted_by_default(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
        merged_into: str,
    ) -> None:
        """A branch whose commits are all on another branch is deleted as
        before, whether or not ``git branch -d`` accepts it, and the task
        starts fresh."""
        root = two_project_root
        task_id = f"CODER-20260722-T112-merged-into-{merged_into}"
        feature_branch = f"feature/{task_id}"
        task_dir = _task_with_progress(root, task_id)
        dev_tree = _dev_tree_with_feature_work(tmp_path, feature_branch, commits=2, merged_into=merged_into)
        _patch_project_cfg_dev_tree(root / "projects" / "project-a", dev_tree)
        wt_base = tmp_path / "wt_base"
        wt_base.mkdir()

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project-a", "--key", task_id],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert not _branch_exists(dev_tree, feature_branch), result.stderr
        assert "keeping feature branch" not in result.stderr and "discarding" not in result.stderr, result.stderr
        assert _section((task_dir / "status.md").read_text(encoding="utf-8"), "Resume Mode") is None
        assert list((task_dir / "logs").iterdir()) == [], "a reset with no kept branch clears the task logs"

    @pytest.mark.parametrize("discard", [False, True], ids=["kept", "discarded"])
    def test_force_cleanup_keeps_an_unmerged_branch_its_stale_worktree_pinned(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
        discard: bool,
    ) -> None:
        """--force removes the stale worktree; the unmerged branch it had
        checked out is kept unless --discard-branch is also given."""
        root = two_project_root
        task_id = f"CODER-20260722-T113-force-{'discards' if discard else 'keeps'}"
        feature_branch = f"feature/{task_id}"
        task_dir = _task_with_progress(root, task_id)
        dev_tree = _dev_tree_with_feature_work(tmp_path, feature_branch, commits=2)
        _patch_project_cfg_dev_tree(root / "projects" / "project-a", dev_tree)
        wt_base = tmp_path / "wt_base"
        wt_path = wt_base / task_id
        wt_base.mkdir()
        _git_out(dev_tree, "worktree", "add", "-q", str(wt_path), feature_branch)
        tip = _git_out(dev_tree, "rev-parse", f"refs/heads/{feature_branch}")
        short_tip = _git_out(dev_tree, "rev-parse", "--short", f"refs/heads/{feature_branch}")

        args = ["--project", "project-a", "--key", task_id, "--force"]
        if discard:
            args.append("--discard-branch")
        result = _run_operator(_RESET_SCRIPT, args, root, extra_env={"PGAI_WORKTREE_BASE": str(wt_base)})

        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        assert not wt_path.is_dir(), "the stale worktree was not removed"
        status = (task_dir / "status.md").read_text(encoding="utf-8")
        if discard:
            assert f"discarding 2 commits at {short_tip}" in result.stderr, result.stderr
            assert not _branch_exists(dev_tree, feature_branch)
            assert _section(status, "Resume Mode") is None, status
        else:
            assert f"2 commits at {short_tip}" in result.stderr, result.stderr
            assert result.stderr.count("keeping feature branch") == 1, result.stderr
            assert _git_out(dev_tree, "rev-parse", f"refs/heads/{feature_branch}") == tip
            assert _section(status, "Resume Mode") == "branch", status
            assert (task_dir / "logs" / "progress.log").is_file()
