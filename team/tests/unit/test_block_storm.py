"""
test_block_storm.py
===================
Unit tests for pgai_agent_kanban.overwatch.block_storm, the decision behind
the wake's blocked-task guard: whether a project's human-gated blocks
(BLOCKED, Needs Human: yes) are systemic and so hold its task dispatch.

Covered:
  - which queue lines count as human-gated blocks (State and Needs Human,
    each on its own; a task in two queues counted once; an id outside the
    canonical pattern never becomes a path; an unreadable status skipped);
  - the decision: no block, task-local blocks (including one with no Block
    Record), a storm inside and outside the window measured back from now,
    the in-window blocks of three, a systemic reason code, the report
    order when several reason codes are systemic, the agent catch-all
    never forming a storm, and the hold cap (a hold lifts by itself, a
    block during a hold does not extend it, a later one begins a new one);
  - the configuration values: the default, ``none``, normalisation and
    unknown members of the systemic list; the window default and a
    rejected window;
  - the CLI: the six key=value lines, --now and --hold-cap, the
    dual-channel warnings, and that the printed remedy runs and prints the
    blockers;
  - kanban.cfg_example states exactly the module's default list.

Every fixture lives under pytest's tmp_path; nothing outside it is read or
written.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.overwatch import block_storm
from pgai_agent_kanban.overwatch.block_record import write_block_record
from pgai_agent_kanban.overwatch.block_storm import (
    DEFAULT_HOLD_CAP_S,
    DEFAULT_STORM_WINDOW_S,
    DEFAULT_SYSTEMIC_REASON_CODES,
    HumanGatedBlock,
    decide,
    parse_systemic_codes,
    parse_window,
    scan_human_gated_blocks,
)

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_INI_PARSER = _TEAM_DIR / "scripts" / "lib" / "ini_parser.sh"
_ID_PATTERN = r"^(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$"
_T0 = datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc)

_A = "CODER-20260101-001-first"
_B = "WRITER-20260101-002-second"
_C = "CODER-20260101-003-third"


class _Project:
    """A project's tasks/ tree with its queues."""

    def __init__(self, root: pathlib.Path) -> None:
        self.tasks = root / "tasks"
        self.queues = self.tasks / "queues"
        self.queues.mkdir(parents=True)

    def task(
        self,
        task_id: str,
        *,
        state: str = "BLOCKED",
        needs_human: str = "yes",
        marker: str = "B",
        queue: str = "coder",
        reason_code: str | None = None,
        minutes: int = 0,
    ) -> pathlib.Path:
        status = self.tasks / task_id / "status.md"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(
            f"# Status\n\n## State\n{state}\n\n## Blockers\nfixture blocker\n\n"
            f"## Needs Human\n{needs_human}\n",
            encoding="utf-8",
        )
        self.queue_line(queue, marker, task_id)
        if reason_code is not None:
            write_block_record(
                status, reason_code, "CODER", ["worktree=gone"],
                now=_T0 + datetime.timedelta(minutes=minutes),
            )
        return status

    def queue_line(self, queue: str, marker: str, task_id: str) -> None:
        with (self.queues / f"{queue}_backlog.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [{marker}] {task_id} — fixture\n")

    def scan(self) -> list[HumanGatedBlock]:
        return scan_human_gated_blocks(self.queues, self.tasks, _ID_PATTERN)


def _at(minutes: float) -> datetime.datetime:
    """The fixture clock: *minutes* after _T0."""
    return _T0 + datetime.timedelta(minutes=minutes)


def _block(task_id: str, code: str | None, minutes: int | None) -> HumanGatedBlock:
    at = None if minutes is None else _T0 + datetime.timedelta(minutes=minutes)
    return HumanGatedBlock(task_id, pathlib.Path(f"/fixture/{task_id}/status.md"), code, at)


# ---------------------------------------------------------------------------
# Which queue lines are human-gated blocks
# ---------------------------------------------------------------------------


def test_scan_counts_blocked_needs_human_yes_with_its_record(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, reason_code="per_task_timeout", minutes=5)

    blocks = project.scan()

    assert [(b.task_id, b.reason_code) for b in blocks] == [(_A, "per_task_timeout")]
    assert blocks[0].blocked_at == _T0 + datetime.timedelta(minutes=5)


@pytest.mark.parametrize(
    "state, needs_human",
    [
        pytest.param("BLOCKED", "no", id="blocked-needs-human-no"),
        pytest.param("DONE", "yes", id="stale-marker-needs-human-yes"),
    ],
)
def test_scan_needs_both_blocked_state_and_needs_human_yes(tmp_path, state, needs_human):
    project = _Project(tmp_path)
    project.task(_A, state=state, needs_human=needs_human, reason_code="plugin_load_failed")

    assert project.scan() == []


