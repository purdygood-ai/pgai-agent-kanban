"""
test_reliability_streaks.py
===========================
Unit tests for pgai_agent_kanban.metrics.reliability — the Hands-off and
Clean streaks computed from a project's block ledger and its
metrics/rc/*.json rollups, and the metrics/reliability.json refresh.

Coverage:
  - The class filter: a block that only reached class "any" resets Clean
    but not Hands-off; a human block resets both.
  - An operator-cause HALT interval is excluded from both streaks.
  - The block's own RC is not counted in ``rcs``.
  - No qualifying block: status "no block on record", counting from the
    first ledger record; an empty ledger counts nothing.
  - Old-shape (four-field) records take the defaults cause "chain" and
    class "any".
  - An open block is a current block: its streak reads 0.
  - Only shipped rollups with a closed_at are counted.
  - Clean's rcs and hours never exceed Hands-off's, across every fixture.
  - The last-block detail of each class carries time, RC, task,
    reason_code and resolution.
  - An append through blocked_ledger's write API rewrites
    metrics/reliability.json; a ledger outside the convention path is
    skipped; a failed refresh never changes the append's result.
  - The CLI's refresh and show subcommands.

Every fixture is a tmp_path project root; no live filesystem paths.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pgai_agent_kanban.metrics.blocked_ledger import (
    NO_RC_VERSION,
    append_block_end,
    append_block_start,
    mark_block_escalated,
    parse_records,
)
from pgai_agent_kanban.metrics.reliability import (
    STATUS_NO_BLOCK,
    STATUS_OPEN,
    STATUS_RESOLVED,
    compute_reliability,
    refresh_for_ledger,
    reliability,
    write_reliability,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = Path(__file__).resolve().parents[2]

PROJECT = "demo"
NOW = "2026-10-18T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _metrics_dir(root: Path) -> Path:
    path = root / "projects" / PROJECT / "metrics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_ledger(root: Path, records: list) -> Path:
    ledger = _metrics_dir(root) / "blocked-intervals.jsonl"
    ledger.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")
    return ledger


def _write_rollup(root: Path, version: str, closed_at, outcome: str = "shipped") -> None:
    rc_dir = _metrics_dir(root) / "rc"
    rc_dir.mkdir(parents=True, exist_ok=True)
    payload = {"rc": version, "project": PROJECT, "outcome": outcome, "closed_at": closed_at}
    (rc_dir / f"{version}.json").write_text(json.dumps(payload), encoding="utf-8")


def _start(version, source, ts, **extra) -> dict:
    return {"version": version, "kind": "block-start", "source": source, "timestamp": ts, **extra}


def _end(version, source, ts, **extra) -> dict:
    return {"version": version, "kind": "block-end", "source": source, "timestamp": ts, **extra}


def _escalate(version, source, ts) -> dict:
    return {"version": version, "kind": "block-escalate", "source": source, "timestamp": ts, "class": "human"}


def _result(root: Path) -> dict:
    return reliability(PROJECT, kanban_root=root, now=NOW)


# Shipped RC history shared by most fixtures: one release a day, at noon.
_RCS = [
    ("v1.0.0", "2026-10-10T12:00:00+00:00"),
    ("v1.1.0", "2026-10-11T12:00:00+00:00"),
    ("v1.2.0", "2026-10-12T12:00:00+00:00"),
    ("v1.3.0", "2026-10-13T12:00:00+00:00"),
    ("v1.4.0", "2026-10-14T12:00:00+00:00"),
    ("v1.10.0", "2026-10-15T12:00:00+00:00"),
]


def _ship_all(root: Path) -> None:
    for version, closed_at in _RCS:
        _write_rollup(root, version, closed_at)


def _fixture_human_then_any(root: Path) -> None:
    """A human block in v1.0.0 resolved on 10-10 06:00, then an any block in
    v1.2.0 resolved on 10-12 06:00."""
    _ship_all(root)
    _write_ledger(
        root,
        [
            _start(
                "v1.0.0",
                "task:T-human",
                "2026-10-10T01:00:00+00:00",
                **{"class": "any", "cause": "chain", "reason_code": "agent_unclassified"},
            ),
            _escalate("v1.0.0", "task:T-human", "2026-10-10T02:00:00+00:00"),
            _end("v1.0.0", "task:T-human", "2026-10-10T06:00:00+00:00", **{"class": "human", "reason_code": "reset"}),
            _start(
                "v1.2.0",
                "task:T-any",
                "2026-10-12T01:00:00+00:00",
                **{"class": "any", "cause": "chain", "reason_code": "provider_401"},
            ),
            _end("v1.2.0", "task:T-any", "2026-10-12T06:00:00+00:00", **{"class": "any", "reason_code": "requeue"}),
        ],
    )


def _fixture_operator_halt_last(root: Path) -> None:
    """The human/any history above, plus a later operator-raised HALT."""
    _fixture_human_then_any(root)
    ledger = _metrics_dir(root) / "blocked-intervals.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        for record in (
            _start("v1.4.0", "halt:project", "2026-10-14T01:00:00+00:00", cause="operator"),
            _end("v1.4.0", "halt:project", "2026-10-14T03:00:00+00:00"),
        ):
            handle.write(json.dumps(record) + "\n")


def _fixture_operator_only(root: Path) -> None:
    _ship_all(root)
    _write_ledger(
        root,
        [
            _start("v1.1.0", "halt:project", "2026-10-11T01:00:00+00:00", cause="operator"),
            _end("v1.1.0", "halt:project", "2026-10-11T02:00:00+00:00"),
        ],
    )


def _fixture_old_shape_only(root: Path) -> None:
    _ship_all(root)
    _write_ledger(
        root,
        [
            _start("v1.3.0", "halt:project", "2026-10-13T01:00:00+00:00"),
            _end("v1.3.0", "halt:project", "2026-10-13T02:00:00+00:00"),
        ],
    )


def _fixture_open_any_block(root: Path) -> None:
    _fixture_human_then_any(root)
    ledger = _metrics_dir(root) / "blocked-intervals.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _start(NO_RC_VERSION, "task:T-open", "2026-10-16T01:00:00+00:00", reason_code="agent_unclassified")
            )
            + "\n"
        )


def _fixture_open_human_block(root: Path) -> None:
    _fixture_open_any_block(root)
    ledger = _metrics_dir(root) / "blocked-intervals.jsonl"
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_escalate(NO_RC_VERSION, "task:T-open", "2026-10-16T02:00:00+00:00")) + "\n")


def _fixture_empty(root: Path) -> None:
    _ship_all(root)


def _fixture_out_of_order_close(root: Path) -> None:
    """A human block's own RC closes after a later no-RC any block is
    resolved: uncapped, Clean would count that RC while Hands-off may not —
    the shape the Clean <= Hands-off cap exists for."""
    _write_rollup(root, "v1.0.0", "2026-10-15T12:00:00+00:00")
    _write_ledger(
        root,
        [
            _start("v1.0.0", "task:T-human", "2026-10-10T01:00:00+00:00", **{"class": "human"}),
            _end("v1.0.0", "task:T-human", "2026-10-10T06:00:00+00:00", **{"class": "human"}),
            _start(NO_RC_VERSION, "task:T-any", "2026-10-11T01:00:00+00:00"),
            _end(NO_RC_VERSION, "task:T-any", "2026-10-11T06:00:00+00:00"),
        ],
    )


ALL_FIXTURES = [
    _fixture_human_then_any,
    _fixture_operator_halt_last,
    _fixture_operator_only,
    _fixture_old_shape_only,
    _fixture_open_any_block,
    _fixture_open_human_block,
    _fixture_empty,
    _fixture_out_of_order_close,
]


# ---------------------------------------------------------------------------
# Class filter
# ---------------------------------------------------------------------------


def test_block_that_only_reached_class_any_resets_clean_but_not_hands_off(tmp_path: Path) -> None:
    _fixture_human_then_any(tmp_path)

    result = _result(tmp_path)

    hands_off, clean = result["hands_off"], result["clean"]
    assert hands_off["status"] == STATUS_RESOLVED
    assert hands_off["since"] == "2026-10-10T06:00:00+00:00"
    # v1.1.0 .. v1.10.0 closed after the human block's resolution; its own
    # RC v1.0.0 closed after it too but is not counted.
    assert hands_off["rcs"] == 5
    assert hands_off["hours"] == 8 * 24 + 6
    assert hands_off["last_block"]["task"] == "T-human"

    assert clean["status"] == STATUS_RESOLVED
    assert clean["since"] == "2026-10-12T06:00:00+00:00"
    # v1.3.0, v1.4.0, v1.10.0 — v1.2.0 is the any block's own RC.
    assert clean["rcs"] == 3
    assert clean["hours"] == 6 * 24 + 6
    assert clean["last_block"]["task"] == "T-any"


def test_human_block_resets_both_streaks(tmp_path: Path) -> None:
    _ship_all(tmp_path)
    _write_ledger(
        tmp_path,
        [
            _start("v1.0.0", "task:T-any", "2026-10-10T01:00:00+00:00"),
            _end("v1.0.0", "task:T-any", "2026-10-10T02:00:00+00:00"),
            _start("v1.3.0", "task:T-human", "2026-10-13T01:00:00+00:00", **{"class": "human"}),
            _end("v1.3.0", "task:T-human", "2026-10-13T02:00:00+00:00", **{"class": "human"}),
        ],
    )

    result = _result(tmp_path)

    for name in ("hands_off", "clean"):
        assert result[name]["since"] == "2026-10-13T02:00:00+00:00"
        assert result[name]["rcs"] == 2  # v1.4.0, v1.10.0
        assert result[name]["last_block"]["task"] == "T-human"


# ---------------------------------------------------------------------------
# Operator cause, own RC, rollup filter
# ---------------------------------------------------------------------------


def test_operator_cause_halt_interval_is_excluded_from_both_streaks(tmp_path: Path) -> None:
    _fixture_operator_halt_last(tmp_path)
    baseline_root = tmp_path / "baseline"
    _fixture_human_then_any(baseline_root)

    with_operator = _result(tmp_path)
    without_operator = _result(baseline_root)

    for name in ("hands_off", "clean"):
        assert with_operator[name] == without_operator[name]
        assert with_operator[name]["last_block"]["source"] != "halt:project"


def test_operator_only_ledger_reports_no_block_on_record(tmp_path: Path) -> None:
    _fixture_operator_only(tmp_path)

    result = _result(tmp_path)

    for name in ("hands_off", "clean"):
        assert result[name]["status"] == STATUS_NO_BLOCK
        assert result[name]["last_block"] is None
        # Counted from the operator record, the first in the ledger.
        assert result[name]["since"] == "2026-10-11T01:00:00+00:00"
        assert result[name]["rcs"] == 5  # v1.1.0 .. v1.10.0


def test_blocks_own_rc_is_not_counted_in_rcs(tmp_path: Path) -> None:
    _ship_all(tmp_path)
    _write_ledger(
        tmp_path,
        [
            _start("v1.1.0", "task:T", "2026-10-11T01:00:00+00:00"),
            _end("v1.1.0", "task:T", "2026-10-11T02:00:00+00:00"),
        ],
    )

    clean = _result(tmp_path)["clean"]

    # v1.1.0 closed at noon, after the 02:00 resolution, but is the block's
    # own RC; v1.2.0, v1.3.0, v1.4.0 and v1.10.0 count.
    assert clean["rcs"] == 4


def test_block_raised_with_no_active_rc_has_no_own_rc_to_exclude(tmp_path: Path) -> None:
    _ship_all(tmp_path)
    _write_ledger(
        tmp_path,
        [
            _start(NO_RC_VERSION, "halt:project", "2026-10-11T01:00:00+00:00"),
            _end(NO_RC_VERSION, "halt:project", "2026-10-11T02:00:00+00:00"),
        ],
    )

    clean = _result(tmp_path)["clean"]

    assert clean["rcs"] == 5  # v1.1.0 .. v1.10.0
    assert clean["last_block"]["rc"] is None


def test_only_shipped_rollups_with_closed_at_are_counted(tmp_path: Path) -> None:
    _write_rollup(tmp_path, "v1.0.0", "2026-10-10T12:00:00+00:00")
    _write_rollup(tmp_path, "v1.1.0", "2026-10-11T12:00:00+00:00", outcome="cancelled")
    _write_rollup(tmp_path, "v1.2.0", None)
    (_metrics_dir(tmp_path) / "rc" / "v1.3.0.json").write_text("{not json", encoding="utf-8")
    _write_ledger(
        tmp_path,
        [
            _start("v0.9.0", "task:T", "2026-10-01T01:00:00+00:00"),
            _end("v0.9.0", "task:T", "2026-10-01T02:00:00+00:00"),
        ],
    )

    assert _result(tmp_path)["clean"]["rcs"] == 1


# ---------------------------------------------------------------------------
# No block, old shape, open block
# ---------------------------------------------------------------------------


def test_no_block_case_reports_no_block_on_record_and_counts_from_first_record(
    tmp_path: Path,
) -> None:
    _fixture_operator_only(tmp_path)

    result = _result(tmp_path)

    assert result["first_record_at"] == "2026-10-11T01:00:00+00:00"
    for name in ("hands_off", "clean"):
        streak = result[name]
        assert streak["status"] == "no block on record"
        assert streak["since"] == result["first_record_at"]
        assert streak["hours"] == 7 * 24 + 11


def test_empty_ledger_reports_no_block_on_record_with_nothing_counted(tmp_path: Path) -> None:
    _fixture_empty(tmp_path)

    result = _result(tmp_path)

    assert result["first_record_at"] is None
    for name in ("hands_off", "clean"):
        assert result[name]["status"] == STATUS_NO_BLOCK
        assert (result[name]["rcs"], result[name]["hours"], result[name]["since"]) == (0, 0.0, None)


def test_old_shape_records_take_cause_chain_and_class_any(tmp_path: Path) -> None:
    _fixture_old_shape_only(tmp_path)

    result = _result(tmp_path)

    clean, hands_off = result["clean"], result["hands_off"]
    assert clean["status"] == STATUS_RESOLVED
    assert clean["last_block"]["cause"] == "chain"
    assert clean["last_block"]["class"] == "any"
    assert clean["rcs"] == 2  # v1.4.0, v1.10.0
    assert hands_off["status"] == STATUS_NO_BLOCK
    assert hands_off["rcs"] == 3  # counted from 10-13 01:00: v1.3.0, v1.4.0, v1.10.0


def test_open_any_block_reads_clean_zero_and_leaves_hands_off_running(tmp_path: Path) -> None:
    _fixture_open_any_block(tmp_path)

    result = _result(tmp_path)

    clean = result["clean"]
    assert clean["status"] == STATUS_OPEN
    assert (clean["rcs"], clean["hours"], clean["since"]) == (0, 0.0, None)
    assert clean["last_block"]["task"] == "T-open"
    assert clean["last_block"]["resolution"] is None
    assert result["hands_off"]["status"] == STATUS_RESOLVED
    assert result["hands_off"]["rcs"] == 5


def test_open_escalated_block_reads_both_streaks_zero(tmp_path: Path) -> None:
    _fixture_open_human_block(tmp_path)

    result = _result(tmp_path)

    for name in ("hands_off", "clean"):
        assert result[name]["status"] == STATUS_OPEN
        assert (result[name]["rcs"], result[name]["hours"]) == (0, 0.0)
        assert result[name]["last_block"]["escalated"] is True


# ---------------------------------------------------------------------------
# Invariant and last-block detail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("build", ALL_FIXTURES, ids=lambda f: f.__name__.removeprefix("_fixture_"))
def test_clean_is_never_longer_than_hands_off(tmp_path: Path, build) -> None:
    build(tmp_path)

    result = _result(tmp_path)

    assert result["clean"]["rcs"] <= result["hands_off"]["rcs"]
    assert result["clean"]["hours"] <= result["hands_off"]["hours"]


def test_last_block_detail_carries_time_rc_task_reason_code_and_resolution(tmp_path: Path) -> None:
    _fixture_human_then_any(tmp_path)

    result = _result(tmp_path)

    assert result["hands_off"]["last_block"] == {
        "time": "2026-10-10T01:00:00+00:00",
        "rc": "v1.0.0",
        "task": "T-human",
        "source": "task:T-human",
        "reason_code": "agent_unclassified",
        "class": "human",
        "cause": "chain",
        "escalated": True,
        "resolution": {"time": "2026-10-10T06:00:00+00:00", "class": "human", "reason_code": "reset"},
    }
    assert result["clean"]["last_block"] == {
        "time": "2026-10-12T01:00:00+00:00",
        "rc": "v1.2.0",
        "task": "T-any",
        "source": "task:T-any",
        "reason_code": "provider_401",
        "class": "any",
        "cause": "chain",
        "escalated": False,
        "resolution": {"time": "2026-10-12T06:00:00+00:00", "class": "any", "reason_code": "requeue"},
    }


def test_halt_source_block_has_no_task(tmp_path: Path) -> None:
    _fixture_old_shape_only(tmp_path)

    assert _result(tmp_path)["clean"]["last_block"]["task"] is None


# ---------------------------------------------------------------------------
# Refresh: the ledger write hook and the writer
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_append_through_ledger_write_api_rewrites_reliability_json(tmp_path: Path) -> None:
    _ship_all(tmp_path)
    ledger = str(_metrics_dir(tmp_path) / "blocked-intervals.jsonl")
    out = _metrics_dir(tmp_path) / "reliability.json"

    append_block_start(ledger, "v1.4.0", "task:T", "2026-10-14T01:00:00+00:00", reason_code="agent_unclassified")
    after_start = _read_json(out)
    assert after_start["project"] == PROJECT
    assert after_start["clean"]["status"] == STATUS_OPEN
    assert after_start["hands_off"]["status"] == STATUS_NO_BLOCK

    mark_block_escalated(ledger, "v1.4.0", "task:T", "2026-10-14T02:00:00+00:00")
    assert _read_json(out)["hands_off"]["status"] == STATUS_OPEN

    append_block_end(ledger, "v1.4.0", "task:T", "2026-10-14T03:00:00+00:00", block_class="human", reason_code="reset")
    after_end = _read_json(out)
    for name in ("hands_off", "clean"):
        assert after_end[name]["status"] == STATUS_RESOLVED
        assert after_end[name]["since"] == "2026-10-14T03:00:00+00:00"
        assert after_end[name]["rcs"] == 1  # v1.10.0; v1.4.0 is the block's own RC
        assert after_end[name]["last_block"]["resolution"]["reason_code"] == "reset"


def test_ledger_outside_the_convention_path_is_not_refreshed(tmp_path: Path) -> None:
    ledger = tmp_path / "blocked-intervals.jsonl"

    append_block_start(str(ledger), "v1.0.0", "halt:project", "2026-10-10T01:00:00+00:00")

    assert refresh_for_ledger(str(ledger)) is None
    assert not (tmp_path / "reliability.json").exists()


def test_failed_refresh_never_changes_the_ledger_write(tmp_path: Path, capsys) -> None:
    metrics = _metrics_dir(tmp_path)
    ledger = str(metrics / "blocked-intervals.jsonl")
    # A directory where the file belongs makes every refresh fail.
    (metrics / "reliability.json").mkdir()

    append_block_start(ledger, "v1.0.0", "task:T", "2026-10-10T01:00:00+00:00")
    append_block_end(ledger, "v1.0.0", "task:T", "2026-10-10T02:00:00+00:00")

    assert [r["kind"] for r in parse_records(ledger)] == ["block-start", "block-end"]
    warning = capsys.readouterr().err
    assert "reliability.json" in warning and "the ledger write stands" in warning
    assert f"--project {PROJECT}" in warning


def test_write_reliability_writes_the_computed_document(tmp_path: Path) -> None:
    _fixture_human_then_any(tmp_path)

    path = write_reliability(PROJECT, kanban_root=tmp_path, now=NOW)

    assert Path(path) == _metrics_dir(tmp_path) / "reliability.json"
    assert _read_json(Path(path)) == compute_reliability(PROJECT, _metrics_dir(tmp_path), now=NOW)
    assert Path(path).read_text(encoding="utf-8").endswith("}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.metrics.reliability", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def test_cli_refresh_writes_the_file_and_show_prints_the_same_document(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _fixture_human_then_any(root)

    refreshed = _run_cli(tmp_path, "refresh", "--project", PROJECT, "--kanban-root", str(root), "--now", NOW)
    shown = _run_cli(tmp_path, "show", "--project", PROJECT, "--kanban-root", str(root), "--now", NOW)

    assert refreshed.returncode == 0, refreshed.stderr
    written = Path(json.loads(refreshed.stdout)["written"])
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout) == _read_json(written)


def test_cli_malformed_ledger_exits_1_with_the_dual_channel_contract(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (_metrics_dir(root) / "blocked-intervals.jsonl").write_text("{not json\n", encoding="utf-8")

    result = _run_cli(tmp_path, "refresh", "--project", PROJECT, "--kanban-root", str(root))

    assert result.returncode == 1
    assert "error:   blocked-ledger.malformed" in result.stderr
    assert not (_metrics_dir(root) / "reliability.json").exists()
