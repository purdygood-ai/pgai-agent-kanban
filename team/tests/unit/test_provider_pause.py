"""
test_provider_pause.py
======================
Tests for the provider usage-limit pause: pgai_agent_kanban.lib.provider_pause
(recognising the output, reading the reset time, the marker) and the two bash
functions in team/scripts/lib/wake_provider_pause.sh that use it --
wake_record_usage_limit_pause (records the pause and returns the task to
BACKLOG without counting a retry, up to a bound on such requeues, past which
each one is a zero-progress cycle; wake_driver_finalize.sh calls it ahead of
handle_working_after_exit), wake_usage_limit_requeue_reset (clears that
count on real progress) and wake_provider_pause_gate (holds dispatch until
the pause passes, then narrates its end).

The bash functions are exercised as their real bodies, extracted with awk
brace-matching (the pattern test_transient_retry_record.py uses) and run
against a tmp_path kanban root; nothing outside tmp_path is read or written.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.lib import provider_pause
from pgai_agent_kanban.lib.provider_pause import (
    DEFAULT_PAUSE_S,
    UsageLimit,
    check,
    detect,
    marker_path,
    parse_reset,
    record,
)
from tests.fixtures.log_stub import make_log_stub_fragment

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_LIB = _TEAM_DIR / "scripts" / "lib"
_INI_PARSER = _LIB / "ini_parser.sh"
_UTC = datetime.timezone.utc
_NOW = datetime.datetime(2026, 10, 11, 12, 0, tzinfo=_UTC)


def _iso(dt: datetime.datetime) -> str:
    return dt.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ---------------------------------------------------------------------------
# Recognising a usage limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Claude usage limit reached.",
        "Claude AI usage limit reached|1791730800",
        "5-hour limit reached ∙ resets 4pm",
        "You've hit your usage limit. Try again in 2h.",
        "error: usage_limit_exceeded",
        "Weekly limit reached · resets Oct 14, 9am (UTC)",
        "You’ve hit your limit · resets 3pm (America/New_York)",
        "ERROR: You have hit your usage limit.",
        "  API Error: Claude AI usage limit reached|1791730800",
    ],
)
def test_usage_limit_output_is_recognised(line):
    assert detect(f"working...\n{line}\n", _NOW) is not None


@pytest.mark.parametrize(
    "line",
    [
        "API Error: 529 overloaded_error",
        "API Error: 503 Service Unavailable",
        "rate_limit_error: 429 Too Many Requests",
        "insufficient_quota",
        "Your credit balance is too low to access the API",
        "task finished normally",
    ],
)
def test_transient_and_billing_output_is_not_a_usage_limit(line):
    assert detect(f"{line}\n", _NOW) is None


#: An agent's closing message about the usage-limit feature itself: the
#: words are there, the provider's message shape is not.
_AGENT_PROSE = (
    "I updated docs/overwatch.md to describe the usage-limit pause and the\n"
    "[wake] usage_limit_pause_seconds key. Remaining: run the pre-merge check.\n"
    "The wake now pauses on a usage limit only when the CLI prints\n"
    "`Claude AI usage limit reached|<epoch>` or \"5-hour limit reached\" itself.\n"
    "- usage limit reached is no longer matched mid-sentence\n"
    "usage_limit_pause_seconds = 3600\n"
)


@pytest.mark.parametrize("line", _AGENT_PROSE.splitlines())
def test_agent_prose_about_the_usage_limit_is_not_a_usage_limit(line):
    assert detect(f"{line}\n", _NOW) is None


def test_agent_prose_records_no_pause(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text(_AGENT_PROSE, encoding="utf-8")

    result = _cli("record", str(tmp_path), "claude", str(log), "--default-seconds", "3600",
                  "--task", "WRITER-20261011-001-x", "--project", "p", "--now", _iso(_NOW), cwd=tmp_path)

    assert result.returncode == 1 and result.stdout == "", result.stdout + result.stderr
    assert not marker_path(tmp_path, "claude").exists()
    assert not (tmp_path / "provider-pause").exists()


def test_a_provider_line_after_agent_prose_still_counts():
    limit = detect(_AGENT_PROSE + "Claude AI usage limit reached|1791730800\n", _NOW)

    assert limit is not None and limit.source == "stated"
    assert limit.signature == "Claude AI usage limit reached|1791730800"


def test_only_the_log_tail_is_read():
    text = "Claude usage limit reached.\n" + "ordinary line\n" * provider_pause.TAIL_LINES
    assert detect(text, _NOW) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        pytest.param("Claude AI usage limit reached|1791730800", _NOW + datetime.timedelta(hours=3), id="epoch"),
        pytest.param("usage limit; resets 2026-10-11T15:30:00Z", _NOW + datetime.timedelta(hours=3, minutes=30), id="iso"),
        pytest.param("usage limit. Try again in 2h 30m", _NOW + datetime.timedelta(hours=2, minutes=30), id="relative"),
        pytest.param("usage limit, resets in 45 minutes", _NOW + datetime.timedelta(minutes=45), id="relative-words"),
        pytest.param(
            "usage limit reached; your limit will reset at 3pm (America/New_York)",
            datetime.datetime(2026, 10, 11, 19, 0, tzinfo=_UTC),
            id="clock-zone",
        ),
        pytest.param("limit resets 11am (UTC)", datetime.datetime(2026, 10, 12, 11, 0, tzinfo=_UTC), id="clock-next-day"),
        pytest.param("resets Oct 14, 9am (UTC)", datetime.datetime(2026, 10, 14, 9, 0, tzinfo=_UTC), id="date-clock"),
    ],
)
def test_stated_reset_times_are_read(text, expected):
    assert parse_reset(text, _NOW) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("usage limit reached", id="none-stated"),
        pytest.param("Claude AI usage limit reached|1791720000", id="epoch-not-after-now"),
        pytest.param("usage limit; resets 2026-12-25T00:00:00Z", id="too-far-ahead"),
        pytest.param("usage limit; resets at 42", id="bare-number"),
    ],
)
def test_untrusted_or_missing_reset_times_read_as_none(text):
    assert parse_reset(text, _NOW) is None


def test_a_stated_reset_wins_and_the_default_applies_without_one():
    stated = detect("Claude usage limit reached; try again in 2h", _NOW, default_s=600)
    default = detect("Claude usage limit reached.", _NOW, default_s=600)

    assert stated.source == "stated" and stated.resumes_at == _NOW + datetime.timedelta(hours=2)
    assert default.source == "default" and default.resumes_at == _NOW + datetime.timedelta(seconds=600)


# ---------------------------------------------------------------------------
# The marker
# ---------------------------------------------------------------------------


def test_record_writes_the_marker_and_keeps_a_later_one(tmp_path):
    later = UsageLimit("usage limit", _NOW + datetime.timedelta(hours=5), "stated")
    sooner = UsageLimit("usage limit", _NOW + datetime.timedelta(hours=1), "default")

    path, fields = record(tmp_path, "claude", later, _NOW, task="CODER-20261011-001-x", project="demo")
    _, kept = record(tmp_path, "claude", sooner, _NOW)

    assert path == tmp_path / "provider-pause" / "claude.pause"
    text = path.read_text(encoding="utf-8")
    assert "resumes_at=2026-10-11T17:00:00+00:00\n" in text
    assert "source=stated\n" in text and "task=CODER-20261011-001-x\n" in text
    assert kept["resumes_at"] == fields["resumes_at"]


def test_check_reports_paused_then_ended_once_then_clear(tmp_path):
    record(tmp_path, "claude", UsageLimit("usage limit", _NOW + datetime.timedelta(hours=1), "default"), _NOW)

    assert check(tmp_path, "claude", _NOW).state == "paused"
    assert check(tmp_path, "codex", _NOW).state == "clear"
    ended = check(tmp_path, "claude", _NOW + datetime.timedelta(hours=1))
    assert ended.state == "ended" and ended.fields["resumes_at"] == "2026-10-11T13:00:00+00:00"
    assert not marker_path(tmp_path, "claude").exists()
    assert check(tmp_path, "claude", _NOW + datetime.timedelta(hours=2)).state == "clear"


def _cli(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.provider_pause", *args],
        capture_output=True, text=True, timeout=60, cwd=cwd,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_TEAM_DIR), "HOME": str(cwd)},
    )


def test_cli_record_and_check_exit_codes(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text("working\nClaude usage limit reached. Try again in 30 minutes.\n", encoding="utf-8")
    quiet = tmp_path / "quiet.log"
    quiet.write_text("API Error: 503\n", encoding="utf-8")

    recorded = _cli("record", str(tmp_path), "claude", str(log), "--now", _iso(_NOW), cwd=tmp_path)
    nothing = _cli("record", str(tmp_path), "codex", str(quiet), "--now", _iso(_NOW), cwd=tmp_path)
    paused = _cli("check", str(tmp_path), "claude", "--now", _iso(_NOW), cwd=tmp_path)

    assert recorded.returncode == 0, recorded.stderr
    assert "resumes_at=2026-10-11T12:30:00+00:00" in recorded.stdout
    assert "source=stated" in recorded.stdout
    assert nothing.returncode == 1 and nothing.stdout == ""
    assert paused.returncode == 3 and "state=paused" in paused.stdout
    assert _cli("check", str(tmp_path), "claude", "--now", "2026-10-11T12:31:00+00:00", cwd=tmp_path).returncode == 0


def test_cli_refuses_a_provider_name_that_could_escape_the_directory(tmp_path):
    result = _cli("check", str(tmp_path), "../escape", cwd=tmp_path)
    assert result.returncode == 2


def test_kanban_cfg_example_states_the_default_pause():
    result = subprocess.run(
        ["bash", "-c", f"source {str(_INI_PARSER)!r} && read_ini "
         f"{str(_REPO_ROOT / 'kanban.cfg_example')!r} wake usage_limit_pause_seconds ''"],
        capture_output=True, text=True, timeout=15, cwd=_TEAM_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(DEFAULT_PAUSE_S)


# ---------------------------------------------------------------------------
# The wake: handle_working_after_exit and wake_provider_pause_gate
# ---------------------------------------------------------------------------

_FUNCS = [
    "normalize_status_file",
    "set_state",
    "set_block",
    "mark_backlog",
    "transition_task_state",
    "_wake_transition_marker_landed",
    "_wake_transition_restore_state",
    "_wake_transition_arm",
    "_wake_transition_disarm",
    "_wake_transition_on_signal",
    "handle_working_after_exit",
    "wake_usage_limit_requeue_cap",
    "wake_record_usage_limit_pause",
    "wake_usage_limit_requeue_reset",
    "wake_provider_pause_gate",
]

#: The finalize stage's order: the usage-limit pause first, then the
#: transient/zero-progress classifier (wake_driver_finalize.sh).
_CLASSIFY = (
    'if _state="$(wake_record_usage_limit_pause "$1" "$2" "$3")"; then printf "%s\\n" "$_state"; '
    'else handle_working_after_exit "$1" "$2" "$3" 1; fi'
)


def _extract(func_names: list[str]) -> str:
    alternation = "|".join(re.escape(n) for n in func_names)
    files = " ".join(
        f"'{_LIB / name}'" for name in (
            "wake_task_state.sh", "wake_task_transition.sh", "wake_lifecycle.sh", "wake_provider_pause.sh",
        )
    )
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{ depth--; if (depth == 0) {{ found=0 }} }}
                }}
            }}
        ' {files})"
    """)


