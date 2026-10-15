"""
test_ship_policy_acceptance.py
===============================
Closing-verification suite for the ship-policy matrix and the operator
--force override: the five acceptance fixtures asserted together against
one tree, each exercising the REAL code paths (cm-open-rc.sh, cm-release.sh,
and the discovery pipeline) rather than a re-implementation of any of them.

Each of those pieces already carries its own unit coverage
(team/tests/unit/test_cm_ship_policy_bash.py,
test_cm_known_issue_carry_forward_bash.py, test_discovery_bundle_marker_
ordering.py, and the lint in test_lint_halt_scope_writers.py). This module
does not re-test any of those internals; it asserts that the five behaviors
listed under Coverage below hold TOGETHER against a
single synthetic tree, the way an operator's real release run would
exercise them.

Coverage (one class per acceptance fixture):
  - TestRegressionHighStopsOnlyItsOwnProject — a systemic_risk=high
    regression finding halts the affected project at PROJECT scope
    (projects/<name>/HALT, never the root HALT), while a second registered
    project with no such finding still ships normally in the same run.
  - TestPreExistingHighSmallShipsAsKnownIssue — a systemic_risk=high finding
    that predates the RC (regression=no), does no integrity damage, and has
    a small fix effort ships instead of halting: release-notes ## Status
    reads KNOWN-ISSUE naming the finding's own substance, and the causing
    bug is marked. A second act, against the SAME project root right after
    the release ships, runs the real bundling path and asserts the marked
    bug leads the next bundle's ## Bundled Items list ahead of an unmarked
    bug filed later (a filename-lexical trap: the marked bug's ID sorts
    AFTER the unmarked one, so naive scan-order bundling would place it
    second — only real marker-honoring bundling produces the asserted order).
  - TestOperatorForceRefusals — --force with no --reason, and --force with
    an agent wake-context signal set, are each refused before any project
    state is touched: no tag, no release-notes commit, the RC branch intact.
  - TestOperatorForceShipsOverriddenTrigger — the same matrix stop first
    HALTs an unforced run (proving the trigger is real, not a fixture
    artifact), then an operator-shaped --force --reason invocation on the
    identical tree passes it and writes both permanent records: the
    Operator Override event in the project's release-state.md, and the
    release-notes ## Status stamped SHIPPED-BY-OPERATOR-OVERRIDE.

Fixture shape: each test builds its own real git repository (a local
non-bare working tree plus a local bare "origin", exactly the pattern
test_cm_release_synthetic.py uses) and a matching kanban root, then drives
cm-open-rc.sh / cm-release.sh / the discovery pipeline as real subprocesses
against that tree. No seam is mocked. The operator's live install
($PGAI_AGENT_KANBAN_ROOT_PATH) is never referenced by any fixture in this
module — every path resolves through pytest's tmp_path.

Measured wall-clock cost: this module's four test classes (six test
functions; the two Regression/KnownIssue/Override classes each drive one
or two full cm-open-rc.sh + cm-release.sh cycles, and the KnownIssue class
additionally drives one discovery_run_pipeline invocation) ran in
approximately 10 seconds on the authoring host (measured standalone via
`pytest tests/integration/test_ship_policy_acceptance.py`). That cost buys end-to-end
proof that the ship-policy matrix, the known-issue carry-forward marker,
the bundle-ordering pass, and the operator override all cohere against a
single tree — a property no single piece's unit suite can show on
its own. Expressing it against a full cm-open-rc.sh/cm-release.sh cycle
(rather than a hand-seeded project root) is deliberate: the whole point of
this closing suite is that the fixtures run through the REAL release
engine, not a stand-in for it.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Optional

from tests.integration.test_cm_release_synthetic import (
    _commit_on_branch,
    _git_branch_exists_local,
    _git_tag_exists,
    _init_git_repo_with_remote,
    _run_cm_script,
)
from tests.integration.test_discovery_pipeline import (
    _bundled_item_order,
    _requirements_dir,
    _run_discovery,
)
import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="ship-policy-acceptance")

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_CM_SCRIPTS_DIR = _TEAM_DIR / "scripts" / "cm"
_OPEN_RC_SCRIPT = _CM_SCRIPTS_DIR / "open-rc.sh"
_RELEASE_SCRIPT = _CM_SCRIPTS_DIR / "release.sh"
_KANBAN_PY_DIR = _TEAM_DIR / "pgai_agent_kanban"
_REAL_WORKFLOWS_DIR = _TEAM_DIR / "workflows"
# Only the "release" workflow type is exercised anywhere in this module
# (every fixture project sets workflow_type=release in project.cfg), so only
# that plugin directory is copied — mirrors tests/fixtures/two_project.py's
# broader copy, narrowed to what this module's fixtures actually load.
_WORKFLOW_PLUGIN_DIRS = ["release"]


# ---------------------------------------------------------------------------
# Kanban-root / project builders
#
# These compose the two existing fixture styles this suite needs at once:
# test_cm_release_synthetic.py's single-project-with-a-real-git-repo builder
# (needed to drive cm-open-rc.sh / cm-release.sh for real), and
# test_discovery_pipeline.py's project-directory shape (needed to drive
# discovery_run_pipeline for real). Neither existing helper supports both a
# real dev tree AND more than one project sharing a kanban root, so this
# module adds the small amount of glue that combination needs.
# ---------------------------------------------------------------------------


def _build_kanban_root(parent: pathlib.Path) -> pathlib.Path:
    """Build an empty (no projects yet) kanban root with the shared
    pgai_agent_kanban.cm python package copied in, exactly as cm-*.sh's
    read_md_field and the changelog writer expect to find it."""
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
    (root / "projects.cfg").write_text("", encoding="utf-8")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # discovery_step_bugs's bundle writer resolves the bundle's Source Branch
    # field through the workflow plugin dispatcher (wf_load_plugin), which
    # looks under $PGAI_AGENT_KANBAN_ROOT_PATH/workflows/<type>/ first (see
    # team/scripts/lib/workflow.sh's _wf_resolve_workflows_root). Without a
    # copy here, plugin load fails and _disc_write_bundle returns non-zero —
    # a failure discovery_step_bugs does not currently propagate, so the
    # bundled items still get marked "running" with NO bundle file ever
    # written to disk. Copying the real plugin dir (as
    # tests/fixtures/two_project.py already does for its own fixture) is
    # what makes this module's fixtures actually exercise the write path.
    wf_dir = root / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for plugin_name in _WORKFLOW_PLUGIN_DIRS:
        src_plugin = _REAL_WORKFLOWS_DIR / plugin_name
        if src_plugin.is_dir():
            dest_plugin = wf_dir / plugin_name
            if dest_plugin.exists():
                shutil.rmtree(dest_plugin)
            shutil.copytree(src_plugin, dest_plugin)

    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)
    for py_file in (_KANBAN_PY_DIR / "cm").glob("*.py"):
        (cm_py_dir / py_file.name).write_text(
            py_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return root


def _register_project(root: pathlib.Path, project_name: str, priority: int = 1) -> None:
    """Append one [project:<name>] section to the kanban root's projects.cfg."""
    cfg_path = root / "projects.cfg"
    existing = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    existing += (
        f"[project:{project_name}]\n"
        f"priority={priority}\n"
        f"description=Fixture project {project_name}\n"
        "enabled=true\n\n"
    )
    cfg_path.write_text(existing, encoding="utf-8")


