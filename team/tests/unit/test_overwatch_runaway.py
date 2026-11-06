"""
test_overwatch_runaway.py
=========================
Contract tests for ``pgai_agent_kanban.overwatch.runaway``, the runaway
detector: the one path by which OVERWATCH stops a project.

Each counter is driven from the durable records the detector reads (per-RC
release-state JSON, archived cancelled RCs, task Block Records, the operator
inbox) in a throwaway kanban root with two projects, and the test asserts on
what lands on disk: exactly one project-scoped chain HALT that names the
counter, its threshold and its last events (classified as the chain's by
``halt_provenance``), exactly one inbox entry, and nothing at all below each
threshold. The configuration keys are checked against the shipped
``kanban.cfg_example`` and through project.cfg / kanban.cfg overrides, and
the module's own command line is run by file path in a scrubbed
environment.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox, runaway
from pgai_agent_kanban.overwatch.block_record import update_block_record, write_block_record
from pgai_agent_kanban.overwatch.halt_provenance import (
    ORIGIN_CHAIN,
    WRITER_OVERWATCH_RUNAWAY,
    classify_halt,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_KANBAN_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"
_MODULE_FILE = pathlib.Path(runaway.__file__).resolve()

PROJECT = "demo"
OTHER = "sibling"

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A throwaway kanban root with two project directories; every root variable points at it."""
    kanban_root = tmp_path / "kanban"
    for name in (PROJECT, OTHER):
        (kanban_root / "projects" / name / "tasks").mkdir(parents=True)
    (kanban_root / "tmp").mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))
    monkeypatch.setenv("KANBAN_ROOT", str(kanban_root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(kanban_root / "tmp"))
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    return kanban_root


@pytest.fixture
def ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _iso(moment: datetime.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ship(root: pathlib.Path, version: str, closed: datetime.datetime, project: str = PROJECT) -> None:
    """A per-RC record that shipped, the shape the release step leaves."""
    state_dir = root / "projects" / project / "release-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "attempt": 1,
        "cargo": ["BUG-0001-example.md"],
        "closed_at": _iso(closed),
        "family_key": "family-0000",
        "opened_at": _iso(closed - datetime.timedelta(minutes=5)),
        "outcome": "shipped",
        "rc": version,
    }
    (state_dir / f"{version}.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def _cancel(root: pathlib.Path, version: str, closed: datetime.datetime) -> None:
    """A cancelled RC archived to history/, the shape cancel-rc leaves."""
    history = root / "projects" / PROJECT / "release-state" / "history"
    history.mkdir(parents=True, exist_ok=True)
    record = {
        "attempt": 1,
        "cargo": ["BUG-0001-example.md"],
        "closed_at": _iso(closed),
        "family_key": "family-0000",
        "opened_at": _iso(closed - datetime.timedelta(minutes=30)),
        "outcome": "cancelled",
        "rc": version,
    }
    stamp = _iso(closed).replace(":", "-").replace("T", "-")
    (history / f"{version}-cancelled-{stamp}.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def _status_file(root: pathlib.Path, task_id: str) -> pathlib.Path:
    task_dir = root / "projects" / PROJECT / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status = task_dir / "status.md"
    if not status.exists():
        status.write_text(
            f"# Status\n\n## Task\n{task_id}\n\n## State\nBLOCKED\n\n## Summary\nblocked\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n"
        )
    return status


def _blocks(ctx: OpsContext, root: pathlib.Path, task_id: str, reason: str, times: list) -> None:
    """Block *task_id* once per time in *times*, observing after each block as the sweep would."""
    status = _status_file(root, task_id)
    for moment in times:
        write_block_record(status, reason, "CODER", now=moment)
        runaway.observe(ctx, PROJECT, now=moment)


def _inbox_entries(ctx: OpsContext, count: int, start: datetime.datetime) -> None:
    for n in range(count):
        inbox.write_entry(
            ctx,
            PROJECT,
            source=f"CODER-20260301-{n:03d}-example",
            question=f"Question {n}?",
            meanwhile="Carried on.",
            how_to_answer="Answer with the inbox command.",
            now=start + datetime.timedelta(minutes=n),
        )


def _halt(root: pathlib.Path, project: str = PROJECT) -> pathlib.Path:
    return root / "projects" / project / "HALT"


def _all_entries(ctx: OpsContext, project: str = PROJECT) -> tuple:
    box = inbox.read_inbox(ctx, project)
    return box.open + box.done


def _assert_one_stop(
    ctx: OpsContext, root: pathlib.Path, result, counter: str, threshold: int, events: int, inbox_before: int = 0
) -> str:
    """The result tripped *counter*: one chain HALT naming it and its events, one new inbox entry."""
    assert result.outcome == runaway.OUTCOME_TRIPPED, result
    assert [t.counter for t in result.trips] == [counter]
    halt = _halt(root)
    assert result.halt_path == str(halt)
    body = halt.read_text()
    assert f"# runaway-counter: {counter}\n" in body
    assert f"# runaway-threshold: {threshold}\n" in body
    assert f"# runaway-count: {events}\n" in body
    assert body.count("# runaway-event: ") == min(events, runaway.EVIDENCE_EVENTS)
    assert f"# error:   runaway.{counter}\n" in body
    provenance = classify_halt(halt, kanban_root=root)
    assert provenance.origin == ORIGIN_CHAIN, provenance
    assert provenance.writer == WRITER_OVERWATCH_RUNAWAY
    assert counter in (provenance.reason or "")
    # Only this project stops: no sibling HALT, no fleet-scoped HALT.
    assert not _halt(root, OTHER).exists()
    assert not (root / "HALT").exists()
    entries = _all_entries(ctx)
    assert len(entries) == inbox_before + 1
    assert result.inbox_entry == entries[-1].entry_id
    assert counter in entries[-1].question
    return body


# ---------------------------------------------------------------------------
# Each counter trips over its default threshold
# ---------------------------------------------------------------------------


def test_six_releases_in_one_hour_halt_the_project_once(ctx, root):
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))
    result = runaway.run(ctx, PROJECT, now=NOW)
    body = _assert_one_stop(ctx, root, result, runaway.COUNTER_RELEASES, 5, 6)
    for n in range(6):
        assert f"release:v1.0.{n} shipped v1.0.{n}" in body

    again = runaway.run(ctx, PROJECT, now=NOW + datetime.timedelta(minutes=1))
    assert again.outcome == runaway.OUTCOME_CLEAR
    assert _halt(root).read_text() == body
    assert len(_all_entries(ctx)) == 1


def test_three_rc_open_cancel_cycles_in_a_day_halt_the_project_once(ctx, root):
    for n in range(3):
        _cancel(root, f"v2.0.{n}", NOW - datetime.timedelta(hours=6 * n + 1))
    result = runaway.run(ctx, PROJECT, now=NOW)
    body = _assert_one_stop(ctx, root, result, runaway.COUNTER_RC_CYCLES, 2, 3)
    assert "cancelled v2.0.2" in body
    assert _all_entries(ctx)[-1].source == "rc:v2.0.0"


def test_same_reason_code_four_times_on_one_task_halts_the_project_once(ctx, root):
    task = "CODER-20260301-001-example"
    times = [NOW - datetime.timedelta(hours=h) for h in (8, 6, 4, 2)]
    _blocks(ctx, root, task, "per_task_timeout", times)
    result = runaway.run(ctx, PROJECT, now=NOW)
    body = _assert_one_stop(ctx, root, result, runaway.COUNTER_REASON_REPEATS, 3, 4)
    assert f"# runaway-subject: {task}:per_task_timeout\n" in body
    assert body.count(f"{task} blocked: per_task_timeout") == 4
    assert _all_entries(ctx)[-1].source == task


def test_inbox_entries_over_their_threshold_halt_the_project_once(ctx, root):
    _inbox_entries(ctx, 21, NOW - datetime.timedelta(hours=3))
    result = runaway.run(ctx, PROJECT, now=NOW)
    _assert_one_stop(ctx, root, result, runaway.COUNTER_INBOX_ENTRIES, 20, 21, inbox_before=21)


# ---------------------------------------------------------------------------
# Nothing below each threshold
# ---------------------------------------------------------------------------


def _five_releases(ctx, root):
    for n in range(5):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))


