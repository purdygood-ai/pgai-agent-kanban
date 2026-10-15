"""
test_diagnostics_fixture_matrix.py
====================================
Fixture matrix for the v2.15.0 diagnostics suite's four named failure
shapes: stale-marker, invalid-ledger, churn-progress, prefixed-project.

The suite's acceptance is not "the diagnostics work" -- unit suites
already prove that piece by piece (team/tests/unit/test_why_verdict_engine.py,
test_why_details_pane.py, test_refusal_ledger.py) and the pre-existing dual-
project fixture already proves verify-rc-state.sh's branch-prefix handling
(test_verify_rc_state_two_project_prefix_fixture.py). The acceptance here is
that each of the four shapes is a SEAM between pieces built separately, and
one command produces an actionable answer for it against the ASSEMBLED
suite. Each test below deliberately targets ground the unit suites do not
cover, rather than restating their assertions:

  - stale-marker: the unit coverage (test_why_details_pane.py) proves a
    single disagreeing entry renders both fields. This test proves the
    rendering is SELECTIVE inside a realistic multi-entry queue -- an
    agreeing entry stays unflagged while the disagreeing one is named --
    so the "surfaces the disagreement itself, not silently prefer one
    source" claim holds when there is something to distinguish it from.

  - invalid-ledger: no existing test exercises a malformed REFUSAL LEDGER
    (as opposed to a malformed campaign file, which
    test_why_details_pane.py already covers) through why.sh --details.
    This is a genuine seam between ticket 2's ledger library and ticket 3's
    --details pane that nothing else in the tree proves end to end.

  - churn-progress: the unit suite proves stall detection under
    `--project NAME` and proves worst-verdict aggregation across an
    IDLE+BLOCKED pair. Neither combination proves that a STALLED project
    survives the no-argument aggregation walk without being masked by a
    second, healthy RUNNING project in the same walk -- the one scenario
    an operator actually depends on why.sh (no args) to catch.

  - prefixed-project: ticket 4's own dual-project fixture (imported and
    reused here, never re-implemented) only ever drove verify-rc-state.sh's
    branch-prefix-specific checks (a: Active RC field validation, e: branch-
    name construction). Check (c) -- status.md state vs. queue marker
    consistency -- has nothing to do with branches at all; this test proves
    it still finds and reports the identical mismatch on a prefixed project
    as on a bare-prefix one, closing the one check dimension ticket 4's own
    fixture never exercised.

Design notes:
  - All four tests use tmp_path for scratch and never touch the live
    install; PGAI_AGENT_KANBAN_ROOT_PATH is repointed at a fixture root in
    every subprocess invocation.
  - No test relies on wall-clock sleeps: the churn-progress test pins "now"
    via VERDICT_NOW_EPOCH (team/pgai_agent_kanban/ops/verdict.py's testability seam).
  - Each test is independently runnable with no ordering dependency and no
    shared mutable state between tests.
  - Every test invokes exactly one command, named in its own docstring.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

from tests.fixtures.binary_guard import require_binary
from tests.integration.test_verify_rc_state_two_project_prefix_fixture import (
    _build_dual_project_kanban_root,
    _create_branch,
    _init_local_repo,
    _run_verify_rc_state,
    _write_release_state,
)

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/integration/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"


# ---------------------------------------------------------------------------
# Shared why.sh fixture helpers (mirrors the pattern in
# team/tests/unit/test_why_verdict_engine.py and test_why_details_pane.py --
# imitated rather than imported, since those live in unit/ and this suite's
# convention keeps integration fixtures local to team/tests/integration/).
# ---------------------------------------------------------------------------


def _why_env(kanban_root: pathlib.Path, now_epoch: int | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    if now_epoch is not None:
        env["VERDICT_NOW_EPOCH"] = str(now_epoch)
    else:
        env.pop("VERDICT_NOW_EPOCH", None)
    return env


def _run_why(
    args: list[str],
    kanban_root: pathlib.Path,
    now_epoch: int | None = None,
    timeout: int = 20,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_WHY_SH), *args],
        capture_output=True,
        text=True,
        env=_why_env(kanban_root, now_epoch),
        timeout=timeout,
    )


def _write_projects_cfg(kanban_root: pathlib.Path, projects: list[tuple[str, int, bool]]) -> None:
    lines = ["# Format: INI-style"]
    for name, priority, enabled in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append(f"enabled={'true' if enabled else 'false'}")
        lines.append("")
    (kanban_root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_project_dir(kanban_root: pathlib.Path, name: str) -> pathlib.Path:
    project_dir = kanban_root / "projects" / name
    (project_dir / "tasks").mkdir(parents=True, exist_ok=True)
    return project_dir


def _write_task(
    project_dir: pathlib.Path,
    task_id: str,
    state: str,
    mtime_epoch: int | None = None,
) -> pathlib.Path:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(f"## State\n{state}\n\n## Summary\nfixture task\n", encoding="utf-8")
    if mtime_epoch is not None:
        os.utime(status_file, (mtime_epoch, mtime_epoch))
    return status_file


def _write_queue(project_dir: pathlib.Path, agent: str, entries: list[str]) -> pathlib.Path:
    """entries: list of raw '- [marker] TASK-ID' lines, in file order."""
    queue_dir = project_dir / "tasks" / "queues"
    queue_dir.mkdir(parents=True, exist_ok=True)
    queue_file = queue_dir / f"{agent}_backlog.md"
    queue_file.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return queue_file


def _write_refusal_ledger_missing_remedy(
    project_dir: pathlib.Path, *, timestamp: str, surface: str, error: str, message: str
) -> pathlib.Path:
    """A ledger record that exists and has a recognizable header, but is
    truncated (no Remedy line) -- the exact malformation
    pgai_agent_kanban.lib.refusal_ledger.read_latest documents as 'missing
    required field(s)' rather than 'never recorded' (see its module
    docstring: 'a record missing the remedy is worse than no record at
    all')."""
    ledger = project_dir / "refusal-ledger.md"
    ledger.write_text(
        "# Refusal Ledger — demo\n\n"
        f"## Refusal: {timestamp} — demo — {surface}\n"
        f"- Error: {error}\n"
        f"- Message: {message}\n",
        encoding="utf-8",
    )
    return ledger


