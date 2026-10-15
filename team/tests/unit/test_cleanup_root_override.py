"""
test_cleanup_root_override.py
==============================
Behavioral unit tests for team/scripts/cleanup/cleanup.sh's root
reconciliation between KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH.

cleanup.sh used to read TEAM_ROOT (derived from PGAI_AGENT_KANBAN_ROOT_PATH)
for the Step 0 existence guard only, while every destructive step (1-6) and
the summary log's own write path read KANBAN_ROOT directly with no fallback
to PGAI_AGENT_KANBAN_ROOT_PATH. An invocation that repointed only
PGAI_AGENT_KANBAN_ROOT_PATH -- the variable the Live-Install Safety
sandboxing procedure in the role files names -- left KANBAN_ROOT holding
whatever the wake environment had already exported, so Steps 1-6 acted on
the live install while the caller believed they had sandboxed the run.

These tests pin the reconciliation: the two variables are reconciled once,
before any destructive step runs. Three scenarios:

  1. Both set and disagreeing -- refuses dual-channel, naming both
     variables, and leaves a decoy "live" root byte-identical.
  2. Only PGAI_AGENT_KANBAN_ROOT_PATH set (KANBAN_ROOT entirely absent from
     the environment) -- KANBAN_ROOT is derived from the override, so a
     single override sandboxes the whole run.
  3. Both entirely unset -- the script still fails closed (pre-existing
     behavior, preserved unchanged by the reconciliation), rather than silently
     defaulting into a destructive run against $HOME/pgai_agent_kanban.
     HOME is pointed at a decoy directory for this one test so a
     regression cannot reach the real live install even if the
     fail-closed guard breaks.

A fourth and fifth scenario cover a residual seam the KANBAN_ROOT /
PGAI_AGENT_KANBAN_ROOT_PATH reconciliation above does not close:
PGAI_TASKS_DIR and PGAI_PROJECT_ROOT are pre-resolved live-install paths
that outrank anything the reconciled root derives, so a run whose roots
are both correctly sandboxed can still carry one of these two exports
from an earlier, live-install invocation. Both are reconciled the same
way as the two roots above -- refuse dual-channel when the export does
not match the path the reconciled root derives, naming both values,
rather than silently preferring the export.

  4. PGAI_TASKS_DIR set to a decoy directory while both roots are
     correctly sandboxed -- refuses dual-channel, naming the export and
     the reconciled tasks directory, and leaves a decoy terminal task
     folder (backdated so Step 2 would otherwise read it) intact.
  5. PGAI_PROJECT_ROOT set to a decoy directory under the same
     conditions -- refuses dual-channel, naming the export and the
     reconciled project root, and leaves decoy project-scoped state
     intact.

A sixth pair of scenarios locks the follow-on fix: the
comparison above only ever tested exact-string equality, so a
PGAI_TASKS_DIR or PGAI_PROJECT_ROOT that names exactly the reconciled
directory but carries a trailing slash -- the form shell completion,
a `dirname`/`printf '%s/'` construction, or a hand-typed path routinely
produces -- was refused with a message claiming a containment test
("does not sit underneath") that the exact-equality code never ran.
The comparison now strips a single trailing slash before comparing, and
the message says "does not match" -- what the code actually checks.

  6. PGAI_TASKS_DIR (then PGAI_PROJECT_ROOT) set to the reconciled
     directory plus a trailing slash -- the run completes (exit 0) with
     every Step header naming the sandbox root, and a genuine sub-path
     of the reconciled directory is still refused (cleanup.sh normalises
     spelling, it does not widen the check into containment).
"""

from __future__ import annotations

import os
import pathlib
import time

from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"


def _snapshot(root: pathlib.Path) -> set[str]:
    """Return the set of relative paths for every file under *root*."""
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _seed_decoy_root(root: pathlib.Path) -> None:
    """Populate *root* with state that would be visible to Steps 0-6 if
    cleanup.sh ever operated on it: a batch log, a per-project tasks dir,
    and a requirements dir."""
    (root / "logs" / "agents").mkdir(parents=True)
    (root / "logs" / "agents" / "existing.log").write_text("pre-existing content\n")
    (root / "projects" / "demo" / "tasks").mkdir(parents=True)
    (root / "projects" / "demo" / "requirements").mkdir(parents=True)


