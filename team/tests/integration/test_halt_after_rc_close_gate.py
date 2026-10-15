"""
test_halt_after_rc_close_gate.py
=================================
Behavioral fixtures for the HALT-AFTER RC-close gate
(team/pgai_agent_kanban/halt_after/rc_gate.py) and CM's synchronous spend of
a version-pinned sentinel at ship time
(team/scripts/lib/cm/release_finalize.sh, Step 12c).

Two families of coverage:

  - Wake-loop-level (subprocess `python3 -m halt_after`, no git, no CM
    scripts): proves a satisfied drain condition is deferred while a
    project's Active RC is open, that the deferral is latched so a later
    re-check cannot lose it even if the underlying condition flips back to
    "not satisfied," and that the latch converts once Active RC reads
    "none" -- exercising the exact exit-code contract
    team/scripts/lib/wake_project.sh's _check_halt_after_for_project reads.

  - CM-level (real cm/open-rc.sh -> cm/release.sh over a synthetic git
    repo, adapted from test_cm_release_synthetic.py's fixture pattern):
    proves CM spends a version-pinned rc:vX.Y.Z sentinel synchronously at
    ship time -- once when the shipped version exactly matches the target,
    once when the shipped version skips past it -- and narrates the spend
    via token.describe_token in the release output. A third release-level
    test arms a sentinel whose target is later than the shipped version:
    the not-yet-drained answer must leave the sentinel armed and let the
    release run on through its post-tag steps to exit 0.

No seams are mocked; both families exercise the real Python module and (for
the CM-level tests) the real bash scripts end-to-end.
"""

from __future__ import annotations

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
    require_binary("git", label="halt-after-rc-close-gate")

# ---------------------------------------------------------------------------
# Path helpers -- resolve from this file's own location, matching
# test_cm_release_synthetic.py's convention.
#
#   team/tests/integration/test_halt_after_rc_close_gate.py
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CM_SCRIPTS_DIR = _SCRIPTS_DIR / "cm"
_KANBAN_PY_DIR = _TEAM_DIR / "pgai_agent_kanban"

_OPEN_RC_SCRIPT = _CM_SCRIPTS_DIR / "open-rc.sh"
_RELEASE_SCRIPT = _CM_SCRIPTS_DIR / "release.sh"


# ---------------------------------------------------------------------------
# Wake-loop-level fixture helpers (no git, no CM scripts)
# ---------------------------------------------------------------------------


def _write_release_state(
    project_root: pathlib.Path,
    *,
    active_rc: str = "none",
    last_released: str = "none",
) -> None:
    lines = ["# Release State\n\n", "## Active RC\n", f"{active_rc}\n"]
    if last_released and last_released != "none":
        lines.append(f"\n## Last Released\n{last_released}\n")
    (project_root / "release-state.md").write_text("".join(lines), encoding="utf-8")


def _write_task_status(project_root: pathlib.Path, task_id: str, state: str, role: str) -> None:
    task_dir = project_root / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        f"## State\n{state}\n\n## Role\n{role}\n", encoding="utf-8"
    )


