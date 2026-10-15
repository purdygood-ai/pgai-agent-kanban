"""
golden_board.py
================
Test-fidelity helper: build a synthetic kanban board for golden-pair
CLI-vs-REST parity fixtures.

WHY THIS EXISTS
---------------
A golden-pair fixture (see ``golden_comparator.py`` and
``api_server.py`` in this same directory) proves that a CLI script and its
REST twin agree over the SAME data. That data must exercise the full shape
a read verb can render -- every task state, a deferred bug, and an active
deferral campaign -- or the comparison is trivial and would not catch a
verb that only handles the happy-path subset of its own vocabulary.

This module builds exactly that board, once, so every golden-pair test
seeds the identical shape instead of each test file hand-rolling its own
partial fixture (the inconsistency ``two_project.py``'s own docstring
warns against for the two-project case).

HOW TO USE
----------
    from tests.fixtures.golden_board import build_synthetic_board

    board_root = build_synthetic_board(tmp_path)
    # board_root is a pathlib.Path kanban root with:
    #   scripts/            -- symlinked to the REAL team/scripts/ tree
    #                          (the entry-point law: fixtures run the
    #                          production scripts, never a reimplementation)
    #   projects.cfg        -- registers PROJECT_NAME (priority 1, enabled)
    #   projects/<name>/tasks/<id>/status.md
    #                       -- one task per state: BACKLOG, WAITING,
    #                          WORKING, BLOCKED, DONE, WONT-DO
    #   projects/<name>/bugs/<id>.md
    #                       -- one open, low-severity bug (deferred under
    #                          the campaign file below)
    #   projects/<name>/DEFER-LOW-BUGS
    #                       -- an active deferral campaign (until=vX.Y.Z)

    board_root = build_synthetic_board(tmp_path, with_queues=True)
    # ...the same board, plus projects/<name>/tasks/queues/coder_backlog.md
    # listing every task under the marker its state derives, so the board's
    # CODER column (GET /board, the dashboard's queue pane) shows one item
    # per state -- WONT-DO included.

WHAT THIS HELPER DOES NOT DO
-----------------------------
- It does not run install.sh, create-project.sh, or any operator verb.
  It is a fast in-process builder, same contract as ``installed_root.py``
  and ``two_project.py``.
- It does not write anywhere outside the *kanban_root* argument the caller
  supplies (constraint: "No fixture writes outside the framework temp root
  or the fixture's own synthetic board directory" -- the caller is
  responsible for passing a path under pytest's ``tmp_path``, which the
  parent conftest routes under ``PGAI_AGENT_KANBAN_TEMP_DIR``).
- It does not source shell-env, claude-env, or secrets. The scripts/
  symlink alone is sufficient: ``env_bootstrap.sh``'s idempotency guard
  honors an already-exported ``PGAI_AGENT_KANBAN_ROOT_PATH`` (the operator
  env wins) without deriving anything from shell-env.
"""

from __future__ import annotations

import configparser
import pathlib

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# team/ directory in the dev tree -- two levels up from this file's directory
# (fixtures/ -> tests/ -> team/), matching installed_root.py's own constant.
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REAL_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# Default project name used across golden-pair fixtures -- deterministic and
# recognisable in logs and diffs, same rationale two_project.py gives for its
# own fixed PROJECT_A / PROJECT_B names.
PROJECT_NAME = "golden-fixture"

# One task id per state, in a stable, sorted order so callers (and directory
# listings) see a deterministic shape run to run.
_TASK_IDS_BY_STATE: dict[str, str] = {
    "BACKLOG": "CODER-20260101-001-golden-backlog",
    "WAITING": "CODER-20260101-002-golden-waiting",
    "WORKING": "CODER-20260101-003-golden-working",
    "BLOCKED": "CODER-20260101-004-golden-blocked",
    "DONE": "CODER-20260101-005-golden-done",
    "WONT-DO": "CODER-20260101-006-golden-wontdo",
}

