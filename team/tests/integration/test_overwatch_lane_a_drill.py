"""
test_overwatch_lane_a_drill.py
================================
Integration tests for the OVERWATCH Lane A recovery playbook, steps (b)
through (f) — pgai_agent_kanban.overwatch.resolution_stage._run_lane_a() /
execute_lane_a_drill(), reached from run_resolution_stage() when the
three-lane classifier (pgai_agent_kanban.overwatch.resolution_matrix.
classify_lane()) returns LANE_A.

Coverage:
  - Golden path: a Lane A block runs the drill through the REAL
    team/scripts/unwind-rc.sh and team/scripts/cm/cancel-rc.sh subprocesses
    (not mocks), deletes the bundle doc via the real ops delete_item(),
    clears the ship-policy HALT, and writes exactly one Lane A ledger entry
    plus one "## OVERWATCH Decision" event (Lane: A).
  - The single-attempt guard: a second HALT that classifies Lane A against
    the SAME regression_bug_id does not run the drill again — it escalates
    to Lane C, writes a second Decision event (Lane: C) whose evidence
    carries both attempts, and leaves the second HALT file, the second
    bundle doc, and Active RC untouched.
  - An operator-authored HALT alongside the ship-policy HALT declines
    before the three-lane classifier is ever consulted, exactly as the
    pre-existing (non-Lane-A) path already guarantees.
  - The rewrite precondition: a Lane A block whose
    blamed bug carries NO attributed OVERWATCH Fix rewrite declines steps
    (b)-(e) entirely — RC not unwound, not cancelled, bundle doc still
    present, HALT still set, no ledger entry written — rather than assume
    step (a) already ran. The full playbook test below proves the
    complementary case: once step (a) has run (via the real CLI), the
    drill proceeds.

Design:
  - Every fixture here deliberately gives the RC branch a direct (non-
    merge) commit ahead of its base branch, so operator_commits_in_lineage
    extracts True and the block therefore FAILS the shipped
    CANCEL-AND-REBUNDLE matrix row (which requires that fact False) before
    ever reaching classify_lane() — the same technique
    team/tests/unit/test_overwatch_lane_classification.py documents in its
    own module docstring (there, by leaving dev_tree_path unset; here, by
    a real git commit, since the Lane A drill itself needs a real git
    dev tree for unwind-rc.sh/cancel-rc.sh to operate on).
  - unwind-rc.sh and cm/cancel-rc.sh are invoked as REAL subprocesses
    against a temp kanban root, mirroring test_overwatch_resolution_stage.py's
    own technique: the live-install layout (scripts/ copied wholesale,
    pgai_agent_kanban/cm/*.py copied into place) is reproduced under
    tmp_path so both scripts' own `source ../lib/*.sh` and
    `$KANBAN_ROOT/pgai_agent_kanban/cm/*.py` shell-outs resolve exactly as
    they do in a real install.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.overwatch.ledger import ledger_path_for_project, parse_entries
from pgai_agent_kanban.overwatch.resolution_stage import (
    DECLINED,
    EXECUTED,
    LANE_A_MATRIX_ROW,
    run_resolution_stage,
)
from tests.fixtures.binary_guard import require_binary
from tests.integration.helpers import run_entrypoint


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="overwatch-lane-a-drill")


_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_KANBAN_PY_CM_DIR = _TEAM_DIR / "pgai_agent_kanban" / "cm"


# ---------------------------------------------------------------------------
# Fixture builders (adapted from test_overwatch_resolution_stage.py's own
# helpers; duplicated rather than imported cross-module per this test
# tree's established convention of one self-contained fixture set per file)
# ---------------------------------------------------------------------------


def _short_bug_id(bug_id: str) -> str:
    """"BUG-NNNN-some-slug" -> "BUG-NNNN" — classify_lane()'s
    LaneClassification.regression_bug_id (and this drill's ledger
    findings) carry the short BUG-NNNN form, not the full file-slug id."""
    parts = bug_id.split("-")
    return "-".join(parts[:2])


def _run_git(repo_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args], capture_output=True, text=True, check=True
    )


# ---------------------------------------------------------------------------
# Lane A recovery-playbook step (a) — performed through the REAL shipped CLI
# (team/scripts/overwatch-attach-fix-rewrite.sh, which wraps
# pgai_agent_kanban.overwatch.fix_rewrite.rewrite_bug_fix_section()), as a
# subprocess against the REAL dev-tree script — never imported and called
# directly in this test file. This mirrors the same convention this file's
# docstring already documents for unwind-rc.sh/cm/cancel-rc.sh: the shipped
# call path is exercised end to end, not stood in for. The caller supplying
# forbidden_approach/correct_direction/ground_truth_ref stands in for the
# reasoning tier's own judgment call, exactly as it would in a live firing
# (pgai_agent_kanban.overwatch.resolution_stage never derives that content
# itself — see that module's own precondition-guard comment in _run_lane_a).
# ---------------------------------------------------------------------------

_DEFAULT_FORBIDDEN_APPROACH = (
    "Inverting the column-count comparison operator to silence the dashboard "
    "width check (implemented in this RC's own fix)."
)
_DEFAULT_CORRECT_DIRECTION = (
    "Compare column_count using the original comparison direction against the "
    "configured max_width, per the layout spec."
)
_DEFAULT_GROUND_TRUTH_REF = (
    "the bug's own original ## Fix section, applied verbatim in this RC's bundle"
)


def _attach_fix_rewrite_via_cli(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str,
    *,
    forbidden_approach: str = _DEFAULT_FORBIDDEN_APPROACH,
    correct_direction: str = _DEFAULT_CORRECT_DIRECTION,
    ground_truth_ref: str = _DEFAULT_GROUND_TRUTH_REF,
) -> None:
    script = _TEAM_DIR / "scripts" / "overwatch-attach-fix-rewrite.sh"
    result = subprocess.run(
        [
            str(script),
            "--project", project_name,
            "--kanban-root", str(kanban_root),
            "--bug-id", _short_bug_id(bug_id),
            "--forbidden-approach", forbidden_approach,
            "--correct-direction", correct_direction,
            "--ground-truth-ref", ground_truth_ref,
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"overwatch-attach-fix-rewrite.sh failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _init_git_repo_with_direct_commit(repo_path: pathlib.Path, main_branch: str, rc_branch: str) -> None:
    """Initialise a local git repo with one commit on *main_branch*, branch
    *rc_branch* off it, then add ONE further direct (non-merge, single-
    parent) commit on *rc_branch* — the deliberate deviation from a clean
    --no-ff-merge-only lineage that makes operator_commits_in_lineage
    extract True (see this module's docstring)."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", f"--initial-branch={main_branch}", str(repo_path)],
        capture_output=True, check=True,
    )
    _run_git(repo_path, "config", "--local", "user.email", "test@pgai.local")
    _run_git(repo_path, "config", "--local", "user.name", "PGAI Test")
    (repo_path / "README.md").write_text("# Test repo\n", encoding="utf-8")
    _run_git(repo_path, "add", "README.md")
    _run_git(repo_path, "commit", "-m", "Initial commit")
    _run_git(repo_path, "checkout", "-b", rc_branch)
    (repo_path / "DIRECT.txt").write_text(f"direct commit on {rc_branch}\n", encoding="utf-8")
    _run_git(repo_path, "add", "DIRECT.txt")
    _run_git(repo_path, "commit", "-m", f"direct commit on {rc_branch}")
    _run_git(repo_path, "checkout", main_branch)


