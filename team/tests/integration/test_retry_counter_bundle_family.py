"""
test_retry_counter_bundle_family.py
====================================
Integration tests for the retry counter ("attempt: N in
release-state/<version>.json, keyed by bundle family") exercised through the
real cm-open-rc.sh / cm-cancel-rc.sh production entry points — not just the
Python helper functions in isolation (those are covered by
test_bundle_family.py and test_write_rc_state_attempt.py).

Coverage:
  - attempt: N is persisted in release-state/<version>.json after a real
    open-rc.sh run against a requirements bundle.
  - The same sorted cargo set rebundled under a new version (open -> cancel
    -> new bundle with identical bugs -> open) increments attempt (1 -> 2).
  - A different cargo set starts a fresh family at attempt 1, even when a
    cancelled record already exists for a different family.
  - First-attempt happy path (no bundle at all, or a fresh family) records
    attempt 1.

Design:
  - All tests use pytest tmp_path for scratch — no bare /tmp paths.
  - Each test is self-contained; no shared state between tests.
  - The dev tree in every test is a local temp git repo.
  - Shell scripts (cm-open-rc.sh, cm-cancel-rc.sh) run against a temp kanban
    root that is never the live install.
  - Helper functions mirror the pattern established in
    test_cancel_rc_frees_version.py (SOP convention: each integration test
    file keeps its own self-contained copies rather than importing across
    test files).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Optional

import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="retry-counter-bundle-family")

# ---------------------------------------------------------------------------
# Path helpers — resolve from this file's own location.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CM_SCRIPTS_DIR = _SCRIPTS_DIR / "cm"
_KANBAN_PY_DIR = _TEAM_DIR / "pgai_agent_kanban"

_OPEN_RC_SCRIPT = _CM_SCRIPTS_DIR / "open-rc.sh"
_CANCEL_RC_SCRIPT = _CM_SCRIPTS_DIR / "cancel-rc.sh"


def _init_git_repo_with_remote(
    repo_path: pathlib.Path,
    bare_remote: pathlib.Path,
    *,
    main_branch: str = "main",
) -> None:
    """Initialise a local git repo with an initial commit and a local bare remote."""
    bare_remote.mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "--bare", str(bare_remote)], capture_output=True, check=True)
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

    (repo_path / "README.md").write_text("# Test repo\n", encoding="utf-8")
    (repo_path / "release-notes").mkdir(parents=True, exist_ok=True)
    (repo_path / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "README.md", "release-notes/PUBLISHED"],
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


def _build_kanban_root_for_rc(
    parent: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    *,
    push_to_remote: str = "false",
) -> pathlib.Path:
    """Build a minimal kanban root configured for RC lifecycle testing,
    including a requirements/ directory for bundle files."""
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\n"
        "max_tasks_per_wake = 1\nmax_runtime_seconds = 600\n",
        encoding="utf-8",
    )

    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\n"
        "description=retry-counter-bundle-family test\nenabled=true\n",
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
        f"\n[versioning]\n"
        f"max_patch = 99\nmax_minor = 9\nmax_major = 0\n",
        encoding="utf-8",
    )

    (proj / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n"
        "## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )

    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (proj / "requirements").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)
    src_cm_dir = _KANBAN_PY_DIR / "cm"
    for py_file in src_cm_dir.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")

    return root


def _write_bundle(
    requirements_dir: pathlib.Path, version: str, bug_basenames: list, suffix: str = "bugfix-bundle-20260727"
) -> pathlib.Path:
    """Write a minimal requirements bundle file with a ## Bundled Items
    section listing *bug_basenames*, mirroring discovery.sh's _disc_write_bundle
    output shape closely enough for bundle_family.py's parser."""
    bundle_path = requirements_dir / f"{version}-{suffix}.md"
    lines = [
        f"# Requirements: {version} — BUG bundle\n\n",
        "## Status\n\nopen\n\n",
        "## Target Version\n\n", f"{version}\n\n",
        "## Bundled Items\n\n",
    ]
    for name in bug_basenames:
        lines.append(f"- {name} (`/abs/bugs/{name}`)\n")
    lines.append("\n## Acceptance Criteria\n\n- [ ] done\n")
    bundle_path.write_text("".join(lines), encoding="utf-8")
    return bundle_path


def _run_cm_script(
    script_path: pathlib.Path,
    args: list,
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
    extra_env: Optional[dict] = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess:
    """Run a CM script against a temporary kanban root."""
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
        ["bash", str(script_path)] + list(args),
        capture_output=True, text=True, env=base_env,
        cwd=str(kanban_root), timeout=timeout,
    )


def _read_rc_state_json(kanban_root: pathlib.Path, project_name: str, version: str) -> dict:
    path = kanban_root / "projects" / project_name / "release-state" / f"{version}.json"
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# AC 4 / first-attempt happy path
# ---------------------------------------------------------------------------