def _two_cycles(ctx, root):
    for n in range(2):
        _cancel(root, f"v2.0.{n}", NOW - datetime.timedelta(hours=n + 1))


def _three_repeats(ctx, root):
    _blocks(
        ctx,
        root,
        "CODER-20260301-001-example",
        "per_task_timeout",
        [NOW - datetime.timedelta(hours=h) for h in (6, 4, 2)],
    )


def _twenty_inbox_entries(ctx, root):
    _inbox_entries(ctx, 20, NOW - datetime.timedelta(hours=3))


@pytest.mark.parametrize(
    "fill",
    [_five_releases, _two_cycles, _three_repeats, _twenty_inbox_entries],
    ids=["releases", "rc-cycles", "reason-repeats", "inbox-entries"],
)
def test_a_counter_at_its_threshold_writes_no_halt_and_no_inbox_entry(ctx, root, fill):
    fill(ctx, root)
    before = len(_all_entries(ctx))
    result = runaway.run(ctx, PROJECT, now=NOW)
    assert result.outcome == runaway.OUTCOME_CLEAR, result
    assert result.trips == ()
    assert not _halt(root).exists()
    assert len(_all_entries(ctx)) == before


def test_releases_spread_past_the_window_do_not_trip(ctx, root):
    for n in range(8):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=15 * n))
    assert runaway.run(ctx, PROJECT, now=NOW).outcome == runaway.OUTCOME_CLEAR
    assert not _halt(root).exists()