def _build_kanban_root(
    tmp_path: pathlib.Path,
    project_name: str,
    repo_path: pathlib.Path,
    *,
    full_tree: bool = False,
    overwatch_resolve: bool = True,
) -> pathlib.Path:
    """Build the fixture kanban root.

    *full_tree* is False by default (the pre-existing, minimal shape this
    file's original three test classes rely on: scripts/ plus the copied
    pgai_agent_kanban/cm/*.py helpers cancel-rc.sh shells out to — nothing
    else). Passing full_tree=True additionally copies pgai_agent_kanban/,
    workflows/, pm-agent/, and config/ wholesale (the same set
    test_overwatch_wake_driven_fixtures.py's own builder copies) and seeds
    the per-agent queue files, projects.cfg registration, and [wake]
    kanban.cfg section a real wake/PM firing needs — used only by the
    fix-rewrite-plus-redecompose fixture below, which drives a real PM
    wake after the Lane A drill.
    """
    import shutil

    root = tmp_path / "kanban_root"
    proj = root / "projects" / project_name
    root.mkdir(parents=True, exist_ok=True)

    kanban_cfg_body = "[paths]\n\n[chain]\npm_mode = automatic\n\n[release]\nmax_rc_retries = 3\n"
    if full_tree:
        kanban_cfg_body += "\n[wake]\nmax_tasks_per_wake = 1\n"
    (root / "kanban.cfg").write_text(kanban_cfg_body, encoding="utf-8")

    if full_tree:
        (root / "projects.cfg").write_text(
            f"[project:{project_name}]\npriority=1\ndescription=Lane A fixture project\nenabled=true\n",
            encoding="utf-8",
        )

    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        f"dev_tree_path = {repo_path}\n"
        "branch_prefix = ai_\n"
        "push_to_remote = false\n"
        f"\n[release]\nmax_rc_retries = 3\noverwatch_resolve = {'true' if overwatch_resolve else 'false'}\n",
        encoding="utf-8",
    )

    (proj / "requirements").mkdir(parents=True, exist_ok=True)
    (proj / "bugs").mkdir(parents=True, exist_ok=True)
    (proj / "priority").mkdir(parents=True, exist_ok=True)
    (proj / "tasks" / "queues").mkdir(parents=True, exist_ok=True)
    (proj / "release-state").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    if full_tree:
        queues_dir = proj / "tasks" / "queues"
        for agent, title in (
            ("overwatch", "Overwatch Backlog"),
            ("pm", "PM Backlog"),
            ("coder", "Coder Backlog"),
            ("writer", "Writer Backlog"),
            ("tester", "Tester Backlog"),
            ("cm", "CM Backlog"),
        ):
            (queues_dir / f"{agent}_backlog.md").write_text(
                f"# {title}\n\nNo tasks queued by this fixture.\n", encoding="utf-8"
            )
        (queues_dir / "bug_backlog.md").write_text("# Bug Backlog\n\n", encoding="utf-8")
        (queues_dir / "priority_backlog.md").write_text("# Priority Backlog\n\n", encoding="utf-8")

    cm_py_dir = root / "pgai_agent_kanban" / "cm"
    cm_py_dir.mkdir(parents=True, exist_ok=True)
    for py_file in _KANBAN_PY_CM_DIR.glob("*.py"):
        (cm_py_dir / py_file.name).write_text(py_file.read_text(encoding="utf-8"), encoding="utf-8")

    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")

    if full_tree:
        # A real wake/PM firing needs the whole package tree, not just cm/ —
        # same reasoning test_overwatch_lane_b_ship.py's own builder
        # documents for its own full-copy requirement.
        shutil.rmtree(root / "pgai_agent_kanban")
        shutil.copytree(
            _TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copytree(_TEAM_DIR / "workflows", root / "workflows")
        shutil.copytree(_TEAM_DIR / "pm-agent", root / "pm-agent")
        shutil.copytree(_TEAM_DIR / "config", root / "config")

    return root


def _write_release_state_md(proj: pathlib.Path, active_rc: str, last_released: str = "v0.0.0") -> None:
    (proj / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## RC Opened At\n2026-09-05T00:00:00Z\n\n"
        "## RC Opened By Task\nCM-20260905-001-open\n\n"
        f"## Last Released\n{last_released}\n",
        encoding="utf-8",
    )


def _reopen_release_state(proj: pathlib.Path, new_active_rc: str, opened_by_task: str) -> None:
    """Simulate CM re-opening a new RC on an EXISTING release-state.md: a
    surgical field update (Active RC / RC Opened At / RC Opened By Task)
    that preserves every other byte — critically, any "## OVERWATCH
    Decision" events a prior Lane A drill already appended — the same
    preserve-everything-else contract _atomic_append_release_state_event()
    documents. A full-file overwrite here would be a test-fixture bug: it
    would destroy the first attempt's own Decision event, which the
    single-attempt guard's escalation evidence must still be able to see.
    """
    import re

    path = proj / "release-state.md"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"## Active RC\n[^\n]*\n", f"## Active RC\n{new_active_rc}\n", text, count=1)
    text = re.sub(
        r"## RC Opened At\n[^\n]*\n",
        "## RC Opened At\n2026-09-06T00:00:00Z\n",
        text,
        count=1,
    )
    text = re.sub(
        r"## RC Opened By Task\n[^\n]*\n",
        f"## RC Opened By Task\n{opened_by_task}\n",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def _write_ship_policy_halt(path: pathlib.Path, reason_suffix: str = "") -> None:
    path.write_text(
        "# HALT created by CM at 2026-09-05T00:00:00Z\n"
        f"# Reason: TESTER systemic_risk=high{reason_suffix} (TESTER-20260905-001)\n"
        "# Resolution: Operator review required. See CM task status.md for full reason.\n",
        encoding="utf-8",
    )


def _write_operator_halt(path: pathlib.Path) -> None:
    path.write_text("# operator full-stop pending governance review\n", encoding="utf-8")


def _write_task(
    tasks_dir: pathlib.Path,
    task_id: str,
    version: str,
    state: str,
    role: str = "CODER",
) -> None:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\n{role}\n\n"
        f"## Source Branch\nai_rc/{version}\n\n## Release Version\n{version}\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


def _write_lane_a_tester_report(
    tasks_dir: pathlib.Path, tester_task_id: str, version: str, bug_id: str
) -> None:
    """A TESTER report carrying a Regression=yes finding that traces (by
    textual BUG-NNNN reference in the finding body) to *bug_id* — the
    Lane A shape classify_lane()'s find_regression_source_finding() looks
    for."""
    task_dir = tasks_dir / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    _write_task(tasks_dir, tester_task_id, version, "DONE", role="TESTER")
    short_id = bug_id.split("-")[0] + "-" + bug_id.split("-")[1]
    (task_dir / "artifacts" / "report.md").write_text(
        "# TESTER Report\n\n"
        "### Finding 1: dashboard column count regression\n"
        "**Systemic Risk:** high\n"
        "**Regression:** yes\n"
        f"**Filed As:** {bug_id} (Path C)\n\n"
        f"Traces to {short_id}, whose fix in this RC inverted the column check.\n",
        encoding="utf-8",
    )


def _write_bug(bugs_dir: pathlib.Path, bug_id: str, status: str = "assigned") -> pathlib.Path:
    path = bugs_dir / f"{bug_id}.md"
    path.write_text(
        f"# Bug: {bug_id}\n\n## Status\n{status}\n\n## Symptom\ndashboard column inverted\n\n"
        "## Fix\nOriginal (ambiguous) fix direction.\n",
        encoding="utf-8",
    )
    return path


def _write_bundle(requirements_dir: pathlib.Path, version: str, bug_basenames: list, bugs_dir: pathlib.Path) -> pathlib.Path:
    bundle_path = requirements_dir / f"{version}-bugfix-bundle-20260905.md"
    lines = [
        f"# Requirements: {version} — BUG bundle\n\n",
        "## Status\nrunning\n\n",
        f"## Target Version\n{version}\n\n",
        "## Bundled Items\n\n",
    ]
    for name in bug_basenames:
        lines.append(f"- {name} (`{bugs_dir / name}`)\n")
    bundle_path.write_text("".join(lines), encoding="utf-8")
    return bundle_path


def _build_lane_a_fixture(
    tmp_path: pathlib.Path,
    project_name: str,
    version: str,
    bug_id: str,
    bundle_suffix: str = "20260905",
    *,
    full_tree: bool = False,
    overwatch_resolve: bool = True,
    attach_rewrite: bool = True,
):
    """Build a complete Lane A fixture: ship-policy HALT, a TESTER report
    whose finding carries Regression=yes tracing to *bug_id*, a bundle
    whose cargo includes *bug_id*, and an RC branch carrying a direct
    (non-merge) commit so the block fails CANCEL-AND-REBUNDLE and reaches
    classify_lane().

    *attach_rewrite* (default True) additionally performs Lane A step (a)
    — via the real shipped CLI, _attach_fix_rewrite_via_cli() — so
    run_resolution_stage() finds the rewrite-precondition guard satisfied
    and proceeds to steps (b)-(e). Callers exercising the guard itself
    (the precondition NOT yet met) pass attach_rewrite=False.

    Returns (kanban_root, proj, repo_path, halt_path).
    """
    repo_path = tmp_path / f"dev_repo_{version.replace('.', '_')}"
    _init_git_repo_with_direct_commit(repo_path, "ai_main", f"ai_rc/{version}")

    kanban_root = _build_kanban_root(
        tmp_path, project_name, repo_path, full_tree=full_tree, overwatch_resolve=overwatch_resolve
    )
    proj = kanban_root / "projects" / project_name
    _write_release_state_md(proj, version)

    halt_path = proj / "HALT"
    _write_ship_policy_halt(halt_path)

    (proj / "release-state" / f"{version}.json").write_text(
        json.dumps({"rc": version, "attempt": 1, "outcome": "in_progress", "family_key": None}),
        encoding="utf-8",
    )

    _write_bug(proj / "bugs", bug_id)
    (proj / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {bug_id}\n", encoding="utf-8")

    if attach_rewrite:
        _attach_fix_rewrite_via_cli(kanban_root, project_name, bug_id)

    _write_bundle(proj / "requirements", version, [f"{bug_id}.md"], proj / "bugs")

    tester_task_id = f"TESTER-20260905-{version[-1]}01-verify"
    _write_lane_a_tester_report(proj / "tasks", tester_task_id, version, bug_id)

    return kanban_root, proj, repo_path, halt_path


# ---------------------------------------------------------------------------
# Golden path: Lane A classification runs the recovery drill
# ---------------------------------------------------------------------------


class TestLaneAGoldenPath:
    def test_lane_a_drill_unwinds_cancels_deletes_bundle_and_clears_halt(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "lanea_project"
        version = "v2.23.0"
        bug_id = "BUG-0055-inverted-column"

        kanban_root, proj, repo_path, halt_path = _build_lane_a_fixture(
            tmp_path, project_name, version, bug_id
        )

        outcome = run_resolution_stage(project_name, str(kanban_root))

        assert outcome.status == EXECUTED, outcome.reason
        assert outcome.ledger_entry_id
        assert not outcome.action_errors, outcome.action_errors

        # Ledger: exactly one Lane A entry, tagged with the regression bug id.
        ledger_path = proj / "auto-resolutions.md"
        assert ledger_path.is_file()
        entries = parse_entries(str(ledger_path))
        lane_a_entries = [e for e in entries if e.get("matrix_row") == LANE_A_MATRIX_ROW]
        assert len(lane_a_entries) == 1
        assert lane_a_entries[0]["findings"] == [_short_bug_id(bug_id)]

        # release-state.md: exactly one OVERWATCH Decision event, Lane: A.
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert release_state_text.count("## OVERWATCH Decision") == 1
        assert "Lane: A" in release_state_text
        # Active RC reset to none by the real unwind-rc.sh/cancel-rc.sh run.
        assert "## Active RC\nnone" in release_state_text

        # Ship-policy HALT cleared.
        assert not halt_path.exists()

        # RC branch deleted locally (unwind-rc.sh's own git-unwind step).
        branches = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "--list", f"ai_rc/{version}"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert branches.strip() == ""

        # Bundle doc gone: neither the original basename nor a superseded
        # rename survives — Lane A's own delete step removes whichever is
        # on disk when it runs.
        requirements_dir = proj / "requirements"
        remaining = list(requirements_dir.glob(f"{version}-*.md"))
        assert remaining == [], f"bundle doc(s) still present: {remaining}"

        # Bug reset to open by unwind-rc.sh's own bug-backlog reset step —
        # the precondition discovery needs to re-bundle the rewritten bug.
        bug_text = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nopen" in bug_text
        backlog_text = (proj / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
        assert f"[ ] {bug_id}" in backlog_text

    def test_lane_a_resolution_path_never_writes_release_state_or_queue_files_directly(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Structural proxy for the grep-zero acceptance criterion: every
        mutation observed after a Lane A drill is attributable to an
        invoked operator verb (unwind-rc.sh, cancel-rc.sh) or the ops
        write layer (delete_item), never a direct open(..., 'w') in
        resolution_stage.py's Lane A functions themselves. Grepping the
        Lane A functions' source for a raw write-mode file open onto
        release-state.md, a queue file, or a status.md path other than
        the append-only ledger/Decision-event/action-log writers already
        covers this at the source level; this test additionally proves
        the observable behavior matches (Active RC actually changed, the
        bug backlog actually flipped) — i.e. the operator verbs really
        ran rather than being silently skipped.
        """
        project_name = "lanea_grep_project"
        version = "v2.23.1"
        bug_id = "BUG-0056-inverted-column"

        kanban_root, proj, repo_path, halt_path = _build_lane_a_fixture(
            tmp_path, project_name, version, bug_id
        )

        outcome = run_resolution_stage(project_name, str(kanban_root))
        assert outcome.status == EXECUTED, outcome.reason

        import re

        source = (
            _TEAM_DIR / "pgai_agent_kanban" / "overwatch" / "resolution_stage.py"
        ).read_text(encoding="utf-8")
        start = source.index("def _run_unwind_rc")
        end = source.index("def _run_lane_classification")
        lane_a_source = source[start:end]
        # No raw write-mode open() of release-state.md, a queue file, or a
        # task status.md inside the Lane A functions — the only writes are
        # via subprocess (operator verbs), delete_item (ops layer), and the
        # shared append-only writers (write_decision_event/append_entry/
        # log_action) already precedented by the pre-existing Lane B/C path.
        assert not re.search(r'open\([^)]*["\']w', lane_a_source)


# ---------------------------------------------------------------------------
# Single-attempt guard: a second identical HALT escalates instead of
# re-running the drill
# ---------------------------------------------------------------------------


class TestLaneASingleAttemptGuard:
    def test_second_halt_on_same_finding_class_escalates_to_lane_c(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "lanea_guard_project"
        bug_id = "BUG-0077-inverted-column"

        version1 = "v2.23.0"
        kanban_root, proj, repo_path1, halt_path1 = _build_lane_a_fixture(
            tmp_path, project_name, version1, bug_id
        )

        first = run_resolution_stage(project_name, str(kanban_root))
        assert first.status == EXECUTED, first.reason

        ledger_path = proj / "auto-resolutions.md"
        lane_a_entries_after_first = [
            e for e in parse_entries(str(ledger_path)) if e.get("matrix_row") == LANE_A_MATRIX_ROW
        ]
        assert len(lane_a_entries_after_first) == 1

        # Simulate the reborn RC: a new version, a new dev-tree branch (same
        # direct-commit trick so it again fails CANCEL-AND-REBUNDLE), a new
        # ship-policy HALT, a new TESTER report tracing to the SAME bug_id,
        # and a new bundle carrying the same bug — the shape "discovery
        # re-decomposed, CM re-opened, and the identical regression HALTed
        # again."
        version2 = "v2.23.1"
        repo_path2 = tmp_path / "dev_repo_reborn"
        _init_git_repo_with_direct_commit(repo_path2, "ai_main", f"ai_rc/{version2}")

        # Point the project at the reborn dev tree and reopen the RC.
        (proj / "project.cfg").write_text(
            "[project]\n"
            f"project_name = {project_name}\n"
            f"dev_tree_path = {repo_path2}\n"
            "branch_prefix = ai_\n"
            "push_to_remote = false\n"
            "\n[release]\nmax_rc_retries = 3\noverwatch_resolve = true\n",
            encoding="utf-8",
        )
        # Surgical field update — NOT a full rewrite — so the first
        # attempt's own "## OVERWATCH Decision" event survives, exactly as
        # a real cm-open-rc.sh reopening on top of an existing
        # release-state.md would leave it.
        _reopen_release_state(proj, version2, "CM-20260906-001-open")

        halt_path2 = proj / "HALT"
        _write_ship_policy_halt(halt_path2, reason_suffix=" (reborn RC)")

        (proj / "release-state" / f"{version2}.json").write_text(
            json.dumps({"rc": version2, "attempt": 1, "outcome": "in_progress", "family_key": None}),
            encoding="utf-8",
        )

        # The bug is open again (unwind-rc reset it); re-bundle it.
        (proj / "bugs" / f"{bug_id}.md").write_text(
            f"# Bug: {bug_id}\n\n## Status\nassigned\n\n## Symptom\ndashboard column inverted\n\n"
            "## Fix\nOriginal (ambiguous) fix direction.\n",
            encoding="utf-8",
        )
        (proj / "tasks" / "queues" / "bug_backlog.md").write_text(f"- [x] {bug_id}\n", encoding="utf-8")
        _write_bundle(proj / "requirements", version2, [f"{bug_id}.md"], proj / "bugs")

        tester_task_id2 = "TESTER-20260906-001-verify"
        _write_lane_a_tester_report(proj / "tasks", tester_task_id2, version2, bug_id)

        second = run_resolution_stage(project_name, str(kanban_root))

        assert second.status == DECLINED, second.reason
        assert "single-attempt guard" in second.reason.lower() or "escalated" in second.reason.lower()

        # No second drill: the reborn HALT, the reborn bundle, and Active RC
        # are all left exactly as this fixture set them up.
        assert halt_path2.is_file()
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert f"## Active RC\n{version2}" in release_state_text
        bundle_path2 = proj / "requirements" / f"{version2}-bugfix-bundle-20260905.md"
        assert bundle_path2.is_file()
        bundle_text = bundle_path2.read_text(encoding="utf-8")
        assert "## Status\nrunning" in bundle_text

        # No new Lane A ledger entry — the guard never re-ran the drill.
        lane_a_entries_after_second = [
            e for e in parse_entries(str(ledger_path)) if e.get("matrix_row") == LANE_A_MATRIX_ROW
        ]
        assert len(lane_a_entries_after_second) == 1

        # A second OVERWATCH Decision event was written (Lane: C), whose
        # evidence carries references from BOTH attempts.
        assert release_state_text.count("## OVERWATCH Decision") == 2
        decision_blocks = release_state_text.split("## OVERWATCH Decision")[1:]
        second_block = decision_blocks[-1]
        assert "Lane: C" in second_block
        assert str(halt_path1) in second_block or str(proj / "HALT") in second_block
        # The first attempt's own evidence (its TESTER report) is carried
        # into the escalation text, not just the second attempt's.
        first_tester_report = proj / "tasks" / f"TESTER-20260905-{version1[-1]}01-verify" / "artifacts" / "report.md"
        assert str(first_tester_report) in second_block


# ---------------------------------------------------------------------------
# Operator-HALT inviolability applies before Lane A is ever consulted
# ---------------------------------------------------------------------------


class TestLaneAOperatorHaltInviolability:
    def test_operator_halt_alongside_ship_policy_halt_declines_before_lane_classification(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "lanea_operator_halt_project"
        version = "v2.23.0"
        bug_id = "BUG-0088-inverted-column"

        kanban_root, proj, repo_path, halt_path = _build_lane_a_fixture(
            tmp_path, project_name, version, bug_id
        )

        global_halt = kanban_root / "HALT"
        _write_operator_halt(global_halt)

        outcome = run_resolution_stage(project_name, str(kanban_root))

        assert outcome.status == DECLINED
        assert "operator-authored HALT" in outcome.reason

        # Both HALT files untouched, nothing drilled, no ledger, no Decision
        # event — the operator-HALT check runs before the matrix/lane
        # consultation is ever reached.
        assert halt_path.is_file()
        assert global_halt.is_file()
        assert not (proj / "auto-resolutions.md").exists()
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert "## OVERWATCH Decision" not in release_state_text
        assert f"## Active RC\n{version}" in release_state_text


# ---------------------------------------------------------------------------
# Rewrite precondition: a Lane A firing with
# NO attributed Fix rewrite on the blamed bug declines steps (b)-(e) rather
# than run the destructive drill against an unrewritten bug — closing the
# gap where the drill previously assumed step (a) had already run, when
# nothing in the shipped wake path ever performed it. This test supplies no
# rewrite at all (attach_rewrite=False), so it proves the guard itself, the
# opposite case from TestLaneAGoldenPath / TestLaneAFullPlaybook... above,
# which prove the drill proceeds once the rewrite IS present.
# ---------------------------------------------------------------------------


class TestLaneARewritePrecondition:
    def test_steps_b_through_e_do_not_run_without_an_attributed_rewrite(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "lanea_no_rewrite_project"
        version = "v2.23.5"
        bug_id = "BUG-0111-inverted-column"

        kanban_root, proj, repo_path, halt_path = _build_lane_a_fixture(
            tmp_path, project_name, version, bug_id, attach_rewrite=False
        )

        bug_path = proj / "bugs" / f"{bug_id}.md"
        bug_text_before = bug_path.read_text(encoding="utf-8")
        assert "## Fix (OVERWATCH rewrite)" not in bug_text_before, (
            "fixture precondition: no rewrite should be present yet"
        )
        release_state_before = (proj / "release-state.md").read_text(encoding="utf-8")

        outcome = run_resolution_stage(project_name, str(kanban_root))

        assert outcome.status == DECLINED, outcome.reason
        assert "rewrite" in outcome.reason.lower()

        # RC not unwound: the local RC branch still exists.
        branches = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "--list", f"ai_rc/{version}"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert f"ai_rc/{version}" in branches, f"RC branch must still exist — nothing was unwound.\n{branches}"

        # RC not cancelled: Active RC untouched.
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert f"## Active RC\n{version}" in release_state_text

        # Bundle document still present.
        bundle_path = proj / "requirements" / f"{version}-bugfix-bundle-20260905.md"
        assert bundle_path.is_file(), "bundle document must still be present — nothing was deleted"

        # Ship-policy HALT still set.
        assert halt_path.is_file(), "ship-policy HALT must still be present — nothing was cleared"

        # No ledger entry: a decline on the precondition never counts as an
        # "attempt" the single-attempt guard should later see.
        ledger_path = proj / "auto-resolutions.md"
        assert not ledger_path.exists() or not [
            e for e in parse_entries(str(ledger_path)) if e.get("matrix_row") == LANE_A_MATRIX_ROW
        ]

        # The bug file itself is untouched.
        assert bug_path.read_text(encoding="utf-8") == bug_text_before

        # A "## OVERWATCH Decision" event IS still written (Lane: A), same
        # as every other decline in this stage — the decision is auditable
        # even though nothing was drilled.
        assert release_state_text != release_state_before
        assert "## OVERWATCH Decision" in release_state_text
        assert "Lane: A" in release_state_text


# ---------------------------------------------------------------------------
# Full playbook: the reasoning-tier Fix-rewrite step (a), driven through the
# REAL shipped CLI (team/scripts/overwatch-attach-fix-rewrite.sh) as a
# subprocess — never a test-body call to rewrite_bug_fix_section() itself —
# plus the deterministic drill (b)-(e) via run_resolution_stage() alone,
# then a real PM wake proving step (f) — discovery re-decomposing the
# rewritten bug on its own clock. This is the acceptance criterion "the Lane
# A fixture asserts all six of the brief's Lane A outcomes"
# (requirements/v2.23.0-overwatch-block-resolution.md, Acceptance) exercised
# end to end against the real shipped call path: the CLI wraps
# pgai_agent_kanban.overwatch.fix_rewrite.rewrite_bug_fix_section() for step
# (a) — per team/roles/OVERWATCH.md ("Performing that rewrite is the
# reasoning tier's duty, not the drill's"), the caller supplying
# forbidden_approach/correct_direction/ground_truth_ref stands in for the
# OVERWATCH agent's own judgment call, exactly as it would in a live firing
# — then run_resolution_stage() for steps (b)-(e), gated on that CLI having
# run (pgai_agent_kanban.overwatch.resolution_stage._run_lane_a's
# rewrite-precondition guard), then a real
# `bash scripts/wake/claude.sh --agent=pm --max-tasks=1` for step (f).
# ---------------------------------------------------------------------------


class TestLaneAFullPlaybookWithFixRewriteAndRedecompose:
    def test_all_six_lane_a_outcomes_including_fix_rewrite_and_redecompose(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "lanea_full_playbook_project"
        version = "v2.23.2"
        bug_id = "BUG-0099-inverted-column"

        kanban_root, proj, repo_path, halt_path = _build_lane_a_fixture(
            tmp_path, project_name, version, bug_id, full_tree=True, attach_rewrite=False
        )

        # Step (a) — the reasoning tier's Fix-rewrite, performed via the real
        # shipped CLI as a subprocess (not fix_rewrite.py's own unit test,
        # and not a direct call to rewrite_bug_fix_section() from this test
        # file — this is the integration wiring for the shipped call path;
        # the rewrite lands on the SAME bug file the deterministic drill
        # below is about to reset to open).
        bug_path = proj / "bugs" / f"{bug_id}.md"
        assert bug_path.is_file(), f"expected a bug file at {bug_path}"

        forbidden_approach = (
            "Inverting the column-count comparison (using '>' where the layout "
            "spec requires '<') to silence the dashboard width check."
        )
        correct_direction = (
            "Compare column_count using '<' against the configured max_width, "
            "per the layout spec."
        )
        ground_truth_ref = (
            "commit 9f2a3c1: \"fix: correct dashboard column width check\" "
            "(the exact diff this RC's own bundle shipped)"
        )
        _attach_fix_rewrite_via_cli(
            kanban_root, project_name, bug_id,
            forbidden_approach=forbidden_approach,
            correct_direction=correct_direction,
            ground_truth_ref=ground_truth_ref,
        )

        bug_text_after_rewrite = bug_path.read_text(encoding="utf-8")
        assert "FORBIDDEN" in bug_text_after_rewrite
        assert forbidden_approach in bug_text_after_rewrite
        assert ground_truth_ref in bug_text_after_rewrite
        assert "## Fix\nOriginal (ambiguous) fix direction." in bug_text_after_rewrite, (
            "the original Fix section must be preserved unchanged, not edited in place"
        )

        # Steps (b)-(e) — the deterministic drill, driven through
        # run_resolution_stage() ALONE: no test-supplied rewrite call here,
        # only the CLI subprocess above and this one production entry point.
        outcome = run_resolution_stage(project_name, str(kanban_root))
        assert outcome.status == EXECUTED, outcome.reason

        # Outcome 1: the bug is rewritten — forbidden approach named, ground
        # truth reference present — and that rewrite survives the drill's
        # own bug-reset-to-open step (only the Status field changes; the
        # appended Fix rewrite block must not be clobbered).
        bug_text_after_drill = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert "## Status\nopen" in bug_text_after_drill
        assert forbidden_approach in bug_text_after_drill
        assert ground_truth_ref in bug_text_after_drill
        assert "FORBIDDEN" in bug_text_after_drill

        # Outcome 2: RC unwound (unwind-rc.sh ran).
        assert any("unwind-rc.sh" in action for action in outcome.actions_taken), outcome.actions_taken

        # Outcome 3: RC cancelled (cancel-rc.sh ran) — Active RC reset to none.
        assert any("cancel-rc.sh" in action for action in outcome.actions_taken), outcome.actions_taken
        release_state_text = (proj / "release-state.md").read_text(encoding="utf-8")
        assert "## Active RC\nnone" in release_state_text

        # Local RC branch actually gone (unwind-rc.sh's own git-unwind step).
        branches = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "--list", f"ai_rc/{version}"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert branches.strip() == ""

        # Outcome 4: bundle deleted.
        remaining_bundles = list((proj / "requirements").glob(f"{version}-*.md"))
        assert remaining_bundles == [], f"bundle doc(s) still present: {remaining_bundles}"

        # Outcome 5: HALT removed.
        assert not halt_path.exists()

        backlog_text = (proj / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
        assert f"[ ] {bug_id}" in backlog_text

        # Outcome 6: a real PM wake re-decomposes the rewritten bug into a
        # fresh bundle — step (f), "let discovery re-decompose", driven
        # through the actual production entry point rather than asserted
        # as a side effect of internal state.
        requirements_dir = proj / "requirements"
        pre_files = set(requirements_dir.glob("*.md"))

        pm_result = run_entrypoint(
            ["bash", str(kanban_root / "scripts" / "wake" / "claude.sh"), "--agent=pm", "--max-tasks=1"],
            kanban_root=kanban_root,
            timeout=90,
        )
        assert pm_result.returncode == 0, (
            f"wake/claude.sh --agent=pm exited {pm_result.returncode}\n"
            f"stdout:\n{pm_result.stdout}\nstderr:\n{pm_result.stderr}"
        )

        post_files = set(requirements_dir.glob("*.md"))
        new_files = post_files - pre_files
        assert new_files, (
            "the PM wake produced no new requirements bundle from the reopened, "
            f"rewritten bug. pm wake stdout:\n{pm_result.stdout}\nstderr:\n{pm_result.stderr}"
        )
        new_bundle_text = "\n".join(p.read_text(encoding="utf-8") for p in new_files)
        assert bug_id in new_bundle_text, (
            f"rewritten bug {bug_id} not present in the newly bundled requirements file(s): {new_files}"
        )


# ---------------------------------------------------------------------------
# overwatch_resolve=false: a Lane-A-shaped block behaves exactly as it did
# before the three-lane classifier existed — byte-identical release-state,
# no Decision event, nothing drilled.
# ---------------------------------------------------------------------------


class TestLaneAOverwatchResolveFalse:
    def test_lane_a_shaped_block_resolves_nothing_when_overwatch_resolve_false(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "lanea_resolve_off_project"
        version = "v2.23.3"
        bug_id = "BUG-0100-inverted-column"

        kanban_root, proj, repo_path, halt_path = _build_lane_a_fixture(
            tmp_path, project_name, version, bug_id, overwatch_resolve=False
        )

        release_state_before = (proj / "release-state.md").read_text(encoding="utf-8")
        bug_text_before = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")

        outcome = run_resolution_stage(project_name, str(kanban_root))

        assert outcome.status == DECLINED, outcome.reason
        # The pre-existing (pre-v2.23.0) decline reason names the unmatched
        # facts directly — it must NOT mention a lane, proving the flag
        # gates entry into classify_lane() before any lane-specific text
        # could ever be produced.
        assert "lane" not in outcome.reason.lower(), outcome.reason

        release_state_after = (proj / "release-state.md").read_text(encoding="utf-8")
        assert release_state_after == release_state_before, (
            "release-state.md must be byte-identical when overwatch_resolve is "
            "false — no lane classification, no Decision event, nothing drilled."
        )
        assert "## OVERWATCH Decision" not in release_state_after

        # Nothing drilled: HALT, bug content, and the RC branch are all
        # exactly as this fixture set them up.
        assert halt_path.is_file()
        bug_text_after = (proj / "bugs" / f"{bug_id}.md").read_text(encoding="utf-8")
        assert bug_text_after == bug_text_before
        assert not (proj / "auto-resolutions.md").exists()
        branches = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "--list", f"ai_rc/{version}"],
            capture_output=True, text=True, check=True,
        ).stdout
        assert f"ai_rc/{version}" in branches, (
            f"the RC branch must still exist — nothing was unwound.\n{branches}"
        )


# ---------------------------------------------------------------------------
# overwatch_resolve=false applies identically regardless of which lane the
# block would have classified into — the gate sits before classify_lane()
# is ever called (see run_resolution_stage's "if overwatch_resolve:" branch
# in resolution_stage.py), so a Lane-C-shaped block (no regression finding,
# no INTENT.md match — the default lane) proves the SAME mechanism that
# TestLaneAOverwatchResolveFalse proves for Lane A and
# test_overwatch_lane_b_ship.py's own flag-off fixture proves for Lane B —
# closing out "overwatch_resolve=false -> all lanes behave as today" across
# all three shapes.
# ---------------------------------------------------------------------------


def _write_plain_tester_report(tasks_dir: pathlib.Path, tester_task_id: str, version: str) -> None:
    """A TESTER report with a finding that carries no Regression field at
    all and no traceable bug reference — the Lane-C shape: neither
    find_regression_source_finding() (no Regression=yes) nor a Lane B
    INTENT.md match (none written) can ever fire, so a Lane-C-shaped block
    would classify LANE_C if classify_lane() were ever reached."""
    task_dir = tasks_dir / tester_task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    _write_task(tasks_dir, tester_task_id, version, "DONE", role="TESTER")
    (task_dir / "artifacts" / "report.md").write_text(
        "# TESTER Report\n\n"
        "### Finding 1: unrelated flaky assertion\n"
        "**Systemic Risk:** high\n"
        "**Filed As:** none\n\n"
        "No regression, no traceable originating bug.\n",
        encoding="utf-8",
    )


class TestFlagOffAppliesRegardlessOfLane:
    def test_lane_c_shaped_block_resolves_nothing_when_overwatch_resolve_false(
        self, tmp_path: pathlib.Path
    ) -> None:
        project_name = "lanec_resolve_off_project"
        version = "v2.23.4"

        repo_path = tmp_path / "dev_repo_lanec"
        _init_git_repo_with_direct_commit(repo_path, "ai_main", f"ai_rc/{version}")

        kanban_root = _build_kanban_root(tmp_path, project_name, repo_path, overwatch_resolve=False)
        proj = kanban_root / "projects" / project_name
        _write_release_state_md(proj, version)

        halt_path = proj / "HALT"
        _write_ship_policy_halt(halt_path)
        (proj / "release-state" / f"{version}.json").write_text(
            json.dumps({"rc": version, "attempt": 1, "outcome": "in_progress", "family_key": None}),
            encoding="utf-8",
        )
        tester_task_id = "TESTER-20260905-401-verify"
        _write_plain_tester_report(proj / "tasks", tester_task_id, version)
        # No INTENT.md at all -- a Lane B match is structurally impossible.

        release_state_before = (proj / "release-state.md").read_text(encoding="utf-8")

        outcome = run_resolution_stage(project_name, str(kanban_root))

        assert outcome.status == DECLINED, outcome.reason
        assert "lane" not in outcome.reason.lower(), outcome.reason

        release_state_after = (proj / "release-state.md").read_text(encoding="utf-8")
        assert release_state_after == release_state_before, (
            "release-state.md must be byte-identical when overwatch_resolve is "
            "false, regardless of which lane the block would have classified "
            "into — no Decision event, nothing drilled."
        )
        assert "## OVERWATCH Decision" not in release_state_after
        assert halt_path.is_file()
        assert not (proj / "auto-resolutions.md").exists()
