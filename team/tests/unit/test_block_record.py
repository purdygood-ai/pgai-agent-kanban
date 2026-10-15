"""
test_block_record.py
====================
Behaviour of the durable ``## Block Record`` section: the Python module
pgai_agent_kanban.overwatch.block_record, its sourced bash wrapper
team/scripts/lib/block_record.sh, the task_status ``remove_section``
primitive it removes the section with, and the record's survival across the
wake's promote_blocked_to_backlog.

Covered:
  1. A first write into a status.md with no record appends the section with
     all ten fields in order, ISO-8601 timestamps, attempts 0, and the
     reader returns the same values; every earlier byte is unchanged.
  2. A re-block keeps first_blocked_at, attempts, cooldown_until and resume,
     replaces reason_code, owner_role, evidence and last_blocked_at, and
     leaves every other section byte-identical — with the record at the end
     of the file or between two sections.
  3. block_record_write refuses an unknown reason_code or owner_role
     dual-channel (error/message/remedy), exits non-zero, and leaves
     status.md byte-identical.
  4. The update entry point sets attempts / cooldown_until / resume and
     refuses an absent record or an empty update; the removal entry point
     restores the bytes the first write appended and is a no-op when there
     is no record.
  5. Reading is liberal (key case, separators, spacing); a malformed record
     is refused as unreadable, and a re-block over one rewrites a clean
     record keeping what still reads.
  6. promote_blocked_to_backlog on a BLOCKED task carrying a Block Record
     yields BACKLOG with the section byte-identical (the same guarantee the
     ## Transient Retry and ## Zero Progress records already have).

Every fixture lives under pytest's tmp_path; bash runs with an environment
built from an explicit set and a working directory outside the repository.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_record import (
    AGENT_REASON_CODES,
    FIELD_ORDER,
    OWNER_ROLES,
    REASON_CODES,
    BlockRecordError,
    format_evidence,
    load_block_record,
    parse_evidence,
    read_block_record,
    remove_block_record,
    update_block_record,
    write_block_record,
)
from pgai_agent_kanban.task_status import remove_section, set_or_append_section
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/, so three parents up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_BLOCK_RECORD_LIB = _LIB_DIR / "block_record.sh"
_WAKE_TASK_STATE_LIB = _LIB_DIR / "wake_task_state.sh"

_TASK_ID = "CODER-20260101-001-block-record-task"
_T1 = "2026-01-01T10:00:00+00:00"
_T2 = "2026-01-01T11:30:00+00:00"

_BASE_STATUS = (
    "# Status\n\n## Task\n" + _TASK_ID + "\n\n## Role\nCODER\n\n## State\nBLOCKED\n\n"
    "## Summary\nStopped: worktree could not be created.\n\n## Artifacts\nnone\n\n"
    "## Blockers\nworktree add failed  \n\n## Blocked By Agent\ncoder\n\n## Blocked Reason\nresidue\n\n"
    "## Needs Human\nyes\n\n## Transient Retry\nreason_code: none\nretry_count: 0\n\n"
    "## Next Recommended Step\nTBD\n\n## Instruction Conflicts\nnone\n"
)

_RECORD_RE = re.compile(r"^## Block Record[^\n]*\n(?:(?!##)[^\n]*\n?)*", re.M)


def _status_file(tmp_path: pathlib.Path, text: str = _BASE_STATUS) -> pathlib.Path:
    task_dir = tmp_path / "tasks" / _TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / "status.md"
    path.write_bytes(text.encode("utf-8"))
    return path


def _record_section(text: str) -> str:
    """The ``## Block Record`` heading and body exactly as written."""
    m = _RECORD_RE.search(text)
    assert m is not None, f"no ## Block Record section in:\n{text}"
    return m.group(0)