def test_scan_ignores_needs_human_absent(tmp_path):
    project = _Project(tmp_path)
    status = project.task(_A)
    status.write_text("# Status\n\n## State\nBLOCKED\n", encoding="utf-8")

    assert project.scan() == []


def test_scan_reads_every_queue_and_counts_a_task_once(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, queue="coder", reason_code="zero_progress")
    project.task(_B, queue="writer", reason_code="zero_progress")
    project.queue_line("pm", "B", _A)

    assert sorted(b.task_id for b in project.scan()) == [_A, _B]


def test_scan_skips_an_id_outside_the_canonical_pattern(tmp_path):
    project = _Project(tmp_path)
    project.queue_line("coder", "B", "../../escape")

    assert project.scan() == []


def test_scan_reads_a_task_without_a_block_record_as_unclassified(tmp_path):
    project = _Project(tmp_path)
    project.task(_A)

    blocks = project.scan()

    assert len(blocks) == 1
    assert blocks[0].reason_code is None and blocks[0].blocked_at is None


def test_scan_reads_an_unreadable_block_record_as_unclassified(tmp_path):
    project = _Project(tmp_path)
    status = project.task(_A)
    with status.open("a", encoding="utf-8") as handle:
        handle.write("\n## Block Record\nreason_code: per_task_timeout\nattempts: many\n")

    blocks = project.scan()

    assert len(blocks) == 1 and blocks[0].reason_code is None


def test_scan_skips_a_missing_status_file(tmp_path):
    project = _Project(tmp_path)
    project.queue_line("coder", "B", _C)

    assert project.scan() == []


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def test_no_block_decides_none():
    decision = decide([])

    assert decision.verdict == "none" and decision.count == 0 and not decision.systemic


def test_one_block_is_task_local():
    decision = decide([_block(_A, "per_task_timeout", 0)])

    assert decision.verdict == "task_local"
    assert decision.count == 1
    assert decision.reason_code is None
    assert decision.remedy == "none"
    assert f"{_A} (per_task_timeout)" in decision.why


def test_blocks_without_records_are_task_local_however_many():
    decision = decide([_block(_A, None, None), _block(_B, None, None), _block(_C, None, None)])

    assert decision.verdict == "task_local"
    assert decision.why.count("(unrecorded)") == 3


def test_two_same_code_blocks_inside_the_window_are_a_storm():
    decision = decide(
        [_block(_A, "per_task_timeout", 0), _block(_B, "per_task_timeout", 10)], 3600, now=_at(10)
    )

    assert decision.systemic
    assert decision.reason_code == "per_task_timeout"
    assert decision.tasks == (_A, _B)
    assert "2 human-gated blocks share reason_code per_task_timeout" in decision.why
    assert "block_storm_window=3600s" in decision.why


def test_the_same_two_blocks_outside_the_window_are_task_local():
    decision = decide(
        [_block(_A, "per_task_timeout", 0), _block(_B, "per_task_timeout", 120)], 3600, now=_at(120)
    )

    assert decision.verdict == "task_local"


def test_blocks_exactly_one_window_apart_are_a_storm():
    decision = decide([_block(_A, "zero_progress", 0), _block(_B, "zero_progress", 60)], 3600, now=_at(60))

    assert decision.systemic


def test_different_codes_inside_the_window_are_not_a_storm():
    decision = decide([_block(_A, "per_task_timeout", 0), _block(_B, "zero_progress", 1)], 3600, now=_at(1))

    assert decision.verdict == "task_local"


def test_a_block_with_no_time_never_joins_a_storm():
    decision = decide(
        [_block(_A, "per_task_timeout", 0), _block(_B, "per_task_timeout", None)], 3600, now=_at(0)
    )

    assert decision.verdict == "task_local"


def test_storm_reports_the_blocks_inside_the_window_from_now():
    blocks = [
        _block(_A, "per_task_timeout", 0),
        _block(_B, "per_task_timeout", 300),
        _block(_C, "per_task_timeout", 320),
    ]

    decision = decide(blocks, 3600, now=_at(320))

    assert decision.tasks == (_B, _C)
    assert decision.why.startswith("2 human-gated blocks")