def _status_count(root: pathlib.Path, now: datetime.datetime, capsys: pytest.CaptureFixture) -> int:
    """The reason_repeats count the ``status`` command prints."""
    capsys.readouterr()
    assert runaway.main(["status", "--project", PROJECT, "--kanban-root", str(root), "--now", _iso(now), "--json"]) == 0
    (row,) = [r for r in json.loads(capsys.readouterr().out) if r["counter"] == runaway.COUNTER_REASON_REPEATS]
    return row["count"]


def test_blocks_that_follow_a_triage_requeue_are_not_counted_until_the_requeues_stop(ctx, root, capsys):
    task = "CODER-20260301-004-example"
    status = _status_file(root, task)
    start = NOW - datetime.timedelta(days=1)

    def block(hours: int) -> datetime.datetime:
        moment = start + datetime.timedelta(hours=hours)
        write_block_record(status, "agent_unclassified", "CODER", now=moment)
        runaway.observe(ctx, PROJECT, now=moment)
        return moment

    # A three-requeue ladder: four blocks, three of them after a triage requeue.
    moment = block(0)
    for hours in (1, 2, 3):
        update_block_record(status, bump_attempts=True, record_requeue="agent_unclassified")
        moment = block(hours)
    assert runaway.run(ctx, PROJECT, now=moment).outcome == runaway.OUTCOME_CLEAR
    assert _status_count(root, moment, capsys) == 1

    # Triage requeues no more: every further block counts, and the third trips.
    for hours in (4, 5):
        moment = block(hours)
        assert runaway.run(ctx, PROJECT, now=moment).outcome == runaway.OUTCOME_CLEAR
    moment = block(6)
    result = runaway.run(ctx, PROJECT, now=moment)
    body = _assert_one_stop(ctx, root, result, runaway.COUNTER_REASON_REPEATS, 3, 4)
    assert f"# runaway-subject: {task}:agent_unclassified\n" in body
    stored = json.loads(runaway.counters_path(ctx, PROJECT).read_text())["events"]
    assert [e["requeues"] for e in stored if e["counter"] == runaway.COUNTER_REASON_REPEATS] == [0, 1, 2, 3, 3, 3, 3]


