"""
test_reliability_streaks_pipeline.py
====================================
End-to-end coverage for the two reliability streaks (Hands-off: since the
last block a human had to clear; Clean: since the last block of any kind),
driven through the production entry points that block a task, triage it and
release it, and read back from the metrics/reliability.json those entry
points refresh on every ledger append.

Pipeline under test, per scenario:

  1. a seeded history -- four shipped RCs whose rollups come from the real
     aggregator (scripts/lib/metrics_aggregator.py aggregate_rc), and whose
     ledger lines (one chain HALT a human cleared, two operator HALTs) are
     written through the ledger's own writers before the scenario starts,
     then refreshed once through the RC-close entry point
     (``python3 -m pgai_agent_kanban.metrics.reliability refresh``);
  2. a task block, through a real block entry point;
  3. OVERWATCH block triage, through
     ``python3 -m pgai_agent_kanban.overwatch.block_triage apply``;
  4. for the escalated block, the operator release through scripts/reset.sh.

Scenarios:

  - Requeue: the wake's Block Record writer
    (``python3 -m pgai_agent_kanban.overwatch.block_record write``, the call
    the zero-progress ceiling makes after it moves the task to BLOCKED)
    records a zero_progress block; triage acts with a requeue to the owner.
    Clean resets to 0 RCs while Hands-off keeps its 3.
  - Escalation: the status-writer tool (scripts/task-status.sh) blocks the
    task with agent_unclassified, whose retry ladder is then marked spent
    (``block_record update --attempts 3``); triage escalates it; reset.sh
    releases it.
    Both streaks read 0 RCs, and the last human-class block names the task
    and its reason_code.

Both scenarios assert the seeded RCs' per-RC blocked minutes -- read through
the ledger's blocked_minutes() and re-aggregated through aggregate_rc -- are
unchanged by the scenario.

Clocks: the seeded history and the Block Record write use fixed timestamps,
and so does the requeue scenario's triage (``--now``). The status-writer
tool and reset.sh take no clock argument and stamp the ledger with the
current time, so the escalation scenario runs on wall-clock time; its triage
is given the current time explicitly so the escalation never predates the
block. reliability.json's hours are measured to the current time at each
refresh, so only RC counts, statuses and block details are asserted.

Every subprocess runs in the five-variable sandbox (KANBAN_ROOT and
PGAI_AGENT_KANBAN_ROOT_PATH on the fixture root, PGAI_AGENT_KANBAN_TEMP_DIR
inside it, PGAI_TASKS_DIR and PGAI_PROJECT_ROOT cleared); the fixture root
lives under pytest's tmp_path. No real provider, no git repository, no
sleeps.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.two_project import PROJECT_A, build_two_project_root

# ---------------------------------------------------------------------------
# Paths -- anchored to this file, so the tree under test finds itself.
#   team/tests/integration/test_reliability_streaks_pipeline.py
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"
_TASK_STATUS_SH = _SCRIPTS_DIR / "task-status.sh"
_RESET_SH = _SCRIPTS_DIR / "reset.sh"

for _p in (str(_LIB_DIR), str(_TEAM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metrics_aggregator as ma  # noqa: E402
from pgai_agent_kanban.metrics.blocked_ledger import (  # noqa: E402
    append_block_end,
    append_block_start,
    block_intervals,
    blocked_minutes,
    ledger_path_for_project,
)

_TIMEOUT_SECONDS = 60
_PROJECT = PROJECT_A
_ACTIVE_RC = "v2.4.0"

_REQUEUED_TASK = "CODER-20260305-001-example-requeued-task"
_ESCALATED_TASK = "CODER-20260305-002-example-escalated-task"

# The seeded history: four shipped RCs, oldest first. Each entry is
# (version, opened_at, closed_at, blocks, expected blocked minutes), and each
# block is (source, start, end, start fields, end fields) for the ledger's
# own writers. v2.0.0 carries the last block a human cleared (a chain HALT
# released by unhalt); v2.1.0 and v2.2.0 carry operator HALTs only, which
# drive blocked minutes but never a streak; v2.3.0 was never blocked.
_HUMAN_HALT_CLEARED_AT = "2026-03-01T02:30:00+00:00"
_SEEDED_RCS = (
    (
        "v2.0.0",
        "2026-03-01T00:00:00+00:00",
        "2026-03-01T12:00:00+00:00",
        (
            (
                "halt:project",
                "2026-03-01T02:00:00+00:00",
                _HUMAN_HALT_CLEARED_AT,
                {"block_class": "any", "cause": "chain", "reason_code": "release_trigger"},
                {"block_class": "human", "cause": "chain", "reason_code": "unhalt"},
            ),
        ),
        30,
    ),
    (
        "v2.1.0",
        "2026-03-02T00:00:00+00:00",
        "2026-03-02T12:00:00+00:00",
        (
            (
                "halt:project",
                "2026-03-02T03:00:00+00:00",
                "2026-03-02T03:40:00+00:00",
                {"cause": "operator"},
                {"block_class": "any", "cause": "operator", "reason_code": "unhalt"},
            ),
        ),
        40,
    ),
    (
        "v2.2.0",
        "2026-03-03T00:00:00+00:00",
        "2026-03-03T12:00:00+00:00",
        (
            (
                "halt:project",
                "2026-03-03T05:00:00+00:00",
                "2026-03-03T05:25:00+00:00",
                {"cause": "operator"},
                {"block_class": "any", "cause": "operator", "reason_code": "unhalt"},
            ),
        ),
        25,
    ),
    ("v2.3.0", "2026-03-04T00:00:00+00:00", "2026-03-04T12:00:00+00:00", (), 0),
)
#: Shipped RCs after the human-cleared HALT, its own RC excluded.
_RCS_SINCE_HUMAN_BLOCK = 3

# The requeue scenario's fixed clock: blocked after every seeded RC closed,
# triaged two hours later.
_REQUEUE_BLOCKED_AT = "2026-03-05T10:00:00+00:00"
_REQUEUE_TRIAGED_AT = "2026-03-05T12:00:00+00:00"
_HISTORY_REFRESHED_AT = "2026-03-05T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def _sandbox_env(root: pathlib.Path) -> dict[str, str]:
    """The inherited environment with the five live-install names set or
    cleared (projects/pgai-agent-kanban/README.md, "Live-install safety"),
    plus the project identity and dev-tree names a wake would export."""
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME", "PGAI_DEV_TREE_PATH"):
        env.pop(name, None)
    env["KANBAN_ROOT"] = str(root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(root / "tmp")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(_TEAM_DIR) + (os.pathsep + existing if existing else "")
    env["NO_COLOR"] = "1"
    return env


#: The prefixes a best-effort ledger stamp warns with when it fails; the
#: entry point still exits 0, so the warning is the only sign of a lost stamp.
_LEDGER_WARNINGS = ("[blocked-ledger] WARNING", "[block-triage] WARNING")


def _run(root: pathlib.Path, argv: list[str]) -> subprocess.CompletedProcess:
    """Run *argv* in the sandbox; a non-zero exit, or a ledger-stamp warning
    on stderr, fails the test with both output streams."""
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=_sandbox_env(root),
        cwd=str(root),
        timeout=_TIMEOUT_SECONDS,
    )
    detail = f"{' '.join(argv)} exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.returncode == 0, detail
    assert not any(prefix in result.stderr for prefix in _LEDGER_WARNINGS), detail
    return result


def _run_module(root: pathlib.Path, module: str, *args: str) -> subprocess.CompletedProcess:
    return _run(root, [sys.executable, "-m", module, *args])


class Project:
    """The fixture project: paths and the reads the assertions need."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.dir = root / "projects" / _PROJECT
        self.ledger = pathlib.Path(ledger_path_for_project(str(root), _PROJECT))
        self.reliability_file = self.dir / "metrics" / "reliability.json"
        self.queue = self.dir / "tasks" / "queues" / "coder_backlog.md"

    def task_dir(self, task_id: str) -> pathlib.Path:
        return self.dir / "tasks" / task_id

    def status(self, task_id: str) -> pathlib.Path:
        return self.task_dir(task_id) / "status.md"

    def reliability(self) -> dict:
        return json.loads(self.reliability_file.read_text(encoding="utf-8"))

    def blocks_of(self, source: str) -> list[dict]:
        return [b for b in block_intervals(str(self.ledger)) if b["source"] == source]

    def seeded_blocked_minutes(self) -> dict[str, float]:
        """Each seeded RC's blocked minutes through the ledger's own reader,
        clamped to that RC's window."""
        return {
            version: blocked_minutes(str(self.ledger), version, opened_at, closed_at)
            for version, opened_at, closed_at, _blocks, _expected in _SEEDED_RCS
        }

    def aggregated_blocked_minutes(self) -> dict[str, object]:
        """Each seeded RC re-aggregated through aggregate_rc; the rollup's
        blocked_minutes."""
        minutes = {}
        for version, *_rest in _SEEDED_RCS:
            rollup = ma.aggregate_rc(_PROJECT, version, kanban_root=self.root)
            minutes[version] = json.loads(rollup.read_text(encoding="utf-8"))["blocked_minutes"]
        return minutes