def _build_release_project(
    root: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    *,
    priority: int = 1,
    push_to_remote: str = "false",
) -> pathlib.Path:
    """Register *project_name* and build a project directory wired to the
    real git repo at *repo_path* — dev_tree_path in project.cfg so the
    discovery pipeline's version lookup agrees with the tree
    cm-open-rc.sh/cm-release.sh (given REPO_ROOT directly) actually operate
    on. Includes bugs/ and tasks/queues/ so both the release engine and the
    discovery pipeline find what they each expect."""
    _register_project(root, project_name, priority=priority)
    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)

    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        f"git_repo_url = git@example.com:{project_name}.git\n"
        "git_remote_name = origin\n"
        "workflow_type = release\n"
        "is_self_build = false\n"
        f"push_to_remote = {push_to_remote}\n"
        "\n"
        "[versioning]\n"
        "max_patch = 99\n"
        "max_minor = 9\n"
        "max_major = 0\n",
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

    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "bug_backlog.md").write_text("# Bug Backlog\n", encoding="utf-8")
    (queues_dir / "priority_backlog.md").write_text("# Priority Backlog\n", encoding="utf-8")

    (proj / "bugs").mkdir(parents=True, exist_ok=True)

    return proj


# ---------------------------------------------------------------------------
# TESTER task + report + bug builders
# ---------------------------------------------------------------------------

_FINDING_BLOCK = """### Finding {n}: {title}

**Category:** {category}
**Fix Effort:** {fix_effort}
**Systemic Risk:** {risk} ({risk_note})
**Regression:** {regression}
**Integrity:** {integrity}
**Filed As:** {filed_as}

"""


def _write_tester_report(
    report_path: pathlib.Path,
    version: str,
    *,
    recommendation: str = "pass",
    systemic_risk: str = "none",
    findings: Optional[list] = None,
) -> None:
    blocks = "".join(
        _FINDING_BLOCK.format(
            n=i + 1,
            title=f.get("title", "a finding"),
            category=f.get("category", "bug"),
            fix_effort=f.get("fix_effort", "small"),
            risk=f.get("risk", "high"),
            risk_note=f.get("risk_note", "note"),
            regression=f.get("regression", "no"),
            integrity=f.get("integrity", "none"),
            filed_as=f.get("filed_as", "none"),
        )
        for i, f in enumerate(findings or [])
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        f"# Verification Report: {version}\n\n"
        f"## Recommendation\n{recommendation}\n\n"
        f"## Systemic Risk\n{systemic_risk}\n\n"
        "## Findings\n\n"
        f"{blocks}"
        "## Lint Findings (non-blocking)\n\nNone.\n",
        encoding="utf-8",
    )