def _section(stdout: str, header: str) -> str:
    """Extract the text between '== <header> ==' and the next '== ... ==' (or EOF)."""
    pattern = re.compile(rf"== {re.escape(header)} ==\n(.*?)(?=\n== |\Z)", re.S)
    m = pattern.search(stdout)
    assert m, f"section '{header}' not found in output:\n{stdout}"
    return m.group(1)


# ---------------------------------------------------------------------------
# Shape 1: stale-marker
# ---------------------------------------------------------------------------


def test_why_details_names_disagreeing_marker_and_state_while_leaving_agreeing_entries_alone(
    tmp_path: pathlib.Path,
) -> None:
    """One command: `why.sh --project demo --details`.

    Seeds a realistic multi-entry coder queue: two entries whose marker
    agrees with their task's status.md ## State, and one whose marker
    ('[x]', conventionally DONE) disagrees with its task's real state
    (WORKING). The command's queue-tail output must name BOTH the raw
    marker and the real state for the disagreeing entry -- and must not
    blur that signal by also mis-describing the agreeing entries, proving
    the disagreement is surfaced as a property of that one entry, not a
    blanket normalization failure.
    """
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")

    _write_task(project_dir, "CODER-20260910-101-agrees-done", "DONE")
    _write_task(project_dir, "CODER-20260910-102-stale-marker", "WORKING")
    _write_task(project_dir, "CODER-20260910-103-agrees-backlog", "BACKLOG")

    _write_queue(
        project_dir,
        "coder",
        [
            "- [x] CODER-20260910-101-agrees-done — shipped",
            "- [x] CODER-20260910-102-stale-marker — marker says done, status.md disagrees",
            "- [ ] CODER-20260910-103-agrees-backlog — queued",
        ],
    )

    result = _run_why(["--project", "demo", "--details"], tmp_path)

    assert result.returncode in (0, 2, 3, 4, 5), f"why.sh refused: {result.stderr!r}"
    tails = _section(result.stdout, "queue tails (last 5, raw markers)")

    disagreeing_line = next(
        line for line in tails.splitlines() if "CODER-20260910-102-stale-marker" in line
    )
    assert "[x]" in disagreeing_line, disagreeing_line
    assert "status.md: WORKING" in disagreeing_line, (
        "the disagreeing entry must name both the raw marker and the real "
        f"status.md state in the same line: {disagreeing_line!r}"
    )

    agrees_done_line = next(
        line for line in tails.splitlines() if "CODER-20260910-101-agrees-done" in line
    )
    assert "status.md: DONE" in agrees_done_line, agrees_done_line

    agrees_backlog_line = next(
        line for line in tails.splitlines() if "CODER-20260910-103-agrees-backlog" in line
    )
    assert "status.md: BACKLOG" in agrees_backlog_line, agrees_backlog_line


# ---------------------------------------------------------------------------
# Shape 2: invalid-ledger
# ---------------------------------------------------------------------------


