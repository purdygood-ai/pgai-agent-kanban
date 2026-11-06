"""
test_inbox_surfaces.py
======================
Golden and behaviour tests for the three surfaces that show a project's
operator inbox (``projects/<name>/operator-inbox.md``), all read through
``pgai_agent_kanban.metrics.inbox_view.read_summary``:

  * the dashboard attention pane (``scripts/dashboard/attention.sh``, whose
    OPERATOR INBOX block ``dashboard.scan_attention.scan_inbox_section``
    prints);
  * ``scripts/metrics-report.sh --inbox`` (every project, or one with
    ``--project``);
  * ``scripts/metrics-report.sh --day <YYYY-MM-DD>``, whose JSON carries the
    project's open count as ``"inbox_open"``.

The sandbox root is the dashboard render fixture
(``tests/fixtures/dashboard_render_fixture.py``) with three projects
registered: ``alpha`` has two open entries and one closed one, ``beta`` has
only a closed entry, and ``gamma`` has no inbox file. Entries are written
through the inbox module with pinned dates, so the newest open question is
fixed.

Covered behaviour:

  * the attention pane's OPERATOR INBOX block matches the checked-in golden
    (alpha's open count 2 and one line per open entry, newest first; beta
    and gamma absent);
  * the three entries the decision wake writes when an action does not run
    -- a verb whose executor raised, a verb ``[overwatch] block_triage =
    false`` keeps unrun while its task stays BLOCKED, and a runaway
    threshold that is not a whole number -- each give exactly one pane line,
    and the block matches its own checked-in golden (entry dates, which the
    decision wake stamps with the wall clock, read ``{DATE}`` there);
  * with no open entry anywhere the block is absent, and the rest of the
    pane is byte-identical whether the inbox holds closed entries, open
    ones, or no file at all;
  * the scanner shows an unreadable inbox rather than hiding it, cuts a long
    question, and prints nothing for an empty kanban;
  * every ``metrics-report.sh --inbox`` case (every project, one project,
    the refusals) matches the checked-in golden outcome, and ``--help``
    lists ``--inbox``;
  * ``metrics-report.sh --day`` prints the stored rollup with
    ``"inbox_open"`` added: 2 for alpha, 0 for gamma, ``null`` for an
    unreadable inbox; a day file that is not a JSON object prints as stored.

Every tree is built under pytest's tmp_path; subprocesses run with an
environment built from an explicit set (``build_scrubbed_env``) whose kanban
root, project root and temp dir all name the sandbox, and a throwaway HOME.
Tree paths are replaced by ``{ROOT}`` and ``{TEAM}`` in the goldens. To
re-record after a deliberate change, run this file directly with
``--record`` from team/: ``python3 tests/unit/test_inbox_surfaces.py --record``
(it rewrites all three goldens).
"""

from __future__ import annotations

import datetime
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from unittest import mock

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.scan_attention import scan_inbox_section  # noqa: E402
from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402
from pgai_agent_kanban.metrics.inbox_view import (  # noqa: E402
    DAY_KEY,
    count_lines,
    day_report_text,
)
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.overwatch import decision_executor as dx  # noqa: E402
from pgai_agent_kanban.overwatch import decision_wake as dw  # noqa: E402
from pgai_agent_kanban.overwatch.block_record import write_block_record  # noqa: E402
from pgai_agent_kanban.overwatch.inbox import close_entry, list_open, write_entry  # noqa: E402
from pgai_agent_kanban.task_status import set_or_append_section  # noqa: E402
from tests.fixtures.dashboard_render_fixture import (  # noqa: E402
    build_dashboard_project_root,
    run_dashboard_script,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_ATTENTION_SH = _TEAM_DIR / "scripts" / "dashboard" / "attention.sh"
_METRICS_REPORT_SH = _TEAM_DIR / "scripts" / "metrics-report.sh"
_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "golden"
PANE_GOLDEN = _GOLDEN_DIR / "attention_pane_inbox.txt"
CLI_GOLDEN = _GOLDEN_DIR / "metrics_report_inbox_cli.json"
FAILURES_GOLDEN = _GOLDEN_DIR / "attention_pane_decision_wake_failures.txt"

PROJECTS = ("alpha", "beta", "gamma")
DAY = "2026-10-18"
INBOX_HEADER = "? OPERATOR INBOX"
NEXT_HEADER = "! PENDING APPROVALS"
_TIMEOUT_SECONDS = 60

#: The decision-wake fixture's project. Long enough that the pane's
#: 100-character cut of the runaway question falls before the kanban.cfg
#: path in it, so the golden holds no tree path.
WAKE_PROJECT = "decision-wake-fixture"
WAKE_NOW = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)
WAKE_BLOCKED_AT = "2026-01-01T11:00:00+00:00"
FAILED_VERB_TASK = "CODER-20260101-001-reset-raises"
DECLINED_TASK = "CODER-20260101-002-left-blocked"
_ISO_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")