def _write_release_state(project: Project) -> None:
    (project.dir / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{_ACTIVE_RC}\n\n## RC Opened At\n2026-03-05T00:00:00+00:00\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv2.3.0\n",
        encoding="utf-8",
    )


def _seed_history(project: Project) -> None:
    """The history before the scenario: ledger lines through the ledger's
    writers, then per-RC rollups through the aggregator, then one refresh
    through the RC-close entry point."""
    state_dir = project.dir / "release-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    ledger = str(project.ledger)
    for version, opened_at, closed_at, blocks, _expected in _SEEDED_RCS:
        for source, start, end, start_fields, end_fields in blocks:
            append_block_start(ledger, version, source, start, **start_fields)
            append_block_end(ledger, version, source, end, **end_fields)
        (state_dir / f"{version}.json").write_text(
            json.dumps({"opened_at": opened_at, "closed_at": closed_at, "outcome": "shipped"}),
            encoding="utf-8",
        )
        ma.aggregate_rc(_PROJECT, version, kanban_root=project.root)
    _run_module(
        project.root,
        "pgai_agent_kanban.metrics.reliability",
        "refresh",
        "--project",
        _PROJECT,
        "--kanban-root",
        str(project.root),
        "--now",
        _HISTORY_REFRESHED_AT,
    )


