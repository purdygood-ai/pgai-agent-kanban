"""
test_why_details_pane.py
=========================
Behavioral tests for team/scripts/why.sh --details' full diagnostic pane
(team/scripts/lib/why_details.sh): locks, queue tails with raw markers, the
embedded show-queues --details pane, the whole posture (HALT/HALT-AFTER,
deferral tiers, Active RC, pending bundles), and the project's most recent
discovery/gate refusal.

Acceptance criteria exercised:

  (1) A held lock is printed with its holder, its age, and its full
      provenance record, sourced from lock_provenance.sh's readers.
  (2) The last five queue entries print with their raw markers verbatim; a
      marker that disagrees with its task's status.md ## State shows both
      values in the output.
  (3) The output contains the show-queues --details pane; why.sh invokes
      show-queues.sh rather than carrying its own copy of that rendering
      (proven by comparing the embedded section against a direct
      invocation of show-queues.sh with the same arguments).
  (4) The posture section names HALT/HALT-AFTER (including, for a
      version-pinned sentinel, the until target via the shared
      describe_token definition), the deferral posture with tiers, the
      Active RC, and every pending bundle with its ## Status.
  (5) The most recent discovery/gate refusal is read from the refusal
      ledger; a fixture with none recorded renders an explicit empty
      state.
  (6) The verdict line still appears, unchanged, above the detail
      sections; running --details twice leaves every file under the
      fixture kanban root byte-identical.

Tests invoke why.sh via subprocess, exercising the real production shell
code rather than a reimplementation of it, matching the pattern in
test_why_verdict_engine.py.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import subprocess

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WHY_SH = _TEAM_DIR / "scripts" / "why.sh"
_SHOW_QUEUES_SH = _TEAM_DIR / "scripts" / "dashboard" / "show-queues.sh"


def _env(kanban_root: pathlib.Path, now_epoch: int | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    if now_epoch is not None:
        env["VERDICT_NOW_EPOCH"] = str(now_epoch)
    else:
        env.pop("VERDICT_NOW_EPOCH", None)
    return env


def _run(
    args: list[str],
    kanban_root: pathlib.Path,
    now_epoch: int | None = None,
    timeout: int = 20,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_WHY_SH), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root, now_epoch),
        timeout=timeout,
    )


def _run_show_queues(args: list[str], kanban_root: pathlib.Path, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SHOW_QUEUES_SH), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root),
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


def _write_task(project_dir: pathlib.Path, task_id: str, state: str) -> pathlib.Path:
    task_dir = project_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(f"## State\n{state}\n\n## Summary\nfixture task\n", encoding="utf-8")
    return status_file


def _write_queue(project_dir: pathlib.Path, agent: str, entries: list[str]) -> pathlib.Path:
    """entries: list of raw '- [marker] TASK-ID' lines, in file order."""
    queue_dir = project_dir / "tasks" / "queues"
    queue_dir.mkdir(parents=True, exist_ok=True)
    queue_file = queue_dir / f"{agent}_backlog.md"
    queue_file.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return queue_file


def _write_lock(kanban_root: pathlib.Path, filename: str, holder: str, pid: str, op: str, task: str) -> pathlib.Path:
    locks_dir = kanban_root / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_file = locks_dir / filename
    lock_file.write_text(f"holder={holder}\npid={pid}\nop={op}\ntask={task}\n", encoding="utf-8")
    return lock_file


def _write_refusal_ledger(
    project_dir: pathlib.Path,
    *,
    timestamp: str,
    surface: str,
    error: str,
    message: str,
    remedy: str,
    context: str | None = None,
) -> pathlib.Path:
    """context=None omits the '- Context:' bullet entirely -- reproducing a
    record written before that field existed, which read_latest() reports
    as context=unknown rather than guessing."""
    ledger = project_dir / "refusal-ledger.md"
    context_line = f"- Context: {context}\n" if context is not None else ""
    ledger.write_text(
        "# Refusal Ledger — demo\n\n"
        f"## Refusal: {timestamp} — demo — {surface}\n"
        f"{context_line}"
        f"- Error: {error}\n"
        f"- Message: {message}\n"
        f"- Remedy: {remedy}\n",
        encoding="utf-8",
    )
    return ledger


def _write_bundle(project_dir: pathlib.Path, filename: str, status: str) -> pathlib.Path:
    requirements_dir = project_dir / "requirements"
    requirements_dir.mkdir(parents=True, exist_ok=True)
    bundle_file = requirements_dir / filename
    bundle_file.write_text(f"# {filename}\n\n## Status\n{status}\n", encoding="utf-8")
    return bundle_file


def _hash_tree(root: pathlib.Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _section(stdout: str, header: str) -> str:
    """Extract the text between '== <header> ==' and the next '== ... ==' (or EOF)."""
    pattern = re.compile(rf"== {re.escape(header)} ==\n(.*?)(?=\n== |\Z)", re.S)
    m = pattern.search(stdout)
    assert m, f"section '{header}' not found in output:\n{stdout}"
    return m.group(1)


# ---------------------------------------------------------------------------
# (1) Locks: holder, age, full provenance record
# ---------------------------------------------------------------------------


def test_details_prints_held_lock_with_holder_held_line_and_provenance(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")
    _write_lock(tmp_path, "repo-wake-demo.lock", "coder", "12345", "wake-chain:demo", "CODER-20260910-001-demo")

    result = _run(["--project", "demo", "--details"], tmp_path)

    assert result.returncode in (0, 2, 3, 4, 5), f"why.sh refused: {result.stderr!r}"
    locks = _section(result.stdout, "locks")
    assert "holder=coder" in locks
    assert "pid=12345" in locks
    assert "op=wake-chain:demo" in locks
    assert "task=CODER-20260910-001-demo" in locks
    # The fixture's record carries no acquired_at, so the held line says so
    # instead of inventing a duration -- and no mtime-derived age is shown.
    assert "held: acquired_at not recorded (pre-upgrade lock) by coder pid 12345" in locks, locks
    assert not re.search(r"^\s*age:", locks, re.MULTILINE), f"mtime age must not render, got: {locks!r}"


def test_details_locks_section_renders_explicit_empty_state_with_no_locks(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo", "--details"], tmp_path)

    locks = _section(result.stdout, "locks")
    assert "no locks held for this project" in locks


def test_details_ignores_a_lock_belonging_to_a_different_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")
    _write_lock(tmp_path, "repo-wake-other.lock", "coder", "1", "wake-chain:other", "none")

    result = _run(["--project", "demo", "--details"], tmp_path)

    locks = _section(result.stdout, "locks")
    assert "no locks held for this project" in locks


# ---------------------------------------------------------------------------
# (2) Queue tails: raw markers, last 5, marker/status.md disagreement visible
# ---------------------------------------------------------------------------


def test_details_prints_last_five_queue_entries_with_raw_markers(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    entries = [f"- [ ] CODER-20260910-{i:03d}-demo — item {i}" for i in range(1, 8)]
    _write_queue(project_dir, "coder", entries)

    result = _run(["--project", "demo", "--details"], tmp_path)

    tails = _section(result.stdout, "queue tails (last 5, raw markers)")
    # Only the last 5 of the 7 written entries appear.
    assert "CODER-20260910-001-demo" not in tails
    assert "CODER-20260910-002-demo" not in tails
    for i in range(3, 8):
        assert f"CODER-20260910-{i:03d}-demo" in tails
    assert "[ ]" in tails


def test_details_shows_both_raw_marker_and_status_md_state_when_they_disagree(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")
    # Queue marker 'x' normally means done -- but status.md says WORKING.
    _write_queue(project_dir, "coder", ["- [x] CODER-20260910-001-demo — stale marker"])

    result = _run(["--project", "demo", "--details"], tmp_path)

    tails = _section(result.stdout, "queue tails (last 5, raw markers)")
    assert "[x] CODER-20260910-001-demo" in tails
    assert "status.md: WORKING" in tails


def test_details_queue_section_renders_empty_state_per_queue_and_overall(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    (project_dir / "tasks" / "queues").mkdir(parents=True, exist_ok=True)

    result = _run(["--project", "demo", "--details"], tmp_path)

    tails = _section(result.stdout, "queue tails (last 5, raw markers)")
    assert "no queues found for this project" in tails


# ---------------------------------------------------------------------------
# (3) show-queues --details pane embedded, not reimplemented
# ---------------------------------------------------------------------------


def test_details_show_queues_section_matches_direct_show_queues_invocation(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")

    why_result = _run(["--project", "demo", "--details"], tmp_path)
    show_queues_result = _run_show_queues(["--project", "demo", "--details"], tmp_path)

    assert show_queues_result.returncode == 0, f"show-queues.sh failed: {show_queues_result.stderr!r}"
    embedded = _section(why_result.stdout, "show-queues --details").strip("\n")
    direct = show_queues_result.stdout.strip("\n")
    assert embedded == direct, (
        "why.sh's embedded show-queues pane must be byte-identical to invoking "
        "show-queues.sh directly with the same arguments -- a mismatch means "
        "why.sh carries its own copy of the rendering instead of calling it."
    )


# ---------------------------------------------------------------------------
# (4) Posture: HALT/HALT-AFTER, deferral tiers, Active RC, pending bundles
# ---------------------------------------------------------------------------


def test_details_posture_names_a_version_pinned_halt_after_sentinel_and_its_until_target(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    (project_dir / "HALT-AFTER").write_text("rc:v2.9.15\n", encoding="utf-8")

    result = _run(["--project", "demo", "--details"], tmp_path)

    posture = _section(result.stdout, "posture")
    assert "HALT-AFTER" in posture
    assert "token=rc:v2.9.15" in posture, f"expected the sentinel's raw form in: {posture!r}"
    assert "until v2.9.15" in posture, "expected the until target rendered via describe_token"


def test_details_posture_names_halt_present(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    (project_dir / "HALT").write_text("# CM HALT: repeated release failure\n", encoding="utf-8")

    result = _run(["--project", "demo", "--details"], tmp_path)

    posture = _section(result.stdout, "posture")
    assert "HALT:" in posture
    assert "present" in posture


def test_details_posture_names_no_halt_in_force_when_neither_file_present(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo", "--details"], tmp_path)

    posture = _section(result.stdout, "posture")
    assert "none in force" in posture


def test_details_posture_still_contains_the_four_deferral_posture_lines(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo", "--details"], tmp_path)

    posture = _section(result.stdout, "posture")
    assert "deferred_bugs=false (default)" in posture
    assert "deferred_bug_level=low (default)" in posture
    assert "defer_piggyback=true (default)" in posture
    assert "until=(none) (default)" in posture


def test_details_posture_names_active_rc_and_pending_bundles(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    (project_dir / "release-state.md").write_text("## Active RC\nv2.15.0\n", encoding="utf-8")
    _write_bundle(project_dir, "v2.15.0-bugfix-bundle-20260910.md", "open")
    _write_bundle(project_dir, "v2.14.0-bugfix-bundle-20260101.md", "done")

    result = _run(["--project", "demo", "--details"], tmp_path)

    posture = _section(result.stdout, "posture")
    assert "Active RC: v2.15.0" in posture
    assert "v2.15.0-bugfix-bundle-20260910.md: open" in posture
    assert "v2.14.0-bugfix-bundle-20260101.md" not in posture, "a done bundle is not pending"


def test_details_posture_names_no_pending_bundles_when_none_open(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_bundle(project_dir, "v2.14.0-bugfix-bundle-20260101.md", "done")

    result = _run(["--project", "demo", "--details"], tmp_path)

    posture = _section(result.stdout, "posture")
    assert "no pending bundles" in posture
    assert "Active RC: none" in posture


# ---------------------------------------------------------------------------
# (5) Discovery/gate refusal from the ledger
# ---------------------------------------------------------------------------


def test_details_prints_the_most_recent_refusal_recorded_in_the_ledger(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_refusal_ledger(
        project_dir,
        timestamp="2026-09-09T12:00:00+00:00",
        surface="discovery.step_bugs",
        error="discovery.bugs_dir_missing",
        message="bugs/ directory not found for project demo",
        remedy="create projects/demo/bugs/",
    )

    result = _run(["--project", "demo", "--details"], tmp_path)

    discovery = _section(result.stdout, "discovery skip reason")
    assert "2026-09-09T12:00:00+00:00" in discovery
    assert "discovery.step_bugs" in discovery
    assert "discovery.bugs_dir_missing" in discovery
    assert "bugs/ directory not found for project demo" in discovery
    assert "create projects/demo/bugs/" in discovery


def test_details_discovery_skip_reason_renders_explicit_empty_state_with_no_refusal_recorded(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo", "--details"], tmp_path)

    discovery = _section(result.stdout, "discovery skip reason")
    assert "no discovery/gate refusal recorded for this project" in discovery


# ---------------------------------------------------------------------------
# (5b) Belt-and-braces filter: only the install's own refusals render,
# never a test-context or out-of-root one -- see why_details.sh's own
# _why_details_refusal_is_install_scoped for the full rationale.
# ---------------------------------------------------------------------------


def test_details_prints_an_install_context_refusal_naming_a_path_inside_the_kanban_root(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    lock_path = tmp_path / "locks" / "repo-wake-demo.lock"
    _write_refusal_ledger(
        project_dir,
        timestamp="2026-09-19T20:00:00+00:00",
        surface="lock_provenance.sh:pgai_lock_acquire",
        error="lock.provenance.contended",
        message=f"pgai_lock_acquire could not take {lock_path}: it is already held by coder (pid 999)",
        remedy="team/scripts/release-locks.sh --release repo-wake-demo",
        context="install",
    )

    result = _run(["--project", "demo", "--details"], tmp_path)

    discovery = _section(result.stdout, "discovery skip reason")
    assert "2026-09-19T20:00:00+00:00" in discovery
    assert "lock.provenance.contended" in discovery
    assert str(lock_path) in discovery


def test_details_skips_a_test_context_refusal_and_reports_no_refusal_at_all(
    tmp_path: pathlib.Path,
    pgai_tests_temp_dir: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    # The lock path is inert message text the filter under test reads, never
    # a path this test opens -- routed through the framework's own scratch
    # root (pgai_tests_temp_dir, which itself respects
    # PGAI_AGENT_KANBAN_TEMP_DIR) rather than a hand-spelled literal so this
    # fixture data never doubles as an anti-pattern the hygiene lint has to
    # flag on every run.
    _lock_path = (
        pgai_tests_temp_dir
        / "pytest-of-rocky" / "pytest-0" / "test_x0" / "repo-wake-demo-project.lock"
    )
    _write_refusal_ledger(
        project_dir,
        timestamp="2026-09-19T19:23:10+00:00",
        surface="lock_provenance.sh:pgai_lock_acquire",
        error="lock.provenance.contended",
        message=(
            f"pgai_lock_acquire could not take "
            f"{_lock_path}: it is already held by pytest (pid 1)"
        ),
        remedy="team/scripts/release-locks.sh --release repo-wake-demo-project",
        context="test",
    )

    result = _run(["--project", "demo", "--details"], tmp_path)

    discovery = _section(result.stdout, "discovery skip reason")
    assert "no discovery/gate refusal recorded for this project" in discovery
    assert "repo-wake-demo-project.lock" not in discovery
    assert "2026-09-19T19:23:10+00:00" not in discovery


def test_details_skips_a_refusal_whose_subject_path_lies_outside_kanban_root_even_without_a_context_field(
    tmp_path: pathlib.Path,
    pgai_tests_temp_dir: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    # The lock path is inert message text the filter under test reads, never
    # a path this test opens -- routed through the framework's own scratch
    # root (pgai_tests_temp_dir) rather than a hand-spelled literal; the
    # only property this test actually depends on is that the path lies
    # outside the fixture's own kanban_root (tmp_path).
    _lock_path = (
        pgai_tests_temp_dir
        / "pytest-of-rocky" / "pytest-0" / "test_x0" / "repo-wake-demo-project.lock"
    )
    _write_refusal_ledger(
        project_dir,
        timestamp="2026-09-19T19:23:10+00:00",
        surface="lock_provenance.sh:pgai_lock_acquire",
        error="lock.provenance.contended",
        message=(
            f"pgai_lock_acquire could not take "
            f"{_lock_path}: it is already held by pytest (pid 1)"
        ),
        remedy="team/scripts/release-locks.sh --release repo-wake-demo-project",
        # context intentionally omitted -- reproduces a record written
        # before that field existed (read_latest() reports context=unknown
        # for it); the path check alone must still catch it.
    )

    result = _run(["--project", "demo", "--details"], tmp_path)

    discovery = _section(result.stdout, "discovery skip reason")
    assert "no discovery/gate refusal recorded for this project" in discovery
    assert "repo-wake-demo-project.lock" not in discovery


# ---------------------------------------------------------------------------
# (6) Verdict line stays visible above the detail sections; read-only
# ---------------------------------------------------------------------------


def test_details_verdict_line_appears_unchanged_above_the_detail_sections(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BLOCKED")

    plain_result = _run(["--project", "demo"], tmp_path)
    details_result = _run(["--project", "demo", "--details"], tmp_path)

    # --details exits with the same code the plain verdict invocation does
    # (the exit-code equality half of the contract), and its printed
    # CONTENT gained the verdict line and the new sections -- this asserts
    # both the exit code and the content.
    plain_line = plain_result.stdout.strip()
    assert plain_line, "plain verdict invocation printed nothing"
    assert details_result.returncode == plain_result.returncode, (
        f"--details exited {details_result.returncode} but the plain verdict "
        f"invocation exited {plain_result.returncode} for the same project state"
    )
    assert details_result.stdout.splitlines()[0] == plain_line, (
        "the verdict line must be the first line of --details' output, "
        "unchanged from the plain --project invocation"
    )


def test_details_run_twice_leaves_every_file_under_the_fixture_root_byte_identical(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")
    _write_queue(project_dir, "coder", ["- [ ] CODER-20260910-001-demo — working"])
    _write_lock(tmp_path, "repo-wake-demo.lock", "coder", "1", "wake-chain:demo", "CODER-20260910-001-demo")
    (project_dir / "release-state.md").write_text("## Active RC\nv2.15.0\n", encoding="utf-8")
    _write_bundle(project_dir, "v2.15.0-bugfix-bundle-20260910.md", "open")
    _write_refusal_ledger(
        project_dir,
        timestamp="2026-09-09T12:00:00+00:00",
        surface="discovery.step_bugs",
        error="discovery.bugs_dir_missing",
        message="bugs/ directory not found for project demo",
        remedy="create projects/demo/bugs/",
    )

    before = _hash_tree(tmp_path)

    first = _run(["--project", "demo", "--details"], tmp_path)
    second = _run(["--project", "demo", "--details"], tmp_path)

    assert first.returncode == second.returncode

    after = _hash_tree(tmp_path)
    assert before == after, "--details must never write, edit, or remove any file under the fixture root"


# ---------------------------------------------------------------------------
# Refusal propagation: a malformed campaign file refuses before anything
# from --details prints -- including the new detail sections above it.
# ---------------------------------------------------------------------------


def test_details_prints_nothing_when_the_campaign_file_is_malformed_even_with_other_sections_populated(
    tmp_path: pathlib.Path,
) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_lock(tmp_path, "repo-wake-demo.lock", "coder", "1", "wake-chain:demo", "CODER-20260910-001-demo")
    (project_dir / "DEFER-LOW-BUGS").write_text("until=2.14\n", encoding="utf-8")

    result = _run(["--project", "demo", "--details"], tmp_path)

    assert result.returncode != 0
    assert result.stdout == "", "a malformed campaign file must refuse before any detail section prints"
    assert str(project_dir / "DEFER-LOW-BUGS") in result.stderr


# ---------------------------------------------------------------------------
# --details must exit with the verdict's own code, not the rendering
# function's return status. A halted lane and a healthy one used to both
# come back exit 0 under --details; these tests pin the equality against
# the plain (non-detailed) invocation for each of the five verdicts, with
# HALTED and RUNNING-stalled covered explicitly since those are the two
# codes an operator actually pages on.
# ---------------------------------------------------------------------------

# Mirrors STALL_THRESHOLD_MINUTES in team/pgai_agent_kanban/ops/verdict.py.
# Duplicated here (not sourced), matching test_why_verdict_engine.py's own
# black-box copy, so a change to the production constant fails this test
# loud rather than silently tracking a moving target.
_STALL_THRESHOLD_MINUTES = 60


def test_details_exit_code_matches_plain_verdict_for_halted_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    (project_dir / "HALT").write_text("# CM HALT: repeated release failure\n", encoding="utf-8")

    plain_result = _run(["--project", "demo"], tmp_path)
    details_result = _run(["--project", "demo", "--details"], tmp_path)

    assert plain_result.returncode == 5, f"expected the HALTED exit code, got {plain_result.returncode}"
    assert details_result.returncode == plain_result.returncode == 5, (
        f"--details exited {details_result.returncode} for a HALTED project; must exit 5 like the plain "
        "invocation does, not collapse the verdict to 0"
    )


def test_details_exit_code_matches_plain_verdict_for_stalled_running_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    now = 1_800_000_000
    stalled_mtime = now - ((_STALL_THRESHOLD_MINUTES + 5) * 60)
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")
    os.utime(project_dir / "tasks" / "CODER-20260910-001-demo" / "status.md", (stalled_mtime, stalled_mtime))

    plain_result = _run(["--project", "demo"], tmp_path, now_epoch=now)
    details_result = _run(["--project", "demo", "--details"], tmp_path, now_epoch=now)

    assert plain_result.returncode == 3, f"expected the RUNNING-stalled exit code, got {plain_result.returncode}"
    assert details_result.returncode == plain_result.returncode == 3, (
        f"--details exited {details_result.returncode} for a stalled RUNNING project; must exit 3 like the "
        "plain invocation does, not collapse the verdict to 0"
    )


def test_details_exit_code_matches_plain_verdict_for_blocked_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BLOCKED")

    plain_result = _run(["--project", "demo"], tmp_path)
    details_result = _run(["--project", "demo", "--details"], tmp_path)

    assert plain_result.returncode == 4
    assert details_result.returncode == plain_result.returncode == 4


def test_details_exit_code_matches_plain_verdict_for_idle_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    plain_result = _run(["--project", "demo"], tmp_path)
    details_result = _run(["--project", "demo", "--details"], tmp_path)

    assert plain_result.returncode == 2
    assert details_result.returncode == plain_result.returncode == 2


def test_details_exit_code_matches_plain_verdict_for_healthy_running_project(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "WORKING")

    plain_result = _run(["--project", "demo"], tmp_path)
    details_result = _run(["--project", "demo", "--details"], tmp_path)

    assert plain_result.returncode == 0
    assert details_result.returncode == plain_result.returncode == 0


def test_details_still_exits_1_and_prints_nothing_on_a_section_refusal(tmp_path: pathlib.Path) -> None:
    # Guards against a fix that always exits with the verdict's code even
    # when a section refused -- a refusal must still win at exit 1
    # regardless of what the (never-printed) verdict would have been.
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    project_dir = _make_project_dir(tmp_path, "demo")
    _write_task(project_dir, "CODER-20260910-001-demo", "BLOCKED")
    (project_dir / "DEFER-LOW-BUGS").write_text("until=2.14\n", encoding="utf-8")

    result = _run(["--project", "demo", "--details"], tmp_path)

    assert result.returncode == 1, "a section refusal must exit 1, not the verdict's own code"
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# (7) Last docker use: the last line of the install's docker-use log
# ---------------------------------------------------------------------------


def test_details_prints_the_last_docker_use_line_from_the_install_log(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")
    log = tmp_path / "logs" / "docker-use.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    older = "2026-10-01T03:00:00Z START actor=operator task=- script=deep-verify.sh images=debian"
    newest = (
        "2026-10-01T03:10:00Z SKIPPED actor=coder task=CODER-20261001-001-demo "
        "script=run-cold-build-tests.sh images=-"
    )
    log.write_text(f"{older}\n{newest}\n", encoding="utf-8")

    result = _run(["--project", "demo", "--details"], tmp_path)

    docker_use = _section(result.stdout, "docker use")
    assert docker_use.strip() == f"last docker use: {newest}", docker_use
    assert older not in result.stdout


def test_details_docker_use_section_renders_explicit_empty_state_with_no_log(tmp_path: pathlib.Path) -> None:
    _write_projects_cfg(tmp_path, [("demo", 1, True)])
    _make_project_dir(tmp_path, "demo")

    result = _run(["--project", "demo", "--details"], tmp_path)

    docker_use = _section(result.stdout, "docker use")
    assert "no docker use recorded" in docker_use, docker_use
    assert not (tmp_path / "logs" / "docker-use.log").exists(), "--details must never create the log"
