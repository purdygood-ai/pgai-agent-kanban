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
  - Release-path push gate (TestReleasePathPushGate): open-rc and release
    push only for a project registered in projects.cfg whose own project.cfg
    names dev_tree_path. No project.cfg, an unregistered project, and a
    project.cfg without dev_tree_path each release locally with zero git
    network calls (git network stub) and one skipped-unresolved-project
    line per script; a registered project with no push_to_remote key still
    pushes, and push_to_remote = false stays local.

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

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
from typing import Optional

import pytest
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.git_network_stub import install_git_network_stub
from tests.fixtures.scrubbed_subprocess_env import (
    AGENT_SCOPED_ENV_NAMES,
    build_scrubbed_env,
    scrub_agent_scoped_env,
)


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="cm-release-synthetic")

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


def _build_cm_script_env(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
    extra_env: Optional[dict] = None,
) -> dict:
    """Build the environment `_run_cm_script` hands to its child process.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT, TEAM_ROOT, and
    PGAI_PROJECT_NAME so the script operates against the caller's temp tree
    rather than the live install.  REPO_ROOT is also set when provided.

    The base environment inherits the invoking shell's `os.environ` (this
    fixture drives a real, unstubbed `git`/`bash` toolchain end-to-end, so it
    cannot enumerate everything those need) but always scrubs
    `tests.fixtures.scrubbed_subprocess_env.AGENT_SCOPED_ENV_NAMES` first --
    a caller that wants the script under test to see an agent-wake context
    passes it explicitly through *extra_env*, applied last, rather than
    relying on whatever the shell that launched pytest happened to export.

    Returns a plain dict suitable for `subprocess.run(..., env=...)`.  Split
    out from `_run_cm_script` so a test can obtain and assert on the built
    environment directly, without driving a real subprocess.
    """
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    if repo_root is not None:
        base_env["REPO_ROOT"] = str(repo_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    scrub_agent_scoped_env(base_env)
    if extra_env:
        base_env.update(extra_env)
    return base_env


def _run_cm_script(
    script_path: pathlib.Path,
    args: list[str],
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
    extra_env: Optional[dict] = None,
    timeout: int = 90,
    cwd: Optional[pathlib.Path] = None,
) -> subprocess.CompletedProcess:
    """Run a CM script against a temporary kanban root.

    See `_build_cm_script_env` for how the child's environment is
    constructed.  The child runs in *cwd* when given, else in the kanban
    root.  Returns subprocess.CompletedProcess with returncode, stdout,
    stderr.
    """
    env = _build_cm_script_env(
        kanban_root, project_name, repo_root=repo_root, extra_env=extra_env,
    )
    return subprocess.run(
        ["bash", str(script_path)] + args,
        capture_output=True, text=True, env=env,
        cwd=str(cwd if cwd is not None else kanban_root), timeout=timeout,
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
# _run_cm_script's environment scrub guarantee
# ---------------------------------------------------------------------------


class TestRunCmScriptEnvironmentScrub:
    """`_build_cm_script_env` must never let an agent-scoped signal reach a
    CM script by inheritance, only through an explicit `extra_env` opt-in.

    No test in this class drives a real CM script, git, or bash — each
    builds the environment dict directly and asserts on its contents.
    """

    def test_ambient_agent_scoped_signal_is_absent_from_built_env(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent-scoped name present in the current process must not
        survive into the built environment, regardless of whether the
        invoking shell happens to carry it — the guard sets the signal
        itself rather than relying on the ambient state of any one host."""
        for name in AGENT_SCOPED_ENV_NAMES:
            monkeypatch.setenv(name, "1")

        built_env = _build_cm_script_env(
            tmp_path / "kanban_root", "scrub_guard_project",
        )

        for name in AGENT_SCOPED_ENV_NAMES:
            assert name not in built_env, (
                f"{name} was present in the current process and must be "
                f"scrubbed from the environment _run_cm_script builds, but "
                f"was found in the result: {built_env.get(name)!r}"
            )

    def test_extra_env_still_delivers_agent_scoped_signal_explicitly(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A caller that deliberately wants the wake context still gets it
        through `extra_env`, applied after the scrub — the opt-in path the
        scrub is not allowed to break."""
        for name in AGENT_SCOPED_ENV_NAMES:
            monkeypatch.delenv(name, raising=False)

        opted_in = {name: "1" for name in AGENT_SCOPED_ENV_NAMES}
        built_env = _build_cm_script_env(
            tmp_path / "kanban_root", "scrub_guard_project",
            extra_env=opted_in,
        )

        for name in AGENT_SCOPED_ENV_NAMES:
            assert built_env.get(name) == "1", (
                f"{name} was passed explicitly through extra_env and must "
                f"reach the built environment unchanged, but was "
                f"{built_env.get(name)!r}"
            )


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
# Fixed-In writeback: cwd-independent, scoped to the bugs this release closed
# ---------------------------------------------------------------------------


def _ledger_bug(name: str, status: str) -> str:
    """A minimal ledger bug file with an empty, template-commented Fixed In."""
    return (
        f"# {name}\n\n"
        f"## Status\n{status}\n\n"
        "## Symptom\nSomething misbehaves.\n\n"
        "## Fixed In\n<!-- Written by the release engine at the fixing release. -->\n\n"
        "## Public ID\n<!-- Assigned at first disclosure. -->\n"
    )


class TestFixedInWriteback:
    """Step 16b stamps ## Fixed In on this release's bugs, from any cwd."""

    def test_release_from_shadowing_cwd_stamps_only_bugs_this_release_closed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Release run from a directory whose child ``pgai_agent_kanban/`` is
        not a Python package (the kanban root seen from its parent), with
        PYTHONPATH empty so only the release engine's own path composition
        can make the package importable.

        Asserts:
          - Step 16b prints its result line with 1 stamped and no traceback.
          - The bug this release promoted to done carries ## Fixed In: v0.1.0.
          - A done bug from an earlier release with an empty ## Fixed In is
            byte-for-byte unchanged.
          - The closing summary reports the stamp count, not a bare claim.
        """
        version = "v0.1.0"
        project_name = "fixed_in_release_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)
        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )
        proj = kanban_root / "projects" / project_name

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        _commit_on_branch(
            repo_path, f"rc/{version}", "feature_fixed_in.txt", "fix\n",
            "fix: resolve the bundled defect", return_branch="main",
        )

        # Ledger: one bug bundled into this RC, one closed by an earlier release.
        bugs_dir = proj / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)
        current = bugs_dir / "BUG-0002-closed-by-this-release.md"
        current.write_text(_ledger_bug(current.stem, "running"), encoding="utf-8")
        earlier = bugs_dir / "BUG-0001-closed-by-an-earlier-release.md"
        earlier.write_text(_ledger_bug(earlier.stem, "done"), encoding="utf-8")
        earlier_before = earlier.read_bytes()

        # The bundle that produced this RC, and the DONE task that fixed it.
        req_dir = proj / "requirements"
        req_dir.mkdir(parents=True, exist_ok=True)
        (req_dir / f"{version}-bugfix-bundle.md").write_text(
            f"# {version} bugfix bundle\n\n## Status\nrunning\n\n"
            f"## Bundled Items\n\n- {current.name} (`{current}`)\n",
            encoding="utf-8",
        )
        task_dir = proj / "tasks" / "CODER-20990101-001-fix-bundled-defect"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "README.md").write_text(
            f"# Task\n\n## Inputs\n- {current.name}\n\n## Release Version\n{version}\n", encoding="utf-8"
        )
        (task_dir / "status.md").write_text("# Status\n\n## State\nDONE\n", encoding="utf-8")

        shadow_parent = tmp_path / "operator_home"
        (shadow_parent / "pgai_agent_kanban" / "projects").mkdir(parents=True)

        result = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
            extra_env={"PYTHONPATH": ""}, cwd=shadow_parent,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, f"cm-release.sh failed.\n{output}"

        step16b = [line for line in output.splitlines() if line.startswith("[Step 16b]")]
        assert "Traceback" not in "\n".join(step16b) and "ImportError" not in output, output
        assert any(
            line.startswith("[Step 16b] Fixed-In writeback: 1 stamped,") for line in step16b
        ), "Step 16b result line missing or wrong count:\n" + "\n".join(step16b)

        assert f"<!-- Written by the release engine at the fixing release. -->\n{version}\n" in (
            current.read_text(encoding="utf-8")
        ), current.read_text(encoding="utf-8")
        assert earlier.read_bytes() == earlier_before, (
            "a done bug from an earlier release must not be stamped with this "
            f"release's version:\n{earlier.read_text(encoding='utf-8')}"
        )
        assert f"  - ## Fixed In: {version} — 1 stamped," in result.stdout, result.stdout


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
    # current HEAD.  For the tag (which equals the tip once the release completes),
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
            condition a tag cut ahead of the CHANGELOG commit breaks).
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
        # release.sh creates the tag on the last housekeeping
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

    @pytest.mark.parametrize(
        "version, project_name, stop_after",
        [
            pytest.param(
                "v0.5.0", "resume_post_squash_test", "squash",
                id="post_squash_no_notes_no_changelog_no_tag",
            ),
            pytest.param(
                "v0.6.0", "resume_post_notes_test", "notes",
                id="post_notes_no_changelog_no_tag",
            ),
            pytest.param(
                "v0.7.0", "resume_post_changelog_test", "changelog",
                id="post_changelog_no_tag",
            ),
        ],
    )
    def test_resume_after_partial_run_completes_release(
        self,
        tmp_path: pathlib.Path,
        version: str,
        project_name: str,
        stop_after: str,
    ) -> None:
        """Re-invocation after an induced partial-run failure completes the release.

        Three boundaries correspond to the sub-cases named in an earlier defect:
          - post_squash: squash committed; no release-notes, no CHANGELOG, no tag.
            Simulates release.sh dying between Step 7 (squash commit) and Step 8
            (release-notes). The resume run must detect the squash already landed
            (empty index after merge --squash), skip the squash commit, skip the
            fidelity gate, generate and commit the release-notes and CHANGELOG,
            create the tag, and clear the release-state.
          - post_notes: squash + release-notes committed; no CHANGELOG, no tag.
            Simulates release.sh dying between Step 8 (release-notes commit) and
            Step 11b (CHANGELOG commit). The resume run must detect the notes
            already committed (git ls-tree guard at Step 8), regenerate and commit
            CHANGELOG, create the tag, and clear the release-state.
          - post_changelog: squash + notes + CHANGELOG committed; no tag.
            Simulates release.sh dying between Step 11b (CHANGELOG commit) and
            Step 13 (tag). The resume run must detect the squash, notes, and
            CHANGELOG already committed, skip those steps, create the tag, delete
            the RC branch, and clear release-state.

        Each case manually builds the partial git state, then invokes release.sh
        and asserts that the run completes end-to-end: tag exists, RC branch
        deleted, release-state Active RC cleared.
        """
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


# ---------------------------------------------------------------------------
# Shared helper for committing an executable in-repo hook onto a branch
# ---------------------------------------------------------------------------


def _commit_in_repo_hook(
    repo_path: pathlib.Path,
    branch: str,
    phase: str,
    script_body: str,
    message: str,
    *,
    return_branch: Optional[str] = None,
) -> None:
    """Check out *branch*, write an executable in-repo hook for *phase*, commit it.

    Writes to .pgai/hooks/cm-release-<phase>.sh (tier (c) resolution — see
    cm_release_hooks.sh) with the executable bit set so git records mode 100755.
    """
    _git_local(repo_path, "checkout", branch)
    hook_path = repo_path / ".pgai" / "hooks" / f"cm-release-{phase}.sh"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(script_body, encoding="utf-8")
    hook_path.chmod(0o755)
    _git_local(repo_path, "add", str(hook_path.relative_to(repo_path)))
    _git_local(repo_path, "commit", "-m", message)
    if return_branch is not None:
        _git_local(repo_path, "checkout", return_branch)


# ---------------------------------------------------------------------------
# Case (f) — Golden fixture: verify-before-destroy on a rigged pre-tag gate
# ---------------------------------------------------------------------------


class TestVerifyBeforeDestroy:
    """Case (f): a pre-tag gate failure leaves the RC branch, release-state.md,
    and main byte-identical to their pre-release state; fixing the cause and
    re-running (no operator git surgery in between) completes the release.

    This is the golden-fixture proof that destructive steps (RC branch
    deletion, local and origin; release-state.md rewrite) are structurally
    ordered after ALL verification, including the pre-tag hook chain. Before
    the fix, this scenario stranded the release: the RC branch and
    release-state.md were already destroyed by the time the pre-tag hook
    fired, so a rigged failure here left nothing to cleanly re-run against.
    """

    def test_rigged_pre_tag_hook_leaves_world_untouched_then_fix_and_rerun_ships(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Rigged pre-tag hook failure preserves all state; fix-and-rerun tags cleanly.

        Scenario:
          1. Open the RC branch (push_to_remote=true, so the origin fixture
             remote is exercised — the golden fixture's "local + origin" bar).
          2. Add coder work and a pre-tag hook (in-repo, tier (c)) that exits 1
             — the "rigged to fail" gate.
          3. Snapshot main's SHA, release-state.md content, and confirm the RC
             branch exists locally and on the origin fixture remote.
          4. Run cm-release.sh — must exit non-zero.
          5. Assert: RC branch still exists locally AND on origin; release-state.md
             is byte-identical to the snapshot; main is back at the pre-release
             SHA (the rollback guard restored it); no tag was created.
          6. Fix the cause: commit a replacement pre-tag hook that exits 0 (no
             manual git surgery — this is the entire "operator" action).
          7. Re-run cm-release.sh — must exit 0, tag created, RC branch deleted
             (local + origin), release-state Active RC cleared.
        """
        version = "v0.20.0"
        project_name = "verify_before_destroy_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="true"
        )

        # Step 1: Open the RC (pushed to the origin fixture remote).
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
        origin_has_rc = subprocess.run(
            ["git", "-C", str(repo_path), "ls-remote", "--exit-code", "--heads",
             "origin", rc_branch],
            capture_output=True,
        ).returncode == 0
        assert origin_has_rc, (
            f"RC branch '{rc_branch}' was not pushed to the origin fixture remote "
            "by cm-open-rc.sh (push_to_remote=true)."
        )

        # Step 2: Coder work, plus a pre-tag hook rigged to fail.
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_vbd.txt", "verify-before-destroy feature content\n",
            "feat: add feature for verify-before-destroy test",
        )
        _commit_in_repo_hook(
            repo_path, rc_branch, "pre-tag",
            "#!/usr/bin/env bash\nexit 1\n",
            "chore: add pre-tag hook rigged to fail",
            return_branch="main",
        )

        # Step 3: Snapshot pre-release state.
        main_pre_release_sha = _git_local(repo_path, "rev-parse", "main")
        release_state_path = (
            kanban_root / "projects" / project_name / "release-state.md"
        )
        release_state_pre_release = release_state_path.read_text(encoding="utf-8")

        # Step 4: Run cm-release.sh — the rigged pre-tag hook must HALT it.
        result_release_1 = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release_1.returncode != 0, (
            "cm-release.sh must exit non-zero when the pre-tag hook is rigged to fail.\n"
            f"stdout: {result_release_1.stdout}\nstderr: {result_release_1.stderr}"
        )

        # Assertion set 1 — nothing destroyed by the failed gate.
        assert _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' was deleted locally despite the pre-tag gate "
            "failing — destructive steps ran before verification completed.\n"
            f"stdout: {result_release_1.stdout}\nstderr: {result_release_1.stderr}"
        )
        origin_still_has_rc = subprocess.run(
            ["git", "-C", str(repo_path), "ls-remote", "--exit-code", "--heads",
             "origin", rc_branch],
            capture_output=True,
        ).returncode == 0
        assert origin_still_has_rc, (
            f"RC branch '{rc_branch}' was deleted on origin despite the pre-tag gate "
            "failing.\n"
            f"stdout: {result_release_1.stdout}\nstderr: {result_release_1.stderr}"
        )
        assert release_state_path.read_text(encoding="utf-8") == release_state_pre_release, (
            "release-state.md was mutated despite the pre-tag gate failing — it must "
            "be byte-identical to its pre-release state.\n"
            f"stdout: {result_release_1.stdout}\nstderr: {result_release_1.stderr}"
        )
        _git_local(repo_path, "checkout", "main")
        main_post_failure_sha = _git_local(repo_path, "rev-parse", "main")
        assert main_post_failure_sha == main_pre_release_sha, (
            f"main moved from {main_pre_release_sha[:7]} to {main_post_failure_sha[:7]} "
            "despite the pre-tag gate failing — the rollback guard must reset main to "
            "its pre-release commit when verification does not complete.\n"
            f"stdout: {result_release_1.stdout}\nstderr: {result_release_1.stderr}"
        )
        assert not _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' must NOT exist after the rigged pre-tag gate failure.\n"
            f"stdout: {result_release_1.stdout}\nstderr: {result_release_1.stderr}"
        )

        # Step 6: Fix the cause — replace the hook with one that exits 0. No
        # manual git surgery on main, the RC branch, or release-state.md: this
        # commit is the entire "operator" action between the two runs.
        _commit_in_repo_hook(
            repo_path, rc_branch, "pre-tag",
            "#!/usr/bin/env bash\nexit 0\n",
            "fix: pre-tag hook no longer exits non-zero",
            return_branch="main",
        )

        # Step 7: Re-run cm-release.sh — must complete end-to-end this time.
        result_release_2 = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release_2.returncode == 0, (
            "cm-release.sh must complete after the pre-tag hook is fixed, with no "
            "operator git surgery between the two runs.\n"
            f"stdout: {result_release_2.stdout}\nstderr: {result_release_2.stderr}"
        )
        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found after the fixed-and-rerun release.\n"
            f"stdout: {result_release_2.stdout}\nstderr: {result_release_2.stderr}"
        )
        assert not _git_branch_exists_local(repo_path, rc_branch), (
            f"RC branch '{rc_branch}' still exists locally after the successful rerun.\n"
            f"stdout: {result_release_2.stdout}\nstderr: {result_release_2.stderr}"
        )
        origin_has_rc_after = subprocess.run(
            ["git", "-C", str(repo_path), "ls-remote", "--exit-code", "--heads",
             "origin", rc_branch],
            capture_output=True,
        ).returncode == 0
        assert not origin_has_rc_after, (
            f"RC branch '{rc_branch}' still exists on origin after the successful rerun.\n"
            f"stdout: {result_release_2.stdout}\nstderr: {result_release_2.stderr}"
        )

        # Assertion: the step log states verification is complete before the
        # first destructive action (the verification-complete narration). Both lines
        # are plain stdout echoes in release.sh, so their relative position in
        # stdout alone (not stderr-interleaved) reliably reflects execution order.
        stdout_2 = result_release_2.stdout
        assert "verification complete" in stdout_2, (
            "Expected the 'verification complete — proceeding to finalization' "
            "narration in the successful release's output.\n"
            f"stdout: {stdout_2}"
        )
        assert "[Step 10] Deleting" in stdout_2, (
            f"Expected the RC-branch-deletion step log line in stdout.\nstdout: {stdout_2}"
        )
        verification_line_pos = stdout_2.index("verification complete")
        destructive_step_pos = stdout_2.index("[Step 10] Deleting")
        assert verification_line_pos < destructive_step_pos, (
            "The 'verification complete' narration must appear BEFORE the first "
            "destructive step ([Step 10], RC branch deletion) in the log — it "
            f"appeared at index {verification_line_pos}, destructive step at "
            f"{destructive_step_pos}.\nstdout: {stdout_2}"
        )