def _add_task(project: Project, task_id: str, *, state: str, marker: str) -> pathlib.Path:
    """A CODER task folder in the shape the materializer writes, and its
    queue line."""
    task_dir = project.task_dir(task_id)
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "logs").mkdir()
    (task_dir / "README.md").write_text(
        f"# Task: example\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n\n## Source Branch\nai_rc/{_ACTIVE_RC}\n\n"
        f"## Release Version\n{_ACTIVE_RC}\n\n## Goal\nExample task for the reliability pipeline.\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## Participant\nClaude\n\n## Role\nCODER\n\n## Model\nexample-model\n\n"
        f"## State\n{state}\n\n## Summary\nWorking.\n\n## Artifacts\nnone\n\n## Blockers\nnone\n\n"
        "## Needs Human\nno\n\n## Next Recommended Step\nnone\n",
        encoding="utf-8",
    )
    with project.queue.open("a", encoding="utf-8") as handle:
        handle.write(f"- [{marker}] {task_id} — example\n")
    return task_dir / "status.md"


def _triage_apply(project: Project, task_id: str, now: str) -> list[str]:
    """``block_triage apply`` for one task; the printed TSV result."""
    result = _run_module(
        project.root,
        "pgai_agent_kanban.overwatch.block_triage",
        "apply",
        "--kanban-root",
        str(project.root),
        "--project",
        _PROJECT,
        "--task-id",
        task_id,
        "--queue-file",
        str(project.queue),
        "--now",
        now,
    )
    return result.stdout.strip().split("\t")