# The campaign's "until" version -- a well-formed vX.Y.Z that resolve_defer_
# posture's until= validator accepts; deliberately far ahead of any real
# release so the campaign reads as active for the lifetime of a test run.
_CAMPAIGN_UNTIL_VERSION = "v99.0.0"

_BUG_ID = "BUG-0001-fixture-deferred-example"

# The queue marker each task state derives (SOP-core's marker table): a
# state's marker is written by the same transition that writes the state.
_MARKER_BY_STATE: dict[str, str] = {
    "BACKLOG": " ",
    "WAITING": "W",
    "WORKING": "A",
    "BLOCKED": "B",
    "DONE": "x",
    "WONT-DO": "x",
}


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _write_projects_cfg(kanban_root: pathlib.Path, project: str) -> None:
    parser = configparser.RawConfigParser()
    section = f"project:{project}"
    parser.add_section(section)
    parser.set(section, "priority", "1")
    parser.set(section, "enabled", "true")
    with (kanban_root / "projects.cfg").open("w", encoding="utf-8") as fh:
        parser.write(fh)


def _write_tasks(project_dir: pathlib.Path) -> None:
    """Write one task per state under <project_dir>/tasks/<id>/status.md.

    Only the ``## State`` field is populated -- the minimum every consumer
    (resolve_verdicts, kanban-status.sh, show.sh) reads to classify a
    task, matching the minimal shape already proven sufficient by the
    existing CLI/REST parity tests (test_cli_rest_verdict_parity.py's
    ``_write_task``). A ``## Summary`` line is included for read verbs that
    render it (e.g. show.sh).
    """
    for state, task_id in _TASK_IDS_BY_STATE.items():
        task_dir = project_dir / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "status.md").write_text(
            f"## State\n{state}\n\n## Summary\ngolden-pair fixture task ({state})\n",
            encoding="utf-8",
        )