def test_a_requeue_before_a_block_with_another_reason_code_still_exempts_it(ctx, root):
    task = "CODER-20260301-005-example"
    status = _status_file(root, task)
    times = [NOW - datetime.timedelta(hours=h) for h in (8, 6, 4, 2)]
    write_block_record(status, "per_task_timeout", "CODER", now=times[0])
    runaway.observe(ctx, PROJECT, now=times[0])
    for moment in times[1:]:
        update_block_record(status, bump_attempts=True, record_requeue="per_task_timeout")
        write_block_record(status, "agent_unclassified", "CODER", now=moment)
        runaway.observe(ctx, PROJECT, now=moment)
    # Three agent_unclassified blocks, each after a requeue: none counted.
    assert runaway.evaluate(ctx, PROJECT, now=NOW, thresholds={runaway.COUNTER_REASON_REPEATS: 1}) == ()


def test_record_event_counts_a_block_unless_its_requeues_rose_since_the_last(ctx, root):
    task = "CODER-20260301-006-example"
    for h in (8, 6, 4, 2):
        assert runaway.record_event(
            ctx,
            PROJECT,
            runaway.COUNTER_REASON_REPEATS,
            key="",
            at=NOW - datetime.timedelta(hours=h),
            subject=task,
            detail="agent_unclassified",
        )
    (trip,) = runaway.evaluate(ctx, PROJECT, now=NOW)
    assert trip.count == 4

    other = "CODER-20260301-007-example"
    for n, h in enumerate((8, 6, 4, 2)):
        runaway.record_event(
            ctx,
            PROJECT,
            runaway.COUNTER_REASON_REPEATS,
            key="",
            at=NOW - datetime.timedelta(hours=h),
            subject=other,
            detail="agent_unclassified",
            requeues=n,
        )
    assert [t.subject for t in runaway.evaluate(ctx, PROJECT, now=NOW)] == [f"{task}:agent_unclassified"]


def test_different_reason_codes_on_one_task_are_counted_apart(ctx, root):
    task = "CODER-20260301-002-example"
    _blocks(ctx, root, task, "per_task_timeout", [NOW - datetime.timedelta(hours=h) for h in (8, 6)])
    _blocks(ctx, root, task, "worktree_create_failed", [NOW - datetime.timedelta(hours=h) for h in (4, 2)])
    assert runaway.run(ctx, PROJECT, now=NOW).outcome == runaway.OUTCOME_CLEAR


def test_releases_of_another_project_do_not_count(ctx, root):
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10), project=OTHER)
    assert runaway.run(ctx, PROJECT, now=NOW).outcome == runaway.OUTCOME_CLEAR
    assert runaway.run(ctx, OTHER, now=NOW).outcome == runaway.OUTCOME_TRIPPED
    assert not _halt(root).exists()
    assert _halt(root, OTHER).exists()


# ---------------------------------------------------------------------------
# Persistence, the trip latch, an existing HALT
# ---------------------------------------------------------------------------


def test_counters_persist_under_the_project_overwatch_directory_without_duplicates(ctx, root):
    for n in range(3):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=10 * n))
    assert runaway.observe(ctx, PROJECT, now=NOW) == 3
    assert runaway.observe(ctx, PROJECT, now=NOW) == 0
    path = root / "projects" / PROJECT / "overwatch" / "runaway-counters.json"
    assert runaway.counters_path(ctx, PROJECT) == path
    events = json.loads(path.read_text())["events"]
    assert sorted(e["key"] for e in events) == ["release:v1.0.0", "release:v1.0.1", "release:v1.0.2"]


def test_record_event_feeds_a_block_once_even_when_the_record_is_observed_too(ctx, root):
    task = "CODER-20260301-003-example"
    status = _status_file(root, task)
    moment = NOW - datetime.timedelta(hours=1)
    write_block_record(status, "per_task_timeout", "CODER", now=moment)
    assert runaway.record_event(
        ctx, PROJECT, runaway.COUNTER_REASON_REPEATS, key="", at=moment, subject=task, detail="per_task_timeout"
    )
    assert runaway.observe(ctx, PROJECT, now=NOW) == 0