@pytest.fixture
def project(tmp_path: pathlib.Path) -> Project:
    """A sandbox kanban root whose project has the seeded history and an
    open RC, with reliability.json refreshed over that history."""
    root = build_two_project_root(tmp_path)
    (root / "tmp").mkdir()
    proj = Project(root)
    _write_release_state(proj)
    _seed_history(proj)
    return proj


def _assert_seeded_minutes_unchanged(project: Project, before: dict, before_rollups: dict) -> None:
    expected = {version: float(minutes) for version, *_rest, minutes in _SEEDED_RCS}
    assert before == expected
    assert project.seeded_blocked_minutes() == expected
    assert project.aggregated_blocked_minutes() == before_rollups
    assert before_rollups == {version: int(minutes) for version, minutes in expected.items()}


# ---------------------------------------------------------------------------
# The seeded history on its own
# ---------------------------------------------------------------------------


def test_seeded_history_reads_both_streaks_since_the_human_cleared_halt(project: Project) -> None:
    """Before any scenario, both streaks run from the last block of the
    seeded history -- the chain HALT a human cleared -- and the two
    operator HALTs after it count toward neither."""
    doc = project.reliability()
    for streak in ("hands_off", "clean"):
        assert doc[streak]["status"] == "resolved", doc
        assert doc[streak]["rcs"] == _RCS_SINCE_HUMAN_BLOCK, doc
        assert doc[streak]["since"] == _HUMAN_HALT_CLEARED_AT, doc
        assert doc[streak]["last_block"]["source"] == "halt:project", doc


# ---------------------------------------------------------------------------
# Scenario one: a requeue resets Clean only
# ---------------------------------------------------------------------------


def test_triage_requeue_resets_clean_while_hands_off_keeps_its_count(project: Project) -> None:
    """A zero-progress block the wake records, then requeued by triage
    (``act``): Clean reads 0 RCs since the requeue, Hands-off still reads
    the 3 RCs since the human-cleared HALT, and the seeded RCs' blocked
    minutes are unchanged."""
    minutes_before = project.seeded_blocked_minutes()
    rollups_before = project.aggregated_blocked_minutes()

    # The zero-progress ceiling moves the task to BLOCKED with Needs Human
    # yes (the wake's bash transition, which stamps no ledger line), then
    # writes the Block Record through the module CLI -- the write that
    # opens the block on the ledger.
    status = _add_task(project, _REQUEUED_TASK, state="BLOCKED", marker="B")
    text = status.read_text(encoding="utf-8").replace("## Needs Human\nno", "## Needs Human\nyes")
    status.write_text(text, encoding="utf-8")
    _run_module(
        project.root,
        "pgai_agent_kanban.overwatch.block_record",
        "write",
        str(status),
        "zero_progress",
        "CODER",
        "--now",
        _REQUEUE_BLOCKED_AT,
    )
    (opened,) = project.blocks_of(f"task:{_REQUEUED_TASK}")
    assert opened["end"] is None and opened["reason_code"] == "zero_progress"
    assert project.reliability()["clean"]["status"] == "open"

    outcome, reason_code, action, mutation, *_detail = _triage_apply(project, _REQUEUED_TASK, _REQUEUE_TRIAGED_AT)
    assert (outcome, reason_code, action, mutation) == ("act", "zero_progress", "requeue_owner", "promote")

    (block,) = project.blocks_of(f"task:{_REQUEUED_TASK}")
    assert block["version"] == _ACTIVE_RC
    assert block["class"] == "any" and not block["escalated"]
    assert (block["start"], block["end"]) == (_REQUEUE_BLOCKED_AT, _REQUEUE_TRIAGED_AT)
    assert (block["resolution_class"], block["resolution_reason_code"]) == ("any", "requeue")

    doc = project.reliability()
    clean, hands_off = doc["clean"], doc["hands_off"]
    assert clean["status"] == "resolved", doc
    assert clean["rcs"] == 0, doc
    assert clean["since"] == _REQUEUE_TRIAGED_AT, doc
    assert clean["last_block"]["task"] == _REQUEUED_TASK, doc
    assert clean["last_block"]["reason_code"] == "zero_progress", doc
    assert clean["last_block"]["resolution"] == {
        "time": _REQUEUE_TRIAGED_AT,
        "class": "any",
        "reason_code": "requeue",
    }, doc
    assert hands_off["status"] == "resolved", doc
    assert hands_off["rcs"] == _RCS_SINCE_HUMAN_BLOCK, doc
    assert hands_off["since"] == _HUMAN_HALT_CLEARED_AT, doc
    assert hands_off["last_block"]["source"] == "halt:project", doc

    _assert_seeded_minutes_unchanged(project, minutes_before, rollups_before)