def test_a_systemic_reason_code_holds_on_its_own():
    decision = decide([_block(_A, "plugin_load_failed", 0)], now=_at(0))

    assert decision.systemic
    assert decision.reason_code == "plugin_load_failed"
    assert "is on the systemic list ([wake] systemic_block_reasons)" in decision.why


def test_an_empty_systemic_list_leaves_only_storms():
    decision = decide([_block(_A, "plugin_load_failed", 0)], systemic_codes=(), now=_at(0))

    assert decision.verdict == "task_local"


def test_a_listed_code_is_reported_ahead_of_a_storm_and_the_rest_named():
    blocks = [
        _block(_A, "zero_progress", 0),
        _block(_B, "zero_progress", 1),
        _block(_C, "invalid_assignment", 2),
    ]

    decision = decide(blocks, 3600, now=_at(2))

    assert decision.reason_code == "invalid_assignment"
    assert "; also systemic: zero_progress;" in decision.why
    assert decision.count == 3


def test_systemic_remedy_names_each_task_status_file():
    decision = decide([_block(_A, "per_task_timeout", 0), _block(_B, "per_task_timeout", 1)], now=_at(1))

    assert decision.remedy.startswith("grep -A8 -e '^## Blockers' -e '^## Block Record' ")
    assert f"/fixture/{_A}/status.md" in decision.remedy
    assert f"/fixture/{_B}/status.md" in decision.remedy
    assert "<" not in decision.remedy


def test_two_agent_unclassified_blocks_a_minute_apart_do_not_hold():
    """The catch-all an agent sets on its own block says nothing shared."""
    decision = decide(
        [_block(_A, "agent_unclassified", 0), _block(_B, "agent_unclassified", 1)], 3600, now=_at(1)
    )

    assert decision.verdict == "task_local"
    assert decision.lifts_at is None


def test_agent_unclassified_still_holds_when_the_operator_lists_it():
    decision = decide(
        [_block(_A, "agent_unclassified", 0)], systemic_codes=("agent_unclassified",), now=_at(0)
    )

    assert decision.systemic


def test_a_storm_older_than_the_window_from_now_does_not_hold():
    """The same two blocks that are a storm a minute later are not, two hours later."""
    blocks = [_block(_A, "per_task_timeout", 0), _block(_B, "per_task_timeout", 1)]

    assert decide(blocks, 3600, now=_at(2)).systemic
    assert decide(blocks, 3600, now=_at(120)).verdict == "task_local"


def test_a_storm_lifts_when_its_older_block_leaves_the_window():
    blocks = [_block(_A, "per_task_timeout", 0), _block(_B, "per_task_timeout", 10)]

    held = decide(blocks, 3600, now=_at(30))

    assert held.lifts_at == _at(60)
    assert "the hold lifts at 2026-01-01T11:00:00+00:00" in held.why
    assert decide(blocks, 3600, now=_at(61)).verdict == "task_local"


def test_no_storm_hold_outlasts_the_cap():
    blocks = [_block(_A, "per_task_timeout", 0), _block(_B, "per_task_timeout", 1)]

    held = decide(blocks, 3600, cap_s=600, now=_at(5))

    assert held.systemic and held.lifts_at == _at(11)
    assert "block_hold_cap=600s" in held.why
    # Still inside the storm window, but past the cap: the hold has lifted.
    assert decide(blocks, 3600, cap_s=600, now=_at(12)).verdict == "task_local"


def test_no_systemic_code_hold_outlasts_the_cap():
    blocks = [_block(_A, "plugin_load_failed", 0)]

    held = decide(blocks, cap_s=3600, now=_at(59))

    assert held.systemic and held.lifts_at == _at(60)
    assert decide(blocks, cap_s=3600, now=_at(61)).verdict == "task_local"


def test_a_block_during_a_hold_does_not_extend_it_and_a_later_one_begins_a_new_hold():
    during = [_block(_A, "plugin_load_failed", 0), _block(_B, "plugin_load_failed", 30)]

    assert decide(during, cap_s=3600, now=_at(45)).lifts_at == _at(60)
    assert decide(during, cap_s=3600, now=_at(70)).verdict == "task_local"

    later = during + [_block(_C, "plugin_load_failed", 90)]
    renewed = decide(later, cap_s=3600, now=_at(100))
    assert renewed.systemic and renewed.lifts_at == _at(150)


def test_the_hold_lifts_when_the_last_held_code_lifts():
    blocks = [
        _block(_A, "per_task_timeout", 0),
        _block(_B, "per_task_timeout", 1),
        _block(_C, "plugin_load_failed", 20),
    ]

    decision = decide(blocks, 3600, cap_s=3600, now=_at(21))

    assert decision.reason_code == "plugin_load_failed"
    assert decision.lifts_at == _at(80)