class TestFirstAttemptHappyPath:
    def test_open_rc_with_bundle_records_attempt_one(self, tmp_path: pathlib.Path) -> None:
        """A fresh family (first time this cargo set is bundled) records
        attempt 1 with a populated family_key and cargo list."""
        version = "v0.1.0"
        project_name = "first_attempt_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)
        kanban_root = _build_kanban_root_for_rc(tmp_path, project_name, repo_path)

        requirements_dir = kanban_root / "projects" / project_name / "requirements"
        _write_bundle(requirements_dir, version, ["BUG-0001-foo.md", "BUG-0002-bar.md"])

        result = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path
        )
        assert result.returncode == 0, f"open-rc failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"

        state = _read_rc_state_json(kanban_root, project_name, version)
        assert state["attempt"] == 1
        assert state["family_key"] is not None
        assert state["cargo"] == ["BUG-0001-foo.md", "BUG-0002-bar.md"]

    def test_open_rc_with_no_bundle_file_records_attempt_one(self, tmp_path: pathlib.Path) -> None:
        """An operator-authored requirements doc with no matching bundle file
        (no cargo to track) still records a valid attempt 1, never blocking
        the RC open."""
        version = "v0.1.0"
        project_name = "no_bundle_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)
        kanban_root = _build_kanban_root_for_rc(tmp_path, project_name, repo_path)
        # No bundle file written — requirements/ is empty.

        result = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path
        )
        assert result.returncode == 0, f"open-rc failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"

        state = _read_rc_state_json(kanban_root, project_name, version)
        assert state["attempt"] == 1
        assert state["family_key"] is None
        assert state["cargo"] == []


# ---------------------------------------------------------------------------
# AC 2: same cargo set rebundled under a new version increments attempt
# ---------------------------------------------------------------------------


class TestSameCargoSetIncrementsAcrossRebundle:
    def test_cancel_then_rebundle_same_bugs_increments_attempt(
        self, tmp_path: pathlib.Path
    ) -> None:
        """open(v0.1.0, bugs A+B) -> cancel -> open(v0.1.1, same bugs A+B)
        -> attempt goes 1 -> 2, same family_key, both real script runs."""
        project_name = "same_cargo_retry_test"
        cargo = ["BUG-0001-foo.md", "BUG-0002-bar.md"]

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)
        kanban_root = _build_kanban_root_for_rc(tmp_path, project_name, repo_path)
        requirements_dir = kanban_root / "projects" / project_name / "requirements"

        # First bundle + open: attempt 1.
        _write_bundle(requirements_dir, "v0.1.0", cargo)
        result_open1 = _run_cm_script(
            _OPEN_RC_SCRIPT, ["v0.1.0"], kanban_root, project_name, repo_root=repo_path
        )
        assert result_open1.returncode == 0, result_open1.stderr
        state_v1 = _read_rc_state_json(kanban_root, project_name, "v0.1.0")
        assert state_v1["attempt"] == 1
        family_key = state_v1["family_key"]
        assert family_key is not None

        # Cancel v0.1.0 — archives the attempt-1 record to history/.
        result_cancel = _run_cm_script(
            _CANCEL_RC_SCRIPT, ["--key", "v0.1.0", "--yes"],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_cancel.returncode == 0, result_cancel.stderr

        # Rebundle the SAME bugs under a new patch number and open v0.1.1.
        _write_bundle(requirements_dir, "v0.1.1", list(reversed(cargo)))
        result_open2 = _run_cm_script(
            _OPEN_RC_SCRIPT, ["v0.1.1"], kanban_root, project_name, repo_root=repo_path
        )
        assert result_open2.returncode == 0, result_open2.stderr

        state_v2 = _read_rc_state_json(kanban_root, project_name, "v0.1.1")
        assert state_v2["family_key"] == family_key, "Same cargo set must map to the same family"
        assert state_v2["attempt"] == 2, "Rebundling the same bugs must be attempt N+1, not a fresh 1"


# ---------------------------------------------------------------------------
# AC 3: a different cargo set starts a fresh family at attempt 1
# ---------------------------------------------------------------------------


class TestDifferentCargoSetStartsFreshFamily:
    def test_different_bugs_after_a_cancelled_family_starts_at_one(
        self, tmp_path: pathlib.Path
    ) -> None:
        """After one family (bugs A+B) is opened and cancelled, a completely
        different family (bug C) opened next must record attempt 1 and a
        distinct family_key — the prior family's history must not leak in."""
        project_name = "different_cargo_test"

        bare_remote = tmp_path / "bare_remote.git"
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, bare_remote)
        kanban_root = _build_kanban_root_for_rc(tmp_path, project_name, repo_path)
        requirements_dir = kanban_root / "projects" / project_name / "requirements"

        # Family A: open + cancel.
        _write_bundle(requirements_dir, "v0.1.0", ["BUG-0001-foo.md", "BUG-0002-bar.md"])
        _run_cm_script(_OPEN_RC_SCRIPT, ["v0.1.0"], kanban_root, project_name, repo_root=repo_path)
        state_a = _read_rc_state_json(kanban_root, project_name, "v0.1.0")
        _run_cm_script(
            _CANCEL_RC_SCRIPT, ["--key", "v0.1.0", "--yes"],
            kanban_root, project_name, repo_root=repo_path,
        )

        # Family B: an unrelated bug set bundled next.
        _write_bundle(requirements_dir, "v0.1.1", ["BUG-0099-unrelated.md"])
        result_open_b = _run_cm_script(
            _OPEN_RC_SCRIPT, ["v0.1.1"], kanban_root, project_name, repo_root=repo_path
        )
        assert result_open_b.returncode == 0, result_open_b.stderr

        state_b = _read_rc_state_json(kanban_root, project_name, "v0.1.1")
        assert state_b["family_key"] != state_a["family_key"]
        assert state_b["attempt"] == 1, "A different cargo set must start fresh at attempt 1"
        assert state_b["cargo"] == ["BUG-0099-unrelated.md"]