# ---------------------------------------------------------------------------
# Scenario two: an escalation released by reset.sh resets both
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def test_escalation_released_by_reset_resets_both_streaks(project: Project) -> None:
    """An agent self-block through the status-writer tool, escalated by
    triage and released with reset.sh: Hands-off and Clean both read 0 RCs,
    the last human-class block names the task and its reason_code, and the
    seeded RCs' blocked minutes are unchanged."""
    minutes_before = project.seeded_blocked_minutes()
    rollups_before = project.aggregated_blocked_minutes()
    source = f"task:{_ESCALATED_TASK}"

    _add_task(project, _ESCALATED_TASK, state="WORKING", marker="A")
    _run(
        project.root,
        [
            "bash",
            str(_TASK_STATUS_SH),
            "--project",
            _PROJECT,
            "--key",
            _ESCALATED_TASK,
            "--state",
            "BLOCKED",
            "--needs-human",
            "yes",
            "--reason-code",
            "agent_unclassified",
            "--blockers",
            "the brief names a file that does not exist",
        ],
    )
    # The wake's reconciliation renders the BLOCKED state as the [B] marker.
    project.queue.write_text(
        project.queue.read_text(encoding="utf-8").replace(f"- [A] {_ESCALATED_TASK}", f"- [B] {_ESCALATED_TASK}"),
        encoding="utf-8",
    )
    (opened,) = project.blocks_of(source)
    assert opened["end"] is None and opened["reason_code"] == "agent_unclassified"
    # The row's retry ladder (three requeues) is already spent, so triage escalates.
    _run_module(
        project.root,
        "pgai_agent_kanban.overwatch.block_record",
        "update",
        str(project.status(_ESCALATED_TASK)),
        "--attempts",
        "3",
    )

    outcome, reason_code, action, mutation, *_detail = _triage_apply(project, _ESCALATED_TASK, _utc_now())
    assert (outcome, reason_code, action, mutation) == ("escalate", "agent_unclassified", "escalate", "none")
    (escalated,) = project.blocks_of(source)
    assert escalated["escalated"] and escalated["class"] == "human" and escalated["end"] is None
    assert project.reliability()["hands_off"]["status"] == "open"

    _run(project.root, ["bash", str(_RESET_SH), "--project", _PROJECT, "--key", _ESCALATED_TASK])

    (block,) = project.blocks_of(source)
    assert block["version"] == _ACTIVE_RC and block["end"] is not None
    assert (block["resolution_class"], block["resolution_reason_code"]) == ("human", "reset")

    doc = project.reliability()
    for streak in ("hands_off", "clean"):
        assert doc[streak]["status"] == "resolved", doc
        assert doc[streak]["rcs"] == 0, doc
        assert doc[streak]["since"] == block["end"], doc
    last_human = doc["hands_off"]["last_block"]
    assert last_human["task"] == _ESCALATED_TASK, doc
    assert last_human["reason_code"] == "agent_unclassified", doc
    assert last_human["class"] == "human" and last_human["escalated"], doc
    assert last_human["resolution"]["class"] == "human", doc
    assert last_human["resolution"]["reason_code"] == "reset", doc
    assert doc["clean"]["last_block"]["task"] == _ESCALATED_TASK, doc

    _assert_seeded_minutes_unchanged(project, minutes_before, rollups_before)