def test_why_details_reports_malformed_refusal_ledger_as_unusable_never_as_no_recent_refusal(
    tmp_path: pathlib.Path,
) -> None:
    """One command: `why.sh --project demo --details`.

    Seeds a refusal-ledger.md that exists and has a recognizable header but
    is truncated (missing the Remedy field) -- ticket 2's read_latest()
    raises RefusalLedgerError for exactly this shape rather than returning
    "never recorded". The command must propagate that as a named, unusable
    record: the refusal must name the ledger file and say it is malformed,
    and the output (stdout+stderr combined, since why.sh's 'never printed
    over' contract means nothing reaches stdout once a --details section
    refuses) must NOT contain the healthy-looking 'no discovery/gate
    refusal recorded' empty-state string anywhere -- a malformed record and
    an absent one are different answers, and the second must never stand in
    for the first.
    """
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    ledger_path = _write_refusal_ledger_missing_remedy(
        project_dir,
        timestamp="2026-09-09T12:00:00+00:00",
        surface="discovery.step_bugs",
        error="discovery.bugs_dir_missing",
        message="bugs/ directory not found for project demo",
    )

    result = _run_why(["--project", "demo", "--details"], tmp_path)

    assert result.returncode != 0, (
        "a malformed refusal ledger must refuse, not silently render a "
        f"pane: {result.stdout!r}"
    )
    assert result.stdout == "", (
        "why.sh's --details 'never printed over' contract means nothing "
        f"reaches stdout once a section refuses; got: {result.stdout!r}"
    )

    combined = result.stdout + result.stderr
    assert str(ledger_path) in combined, (
        f"the refusal must name the malformed ledger file by path: {combined!r}"
    )
    assert "malformed" in combined.lower(), combined
    assert "remedy" in combined.lower(), (
        "the refusal must say which required field is missing, not just "
        f"that the file is bad: {combined!r}"
    )
    assert "no discovery/gate refusal recorded" not in combined, (
        "a malformed record must never read as the healthy 'never "
        f"recorded' empty state: {combined!r}"
    )


# ---------------------------------------------------------------------------
# Shape 3: churn-progress
# ---------------------------------------------------------------------------


def test_why_no_argument_walk_does_not_mask_a_stalled_project_behind_a_healthy_one(
    tmp_path: pathlib.Path,
) -> None:
    """One command: `why.sh` (no arguments -- walks every registered project).

    Registers two projects in one fixture kanban root: lane_alpha, whose
    active task has been WORKING for 61 minutes (past
    STALL_THRESHOLD_MINUTES=60), and lane_beta, whose active task is
    healthy WORKING for 5 minutes. The unit suite already proves stall
    detection under `--project NAME` alone, and separately proves worst-
    verdict aggregation across an IDLE+BLOCKED pair -- neither proves that a
    stalled project's non-healthy exit code survives the no-argument
    aggregation walk when the OTHER project in the same walk is a plain
    healthy RUNNING (exit 0), which is exactly the shape that could mask a
    churning lane behind a fleet-wide "nothing to see" exit code.
    """
    now_epoch = 1_800_000_000
    _write_projects_cfg(tmp_path, [("lane_alpha", 1, True), ("lane_beta", 2, True)])

    alpha_dir = _make_project_dir(tmp_path, "lane_alpha")
    _write_task(
        alpha_dir,
        "CODER-20260910-201-churning",
        "WORKING",
        mtime_epoch=now_epoch - (61 * 60),
    )

    beta_dir = _make_project_dir(tmp_path, "lane_beta")
    _write_task(
        beta_dir,
        "CODER-20260910-202-progressing",
        "WORKING",
        mtime_epoch=now_epoch - (5 * 60),
    )

    result = _run_why([], tmp_path, now_epoch=now_epoch)

    assert result.returncode == 3, (
        "the worst verdict across the walk (RUNNING-stalled, exit 3) must "
        f"win over a healthy RUNNING project (exit 0): {result.stdout!r}"
    )

    lines = result.stdout.strip("\n").splitlines()
    assert len(lines) == 2
    alpha_line = next(line for line in lines if line.startswith("lane_alpha:"))
    beta_line = next(line for line in lines if line.startswith("lane_beta:"))

    assert alpha_line.startswith("lane_alpha: RUNNING"), alpha_line
    assert "CODER-20260910-201-churning" in alpha_line, alpha_line
    assert "WORKING" in alpha_line, alpha_line
    assert "61m" in alpha_line, alpha_line
    # A healthy RUNNING line legitimately names the stall *threshold* as
    # context ("... stays within the 60m stall threshold"); what distinguishes
    # an actual stall is the "churn or a hang" cause fragment, only emitted
    # once the threshold is exceeded (team/pgai_agent_kanban/ops/verdict.py).
    assert "churn or a hang" in alpha_line, (
        f"a stalled project's line must not read as a bare healthy RUNNING: {alpha_line!r}"
    )

    assert beta_line.startswith("lane_beta: RUNNING"), beta_line
    assert "churn or a hang" not in beta_line, (
        f"a healthy project sharing the walk must not be flagged as stalled: {beta_line!r}"
    )