def _add_tester_task(
    kanban_root: pathlib.Path,
    project_name: str,
    version: str,
    *,
    # provenance-allowlist: synthetic fixture task ID (test DATA the release
    # engine's task-scan logic reads), not a reference to a real task.
    task_id: str = "TESTER-20260601-001-verify-release",
    state: str = "DONE",
    recommendation: str = "pass",
    systemic_risk: str = "none",
    findings: Optional[list] = None,
) -> pathlib.Path:
    """Create a TESTER task (README + status + artifacts/report.md) for
    *version* — the shape team/scripts/lib/cm/release_ship_policy.sh scans
    for (## Role / ## Release Version in README.md, ## State in status.md,
    artifacts/report.md for the report fields)."""
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n"
        "## Role\n"
        "TESTER\n\n"
        "## Release Version\n"
        f"{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## State\n{state}\n",
        encoding="utf-8",
    )
    report_path = task_dir / "artifacts" / "report.md"
    _write_tester_report(
        report_path,
        version,
        recommendation=recommendation,
        systemic_risk=systemic_risk,
        findings=findings,
    )
    return report_path


_BUG_TEMPLATE = """# {bug_id}: {slug}

**Bug ID:** {bug_id}-{slug}
**Filed By:** TESTER
**Severity:** high

## Status
open

## Category
misc

---

## Symptom
{symptom}

## Expected
Expected behavior, unaffected by this pre-existing defect.

## Actual
{symptom}
"""


def _add_bug(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str,
    slug: str,
    symptom: str,
) -> pathlib.Path:
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_path = bugs_dir / f"{bug_id}-{slug}.md"
    bug_path.write_text(
        _BUG_TEMPLATE.format(bug_id=bug_id, slug=slug, symptom=symptom),
        encoding="utf-8",
    )
    return bug_path


def _read_known_issue_carry_forward(bug_path: pathlib.Path) -> str:
    """Return the value stamped under a bug file's '## Known Issue Carry
    Forward' heading, or "" when the heading is absent or empty."""
    text = bug_path.read_text(encoding="utf-8")
    marker = "## Known Issue Carry Forward"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    for line in tail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return ""
        return stripped
    return ""


def _release_notes_text(repo_path: pathlib.Path, version: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "show", f"main:release-notes/{version}.md"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"release-notes/{version}.md not found on main.\nstderr: {result.stderr}"
    )
    return result.stdout


def _release_state_text(kanban_root: pathlib.Path, project_name: str) -> str:
    return (kanban_root / "projects" / project_name / "release-state.md").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Fixture 1 — regression-high stops only its own project
# ---------------------------------------------------------------------------