class _Wake:
    """A tmp_path kanban root with one WORKING task and the real functions."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban"
        self.root.mkdir()
        self.task_id = "CODER-20261011-001-usage-probe"
        task_dir = self.root / "tasks" / self.task_id
        task_dir.mkdir(parents=True)
        self.status = task_dir / "status.md"
        self.status.write_text(
            f"# Status\n\n## Task\n{self.task_id}\n\n## State\nWORKING\n\n## Summary\nIn progress.\n\n"
            "## Blockers\nnone\n\n## Blocked Reason\nnone\n\n## Transient Retry\nnone\n\n"
            "## Needs Human\nno\n",
            encoding="utf-8",
        )
        self.backlog = tmp_path / "coder_backlog.md"
        self.backlog.write_text(f"# Coder Backlog\n\n- [A] {self.task_id}\n", encoding="utf-8")
        self.wake_log = tmp_path / "wake.log"
        self.tmp = tmp_path

    def run(self, body: str, **env: str) -> subprocess.CompletedProcess:
        exports = "".join(f"export {k}={v!r}\n" for k, v in env.items())
        script = textwrap.dedent(f"""\
            set -euo pipefail
            export PGAI_AGENT_KANBAN_TEMP_DIR="{self.tmp / 'scratch'}"
            export KANBAN_ROOT="{self.root}"
            export ACTIVE_PROVIDER=claude
            unset PGAI_PROJECT_NAME
            source "{_LIB}/overwatch_protocol.sh"
            source "{_LIB}/error_contract.sh"
            source "{_LIB}/atomic_write.sh"
            source "{_LIB}/temp.sh"
            source "{_LIB}/pp_run_ops.sh"
            BACKLOG="{self.backlog}"
        """) + make_log_stub_fragment(self.wake_log) + "\n" + _extract(_FUNCS) + exports + body
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, cwd=_TEAM_DIR)

    def agent_log(self, text: str) -> pathlib.Path:
        path = self.tmp / "agent.log"
        path.write_text(f"Agent started.\n{text}\n", encoding="utf-8")
        return path

    def marker(self) -> dict[str, str]:
        path = self.root / "provider-pause" / "claude.pause"
        return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())

    def section(self, heading: str) -> str:
        m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=\n##|\Z)", self.status.read_text(encoding="utf-8"), re.S | re.M)
        return m.group(1).strip() if m else ""


def test_usage_limit_with_a_reset_time_pauses_and_requeues_without_a_retry(tmp_path):
    wake = _Wake(tmp_path)
    reset = (datetime.datetime.now(_UTC) + datetime.timedelta(hours=3)).replace(microsecond=0)
    log = wake.agent_log(f"Claude usage limit reached; resets {_iso(reset)}")

    result = wake.run(f'classify() {{ {_CLASSIFY}; }}\nclassify "{wake.task_id}" "{wake.status}" "{log}"\n')

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "BACKLOG"
    assert wake.section("State") == "BACKLOG"
    assert f"- [ ] {wake.task_id}" in wake.backlog.read_text(encoding="utf-8")
    assert wake.section("Transient Retry") == "none", "a usage limit must not count a transient retry"
    assert not re.search(r"^## Zero Progress", wake.status.read_text(encoding="utf-8"), re.M)
    marker = wake.marker()
    assert marker["resumes_at"] == _iso(reset) and marker["source"] == "stated"
    assert marker["task"] == wake.task_id
    narration = wake.wake_log.read_text(encoding="utf-8")
    assert f"fleet-wide dispatch pause recorded until {_iso(reset)}" in narration


def test_usage_limit_without_a_reset_time_uses_the_configured_default(tmp_path):
    wake = _Wake(tmp_path)
    log = wake.agent_log("Claude usage limit reached.")
    before = datetime.datetime.now(_UTC).replace(microsecond=0)

    result = wake.run(
        f'classify() {{ {_CLASSIFY}; }}\nclassify "{wake.task_id}" "{wake.status}" "{log}"\n',
        USAGE_LIMIT_PAUSE_SECONDS="900",
    )

    assert result.returncode == 0, result.stderr
    marker = wake.marker()
    resumes = datetime.datetime.fromisoformat(marker["resumes_at"])
    assert marker["source"] == "default"
    assert before + datetime.timedelta(seconds=900) <= resumes <= before + datetime.timedelta(seconds=960)


def test_a_transient_error_is_still_a_counted_retry_and_records_no_pause(tmp_path):
    wake = _Wake(tmp_path)
    log = wake.agent_log("API Error: 529 overloaded_error")

    result = wake.run(f'classify() {{ {_CLASSIFY}; }}\nclassify "{wake.task_id}" "{wake.status}" "{log}"\n')

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "BLOCKED"
    assert "retry_count: 1" in wake.section("Transient Retry")
    assert not (wake.root / "provider-pause").exists()


def test_the_gate_holds_dispatch_while_paused_and_narrates_the_end(tmp_path):
    wake = _Wake(tmp_path)
    log = wake.agent_log("Claude usage limit reached; try again in 2h")
    wake.run(f'wake_record_usage_limit_pause "{wake.task_id}" "{wake.status}" "{log}" >/dev/null\n')
    until = wake.marker()["resumes_at"]

    held = wake.run('if wake_provider_pause_gate demo; then echo "gate=open"; else echo "gate=held until=${WAKE_PROVIDER_PAUSED_UNTIL}"; fi\n')
    other = wake.run('ACTIVE_PROVIDER=codex; wake_provider_pause_gate demo && echo "gate=open"\n')

    assert f"gate=held until={until}" in held.stdout, held.stdout + held.stderr
    assert "error:   wake.dispatch_paused_usage_limit" in held.stderr
    assert f"not dispatching any task until {until}" in wake.wake_log.read_text(encoding="utf-8")
    assert "gate=open" in other.stdout

    # Once the reset time has passed the gate opens, narrates the end once,
    # and removes the marker.
    path = wake.root / "provider-pause" / "claude.pause"
    path.write_text(path.read_text(encoding="utf-8").replace(f"resumes_at={until}", "resumes_at=2026-01-01T00:00:00+00:00"), encoding="utf-8")
    opened = wake.run('wake_provider_pause_gate demo && echo "gate=open"\n')
    again = wake.run('wake_provider_pause_gate demo && echo "gate=open"\n')

    assert "gate=open" in opened.stdout and "gate=open" in again.stdout
    assert not path.exists()
    assert wake.wake_log.read_text(encoding="utf-8").count("usage-limit pause ended") == 1


def test_agent_prose_in_the_log_leaves_the_task_to_the_normal_classifier(tmp_path):
    wake = _Wake(tmp_path)
    log = wake.agent_log(_AGENT_PROSE.rstrip("\n"))

    result = wake.run(f'classify() {{ {_CLASSIFY}; }}\nclassify "{wake.task_id}" "{wake.status}" "{log}"\n')

    assert result.returncode == 0, result.stderr
    assert not (wake.root / "provider-pause").exists(), "agent prose must not record a pause"
    assert "count: 1" in wake.section("Zero Progress"), "the attempt is an ordinary zero-progress cycle"
    assert wake.section("Usage Limit Requeue") == ""


def _redispatch(wake: "_Wake") -> None:
    """Put the task back in WORKING, as the next wake's dispatch does."""
    text = re.sub(r"^## State\s*\n\S+", "## State\nWORKING", wake.status.read_text(encoding="utf-8"), flags=re.M)
    wake.status.write_text(text, encoding="utf-8")
    wake.backlog.write_text(f"# Coder Backlog\n\n- [A] {wake.task_id}\n", encoding="utf-8")