#: label -> metrics-report.sh arguments.
CLI_CASES: dict[str, tuple[str, ...]] = {
    "every registered project": ("--inbox",),
    "one project with open entries": ("--inbox", "--project", "alpha"),
    "one project with only closed entries": ("--inbox", "--project", "beta"),
    "one project without an inbox file": ("--inbox", "--project", "gamma"),
    "unknown project": ("--inbox", "--project", "nope"),
    "combined with day": ("--inbox", "--day", DAY, "--project", "alpha"),
    "combined with reliability": ("--inbox", "--reliability"),
    "combined with format": ("--inbox", "--format", "json"),
}


def _at(day: int, hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 10, day, hour, tzinfo=datetime.UTC)


def _ask(ctx: OpsContext, project: str, question: str, when: datetime.datetime, source: str) -> str:
    return write_entry(
        ctx,
        project,
        source=source,
        question=question,
        meanwhile="Left the RC open; the rest of the queue kept running.",
        how_to_answer='Answer "cancel" or "keep" with the inbox answer command.',
        now=when,
    ).entry_id


def build_root(base: pathlib.Path) -> pathlib.Path:
    """Lay out the sandbox kanban root described in the module docstring
    under *base*, with no inbox written yet; returns the root."""
    root = build_dashboard_project_root(base, project_name="alpha").kanban_root
    (root / "tmp").mkdir(exist_ok=True)
    with (root / "projects.cfg").open("a", encoding="utf-8") as cfg:
        cfg.write("\n[project:beta]\npriority=2\n\n[project:gamma]\npriority=3\n")
    for name in PROJECTS:
        (root / "projects" / name / "metrics" / "day").mkdir(parents=True, exist_ok=True)
        (root / "projects" / name / "metrics" / "day" / f"{DAY}.json").write_text(
            json.dumps({"date": DAY, "project": name, "rcs_included": ["v1.2.0"]}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return root


def write_inboxes(root: pathlib.Path) -> None:
    """alpha: two open entries and one closed; beta: one closed; gamma: none."""
    ctx = OpsContext(kanban_root=root)
    _ask(ctx, "alpha", "Cancel the RC or keep it open for the late fix?", _at(17, 9), "rc:v1.2.0")
    _ask(ctx, "alpha", "Requeue the docs task once the provider is back?", _at(18, 10), "WRITER-20261018-004-docs")
    closed = _ask(ctx, "alpha", "Which release carries the hotfix?", _at(18, 11), "rc:v1.2.0")
    close_entry(ctx, "alpha", closed, now=_at(18, 12))
    old = _ask(ctx, "beta", "Keep the stale bundle?", _at(16, 8), "rc:v0.4.0")
    close_entry(ctx, "beta", old, now=_at(16, 9))


def _env(root: pathlib.Path, home: pathlib.Path) -> dict:
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "NO_COLOR": "1",
            "TERM": "dumb",
        },
    )


def run_pane(root: pathlib.Path, home: pathlib.Path) -> subprocess.CompletedProcess:
    """attention.sh over *root*, colour off."""
    return run_dashboard_script(
        ["bash", str(_ATTENTION_SH), "--kanban-root", str(root)],
        env=_env(root, home),
        timeout=_TIMEOUT_SECONDS,
    )