# ---------------------------------------------------------------------------
# Shape 4: prefixed-project
# ---------------------------------------------------------------------------


def test_verify_rc_state_marker_check_diagnoses_identically_on_prefixed_and_bare_projects(
    tmp_path: pathlib.Path,
) -> None:
    """One command (run once per project, same command shape both times):
    `verify-rc-state.sh <project> --verbose`.

    Reuses ticket 4's own dual-project fixture builder
    (_build_dual_project_kanban_root, imported from
    test_verify_rc_state_two_project_prefix_fixture.py) rather than standing
    up a third variant of the same two-topology fixture. Ticket 4's own
    tests only ever drove the branch-prefix-SPECIFIC checks (a: Active RC
    field validation, e: branch-name construction) through this fixture.
    Check (c) -- status.md state vs. queue marker consistency -- has nothing
    to do with branches at all (verify-rc-state.sh's own header says so).
    This test proves that check (c) still finds and reports the identical
    mismatch, word for word aside from the file path, on the prefixed
    project as it does on the bare-prefix project -- so the branch-prefix
    refactor introduced no regression in the checks that were never about
    branches.
    """
    require_binary("git", label="diagnostics-fixture-matrix")

    prefixed_repo = tmp_path / "repo_prefixed"
    bare_repo = tmp_path / "repo_bare"
    _init_local_repo(prefixed_repo)
    _init_local_repo(bare_repo)
    _create_branch(prefixed_repo, "ai_rc/v1.0.0")
    _create_branch(bare_repo, "rc/v1.0.0")

    kanban_root = _build_dual_project_kanban_root(
        tmp_path,
        "matrixtest_prefixed", prefixed_repo,
        "matrixtest_bare", bare_repo,
    )
    _write_release_state(kanban_root / "projects" / "matrixtest_prefixed", "v1.0.0")
    _write_release_state(kanban_root / "projects" / "matrixtest_bare", "v1.0.0")

    # Identical task id and identical (wrong) marker on both projects: the
    # task is WORKING (expected marker [A]) but the queue still carries the
    # stale [x] ("done") marker from before it was picked back up.
    # verify-rc-state.sh's task_id_pat requires two uppercase letter-groups
    # before the date (the legacy CLAUDE-<AGENT>-YYYYMMDD-NNN-slug shape --
    # see team/SOP-core.md, "Task ID and queue path format"); a bare
    # CODER-YYYYMMDD-NNN id would be silently skipped by checks (b)/(c)/(d).
    task_id = "CLAUDE-CODER-20260910-301-consistency-check"
    for project_name in ("matrixtest_prefixed", "matrixtest_bare"):
        project_root = kanban_root / "projects" / project_name
        task_dir = project_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.md").write_text(
            "## State\nWORKING\n\n## Summary\nfixture task\n", encoding="utf-8"
        )
        queue_dir = project_root / "tasks" / "queues"
        queue_dir.mkdir(parents=True, exist_ok=True)
        (queue_dir / "coder_backlog.md").write_text(
            f"- [x] {task_id} — stale done marker\n", encoding="utf-8"
        )

    prefixed_result = _run_verify_rc_state(kanban_root, "matrixtest_prefixed")
    bare_result = _run_verify_rc_state(kanban_root, "matrixtest_bare")

    for result in (prefixed_result, bare_result):
        assert result.returncode == 1, result.stdout + result.stderr
        error_lines = [
            line for line in result.stdout.splitlines()
            if line.startswith("[ERROR]") and "status/marker mismatch" in line
        ]
        assert error_lines, result.stdout

    prefixed_error = next(
        line for line in prefixed_result.stdout.splitlines()
        if line.startswith("[ERROR]") and "status/marker mismatch" in line
    )
    bare_error = next(
        line for line in bare_result.stdout.splitlines()
        if line.startswith("[ERROR]") and "status/marker mismatch" in line
    )
    # Compare everything up to the trailing "— status: <path>" segment, since
    # the two status.md paths differ by project name/tmp_path even though
    # the seeded inconsistency and its finding text are identical.
    prefixed_msg = prefixed_error.split(" — status: ")[0]
    bare_msg = bare_error.split(" — status: ")[0]
    assert prefixed_msg == bare_msg, (
        "the same seeded status.md/queue-marker mismatch must produce "
        "byte-identical finding text (aside from the file path) on both "
        f"topologies:\nprefixed: {prefixed_msg!r}\nbare:     {bare_msg!r}"
    )
    assert task_id in prefixed_msg
    assert "expected [A]" in prefixed_msg, prefixed_msg