def _write_coder_queue(project_dir: pathlib.Path) -> None:
    """Write tasks/queues/coder_backlog.md listing every fixture task.

    One canonical queue line (``- [<marker>] <task-id> — <state>``) per
    task, in the stable ``_TASK_IDS_BY_STATE`` order, each under the marker
    its state derives.  The board reads a queue item's state from the
    task's status.md first and falls back to the marker, so both agree.
    """
    queues_dir = project_dir / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# CODER backlog", ""]
    for state, task_id in _TASK_IDS_BY_STATE.items():
        lines.append(f"- [{_MARKER_BY_STATE[state]}] {task_id} — {state}")
    (queues_dir / "coder_backlog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_deferred_bug(project_dir: pathlib.Path) -> None:
    """Write one open, low-severity bug -- deferred under DEFER-LOW-BUGS.

    Shape follows team/templates/project/release/BUG-TEMPLATE.md's header
    fields. Severity ``low`` is the rung the DEFER-LOW-BUGS campaign (see
    ``_write_campaign_file``) defers; together the two files put the board
    in a genuinely deferred state rather than merely a low-severity one
    nothing is deferring.
    """
    bugs_dir = project_dir / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    (bugs_dir / f"{_BUG_ID}.md").write_text(
        f"""# {_BUG_ID}

**Bug ID:** {_BUG_ID}
**Filed By:** golden-pair fixture
**Date:** 2026-01-01
**Severity:** low

## Status
open

## Category
misc

---

## Symptom
Fixture-only placeholder bug used to exercise the deferred-bug shape a
golden-pair board must carry -- not a real defect.

## Expected
N/A (synthetic fixture data).

## Actual
N/A (synthetic fixture data).

## Reproduction
Steps to reproduce:

1. Build this fixture.
2. Read this file.

## Files Involved

- (none -- fixture-only)
""",
        encoding="utf-8",
    )


def _write_campaign_file(kanban_root: pathlib.Path, project: str) -> None:
    """Write a project-scoped DEFER-LOW-BUGS campaign file.

    Path and body shape follow team/scripts/lib/defer_posture.sh's own
    resolution: ``<kanban_root>/projects/<project>/DEFER-LOW-BUGS`` with a
    parseable ``until=vX.Y.Z`` line, which forces ``deferred_bugs=true`` and
    ``deferred_bug_level=low`` for this project -- exactly the posture that
    makes the low-severity bug above a *deferred* bug rather than merely a
    filed one.
    """
    campaign_file = kanban_root / "projects" / project / "DEFER-LOW-BUGS"
    campaign_file.parent.mkdir(parents=True, exist_ok=True)
    campaign_file.write_text(
        f"# golden-pair fixture campaign\nuntil={_CAMPAIGN_UNTIL_VERSION}\n",
        encoding="utf-8",
    )


def _link_real_scripts(kanban_root: pathlib.Path) -> None:
    """Symlink <kanban_root>/scripts to the real team/scripts/ tree.

    The production REST server entrypoint (pgai_agent_kanban.api.main) and
    the production CLI scripts both resolve their own location from
    PGAI_AGENT_KANBAN_ROOT_PATH; the entry-point law requires fixtures to
    run those real artifacts, never a test-only reimplementation. A symlink -- not a copy -- keeps this
    fixture a thin, fast pointer at the one real scripts/ tree rather than
    a second copy that could drift from it. This is a directory symlink,
    not a checked-out worktree, so the "copied worktree is not a sandbox"
    constraint (docs/test-authoring-guidelines.md, "Live-install safety")
    does not apply: nothing here is a git repository, and no git operation
    is ever run against it.
    """
    scripts_link = kanban_root / "scripts"
    if scripts_link.exists() or scripts_link.is_symlink():
        return
    scripts_link.symlink_to(_REAL_SCRIPTS_DIR, target_is_directory=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_synthetic_board(
    kanban_root: pathlib.Path,
    project: str = PROJECT_NAME,
    *,
    with_queues: bool = False,
) -> pathlib.Path:
    """Build a synthetic kanban board seeded for golden-pair parity fixtures.

    Args:
        kanban_root: Directory to build the board in. Caller is responsible
                     for choosing a path under the framework temp root (e.g.
                     pytest's ``tmp_path``); this function writes only
                     inside it, plus one symlink pointing outward at the
                     real ``team/scripts/`` tree (never written through).
        project:     Project name to register. Defaults to
                     ``PROJECT_NAME`` ("golden-fixture").
        with_queues: When True, also write
                     ``tasks/queues/coder_backlog.md`` listing every task
                     under the marker its state derives.  Off by default:
                     callers that seed their own queue files keep the
                     board exactly as they expect it.

    Returns:
        The *project* directory (``kanban_root / "projects" / project``) --
        the path most golden-pair callers need next to inspect or extend
        the board.

    Board shape (see module docstring for the full listing):
      - ``scripts/``            symlinked to the real ``team/scripts/`` tree.
      - ``projects.cfg``        registers *project* (priority 1, enabled).
      - ``projects/<project>/tasks/<id>/status.md``
                                one task per BACKLOG, WAITING, WORKING,
                                BLOCKED, DONE, WONT-DO.
      - ``projects/<project>/tasks/queues/coder_backlog.md``
                                (``with_queues=True`` only) every task
                                above, under its state's marker.
      - ``projects/<project>/bugs/<id>.md``
                                one open, low-severity bug.
      - ``projects/<project>/DEFER-LOW-BUGS``
                                an active deferral campaign covering that bug.
    """
    kanban_root.mkdir(parents=True, exist_ok=True)
    _link_real_scripts(kanban_root)
    _write_projects_cfg(kanban_root, project)

    project_dir = kanban_root / "projects" / project
    project_dir.mkdir(parents=True, exist_ok=True)

    _write_tasks(project_dir)
    if with_queues:
        _write_coder_queue(project_dir)
    _write_deferred_bug(project_dir)
    _write_campaign_file(kanban_root, project)

    return project_dir


def task_ids_by_state() -> dict[str, str]:
    """Return a copy of the state -> task-id mapping ``build_synthetic_board`` writes.

    Exposed so callers can assert against a specific task id without
    duplicating the naming scheme.
    """
    return dict(_TASK_IDS_BY_STATE)