class TestRegressionHighStopsOnlyItsOwnProject:
    """A live regression at systemic_risk=high halts its own project at
    PROJECT scope; a sibling project with no such finding ships normally in
    the same run — proving the stop is scoped to the project it belongs to,
    not to the fleet."""

    def test_regression_high_halts_project_scope_while_sibling_project_ships(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two registered projects, one release run each.

        project_a carries a TESTER report with systemic_risk=high and
        Regression: yes on its one finding — the matrix's Trigger 2 refuses
        to ship it, and the refusal must create projects/project_a/HALT,
        never the root-level HALT (which would freeze every project's
        chain, not just project_a's).

        project_b has no TESTER task at all for its release version (the
        "no report, Test Required: false" row), so it ships as an ordinary
        SHIP-FUNCTIONAL release in the same test run — proving project_a's
        stop did not reach it.
        """
        version = "v0.9.0"
        kanban_root = _build_kanban_root(tmp_path)

        # --- project_a: regression-high finding, must HALT at project scope ---
        repo_a = tmp_path / "repo_a"
        _init_git_repo_with_remote(repo_a, tmp_path / "bare_a.git")
        _build_release_project(kanban_root, "project_a", repo_a, priority=1)

        result_open_a = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, "project_a", repo_root=repo_a,
        )
        assert result_open_a.returncode == 0, (
            f"cm-open-rc.sh failed for project_a.\nstdout: {result_open_a.stdout}\n"
            f"stderr: {result_open_a.stderr}"
        )
        _commit_on_branch(
            repo_a, f"rc/{version}", "feature_a.txt", "project_a work\n",
            "feat: project_a RC work", return_branch="main",
        )
        _add_tester_task(
            kanban_root, "project_a", version,
            systemic_risk="high",
            findings=[{
                "title": "a live regression introduced by this RC",
                "risk": "high",
                "risk_note": "breaks the dashboard on every render",
                "regression": "yes",
                "integrity": "none",
                "fix_effort": "small",
            }],
        )

        result_release_a = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, "project_a", repo_root=repo_a,
        )

        assert result_release_a.returncode != 0, (
            "cm-release.sh must refuse to ship a live systemic_risk=high regression.\n"
            f"stdout: {result_release_a.stdout}\nstderr: {result_release_a.stderr}"
        )
        combined_a = result_release_a.stdout + result_release_a.stderr
        assert "Trigger 2" in combined_a, (
            f"Expected the Trigger 2 (systemic_risk=high) HALT.\noutput: {combined_a}"
        )

        project_a_halt = kanban_root / "projects" / "project_a" / "HALT"
        root_halt = kanban_root / "HALT"
        assert project_a_halt.is_file(), (
            f"Expected the project-scoped HALT file at {project_a_halt}.\n"
            f"stdout: {result_release_a.stdout}\nstderr: {result_release_a.stderr}"
        )
        assert not root_halt.exists(), (
            "The regression-high stop must land at PROJECT scope only — a root-level "
            f"HALT file at {root_halt} would freeze every project's chain, not just "
            "project_a's, and this fixture must fail if that ever happens."
        )
        assert not _git_tag_exists(repo_a, version), (
            f"No tag may exist for project_a's {version} after a HALT.\n"
            f"stdout: {result_release_a.stdout}"
        )

        # --- project_b: no TESTER task for this version — ships normally ---
        repo_b = tmp_path / "repo_b"
        _init_git_repo_with_remote(repo_b, tmp_path / "bare_b.git")
        _build_release_project(kanban_root, "project_b", repo_b, priority=2)

        result_open_b = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, "project_b", repo_root=repo_b,
        )
        assert result_open_b.returncode == 0, (
            f"cm-open-rc.sh failed for project_b.\nstdout: {result_open_b.stdout}\n"
            f"stderr: {result_open_b.stderr}"
        )
        _commit_on_branch(
            repo_b, f"rc/{version}", "feature_b.txt", "project_b work\n",
            "feat: project_b RC work", return_branch="main",
        )

        result_release_b = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, "project_b", repo_root=repo_b,
        )

        assert result_release_b.returncode == 0, (
            "project_b must ship normally in the same run that halted project_a — "
            "a fleet-scoped stop would break this.\n"
            f"stdout: {result_release_b.stdout}\nstderr: {result_release_b.stderr}"
        )
        assert _git_tag_exists(repo_b, version), (
            f"Expected project_b's release tag {version} after a successful ship.\n"
            f"stdout: {result_release_b.stdout}"
        )
        assert not (kanban_root / "projects" / "project_b" / "HALT").exists(), (
            "project_b must carry no HALT file of its own."
        )


# ---------------------------------------------------------------------------
# Fixture 2 + 3 — pre-existing-high-small ships as KNOWN-ISSUE, and the
# causing bug leads the next bundle
# ---------------------------------------------------------------------------


class TestPreExistingHighSmallShipsAsKnownIssue:
    """A single test function drives both acts against the SAME project
    root, in order, per the ordering trap the task notes call out: the
    bundling run must happen after the release wrote the marker, against
    the same project root, or the two acts assert unrelated things that
    both happen to pass. Anti-pattern 3 (test-authoring-guidelines.md)
    permits a multi-cycle sequence living inside one test function when the
    docstring documents the step order — this is that case."""

    def test_known_issue_ship_writes_both_records_and_next_bundle_leads_with_the_causing_bug(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Act 1: release ships as SHIP-KNOWN-ISSUE.

        A pre-existing (Regression: no), non-integrity-damaging
        (Integrity: none), small-effort (Fix Effort: small) systemic_risk=high
        finding must SHIP rather than HALT: release-notes ## Status reads
        KNOWN-ISSUE, the '## Known Issues' section names the finding's own
        substance (not just its category), and the causing bug file is
        stamped with '## Known Issue Carry Forward: <version>'.

        Act 2: the marked bug leads the next bundle.

        A second, unrelated bug is filed AFTER the release, with no marker,
        into the same project's bugs/ directory. The causing bug's own
        fixture ID is chosen to sort lexicographically AFTER the unrelated
        one's, deliberately: with that ordering, scan order alone would
        place the unrelated bug first, so only real marker-honoring
        bundling — not incidental scan order — can produce the causing bug
        first in the rendered bundle. Both bugs remain ## Status: open, and
        the project's Active RC has cleared to 'none' after Act 1 (a normal
        release completion), so the discovery pipeline is free to bundle
        both when Act 2 runs discovery_run_pipeline for real.
        """
        version = "v0.1.0"
        project_name = "known_issue_project"
        kanban_root = _build_kanban_root(tmp_path)
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, tmp_path / "bare_remote.git")
        _build_release_project(kanban_root, project_name, repo_path)

        # The causing bug is pre-existing (filed before the RC even opens).
        # provenance-allowlist: synthetic fixture bug ID (test DATA the
        # bundle-ordering assertion below sorts against), not a real bug.
        causing_bug = _add_bug(
            kanban_root, project_name, "BUG-0500", "legacy-rendering-defect",
            "A pre-existing crash triggered by a code path nobody exercises in CI.",
        )

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        _commit_on_branch(
            repo_path, f"rc/{version}", "feature_known_issue.txt",
            "coder work for the known-issue release\n",
            "feat: add feature for known-issue release", return_branch="main",
        )
        _add_tester_task(
            kanban_root, project_name, version,
            systemic_risk="high",
            findings=[{
                "title": "legacy rendering defect",
                "risk": "high",
                "risk_note": "pre-existing, unrelated to this RC",
                "regression": "no",
                "integrity": "none",
                "fix_effort": "small",
                # provenance-allowlist: synthetic fixture bug ID (test DATA
                # this Filed As field must resolve against), not a real bug.
                "filed_as": "BUG-0500",
            }],
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            "A pre-existing, non-integrity-damaging, small-effort high finding must "
            "SHIP rather than HALT.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )

        notes_text = _release_notes_text(repo_path, version)
        assert "## Status" in notes_text and "KNOWN-ISSUE" in notes_text, (
            f"Expected release-notes ## Status: KNOWN-ISSUE.\nnotes:\n{notes_text}"
        )
        assert "legacy rendering defect" in notes_text.lower() or (
            "pre-existing crash" in notes_text.lower()
        ), (
            "The '## Known Issues' section must name the finding's own substance, "
            f"not just its category.\nnotes:\n{notes_text}"
        )

        carried_version = _read_known_issue_carry_forward(causing_bug)
        assert carried_version == version, (
            f"Expected the causing bug ({causing_bug}) to carry "
            f"'## Known Issue Carry Forward: {version}'; got '{carried_version}'.\n"
            f"bug file:\n{causing_bug.read_text(encoding='utf-8')}"
        )

        # --- Act 2: run the real bundling path against the SAME project root ---
        # provenance-allowlist: synthetic fixture bug ID (test DATA, chosen
        # to sort before the causing bug's ID above), not a real bug.
        later_bug = _add_bug(
            kanban_root, project_name, "BUG-0100", "unrelated-later-bug",
            "An unrelated defect filed after the known-issue release shipped.",
        )
        assert causing_bug.name > later_bug.name, (
            "This fixture requires the marked bug's filename to sort AFTER the "
            "unmarked one so scan order alone cannot produce the asserted result."
        )

        active_rc_after_release = _release_state_text(kanban_root, project_name)
        assert "none" in active_rc_after_release.split("## Active RC", 1)[1].splitlines()[1], (
            "Active RC must have cleared after the release completed, or the "
            "discovery pipeline's Active-RC gate will defer bundling.\n"
            f"release-state.md:\n{active_rc_after_release}"
        )

        result_discovery = _run_discovery(kanban_root, project_name)
        assert result_discovery.returncode == 0, (
            f"discovery_run_pipeline failed.\nstdout: {result_discovery.stdout}\n"
            f"stderr: {result_discovery.stderr}"
        )

        bundle_files = list(
            _requirements_dir(kanban_root, project_name).glob("*-bugfix-bundle-*.md")
        )
        assert bundle_files, (
            f"No bug bundle was produced.\nstdout: {result_discovery.stdout}"
        )
        order = _bundled_item_order(bundle_files[0])
        assert order and order[0] == causing_bug.name, (
            f"Expected the marked bug ({causing_bug.name}) to lead the rendered "
            f"bundle ahead of {later_bug.name}; got order {order}. The fixture must "
            "fail here if the front-of-bundle marker is not honored."
        )


# ---------------------------------------------------------------------------
# Fixture 4 — operator --force refusals ship nothing
# ---------------------------------------------------------------------------


class TestFindingHeadingShapeReachesTheShipDecision:
    """The release script's ship decision reads a finding's Regression and
    Integrity fields under any finding heading level, and says so on its
    own Step 4d output when a report carries no finding block at all — so
    an operator can tell a report-shape problem from a finding that really
    reads unknown."""

    def test_level_4_finding_heading_ships_the_same_known_issue_as_level_3(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The clear-to-ship shape — Regression: no, Integrity: none, Fix
        Effort: small on the one systemic_risk=high finding — must reach
        SHIP-KNOWN-ISSUE when the report nests that finding under a
        "#### Finding" heading exactly as it does under "### Finding", and
        the step must not narrate a shape problem for a report that has
        finding blocks."""
        version = "v0.2.0"
        project_name = "level4_known_issue_project"
        kanban_root = _build_kanban_root(tmp_path)
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, tmp_path / "bare_remote.git")
        _build_release_project(kanban_root, project_name, repo_path)

        # provenance-allowlist: synthetic fixture bug ID (test DATA the
        # finding's Filed As field resolves against), not a real bug.
        _add_bug(
            kanban_root, project_name, "BUG-0510", "legacy-parser-defect",
            "A pre-existing crash on an input shape nobody feeds the parser in CI.",
        )
        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        _commit_on_branch(
            repo_path, f"rc/{version}", "feature_level4.txt",
            "coder work for the level-4 heading release\n",
            "feat: add feature for the level-4 heading release", return_branch="main",
        )
        report_path = _add_tester_task(
            kanban_root, project_name, version,
            systemic_risk="high",
            findings=[{
                "title": "legacy parser defect",
                "risk": "high",
                "risk_note": "pre-existing, unrelated to this RC",
                "regression": "no",
                "integrity": "none",
                "fix_effort": "small",
                # provenance-allowlist: synthetic fixture bug ID (test DATA
                # this Filed As field must resolve against), not a real bug.
                "filed_as": "BUG-0510",
            }],
        )
        # Demote the one finding heading a level, as a report that groups its
        # findings under intermediate sub-section headings does.
        report_text = report_path.read_text(encoding="utf-8")
        assert "### Finding 1:" in report_text
        report_path.write_text(
            report_text.replace("### Finding 1:", "#### Finding 1:"), encoding="utf-8",
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
        )
        combined = result_release.stdout + result_release.stderr
        assert result_release.returncode == 0, (
            "A clear-to-ship high finding under a level-4 heading must SHIP as a "
            f"known issue exactly as it does under a level-3 heading.\noutput: {combined}"
        )
        assert "Regression:           no" in combined, combined
        assert "Integrity damage:     none" in combined, combined
        assert "SHIP-KNOWN-ISSUE" in combined, combined
        assert "Finding reader:" not in combined, (
            f"A report with finding blocks must not narrate a shape problem.\noutput: {combined}"
        )
        assert _git_tag_exists(repo_path, version), combined

    def test_report_without_finding_blocks_halts_and_names_the_shape_problem(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A systemic_risk=high report with no finding heading at all still
        refuses to ship (the two dimensions default to unknown, which never
        clears the row), and Step 4d's own output says why: zero finding
        blocks in the named report, regression/integrity defaulted to
        unknown for that reason. The stop lands at project scope."""
        version = "v0.3.0"
        project_name = "no_finding_blocks_project"
        kanban_root = _build_kanban_root(tmp_path)
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, tmp_path / "bare_remote.git")
        _build_release_project(kanban_root, project_name, repo_path)

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        _commit_on_branch(
            repo_path, f"rc/{version}", "feature_no_blocks.txt",
            "coder work for the no-finding-blocks release\n",
            "feat: add feature for the no-finding-blocks release", return_branch="main",
        )
        report_path = _add_tester_task(
            kanban_root, project_name, version,
            recommendation="ship-with-serious-concerns",
            systemic_risk="high",
            findings=None,
        )
        report_text = report_path.read_text(encoding="utf-8")
        assert "## Findings" in report_text and "Finding 1:" not in report_text

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
        )
        combined = result_release.stdout + result_release.stderr
        assert result_release.returncode != 0, (
            f"A high-risk report with no finding block must not ship.\noutput: {combined}"
        )
        assert "Trigger 2" in combined, combined
        narration = [line for line in combined.splitlines() if "Finding reader:" in line]
        assert narration, (
            f"Step 4d must narrate the zero-block report on its own output.\noutput: {combined}"
        )
        assert any("zero finding blocks" in line for line in narration), narration
        assert any(str(report_path) in line for line in narration), narration
        assert any("regression/integrity defaulted to unknown" in line for line in narration), narration
        assert "Regression:           unknown" in combined, combined
        assert "Integrity damage:     unknown" in combined, combined
        assert (kanban_root / "projects" / project_name / "HALT").is_file(), combined
        assert not (kanban_root / "HALT").exists(), combined
        assert not _git_tag_exists(repo_path, version), combined


class TestOperatorForceRefusals:
    """Both --force refusal paths ship nothing: the refusals fire before any
    project or repo state is touched, so the RC branch survives intact and
    no tag or release-notes commit appears — asserted separately for the
    missing-reason case and the agent-wake-context case. As a red-case
    control, this project's TESTER-free setup would otherwise ship cleanly
    (SHIP-FUNCTIONAL, the "no report" row) if --force were incorrectly let
    through — so a refusal-detection regression here would flip these
    "ships nothing" assertions to failures, not leave them vacuously true.
    """

    def _build_project(self, tmp_path: pathlib.Path, version: str):
        project_name = "force_refusal_project"
        kanban_root = _build_kanban_root(tmp_path)
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, tmp_path / "bare_remote.git")
        _build_release_project(kanban_root, project_name, repo_path)

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        _commit_on_branch(
            repo_path, f"rc/{version}", "feature.txt", "coder work\n",
            "feat: add feature", return_branch="main",
        )
        return kanban_root, project_name, repo_path

    def test_force_without_reason_is_refused_and_ships_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        version = "v0.11.0"
        kanban_root, project_name, repo_path = self._build_project(tmp_path, version)
        rc_branch = f"rc/{version}"

        result = _run_cm_script(
            _RELEASE_SCRIPT, ["--force"], kanban_root, project_name, repo_root=repo_path,
        )

        assert result.returncode != 0, (
            f"--force with no --reason must be refused.\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "cm-release.operator-force.reason-required" in combined, (
            f"Expected the reason-required error key in the refusal.\noutput: {combined}"
        )
        assert not _git_tag_exists(repo_path, version), "No tag may exist after the refusal."
        assert _git_branch_exists_local(repo_path, rc_branch), (
            "The RC branch must survive a refused --force invocation untouched."
        )
        assert not (kanban_root / "projects" / project_name / "HALT").exists(), (
            "An argument-validation refusal is not a ship-policy HALT and must not "
            "write a HALT file."
        )

    def test_force_from_agent_wake_context_is_refused_and_ships_nothing(
        self, tmp_path: pathlib.Path
    ) -> None:
        version = "v0.12.0"
        kanban_root, project_name, repo_path = self._build_project(tmp_path, version)
        rc_branch = f"rc/{version}"

        result = _run_cm_script(
            _RELEASE_SCRIPT,
            ["--force", "--reason", "operator says ship it anyway"],
            kanban_root, project_name, repo_root=repo_path,
            extra_env={"PGAI_AGENT_WAKE_ACTIVE": "1"},
        )

        assert result.returncode != 0, (
            f"--force under an agent wake context must be refused even with a "
            f"valid --reason.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "cm-release.operator-force.agent-wake-refused" in combined, (
            f"Expected the agent-wake-refused error key in the refusal.\noutput: {combined}"
        )
        assert not _git_tag_exists(repo_path, version), "No tag may exist after the refusal."
        assert _git_branch_exists_local(repo_path, rc_branch), (
            "The RC branch must survive a refused --force invocation untouched."
        )


