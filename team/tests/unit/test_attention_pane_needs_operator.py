"""
test_attention_pane_needs_operator.py
=====================================
Golden and behaviour tests for the attention pane's NEEDS OPERATOR section
(``scripts/dashboard/attention.sh``, printed by
``dashboard.scan_attention.scan_needs_operator_section``): each project's
count of bug and priority items at ``needs-operator`` and the newest open
operator-inbox question that names one of them.

The sandbox root is the dashboard render fixture
(``tests/fixtures/dashboard_render_fixture.py``) with two projects
registered. ``alpha`` has two bugs parked at needs-operator through
``ops.write.set_needs_operator`` (which writes each question to the inbox
with a pinned date), one open bug, and a newer inbox question about an RC
that names no item. ``beta`` has one open bug and nothing parked.

Covered behaviour:

  * the pane's NEEDS OPERATOR block matches the checked-in golden: alpha's
    count 2 and its newest needs-operator question -- not the newer RC
    question -- with beta absent;
  * with no needs-operator item anywhere the section is absent, and the
    rest of the pane (the OPERATOR INBOX block aside, which the parked
    questions also appear in) is byte-identical once one is parked;
  * answering the inbox question reopens the item, and the count follows;
  * priority items count, a source naming an item by its leading ID
    matches, an item with no open question says so, an unreadable inbox
    says so, and an empty kanban prints nothing.

Every tree is built under pytest's tmp_path; the pane runs with an
environment built from an explicit set (``build_scrubbed_env``) whose
kanban root and temp dir name the sandbox, and a throwaway HOME. Bug IDs
are in the fictitious BUG-9xxx range. To re-record the golden after a
deliberate change, run this file directly with ``--record`` from team/:
``python3 tests/unit/test_attention_pane_needs_operator.py --record``.
"""

from __future__ import annotations