def test_after_a_trip_only_newer_events_count_again(ctx, root):
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))
    assert runaway.run(ctx, PROJECT, now=NOW).outcome == runaway.OUTCOME_TRIPPED
    _halt(root).unlink()  # the operator resumes the project

    later = NOW + datetime.timedelta(minutes=5)
    assert runaway.run(ctx, PROJECT, now=later).outcome == runaway.OUTCOME_CLEAR
    assert not _halt(root).exists()

    for n in range(6):
        _ship(root, f"v1.1.{n}", later + datetime.timedelta(minutes=n))
    tripped = runaway.run(ctx, PROJECT, now=later + datetime.timedelta(minutes=10))
    assert tripped.outcome == runaway.OUTCOME_TRIPPED
    assert tripped.trips[0].count == 6
    assert len(_all_entries(ctx)) == 2


def test_an_existing_operator_halt_is_left_untouched(ctx, root):
    _halt(root).write_text("")
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))
    result = runaway.run(ctx, PROJECT, now=NOW)
    assert result.outcome == runaway.OUTCOME_ALREADY_HALTED
    assert [t.counter for t in result.trips] == [runaway.COUNTER_RELEASES]
    assert _halt(root).read_text() == ""
    assert _all_entries(ctx) == ()
    assert json.loads(runaway.counters_path(ctx, PROJECT).read_text())["trips"] == []


def test_the_detector_does_not_count_its_own_inbox_entries(ctx, root):
    _inbox_entries(ctx, 20, NOW - datetime.timedelta(hours=3))
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))
    assert runaway.run(ctx, PROJECT, now=NOW).outcome == runaway.OUTCOME_TRIPPED
    _halt(root).unlink()
    # 21 entries now exist, one of them the detector's own: still at the threshold.
    assert len(_all_entries(ctx)) == 21
    assert runaway.run(ctx, PROJECT, now=NOW + datetime.timedelta(minutes=1)).outcome == runaway.OUTCOME_CLEAR


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_NEW_KEYS = {
    "runaway_releases_per_hour": "5",
    "runaway_rc_cycles_per_day": "2",
    "runaway_reason_repeats": "3",
    "runaway_inbox_entries_per_day": "20",
    "halt_after_auto_resume": "false",
}


@pytest.mark.parametrize("key,value", sorted(_NEW_KEYS.items()))
def test_kanban_cfg_example_documents_each_key_at_its_default(key, value):
    assert ini.read_line_value(_KANBAN_CFG_EXAMPLE, "overwatch", key) == value
    text = _KANBAN_CFG_EXAMPLE.read_text()
    block = text[: text.index(f"\n{key} = ")].rsplit("\n\n", 1)[-1]
    assert "# Accepted:" in block and "Default:" in block, block


def test_module_defaults_match_the_shipped_example():
    for spec in runaway.COUNTERS:
        assert str(spec.default) == _NEW_KEYS[spec.cfg_key]
    assert str(runaway.DEFAULT_HALT_AFTER_AUTO_RESUME).lower() == _NEW_KEYS["halt_after_auto_resume"]


@pytest.mark.parametrize("text", ["", "[overwatch]\n", "[overwatch]\nrunaway_releases_per_hour =\n"])
def test_absent_or_blank_keys_resolve_to_their_defaults(root, text):
    (root / "kanban.cfg").write_text(text + "halt_after_auto_resume =\n" if text else "")
    assert runaway.resolve_thresholds(root, PROJECT) == {s.name: s.default for s in runaway.COUNTERS}
    assert runaway.resolve_halt_after_auto_resume(root, PROJECT) is False


def test_project_cfg_overrides_kanban_cfg_and_zero_turns_a_counter_off(ctx, root):
    (root / "kanban.cfg").write_text("[overwatch]\nrunaway_releases_per_hour = 2\nhalt_after_auto_resume = true\n")
    (root / "projects" / PROJECT / "project.cfg").write_text("[overwatch]\nrunaway_releases_per_hour = 0\n")
    assert runaway.resolve_thresholds(root, PROJECT)[runaway.COUNTER_RELEASES] == 0
    assert runaway.resolve_thresholds(root, OTHER)[runaway.COUNTER_RELEASES] == 2
    assert runaway.resolve_halt_after_auto_resume(root, PROJECT) is True
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))
    assert runaway.run(ctx, PROJECT, now=NOW).outcome == runaway.OUTCOME_CLEAR


