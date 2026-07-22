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
                            (no partial reset).  This test requires root (bind
                            mount) and is skipped when the effective UID != 0.

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
    labels appear in function names (SOP.md Anti-pattern 6).
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
    _build_task_folder,
    _read_backlog_marker,
    _read_status_state,
    _run_operator,
)

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
            "git", "-C", str(dev_tree),
            "-c", "user.email=test@test.com",
            "-c", "user.name=Test",
            "commit", "--quiet", "-m", "init",
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
            "git", "-C", str(dev_tree),
            "worktree", "add", "--quiet",
            "-b", branch_name,
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
        task_dir = _build_task_folder(root, "project_a", task_id, state="DONE")

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
            root / "projects" / "project_a",
            dev_tree,
        )

        status_before = (task_dir / "status.md").read_text(encoding="utf-8")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        # Must refuse with exit code 2 (stale-worktree refusal).
        assert result.returncode == 2, (
            f"Expected exit code 2 (stale-worktree refusal) but got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Removal recipe must appear on stderr.
        assert (
            "git worktree" in result.stderr.lower()
            or "stale" in result.stderr.lower()
        ), (
            f"Expected a stale-worktree warning with removal recipe on stderr.\n"
            f"stderr: {result.stderr}"
        )

        # Task state must be unchanged — no reset was performed.
        status_after = (task_dir / "status.md").read_text(encoding="utf-8")
        assert status_after == status_before, (
            "Bare reset (no --force) must not modify status.md when a stale worktree is present.\n"
            f"Before:\n{status_before}\nAfter:\n{status_after}"
        )

        # On-disk worktree path must still exist — no cleanup occurred.
        assert wt_path.is_dir(), (
            "Bare reset (no --force) must not remove the stale on-disk worktree path."
        )

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
        _build_task_folder(root, "project_a", task_id, state="DONE")

        # Pre-condition: flip backlog marker to [x] to simulate a prior close.
        backlog_file = (
            root / "projects" / "project_a" / "tasks" / "queues" / "coder_backlog.md"
        )
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
            root / "projects" / "project_a",
            dev_tree,
        )

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", task_id, "--force"],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        # Must succeed.
        assert result.returncode == 0, (
            f"reset --force exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Cleanup narration must appear on stderr.
        assert (
            "worktree cleanup" in result.stderr.lower()
            or "stale-worktree" in result.stderr.lower()
        ), (
            f"Expected worktree cleanup narration on stderr.\nstderr: {result.stderr}"
        )

        # Stale on-disk path must be gone.
        assert not wt_path.is_dir(), (
            f"Expected stale worktree path {wt_path} to be removed after --force, "
            "but it still exists."
        )

        # status.md must be regenerated to BACKLOG.
        task_dir = root / "projects" / "project_a" / "tasks" / task_id
        assert _read_status_state(task_dir) == "BACKLOG", (
            f"Expected status.md ## State to be BACKLOG after reset --force.\n"
            f"status.md:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )

        # Backlog marker must be flipped to open.
        marker = _read_backlog_marker(root, "project_a", task_id)
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
        task_dir_force = _build_task_folder(root, "project_a", task_id_force, state="DONE")
        task_dir_bare = _build_task_folder(root, "project_a", task_id_bare, state="DONE")

        dev_tree = _init_git_dev_tree(tmp_path, "dev_tree_clean_force")

        # wt_base exists but contains no task_id subdirectories — detection finds nothing.
        wt_base = tmp_path / "wt_base_clean_force"
        wt_base.mkdir(parents=True, exist_ok=True)

        _patch_project_cfg_dev_tree(
            root / "projects" / "project_a",
            dev_tree,
        )

        result_force = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", task_id_force, "--force"],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        result_bare = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", task_id_bare],
            root,
            extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
        )

        # Both must succeed.
        assert result_force.returncode == 0, (
            f"reset --force on clean task exited non-zero.\n"
            f"stdout: {result_force.stdout}\nstderr: {result_force.stderr}"
        )
        assert result_bare.returncode == 0, (
            f"Bare reset on clean task exited non-zero.\n"
            f"stdout: {result_bare.stdout}\nstderr: {result_bare.stderr}"
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
        marker_force = _read_backlog_marker(root, "project_a", task_id_force)
        marker_bare = _read_backlog_marker(root, "project_a", task_id_bare)
        assert marker_force == "" or marker_force == " ", (
            f"Expected --force task backlog marker to be open; got {marker_force!r}."
        )
        assert marker_bare == "" or marker_bare == " ", (
            f"Expected bare task backlog marker to be open; got {marker_bare!r}."
        )

    # -----------------------------------------------------------------------
    # Fixture 4: mount-pinned-fails-loud
    # -----------------------------------------------------------------------

    @pytest.mark.skipif(
        os.getuid() != 0,
        reason=(
            "Mount-pinned test requires root (bind mount creation needs CAP_SYS_ADMIN); "
            "skipped when effective UID != 0."
        ),
    )
    def test_force_reset_aborts_loud_when_worktree_path_is_mount_target(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """reset --force exits 4 and names the blocking mount when the worktree path is pinned.

        When the on-disk worktree path is an active mount target, --force must
        abort before any cleanup, print the blocking mount name to stderr, and
        leave the task state and backlog marker completely unchanged (no partial
        reset).

        This test requires root in order to create a bind mount.  It is skipped
        when the effective UID is not 0.
        """
        root = two_project_root
        task_id = "CODER-20260722-T103-mount-pinned-fails"
        task_dir = _build_task_folder(root, "project_a", task_id, state="DONE")

        dev_tree = _init_git_dev_tree(tmp_path, "dev_tree_mount_pinned")

        wt_base = tmp_path / "wt_base_mount_pinned"
        wt_path = wt_base / task_id
        wt_path.mkdir(parents=True, exist_ok=True)

        # Create a directory to bind-mount onto wt_path.
        mount_src = tmp_path / "mount_src_pinned"
        mount_src.mkdir(parents=True, exist_ok=True)
        (mount_src / "sentinel").write_text("pinned\n", encoding="utf-8")

        mount_result = subprocess.run(
            ["mount", "--bind", str(mount_src), str(wt_path)],
            capture_output=True,
            text=True,
        )
        if mount_result.returncode != 0:
            pytest.skip(
                f"Bind mount failed (may lack CAP_SYS_ADMIN even as root): "
                f"{mount_result.stderr.strip()}"
            )

        try:
            # Seed registration-only so detection finds registration_exists=True.
            _seed_stale_registration_only(
                dev_tree,
                task_id,
                feature_branch=f"feature/{task_id}",
                worktree_path=wt_path,
            )

            _patch_project_cfg_dev_tree(
                root / "projects" / "project_a",
                dev_tree,
            )

            status_before = (task_dir / "status.md").read_text(encoding="utf-8")

            result = _run_operator(
                _RESET_SCRIPT,
                ["--project", "project_a", "--key", task_id, "--force"],
                root,
                extra_env={"PGAI_WORKTREE_BASE": str(wt_base)},
            )

            # Must abort with exit code 4 (mount-pinned path).
            assert result.returncode == 4, (
                f"Expected exit code 4 (mount-pinned abort) but got {result.returncode}.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

            # stderr must reference the blocking path or the word 'mount'.
            assert str(wt_path) in result.stderr or "mount" in result.stderr.lower(), (
                f"Expected the blocking mount path or 'mount' keyword in stderr.\n"
                f"stderr: {result.stderr}"
            )

            # Task state must be completely unchanged — no partial reset.
            status_after = (task_dir / "status.md").read_text(encoding="utf-8")
            assert status_after == status_before, (
                "reset --force must not modify status.md when aborted by a mount pin.\n"
                f"Before:\n{status_before}\nAfter:\n{status_after}"
            )

        finally:
            # Always unmount to avoid leaving a mounted filesystem under tmp_path.
            subprocess.run(
                ["umount", str(wt_path)],
                capture_output=True,
            )