def run_report(root: pathlib.Path, home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """metrics-report.sh over *root* with *args*."""
    return subprocess.run(
        ["bash", str(_METRICS_REPORT_SH), "--kanban-root", str(root), *args],
        capture_output=True,
        text=True,
        env=_env(root, home),
        cwd=str(root),
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )


def inbox_block(stdout: str) -> str:
    """The OPERATOR INBOX block of the pane: its header line through the
    scanner's closing blank line (the PENDING APPROVALS section prints its
    own blank line ahead of its header); ``""`` when absent."""
    lines = stdout.splitlines(keepends=True)
    headers = [line.rstrip("\n") for line in lines]
    if INBOX_HEADER not in headers:
        return ""
    start = headers.index(INBOX_HEADER)
    end = headers.index(NEXT_HEADER, start) - 1
    return "".join(lines[start:end])


def without_inbox_block(stdout: str) -> str:
    """The pane's stdout with the OPERATOR INBOX block, and the blank line
    the scanner prints ahead of it, taken out."""
    block = inbox_block(stdout)
    return stdout.replace("\n" + block, "", 1) if block else stdout


def cli_outcome(root: pathlib.Path, result: subprocess.CompletedProcess) -> dict:
    """One CLI run as a golden outcome, tree paths as placeholders."""

    def scrub(text: str) -> str:
        return text.replace(str(root), "{ROOT}").replace(str(_TEAM_DIR), "{TEAM}")

    return {"exit_code": result.returncode, "stdout": scrub(result.stdout), "stderr": scrub(result.stderr)}


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


@pytest.fixture
def home(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def bare_root(tmp_path: pathlib.Path) -> pathlib.Path:
    return build_root(tmp_path)


@pytest.fixture
def root(bare_root: pathlib.Path) -> pathlib.Path:
    write_inboxes(bare_root)
    return bare_root


# ---------------------------------------------------------------------------
# The attention pane
# ---------------------------------------------------------------------------


def test_pane_inbox_block_matches_the_golden(root: pathlib.Path, home: pathlib.Path) -> None:
    result = run_pane(root, home)
    assert result.returncode == 0, _diagnostic(result)
    block = inbox_block(result.stdout)
    assert block == PANE_GOLDEN.read_text(encoding="utf-8"), _diagnostic(result)


def test_golden_block_shows_the_open_count_and_every_open_entry_newest_first() -> None:
    block = PANE_GOLDEN.read_text(encoding="utf-8")
    assert "  alpha: 2 open\n" in block
    lines = block.splitlines()
    newer = [i for i, line in enumerate(lines) if "Requeue the docs task once the provider is back?" in line]
    older = [i for i, line in enumerate(lines) if "Cancel the RC or keep it open for the late fix?" in line]
    assert len(newer) == 1 and len(older) == 1, block  # one line per open entry
    assert newer[0] < older[0], "newest first"
    assert "INBOX-0002" in lines[newer[0]] and "(WRITER-20261018-004-docs)" in lines[newer[0]]
    assert "Which release carries the hotfix?" not in block  # closed
    assert "beta" not in block and "gamma" not in block


def test_no_open_entry_hides_the_block_and_leaves_the_pane_unchanged(
    bare_root: pathlib.Path, home: pathlib.Path
) -> None:
    no_inbox = run_pane(bare_root, home)
    assert no_inbox.returncode == 0, _diagnostic(no_inbox)
    assert INBOX_HEADER not in no_inbox.stdout

    # Only closed entries: still no block, and the pane is byte-identical.
    ctx = OpsContext(kanban_root=bare_root)
    for project in ("alpha", "beta"):
        close_entry(ctx, project, _ask(ctx, project, "Closed already?", _at(15, 8), "rc:v1.0.0"), now=_at(15, 9))
    only_closed = run_pane(bare_root, home)
    assert only_closed.returncode == 0, _diagnostic(only_closed)
    assert only_closed.stdout == no_inbox.stdout

    # Open entries: the block appears, and everything else is byte-identical.
    _ask(ctx, "alpha", "Keep the RC open?", _at(18, 10), "rc:v1.2.0")
    _ask(ctx, "alpha", "Requeue the docs task?", _at(18, 11), "rc:v1.2.0")
    with_open = run_pane(bare_root, home)
    assert with_open.returncode == 0, _diagnostic(with_open)
    assert "  alpha: 2 open\n" in inbox_block(with_open.stdout)
    assert without_inbox_block(with_open.stdout) == no_inbox.stdout


def _scan(root: pathlib.Path, use_color: bool = False) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        scan_inbox_section(str(root), use_color=use_color)
    return out.getvalue()


def test_scanner_prints_nothing_for_a_kanban_without_projects(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects").mkdir()
    assert _scan(tmp_path) == ""


def test_scanner_shows_an_unreadable_inbox(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "delta").mkdir(parents=True)
    (tmp_path / "projects" / "delta" / "operator-inbox.md").write_bytes(b"## Open\n\xff\xfe\n")
    output = _scan(tmp_path)
    assert INBOX_HEADER in output
    assert "  delta: inbox unreadable\n" in output


def test_scanner_cuts_a_long_question_and_colours_on_request(tmp_path: pathlib.Path) -> None:
    (tmp_path / "projects" / "delta").mkdir(parents=True)
    ctx = OpsContext(kanban_root=tmp_path)
    _ask(ctx, "delta", "x" * 150 + "\nsecond line", _at(18, 9), "rc:v2.0.0")
    plain = _scan(tmp_path)
    assert f"(rc:v2.0.0)  {'x' * 100}…\n" in plain
    assert "second line" not in plain
    coloured = _scan(tmp_path, use_color=True)
    assert "\U0001f4e5 OPERATOR INBOX" in coloured and "\033[" in coloured


# ---------------------------------------------------------------------------
# Decision-wake failures on the attention pane
# ---------------------------------------------------------------------------


def _block_task(project_dir: pathlib.Path, task_id: str, code: str) -> None:
    """A task as a block site leaves it: BLOCKED, Needs Human yes, a Block
    Record with *code*, and a [B] line in the CODER queue."""
    task_dir = project_dir / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "README.md").write_text(f"# Task\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n", encoding="utf-8")
    status = task_dir / "status.md"
    text = f"# Status\n\n## Task\n{task_id}\n\n## State\nWORKING\n\n## Summary\nWorking.\n"
    for heading, body in (("State", "BLOCKED"), ("Needs Human", "yes"), ("Blockers", f"{code} block")):
        text = set_or_append_section(text, heading, body)
    status.write_text(text, encoding="utf-8")
    write_block_record(status, code, "CODER", [], now=WAKE_BLOCKED_AT)
    with (project_dir / "tasks" / "queues" / "coder_backlog.md").open("a", encoding="utf-8") as queue:
        queue.write(f"- [B] {task_id} — fixture\n")


def _executor_raises(_run: object, verb: str) -> None:
    raise RuntimeError(f"the {verb} runner broke")


def build_decision_wake_root(base: pathlib.Path) -> pathlib.Path:
    """A one-project dashboard root whose decision wake has written the three
    failure entries: a verb whose executor raised (FAILED_VERB_TASK), a verb
    ``block_triage = false`` kept unrun while DECLINED_TASK stays BLOCKED,
    and ``runaway_releases_per_hour = abc`` in kanban.cfg. Returns the root."""
    root = build_dashboard_project_root(base, project_name=WAKE_PROJECT, subdir="decision_wake_root").kanban_root
    (root / "tmp").mkdir(exist_ok=True)
    with (root / "kanban.cfg").open("a", encoding="utf-8") as cfg:
        cfg.write("\n[overwatch]\nrunaway_releases_per_hour = abc\n")
    project_dir = root / "projects" / WAKE_PROJECT
    sandbox = {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    with mock.patch.dict(os.environ, sandbox):
        for name in (
            "PGAI_TASKS_DIR",
            "PGAI_PROJECT_ROOT",
            "PGAI_PROJECT_NAME",
            "HALT_OVERWATCH",
            "OVERWATCH_FIRING_TIMESTAMP",
            "OVERWATCH_LOCK_WAIT_SECONDS",
        ):
            os.environ.pop(name, None)
        _block_task(project_dir, FAILED_VERB_TASK, "invalid_state")
        with mock.patch.dict(dx._VERB_RUNNERS, {"reset-to-backlog": _executor_raises}):
            dw.run_project(root, WAKE_PROJECT, now=WAKE_NOW)
        _block_task(project_dir, DECLINED_TASK, "zero_progress")
        dw.run_project(root, WAKE_PROJECT, now=WAKE_NOW, triage_enabled=False)
    return root


def failures_block(stdout: str) -> str:
    """The pane's OPERATOR INBOX block with every ISO-8601 stamp read as {DATE}."""
    return _ISO_STAMP.sub("{DATE}", inbox_block(stdout))


@pytest.fixture
def wake_root(tmp_path: pathlib.Path) -> pathlib.Path:
    return build_decision_wake_root(tmp_path)


def test_each_decision_wake_failure_gets_exactly_one_pane_line(wake_root: pathlib.Path, home: pathlib.Path) -> None:
    entries = list_open(OpsContext(kanban_root=wake_root), WAKE_PROJECT)
    assert len(entries) == 3, entries
    by_source = {}
    for entry in entries:
        by_source.setdefault(entry.source, []).append(entry)
    assert sorted(by_source) == sorted([FAILED_VERB_TASK, DECLINED_TASK, WAKE_PROJECT]), by_source

    result = run_pane(wake_root, home)
    assert result.returncode == 0, _diagnostic(result)
    block = inbox_block(result.stdout)
    assert f"  {WAKE_PROJECT}: 3 open\n" in block, block
    pane_lines = result.stdout.splitlines()
    for entry in entries:
        # One line on the whole pane carries the entry, and it names the source.
        carrying = [line for line in pane_lines if entry.entry_id in line]
        assert len(carrying) == 1, (entry.entry_id, carrying)
        assert f"({entry.source})" in carrying[0], carrying[0]
    failed, declined, detector = (by_source[k][0] for k in (FAILED_VERB_TASK, DECLINED_TASK, WAKE_PROJECT))
    assert "verb reset-to-backlog" in _line_for(pane_lines, failed) and "did not run" in _line_for(pane_lines, failed)
    assert "verb requeue" in _line_for(pane_lines, declined) and "did not run" in _line_for(pane_lines, declined)
    assert "runaway detector is off" in _line_for(pane_lines, detector)
    assert "runaway_releases_per_hour" in _line_for(pane_lines, detector)


def _line_for(pane_lines: list, entry: object) -> str:
    return next(line for line in pane_lines if entry.entry_id in line)


def test_decision_wake_failures_block_matches_the_golden(wake_root: pathlib.Path, home: pathlib.Path) -> None:
    result = run_pane(wake_root, home)
    assert result.returncode == 0, _diagnostic(result)
    assert failures_block(result.stdout) == FAILURES_GOLDEN.read_text(encoding="utf-8"), _diagnostic(result)


def test_failures_golden_holds_one_line_per_entry_and_no_tree_path() -> None:
    block = FAILURES_GOLDEN.read_text(encoding="utf-8")
    entry_lines = [line for line in block.splitlines() if re.match(r"^    INBOX-\d{4}  ", line)]
    assert len(entry_lines) == 3, block
    assert sum(f"({FAILED_VERB_TASK})" in line for line in entry_lines) == 1
    assert sum(f"({DECLINED_TASK})" in line for line in entry_lines) == 1
    assert sum(f"({WAKE_PROJECT})" in line for line in entry_lines) == 1
    assert "/tmp" not in block and "pytest" not in block


# ---------------------------------------------------------------------------
# metrics-report.sh --inbox
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(CLI_CASES))
def test_cli_case_matches_the_golden(root: pathlib.Path, home: pathlib.Path, label: str) -> None:
    result = run_report(root, home, *CLI_CASES[label])
    expected = json.loads(CLI_GOLDEN.read_text(encoding="utf-8"))[label]
    assert cli_outcome(root, result) == expected, _diagnostic(result)


def test_every_cli_case_is_recorded() -> None:
    assert set(json.loads(CLI_GOLDEN.read_text(encoding="utf-8"))) == set(CLI_CASES)


def test_golden_cli_counts_are_the_open_entries() -> None:
    golden = json.loads(CLI_GOLDEN.read_text(encoding="utf-8"))
    assert golden["every registered project"]["stdout"] == "  alpha: 2 open\n  beta: 0 open\n  gamma: 0 open\n"
    assert golden["one project with open entries"]["stdout"] == "  alpha: 2 open\n"
    for label in ("unknown project", "combined with day", "combined with reliability", "combined with format"):
        assert golden[label]["exit_code"] == 1
        assert golden[label]["stderr"].startswith("error:   metrics_report.inbox_"), label
        assert "<" not in golden[label]["stderr"].split("remedy:", 1)[1], label


def test_help_lists_inbox(root: pathlib.Path, home: pathlib.Path) -> None:
    result = run_report(root, home, "--help")
    assert result.returncode == 0, _diagnostic(result)
    assert "metrics-report.sh --inbox " in result.stdout
    assert "metrics-report.sh --inbox --project <name>" in result.stdout
    assert "  --inbox " in result.stdout


def test_count_lines_without_projects() -> None:
    assert count_lines("/nonexistent", []) == ["  (no projects registered)"]


# ---------------------------------------------------------------------------
# metrics-report.sh --day
# ---------------------------------------------------------------------------


def _day_file(root: pathlib.Path, project: str) -> pathlib.Path:
    return root / "projects" / project / "metrics" / "day" / f"{DAY}.json"


@pytest.mark.parametrize("project, expected", [("alpha", 2), ("beta", 0), ("gamma", 0)])
def test_day_report_carries_the_open_inbox_count(
    root: pathlib.Path, home: pathlib.Path, project: str, expected: int
) -> None:
    result = run_report(root, home, "--day", DAY, "--project", project)
    assert result.returncode == 0, _diagnostic(result)
    report = json.loads(result.stdout)
    assert report[DAY_KEY] == expected
    stored = json.loads(_day_file(root, project).read_text(encoding="utf-8"))
    assert DAY_KEY not in stored  # added when printing, never stored
    assert {k: v for k, v in report.items() if k != DAY_KEY} == stored
    # Written in the stored file's own shape: only the added line differs.
    assert result.stdout == day_report_text(_day_file(root, project).read_text(encoding="utf-8"), expected)


def test_day_report_with_an_unreadable_inbox_carries_null(root: pathlib.Path, home: pathlib.Path) -> None:
    (root / "projects" / "gamma" / "operator-inbox.md").write_bytes(b"## Open\n\xff\n")
    result = run_report(root, home, "--day", DAY, "--project", "gamma")
    assert result.returncode == 0, _diagnostic(result)
    assert json.loads(result.stdout)[DAY_KEY] is None


def test_day_file_that_is_not_an_object_prints_as_stored(root: pathlib.Path, home: pathlib.Path) -> None:
    _day_file(root, "alpha").write_text("[1, 2]\n", encoding="utf-8")
    result = run_report(root, home, "--day", DAY, "--project", "alpha")
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout == "[1, 2]\n"
    assert "not a JSON object" in result.stderr


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record() -> None:
    """Rewrite all three goldens from the current scripts."""
    scratch_root = pathlib.Path(resolve_temp_root())
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="inbox-golden-", dir=scratch_root) as scratch:
        base = pathlib.Path(scratch)
        sandbox = build_root(base)
        write_inboxes(sandbox)
        home_dir = base / "home"
        home_dir.mkdir()
        pane = run_pane(sandbox, home_dir)
        PANE_GOLDEN.write_text(inbox_block(pane.stdout), encoding="utf-8")
        outcomes = {
            label: cli_outcome(sandbox, run_report(sandbox, home_dir, *args))
            for label, args in sorted(CLI_CASES.items())
        }
        CLI_GOLDEN.write_text(json.dumps(outcomes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        wake_base = base / "wake"
        wake_base.mkdir()
        wake_root = build_decision_wake_root(wake_base)
        FAILURES_GOLDEN.write_text(failures_block(run_pane(wake_root, home_dir).stdout), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        print("usage: python3 tests/unit/test_inbox_surfaces.py --record", file=sys.stderr)
        sys.exit(2)
    record()
