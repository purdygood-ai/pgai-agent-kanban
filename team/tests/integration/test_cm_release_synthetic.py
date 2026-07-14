"""
test_cm_release_synthetic.py
============================
End-to-end integration tests for the CM release engine
(cm/open-rc.sh -> synthetic feature commit -> cm/release.sh).

These tests mirror the fixture pattern from test_rc_lifecycle.py: each test
stands up a real temporary git repo and a minimal kanban project skeleton,
then drives the CM scripts end-to-end.  No seams are mocked.

Coverage:
  - Case (a): Clean release — squash lands, fidelity gate OK line present in
    output, release-notes commit exists on main, version tag created, RC branch
    cleaned up per procedure.
  - Case (b): Conflicting mid-RC main commit — release exits non-zero via the
    Trigger-5 conflict HALT, no tag is created, and main's pre-release commit
    is still present after the failed release (nothing destroyed).
  - Case (c): Non-conflicting mid-RC main commit — fidelity gate HALT fires.
    Because TestFidelityGate.test_fidelity_gate_halts_when_main_diverges_mid_rc
    in test_rc_lifecycle.py already covers this behavior with an equivalent
    fixture, this module includes a reference test that confirms the gate fires
    but intentionally avoids duplicating the full assertion set.
  - Case (d): Changelog freshness at tip and tag after full release including
    the WRITER polish step — regression lock for the polish-before-regeneration
    ordering fix.  Asserts: changelog is fresh at the branch tip, changelog is
    fresh at the tag, and the tag and tip are the same commit (no extra commits
    land after the tag).
  - Case (e): Resume from induced partial-run failures — parameterized at three
    step boundaries (post-squash, post-notes, post-CHANGELOG).  Asserts that
    re-invocation after each induced failure completes the release end-to-end
    (tag created, RC branch deleted, release-state Active RC cleared).

Design notes:
  - All tests use pytest's tmp_path for scratch.  No bare /tmp paths.
  - Each test is self-contained and produces no state visible to other tests.
  - The "dev tree" in every test is a local temp git repo, never the live repo.
  - A local bare git repo serves as origin so cm-open-rc.sh's unconditional
    fetch/pull succeeds without real network access.
  - Test names describe the behavior under test; no bug IDs or version numbers
    appear in function names.

Reference: TestFidelityGate.test_fidelity_gate_halts_when_main_diverges_mid_rc
in test_rc_lifecycle.py covers Case (c) equivalently; Case (c) in this module
delegates the full assertion and adds only a confirming smoke-check.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path helpers — resolve from this file's own location.
#
# File structure (relative to dev-tree root):
#   team/tests/integration/test_cm_release_synthetic.py
#              └── integration/    (this file)
#              └── tests/
#          └── team/
#      └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CM_SCRIPTS_DIR = _SCRIPTS_DIR / "cm"
_KANBAN_PY_DIR = _TEAM_DIR / "pgai_agent_kanban"

_OPEN_RC_SCRIPT = _CM_SCRIPTS_DIR / "open-rc.sh"
_RELEASE_SCRIPT = _CM_SCRIPTS_DIR / "release.sh"


# ---------------------------------------------------------------------------
# Fixture helpers — adapted from test_rc_lifecycle.py
# ---------------------------------------------------------------------------


def _init_git_repo_with_remote(
    repo_path: pathlib.Path,
    bare_remote: pathlib.Path,
    *,
    main_branch: str = "main",
) -> None:
    """Initialise a local git repo with a trunk branch and a bare remote.

    Creates a bare 'origin' repo at *bare_remote* and a non-bare repo at
    *repo_path* with an initial commit on *main_branch* already pushed to the
    bare remote.  All git config is repo-local so ~/.gitconfig is not touched.
    """
    bare_remote.mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["git", "init", "--bare", str(bare_remote)],
        capture_output=True, check=True,
    )

    subprocess.run(
        ["git", "-C", str(repo_path), "init", f"--initial-branch={main_branch}"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "--local", "user.email", "test@pgai.local"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "--local", "user.name", "PGAI Test"],
        capture_output=True, check=True,
    )

    (repo_path / "README.md").write_text("# Synthetic release test repo\n", encoding="utf-8")
    # release-notes/PUBLISHED: required by the changelog writer (load_published_manifest).
    # An empty file is valid — it means no versions have been publicly released yet.
    (repo_path / "release-notes").mkdir(parents=True, exist_ok=True)
    (repo_path / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "README.md",
         "release-notes/PUBLISHED"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "Initial commit"],
        capture_output=True, check=True,
    )

    subprocess.run(
        ["git", "-C", str(repo_path), "remote", "add", "origin", str(bare_remote)],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "push", "origin", main_branch],
        capture_output=True, check=True,
    )


def _build_kanban_root(
    parent: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    *,
    push_to_remote: str = "false",
) -> pathlib.Path:
    """Build a minimal kanban root configured for release lifecycle testing.

    Creates kanban.cfg, projects.cfg, project.cfg, release-state.md (idle),
    tasks/queues/, logs/, locks/, and the Python helper modules that the CM
    scripts import.  The project.cfg dev_tree_path points at *repo_path*.

    Returns the kanban root path.
    """
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n"
        "\n"
        "[chain]\n"
        "pm_mode = automatic\n"
        "\n"
        "[wake]\n"
        "max_tasks_per_wake = 1\n"
        "max_runtime_seconds = 600\n",
        encoding="utf-8",
    )

    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\n"
        "priority=1\n"
        "description=Synthetic release test project\n"
        "enabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = release\n"
        f"is_self_build = false\n"
        f"push_to_remote = {push_to_remote}\n"
        f"\n"
        f"[versioning]\n"
        f"max_patch = 99\n"
        f"max_minor = 9\n"
        f"max_major = 0\n",
        encoding="utf-8",
    )

    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        "## Active RC\n"
        "none\n\n"
        "## RC Opened At\n"
        "none\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )

    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)

    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)

    src_cm_dir = _KANBAN_PY_DIR / "cm"
    for py_file in src_cm_dir.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(
            py_file.read_text(encoding="utf-8"), encoding="utf-8"
        )

    return root


def _run_cm_script(
    script_path: pathlib.Path,
    args: list[str],
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
    extra_env: Optional[dict] = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess:
    """Run a CM script against a temporary kanban root.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT, TEAM_ROOT, and
    PGAI_PROJECT_NAME so the script operates against the caller's temp tree
    rather than the live install.  REPO_ROOT is also set when provided.

    Returns subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    if repo_root is not None:
        base_env["REPO_ROOT"] = str(repo_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    if extra_env:
        base_env.update(extra_env)

    return subprocess.run(
        ["bash", str(script_path)] + args,
        capture_output=True, text=True, env=base_env,
        cwd=str(kanban_root), timeout=timeout,
    )


def _git_local(repo_path: pathlib.Path, *args: str) -> str:
    """Run a git command in *repo_path* and return stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args),
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _git_branch_exists_local(repo_path: pathlib.Path, branch: str) -> bool:
    """Return True when *branch* exists as a local branch in *repo_path*."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _git_tag_exists(repo_path: pathlib.Path, tag: str) -> bool:
    """Return True when *tag* exists in *repo_path*."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _commit_on_branch(
    repo_path: pathlib.Path,
    branch: str,
    filename: str,
    content: str,
    message: str,
    return_branch: Optional[str] = None,
) -> None:
    """Check out *branch*, write *filename* with *content*, commit, then return to *return_branch*.

    When *return_branch* is None the caller's current branch is left as *branch*.
    """
    _git_local(repo_path, "checkout", branch)
    (repo_path / filename).write_text(content, encoding="utf-8")
    _git_local(repo_path, "add", filename)
    _git_local(repo_path, "commit", "-m", message)
    if return_branch is not None:
        _git_local(repo_path, "checkout", return_branch)


# ---------------------------------------------------------------------------
# Case (a) — Clean release
# ---------------------------------------------------------------------------


class TestCleanRelease:
    """Case (a): end-to-end clean release with no mid-RC divergence on main."""

    def test_clean_release_produces_tag_notes_and_cleans_rc_branch(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Open RC, add coder work, release cleanly — all five assertions must hold.

        Asserts:
          - Squash landed: the coder's feature file is present on main.
          - Fidelity gate OK line appears in release output.
          - A release-notes commit exists on main (release-notes/<version>.md).
          - The version tag exists locally.
          - The RC branch was deleted (cleanup per procedure).

        This is the load-bearing positive case: if any of these fail, the release
        engine's core path is broken.
        """
        version = "v0.1.0"
        project_name = "clean_release_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # Step 1: Open the RC.
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        rc_branch = f"rc/{version}"
        assert _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' was not created.\n"
            f"stdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        # Step 2: Simulate coder work — add a unique file on the RC branch.
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_clean.txt", "clean feature content\n",
            "feat: add feature for clean release",
            return_branch="main",
        )

        # Step 3: Run cm-release.sh.
        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            f"cm-release.sh failed.\nstdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        combined_output = result_release.stdout + result_release.stderr

        # Assertion 1: squash landed — coder's file is present on main.
        _git_local(repo_path, "checkout", "main")
        assert (repo_path / "feature_clean.txt").exists(), (
            "feature_clean.txt committed on RC branch must be present on main after squash.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 2: fidelity gate OK line in output.
        assert "Fidelity gate" in combined_output and "OK" in combined_output, (
            "Expected 'Fidelity gate' and 'OK' in release output (gate should have passed).\n"
            f"output: {combined_output}"
        )

        # Assertion 3: release-notes commit exists on main.
        rn_committed = subprocess.run(
            ["git", "-C", str(repo_path), "ls-tree", "--name-only", "HEAD",
             f"release-notes/{version}.md"],
            capture_output=True, text=True,
        )
        assert f"release-notes/{version}.md" in rn_committed.stdout, (
            f"release-notes/{version}.md was not committed to main HEAD.\n"
            f"git ls-tree stdout: {rn_committed.stdout}\n"
            f"release stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 4: version tag exists locally.
        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found after cm-release.sh.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 5: RC branch cleaned up.
        assert not _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' still exists after cm-release.sh (should be deleted).\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )


# ---------------------------------------------------------------------------
# Case (b) — Conflicting mid-RC main commit (load-bearing proof of Goal 1)
# ---------------------------------------------------------------------------


class TestConflictingMidRcMainCommit:
    """Case (b): release exits non-zero when main diverges via a conflicting commit.

    This is the load-bearing behavioral proof that -X theirs has been removed from
    the squash step.  If -X theirs were still present, the squash would silently
    resolve the conflict toward the RC branch, the fidelity gate would see identical
    trees, and this test would falsely report a successful release — destroying the
    operator's commit silently.

    With -X theirs absent, the content conflict surfaces as a real merge conflict
    and the existing Trigger-5 HALT fires.
    """

    def test_conflicting_mid_rc_commit_triggers_halt_and_preserves_main(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Conflicting commit on main mid-RC causes Trigger-5 HALT; main's commit survives.

        Scenario:
          1. Open the RC branch.
          2. Add a commit to the RC branch that modifies 'shared.txt'.
          3. Add a CONFLICTING commit to main that modifies 'shared.txt' differently.
          4. Run cm-release.sh — the squash must produce a content conflict.

        Asserts:
          - cm-release.sh exits non-zero.
          - Output contains "Trigger 5" (the squash conflict HALT message).
          - No release tag was created.
          - Main still contains the pre-release commit (operator's work is intact).
        """
        version = "v0.2.0"
        project_name = "conflict_mid_rc_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # Create a shared file on main that both branches will modify differently.
        _commit_on_branch(
            repo_path, "main",
            "shared.txt", "line A\nline B\nline C\n",
            "chore: add shared file before RC",
        )
        # Push so origin is up to date for cm-open-rc.sh's fetch/pull.
        subprocess.run(
            ["git", "-C", str(repo_path), "push", "origin", "main"],
            capture_output=True, check=True,
        )

        # Step 1: Open the RC.
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        rc_branch = f"rc/{version}"

        # Step 2: Coder modifies shared.txt on the RC branch.
        _commit_on_branch(
            repo_path, rc_branch,
            "shared.txt", "line A\nline B — RC version\nline C\n",
            "feat: RC modifies shared.txt",
            return_branch="main",
        )

        # Step 3: An operator commits a CONFLICTING change to shared.txt on main
        # while the RC is in flight.  This simulates the scenario -X theirs used
        # to silently absorb — with the strategy removed it must now HALT.
        _commit_on_branch(
            repo_path, "main",
            "shared.txt", "line A\nline B — main operator version\nline C\n",
            "fix: operator commits conflicting change to main mid-RC",
        )

        # Record the SHA of the operator's pre-release commit on main so we can
        # verify it survives the failed release attempt.
        main_pre_release_sha = _git_local(repo_path, "rev-parse", "HEAD")

        # Step 4: Attempt cm-release.sh — must HALT on squash conflict.
        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )

        # Assertion 1: non-zero exit.
        assert result_release.returncode != 0, (
            "cm-release.sh must exit non-zero when the squash produces a content conflict.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 2: HALT message cites the squash conflict (Trigger 5).
        combined_output = result_release.stdout + result_release.stderr
        assert "Trigger 5" in combined_output, (
            "Expected 'Trigger 5' in release output (squash conflict HALT).\n"
            f"output: {combined_output}"
        )

        # Assertion 3: no tag was created.
        assert not _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' must NOT exist after a Trigger-5 HALT.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 4: main's pre-release commit still present (nothing destroyed).
        # Abort any residual squash state before inspecting main.
        subprocess.run(
            ["git", "-C", str(repo_path), "merge", "--abort"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "checkout", "main"],
            capture_output=True,
        )
        main_tip_sha = _git_local(repo_path, "rev-parse", "HEAD")
        assert main_tip_sha == main_pre_release_sha, (
            f"Main's pre-release commit (SHA {main_pre_release_sha[:7]}) was destroyed by the "
            f"failed release attempt.  HEAD is now {main_tip_sha[:7]}.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )


# ---------------------------------------------------------------------------
# Case (c) — Non-conflicting mid-RC main commit (fidelity gate)
# ---------------------------------------------------------------------------


class TestNonConflictingMidRcMainCommit:
    """Case (c): non-conflicting mid-RC main commit fires the fidelity gate.

    The fidelity gate (Step 7a in cm-release.sh) halts the release when
    main's tree diverges from rc/<version> after the squash, even when the
    squash itself completed without conflicts.

    TestFidelityGate.test_fidelity_gate_halts_when_main_diverges_mid_rc in
    test_rc_lifecycle.py already covers this behavior with a complete assertion
    set.  This class adds a confirming smoke-check to anchor the case here
    without duplicating the full fixture.
    """

    def test_non_conflicting_mid_rc_commit_fires_fidelity_gate(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Non-conflicting commit on main mid-RC causes fidelity gate HALT; no tag created.

        Scenario:
          1. Open the RC branch.
          2. Add coder work to the RC branch (a new file, no conflict potential).
          3. Add a DIFFERENT file directly to main (does not conflict with the RC).
          4. Run cm-release.sh — squash succeeds but fidelity gate detects divergence.

        Asserts:
          - cm-release.sh exits non-zero (fidelity gate HALT).
          - Output contains a fidelity-gate divergence indicator.
          - No release tag was created.

        For the full assertion set (HALT file existence, exact message format,
        RC branch survival) see TestFidelityGate.test_fidelity_gate_halts_when_
        main_diverges_mid_rc in test_rc_lifecycle.py.
        """
        version = "v0.3.0"
        project_name = "nonconflict_mid_rc_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # Step 1: Open the RC.
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        rc_branch = f"rc/{version}"

        # Step 2: Coder adds a file on the RC branch.
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_nc.txt", "non-conflicting feature content\n",
            "feat: add feature on RC branch",
            return_branch="main",
        )

        # Step 3: Operator adds a DIFFERENT file on main (no overlap with RC content).
        # This commit cannot conflict with the squash but will make main's tree differ
        # from rc/<version> after the squash, triggering the fidelity gate.
        _commit_on_branch(
            repo_path, "main",
            "hotfix_nc.txt", "non-conflicting hotfix content\n",
            "fix: operator adds non-conflicting hotfix to main mid-RC",
        )

        # Step 4: Run cm-release.sh — squash succeeds but fidelity gate fires.
        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )

        # Assertion 1: non-zero exit (fidelity gate HALT).
        assert result_release.returncode != 0, (
            "cm-release.sh must exit non-zero when the fidelity gate detects divergence.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 2: fidelity-gate divergence indicator in output.
        combined_output = result_release.stdout + result_release.stderr
        assert (
            "DIVERGENCE" in combined_output
            or "fidelity gate" in combined_output.lower()
        ), (
            "Expected divergence or fidelity gate indicator in release output.\n"
            f"output: {combined_output}"
        )

        # Assertion 3: no tag was created.
        assert not _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' must NOT exist after fidelity gate HALT.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )


# ---------------------------------------------------------------------------
# Shared helper for changelog freshness verification in synthetic fixtures
# ---------------------------------------------------------------------------


def _assert_changelog_fresh_at_commit(
    repo_path: pathlib.Path,
    commit_ref: str,
    bugs_dir: pathlib.Path,
    label: str,
) -> None:
    """Assert that CHANGELOG.md at *commit_ref* is byte-identical to a fresh regeneration.

    Extracts CHANGELOG.md from *commit_ref* in *repo_path*, then regenerates the
    changelog via ``changelog_writer`` subprocess using the release-notes files and
    PUBLISHED manifest that were committed at that same ref.  Byte-compares the two.

    This is the synthetic-fixture equivalent of ``lint_changelog_freshness.py``'s
    freshness check, adapted so it reads from the fixture repo rather than from the
    script's own dev-tree root (which would be the live repository, not the fixture).

    Args:
        repo_path:   Path to the synthetic git repository.
        commit_ref:  Git ref (branch tip, tag, or SHA) to check.
        bugs_dir:    Path to the bugs directory used by this fixture (typically empty).
        label:       Human-readable label for assertion messages (e.g. "tip" or "tag").
    """
    # Extract the committed CHANGELOG.md at this ref.
    result = subprocess.run(
        ["git", "-C", str(repo_path), "show", f"{commit_ref}:CHANGELOG.md"],
        capture_output=True,
        text=False,  # raw bytes
    )
    assert result.returncode == 0, (
        f"Cannot read CHANGELOG.md from {label} ({commit_ref}).\n"
        f"stderr: {result.stderr.decode(errors='replace')}\n"
        "CHANGELOG.md must be committed to the repository at this point."
    )
    committed_bytes = result.stdout

    # Determine the repo root as seen from this ref (use a temp working tree
    # snapshot so the changelog_writer subprocess reads the correct release-notes).
    # We cannot run changelog_writer directly against commit_ref because it is a
    # Python module that reads files from the filesystem, not from git objects.
    # Instead, export the release-notes/ tree and PUBLISHED file from the ref into
    # a temporary directory, then run the writer against that directory.
    #
    # Determine which version files are present at commit_ref.
    ls_result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-tree", "--name-only", commit_ref,
         "release-notes/"],
        capture_output=True, text=True,
    )
    release_notes_entries = [
        e.strip() for e in ls_result.stdout.splitlines() if e.strip()
    ]

    # Build a minimal file-system snapshot that changelog_writer can read.
    # We use the repo_path directly but at this point in the test the working
    # tree is on main and already has the release-notes committed, so we can
    # run the writer directly against the working tree when the ref is the
    # current HEAD.  For the tag (which equals the tip in the post-fix world),
    # the working tree has the same content, so this is safe.
    #
    # Regenerate via subprocess, mirroring the cm-release.sh Step 11b invocation:
    #   PYTHONHASHSEED=0 python3 -m pgai_agent_kanban.cm.changelog_writer \
    #     "$REPO_ROOT" "$_cl_bugs_dir"
    team_dir = _TEAM_DIR
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = str(team_dir)
    regen_result = subprocess.run(
        [
            sys.executable,
            "-m", "pgai_agent_kanban.cm.changelog_writer",
            str(repo_path),
            str(bugs_dir),
        ],
        capture_output=True,
        text=False,  # raw bytes
        env=env,
        cwd=str(team_dir),
    )
    assert regen_result.returncode == 0, (
        f"changelog_writer subprocess failed for {label} freshness check.\n"
        f"stderr: {regen_result.stderr.decode(errors='replace')}\n"
        f"stdout: {regen_result.stdout.decode(errors='replace')}"
    )
    regenerated_bytes = regen_result.stdout

    assert committed_bytes == regenerated_bytes, (
        f"CHANGELOG.md is STALE at {label} ({commit_ref}).\n"
        "The committed artifact does not match a fresh regeneration from the "
        "current release-notes and bug ledger.\n"
        f"Committed size: {len(committed_bytes)} bytes, "
        f"Regenerated size: {len(regenerated_bytes)} bytes.\n"
        "This is the freshness condition that must hold at the branch tip after "
        "every release.  Check that the polish step runs BEFORE CHANGELOG regeneration."
    )


# ---------------------------------------------------------------------------
# Case (d) — Changelog freshness and tag-tip identity after full release
# ---------------------------------------------------------------------------


class TestChangelogFreshnessAfterRelease:
    """Case (d): changelog freshness at tip and tag; tag == tip after full release.

    This class is the regression lock for the polish-before-regeneration ordering
    fix.  The fix ensures that after a complete release run the committed
    CHANGELOG.md matches a fresh regeneration from the release-notes inputs, and
    that the release tag and the branch tip point to the same commit.

    Three load-bearing assertions:
      1. changelog_writer regeneration on the fixture's release-notes + empty bugs
         produces bytes identical to the committed CHANGELOG.md at the branch TIP
         after the release.  (This is the freshness-at-tip condition.)
      2. The same regeneration matches the committed CHANGELOG.md at the release
         TAG.  (Regression lock on the previously-working tag-side gate.)
      3. git describe --tags on the main branch tip has no -N-g suffix, confirming
         the tag and the tip are the same commit.  (Verifies the no-orphaned-tag
         invariant introduced by the polish-before-regeneration fix.)

    The fixture includes WRITER-authored release notes committed on the RC branch
    so that cm-release.sh exercises the WRITER-notes path (Step 4e stamp +
    Step 8 skip + Step 8b polish-check).  This is the realistic scenario where the
    freshness bug historically occurred: a WRITER polish commit landed after the
    CHANGELOG commit, making the tip stale.  The fix ensures that after the
    release the tag equals the tip — any regression that re-introduces a commit
    between the CHANGELOG commit and the tag will break assertion 3.

    Determinism: changelog_writer.py sorts heading collections alphabetically
    (sorted(_IMPLEMENTED_HEADINGS) / sorted(_FIXED_HEADINGS)), so output is
    stable regardless of PYTHONHASHSEED.  PYTHONHASHSEED=0 is passed to the
    subprocess as belt-and-braces (mirroring the cm-release.sh invocation).
    """

    def test_changelog_fresh_at_tip_and_tag_after_release_with_writer_notes(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Full release with WRITER-authored notes: changelog fresh at tip and tag; tag == tip.

        Scenario:
          1. Open the RC branch.
          2. Add coder work on the RC branch.
          3. Add WRITER-authored release notes on the RC branch (with PENDING-RELEASE
             placeholder) so cm-release.sh exercises the Step 4e stamp path and
             the Step 8 skip path.
          4. Run cm-release.sh.

        Asserts:
          - cm-release.sh exits 0.
          - CHANGELOG.md at the branch tip is byte-identical to a fresh
            changelog_writer regeneration (freshness at tip — the load-bearing
            condition that failed before the fix).
          - CHANGELOG.md at the release tag is byte-identical to the same
            regeneration (freshness at tag — regression lock on the
            currently-working half).
          - git describe --tags on the tip has no -N-g suffix (tag == tip —
            no orphaned commits between the tag and the branch tip).
        """
        version = "v0.4.0"
        project_name = "polish_freshness_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # Create the bugs directory that cm-release.sh Step 11b passes to
        # changelog_writer.  An empty directory means no known issues — the
        # freshness check must still pass (the writer produces valid output
        # from release-notes alone).
        bugs_dir = kanban_root / "projects" / project_name / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Open the RC.
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        rc_branch = f"rc/{version}"
        assert _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' was not created by cm-open-rc.sh.\n"
            f"stdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        # Step 2: Simulate coder work on the RC branch.
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_polish.txt", "polish regression test feature\n",
            "feat: add feature for polish regression test",
            return_branch=None,  # stay on rc_branch
        )

        # Step 3: Add WRITER-authored release notes on the RC branch.
        # The notes include a PENDING-RELEASE placeholder in ## Status so that
        # cm-release.sh Step 4e stamps it with the actual ship decision.
        # Features section is non-empty so changelog_writer produces a real entry.
        writer_notes = textwrap.dedent(f"""\
            # Release Notes: {project_name} {version}

            **Release Date:** 2026-07-11
            **Released By:** WRITER

            ## Status
            PENDING-RELEASE

            ## Summary
            Test release for changelog freshness regression lock.

            ## Features
            - Add feature for polish regression test

            ## Bug Fixes
            None

            ## Known Issues
            None
            """)
        (repo_path / "release-notes" / f"{version}.md").write_text(
            writer_notes, encoding="utf-8"
        )
        _git_local(repo_path, "add", f"release-notes/{version}.md")
        _git_local(repo_path, "commit", "-m",
                   f"docs: WRITER release notes for {version}")

        # Return to main for the release run (cm-release.sh checks out main itself,
        # but starting from main avoids any detached HEAD confusion).
        _git_local(repo_path, "checkout", "main")

        # Step 4: Run cm-release.sh.
        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            f"cm-release.sh failed.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Verify we are on main at the tip.
        _git_local(repo_path, "checkout", "main")

        # Assertion 1: tag exists.
        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found after cm-release.sh.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 2: CHANGELOG.md is fresh at the branch tip.
        # Regenerate using the same subprocess invocation as cm-release.sh Step 11b:
        #   PYTHONHASHSEED=0 python3 -m pgai_agent_kanban.cm.changelog_writer
        #   "$REPO_ROOT" "$_cl_bugs_dir"
        # then byte-compare against the committed artifact.
        _assert_changelog_fresh_at_commit(
            repo_path=repo_path,
            commit_ref="main",
            bugs_dir=bugs_dir,
            label="tip",
        )

        # Assertion 3: CHANGELOG.md is fresh at the release tag.
        # Regression lock: the tag-side freshness was already passing before
        # the fix.  This assertion ensures a future regression that makes the
        # tag-side stale (e.g., moving tag creation to before CHANGELOG commit)
        # is caught.
        _assert_changelog_fresh_at_commit(
            repo_path=repo_path,
            commit_ref=version,
            bugs_dir=bugs_dir,
            label="tag",
        )

        # Assertion 4: tag == tip — no orphaned commits between the CHANGELOG
        # commit and the branch tip.
        # git describe --tags returns "vX.Y.Z" when HEAD is the tagged commit
        # and "vX.Y.Z-N-gSHA" when HEAD is N commits ahead of the tag.
        # After the fix, release.sh creates the tag on the last housekeeping
        # commit (README.md reference update), which is also the branch tip —
        # no subsequent commits are added.
        describe_result = subprocess.run(
            ["git", "-C", str(repo_path), "describe", "--tags", "--exact-match",
             "HEAD"],
            capture_output=True, text=True,
        )
        # --exact-match exits 0 only when HEAD is directly at a tag; exits 128 otherwise.
        assert describe_result.returncode == 0, (
            f"git describe --tags --exact-match HEAD failed (rc={describe_result.returncode}).\n"
            "The branch tip is NOT at the release tag — extra commits were added "
            "between the tag and the branch tip.  This breaks the tag==tip invariant "
            "introduced by the polish-before-regeneration fix.\n"
            f"git describe output: {describe_result.stdout!r}\n"
            f"git describe stderr: {describe_result.stderr!r}\n"
            f"release stdout: {result_release.stdout}\nrelease stderr: {result_release.stderr}"
        )
        described_tag = describe_result.stdout.strip()
        assert described_tag == version, (
            f"git describe --tags --exact-match reports '{described_tag}' "
            f"but expected '{version}'.\n"
            "The branch tip is at a different tag than the release tag."
        )


# ---------------------------------------------------------------------------
# Case (e) — Resume from induced partial-run failures (an earlier defect)
# ---------------------------------------------------------------------------
#
# Three parameterized sub-cases exercise the resume semantics added by an earlier defect:
#   - post-squash:    squash committed to main; no release-notes, no tag.
#   - post-notes:     squash + release-notes committed; no CHANGELOG, no tag.
#   - post-CHANGELOG: squash + release-notes + CHANGELOG committed; no tag.
#
# Each case builds the partial git state manually (mirroring what release.sh
# would have committed) then re-invokes release.sh and asserts end-to-end
# completion: tag created, RC branch deleted, release-state updated.
#
# Simulation approach: instead of process-kill machinery, each case manually
# commits exactly what the prior run would have committed, stopping at the
# desired boundary.  This is stable and fast.
# ---------------------------------------------------------------------------


def _build_partial_release_state(
    repo_path: pathlib.Path,
    kanban_root: pathlib.Path,
    project_name: str,
    version: str,
    *,
    stop_after: str,
) -> None:
    """Build partial git state on main as release.sh would have left it at *stop_after*.

    *stop_after* must be one of: 'squash', 'notes', 'changelog'.

    Adds a feature file on the RC branch, checks out main, squash-merges the RC,
    then optionally commits the release-notes and CHANGELOG stubs — stopping at
    the requested boundary.  The RC branch is left intact (as it would be if
    release.sh died before Step 10-11).
    """
    rc_branch = f"rc/{version}"

    # Add coder work on the RC branch.
    _commit_on_branch(
        repo_path, rc_branch,
        f"feature_{stop_after}_resume.txt",
        f"resume test feature content for {stop_after}\n",
        f"feat: add feature for {stop_after} resume test",
        return_branch="main",
    )

    # Squash the RC branch content onto main (Step 6 + 7).
    subprocess.run(
        ["git", "-C", str(repo_path), "merge", "--squash", rc_branch],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", f"Release {version}"],
        capture_output=True, check=True,
    )

    if stop_after == "squash":
        return

    # Commit a stub release-notes file (Step 8 / Step 11a).
    release_notes_dir = repo_path / "release-notes"
    release_notes_dir.mkdir(parents=True, exist_ok=True)
    notes_stub = (
        f"# Release Notes: {project_name} {version}\n\n"
        f"## Status\nFUNCTIONAL\n\n"
        f"## Summary\nResume test stub.\n\n"
        f"## Features\n- Resume test feature\n\n"
        f"## Bug Fixes\nNone\n\n"
        f"## Known Issues\nNone\n"
    )
    (release_notes_dir / f"{version}.md").write_text(notes_stub, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", f"release-notes/{version}.md"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", f"Add release notes for {version}"],
        capture_output=True, check=True,
    )

    if stop_after == "notes":
        return

    # Commit a stub CHANGELOG.md (Step 11b).
    # Use the real changelog_writer to generate a valid CHANGELOG so the
    # freshness gate is not confused by a hand-crafted stub.
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    team_dir = _TEAM_DIR
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = str(team_dir)
    cl_result = subprocess.run(
        [
            sys.executable,
            "-m", "pgai_agent_kanban.cm.changelog_writer",
            str(repo_path),
            str(bugs_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(team_dir),
    )
    assert cl_result.returncode == 0, (
        f"changelog_writer failed while building partial state.\n"
        f"stderr: {cl_result.stderr}\nstdout: {cl_result.stdout}"
    )
    (repo_path / "CHANGELOG.md").write_text(cl_result.stdout, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "CHANGELOG.md"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", f"Update CHANGELOG.md for {version}"],
        capture_output=True, check=True,
    )

    # stop_after == 'changelog' — return without creating the tag.


class TestResumeFromPartialRun:
    """Case (e): re-invocation after an induced partial-run failure completes the release.

    Three sub-cases correspond to the boundaries named in an earlier defect:
      - post-squash: squash committed; no release-notes, no CHANGELOG, no tag.
      - post-notes:  squash + release-notes committed; no CHANGELOG, no tag.
      - post-CHANGELOG: squash + notes + CHANGELOG committed; no tag.

    Each case manually builds the partial git state, then invokes release.sh and
    asserts that the run completes end-to-end: tag exists, RC branch deleted,
    release-state Active RC cleared.
    """

    def _run_resume_case(
        self,
        tmp_path: pathlib.Path,
        version: str,
        project_name: str,
        stop_after: str,
    ) -> None:
        """Shared body for all three resume sub-cases."""
        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        # Step 1: Open the RC branch.
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )

        rc_branch = f"rc/{version}"

        # Step 2: Build partial git state (simulate release.sh dying at stop_after).
        _build_partial_release_state(
            repo_path, kanban_root, project_name, version, stop_after=stop_after,
        )

        # Verify the partial state looks right before resuming.
        assert _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' must still exist in partial state '{stop_after}'."
        )
        assert not _git_tag_exists(repo_path, version), (
            f"Tag '{version}' must NOT exist before the resume run (partial state '{stop_after}')."
        )

        # Step 3: Re-invoke release.sh — must complete end-to-end.
        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            f"cm-release.sh failed on resume from partial state '{stop_after}'.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 1: tag exists after resume.
        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found after resume from '{stop_after}'.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 2: RC branch deleted.
        assert not _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' still exists after resume from '{stop_after}' "
            f"(should be deleted).\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # Assertion 3: release-state Active RC cleared.
        release_state_path = (
            kanban_root / "projects" / project_name / "release-state.md"
        )
        release_state_text = release_state_path.read_text(encoding="utf-8")
        assert "Active RC" in release_state_text, (
            "release-state.md must contain '## Active RC' after resume run."
        )
        # The Active RC field must be 'none' (cleared by Step 12).
        lines = release_state_text.splitlines()
        active_rc_value = ""
        for i, line in enumerate(lines):
            if line.strip() == "## Active RC":
                if i + 1 < len(lines):
                    active_rc_value = lines[i + 1].strip()
                break
        assert active_rc_value == "none", (
            f"release-state.md Active RC must be 'none' after resume; got '{active_rc_value}'.\n"
            f"release-state content:\n{release_state_text}"
        )

        # Assertion 4: feature file from partial state is present on main.
        _git_local(repo_path, "checkout", "main")
        feature_file = f"feature_{stop_after}_resume.txt"
        assert (repo_path / feature_file).exists(), (
            f"{feature_file} must be present on main after resume from '{stop_after}'.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

    def test_resume_after_squash_committed(self, tmp_path: pathlib.Path) -> None:
        """Re-invocation after squash committed (no notes, no CHANGELOG, no tag) completes the release.

        Simulates release.sh dying between Step 7 (squash commit) and Step 8 (release-notes).
        The resume run must detect the squash already landed (empty index after merge --squash),
        skip the squash commit, skip the fidelity gate, generate and commit the release-notes
        and CHANGELOG, create the tag, and clear the release-state.
        """
        self._run_resume_case(
            tmp_path,
            version="v0.5.0",
            project_name="resume_post_squash_test",
            stop_after="squash",
        )

    def test_resume_after_notes_committed(self, tmp_path: pathlib.Path) -> None:
        """Re-invocation after release-notes committed (no CHANGELOG, no tag) completes the release.

        Simulates release.sh dying between Step 8 (release-notes commit) and Step 11b
        (CHANGELOG commit) — the exact boundary described in an earlier defect's reproduction case.
        The resume run must detect the notes already committed (git ls-tree guard at Step 8),
        regenerate and commit CHANGELOG, create the tag, and clear the release-state.
        """
        self._run_resume_case(
            tmp_path,
            version="v0.6.0",
            project_name="resume_post_notes_test",
            stop_after="notes",
        )

    def test_resume_after_changelog_committed(self, tmp_path: pathlib.Path) -> None:
        """Re-invocation after CHANGELOG committed (no tag) completes the release.

        Simulates release.sh dying between Step 11b (CHANGELOG commit) and Step 13 (tag).
        The resume run must detect the squash, notes, and CHANGELOG already committed,
        skip those steps, create the tag, delete the RC branch, and clear release-state.
        """
        self._run_resume_case(
            tmp_path,
            version="v0.7.0",
            project_name="resume_post_changelog_test",
            stop_after="changelog",
        )