def _without_record(text: str) -> str:
    return remove_section(text, "Block Record")


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"rc={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _bash(tmp_path: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    """Run *script* under bash with the wrapper sourced, from a foreign cwd."""
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "kanban-temp")},
    )
    (tmp_path / "kanban-temp").mkdir(exist_ok=True)
    full = f'set -uo pipefail\nsource "{_BLOCK_RECORD_LIB}"\n' + script
    return subprocess.run(
        ["bash", "-c", full], capture_output=True, text=True, timeout=60, env=env, cwd=str(cwd)
    )


# ---------------------------------------------------------------------------
# 1. First write: shape, order, timestamps, round trip
# ---------------------------------------------------------------------------


def test_first_write_appends_every_field_in_order_and_reads_back(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    before = path.read_text(encoding="utf-8")

    written = write_block_record(
        path, "worktree_create_failed", "CODER", ["worktree=gone", "branch=ai_feature/example", "ahead=2"], now=_T1
    )

    after = path.read_text(encoding="utf-8")
    assert after.startswith(before), "the first write must only append; earlier bytes changed"
    section = _record_section(after)
    lines = section.rstrip("\n").split("\n")
    assert lines[0] == "## Block Record"
    assert [line.split(":", 1)[0] for line in lines[1:]] == list(FIELD_ORDER), section
    assert all(re.match(r"^[a-z_]+: \S", line) for line in lines[1:]), section

    for stamp in (written.first_blocked_at, written.last_blocked_at):
        parsed = datetime.datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None, f"{stamp} must carry a UTC offset"
    assert written.first_blocked_at == written.last_blocked_at == _T1
    assert written.attempts == 0
    assert written.cooldown_until == "none"
    assert written.resume == "fresh"
    assert written.evidence == "worktree=gone; branch=ai_feature/example; ahead=2"

    assert load_block_record(path) == written
    assert read_block_record(after) == written


def test_first_write_without_now_stamps_the_current_utc_time(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    start = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    record = write_block_record(path, "agent_unclassified", "WRITER")
    stamp = datetime.datetime.fromisoformat(record.first_blocked_at)
    assert start <= stamp <= datetime.datetime.now(datetime.timezone.utc)
    assert record.evidence == "none"


# ---------------------------------------------------------------------------
# 2. Re-block lifecycle
# ---------------------------------------------------------------------------


_MIDDLE_STATUS = _BASE_STATUS.replace(
    "## Next Recommended Step",
    "## Block Record\nreason_code: per_task_timeout\nowner_role: CODER\nattempts: 0\n"
    f"first_blocked_at: {_T1}\nlast_blocked_at: {_T1}\ncooldown_until: none\n"
    "evidence: worktree=gone\nresume: fresh\n\n\n## Next Recommended Step",
)


@pytest.mark.parametrize("layout", ["record-appended-at-end", "record-between-sections"])
def test_reblock_keeps_first_blocked_at_and_attempts_and_every_other_section(
    tmp_path: pathlib.Path, layout: str
) -> None:
    if layout == "record-appended-at-end":
        path = _status_file(tmp_path)
        write_block_record(path, "per_task_timeout", "CODER", ["worktree=gone"], now=_T1)
    else:
        path = _status_file(tmp_path, _MIDDLE_STATUS)
    update_block_record(path, attempts=2, cooldown_until="2026-01-01T10:30:00Z", resume="branch")
    before = path.read_text(encoding="utf-8")
    first = load_block_record(path)

    second = write_block_record(path, "gate_dirty_worktree", "WRITER", ["patch=block-evidence/dirty.patch"], now=_T2)

    after = path.read_text(encoding="utf-8")
    assert second.first_blocked_at == first.first_blocked_at == _T1
    assert second.attempts == first.attempts == 2, "a block write must never change attempts"
    assert second.cooldown_until == first.cooldown_until == "2026-01-01T10:30:00+00:00"
    assert second.resume == first.resume == "branch"
    assert second.last_blocked_at == _T2
    assert second.reason_code == "gate_dirty_worktree"
    assert second.owner_role == "WRITER"
    assert second.evidence == "patch=block-evidence/dirty.patch"
    assert load_block_record(path) == second

    # Every byte outside the record section is unchanged, and the record
    # stays where it was.
    assert _without_record(after) == _without_record(before)
    assert after.index("## Block Record") == before.index("## Block Record")
    assert after.count("## Block Record") == 1


def test_a_none_record_body_reads_as_no_record_and_the_next_write_starts_fresh(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path, _BASE_STATUS + "\n## Block Record\nnone\n")
    assert load_block_record(path) is None
    record = write_block_record(path, "zero_progress", "PM", now=_T2)
    assert record.first_blocked_at == _T2 and record.attempts == 0
    assert path.read_text(encoding="utf-8").count("## Block Record") == 1


# ---------------------------------------------------------------------------
# 3. Refusals: unknown reason_code / owner_role via the bash wrapper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,role,error_key",
    [
        ("worktree_vanished", "CODER", "block-record.unknown-reason-code"),
        ("agent_made_up", "CODER", "block-record.unknown-reason-code"),
        ("per_task_timeout", "JANITOR", "block-record.unknown-owner-role"),
    ],
)
def test_wrapper_refuses_unknown_vocabulary_dual_channel_and_writes_nothing(
    tmp_path: pathlib.Path, code: str, role: str, error_key: str
) -> None:
    path = _status_file(tmp_path)
    write_block_record(path, "per_task_timeout", "CODER", ["worktree=gone"], now=_T1)
    before = path.read_bytes()

    result = _bash(tmp_path, f'block_record_write "{path}" "{code}" "{role}" "worktree=gone"\necho "RC=$?"\n')

    assert "RC=1" in result.stdout, _diagnostic(result)
    assert re.search(rf"^error:\s+{re.escape(error_key)}$", result.stderr, re.M), _diagnostic(result)
    assert re.search(r"^message:\s+\S", result.stderr, re.M), _diagnostic(result)
    assert re.search(r"^remedy:\s+\S", result.stderr, re.M), _diagnostic(result)
    assert "<" not in result.stderr.split("remedy:", 1)[1], "remedy must carry no placeholder"
    assert path.read_bytes() == before, "a refused write must leave status.md byte-identical"


def test_wrapper_refusal_on_a_task_without_a_record_adds_nothing(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    before = path.read_bytes()
    result = _bash(tmp_path, f'block_record_write "{path}" bogus CODER\necho "RC=$?"\n')
    assert "RC=1" in result.stdout, _diagnostic(result)
    assert path.read_bytes() == before


def test_wrapper_writes_reads_updates_and_removes_through_the_module(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    before = path.read_bytes()
    script = textwrap.dedent(f"""\
        set -e
        block_record_write --now {_T1} "{path}" gate-merge-not-found coder "branch=ai_feature/example" "merged=no"
        echo "code=$(block_record_field "{path}" reason_code)"
        echo "role=$(block_record_field "{path}" owner_role)"
        echo "evidence=$(block_record_field "{path}" evidence)"
        block_record_update "{path}" --bump-attempts --resume branch
        echo "attempts=$(block_record_field "{path}" attempts)"
        echo "resume=$(block_record_field "{path}" resume)"
        block_record_remove "{path}"
        echo "after-remove=$(block_record_field "{path}" attempts absent)"
    """)
    result = _bash(tmp_path, script)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.splitlines() == [
        "code=gate_merge_not_found",
        "role=CODER",
        "evidence=branch=ai_feature/example; merged=no",
        "attempts=1",
        "resume=branch",
        "after-remove=absent",
    ], _diagnostic(result)
    assert path.read_bytes() == before, "write then remove must give back the original bytes"


def test_codes_lists_the_closed_vocabularies(tmp_path: pathlib.Path) -> None:
    result = _bash(
        tmp_path,
        "pp_run_ops pgai_agent_kanban.overwatch.block_record codes; echo ---; "
        "pp_run_ops pgai_agent_kanban.overwatch.block_record codes --owner-roles\n",
    )
    assert result.returncode == 0, _diagnostic(result)
    codes, roles = result.stdout.split("---\n")
    assert codes.split() == list(REASON_CODES)
    assert roles.split() == ["CODER", "WRITER", "TESTER", "CM", "PM"] == list(OWNER_ROLES)
    assert {"agent_operator_data_action", "agent_unclassified"} <= set(AGENT_REASON_CODES)
    assert all(code.startswith("agent_") for code in AGENT_REASON_CODES)


# ---------------------------------------------------------------------------
# 4. Update and removal entry points
# ---------------------------------------------------------------------------


def test_update_changes_only_the_named_triage_fields(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    first = write_block_record(path, "provider_refusal", "TESTER", ["diag=wake.log:40"], now=_T1)

    bumped = update_block_record(path, bump_attempts=True)
    assert bumped.attempts == 1
    cooled = update_block_record(path, cooldown_until="2026-01-01T12:00:00+02:00")
    assert cooled.cooldown_until == "2026-01-01T10:00:00+00:00"
    assert cooled.attempts == 1
    reset = update_block_record(path, attempts=0, cooldown_until="none", resume="fresh")

    assert reset == first
    assert load_block_record(path) == first


@pytest.mark.parametrize(
    "kwargs,error_key",
    [
        ({}, "block-record.nothing-to-update"),
        ({"attempts": 1, "bump_attempts": True}, "block-record.conflicting-attempts"),
        ({"attempts": -1}, "block-record.bad-attempts"),
        ({"cooldown_until": "soon"}, "block-record.bad-cooldown"),
        ({"resume": "rebase"}, "block-record.bad-resume"),
    ],
)
def test_update_refuses_bad_input_and_writes_nothing(tmp_path: pathlib.Path, kwargs: dict, error_key: str) -> None:
    path = _status_file(tmp_path)
    write_block_record(path, "provider_refusal", "TESTER", now=_T1)
    before = path.read_bytes()
    with pytest.raises(BlockRecordError) as info:
        update_block_record(path, **kwargs)
    assert info.value.error_key == error_key
    assert info.value.remedy.strip()
    assert path.read_bytes() == before


def test_restart_epoch_starts_a_new_epoch_at_the_latest_block_and_keeps_resume(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    write_block_record(path, "per_task_timeout", "CODER", ["branch=ai_feature/x"], now=_T1)
    update_block_record(path, bump_attempts=True, cooldown_until=_T2, resume="branch")
    write_block_record(path, "zero_progress", "CODER", now=_T2)

    restarted = update_block_record(path, restart_epoch=True)

    assert (restarted.first_blocked_at, restarted.last_blocked_at) == (_T2, _T2)
    assert (restarted.attempts, restarted.cooldown_until) == (0, "none")
    assert restarted.resume == "branch", "a resumed branch may hold work; restarting never switches it to fresh"
    assert restarted.reason_code == "zero_progress"
    assert update_block_record(path, restart_epoch=True, bump_attempts=True).attempts == 1


def test_restart_epoch_cli_flag_is_accepted(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    write_block_record(path, "per_task_timeout", "CODER", now=_T1)
    update_block_record(path, attempts=1)
    write_block_record(path, "zero_progress", "CODER", now=_T2)
    result = subprocess.run(
        ["python3", "-m", "pgai_agent_kanban.overwatch.block_record", "update", str(path), "--restart-epoch"],
        capture_output=True, text=True, timeout=60, cwd=str(_TEAM_DIR),
        env=build_scrubbed_env(home_dir=tmp_path, extra_env={"PYTHONPATH": str(_TEAM_DIR)}),
    )
    assert result.returncode == 0, result.stderr
    record = load_block_record(path)
    assert (record.first_blocked_at, record.attempts) == (_T2, 0)


def test_update_without_a_record_is_refused(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    with pytest.raises(BlockRecordError) as info:
        update_block_record(path, bump_attempts=True)
    assert info.value.error_key == "block-record.absent"
    assert path.read_text(encoding="utf-8") == _BASE_STATUS


def test_remove_drops_a_record_between_sections_and_is_a_noop_without_one(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path, _MIDDLE_STATUS)
    assert remove_block_record(path) is True
    text = path.read_text(encoding="utf-8")
    assert "## Block Record" not in text
    assert text == _BASE_STATUS, "the next section must move up into the removed one's place"
    assert remove_block_record(path) is False
    assert path.read_text(encoding="utf-8") == text


def test_missing_status_file_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(BlockRecordError) as info:
        write_block_record(tmp_path / "absent" / "status.md", "zero_progress", "CODER")
    assert info.value.error_key == "block-record.missing-status-file"
    assert not (tmp_path / "absent").exists()


def test_write_keeps_the_status_file_mode(tmp_path: pathlib.Path) -> None:
    path = _status_file(tmp_path)
    path.chmod(0o644)
    write_block_record(path, "zero_progress", "CODER", now=_T1)
    assert path.stat().st_mode & 0o777 == 0o644


# ---------------------------------------------------------------------------
# remove_section — the task_status primitive removal is built on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("# Status\n\n## State\nBLOCKED\n", "# Status\n\n## State\nBLOCKED\n"),
        ("# Status\n\n## State\nBLOCKED", "# Status\n\n## State\nBLOCKED\n"),
        ("## State\nBLOCKED\n\n\n", "## State\nBLOCKED\n"),
    ],
    ids=["trailing-newline", "no-trailing-newline", "trailing-blank-lines"],
)
def test_remove_section_undoes_an_appended_section(text: str, expected: str) -> None:
    appended = set_or_append_section(text, "Block Record", "reason_code: zero_progress")
    assert remove_section(appended, "Block Record") == expected


def test_remove_section_leaves_text_without_the_heading_unchanged() -> None:
    text = "## State\nBLOCKED\n\n## Block Records Archive\nkeep\n"
    assert remove_section(text, "Block Record") == text


# ---------------------------------------------------------------------------
# 5. Liberal reading, unreadable records
# ---------------------------------------------------------------------------


def test_reader_accepts_hand_edited_key_shapes() -> None:
    text = (
        "## State\nBLOCKED\n\n## Block Record\n"
        "Reason-Code :  Per-Task-Timeout\n  owner role:coder\nATTEMPTS: 1 \n"
        "first.blocked.at: 2026-01-01T10:00:00Z\nlast_blocked_at:2026-01-01T11:30:00+00:00\n"
        "cooldown_until: NONE\nevidence: diag=wake.log:12; batch_log=agents/coder.log#10-90\nresume: Branch\n"
    )
    record = read_block_record(text)
    assert record is not None
    assert record.as_dict() == {
        "reason_code": "per_task_timeout",
        "owner_role": "CODER",
        "attempts": 1,
        "first_blocked_at": _T1,
        "last_blocked_at": _T2,
        "cooldown_until": "none",
        "evidence": "diag=wake.log:12; batch_log=agents/coder.log#10-90",
        "resume": "branch",
        "requeues": 0,
        "requeue_reasons": "none",
    }
    assert parse_evidence(record.evidence) == {"diag": "wake.log:12", "batch_log": "agents/coder.log#10-90"}


def test_malformed_record_is_refused_as_unreadable_naming_each_problem() -> None:
    text = "## Block Record\nreason_code: zero_progress\nattempts: two\nfirst_blocked_at: yesterday\n"
    with pytest.raises(BlockRecordError) as info:
        read_block_record(text)
    assert info.value.error_key == "block-record.unreadable"
    for fragment in ("owner_role", "attempts 'two'", "first_blocked_at 'yesterday'"):
        assert fragment in info.value.message


def test_reblock_over_a_malformed_record_keeps_what_still_reads(tmp_path: pathlib.Path) -> None:
    path = _status_file(
        tmp_path,
        _BASE_STATUS + f"\n## Block Record\nreason_code: ???\nattempts: 3\nfirst_blocked_at: {_T1}\nresume: sideways\n",
    )
    record = write_block_record(path, "plugin_load_failed", "CM", now=_T2)
    assert (record.attempts, record.first_blocked_at, record.resume) == (3, _T1, "fresh")
    assert load_block_record(path) == record


def test_format_evidence_keeps_the_record_on_one_line() -> None:
    assert format_evidence(["worktree=/w/x\n", "", "diag=a\nb"]) == "worktree=/w/x; diag=a b"
    assert format_evidence([]) == "none"


# ---------------------------------------------------------------------------
# 6. promote_blocked_to_backlog preserves the record
# ---------------------------------------------------------------------------


def test_backlog_promotion_preserves_block_record_byte_identical(tmp_path: pathlib.Path) -> None:
    """promote_blocked_to_backlog resets State, Blockers, Blocked By Agent,
    Blocked Reason, Needs Human and Summary, and leaves ## Block Record
    exactly as written — the same survival guarantee ## Transient Retry and
    ## Zero Progress have."""
    path = _status_file(tmp_path)
    backlog = tmp_path / "coder_backlog.md"
    backlog.write_text(f"# Coder Backlog\n\n- [B] {_TASK_ID} — blocked task\n", encoding="utf-8")
    write_block_record(path, "per_task_timeout", "CODER", ["worktree=gone", "ahead=2"], now=_T1)
    update_block_record(path, attempts=1, resume="branch")
    before = path.read_text(encoding="utf-8")

    script = textwrap.dedent(f"""\
        BACKLOG="{backlog}"
        TASKS_ROOT="{tmp_path / 'tasks'}"
        log() {{ printf 'LOG: %s\\n' "$*"; }}
        source "{_WAKE_TASK_STATE_LIB}"
        promote_blocked_to_backlog "{_TASK_ID}" "{path}" "operator cleared the blocker"
        echo "RC=$?"
    """)
    result = _bash(tmp_path, script)
    assert "RC=0" in result.stdout, _diagnostic(result)

    after = path.read_text(encoding="utf-8")
    assert re.search(r"^## State\nBACKLOG$", after, re.M), after
    assert re.search(r"^## Needs Human\nno$", after, re.M), after
    assert re.search(r"^## Blocked Reason\nnone$", after, re.M), after
    assert _record_section(after) == _record_section(before)
    assert load_block_record(path).as_dict()["attempts"] == 1
    assert f"- [ ] {_TASK_ID}" in backlog.read_text(encoding="utf-8")