@pytest.mark.parametrize(
    "key,value,error",
    [
        ("runaway_releases_per_hour", "five", "runaway.bad_threshold"),
        ("runaway_reason_repeats", "-1", "runaway.bad_threshold"),
        ("runaway_inbox_entries_per_day", "2.5", "runaway.bad_threshold"),
        ("halt_after_auto_resume", "yes", "runaway.bad_halt_after_auto_resume"),
    ],
)
def test_an_unrecognised_value_refuses_dual_channel_with_a_runnable_remedy(ctx, root, key, value, error):
    cfg = root / "kanban.cfg"
    cfg.write_text(f"[overwatch]\n{key} = {value}\n")
    with pytest.raises(runaway.RunawayError) as caught:
        runaway.resolve_thresholds(root, PROJECT)
        runaway.resolve_halt_after_auto_resume(root, PROJECT)
    exc = caught.value
    assert exc.error == error
    assert value in exc.message and str(cfg) in exc.message
    assert "<" not in exc.remedy
    subprocess.run(
        ["bash", "-c", exc.remedy], check=True, cwd=str(root), timeout=30, env=build_scrubbed_env(home_dir=root / "tmp")
    )
    runaway.resolve_thresholds(root, PROJECT)
    runaway.resolve_halt_after_auto_resume(root, PROJECT)


def test_a_refused_threshold_writes_nothing(ctx, root):
    (root / "kanban.cfg").write_text("[overwatch]\nrunaway_rc_cycles_per_day = on\n")
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))
    with pytest.raises(runaway.RunawayError):
        runaway.run(ctx, PROJECT, now=NOW)
    assert not _halt(root).exists()
    assert not runaway.counters_path(ctx, PROJECT).exists()


# ---------------------------------------------------------------------------
# Command line, run by file path
# ---------------------------------------------------------------------------


def _cli(tmp_path: pathlib.Path, root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp")},
    )
    return subprocess.run(
        [sys.executable, str(_MODULE_FILE), *args],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_check_stops_a_runaway_and_prints_the_dual_channel_block(tmp_path, root):
    for n in range(6):
        _ship(root, f"v1.0.{n}", NOW - datetime.timedelta(minutes=50 - n * 10))
    done = _cli(tmp_path, root, "check", "--project", PROJECT, "--now", _iso(NOW))
    assert done.returncode == 0, done.stderr
    assert "tripped" in done.stdout
    assert re.search(r"^error:\s+runaway\.releases_per_hour$", done.stderr, re.M), done.stderr
    remedy = re.search(r"^remedy:\s+(.*)$", done.stderr, re.M).group(1)
    shown = subprocess.run(
        ["bash", "-c", remedy],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        env=build_scrubbed_env(home_dir=tmp_path / "home"),
    )
    assert shown.returncode == 0, shown.stderr
    assert "releases_per_hour" in shown.stdout
    assert _halt(root).exists()


def test_cli_config_and_status_read_only(tmp_path, root):
    done = _cli(tmp_path, root, "config", "--project", PROJECT, "--json")
    assert done.returncode == 0, done.stderr
    assert {k: str(v).lower() for k, v in json.loads(done.stdout).items()} == _NEW_KEYS
    status = _cli(tmp_path, root, "status", "--project", PROJECT)
    assert status.returncode == 0, status.stderr
    assert "releases_per_hour" in status.stdout
    assert not (root / "projects" / PROJECT / "overwatch").exists()


def test_cli_refuses_an_unrecognised_value_with_exit_1(tmp_path, root):
    (root / "kanban.cfg").write_text("[overwatch]\nrunaway_releases_per_hour = lots\n")
    done = _cli(tmp_path, root, "check", "--project", PROJECT)
    assert done.returncode == 1
    assert re.search(r"^error:\s+runaway\.bad_threshold$", done.stderr, re.M), done.stderr
    assert not _halt(root).exists()