def _run_halt_after_cli(project_root: pathlib.Path) -> subprocess.CompletedProcess:
    """Invoke `python3 -m halt_after <project_root>` exactly as the wake loop does.

    Runs against team/'s own halt_after shim and pgai_agent_kanban package
    (PYTHONPATH=team/) -- the same layout `_check_halt_after_for_project`
    (team/scripts/lib/wake_project.sh) targets in a live install, where
    "team/" is flattened to the install root.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_TEAM_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        ["python3", "-m", "halt_after", str(project_root)],
        capture_output=True, text=True, env=env, cwd=str(_TEAM_DIR), timeout=30,
    )


# ---------------------------------------------------------------------------
# Wake-loop-level: the literal Goal fixture -- a role token (coder) armed
# mid-RC, a CODER task WORKING when the RC is genuinely in flight, the RC
# shipping while the drain stays latched, and HALT appearing only afterward.
# ---------------------------------------------------------------------------


def test_role_token_drain_defers_through_rc_close_then_promotes(tmp_path: pathlib.Path) -> None:
    """The Goal's literal scenario: 'coder' drains mid-RC; HALT waits for RC close.

    Narrative (three python3 -m halt_after evaluations against the same
    project root, mirroring three successive wake firings):

      1. RC open, a CODER task is WORKING (the RC is genuinely in flight --
         real work is happening). check_drain('coder') is False (a WORKING
         CODER task blocks it) -- exit 1, no HALT, no latch.
      2. The CODER task finishes (DONE) with nothing else queued for CODER,
         so the drain condition becomes satisfied -- but the RC is STILL
         open (other roles have not finished shipping it). Promotion must
         defer: exit 5, no HALT file, a latch is written recording 'coder'.
      3. The RC ships -- Active RC flips to 'none' (exactly what
         cm-release.sh's Step 12 does). The NEXT evaluation reads the latch
         and promotes: exit 0, HALT created, HALT-AFTER removed, a HALT
         Event appended naming 'coder' verbatim.

    This is the precise failure mode the gate prevents: without the
    RC-close gate, step 2 would have promoted immediately and frozen the
    chain with a half-built RC still on the branch.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "tasks").mkdir()
    (project_root / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    _write_release_state(project_root, active_rc="v0.9.0")

    # --- Evaluation 1: CODER task WORKING, RC open — not drained at all. ---
    _write_task_status(project_root, "CODER-001", "WORKING", "CODER")
    result_1 = _run_halt_after_cli(project_root)
    assert result_1.returncode == 1, (
        f"expected exit 1 (not drained) while a CODER task is WORKING; "
        f"got {result_1.returncode}. stderr: {result_1.stderr}"
    )
    assert not (project_root / "HALT").exists(), "no HALT before the CODER task even finishes"

    # --- Evaluation 2: CODER task finishes; RC still open — must defer. ---
    (project_root / "tasks" / "CODER-001" / "status.md").write_text(
        "## State\nDONE\n\n## Role\nCODER\n", encoding="utf-8"
    )
    result_2 = _run_halt_after_cli(project_root)
    assert result_2.returncode == 5, (
        f"expected exit 5 (drained but deferred -- RC still open); "
        f"got {result_2.returncode}. stderr: {result_2.stderr}"
    )
    assert not (project_root / "HALT").exists(), (
        "HALT must not appear while the RC is still open, even though the "
        "coder drain condition is now satisfied"
    )
    assert (project_root / "HALT-AFTER.rc-gate").exists(), (
        "a latch recording the satisfied drain must be written so a later "
        "re-check cannot lose it"
    )

    # --- The RC ships: Active RC -> none (what cm-release.sh Step 12 does). ---
    _write_release_state(project_root, active_rc="none")

    # --- Evaluation 3: RC closed — the latched drain now promotes. ---
    result_3 = _run_halt_after_cli(project_root)
    assert result_3.returncode == 0, (
        f"expected exit 0 (promoted) once the RC has closed; "
        f"got {result_3.returncode}. stderr: {result_3.stderr}"
    )
    assert (project_root / "HALT").exists(), "HALT must appear only after the RC closes"
    assert not (project_root / "HALT-AFTER").exists()
    assert not (project_root / "HALT-AFTER.rc-gate").exists(), "latch is consumed by promotion"

    release_state_text = (project_root / "release-state.md").read_text(encoding="utf-8")
    assert "## HALT Event" in release_state_text
    assert "Trigger: HALT-AFTER coder auto-promotion" in release_state_text, (
        f"Trigger must name the token verbatim; got:\n{release_state_text}"
    )


def test_latched_drain_is_not_undone_by_a_later_unsatisfied_recheck(tmp_path: pathlib.Path) -> None:
    """Deferring a satisfied drain must not lose it, even if it later re-evaluates false.

    Once 'coder' has been observed drained while the RC is open (latched),
    a brand-new WORKING CODER task starting before the RC closes must not
    cancel the pending promotion -- the constraint is explicit: 'once the
    drain condition has been met, the promotion fires at the next RC close
    even if the condition would no longer evaluate true at that moment.'
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "tasks").mkdir()
    (project_root / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    _write_release_state(project_root, active_rc="v0.9.0")

    # No CODER tasks at all yet — drain is satisfied immediately, RC is open.
    result_1 = _run_halt_after_cli(project_root)
    assert result_1.returncode == 5
    assert (project_root / "HALT-AFTER.rc-gate").read_text(encoding="utf-8").strip() == "coder"

    # A new CODER task starts WORKING before the RC closes -- if the gate
    # re-derived the condition fresh, this would flip it back to False.
    _write_task_status(project_root, "CODER-002", "WORKING", "CODER")
    result_2 = _run_halt_after_cli(project_root)
    assert result_2.returncode == 5, "still deferred (RC still open) -- the latch, not a re-check, decides"
    assert not (project_root / "HALT").exists()

    # RC closes while the new CODER task is STILL working.
    _write_release_state(project_root, active_rc="none")
    result_3 = _run_halt_after_cli(project_root)
    assert result_3.returncode == 0, (
        "the latched drain must promote at RC close even though a fresh "
        "check_drain('coder') would now return False (the new task is still WORKING)"
    )
    assert (project_root / "HALT").exists()


# ---------------------------------------------------------------------------
# CM-level fixture helpers (adapted from test_cm_release_synthetic.py)
# ---------------------------------------------------------------------------


def _init_git_repo_with_remote(repo_path: pathlib.Path, bare_remote: pathlib.Path) -> None:
    bare_remote.mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(bare_remote)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "init", "--initial-branch=main"],
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
    (repo_path / "release-notes").mkdir(parents=True, exist_ok=True)
    (repo_path / "release-notes" / "PUBLISHED").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "initial commit"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "remote", "add", "origin", str(bare_remote)],
        capture_output=True, check=True,
    )
    subprocess.run(["git", "-C", str(repo_path), "push", "origin", "main"], capture_output=True, check=True)


def _build_kanban_root(
    parent: pathlib.Path, project_name: str, repo_path: pathlib.Path
) -> pathlib.Path:
    """Minimal kanban root for release-lifecycle testing.

    Carries the whole pgai_agent_kanban package (as install.sh ships it,
    without __pycache__ and tests directories), plus the halt_after modules
    install.sh ships at the kanban root (halt_after/ and pm-agent/lib/semver.py,
    which drain.py resolves relative to its own copied location) -- neither
    of which test_cm_release_synthetic.py's own builder copies, since none of
    ITS fixtures ever write a HALT-AFTER file.
    """
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n[wake]\nmax_tasks_per_wake = 1\nmax_runtime_seconds = 600\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=Synthetic test project\nenabled=true\n",
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
        f"push_to_remote = false\n\n"
        f"[versioning]\n"
        f"max_patch = 99\n"
        f"max_minor = 9\n"
        f"max_major = 0\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    import shutil

    # pgai_agent_kanban/ — the whole package, as install.sh ships it (one
    # recursive copy of team/pgai_agent_kanban), minus bytecode caches and
    # test directories. The release scripts' python helpers put this root's
    # package first on sys.path, so any sub-package one of them imports
    # (cm/, halt_after/, lib/, ops/, archive/, ...) must be here; copying the
    # whole tree means a new import in a cm/ helper cannot break this root.
    shutil.copytree(
        _KANBAN_PY_DIR,
        root / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )

    # halt_after (root-level shim) + pm-agent/lib/semver.py (drain.py's own
    # dependency), mirroring what install.sh copies from team/ to a live
    # install's root beside the package.
    shutil.copytree(_TEAM_DIR / "halt_after", root / "halt_after")
    (root / "pm-agent" / "lib").mkdir(parents=True, exist_ok=True)
    (root / "pm-agent" / "lib" / "semver.py").write_text(
        (_TEAM_DIR / "pm-agent" / "lib" / "semver.py").read_text(encoding="utf-8"), encoding="utf-8"
    )

    return root


def _run_cm_script(
    script_path: pathlib.Path,
    args: list,
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess:
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    if repo_root is not None:
        base_env["REPO_ROOT"] = str(repo_root)
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    return subprocess.run(
        ["bash", str(script_path)] + args,
        capture_output=True, text=True, env=base_env, cwd=str(kanban_root), timeout=timeout,
    )


def _commit_on_branch(repo_path: pathlib.Path, branch: str, filename: str, content: str, message: str) -> None:
    subprocess.run(["git", "-C", str(repo_path), "checkout", branch], capture_output=True, check=True)
    (repo_path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_path), "add", filename], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-m", message], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo_path), "checkout", "main"], capture_output=True, check=True)


def _ship_one_release(
    tmp_path: pathlib.Path, project_name: str, version: str, *, halt_after_token: str
) -> tuple:
    """Open an RC at *version*, commit a feature, arm HALT-AFTER, then ship.

    Returns (kanban_root, project_root, repo_path, release_result).
    """
    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path)
    project_root = kanban_root / "projects" / project_name

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )

    _commit_on_branch(repo_path, f"rc/{version}", "feature.txt", "feature content\n", "feat: add feature")

    (project_root / "HALT-AFTER").write_text(halt_after_token + "\n", encoding="utf-8")

    result_release = _run_cm_script(_RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path)
    return kanban_root, project_root, repo_path, result_release


# ---------------------------------------------------------------------------
# CM-level: version-pinned sentinel spent synchronously at ship time
# ---------------------------------------------------------------------------


def test_cm_release_spends_versioned_sentinel_when_shipped_version_matches_target(
    tmp_path: pathlib.Path,
) -> None:
    """Shipping exactly the sentinel's target version spends it at RC close.

    Asserts the release output narrates the spend via describe_token
    ('until v0.1.0'), the project now carries HALT (not HALT-AFTER), and the
    HALT Event in release-state.md names the token verbatim.
    """
    version = "v0.1.0"
    project_name = "halt_after_exact_match_test"

    kanban_root, project_root, repo_path, result_release = _ship_one_release(
        tmp_path, project_name, version, halt_after_token=f"rc:{version}"
    )

    assert result_release.returncode == 0, (
        f"cm-release.sh failed.\nstdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )
    combined = result_release.stdout + result_release.stderr

    assert "HALT-AFTER sentinel spent at RC close" in combined, (
        f"expected the ship-time spend narration in release output:\n{combined}"
    )
    assert f"until {version}" in combined, (
        f"expected the target rendered via describe_token ('until {version}'):\n{combined}"
    )

    assert (project_root / "HALT").exists(), "HALT must exist once the target version has shipped"
    assert not (project_root / "HALT-AFTER").exists(), "HALT-AFTER must be removed once spent"

    release_state_text = (project_root / "release-state.md").read_text(encoding="utf-8")
    assert "## HALT Event" in release_state_text
    assert f"Trigger: HALT-AFTER rc:{version} auto-promotion" in release_state_text, (
        f"Trigger must name the versioned token verbatim; got:\n{release_state_text}"
    )
    assert f"until {version} shipped" in release_state_text, (
        f"Event drained must use describe_token's phrase, not a locally composed one; got:\n{release_state_text}"
    )


def test_cm_release_spends_versioned_sentinel_when_shipped_version_skips_past_target(
    tmp_path: pathlib.Path,
) -> None:
    """Shipping a version PAST the sentinel's target still spends it (latching >=).

    The sentinel targets v0.1.0 but this RC ships v0.2.0 directly -- proving
    the monotonic/latching semver comparison survived the RC-close gate.
    """
    shipped_version = "v0.2.0"
    target_version = "v0.1.0"
    project_name = "halt_after_skip_ahead_test"

    kanban_root, project_root, repo_path, result_release = _ship_one_release(
        tmp_path, project_name, shipped_version, halt_after_token=f"rc:{target_version}"
    )

    assert result_release.returncode == 0, (
        f"cm-release.sh failed.\nstdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )
    combined = result_release.stdout + result_release.stderr

    assert "HALT-AFTER sentinel spent at RC close" in combined, (
        f"a shipped version past the target must still spend the sentinel:\n{combined}"
    )
    assert f"until {target_version}" in combined

    assert (project_root / "HALT").exists()
    assert not (project_root / "HALT-AFTER").exists()

    release_state_text = (project_root / "release-state.md").read_text(encoding="utf-8")
    assert f"Trigger: HALT-AFTER rc:{target_version} auto-promotion" in release_state_text


def test_cm_release_leaves_versioned_sentinel_armed_when_target_not_yet_shipped(
    tmp_path: pathlib.Path,
) -> None:
    """A sentinel for a later version stays armed and the release completes.

    The sentinel targets v0.9.0 but this RC ships v0.1.0, so the ship-time
    spend step gets a not-yet-drained answer from the evaluator. That answer
    must not end cm/release.sh (which runs under set -euo pipefail): the
    release exits 0 and reaches its post-tag bundle promotion
    ([Step 13c]), HALT-AFTER is still in place for a later release to
    spend, and no HALT appears at either the project or the root scope.
    """
    shipped_version = "v0.1.0"
    target_version = "v0.9.0"
    project_name = "halt_after_not_yet_due_test"

    kanban_root, project_root, repo_path, result_release = _ship_one_release(
        tmp_path, project_name, shipped_version, halt_after_token=f"rc:{target_version}"
    )
    diagnostic = f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"

    assert result_release.returncode == 0, (
        f"cm-release.sh must exit 0 when the HALT-AFTER target is not yet due.\n{diagnostic}"
    )
    combined = result_release.stdout + result_release.stderr
    assert "[Step 13c]" in combined, (
        f"the release must run on past the HALT-AFTER spend step to [Step 13c].\n{diagnostic}"
    )
    assert "HALT-AFTER sentinel spent at RC close" not in combined, (
        f"a sentinel for a later version must not be reported as spent.\n{diagnostic}"
    )

    assert (project_root / "HALT-AFTER").exists(), (
        f"HALT-AFTER must stay armed until {target_version} ships.\n{diagnostic}"
    )
    assert not (project_root / "HALT").exists(), (
        f"no project-scope HALT may be created before the target ships.\n{diagnostic}"
    )
    assert not (kanban_root / "HALT").exists(), (
        f"no root-scope HALT may be created by a project's release.\n{diagnostic}"
    )