#: Usage-limit requeues that count no retry (wake_provider_pause.sh's
#: wake_usage_limit_requeue_cap, documented in kanban.cfg_example).
_REQUEUE_CAP = 3


def test_repeated_usage_limit_requeues_stop_being_silent_after_the_cap(tmp_path):
    wake = _Wake(tmp_path)
    log = wake.agent_log("Claude usage limit reached.")
    classify = f'classify() {{ {_CLASSIFY}; }}\nclassify "{wake.task_id}" "{wake.status}" "{log}"\n'

    for attempt in range(1, _REQUEUE_CAP + 1):
        _redispatch(wake)
        result = wake.run(classify, ZERO_PROGRESS_CAP="2")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines()[-1] == "BACKLOG"
        assert f"count: {attempt}" in wake.section("Usage Limit Requeue")
        assert f"limit: {_REQUEUE_CAP}" in wake.section("Usage Limit Requeue")
        assert not re.search(r"^## Zero Progress", wake.status.read_text(encoding="utf-8"), re.M)
        assert f"usage-limit requeue {attempt}/{_REQUEUE_CAP}" in wake.section("Summary")

    # Past the cap: the pause is still recorded, but the attempt now moves
    # ## Zero Progress -- BACKLOG below zero_progress_cap ...
    (wake.root / "provider-pause" / "claude.pause").unlink()
    _redispatch(wake)
    over = wake.run(classify, ZERO_PROGRESS_CAP="2")

    assert over.returncode == 0, over.stderr
    assert over.stdout.strip().splitlines()[-1] == "BACKLOG"
    assert (wake.root / "provider-pause" / "claude.pause").exists()
    assert f"count: {_REQUEUE_CAP + 1}" in wake.section("Usage Limit Requeue")
    zero = wake.section("Zero Progress")
    assert "reason_code: usage_limit_requeue" in zero and "count: 1" in zero
    assert "counting this attempt as a zero-progress cycle" in wake.wake_log.read_text(encoding="utf-8")

    # ... and BLOCKED with Needs Human at it: the loop ends.
    _redispatch(wake)
    blocked = wake.run(classify, ZERO_PROGRESS_CAP="2")

    assert blocked.returncode == 0, blocked.stderr
    assert blocked.stdout.strip().splitlines()[-1] == "BLOCKED"
    assert wake.section("State") == "BLOCKED"
    assert wake.section("Needs Human") == "yes"
    assert "count: 2" in wake.section("Zero Progress")


def test_real_progress_clears_the_usage_limit_requeue_count(tmp_path):
    wake = _Wake(tmp_path)
    log = wake.agent_log("Claude usage limit reached.")
    untouched = wake.status.read_text(encoding="utf-8")

    noop = wake.run(f'wake_usage_limit_requeue_reset "{wake.status}"\n')
    assert noop.returncode == 0, noop.stderr
    assert wake.status.read_text(encoding="utf-8") == untouched, "no record, nothing written"

    for _ in range(2):
        _redispatch(wake)
        wake.run(f'wake_record_usage_limit_pause "{wake.task_id}" "{wake.status}" "{log}" >/dev/null\n')
    assert "count: 2" in wake.section("Usage Limit Requeue")

    reset = wake.run(f'wake_usage_limit_requeue_reset "{wake.status}"\n')

    assert reset.returncode == 0, reset.stderr
    assert wake.section("Usage Limit Requeue") == "none"