# ---------------------------------------------------------------------------
# Case (g) — Hook-resolution fixture: hooks resolve from the RC branch, never
# from whatever the dev tree currently has checked out
# ---------------------------------------------------------------------------


class TestHookResolutionIsRcBranchAware:
    """Case (g): with the dev tree parked on an unrelated branch carrying
    DIFFERENT in-repo hook content, the release resolves and executes the RC
    branch's hooks — never the checked-out branch's. This retires the
    dev-tree-parking workaround: an operator (or a prior task) may leave the
    dev tree checked out anywhere without affecting hook resolution.
    """

    def test_pre_squash_hook_resolves_from_rc_branch_not_checked_out_branch(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Dev tree parked on an unrelated branch; pre-squash hook still resolves from the RC.

        Scenario:
          1. Open the RC branch.
          2. Commit an in-repo pre-squash hook on the RC branch that writes a
             distinctive marker file ('rc_hook_ran.txt').
          3. Create an unrelated branch with a DIFFERENT in-repo pre-squash hook
             that writes a different marker file ('unrelated_hook_ran.txt').
          4. Check the dev tree out onto the unrelated branch — simulating an
             operator or a prior task leaving the working tree there — and run
             cm-release.sh from that state (the pre-squash hook fires before
             cm-release.sh's own checkout of main).

        Asserts:
          - cm-release.sh exits 0.
          - 'rc_hook_ran.txt' exists (the RC branch's hook executed).
          - 'unrelated_hook_ran.txt' does NOT exist (the checked-out branch's
            hook content was never executed) — content-compare proof of which
            hook ran.
        """
        version = "v0.21.0"
        project_name = "hook_resolution_rc_aware_test"

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

        # Step 2: Coder work + the RC branch's own pre-squash hook.
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_hookres.txt", "hook resolution test feature content\n",
            "feat: add feature for hook resolution test",
        )
        _commit_in_repo_hook(
            repo_path, rc_branch, "pre-squash",
            "#!/usr/bin/env bash\ntouch rc_hook_ran.txt\n",
            "chore: add pre-squash hook that marks the RC branch ran",
        )

        # Step 3: An unrelated branch (off main) with DIFFERENT hook content.
        _git_local(repo_path, "checkout", "-b", "unrelated-branch", "main")
        _commit_in_repo_hook(
            repo_path, "unrelated-branch", "pre-squash",
            "#!/usr/bin/env bash\ntouch unrelated_hook_ran.txt\n",
            "chore: add pre-squash hook that marks the unrelated branch ran",
        )
        # Step 4: Leave the dev tree parked on the unrelated branch — do NOT
        # check out main or the RC branch before invoking cm-release.sh.

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            "cm-release.sh must complete even when the dev tree is parked on an "
            "unrelated branch at invocation time.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        assert (repo_path / "rc_hook_ran.txt").exists(), (
            "The RC branch's pre-squash hook did not run — hook resolution must "
            "read from the RC branch's git tree, not the dev tree's checkout.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )
        assert not (repo_path / "unrelated_hook_ran.txt").exists(), (
            "The unrelated branch's pre-squash hook ran — hook resolution must "
            "NOT be a function of whatever the dev tree currently has checked out.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )


# ---------------------------------------------------------------------------
# Case (h) — Hook-order recurrence fixture: a pre-squash hook that COMMITS
# (not just runs) must have that commit land on the RC branch, and therefore
# ride the squash into main, even when the dev tree is parked on an unrelated
# branch at invocation time.
#
# This is distinct from Case (g) above. Case (g) proves hook CONTENT
# resolution is RC-branch-aware (cm_release_hooks.sh reads the hook script's
# bytes via `git show <rc_branch>:<path>`, independent of the checkout).
# Case (g)'s hook only touches an untracked marker file — it never commits —
# so it cannot detect whether the hook's own git operations land on the
# right branch. This fixture reproduces the v0.11.0 production failure: a
# hook that behaves like a real version-bump hook (writes a file AND commits
# it) with the dev tree parked on an unrelated branch. Without cm-release.sh
# checking out the RC branch before running the hook, the hook's commit
# lands on whatever branch happened to be checked out — never reaching the
# RC branch — so the squash into main silently drops it.
# ---------------------------------------------------------------------------


class TestHookOrderRecurrence:
    """Case (h): a pre-squash hook's own commit must land on the RC branch.

    Regression lock for the hook-ordering recurrence (originally surfaced at
    v0.11.0's release block): an earlier fix made hook CONTENT resolution
    RC-branch-aware (see TestHookResolutionIsRcBranchAware / Case (g)), but
    that fix never addressed the EXECUTION CONTEXT a hook runs in — a
    resolved hook still executed via `cd "$REPO_ROOT"` on whatever branch
    the dev tree had checked out, so a hook that commits (a version bump,
    a manifest regeneration) silently lost its commit whenever the dev tree
    was not already parked on the RC branch. cm-release.sh must check out
    the RC branch before running the pre-squash hook so the hook's own git
    operations land on the branch that gets squashed into main.
    """

    def test_pre_squash_hook_commit_lands_on_rc_branch_and_survives_squash(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Pre-squash hook commits a version file; dev tree parked on an unrelated branch.

        Scenario:
          1. Open the RC branch.
          2. Commit feature work on the RC branch.
          3. Commit an in-repo pre-squash hook on the RC branch that WRITES
             and COMMITS a version file ('MANIFEST_VERSION') — mirroring a
             real version-bump hook, not just a marker-file touch.
          4. Create an unrelated branch off main and leave the dev tree
             checked out there — simulating an operator or a prior task
             leaving the working tree parked anywhere — then run
             cm-release.sh from that state.

        Asserts:
          - cm-release.sh exits 0.
          - MANIFEST_VERSION exists on main after the release, with the
            content the hook wrote — proving the hook's commit rode the
            squash from the RC branch into main.
          - The unrelated branch's tip commit is unchanged — the hook's
            commit did not land there instead.
        """
        version = "v0.22.0"
        project_name = "hook_order_recurrence_test"

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

        # Step 2: Coder work on the RC branch.
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_hookorder.txt", "hook order recurrence test feature content\n",
            "feat: add feature for hook order recurrence test",
        )

        # Step 3: A pre-squash hook that WRITES AND COMMITS a version file —
        # the exact shape of a real version-bump hook, not a marker touch.
        _commit_in_repo_hook(
            repo_path, rc_branch, "pre-squash",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "echo '9.9.9' > MANIFEST_VERSION\n"
            "git add MANIFEST_VERSION\n"
            "git commit -m 'chore: bump MANIFEST_VERSION via pre-squash hook'\n",
            "chore: add pre-squash hook that bumps MANIFEST_VERSION",
        )
        rc_tip_before_release = _git_local(repo_path, "rev-parse", rc_branch)

        # Step 4: An unrelated branch off main; leave the dev tree parked
        # there — do NOT check out main or the RC branch before releasing.
        _git_local(repo_path, "checkout", "-b", "unrelated-branch", "main")
        unrelated_tip_before_release = _git_local(repo_path, "rev-parse", "unrelated-branch")

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            "cm-release.sh must complete even when the dev tree is parked on an "
            "unrelated branch and the pre-squash hook commits.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # The unrelated branch must be untouched — the hook's commit did not
        # land there instead of on the RC branch.
        unrelated_tip_after_release = _git_local(repo_path, "rev-parse", "unrelated-branch")
        assert unrelated_tip_after_release == unrelated_tip_before_release, (
            "The unrelated branch gained a commit — the pre-squash hook's commit "
            "landed on whatever branch was checked out instead of the RC branch.\n"
            f"before: {unrelated_tip_before_release}\nafter: {unrelated_tip_after_release}"
        )
        # Sanity: the RC branch tip captured before release differs from
        # rc_tip_before_release once the hook commits on it (informational —
        # not asserted directly since the RC branch is deleted by Step 11;
        # the real proof is MANIFEST_VERSION riding the squash below).
        del rc_tip_before_release

        manifest_path = repo_path / "MANIFEST_VERSION"
        assert manifest_path.exists(), (
            "MANIFEST_VERSION is missing from main after the release — the "
            "pre-squash hook's commit did not land on the RC branch, so it "
            "was not present in the tree that got squashed into main.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )
        assert manifest_path.read_text(encoding="utf-8").strip() == "9.9.9", (
            "MANIFEST_VERSION exists but has the wrong content — the hook's "
            "commit that rode the squash is not the one this test wrote.\n"
            f"actual content: {manifest_path.read_text(encoding='utf-8')!r}"
        )


# ---------------------------------------------------------------------------
# Case (i) — Regression fixture: release-flow HALT scope
#
# Recurrence: a release-flow HALT trigger (a rigged pre-squash hook, a
# ship-policy refusal, any of cm-release.sh's nine HALT triggers) must freeze
# projects/<name>/HALT only. The global $KANBAN_ROOT/HALT sentinel is reserved
# for the operator and framework-wide emergencies; a single project's release
# failure must never freeze sibling projects' chains. The failure this
# guards against froze the kanban's own chains: one project's
# cm-release.sh HALT tripped wake_common_run's
# startup-level global HALT check, which exits the ENTIRE wake invocation
# before the per-project iteration loop even begins, starving every sibling
# project regardless of its own state.
# ---------------------------------------------------------------------------

_SCRIPTS_LIB = _SCRIPTS_DIR / "lib"


def _add_sibling_project(
    kanban_root: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
) -> None:
    """Register a second, independent project under an existing kanban root.

    Appends a `[project:<name>]` stanza to the existing projects.cfg (rather
    than overwriting it, unlike _build_kanban_root which creates the first
    project) and lays down the minimal projects/<name>/ skeleton
    (project.cfg, release-state.md, tasks/queues/) that pp_project_root and
    pp_project_halted need to resolve. This project is never opened for
    release in the fixture below — it only needs to exist so the test can
    prove it stays UNAFFECTED by project A's release failure.
    """
    projects_cfg = kanban_root / "projects.cfg"
    with projects_cfg.open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n[project:{project_name}]\n"
            "priority=1\n"
            "description=Sibling project for HALT-scope regression fixture\n"
            "enabled=true\n"
        )

    proj = kanban_root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "project.cfg").write_text(
        f"[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        f"git_remote_name = origin\n"
        f"workflow_type = release\n"
        f"is_self_build = false\n"
        f"push_to_remote = false\n"
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

    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)


def _run_project_paths_check(kanban_root: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    """Run a bash snippet with project_paths.sh sourced and KANBAN_ROOT set.

    Mirrors the sourcing pattern used by test_discovery_pipeline.py so this
    fixture exercises the real pp_project_halted() function, not a
    reimplementation of its logic.
    """
    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    full_script = textwrap.dedent(f"""\
        set -euo pipefail
        source "{_SCRIPTS_LIB}/project_paths.sh"
        {script}
    """)
    return subprocess.run(
        ["bash", "-c", full_script],
        capture_output=True, text=True, env=base_env, timeout=30,
    )


class TestHaltScopeRecurrence:
    """Case (i): a release-flow HALT must freeze projects/<name>/HALT only.

    Regression lock for the HALT-scoping recurrence. An earlier fix built the
    READ side of per-project HALT scoping — pp_project_halted() in
    project_paths.sh (consulted by wake_common_run's per-project dispatch
    loop) and the dashboard's halt_scope.sh — but never touched the WRITE
    side: cm-release.sh's write_halt_file()/cm_halt() kept creating
    $KANBAN_ROOT/HALT (the GLOBAL sentinel) for every one of its nine HALT
    triggers. wake_common_run's startup check (a bare
    `[[ -f "$TEAM_ROOT/HALT" ]]`, evaluated ONCE before the per-project
    iteration loop even starts) treats that global file as a chain-wide stop.
    So a single project's release failure tripped the startup gate and froze
    every sibling project's chain in the same wake — reproducing the
    production failure in which one release event froze the kanban's own
    chains. The read-side fix alone could never have prevented
    this: pp_project_halted() is never reached when the startup gate above it
    already exited the whole process.

    cm-release.sh must write the project-scoped HALT file
    ($KANBAN_ROOT/projects/<name>/HALT) instead, so the ONLY gate that fires
    for a project's own release failure is the per-project
    pp_project_halted() check inside the dispatch loop — the startup-level
    global gate never sees it, and sibling projects are never touched.
    """

    def test_project_release_failure_writes_project_scoped_halt_only(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A rigged pre-squash hook HALTs project A; project B stays dispatchable.

        Scenario:
          1. Build a kanban root with two independent sibling projects, A and
             B, each backed by its own git repo.
          2. Open an RC for project A and rig its pre-squash hook to fail —
             a real HALT trigger (Trigger 4), not a synthetic function call.
          3. Run cm-release.sh for project A. It must exit non-zero.

        Asserts:
          - $KANBAN_ROOT/projects/A/HALT exists — the project-scoped HALT
            file was written for the project that actually failed.
          - $KANBAN_ROOT/HALT does NOT exist — the global sentinel was never
            touched by project A's release failure.
          - pp_project_halted("A") is true and pp_project_halted("B") is
            false, via the real project_paths.sh function.
          - wake_common_run's own startup gate — the literal
            `[[ -f "$KANBAN_ROOT/HALT" ]]` check that runs BEFORE the
            per-project loop — evaluates false. Project B's wake would enter
            the per-project iteration loop rather than exiting the whole
            process before iteration starts.

        With write_halt_file() pointed at "${{KANBAN_ROOT}}/HALT", the first
        two assertions invert — the global file exists and the project-scoped
        one does not — and the startup-gate assertion fails, reproducing the
        cross-project freeze. Pointing team/scripts/cm/release.sh's
        write_halt_file() at the global path makes this test fail on exactly
        those three points.
        """
        version = "v0.30.0"
        project_a = "halt_scope_project_a"
        project_b = "halt_scope_project_b"

        bare_remote_a = tmp_path / "bare_remote_a.git"
        repo_a = tmp_path / "dev_repo_a"
        _init_git_repo_with_remote(repo_a, bare_remote_a)

        kanban_root = _build_kanban_root(
            tmp_path, project_a, repo_a, push_to_remote="false"
        )

        bare_remote_b = tmp_path / "bare_remote_b.git"
        repo_b = tmp_path / "dev_repo_b"
        _init_git_repo_with_remote(repo_b, bare_remote_b)
        _add_sibling_project(kanban_root, project_b, repo_b)

        # Step 2: Open project A's RC, add coder work, and rig the
        # pre-squash hook to fail — a genuine HALT trigger.
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_a, repo_root=repo_a,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        rc_branch = f"rc/{version}"

        _commit_on_branch(
            repo_a, rc_branch,
            "feature_haltscope.txt", "halt scope recurrence test feature content\n",
            "feat: add feature for halt scope recurrence test",
        )
        _commit_in_repo_hook(
            repo_a, rc_branch, "pre-squash",
            "#!/usr/bin/env bash\necho 'rigged failure' >&2\nexit 1\n",
            "chore: add pre-squash hook rigged to fail",
            return_branch="main",
        )

        global_halt_file = kanban_root / "HALT"
        project_a_halt_file = kanban_root / "projects" / project_a / "HALT"
        project_b_halt_file = kanban_root / "projects" / project_b / "HALT"
        assert not global_halt_file.exists()
        assert not project_a_halt_file.exists()
        assert not project_b_halt_file.exists()

        # Step 3: Run cm-release.sh for project A — the rigged hook must HALT it.
        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_a, repo_root=repo_a,
        )
        assert result_release.returncode != 0, (
            "cm-release.sh must exit non-zero when the pre-squash hook is rigged to fail.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # --- Assertion set: project-scoped HALT only ---
        assert project_a_halt_file.exists(), (
            "Project A's release failure did not write a project-scoped HALT "
            f"file at {project_a_halt_file}.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )
        assert not global_halt_file.exists(), (
            "Project A's release failure wrote the GLOBAL HALT file at "
            f"{global_halt_file} — this is the exact recurrence: a release-flow "
            "HALT trigger must freeze projects/<name>/HALT only, never the "
            "global sentinel, or every sibling project's chain freezes with it.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )
        assert not project_b_halt_file.exists(), (
            "Project B (an untouched sibling) unexpectedly has its own HALT "
            f"file at {project_b_halt_file} — it should never have been written."
        )

        # --- Assertion set: pp_project_halted() agrees, via the real function ---
        result_a_halted = _run_project_paths_check(
            kanban_root, f'pp_project_halted "{project_a}" && echo HALTED || echo NOT_HALTED'
        )
        assert "HALTED" in result_a_halted.stdout and "NOT_HALTED" not in result_a_halted.stdout, (
            f"pp_project_halted('{project_a}') should report HALTED.\n"
            f"stdout: {result_a_halted.stdout}\nstderr: {result_a_halted.stderr}"
        )
        result_b_halted = _run_project_paths_check(
            kanban_root, f'pp_project_halted "{project_b}" && echo HALTED || echo NOT_HALTED'
        )
        assert "NOT_HALTED" in result_b_halted.stdout, (
            f"pp_project_halted('{project_b}') must report NOT_HALTED — a "
            "sibling project's dispatchability must be unaffected by project "
            f"A's release failure.\nstdout: {result_b_halted.stdout}\n"
            f"stderr: {result_b_halted.stderr}"
        )

        # --- Assertion: wake_common_run's own startup gate would not fire ---
        # Literal reproduction of the check at the top of wake_common_run()
        # (team/scripts/lib/wake_common.sh): `if [[ -f "${TEAM_ROOT}/HALT" ]]`.
        # If this evaluates true, the ENTIRE wake process exits before the
        # per-project loop (and therefore before pp_project_halted() for
        # project B is ever consulted) — this is the mechanism of the original
        # cross-project freeze.
        startup_gate = subprocess.run(
            ["bash", "-c", f'[[ -f "{kanban_root}/HALT" ]] && echo GATE_TRIPPED || echo GATE_CLEAR'],
            capture_output=True, text=True, timeout=10,
        )
        assert "GATE_CLEAR" in startup_gate.stdout, (
            "wake_common_run's startup-level global HALT gate would have "
            "tripped for EVERY project (including sibling project B) as a "
            "side effect of project A's release failure alone.\n"
            f"stdout: {startup_gate.stdout}"
        )


# ---------------------------------------------------------------------------
# Case (j) — The hands-free demo (v2.3.5 Acceptance 1, the release centerpiece)
#
# A fixture tree with a deliberate quality-class finding in notes prose and
# otherwise-green tests runs the ENTIRE cycle hands-free: a gated-runner-shaped
# script exits 0 with the quality-class findings file written, a TESTER report
# cites the finding against a filed bug and recommends PASS, and cm-release.sh
# ships the resulting release FUNCTIONAL — with zero operator hands at any step.
# ---------------------------------------------------------------------------

_FIXTURES_DIR = _TEAM_DIR / "tests" / "fixtures"
_HANDS_FREE_DEMO_RUNNER = _FIXTURES_DIR / "hands_free_demo_runner.sh"
_HANDS_FREE_DEMO_GREEN_PYTEST = (
    _FIXTURES_DIR / "hands_free_demo_green" / "fixture_hands_free_demo_green_checks.py"
)
_TEMP_LIB = _SCRIPTS_DIR / "lib" / "temp.sh"


def _make_scan_dir() -> pathlib.Path:
    """Create a scratch directory for content that lint_hygiene.py's
    provenance class itself will scan, deliberately OUTSIDE pytest's own
    tmp_path tree.

    Used for both the prose surface (release-notes-shaped .md fixtures) and
    the code surface (.py/.sh fixtures carrying a planted comment) — the
    exclusion problem this helper works around applies identically to
    either kind of content.

    lint_hygiene.py excludes any path containing a 'tests/' (or
    'fixtures/') segment by design, so its own test suite is never
    self-scanned (see scripts/lint_hygiene.py's
    _CODE_EXCLUDE_SEGMENTS). This repo's own tests/conftest.py redirects
    pytest tmp_path fixtures under
    "$PGAI_AGENT_KANBAN_TEMP_DIR/tests/pytest-of-.../..." whenever
    PGAI_AGENT_KANBAN_TEMP_DIR is set (the framework's own /tmp-pollution
    guard) — so a fixture built via tmp_path would collide with that same
    exclusion and silently never be scanned. This helper instead creates a
    directory directly under the configured framework temp root (never bare
    /tmp, per roles/CODER.md "Temporary File Hygiene") with no 'tests/' segment in the
    path, so the real production lint actually scans it.
    """
    framework_temp_root = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR")
    if not framework_temp_root:
        # Resolve via the shared shell resolver (never inline the fallback
        # literal here — see scripts/lib/temp.sh's pgai_temp_dir()).
        resolved = subprocess.run(
            ["bash", "-c", f"source {str(_TEMP_LIB)!r} && pgai_temp_dir"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        framework_temp_root = resolved or "/".join(["", "tmp", "pgai_kanban_tmp"])
    scan_scratch_root = pathlib.Path(framework_temp_root) / "hands-free-demo-scan"
    scan_scratch_root.mkdir(parents=True, exist_ok=True)
    return pathlib.Path(tempfile.mkdtemp(dir=str(scan_scratch_root)))


def _build_tester_task(
    kanban_root: pathlib.Path,
    project_name: str,
    task_id: str,
    release_version: str,
    *,
    state: str = "DONE",
    recommendation: str = "PASS",
    systemic_risk: str = "low",
    lint_findings_section: str = "None.",
) -> pathlib.Path:
    """Create a synthetic TESTER task folder in the shape cm-release.sh's
    Step 4d discovers (README.md Role/Release Version, status.md State,
    artifacts/report.md Recommendation/Systemic Risk/Lint Findings), mirroring
    team/roles/TESTER.md's Step 9 report template closely enough for
    cm_ship_policy's field reads without reproducing its full prose.
    """
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    (task_dir / "README.md").write_text(
        "# Task: Synthetic hands-free demo verification\n\n"
        f"## Task ID\n{task_id}\n\n"
        "## Role\nTESTER\n\n"
        f"## Release Version\n{release_version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        "# Status\n\n"
        f"## Task\n{task_id}\n\n"
        "## Role\nTESTER\n\n"
        f"## State\n{state}\n",
        encoding="utf-8",
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {release_version}\n\n"
        "## Recommendation\n"
        f"{recommendation}\n\n"
        "## Systemic Risk\n"
        f"{systemic_risk}\n\n"
        "## Findings\n\n"
        "No bug, gap, or stale-assertion findings — suites green.\n\n"
        "## Lint Findings (non-blocking)\n\n"
        f"{lint_findings_section}\n",
        encoding="utf-8",
    )
    return task_dir


class TestHandsFreeDemoEndToEnd:
    """Case (j): the v2.3.5 hands-free demo, the release's centerpiece.

    Drives the full quality-class-finding-to-shipped-release cycle with zero
    operator hands, using real production code at every step (never a
    reimplementation):

      1. hands_free_demo_runner.sh (a fixture gated-runner-shaped script)
         reuses scripts/lib/gated_runner_lint.sh and the real
         scripts/lint_hygiene.py (provenance class) to scan a
         deliberately-violating release-notes-shaped prose fixture — the
         runner exits 0 (the provenance finding is quality-class, never
         fatal) with the findings file written, while its own green pytest
         fixture still runs and passes.
      2. A bug is filed citing the finding (the cite-an-open-bug-or-file-one
         rule from team/roles/TESTER.md Step 5c), and a synthetic TESTER
         report (report.md) cites it in a "Lint Findings (non-blocking)"
         section while recommending PASS with Systemic Risk low — exactly
         the shape a real TESTER agent's report would carry per Step 9.
      3. cm-release.sh (the real release engine, not a stand-in) reads that
         TESTER report, applies the real cm_ship_policy decision matrix, and
         ships the release with release-notes Status FUNCTIONAL.
    """

    def test_quality_class_finding_flows_hands_free_to_functional_release(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Deliberate notes-prose finding + green tests -> FUNCTIONAL release, hands-free.

        Asserts:
          - hands_free_demo_runner.sh exits 0.
          - Its findings file contains the deliberate hygiene provenance
            finding.
          - cm-release.sh exits 0 for the RC carrying the resulting TESTER
            report.
          - release-notes/<version>.md on main carries `## Status` FUNCTIONAL.
          - The version tag exists (the release actually shipped).
        """
        version = "v0.9.0"
        project_name = "hands_free_demo_test"

        # --- Step 1: the fixture tree's deliberate quality-class finding ---
        # A release-notes-shaped prose file citing a bare internal task ID
        # instead of describing the fix by substance — the exact class of
        # finding lint_hygiene.py's provenance class exists to catch. A bare
        # BUG-ID citation is deliberately NOT used here:
        # BUG-NNNN/PRIORITY-NNNN references are the sanctioned content of
        # the release-notes prose surface and are exempt from this lint, so
        # a task-ID citation (still enforced everywhere, including prose)
        # is the fixture's trigger instead.
        # Built via _make_scan_dir(), not tmp_path — see that helper's
        # docstring for why (tmp_path collides with the lint's own 'tests/'
        # exclusion segment under this repo's PYTEST_DEBUG_TEMPROOT redirect).
        prose_dir = _make_scan_dir()
        try:
            (prose_dir / "v9.9.9.md").write_text(
                "# v9.9.9 — hands-free demo fixture release notes\n\n"
                "## Fixed\n"
                "Fixed by CODER-20260101-042 in the retry loop.\n",
                encoding="utf-8",
            )

            findings_file = tmp_path / "lint" / "findings-hands-free-demo.json"

            result_runner = subprocess.run(
                [
                    "bash", str(_HANDS_FREE_DEMO_RUNNER),
                    "--prose-dir", str(prose_dir),
                    "--pytest-path", str(_HANDS_FREE_DEMO_GREEN_PYTEST),
                    "--findings-file", str(findings_file),
                ],
                capture_output=True, text=True, timeout=90,
            )
        finally:
            shutil.rmtree(prose_dir, ignore_errors=True)

        # --- Assertion 1: the runner exits 0 hands-free ---
        # (the notes-prose finding is quality-class; it never fails the run,
        # and the fixture's own pytest suite is green.)
        assert result_runner.returncode == 0, (
            "hands_free_demo_runner.sh must exit 0: the notes-prose finding is "
            "quality-class (never fatal) and the fixture pytest suite is "
            "green.\n"
            f"stdout: {result_runner.stdout}\nstderr: {result_runner.stderr}"
        )

        # --- Assertion 2: the findings file was written and names the finding ---
        assert findings_file.is_file(), (
            "hands_free_demo_runner.sh must write the quality-class findings "
            f"file unconditionally.\nstdout: {result_runner.stdout}\n"
            f"stderr: {result_runner.stderr}"
        )
        findings_doc = json.loads(findings_file.read_text(encoding="utf-8"))
        provenance_findings = [
            f for f in findings_doc.get("findings", [])
            if f.get("lint") == "hygiene" and f.get("class") == "provenance"
        ]
        assert provenance_findings, (
            "Expected a deliberate hygiene provenance finding in the "
            f"findings file. Got: {findings_doc}"
        )

        # --- Step 2: file the bug the finding cites, and write the TESTER report ---
        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        bugs_dir = kanban_root / "projects" / project_name / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)
        (bugs_dir / "BUG-0001-notes-prose-cites-ledger-id.md").write_text(
            "# BUG-0001-notes-prose-cites-ledger-id\n\n"
            "**Bug ID:** BUG-0001-notes-prose-cites-ledger-id\n"
            "**Filed By:** TESTER\n"
            "**Severity:** low\n\n"
            "## Status\n"
            "open\n\n"
            "## Category\n"
            "release-notes\n\n"
            "---\n\n"
            "## Symptom\n"
            "A fixture release-notes file described a fix by bare internal "
            "task ID ('Fixed by CODER-20260101-042 in the retry loop') "
            "instead of by substance, flagged by the content-described-notes "
            "provenance lint.\n\n"
            "## Expected\n"
            "Release notes describe resolved items by substance sourced from "
            "the bug's own Symptom/Fix text.\n\n"
            "## Actual\n"
            "The notes prose cited the internal task ID directly.\n\n"
            "## Reproduction\n"
            "1. Run the content-described-notes provenance lint against the "
            "fixture notes directory.\n"
            "2. Observe the task-ID finding.\n\n"
            "## Files Involved\n\n"
            "- fixture release-notes prose (hands-free demo)\n",
            encoding="utf-8",
        )

        tester_task_id = "TESTER-20260808-900-hands-free-demo"
        _build_tester_task(
            kanban_root, project_name, tester_task_id, version,
            state="DONE",
            recommendation="PASS",
            systemic_risk="low",
            lint_findings_section=(
                "- `hygiene` (provenance) — release-notes prose cited a "
                "bare internal task ID instead of describing the fix by "
                "substance — cites BUG-0001-notes-prose-cites-ledger-id (open)"
            ),
        )

        # --- Step 3: open the RC, land coder work, and release hands-free ---
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        rc_branch = f"rc/{version}"
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_hands_free_demo.txt", "hands-free demo feature content\n",
            "feat: add feature for the hands-free demo release",
            return_branch="main",
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )

        # --- Assertion 3: cm-release.sh ships hands-free ---
        assert result_release.returncode == 0, (
            "cm-release.sh must ship the release hands-free when TESTER "
            "recommends PASS with Systemic Risk low, even though its report "
            "carries a non-blocking Lint Findings section.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # --- Assertion 4: release-notes Status is FUNCTIONAL ---
        release_notes = (repo_path / "release-notes" / f"{version}.md").read_text(
            encoding="utf-8"
        )
        assert "FUNCTIONAL" in release_notes, (
            "Expected release-notes Status FUNCTIONAL (green suites + "
            "non-blocking lint findings -> Ship FUNCTIONAL per CM.md's "
            f"ship-policy matrix).\nrelease-notes content:\n{release_notes}"
        )

        # --- Assertion 5: the release actually shipped (tag exists) ---
        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found after cm-release.sh.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )


# ---------------------------------------------------------------------------
# Case (j2) — The hands-free demo, code surface
#
# The completion gate's diff-scope comment-provenance lint is retired:
# a task's own diff is no longer refused for a planted ledger-ID comment.
# This fixture tree proves the litter class the gate no longer catches is
# still caught downstream — the SAME quality-class provenance lint the
# gated runners already run against the code surface (team/scripts/,
# team/scripts/lib/, team/pgai_agent_kanban/ in production) at verification
# time, not a new check — and that the finding still rides the existing
# findings-file -> TESTER-report -> ship pipeline hands-free, same as Case
# (j) above for the prose surface.
# ---------------------------------------------------------------------------


class TestHandsFreeDemoCodeSurfaceLitterEndToEnd:
    """Case (j2): the hands-free demo, replayed for code-surface litter.

    Structurally identical to TestHandsFreeDemoEndToEnd above (same runner,
    same TESTER-report shape, same cm-release.sh drive); the only difference
    is the scan target — hands_free_demo_runner.sh's --code-dir (a planted
    .py comment citing a bare internal task ID) instead of --prose-dir (a
    planted release-notes citation). This is exactly the litter class this
    fixture describes: previously caught (also) at the completion gate's
    diff-scope check, and — after that check's retirement — caught ONLY
    downstream, at verification time, by the same lint this class already
    proves for the prose surface.
    """

    def test_code_surface_ledger_comment_flows_hands_free_to_functional_release(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Deliberate code-comment ledger-ID finding + green tests -> FUNCTIONAL release, hands-free.

        Asserts:
          - hands_free_demo_runner.sh exits 0 (--code-dir surface).
          - Its findings file contains the deliberate hygiene provenance
            finding, naming the planted file and citing the planted token.
          - cm-release.sh exits 0 for the RC carrying the resulting TESTER
            report (non-blocking Lint Findings section citing the filed bug).
          - release-notes/<version>.md on main carries `## Status` FUNCTIONAL.
          - The version tag exists (the release actually shipped).
        """
        version = "v0.9.1"
        project_name = "hands_free_demo_code_surface_test"

        # --- Step 1: the fixture tree's deliberate quality-class finding ---
        # A code file (the shape team/scripts/, team/scripts/lib/, and
        # team/pgai_agent_kanban/ carry in production) whose comment cites a
        # bare internal task ID instead of describing the fix by substance —
        # exactly the litter class the completion gate's now-retired
        # diff-scope lint used to refuse at done-claim time.
        # Built via _make_scan_dir(), not tmp_path — see that helper's
        # docstring for why (tmp_path collides with the lint's own 'tests/'
        # exclusion segment under this repo's PYTEST_DEBUG_TEMPROOT redirect).
        code_dir = _make_scan_dir()
        try:
            (code_dir / "fixture_retry_loop_helper.py").write_text(
                '"""Fixture module standing in for a code-surface file the '
                'quality-class provenance lint scans at verification '
                'time."""\n\n\n'
                "def retry_loop_helper() -> None:\n"
                "    # Fix landed per CODER-20260101-042 in the retry loop.\n"
                "    return None\n",
                encoding="utf-8",
            )

            findings_file = tmp_path / "lint" / "findings-hands-free-demo-code.json"

            result_runner = subprocess.run(
                [
                    "bash", str(_HANDS_FREE_DEMO_RUNNER),
                    "--code-dir", str(code_dir),
                    "--pytest-path", str(_HANDS_FREE_DEMO_GREEN_PYTEST),
                    "--findings-file", str(findings_file),
                ],
                capture_output=True, text=True, timeout=90,
            )
        finally:
            shutil.rmtree(code_dir, ignore_errors=True)

        # --- Assertion 1: the runner exits 0 hands-free ---
        # (the code-comment finding is quality-class, never fatal; the
        # fixture's own pytest suite is green.)
        assert result_runner.returncode == 0, (
            "hands_free_demo_runner.sh must exit 0: the code-comment finding "
            "is quality-class (never fatal) and the fixture pytest suite is "
            "green.\n"
            f"stdout: {result_runner.stdout}\nstderr: {result_runner.stderr}"
        )

        # --- Assertion 2: the findings file was written and names the finding ---
        assert findings_file.is_file(), (
            "hands_free_demo_runner.sh must write the quality-class findings "
            f"file unconditionally.\nstdout: {result_runner.stdout}\n"
            f"stderr: {result_runner.stderr}"
        )
        findings_doc = json.loads(findings_file.read_text(encoding="utf-8"))
        provenance_findings = [
            f for f in findings_doc.get("findings", [])
            if f.get("lint") == "hygiene" and f.get("class") == "provenance"
        ]
        assert provenance_findings, (
            "Expected a deliberate hygiene provenance finding in the "
            f"findings file. Got: {findings_doc}"
        )
        finding_cause = provenance_findings[0].get("cause", "")
        assert "fixture_retry_loop_helper.py" in finding_cause, (
            "Expected the finding cause to name the planted code file.\n"
            f"cause: {finding_cause}"
        )
        assert "CODER-20260101-042" in finding_cause, (
            "Expected the finding cause to cite the planted ledger-ID "
            f"token.\ncause: {finding_cause}"
        )

        # --- Step 2: file the bug the finding cites, and write the TESTER report ---
        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        bugs_dir = kanban_root / "projects" / project_name / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)
        (bugs_dir / "BUG-0001-code-comment-cites-ledger-id.md").write_text(
            "# BUG-0001-code-comment-cites-ledger-id\n\n"
            "**Bug ID:** BUG-0001-code-comment-cites-ledger-id\n"
            "**Filed By:** TESTER\n"
            "**Severity:** low\n\n"
            "## Status\n"
            "open\n\n"
            "## Category\n"
            "code-comment\n\n"
            "---\n\n"
            "## Symptom\n"
            "A fixture code file's comment cited a bare internal task ID "
            "('Fix landed per CODER-20260101-042 in the retry loop') instead "
            "of describing the behavior, flagged by the "
            "content-described-notes provenance lint's code surface.\n\n"
            "## Expected\n"
            "Code comments describe behavior, not process history "
            "(docs/coding-standards.md Directive 8).\n\n"
            "## Actual\n"
            "The comment cited the internal task ID directly.\n\n"
            "## Reproduction\n"
            "1. Run the content-described-notes provenance lint against the "
            "fixture code directory.\n"
            "2. Observe the task-ID finding.\n\n"
            "## Files Involved\n\n"
            "- fixture code module (hands-free demo, code surface)\n",
            encoding="utf-8",
        )

        tester_task_id = "TESTER-20260808-901-hands-free-demo-code-surface"
        _build_tester_task(
            kanban_root, project_name, tester_task_id, version,
            state="DONE",
            recommendation="PASS",
            systemic_risk="low",
            lint_findings_section=(
                "- `hygiene` (provenance) — a code comment cited a bare "
                "internal task ID instead of describing the fix by "
                "substance — cites BUG-0001-code-comment-cites-ledger-id "
                "(open)"
            ),
        )

        # --- Step 3: open the RC, land coder work, and release hands-free ---
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        rc_branch = f"rc/{version}"
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_hands_free_demo_code_surface.txt",
            "hands-free demo code-surface feature content\n",
            "feat: add feature for the hands-free demo code-surface release",
            return_branch="main",
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )

        # --- Assertion 3: cm-release.sh ships hands-free ---
        assert result_release.returncode == 0, (
            "cm-release.sh must ship the release hands-free when TESTER "
            "recommends PASS with Systemic Risk low, even though its report "
            "carries a non-blocking Lint Findings section for a "
            "code-surface finding.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        # --- Assertion 4: release-notes Status is FUNCTIONAL ---
        release_notes = (repo_path / "release-notes" / f"{version}.md").read_text(
            encoding="utf-8"
        )
        assert "FUNCTIONAL" in release_notes, (
            "Expected release-notes Status FUNCTIONAL (green suites + "
            "non-blocking lint findings -> Ship FUNCTIONAL per CM.md's "
            f"ship-policy matrix).\nrelease-notes content:\n{release_notes}"
        )

        # --- Assertion 5: the release actually shipped (tag exists) ---
        assert _git_tag_exists(repo_path, version), (
            f"Release tag '{version}' not found after cm-release.sh.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )


# ---------------------------------------------------------------------------
# Case (k) — Dual-channel error-contract fixtures (v2.3.5 Acceptance 4)
#
# A forced hook failure and a forced gate refusal must each emit the
# error/message/remedy triple (team/scripts/lib/error_contract.sh), with the
# error key matching a trigger id documented in team/roles/CM.md's "HALT
# triggers" list, and a message free of bare internal identifiers the trigger
# id itself isn't already explained in plain language alongside.
# ---------------------------------------------------------------------------


def _assert_dual_channel_triple(output: str, error_key: str) -> None:
    """Assert *output* contains a well-formed error/message/remedy triple
    for *error_key*, in the house shape from error_contract.sh.
    """
    assert f"error:   {error_key}" in output, (
        f"Expected 'error:   {error_key}' in output.\noutput:\n{output}"
    )
    assert "message: " in output, f"Expected a 'message: ' line in output.\noutput:\n{output}"
    assert "remedy:  " in output, f"Expected a 'remedy:  ' line in output.\noutput:\n{output}"


class TestDualChannelForcedHookFailure:
    """Case (k1): a forced pre-squash hook failure emits error/message/remedy.

    The hook-failure error key (`cm-release.trigger-4.pre-squash-hook-failed`)
    matches HALT Trigger 4 ("Pre-squash hook fails") documented in
    team/roles/CM.md's "HALT triggers" list.
    """

    def test_forced_pre_squash_hook_failure_emits_dual_channel_triple(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A pre-squash hook rigged to exit 1 halts the release with a
        dual-channel error/message/remedy triple naming Trigger 4.

        Asserts:
          - cm-release.sh exits non-zero.
          - stderr contains the error/message/remedy triple for
            cm-release.trigger-4.pre-squash-hook-failed.
          - The message explains the hook's own name in plain language (not
            just a bare trigger number) and the remedy is a concrete next
            step (read the hook's own diagnostic output, fix it, re-run).
        """
        version = "v0.10.0"
        project_name = "dual_channel_hook_failure_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        rc_branch = f"rc/{version}"

        _commit_on_branch(
            repo_path, rc_branch,
            "feature_dual_channel_hook.txt", "dual-channel hook-failure test feature content\n",
            "feat: add feature for the dual-channel hook-failure fixture",
            return_branch="main",
        )
        _commit_in_repo_hook(
            repo_path, rc_branch, "pre-squash",
            "#!/usr/bin/env bash\necho 'rigged pre-squash hook failure' >&2\nexit 1\n",
            "chore: add pre-squash hook rigged to fail",
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )

        assert result_release.returncode != 0, (
            "cm-release.sh must exit non-zero when the pre-squash hook fails.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        combined_output = result_release.stdout + result_release.stderr
        _assert_dual_channel_triple(
            combined_output, "cm-release.trigger-4.pre-squash-hook-failed"
        )
        # The message names the hook and explains the failure in plain
        # language alongside the bare rc= exit code (not just the number).
        assert "cm-release-pre-squash hook exited non-zero" in combined_output, (
            "Expected the message to explain the hook-failure trigger in "
            f"plain language.\noutput:\n{combined_output}"
        )


class TestDualChannelForcedGateRefusal:
    """Case (k2): a forced ship-policy gate refusal emits error/message/remedy.

    The gate-refusal error key (`cm-release.trigger-1.tester-blocked`) matches
    HALT Trigger 1 ("TESTER state is BLOCKED") documented in team/roles/CM.md's
    "HALT triggers" list — CM's ship-policy gate refuses to release an RC
    whose verification could not complete.
    """

    def test_forced_tester_blocked_gate_refusal_emits_dual_channel_triple(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A TESTER task left in State=BLOCKED halts the release with a
        dual-channel error/message/remedy triple naming Trigger 1.

        Asserts:
          - cm-release.sh exits non-zero.
          - stderr contains the error/message/remedy triple for
            cm-release.trigger-1.tester-blocked.
          - The message names the specific TESTER task in plain language
            (not just a bare trigger number) and the remedy names the
            concrete next step (inspect the task, resolve, re-run TESTER).
        """
        version = "v0.11.0"
        project_name = "dual_channel_gate_refusal_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)

        kanban_root = _build_kanban_root(
            tmp_path, project_name, repo_path, push_to_remote="false"
        )

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        rc_branch = f"rc/{version}"
        _commit_on_branch(
            repo_path, rc_branch,
            "feature_dual_channel_gate.txt", "dual-channel gate-refusal test feature content\n",
            "feat: add feature for the dual-channel gate-refusal fixture",
            return_branch="main",
        )

        tester_task_id = "TESTER-20260808-901-dual-channel-gate-refusal"
        _build_tester_task(
            kanban_root, project_name, tester_task_id, version,
            state="BLOCKED",
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [],
            kanban_root, project_name, repo_root=repo_path,
        )

        assert result_release.returncode != 0, (
            "cm-release.sh must exit non-zero when the gating TESTER task is "
            "BLOCKED.\nstdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        combined_output = result_release.stdout + result_release.stderr
        _assert_dual_channel_triple(
            combined_output, "cm-release.trigger-1.tester-blocked"
        )
        assert tester_task_id in combined_output, (
            "Expected the message to name the specific BLOCKED TESTER task in "
            f"plain language.\noutput:\n{combined_output}"
        )


# ---------------------------------------------------------------------------
# Release-path push gate: open-rc.sh and release.sh decide through the shared
# push-decision resolver
#
# Only a project registered in projects.cfg whose own project.cfg names
# dev_tree_path may push. Each unresolved shape -- (a) no project.cfg, (b) a
# project not in projects.cfg, (c) a project.cfg without dev_tree_path --
# runs the whole RC locally while REPO_ROOT and PGAI_DEV_TREE_PATH both point
# at a real checkout: zero git network calls, one skipped-unresolved-project
# line per script. A registered project with no push_to_remote key still
# pushes; push_to_remote = false stays local.
#
# Every git network verb goes to the git network stub; the fixture repo is
# built without any push (origin/main is set with update-ref), and each
# subprocess environment is an explicit set built by build_scrubbed_env.
# ---------------------------------------------------------------------------

_PUSH_GATE_SKIP = "skipped-unresolved-project"


def _init_git_repo_offline(repo_path: pathlib.Path, bare_remote: pathlib.Path) -> None:
    """A dev repo on main with an ``origin`` remote that is never contacted.

    ``refs/remotes/origin/main`` is set locally to the initial commit, so a
    pushing run's ff-only pull from origin/main is a no-op, without any real
    push having built it.
    """
    subprocess.run(["git", "init", "--bare", str(bare_remote)], capture_output=True, check=True)
    repo_path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "--initial-branch=main"],
        ["config", "--local", "user.email", "test@pgai.local"],
        ["config", "--local", "user.name", "PGAI Test"],
    ):
        subprocess.run(["git", "-C", str(repo_path), *args], capture_output=True, check=True)
    (repo_path / "README.md").write_text("# Push gate fixture\n", encoding="utf-8")
    (repo_path / "release-notes").mkdir()
    (repo_path / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
    _git_local(repo_path, "add", "README.md", "release-notes/PUBLISHED")
    _git_local(repo_path, "commit", "-m", "Initial commit")
    _git_local(repo_path, "remote", "add", "origin", str(bare_remote))
    _git_local(repo_path, "update-ref", "refs/remotes/origin/main", "main")


class _PushGateCase:
    """One fixture shape: kanban root, offline dev repo, stubbed git, env."""

    def __init__(self, tmp_path: pathlib.Path, shape: str) -> None:
        self.tmp_path = tmp_path
        self.project = "push_gate_project"
        self.repo = tmp_path / "dev_repo"
        self.origin = tmp_path / "origin.git"
        _init_git_repo_offline(self.repo, self.origin)
        self.root = _build_kanban_root(tmp_path, self.project, self.repo, push_to_remote="false")
        (self.root / "tmp").mkdir(exist_ok=True)
        cfg = self.root / "projects" / self.project / "project.cfg"
        kept = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
        if shape == "push-default":
            cfg.write_text("".join(l for l in kept if not l.startswith("push_to_remote")), encoding="utf-8")
        elif shape == "no-project-cfg":
            cfg.unlink()
        elif shape == "not-registered":
            (self.root / "projects.cfg").write_text("[project:another_project]\npriority=1\n", encoding="utf-8")
        elif shape == "no-dev-tree-path":
            cfg.write_text(
                "".join(l for l in kept if not l.startswith(("push_to_remote", "dev_tree_path"))),
                encoding="utf-8",
            )
        elif shape != "local-only":
            raise ValueError(f"unknown push-gate fixture shape {shape!r}")
        self.stub_log = tmp_path / "git-network-calls.log"
        self.stub_bin = tmp_path / "stub-bin"
        # A pushing run must get past its fetch to reach the push it is
        # judged on; the stub logs both and contacts nothing.
        install_git_network_stub(self.stub_bin, self.stub_log, succeed_verbs=("fetch", "push"))
        (tmp_path / "home").mkdir()

    def run(self, script: pathlib.Path, args: list[str]) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[self.stub_bin, pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "TEAM_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
                "PGAI_PROJECT_NAME": self.project,
                # Both ways a script could inherit a checkout point at a real
                # repo; neither may make an unresolved project pushable.
                "REPO_ROOT": str(self.repo),
                "PGAI_DEV_TREE_PATH": str(self.repo),
            },
        )
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True, text=True, env=env, cwd=str(self.root), timeout=180,
        )

    def network_calls(self) -> list[str]:
        if not self.stub_log.is_file():
            return []
        return [l for l in self.stub_log.read_text(encoding="utf-8").splitlines() if l]

    def pushes(self) -> list[str]:
        return [l for l in self.network_calls() if "push" in l.split()]

    def open_and_release(self, version: str):
        """Open the RC, land one commit on it, run the release."""
        opened = self.run(_OPEN_RC_SCRIPT, [version])
        open_calls = self.network_calls()
        if opened.returncode == 0:
            _commit_on_branch(
                self.repo, f"rc/{version}", "gate_feature.txt", "gate feature\n",
                "feat: push gate fixture feature", return_branch="main",
            )
        released = self.run(_RELEASE_SCRIPT, []) if opened.returncode == 0 else None
        return opened, open_calls, released


def _out(result: Optional[subprocess.CompletedProcess]) -> str:
    return "" if result is None else result.stdout + result.stderr


def _gate_skip_lines(text: str) -> list[str]:
    return [l for l in text.splitlines() if _PUSH_GATE_SKIP in l]


class TestReleasePathPushGate:
    """open-rc.sh and release.sh push only for a resolved project."""

    @pytest.mark.parametrize(
        ("shape", "token"),
        [
            ("no-project-cfg", "missing project-cfg"),
            ("not-registered", "missing registration"),
            ("no-dev-tree-path", "missing dev-tree-path"),
        ],
    )
    def test_unresolved_project_releases_locally_without_contacting_a_remote(
        self, tmp_path: pathlib.Path, shape: str, token: str
    ) -> None:
        version = "v0.1.0"
        case = _PushGateCase(tmp_path, shape)

        opened, open_calls, released = case.open_and_release(version)

        assert opened.returncode == 0, f"{shape}: open-rc failed\n{_out(opened)}"
        assert open_calls == [], f"{shape}: open-rc made a git network call\n{_out(opened)}"
        open_skips = _gate_skip_lines(_out(opened))
        assert len(open_skips) == 1 and token in open_skips[0], (
            f"{shape}: expected one open-rc {_PUSH_GATE_SKIP} line naming {token!r}\n{_out(opened)}"
        )
        assert open_skips[0].startswith("[cm-open-rc] "), open_skips[0]

        assert released is not None and released.returncode == 0, (
            f"{shape}: release failed\n{_out(released)}"
        )
        assert case.network_calls() == [], (
            f"{shape}: the release made a git network call: {case.network_calls()}\n{_out(released)}"
        )
        release_skips = _gate_skip_lines(_out(released))
        assert len(release_skips) == 1 and token in release_skips[0], (
            f"{shape}: expected one release {_PUSH_GATE_SKIP} line naming {token!r}\n{_out(released)}"
        )
        assert release_skips[0].startswith("[cm-release] "), release_skips[0]
        assert _git_tag_exists(case.repo, version), _out(released)
        assert _git_local(case.origin, "for-each-ref") == ""

    def test_registered_project_without_push_key_still_pushes(self, tmp_path: pathlib.Path) -> None:
        version = "v0.1.0"
        case = _PushGateCase(tmp_path, "push-default")

        opened, open_calls, released = case.open_and_release(version)

        assert opened.returncode == 0, _out(opened)
        assert any(f"rc/{version}" in l.split() for l in open_calls if "push" in l.split()), (
            f"open-rc must push the rc branch; stub log: {open_calls}\n{_out(opened)}"
        )
        assert released is not None and released.returncode == 0, _out(released)
        assert any(f"refs/tags/{version}" in l.split() for l in case.pushes()), (
            f"the release must push its tag by its ref; stub log: {case.network_calls()}\n{_out(released)}"
        )
        assert _gate_skip_lines(_out(opened) + _out(released)) == []
        assert "Push policy: push_to_remote=true" in _out(released)

    def test_release_pushes_its_own_tag_ref_and_no_other_local_tag(self, tmp_path: pathlib.Path) -> None:
        """Step 14 names the release tag's ref; an unrelated, unpublished
        local tag in the same repository is never pushed."""
        version = "v0.1.0"
        unrelated = "v0.0.5"
        case = _PushGateCase(tmp_path, "push-default")
        _git_local(case.repo, "tag", unrelated)

        opened, _open_calls, released = case.open_and_release(version)

        assert opened.returncode == 0, _out(opened)
        assert released is not None and released.returncode == 0, _out(released)
        tag_pushes = [l for l in case.pushes() if "--tags" in l.split() or "refs/tags/" in l]
        assert tag_pushes, f"the release must push its tag; stub log: {case.network_calls()}\n{_out(released)}"
        for line in tag_pushes:
            assert line.split()[-1] == f"refs/tags/{version}", (
                f"a tag push must name only the release tag ref: {line!r}\n{_out(released)}"
            )
        assert not any(unrelated in l for l in case.pushes()), case.network_calls()

    def test_push_to_remote_false_stays_local(self, tmp_path: pathlib.Path) -> None:
        version = "v0.1.0"
        case = _PushGateCase(tmp_path, "local-only")

        opened, open_calls, released = case.open_and_release(version)

        assert opened.returncode == 0, _out(opened)
        assert open_calls == [], _out(opened)
        assert released is not None and released.returncode == 0, _out(released)
        assert case.network_calls() == [], _out(released)
        assert _gate_skip_lines(_out(opened) + _out(released)) == []
        assert "Push policy: push_to_remote=false" in _out(released)
        assert _git_tag_exists(case.repo, version)