def test_a_block_dated_after_now_is_read_as_now():
    blocks = [_block(_A, "plugin_load_failed", 30)]

    decision = decide(blocks, cap_s=600, now=_at(0))

    assert decision.systemic and decision.lifts_at == _at(10)


# ---------------------------------------------------------------------------
# Configuration values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_systemic_list_means_the_default(value):
    assert parse_systemic_codes(value) == (DEFAULT_SYSTEMIC_REASON_CODES, ())


@pytest.mark.parametrize("value", ["none", "NONE", " None "])
def test_none_empties_the_systemic_list(value):
    assert parse_systemic_codes(value) == ((), ())


def test_systemic_list_members_are_normalised_deduplicated_and_unknowns_returned():
    codes, unknown = parse_systemic_codes("Plugin-Load-Failed, plugin_load_failed  per_task_timeout,bogus")

    assert codes == ("plugin_load_failed", "per_task_timeout")
    assert unknown == ("bogus",)


def test_default_systemic_list_is_the_config_and_plugin_classes():
    assert set(DEFAULT_SYSTEMIC_REASON_CODES) == {
        "invalid_effort_config",
        "workflow_type_unparseable",
        "plugin_load_failed",
        "branch_prefix_failed",
        "invalid_assignment",
    }


@pytest.mark.parametrize(
    "value, expected",
    [
        pytest.param(None, (DEFAULT_STORM_WINDOW_S, None), id="unset"),
        pytest.param("", (DEFAULT_STORM_WINDOW_S, None), id="blank"),
        pytest.param("900", (900, None), id="digits"),
        pytest.param("1h", (DEFAULT_STORM_WINDOW_S, "1h"), id="unit-suffix"),
        pytest.param("-5", (DEFAULT_STORM_WINDOW_S, "-5"), id="signed"),
    ],
)
def test_parse_window(value, expected):
    assert parse_window(value) == expected