# ---------------------------------------------------------------------------
# Scenario 1 — both roots set and disagreeing: refuse, decoy untouched
# ---------------------------------------------------------------------------


def test_disagreeing_roots_refuse_dual_channel_and_leave_decoy_untouched(
    tmp_path: pathlib.Path,
) -> None:
    decoy_root = tmp_path / "decoy_live_root"
    _seed_decoy_root(decoy_root)
    sandbox_root = tmp_path / "sandbox_root"
    sandbox_root.mkdir()

    before = _snapshot(decoy_root)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo --dry-run",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(decoy_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
        },
    )

    assert result.returncode != 0, (
        "expected a refusal when KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH "
        f"disagree; got exit {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    # Dual-channel error contract: stable key, plain message, runnable remedy.
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    # Both variables are named so the operator knows exactly what disagreed.
    assert str(decoy_root) in result.stderr
    assert str(sandbox_root) in result.stderr

    # No Step header or summary "log written to:" line ever fired.
    assert "Step 1" not in result.stdout
    assert "Step 2" not in result.stdout
    assert "Step 3" not in result.stdout
    assert "log written to:" not in result.stdout

    # The decoy "live" root is byte-identical to before the invocation.
    after = _snapshot(decoy_root)
    assert before == after, (
        f"decoy root changed: added={after - before}, removed={before - after}"
    )
    assert (
        decoy_root / "logs" / "agents" / "existing.log"
    ).read_text() == "pre-existing content\n"


def test_disagreeing_roots_refuse_even_without_dry_run(tmp_path: pathlib.Path) -> None:
    """The refusal fires before any destructive action, so it does not
    depend on --dry-run being present -- a non-dry-run invocation with
    disagreeing roots must refuse just as loudly."""
    decoy_root = tmp_path / "decoy_live_root"
    _seed_decoy_root(decoy_root)
    sandbox_root = tmp_path / "sandbox_root"
    sandbox_root.mkdir()

    before = _snapshot(decoy_root)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(decoy_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
        },
    )

    assert result.returncode != 0
    after = _snapshot(decoy_root)
    assert before == after, (
        f"decoy root changed: added={after - before}, removed={before - after}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — only PGAI_AGENT_KANBAN_ROOT_PATH set: derive and stay sandboxed
# ---------------------------------------------------------------------------


def test_missing_kanban_root_is_derived_from_override_and_stays_sandboxed(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Following the role-file sandboxing procedure literally -- repointing
    only PGAI_AGENT_KANBAN_ROOT_PATH -- must actually sandbox the run when
    KANBAN_ROOT was never set in the environment to begin with."""
    sandbox_root = tmp_path / "sandbox_root"
    sandbox_root.mkdir()
    monkeypatch.delenv("KANBAN_ROOT", raising=False)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo --temp-only",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox_root / "temp"),
            # Cleared so cleanup.sh derives both from the just-repointed
            # KANBAN_ROOT rather than refusing on a stale export inherited
            # from the calling shell -- this test is exercising root
            # derivation, not export reconciliation.
            "PGAI_TASKS_DIR": "",
            "PGAI_PROJECT_ROOT": "",
        },
    )

    assert result.returncode == 0, (
        f"expected the derived KANBAN_ROOT to let the run complete; "
        f"got exit {result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert str(sandbox_root) in result.stdout, (
        "expected the sandboxed temp dir path to appear in output once "
        f"KANBAN_ROOT is derived from PGAI_AGENT_KANBAN_ROOT_PATH:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — both entirely unset: still fails closed (Case B, preserved)
# ---------------------------------------------------------------------------


def test_both_roots_unset_fails_closed_without_home_fallback(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """With neither variable set, cleanup.sh must still fail closed rather
    than silently operating against $HOME/pgai_agent_kanban. HOME is pointed
    at a decoy directory so a regression in the fail-closed guard cannot
    reach the real live install even if this assertion is wrong."""
    decoy_home = tmp_path / "decoy_home"
    decoy_home.mkdir()
    monkeypatch.delenv("KANBAN_ROOT", raising=False)
    monkeypatch.delenv("PGAI_AGENT_KANBAN_ROOT_PATH", raising=False)
    monkeypatch.setenv("HOME", str(decoy_home))

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo --dry-run",
    )

    assert result.returncode != 0, (
        "expected cleanup.sh to fail closed with both root variables unset; "
        f"got exit {result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert not (decoy_home / "pgai_agent_kanban").exists(), (
        "cleanup.sh must not silently default into $HOME/pgai_agent_kanban "
        "when both KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH are unset"
    )
    assert "Step 1" not in result.stdout
    assert "log written to:" not in result.stdout


# ---------------------------------------------------------------------------
# Scenario 4 -- PGAI_TASKS_DIR set to a decoy while both roots are correctly
# sandboxed: refuses dual-channel, decoy terminal task folder untouched
# ---------------------------------------------------------------------------


def _seed_decoy_tasks_dir(tasks_dir: pathlib.Path) -> None:
    """Populate *tasks_dir* with a terminal-state task folder old enough for
    Step 2 to count -- the concrete thing a run that resolved
    PGAI_TASKS_DIR from this decoy instead of the sandboxed root would
    read if the export reconciliation did not refuse first."""
    task_dir = tasks_dir / "CLAUDE-old-terminal-task"
    task_dir.mkdir(parents=True)
    status_file = task_dir / "status.md"
    status_file.write_text("## State\nDONE\n")
    # Backdate well past the default 7-day task retention window so an
    # unguarded run would actually reach this folder, not skip it as too
    # young -- the assertion below proves something, rather than nothing.
    old_time = time.time() - (10 * 86400)
    os.utime(status_file, (old_time, old_time))
    os.utime(task_dir, (old_time, old_time))


def test_tasks_dir_export_reaching_past_sandboxed_roots_refuses_and_leaves_decoy_untouched(
    tmp_path: pathlib.Path,
) -> None:
    """Both roots point at the sandbox correctly (the Scenario 2 shape) but
    PGAI_TASKS_DIR still carries a decoy path, as it would if the calling
    environment exported it before the roots were repointed. The export
    reconciliation must refuse rather than let Step 2 act on the decoy."""
    sandbox_root = tmp_path / "sandbox_root"
    sandbox_root.mkdir()
    reconciled_tasks_dir = sandbox_root / "projects" / "demo" / "tasks"

    decoy_tasks_dir = tmp_path / "decoy_live_install" / "tasks"
    _seed_decoy_tasks_dir(decoy_tasks_dir)
    before = _snapshot(decoy_tasks_dir)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(sandbox_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "PGAI_TASKS_DIR": str(decoy_tasks_dir),
            # Cleared so only the tasks-dir export is under test here.
            "PGAI_PROJECT_ROOT": "",
        },
    )

    assert result.returncode != 0, (
        "expected a refusal when PGAI_TASKS_DIR does not match the "
        f"reconciled tasks directory; got exit {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    # Dual-channel error contract: stable key, plain message, runnable remedy.
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    # Both the export and the reconciled path are named so the operator
    # knows exactly what disagreed.
    assert str(decoy_tasks_dir) in result.stderr
    assert str(reconciled_tasks_dir) in result.stderr

    # No Step header or summary "log written to:" line ever fired -- the
    # refusal happens before the script has anything to log.
    assert "Step 2" not in result.stdout
    assert "log written to:" not in result.stdout

    # The decoy terminal task folder is byte-identical to before the
    # invocation -- no step reached it.
    after = _snapshot(decoy_tasks_dir)
    assert before == after, (
        f"decoy tasks dir changed: added={after - before}, removed={before - after}"
    )
    assert (
        decoy_tasks_dir / "CLAUDE-old-terminal-task" / "status.md"
    ).read_text() == "## State\nDONE\n"


# ---------------------------------------------------------------------------
# Scenario 5 -- PGAI_PROJECT_ROOT set to a decoy while both roots are
# correctly sandboxed: refuses dual-channel, decoy project state untouched
# ---------------------------------------------------------------------------


def _seed_decoy_project_root(project_root: pathlib.Path) -> None:
    """Populate *project_root* with release-state and requirements content
    old enough to be archived -- the concrete state a run that resolved
    project-scoped paths from this decoy instead of the sandboxed root
    would read from or archive against."""
    project_root.mkdir(parents=True)
    (project_root / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## Last Released\nv9.9.9\n"
    )
    req_dir = project_root / "requirements"
    req_dir.mkdir()
    (req_dir / "v1.0.0-shipped.md").write_text(
        "# Requirements\n\n## Target Version\nv1.0.0\n"
    )


def test_project_root_export_reaching_past_sandboxed_roots_refuses_and_leaves_decoy_untouched(
    tmp_path: pathlib.Path,
) -> None:
    """Both roots point at the sandbox correctly (the Scenario 2 shape) but
    PGAI_PROJECT_ROOT still carries a decoy path, as it would if the
    calling environment exported it before the roots were repointed. The
    export reconciliation must refuse rather than let any project-scoped
    lookup resolve against the decoy."""
    sandbox_root = tmp_path / "sandbox_root"
    sandbox_root.mkdir()
    reconciled_project_root = sandbox_root / "projects" / "demo"

    decoy_project_root = tmp_path / "decoy_live_install" / "projects" / "demo"
    _seed_decoy_project_root(decoy_project_root)
    before = _snapshot(decoy_project_root)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(sandbox_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "PGAI_PROJECT_ROOT": str(decoy_project_root),
            # Cleared so only the project-root export is under test here.
            "PGAI_TASKS_DIR": "",
        },
    )

    assert result.returncode != 0, (
        "expected a refusal when PGAI_PROJECT_ROOT does not match "
        f"the reconciled project root; got exit {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    # Dual-channel error contract: stable key, plain message, runnable remedy.
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    # Both the export and the reconciled path are named so the operator
    # knows exactly what disagreed.
    assert str(decoy_project_root) in result.stderr
    assert str(reconciled_project_root) in result.stderr

    # No Step header or summary "log written to:" line ever fired -- the
    # refusal happens before the script has anything to log.
    assert "Step 1" not in result.stdout
    assert "log written to:" not in result.stdout

    # The decoy project state is byte-identical to before the invocation.
    after = _snapshot(decoy_project_root)
    assert before == after, (
        f"decoy project root changed: added={after - before}, removed={before - after}"
    )
    assert (
        decoy_project_root / "requirements" / "v1.0.0-shipped.md"
    ).read_text() == "# Requirements\n\n## Target Version\nv1.0.0\n"


# ---------------------------------------------------------------------------
# Scenario 6 -- an export that names the reconciled directory but
# spells it with a trailing slash is accepted, not refused; a genuine
# sub-path is still refused (no widening into containment).
# ---------------------------------------------------------------------------


def _seed_sandbox_project(sandbox_root: pathlib.Path) -> pathlib.Path:
    """Populate *sandbox_root* with the minimal project layout a full
    (non ``--temp-only``) run walks -- the same shape the trailing-slash
    regression's own reproduction used -- and return the reconciled tasks
    directory."""
    tasks_dir = sandbox_root / "projects" / "demo" / "tasks"
    tasks_dir.mkdir(parents=True)
    (sandbox_root / "projects" / "demo" / "requirements").mkdir(parents=True)
    (sandbox_root / "logs" / "agents").mkdir(parents=True)
    (sandbox_root / "projects" / "demo" / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## Last Released\nv1.0.0\n"
    )
    return tasks_dir


def test_tasks_dir_export_with_trailing_slash_matches_reconciled_directory(
    tmp_path: pathlib.Path,
) -> None:
    """A PGAI_TASKS_DIR naming exactly the reconciled tasks directory but
    spelled with a trailing slash must be accepted: the comparison tests
    equality (mod trailing slash), never containment, so the same string
    with either spelling names the same directory. Fails against a
    cleanup.sh that compares the spellings literally and so refuses this
    exact input."""
    sandbox_root = tmp_path / "sandbox_root"
    reconciled_tasks_dir = _seed_sandbox_project(sandbox_root)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo --dry-run",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(sandbox_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "PGAI_TASKS_DIR": str(reconciled_tasks_dir) + "/",
            # Cleared so only the tasks-dir export is under test here.
            "PGAI_PROJECT_ROOT": "",
        },
    )

    assert result.returncode == 0, (
        "a trailing slash on PGAI_TASKS_DIR names the same directory the "
        f"reconciled root derives and must not be refused; got exit "
        f"{result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert f"Step 2: report terminal task folders in {reconciled_tasks_dir} (none are deleted)" in result.stdout, (
        f"expected the Step 2 header to name the sandbox tasks directory "
        f"without a doubled trailing slash:\n{result.stdout}"
    )


def test_tasks_dir_export_naming_a_subdirectory_still_refuses(
    tmp_path: pathlib.Path,
) -> None:
    """A genuine sub-path of the reconciled tasks directory is still not
    the directory Step 2 would read, so it must still refuse -- the check
    normalises spelling, it does not widen the check into containment."""
    sandbox_root = tmp_path / "sandbox_root"
    reconciled_tasks_dir = _seed_sandbox_project(sandbox_root)
    subdir_export = reconciled_tasks_dir / "subdir"
    subdir_export.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo --dry-run",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(sandbox_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "PGAI_TASKS_DIR": str(subdir_export),
            "PGAI_PROJECT_ROOT": "",
        },
    )

    assert result.returncode != 0, (
        "a sub-path of the reconciled tasks directory is still not the "
        f"reconciled directory itself and must be refused; got exit "
        f"{result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "does not match" in result.stderr


def test_project_root_export_with_trailing_slash_matches_reconciled_directory(
    tmp_path: pathlib.Path,
) -> None:
    """The same trailing-slash acceptance for PGAI_PROJECT_ROOT. Fails
    against a cleanup.sh that compares the spellings literally and so
    refuses this exact input."""
    sandbox_root = tmp_path / "sandbox_root"
    _seed_sandbox_project(sandbox_root)
    reconciled_project_root = sandbox_root / "projects" / "demo"

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo --dry-run",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(sandbox_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "PGAI_PROJECT_ROOT": str(reconciled_project_root) + "/",
            # Cleared so only the project-root export is under test here.
            "PGAI_TASKS_DIR": "",
        },
    )

    assert result.returncode == 0, (
        "a trailing slash on PGAI_PROJECT_ROOT names the same directory the "
        f"reconciled root derives and must not be refused; got exit "
        f"{result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "error: cleanup.project_root_export_mismatch" not in result.stderr


def test_project_root_export_naming_a_subdirectory_still_refuses(
    tmp_path: pathlib.Path,
) -> None:
    """A genuine sub-path of the reconciled project root is still refused --
    no widening into containment for PGAI_PROJECT_ROOT either."""
    sandbox_root = tmp_path / "sandbox_root"
    _seed_sandbox_project(sandbox_root)
    reconciled_project_root = sandbox_root / "projects" / "demo"
    subdir_export = reconciled_project_root / "requirements"

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project demo --dry-run",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox_root),
            "KANBAN_ROOT": str(sandbox_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
            "PGAI_PROJECT_ROOT": str(subdir_export),
            "PGAI_TASKS_DIR": "",
        },
    )

    assert result.returncode != 0, (
        "a sub-path of the reconciled project root is still not the "
        f"reconciled directory itself and must be refused; got exit "
        f"{result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "does not match" in result.stderr
