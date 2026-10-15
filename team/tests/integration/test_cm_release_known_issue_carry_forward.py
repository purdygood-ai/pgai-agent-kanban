"""
test_cm_release_known_issue_carry_forward.py
=============================================
End-to-end integration tests for the SHIP-KNOWN-ISSUE ship outcome:
cm-release.sh, given a TESTER report whose one high-systemic-risk finding
is pre-existing (Regression: no, Integrity: none) and small effort, ships
the release rather than halting, and leaves two records behind:

  1. The release notes carry the KNOWN-ISSUE status and a "## Known
     Issues" section naming the causing bug by substance (never the bare
     internal ticket ID) — on both the WRITER-authored-notes path and the
     auto-generated-notes path.
  2. The causing bug file gains a new "## Known Issue Carry Forward" field
     stamped with the shipping version, while its "## Status" stays open
     and every other bug file in the fixture is left byte-identical.

Fixture pattern (git repo + kanban root + TESTER task folder) mirrors
test_cm_release_synthetic.py's established helpers, trimmed to what this
module's scenarios need.  No seams are mocked: the real cm-release.sh
drives every scenario end to end.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
from typing import Optional

import pytest

from tests.unit.shell_harness import run_bash
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="cm-release-known-issue-carry-forward")

# ---------------------------------------------------------------------------
# Path helpers — mirrors test_cm_release_synthetic.py's own resolution.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CM_SCRIPTS_DIR = _TEAM_DIR / "scripts" / "cm"
_OPEN_RC_SCRIPT = _CM_SCRIPTS_DIR / "open-rc.sh"
_RELEASE_SCRIPT = _CM_SCRIPTS_DIR / "release.sh"
_KNOWN_ISSUE_LIB = str((_TEAM_DIR / "scripts" / "lib" / "cm" / "release_known_issue.sh").resolve())


# ---------------------------------------------------------------------------
# Fixture helpers (trimmed copies of test_cm_release_synthetic.py's own —
# each integration module in this tree builds its own fixture surface
# rather than importing another test module's private helpers).
# ---------------------------------------------------------------------------


def _init_git_repo_with_remote(
    repo_path: pathlib.Path, bare_remote: pathlib.Path, *, main_branch: str = "main"
) -> None:
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

    (repo_path / "README.md").write_text("# Synthetic release test repo\n", encoding="utf-8")
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


def _build_kanban_root(
    parent: pathlib.Path, project_name: str, repo_path: pathlib.Path, *, push_to_remote: str = "false"
) -> pathlib.Path:
    root = parent / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n\n[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 1\nmax_runtime_seconds = 600\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\ndescription=Known-issue ship test project\nenabled=true\n",
        encoding="utf-8",
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {project_name}\ndev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\ngit_remote_name = origin\n"
        f"workflow_type = release\nis_self_build = false\npush_to_remote = {push_to_remote}\n\n"
        f"[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n",
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

    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)
    src_cm_dir = _TEAM_DIR / "pgai_agent_kanban" / "cm"
    for py_file in src_cm_dir.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")

    return root


def _run_cm_script(
    script_path: pathlib.Path, args: list[str], kanban_root: pathlib.Path, project_name: str,
    *, repo_root: Optional[pathlib.Path] = None, timeout: int = 90,
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


def _git_local(repo_path: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path)] + list(args), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _git_tag_exists(repo_path: pathlib.Path, tag: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _commit_on_branch(
    repo_path: pathlib.Path, branch: str, filename: str, content: str, message: str,
    return_branch: Optional[str] = None,
) -> None:
    _git_local(repo_path, "checkout", branch)
    (repo_path / filename).write_text(content, encoding="utf-8")
    _git_local(repo_path, "add", filename)
    _git_local(repo_path, "commit", "-m", message)
    if return_branch is not None:
        _git_local(repo_path, "checkout", return_branch)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_tester_task_with_known_issue_finding(
    kanban_root: pathlib.Path, project_name: str, task_id: str, release_version: str,
    *, filed_as: str, state: str = "DONE",
) -> pathlib.Path:
    """Synthetic TESTER task whose report carries one systemic_risk=high
    finding shaped exactly as read_ship_policy_finding_dimensions and
    read_ship_policy_known_issue_bug_files parse it: Regression: no,
    Integrity: none, Fix Effort: small, Filed As citing *filed_as* — the
    pre-existing, non-integrity-damaging, small-fix shape that
    cm_ship_policy rows to SHIP-KNOWN-ISSUE."""
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    (task_dir / "README.md").write_text(
        "# Task: Synthetic known-issue-ship verification\n\n"
        f"## Task ID\n{task_id}\n\n## Role\nTESTER\n\n"
        f"## Release Version\n{release_version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## Role\nTESTER\n\n## State\n{state}\n",
        encoding="utf-8",
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {release_version}\n\n"
        "## Recommendation\nSHIP-WITH-SERIOUS-CONCERNS\n\n"
        "## Systemic Risk\nhigh\n\n"
        "## Findings\n\n"
        "### Finding 1: a pre-existing defect nobody has run\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** high (pre-existing, unrelated to this RC)\n"
        "**Regression:** no\n"
        "**Integrity:** none\n"
        f"**Filed As:** {filed_as} (Path C)\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )
    return task_dir


def _write_bug(
    bugs_dir: pathlib.Path, bug_id: str, slug: str, symptom: str,
) -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    path = bugs_dir / f"{bug_id}-{slug}.md"
    path.write_text(
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        "**Filed By:** TESTER\n"
        "**Severity:** high\n\n"
        "## Status\nopen\n\n"
        "## Category\nmisc\n\n---\n\n"
        f"## Symptom\n{symptom}\n\n"
        "## Expected\nExpected behavior.\n\n"
        "## Actual\nActual behavior.\n\n"
        "## Files Involved\n\n-\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Case (a) — WRITER-authored notes path
# ---------------------------------------------------------------------------


def test_known_issue_ship_stamps_writer_authored_notes_and_bug_marker(tmp_path: pathlib.Path) -> None:
    """WRITER-authored notes: KNOWN-ISSUE status, named finding, bug marker.

    Asserts:
      - cm-release.sh exits 0 (ships rather than halting).
      - release-notes/<version>.md carries "## Status" KNOWN-ISSUE.
      - Its "## Known Issues" section names the causing bug by substance,
        never the bare internal ticket ID.
      - The causing bug file gains "## Known Issue Carry Forward" stamped
        with the shipping version; its "## Status" is still open.
      - Every other bug file in the fixture is byte-identical (checksum).
      - The release tag exists (the release actually shipped).
    """
    version = "v0.20.0"
    project_name = "known_issue_writer_notes_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    causing_bug = _write_bug(
        bugs_dir, "BUG-0042", "legacy-artefact-defect",
        "The legacy artefact importer crashes on a malformed header it has never actually exercised in production.",
    )
    other_bug = _write_bug(
        bugs_dir, "BUG-0099", "unrelated-open-item",
        "An unrelated cosmetic issue with no bearing on this release.",
    )
    other_bug_sha_before = _sha256(other_bug)

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"

    _commit_on_branch(
        repo_path, rc_branch, "feature_known_issue.txt", "known-issue ship feature content\n",
        "feat: add feature for known-issue ship test",
    )

    writer_notes = (
        f"# Release Notes: {project_name} {version}\n\n"
        "**Release Date:** 2026-09-08\n**Released By:** WRITER\n\n"
        "## Status\nPENDING-RELEASE\n\n"
        "## Summary\nTest release for the known-issue ship path.\n\n"
        "## Features\n- Add feature for known-issue ship test\n\n"
        "## Bug Fixes\nNone\n\n"
        "## Known Issues\n\n"
        "Issues that exist in this release and have not been resolved or formally skipped. "
        "Users should be aware of these.\n\n"
        "- **<Issue title>** — Description and any available workaround.\n"
    )
    (repo_path / "release-notes" / f"{version}.md").write_text(writer_notes, encoding="utf-8")
    _git_local(repo_path, "add", f"release-notes/{version}.md")
    _git_local(repo_path, "commit", "-m", f"docs: WRITER release notes for {version}")
    _git_local(repo_path, "checkout", "main")

    tester_task_id = "TESTER-20260908-900-known-issue-writer-notes"
    _build_tester_task_with_known_issue_finding(
        kanban_root, project_name, tester_task_id, version, filed_as="BUG-0042",
    )

    result_release = _run_cm_script(_RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path)
    assert result_release.returncode == 0, (
        "cm-release.sh must ship a pre-existing, non-integrity-damaging, "
        "small-fix high-risk finding as SHIP-KNOWN-ISSUE rather than halting.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    notes_text = (repo_path / "release-notes" / f"{version}.md").read_text(encoding="utf-8")
    assert "## Status\nKNOWN-ISSUE" in notes_text, (
        f"Expected '## Status' KNOWN-ISSUE in stamped release notes.\nnotes:\n{notes_text}"
    )
    assert "malformed header" in notes_text, (
        "Expected the '## Known Issues' section to name the causing finding by "
        f"substance.\nnotes:\n{notes_text}"
    )
    assert "BUG-0042" not in notes_text, (
        "The bare internal ticket ID must never appear in rendered release notes.\n"
        f"notes:\n{notes_text}"
    )
    assert "<Issue title>" not in notes_text, (
        "The WRITER boilerplate placeholder must be replaced, not left alongside "
        f"the stamped content.\nnotes:\n{notes_text}"
    )

    bug_text = causing_bug.read_text(encoding="utf-8")
    assert f"## Known Issue Carry Forward\n{version}" in bug_text, (
        f"Expected the causing bug to carry the known-issue marker for {version}.\n"
        f"bug file:\n{bug_text}"
    )
    assert "## Status\nopen" in bug_text, (
        f"The causing bug's own Status must remain open.\nbug file:\n{bug_text}"
    )

    assert _sha256(other_bug) == other_bug_sha_before, (
        "An unrelated bug file must be left byte-identical by a known-issue ship."
    )

    assert _git_tag_exists(repo_path, version), (
        f"Release tag '{version}' not found after cm-release.sh.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    # --- Idempotency: re-invoke the exact marker-stamp function this run
    # already exercised, against the real artifact the run produced. A
    # second stamp must leave the bug file unchanged and narrate
    # already-complete — the release-notes half of the same guarantee is
    # covered directly by cm_stamp_release_notes_status's own unit tests
    # (test_cm_known_issue_carry_forward_bash.py), which pin the exact
    # rc=1/"already complete" contract Step 4e's pre-existing placeholder
    # check relies on to skip a second stamp attempt.
    bug_text_before_rerun = causing_bug.read_text(encoding="utf-8")
    rerun = run_bash(
        tmp_path,
        f"source {_KNOWN_ISSUE_LIB} && cm_stamp_known_issue_marker '{version}' '{causing_bug}'",
    )
    assert rerun.returncode == 0, rerun.stderr
    assert "already complete, continuing" in rerun.stdout
    assert causing_bug.read_text(encoding="utf-8") == bug_text_before_rerun


# ---------------------------------------------------------------------------
# Case (b) — auto-generated notes path (no WRITER-authored notes)
# ---------------------------------------------------------------------------


def test_known_issue_ship_names_finding_in_auto_generated_notes(tmp_path: pathlib.Path) -> None:
    """No WRITER-authored notes: the auto-generated notes carry the same
    KNOWN-ISSUE status and name the same finding by substance.

    Asserts:
      - cm-release.sh exits 0.
      - The auto-generated release-notes/<version>.md carries "## Status"
        KNOWN-ISSUE and names the causing bug by substance (never the bare
        internal ticket ID) — the same treatment as the WRITER-authored
        path, on the path that generates its own notes from scratch.
      - The causing bug file gains the known-issue marker.
    """
    version = "v0.21.0"
    project_name = "known_issue_auto_notes_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    causing_bug = _write_bug(
        bugs_dir, "BUG-0007", "startup-race-condition",
        "A rare startup race condition causes the scheduler to skip its first tick under heavy load.",
    )

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_known_issue_auto.txt", "auto-notes known-issue ship feature\n",
        "feat: add feature for auto-notes known-issue ship test",
        return_branch="main",
    )
    # Deliberately no release-notes/<version>.md committed on the RC branch —
    # this exercises the no-WRITER-notes path (Step 8 in release_notes.sh).

    tester_task_id = "TESTER-20260908-901-known-issue-auto-notes"
    _build_tester_task_with_known_issue_finding(
        kanban_root, project_name, tester_task_id, version, filed_as="BUG-0007",
    )

    result_release = _run_cm_script(_RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path)
    assert result_release.returncode == 0, (
        "cm-release.sh must ship the known-issue outcome via the "
        "auto-generated-notes path.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    notes_text = (repo_path / "release-notes" / f"{version}.md").read_text(encoding="utf-8")
    assert "## Status\nKNOWN-ISSUE" in notes_text, (
        f"Expected auto-generated notes to carry '## Status' KNOWN-ISSUE.\nnotes:\n{notes_text}"
    )
    assert "startup race condition" in notes_text, (
        "Expected the auto-generated notes to name the causing finding by "
        f"substance.\nnotes:\n{notes_text}"
    )
    assert "BUG-0007" not in notes_text, (
        f"The bare internal ticket ID must never appear in rendered notes.\nnotes:\n{notes_text}"
    )

    bug_text = causing_bug.read_text(encoding="utf-8")
    assert f"## Known Issue Carry Forward\n{version}" in bug_text, (
        f"Expected the causing bug to carry the known-issue marker for {version}.\n"
        f"bug file:\n{bug_text}"
    )


# ---------------------------------------------------------------------------
# Case (c) — ordinary ships never write the marker
# ---------------------------------------------------------------------------


def test_functional_ship_writes_no_known_issue_marker_on_any_bug(tmp_path: pathlib.Path) -> None:
    """A green TESTER report (PASS, Systemic Risk low) ships FUNCTIONAL and
    must never write the known-issue marker on any bug in the fixture."""
    version = "v0.22.0"
    project_name = "functional_ship_no_marker_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bystander_bug = _write_bug(
        bugs_dir, "BUG-0003", "bystander-open-item",
        "An unrelated open bug that has nothing to do with this release.",
    )
    bystander_sha_before = _sha256(bystander_bug)

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_functional.txt", "functional ship feature content\n",
        "feat: add feature for functional ship test",
        return_branch="main",
    )

    tester_task_id = "TESTER-20260908-902-functional-no-marker"
    task_dir = kanban_root / "projects" / project_name / "tasks" / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{tester_task_id}\n\n## Role\nTESTER\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{tester_task_id}\n\n## Role\nTESTER\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {version}\n\n"
        "## Recommendation\nPASS\n\n## Systemic Risk\nlow\n\n"
        "## Findings\n\nNo bug, gap, or stale-assertion findings — suites green.\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )

    result_release = _run_cm_script(_RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path)
    assert result_release.returncode == 0, (
        f"cm-release.sh must ship a green report FUNCTIONAL.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    notes_text = (repo_path / "release-notes" / f"{version}.md").read_text(encoding="utf-8")
    assert "## Status\nFUNCTIONAL" in notes_text, (
        f"Expected '## Status' FUNCTIONAL for a green report.\nnotes:\n{notes_text}"
    )

    assert _sha256(bystander_bug) == bystander_sha_before, (
        "A FUNCTIONAL ship must never write the known-issue carry-forward "
        "marker onto any bug file."
    )
    assert "## Known Issue Carry Forward" not in bystander_bug.read_text(encoding="utf-8")


def test_known_bugs_ship_writes_no_known_issue_marker_on_any_bug(tmp_path: pathlib.Path) -> None:
    """A low/medium-risk report recommending SHIP-WITH-CONCERNS ships
    KNOWN-BUGS and must never write the known-issue marker on any bug in
    the fixture — the marker is exclusive to the SHIP-KNOWN-ISSUE row."""
    version = "v0.23.0"
    project_name = "known_bugs_ship_no_marker_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    filed_bug = _write_bug(
        bugs_dir, "BUG-0004", "low-risk-cited-item",
        "A low-severity cosmetic defect cited by the TESTER recommendation.",
    )
    filed_bug_sha_before = _sha256(filed_bug)

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_known_bugs.txt", "known-bugs ship feature content\n",
        "feat: add feature for known-bugs ship test",
        return_branch="main",
    )

    tester_task_id = "TESTER-20260908-903-known-bugs-no-marker"
    task_dir = kanban_root / "projects" / project_name / "tasks" / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{tester_task_id}\n\n## Role\nTESTER\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{tester_task_id}\n\n## Role\nTESTER\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {version}\n\n"
        "## Recommendation\nSHIP-WITH-CONCERNS\n\n## Systemic Risk\nlow\n\n"
        "## Findings\n\n"
        "### Finding 1: a low-risk cosmetic defect\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** low (isolated)\n"
        "**Filed As:** BUG-0004 (Path C)\n\n"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )

    result_release = _run_cm_script(_RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path)
    assert result_release.returncode == 0, (
        f"cm-release.sh must ship a low-risk SHIP-WITH-CONCERNS report KNOWN-BUGS.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    notes_text = (repo_path / "release-notes" / f"{version}.md").read_text(encoding="utf-8")
    assert "## Status\nKNOWN-BUGS" in notes_text, (
        f"Expected '## Status' KNOWN-BUGS for a low-risk SHIP-WITH-CONCERNS report.\nnotes:\n{notes_text}"
    )

    assert _sha256(filed_bug) == filed_bug_sha_before, (
        "A KNOWN-BUGS ship must never write the known-issue carry-forward "
        "marker onto any bug file — the marker is exclusive to a "
        "SHIP-KNOWN-ISSUE decision, not any ship that merely lists filed bugs."
    )
    assert "## Known Issue Carry Forward" not in filed_bug.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Case (d) — the "## Bugs Skipped" render path
# ---------------------------------------------------------------------------


_AGENT_PREFIXES = ["PM", "CODER", "WRITER", "TESTER", "CM", "PO"]


def test_bugs_skipped_section_is_rendered_through_the_scrub_not_hand_typed(tmp_path: pathlib.Path) -> None:
    """End-to-end drive of the render path: a WRITER-authored notes file's
    hand-typed "## Bugs Skipped" table — already carrying a leaked task ID —
    is wholesale replaced by cm-release.sh with a table rendered through
    build_bugs_skipped_notes_block from the TESTER report's own findings,
    via read_ship_policy_skipped_bug_pairs.

    The TESTER report carries one bug/gap finding per kanban agent prefix,
    each with a rationale detail quoting a task ID of that prefix, so the
    assertion covers every agent-prefix identifier the release notes could
    otherwise leak — not just one.
    """
    version = "v0.24.0"
    project_name = "bugs_skipped_render_path_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bug_paths = [
        _write_bug(
            bugs_dir, f"BUG-06{i:02d}", f"{prefix.lower()}-leak",
            f"A defect quoting task {prefix}-20260910-777-some-task inline in its own text.",
        )
        for i, prefix in enumerate(_AGENT_PREFIXES)
    ]

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_bugs_skipped.txt", "bugs-skipped render path feature content\n",
        "feat: add feature for bugs-skipped render path test",
    )

    writer_notes = (
        f"# Release Notes: {project_name} {version}\n\n"
        "**Release Date:** 2026-09-10\n**Released By:** WRITER\n\n"
        "## Status\nPENDING-RELEASE\n\n"
        "## Summary\nTest release for the Bugs-Skipped render path.\n\n"
        "## Bugs Skipped\n"
        "Hand-typed text that itself leaks CODER-20260910-023-some-other-task, "
        "the exact failure mode BUG-0231 describes.\n\n"
        "| Substance (what was wrong) | Public ID (if disclosed) | Severity | Rationale for Skipping |\n"
        "|---|---|---|---|\n"
        "| placeholder | internal-only | LOW | placeholder |\n\n"
        "## Known Issues\nNone.\n"
    )
    (repo_path / "release-notes" / f"{version}.md").write_text(writer_notes, encoding="utf-8")
    _git_local(repo_path, "add", f"release-notes/{version}.md")
    _git_local(repo_path, "commit", "-m", f"docs: WRITER release notes for {version}")
    _git_local(repo_path, "checkout", "main")

    tester_task_id = "TESTER-20260910-904-bugs-skipped-render-path"
    task_dir = kanban_root / "projects" / project_name / "tasks" / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{tester_task_id}\n\n## Role\nTESTER\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{tester_task_id}\n\n## Role\nTESTER\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    findings_text = "".join(
        f"### Finding {i + 1}: leaked id finding {prefix}\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** low (isolated)\n"
        f"**Filed As:** BUG-06{i:02d} (Path C)\n\n"
        f"Deferred; see {prefix}-20260910-777-some-task for the root cause.\n\n"
        for i, prefix in enumerate(_AGENT_PREFIXES)
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {version}\n\n"
        "## Recommendation\nSHIP-WITH-CONCERNS\n\n## Systemic Risk\nlow\n\n"
        f"## Findings\n\n{findings_text}"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )

    result_release = _run_cm_script(_RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path)
    assert result_release.returncode == 0, (
        "cm-release.sh must ship a low-risk SHIP-WITH-CONCERNS report KNOWN-BUGS.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    notes_text = (repo_path / "release-notes" / f"{version}.md").read_text(encoding="utf-8")
    assert "## Status\nKNOWN-BUGS" in notes_text, (
        f"Expected '## Status' KNOWN-BUGS for a low-risk SHIP-WITH-CONCERNS report.\nnotes:\n{notes_text}"
    )
    assert "placeholder" not in notes_text, (
        "The hand-authored placeholder table row must be replaced by the "
        f"rendered table, not left alongside it.\nnotes:\n{notes_text}"
    )
    for prefix in _AGENT_PREFIXES:
        assert f"{prefix}-20260910" not in notes_text, (
            f"A {prefix}-prefixed kanban task ID survived in the stamped "
            f"release notes.\nnotes:\n{notes_text}"
        )
    for bug_path in bug_paths:
        assert bug_path.name.split("-")[0] + "-" + bug_path.name.split("-")[1] not in notes_text, (
            f"The bare internal ticket ID for {bug_path.name} must never "
            f"appear in rendered release notes.\nnotes:\n{notes_text}"
        )
    # The removed identifier is replaced with the scrub's neutral stand-in
    # text ("another item"), not deleted, so the substance sentence still
    # reads as ordinary English around it.
    assert "A defect quoting task another item inline in its own text." in notes_text, (
        "Expected the scrubbed bug substance to still appear in the "
        f"rendered Bugs Skipped table.\nnotes:\n{notes_text}"
    )


# ---------------------------------------------------------------------------
# Case (e) — the "## Bugs Skipped" render path when no WRITER draft ever
# lands on the RC branch at all (no release-notes/<version>.md, no
# PENDING-RELEASE placeholder to stamp).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_prefix", _AGENT_PREFIXES)
def test_bugs_skipped_section_is_rendered_on_the_auto_generated_notes_path(
    agent_prefix: str, tmp_path: pathlib.Path
) -> None:
    """Drives the release pipeline down the path this project actually ships
    on: no release-notes file of any kind is ever committed to the RC
    branch, so the WRITER-draft stamp step has nothing to stamp and the
    generator that runs after the squash writes the notes file from
    scratch. The TESTER report carries one bug finding per kanban agent
    prefix, each rationale quoting a task ID of that prefix, so the six
    parametrized cases each verify — independently, with their own fixture
    and their own pipeline run — that one particular agent prefix's
    identifier never reaches the generated file, while every case also
    guards against a regression that drops the section outright rather
    than merely failing to scrub it.
    """
    case_index = _AGENT_PREFIXES.index(agent_prefix)
    version = f"v0.26.{case_index}"
    project_name = f"bugs_skipped_autogen_path_{agent_prefix.lower()}_test"

    bare_remote = tmp_path / "bare_remote.git"
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, bare_remote)
    kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, push_to_remote="false")

    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    for i, prefix in enumerate(_AGENT_PREFIXES):
        _write_bug(
            bugs_dir, f"BUG-08{i:02d}", f"{prefix.lower()}-autogen-leak",
            f"A defect quoting task {prefix}-20260910-888-some-task inline in its own text.",
        )

    result_open = _run_cm_script(_OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path)
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    rc_branch = f"rc/{version}"
    _commit_on_branch(
        repo_path, rc_branch, "feature_bugs_skipped_autogen.txt",
        "auto-generated bugs-skipped render path feature content\n",
        "feat: add feature for auto-generated bugs-skipped render path test",
        return_branch="main",
    )
    # Deliberately no release-notes/<version>.md committed anywhere on the
    # RC branch: this is what routes the release pipeline's WRITER-draft
    # stamp step down its skip branch and leaves the post-squash generator
    # to write the notes from scratch.

    tester_task_id = f"TESTER-20260910-9{case_index:02d}-bugs-skipped-autogen"
    task_dir = kanban_root / "projects" / project_name / "tasks" / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{tester_task_id}\n\n## Role\nTESTER\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{tester_task_id}\n\n## Role\nTESTER\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    findings_text = "".join(
        f"### Finding {i + 1}: leaked id finding {prefix}\n\n"
        "**Category:** bug\n"
        "**Fix Effort:** small (1-2 CODER tasks)\n"
        "**Systemic Risk:** low (isolated)\n"
        f"**Filed As:** BUG-08{i:02d} (Path C)\n\n"
        f"Deferred; see {prefix}-20260910-888-some-task for the root cause.\n\n"
        for i, prefix in enumerate(_AGENT_PREFIXES)
    )
    (task_dir / "artifacts" / "report.md").write_text(
        f"# Verification Report: {version}\n\n"
        "## Recommendation\nSHIP-WITH-CONCERNS\n\n## Systemic Risk\nlow\n\n"
        f"## Findings\n\n{findings_text}"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )

    result_release = _run_cm_script(_RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path)
    assert result_release.returncode == 0, (
        "cm-release.sh must ship a low-risk SHIP-WITH-CONCERNS report KNOWN-BUGS "
        "on the auto-generated-notes path.\n"
        f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
    )

    notes_path = repo_path / "release-notes" / f"{version}.md"
    assert notes_path.exists(), (
        f"Expected the release pipeline to auto-generate {notes_path} on a "
        "branch carrying no WRITER-authored release-notes draft."
    )
    notes_text = notes_path.read_text(encoding="utf-8")

    assert "## Status\nKNOWN-BUGS" in notes_text, (
        f"Expected '## Status' KNOWN-BUGS for a low-risk SHIP-WITH-CONCERNS report.\nnotes:\n{notes_text}"
    )
    assert "## Bugs Skipped" in notes_text, (
        "Expected the auto-generated notes file to carry a '## Bugs Skipped' "
        "section — a regression that drops the section entirely must fail "
        f"this assertion rather than pass it vacuously.\nnotes:\n{notes_text}"
    )
    assert f"{agent_prefix}-20260910" not in notes_text, (
        f"A {agent_prefix}-prefixed kanban task ID survived in the "
        f"auto-generated release notes.\nnotes:\n{notes_text}"
    )
    # Positive assertion on rendered content: the heading and scrub checks
    # above both pass vacuously when the generator's empty-state branch
    # writes "None" under "## Bugs Skipped" instead of a table — the
    # heading is still there and an empty section has no identifier to
    # leak. Require the scrubbed bug substance itself to have reached the
    # file, so a row that stops arriving fails this test rather than the
    # other two assertions passing it by accident.
    assert "A defect quoting task another item inline in its own text." in notes_text, (
        "Expected the scrubbed bug substance to still appear in the "
        f"auto-generated Bugs Skipped table.\nnotes:\n{notes_text}"
    )