def test_kanban_cfg_example_states_the_default_systemic_list():
    """The template's active line reads back, through the production INI
    reader, as exactly the module's default list -- copying it unedited
    changes nothing."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {str(_INI_PARSER)!r} && read_ini "
            f"{str(_REPO_ROOT / 'kanban.cfg_example')!r} wake systemic_block_reasons ''",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_TEAM_DIR,
    )
    assert result.returncode == 0, result.stderr
    codes, unknown = parse_systemic_codes(result.stdout.strip())
    assert unknown == ()
    assert codes == DEFAULT_SYSTEMIC_REASON_CODES


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def _cli(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cwd),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(_TEAM_DIR),
    }
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.overwatch.block_storm", *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
        env=env,
    )


def _fields(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines())


def test_cli_prints_the_six_fields_in_order(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, reason_code="per_task_timeout", minutes=0)
    project.task(_B, queue="writer", reason_code="per_task_timeout", minutes=30)

    result = _cli("decide", str(project.queues), str(project.tasks), "--id-pattern", _ID_PATTERN,
                  "--window", "3600", "--now", _at(30).isoformat(), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert [line.split("=", 1)[0] for line in result.stdout.splitlines()] == [
        "verdict", "count", "reason_code", "why", "remedy", "lifts_at",
    ]
    fields = _fields(result.stdout)
    assert fields["verdict"] == "systemic"
    assert fields["count"] == "2"
    assert fields["reason_code"] == "per_task_timeout"
    assert fields["lifts_at"] == "2026-01-01T11:00:00+00:00"
    assert result.stderr == ""


def test_cli_hold_cap_shortens_the_hold_and_a_task_local_decision_lifts_nothing(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, reason_code="per_task_timeout", minutes=0)
    project.task(_B, queue="writer", reason_code="per_task_timeout", minutes=1)

    capped = _fields(_cli("decide", str(project.queues), str(project.tasks), "--hold-cap", "300",
                          "--now", _at(2).isoformat(), cwd=tmp_path).stdout)
    lifted = _fields(_cli("decide", str(project.queues), str(project.tasks), "--hold-cap", "300",
                          "--now", _at(7).isoformat(), cwd=tmp_path).stdout)

    assert capped["verdict"] == "systemic" and capped["lifts_at"] == "2026-01-01T10:06:00+00:00"
    assert lifted["verdict"] == "task_local" and lifted["lifts_at"] == "none"


def test_cli_without_now_measures_the_window_from_the_current_time(tmp_path):
    """Fixture blocks dated 2026-01-01 are long past any window today."""
    project = _Project(tmp_path)
    project.task(_A, reason_code="per_task_timeout", minutes=0)
    project.task(_B, queue="writer", reason_code="per_task_timeout", minutes=1)

    result = _cli("decide", str(project.queues), str(project.tasks), cwd=tmp_path)

    assert _fields(result.stdout)["verdict"] == "task_local"


def test_cli_warns_on_a_rejected_hold_cap_or_now_and_still_decides(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, reason_code="plugin_load_failed", minutes=0)

    result = _cli("decide", str(project.queues), str(project.tasks), "--hold-cap", "1h",
                  "--now", "yesterday", cwd=tmp_path)

    assert result.returncode == 0
    assert "error:   block_storm.invalid_hold_cap" in result.stderr
    assert "error:   block_storm.invalid_now" in result.stderr
    assert _fields(result.stdout)["verdict"] == "task_local"


def test_cli_remedy_runs_and_prints_each_blocker(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, reason_code="branch_prefix_failed")

    result = _cli("decide", str(project.queues), str(project.tasks), "--now", _at(0).isoformat(), cwd=tmp_path)
    remedy = _fields(result.stdout)["remedy"]
    ran = subprocess.run(["bash", "-c", remedy], capture_output=True, text=True, timeout=15, cwd=tmp_path)

    assert ran.returncode == 0, ran.stderr
    assert "fixture blocker" in ran.stdout
    assert "reason_code: branch_prefix_failed" in ran.stdout


def test_cli_warns_dual_channel_on_unknown_code_and_still_decides(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, reason_code="plugin_load_failed")

    result = _cli("decide", str(project.queues), str(project.tasks),
                  "--systemic-codes", "not_a_code", cwd=tmp_path)

    assert result.returncode == 0
    assert "error:   block_storm.unknown_systemic_code" in result.stderr
    assert "'not_a_code'" in result.stderr
    remedy = next(line for line in result.stderr.splitlines() if line.startswith("remedy:"))
    listed = subprocess.run(
        ["bash", "-c", remedy.split(":", 1)[1].strip()],
        capture_output=True, text=True, timeout=30, cwd=tmp_path,
    )
    assert listed.returncode == 0 and "plugin_load_failed" in listed.stdout
    # The unknown member was dropped, leaving no systemic code at all.
    assert _fields(result.stdout)["verdict"] == "task_local"


def test_cli_warns_on_a_rejected_window_and_uses_the_default(tmp_path):
    project = _Project(tmp_path)
    project.task(_A, reason_code="zero_progress", minutes=0)
    project.task(_B, queue="writer", reason_code="zero_progress", minutes=50)

    result = _cli("decide", str(project.queues), str(project.tasks), "--window", "1h",
                  "--now", _at(50).isoformat(), cwd=tmp_path)

    assert "error:   block_storm.invalid_window" in result.stderr
    fields = _fields(result.stdout)
    assert fields["verdict"] == "systemic"
    assert f"block_storm_window={DEFAULT_STORM_WINDOW_S}s" in fields["why"]


def test_cli_defaults_prints_window_and_list(tmp_path):
    result = _cli("defaults", cwd=tmp_path)

    assert result.returncode == 0
    fields = _fields(result.stdout)
    assert fields["window"] == str(DEFAULT_STORM_WINDOW_S)
    assert fields["hold_cap"] == str(DEFAULT_HOLD_CAP_S)
    assert tuple(fields["systemic_codes"].split(",")) == DEFAULT_SYSTEMIC_REASON_CODES


def test_cli_help_exits_zero(tmp_path):
    result = _cli("decide", "--help", cwd=tmp_path)

    assert result.returncode == 0
    for flag in ("--systemic-codes", "--window", "--hold-cap", "--now"):
        assert flag in result.stdout, flag


def test_module_exposes_the_documented_names():
    for name in ("HumanGatedBlock", "GateDecision", "scan_human_gated_blocks",
                 "parse_systemic_codes", "parse_window", "decide", "main",
                 "DEFAULT_HOLD_CAP_S", "STORM_EXEMPT_REASON_CODES"):
        assert hasattr(block_storm, name), name


def test_kanban_cfg_example_states_the_default_hold_cap():
    """The template's block_hold_cap line reads back as the module default."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {str(_INI_PARSER)!r} && read_ini "
            f"{str(_REPO_ROOT / 'kanban.cfg_example')!r} wake block_hold_cap ''",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_TEAM_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(DEFAULT_HOLD_CAP_S)