import datetime
import io
import pathlib
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.dashboard.scan_attention import scan_needs_operator_section  # noqa: E402
from pgai_agent_kanban.dashboard.status_line import needs_operator_keys  # noqa: E402
from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.write import set_needs_operator  # noqa: E402
from pgai_agent_kanban.overwatch.inbox import answer_entry, write_entry  # noqa: E402
from tests.fixtures.dashboard_render_fixture import (  # noqa: E402
    build_dashboard_project_root,
    run_dashboard_script,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_ATTENTION_SH = _TEAM_DIR / "scripts" / "dashboard" / "attention.sh"
PANE_GOLDEN = _TEAM_DIR / "tests" / "fixtures" / "golden" / "attention_pane_needs_operator.txt"

SECTION_HEADER = "? NEEDS OPERATOR"
INBOX_HEADER = "? OPERATOR INBOX"
#: Headers that can follow the section, in pane order.
_FOLLOWING_HEADERS = (INBOX_HEADER, "! PENDING APPROVALS")
_TIMEOUT_SECONDS = 60

OLDER_QUESTION = "Which account should the fix log in with?"
NEWER_QUESTION = "Is a ten-minute export limit acceptable?"
RC_QUESTION = "Keep the RC open for the late fix?"


def _at(day: int, hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 10, day, hour, tzinfo=datetime.UTC)


def _write_bug(
    root: pathlib.Path, project: str, stem: str, status: str = "open", severity: str = "low"
) -> pathlib.Path:
    bugs = root / "projects" / project / "bugs"
    bugs.mkdir(parents=True, exist_ok=True)
    path = bugs / f"{stem}.md"
    path.write_text(f"# {stem}\n\n## Status\n{status}\n\n**Severity:** {severity}\n", encoding="utf-8")
    return path


def build_root(base: pathlib.Path) -> pathlib.Path:
    """Lay out the two-project sandbox root with every bug open and no
    inbox written yet; returns the root."""
    root = build_dashboard_project_root(base, project_name="alpha").kanban_root
    (root / "tmp").mkdir(exist_ok=True)
    with (root / "projects.cfg").open("a", encoding="utf-8") as cfg:
        cfg.write("\n[project:beta]\npriority=2\n")
    (root / "projects" / "beta").mkdir(parents=True, exist_ok=True)
    _write_bug(root, "alpha", "BUG-9001-login-crash")
    _write_bug(root, "alpha", "BUG-9002-export-timeout")
    _write_bug(root, "alpha", "BUG-9003-tooltip-typo")
    _write_bug(root, "beta", "BUG-9101-slow-search")
    return root


def park_alpha_bugs(root: pathlib.Path) -> None:
    """Park alpha's first two bugs on operator questions, and add a newer
    inbox question that names no item."""
    ctx = OpsContext(kanban_root=root)
    set_needs_operator(ctx, "alpha", "BUG-9001-login-crash", OLDER_QUESTION, now=_at(17, 9))
    set_needs_operator(ctx, "alpha", "BUG-9002-export-timeout", NEWER_QUESTION, now=_at(18, 10))
    write_entry(
        ctx,
        "alpha",
        source="rc:v1.2.0",
        question=RC_QUESTION,
        meanwhile="Left the RC open.",
        how_to_answer='Answer "keep" or "cancel".',
        now=_at(18, 12),
    )


def run_pane(root: pathlib.Path, home: pathlib.Path) -> subprocess.CompletedProcess:
    """attention.sh over *root*, colour off."""
    env = build_scrubbed_env(
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
    return run_dashboard_script(
        ["bash", str(_ATTENTION_SH), "--kanban-root", str(root)],
        env=env,
        timeout=_TIMEOUT_SECONDS,
    )


def _block(stdout: str, header: str, following: tuple[str, ...]) -> str:
    """The pane block from *header* through the line before the blank line
    that precedes the first of *following* after it; ``""`` when absent."""
    lines = stdout.splitlines(keepends=True)
    stripped = [line.rstrip("\n") for line in lines]
    if header not in stripped:
        return ""
    start = stripped.index(header)
    end = min(stripped.index(h, start) for h in following if h in stripped[start:]) - 1
    return "".join(lines[start:end])


def section_block(stdout: str) -> str:
    """The NEEDS OPERATOR block of the pane, or ``""``."""
    return _block(stdout, SECTION_HEADER, _FOLLOWING_HEADERS)


def without_blocks(stdout: str) -> str:
    """The pane with the NEEDS OPERATOR and OPERATOR INBOX blocks (and the
    blank line each scanner prints ahead of its header) taken out."""
    for block in (section_block(stdout), _block(stdout, INBOX_HEADER, _FOLLOWING_HEADERS[1:])):
        if block:
            stdout = stdout.replace("\n" + block, "", 1)
    return stdout


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _scan(root: pathlib.Path, use_color: bool = False) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        scan_needs_operator_section(str(root), use_color=use_color)
    return out.getvalue()


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
    park_alpha_bugs(bare_root)
    return bare_root


# ---------------------------------------------------------------------------
# The attention pane
# ---------------------------------------------------------------------------


def test_pane_needs_operator_block_matches_the_golden(root: pathlib.Path, home: pathlib.Path) -> None:
    result = run_pane(root, home)
    assert result.returncode == 0, _diagnostic(result)
    assert section_block(result.stdout) == PANE_GOLDEN.read_text(encoding="utf-8"), _diagnostic(result)


def test_golden_block_shows_the_count_and_the_newest_needs_operator_question() -> None:
    block = PANE_GOLDEN.read_text(encoding="utf-8")
    assert "  alpha: 2 needs-operator\n" in block
    newest = [line for line in block.splitlines() if line.startswith("    Newest: ")]
    assert len(newest) == 1, block
    assert NEWER_QUESTION in newest[0] and "(BUG-9002-export-timeout)" in newest[0]
    assert OLDER_QUESTION not in block  # only the newest question is shown
    assert RC_QUESTION not in block  # newer, but names no needs-operator item
    assert "beta" not in block  # nothing parked there


def test_no_needs_operator_item_hides_the_section_and_leaves_the_pane_unchanged(
    bare_root: pathlib.Path, home: pathlib.Path
) -> None:
    none_parked = run_pane(bare_root, home)
    assert none_parked.returncode == 0, _diagnostic(none_parked)
    assert SECTION_HEADER not in none_parked.stdout.splitlines()

    park_alpha_bugs(bare_root)
    parked = run_pane(bare_root, home)
    assert parked.returncode == 0, _diagnostic(parked)
    assert "  alpha: 2 needs-operator\n" in section_block(parked.stdout)
    assert without_blocks(parked.stdout) == without_blocks(none_parked.stdout)


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------


def test_answering_the_question_reopens_the_item_and_the_count_follows(root: pathlib.Path) -> None:
    ctx = OpsContext(kanban_root=root)
    newest_id = next(line.split()[1] for line in _scan(root).splitlines() if "Newest:" in line)
    answer_entry(ctx, "alpha", newest_id, "Yes, ten minutes is fine.", now=_at(18, 13))

    output = _scan(root)
    assert "  alpha: 1 needs-operator\n" in output
    assert OLDER_QUESTION in output and NEWER_QUESTION not in output


def test_scanner_prints_nothing_without_a_needs_operator_item(bare_root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    assert _scan(bare_root) == ""
    empty = tmp_path / "empty"
    (empty / "projects").mkdir(parents=True)
    assert _scan(empty) == ""


def test_priority_items_count_and_a_leading_id_source_names_the_item(tmp_path: pathlib.Path) -> None:
    project = tmp_path / "projects" / "delta"
    _write_bug(tmp_path, "delta", "BUG-9201-crash", status="needs-operator")
    (project / "priority").mkdir(parents=True)
    (project / "priority" / "PRIORITY-dark-mode.md").write_text(
        "# Dark mode\n\n## Status\nNeeds-Operator\n", encoding="utf-8"
    )
    write_entry(
        OpsContext(kanban_root=tmp_path),
        "delta",
        source="BUG-9201",
        question="Ship the crash fix behind a flag?",
        meanwhile="Waiting.",
        how_to_answer="Answer yes or no.",
        now=_at(18, 9),
    )

    output = _scan(tmp_path)
    assert "  delta: 2 needs-operator\n" in output
    assert "(BUG-9201)  Ship the crash fix behind a flag?\n" in output


def test_an_item_without_an_open_question_says_so(tmp_path: pathlib.Path) -> None:
    _write_bug(tmp_path, "delta", "BUG-9301-hand-parked", status="needs-operator")
    write_entry(
        OpsContext(kanban_root=tmp_path),
        "delta",
        source="BUG-9301-hand-parked-other",
        question="A different item?",
        meanwhile="Waiting.",
        how_to_answer="Answer.",
        now=_at(18, 9),
    )
    output = _scan(tmp_path)
    assert "  delta: 1 needs-operator\n" in output
    assert "    Newest: (no open inbox question)\n" in output


def test_an_unreadable_inbox_says_so(tmp_path: pathlib.Path) -> None:
    _write_bug(tmp_path, "delta", "BUG-9401-parked", status="needs-operator")
    (tmp_path / "projects" / "delta" / "operator-inbox.md").write_bytes(b"## Open\n\xff\xfe\n")
    assert "    Newest: (inbox unreadable)\n" in _scan(tmp_path)


def test_scanner_colours_on_request(root: pathlib.Path) -> None:
    coloured = _scan(root, use_color=True)
    assert "❓ NEEDS OPERATOR" in coloured and "\033[" in coloured


def test_needs_operator_keys_skips_readmes_and_other_statuses(tmp_path: pathlib.Path) -> None:
    _write_bug(tmp_path, "delta", "BUG-9501-parked", status="needs-operator")
    _write_bug(tmp_path, "delta", "BUG-9502-open")
    _write_bug(tmp_path, "delta", "BUG-9503-running", status="running")
    _write_bug(tmp_path, "delta", "README", status="needs-operator")
    bugs = tmp_path / "projects" / "delta" / "bugs"
    assert needs_operator_keys(bugs) == ["BUG-9501-parked"]
    assert needs_operator_keys(tmp_path / "missing") == []


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record() -> None:
    """Rewrite the golden from the current scripts."""
    scratch_root = pathlib.Path(resolve_temp_root())
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="needs-operator-golden-", dir=scratch_root) as scratch:
        base = pathlib.Path(scratch)
        sandbox = build_root(base)
        park_alpha_bugs(sandbox)
        home_dir = base / "home"
        home_dir.mkdir()
        PANE_GOLDEN.write_text(section_block(run_pane(sandbox, home_dir).stdout), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        print("usage: python3 tests/unit/test_attention_pane_needs_operator.py --record", file=sys.stderr)
        sys.exit(2)
    record()