# ---------------------------------------------------------------------------
# Fixture 5 — a forced ship writes both permanent records
# ---------------------------------------------------------------------------


class TestOperatorForceShipsOverriddenTrigger:
    """The same synthetic tree is released twice: once unforced (proving the
    matrix stop is real — the red case), then again with a proper operator
    --force --reason (the green case), asserting both permanent records the
    override leaves behind."""

    def test_unforced_run_halts_then_forced_run_ships_and_writes_both_override_records(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Step 1: build a tree whose TESTER report trips Trigger 3
        (SHIP-WITH-SERIOUS-CONCERNS with a large-effort finding).

        Step 2 (red case): an unforced cm-release.sh run HALTs on Trigger 3 —
        proving the stop is a real matrix decision on this tree, not a
        fixture artifact that would pass regardless of --force's behavior.

        Step 3 (green case): re-invoking cm-release.sh with a valid
        operator --force --reason (no agent-wake signal) on the SAME tree
        passes the same stop and ships: release-notes ## Status reads
        SHIPPED-BY-OPERATOR-OVERRIDE, and the project's release-state.md
        carries an '## Operator Override' event naming the overridden
        trigger and the operator's reason. The fixture fails if either
        record is missing.
        """
        version = "v0.13.0"
        project_name = "force_override_project"
        kanban_root = _build_kanban_root(tmp_path)
        repo_path = tmp_path / "dev_repo"
        _init_git_repo_with_remote(repo_path, tmp_path / "bare_remote.git")
        _build_release_project(kanban_root, project_name, repo_path)

        result_open = _run_cm_script(
            _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_open.returncode == 0, (
            f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
        )
        _commit_on_branch(
            repo_path, f"rc/{version}", "feature_override.txt",
            "coder work needing an operator override\n",
            "feat: add feature requiring operator override", return_branch="main",
        )
        _add_tester_task(
            kanban_root, project_name, version,
            recommendation="ship-with-serious-concerns",
            systemic_risk="medium",
            findings=[{
                "title": "a large-effort concern out of auto-ship scope",
                "category": "bug",
                "fix_effort": "large",
                "risk": "medium",
                "risk_note": "isolated, but the fix is substantial",
                "regression": "yes",
                "integrity": "none",
            }],
        )

        # --- Step 2 (red case): unforced run HALTs on Trigger 3 ---
        result_unforced = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_unforced.returncode != 0, (
            "An unforced release must HALT on Trigger 3 (serious concerns + large "
            "fix effort) — if this assertion ever passes trivially, the fixture's "
            "override coverage below is not testing anything real.\n"
            f"stdout: {result_unforced.stdout}\nstderr: {result_unforced.stderr}"
        )
        combined_unforced = result_unforced.stdout + result_unforced.stderr
        assert "Trigger 3" in combined_unforced, (
            f"Expected the Trigger 3 HALT.\noutput: {combined_unforced}"
        )
        assert not _git_tag_exists(repo_path, version), (
            "No tag may exist after the unforced HALT."
        )
        pre_override_state = _release_state_text(kanban_root, project_name)
        assert "Operator Override" not in pre_override_state, (
            "No Operator Override event may exist before --force is ever invoked."
        )

        # --- Step 3 (green case): forced run passes the same stop and ships ---
        reason = "operator accepts the large-effort concern for this release"
        result_forced = _run_cm_script(
            _RELEASE_SCRIPT, ["--force", "--reason", reason],
            kanban_root, project_name, repo_root=repo_path,
        )
        assert result_forced.returncode == 0, (
            "A properly-reasoned operator --force must pass Trigger 3 and ship.\n"
            f"stdout: {result_forced.stdout}\nstderr: {result_forced.stderr}"
        )
        assert _git_tag_exists(repo_path, version), (
            f"Expected the release tag {version} after the forced ship.\n"
            f"stdout: {result_forced.stdout}"
        )

        notes_text = _release_notes_text(repo_path, version)
        assert "SHIPPED-BY-OPERATOR-OVERRIDE" in notes_text, (
            f"Expected release-notes ## Status: SHIPPED-BY-OPERATOR-OVERRIDE.\n"
            f"notes:\n{notes_text}"
        )

        post_override_state = _release_state_text(kanban_root, project_name)
        assert "## Operator Override" in post_override_state, (
            f"Expected an '## Operator Override' event in release-state.md.\n"
            f"release-state.md:\n{post_override_state}"
        )
        assert "cm-release.trigger-3.serious-concerns-large-fix" in post_override_state, (
            "The Operator Override event must name the trigger it overrode.\n"
            f"release-state.md:\n{post_override_state}"
        )
        assert reason in post_override_state, (
            "The Operator Override event must carry the operator's own reason text.\n"
            f"release-state.md:\n{post_override_state}"
        )


# ---------------------------------------------------------------------------
# Unreadable ship-policy inputs: the release never HALTs on them and never
# takes the permissive FUNCTIONAL row -- it ships with concerns and files
# the bug that carries the document defect into the next bundle.
# ---------------------------------------------------------------------------


def _open_rc_with_one_commit(
    tmp_path: pathlib.Path, project_name: str, version: str
) -> tuple:
    """Build a kanban root + real git repo, open <version>'s RC and put one
    commit on it, returning (kanban_root, repo_path)."""
    kanban_root = _build_kanban_root(tmp_path)
    repo_path = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo_path, tmp_path / "bare_remote.git")
    _build_release_project(kanban_root, project_name, repo_path)
    result_open = _run_cm_script(
        _OPEN_RC_SCRIPT, [version], kanban_root, project_name, repo_root=repo_path,
    )
    assert result_open.returncode == 0, (
        f"cm-open-rc.sh failed.\nstdout: {result_open.stdout}\nstderr: {result_open.stderr}"
    )
    _commit_on_branch(
        repo_path, f"rc/{version}", "feature.txt", "coder work\n",
        "feat: add feature", return_branch="main",
    )
    return kanban_root, repo_path


def _filed_ship_policy_bugs(kanban_root: pathlib.Path, project_name: str) -> list:
    return sorted(
        p for p in (kanban_root / "projects" / project_name / "bugs").glob("BUG-*.md")
        if "**Filed By:** cm-release ship-policy (autonomous)" in p.read_text(encoding="utf-8")
    )


class TestUnreadableInputsShipWithConcernsAndFileABug:
    """Each test drives one real cm-open-rc.sh + cm-release.sh cycle whose
    TESTER inputs carry a formatting defect the exact-string matrix used to
    fail open on (or halt on). The release must still ship, take the row the
    document names (or the SHIP-WITH-CONCERNS floor when no row can be
    named), and leave the bug record behind in the project's bugs/."""

    def test_garbled_recommendation_ships_known_bugs_and_files_a_bug_quoting_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        version = "v0.2.0"
        project_name = "garbled_value_project"
        kanban_root, repo_path = _open_rc_with_one_commit(tmp_path, project_name, version)
        report_path = _add_tester_task(
            kanban_root, project_name, version,
            recommendation="Recommendation: SHIPP-WITH-CONCERNZ",
            systemic_risk="low",
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            "An unparseable Recommendation must ship (with concerns), never HALT.\n"
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )
        assert "Ship decision:       SHIP-KNOWN-BUGS" in result_release.stdout, (
            f"Expected the SHIP-WITH-CONCERNS row, never SHIP-FUNCTIONAL.\nstdout: {result_release.stdout}"
        )
        notes_text = _release_notes_text(repo_path, version)
        assert "KNOWN-BUGS" in notes_text and "## Status\nFUNCTIONAL" not in notes_text, notes_text
        assert not (kanban_root / "projects" / project_name / "HALT").exists()

        filed = _filed_ship_policy_bugs(kanban_root, project_name)
        assert len(filed) == 1, f"expected exactly one ship-policy bug; got {filed}"
        assert filed[0].name.endswith("-unparseable-recommendation-value.md")
        content = filed[0].read_text(encoding="utf-8")
        assert "Recommendation: SHIPP-WITH-CONCERNZ" in content
        assert str(report_path) in content
        assert "## Status\nopen" in content
        assert filed[0].name in result_release.stdout, (
            f"Step 4d must name the bug it filed.\nstdout: {result_release.stdout}"
        )

    def test_bold_serious_concerns_ships_non_functional_not_functional(
        self, tmp_path: pathlib.Path
    ) -> None:
        version = "v0.2.1"
        project_name = "bold_value_project"
        kanban_root, repo_path = _open_rc_with_one_commit(tmp_path, project_name, version)
        _add_tester_task(
            kanban_root, project_name, version,
            recommendation="**Recommendation:** **SHIP-WITH-SERIOUS-CONCERNS**",
            systemic_risk="Low — nothing systemic",
            findings=[{"title": "a small one", "risk": "low", "fix_effort": "small"}],
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )
        assert "Ship decision:       SHIP-NON-FUNCTIONAL" in result_release.stdout, result_release.stdout
        notes_text = _release_notes_text(repo_path, version)
        assert "NON-FUNCTIONAL" in notes_text, notes_text
        assert _filed_ship_policy_bugs(kanban_root, project_name) == [], (
            "a value that normalizes cleanly is not a defect and files nothing"
        )

    def test_decorated_readme_role_still_finds_the_task_and_files_a_lookup_bug(
        self, tmp_path: pathlib.Path
    ) -> None:
        version = "v0.2.2"
        project_name = "lookup_mismatch_project"
        kanban_root, repo_path = _open_rc_with_one_commit(tmp_path, project_name, version)
        report_path = _add_tester_task(
            kanban_root, project_name, version, recommendation="PASS", systemic_risk="low",
        )
        readme = report_path.parent.parent / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8")
            .replace("## Role\nTESTER\n", "## Role\n**TESTER**\n")
            .replace(f"## Release Version\n{version}\n", f"## Release Version\n`{version}` (bugfix)\n"),
            encoding="utf-8",
        )

        result_release = _run_cm_script(
            _RELEASE_SCRIPT, [], kanban_root, project_name, repo_root=repo_path,
        )
        assert result_release.returncode == 0, (
            f"stdout: {result_release.stdout}\nstderr: {result_release.stderr}"
        )
        assert f"TESTER task:         {report_path.parent.parent.name}" in result_release.stdout, (
            "a Role / Release Version that matches only after normalization must still "
            f"locate the task, never the test-not-required row.\nstdout: {result_release.stdout}"
        )
        assert "Ship decision:       SHIP-FUNCTIONAL" in result_release.stdout, result_release.stdout
        assert "only after normalization" in result_release.stderr, result_release.stderr

        filed = _filed_ship_policy_bugs(kanban_root, project_name)
        assert sorted(p.name.split("-", 2)[2] for p in filed) == [
            "tester-lookup-release-version.md",
            "tester-lookup-role.md",
        ], f"expected one lookup bug per mismatched field; got {[p.name for p in filed]}"
        for bug in filed:
            content = bug.read_text(encoding="utf-8")
            assert str(readme) in content
            assert "**TESTER**" in content or f"`{version}` (bugfix)" in content
