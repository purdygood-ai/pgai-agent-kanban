"""
test_queue_scan_helpers.py
==========================
Unit tests for the queue-line helpers in pgai_agent_kanban.ops.write that
the wake's marker writer and queue scans call, and for their pp_run_ops
verbs:

  (A) replace_marker: text-level set_marker (same match, same canonical
      marker, line count returned, a non-marker refused).
  (B) bracket_marker / queue_markers: "[ ]" reads " ", the "[]" alias reads
      "", spacing is dropped, every line for the key in file order, a
      missing file or key reads [].
  (C) parse_scan_rule: markers, the "[]" alias token, "*" for any state, an
      empty item for no state, refusals for malformed rules.
  (D) scan_queue: rule selection by marker and ## State, a missing
      status.md read as no state, the id gate, the limit, dashless lines,
      an unreadable queue reported as IoError.
  (E) Verbs: render_marker prints and writes nothing (exit 3 with the wake's
      own message for a missing line), get_markers prints "[M]" lines (exit
      3, silent, for none), scan_queue prints keys (exit 1 for a bad rule).

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.ops import (
    IoError,
    OpsError,
    bracket_marker,
    parse_scan_rule,
    queue_markers,
    replace_marker,
    scan_queue,
    set_marker,
)

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_ID_PATTERN = r"^(CLAUDE-)?[A-Z]+-[0-9]{8}-[0-9]{3}-[a-z0-9-]+$"

_KEY = "CODER-20260101-001-queue-task"


def _id(n: int) -> str:
    return f"CODER-20260101-{n:03d}-scan"


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (A) replace_marker
# ---------------------------------------------------------------------------


def test_replace_marker_matches_set_marker_bytes(tmp_path: pathlib.Path) -> None:
    text = f"# Q\n\n- [A] {_KEY} — a [bracket] description\n- [ ] {_KEY}-later — other\n"
    on_disk = _write(tmp_path / "q.md", text)
    set_marker(on_disk, _KEY, "B")
    assert replace_marker(text, _KEY, "B") == (on_disk.read_text(encoding="utf-8"), 1)


def test_replace_marker_reports_no_match_and_leaves_text() -> None:
    text = f"- [ ] {_KEY}-later — other\n"
    assert replace_marker(text, _KEY, "x") == (text, 0)


def test_replace_marker_writes_the_pending_marker_for_blank() -> None:
    assert replace_marker(f"- [] {_KEY}\n", _KEY, "") == (f"- [ ] {_KEY}\n", 1)


def test_replace_marker_refuses_a_non_marker() -> None:
    with pytest.raises(OpsError):
        replace_marker(f"- [ ] {_KEY}\n", _KEY, "Q")


# ---------------------------------------------------------------------------
# (B) bracket_marker / queue_markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "marker"),
    [(" ", " "), ("   ", " "), ("", ""), ("W", "W"), (" W ", "W"), ("?", "?")],
    ids=["pending", "wide-pending", "alias", "waiting", "spaced", "unknown"],
)
def test_bracket_marker_reads_content(content: str, marker: str) -> None:
    assert bracket_marker(content) == marker


def test_queue_markers_returns_every_line_for_the_key_in_order(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path / "q.md",
        f"- [W] {_KEY} — a\n- [ ] {_KEY}-other — not this key\n-  [ B ]  {_KEY}\n- [] {_KEY} — alias\n",
    )
    assert queue_markers(path, _KEY) == ["W", "B", ""]


def test_queue_markers_missing_file_or_key_is_empty(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path / "q.md", f"- [W] {_KEY}-other\n")
    assert queue_markers(path, _KEY) == []
    assert queue_markers(tmp_path / "absent.md", _KEY) == []


def test_queue_markers_case_insensitive_on_request(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path / "q.md", "- [x] Bug-7-Upper\n")
    assert queue_markers(path, "bug-7-upper") == []
    assert queue_markers(path, "bug-7-upper", ignore_case=True) == ["x"]


# ---------------------------------------------------------------------------
# (C) parse_scan_rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "parsed"),
    [
        ("W=*", (frozenset({"W"}), None)),
        (" B=,BACKLOG", (frozenset({" ", "B"}), frozenset({"", "BACKLOG"}))),
        (" []=waiting", (frozenset({" ", ""}), frozenset({"WAITING"}))),
        ("B=BLOCKED, done", (frozenset({"B"}), frozenset({"BLOCKED", "DONE"}))),
    ],
    ids=["any-state", "empty-state-item", "alias-token", "trimmed-uppercased"],
)
def test_parse_scan_rule(rule: str, parsed: tuple) -> None:
    assert parse_scan_rule(rule) == parsed


@pytest.mark.parametrize("rule", ["W", "=BACKLOG", "Q=*", "W?=*"], ids=["no-equals", "no-marker", "bad-marker", "mixed"])
def test_parse_scan_rule_refuses_malformed_rules(rule: str) -> None:
    with pytest.raises(OpsError) as excinfo:
        parse_scan_rule(rule)
    assert "scan_queue: rule" in str(excinfo.value)


# ---------------------------------------------------------------------------
# (D) scan_queue
# ---------------------------------------------------------------------------


def _scan_tree(tmp_path: pathlib.Path, rows: list[tuple[str, str | None]]) -> tuple[pathlib.Path, pathlib.Path]:
    tasks = tmp_path / "tasks"
    lines = ["# Q", "", "- [ ] ../escape — never a path"]
    for n, (marker, state) in enumerate(rows, start=1):
        lines.append(f"{marker} {_id(n)} — row {n}")
        if state is not None:
            _write(tasks / _id(n) / "status.md", f"## Task\n{_id(n)}\n\n{state}\n## Summary\nx\n")
    return _write(tmp_path / "q.md", "\n".join(lines) + "\n"), tasks


_ROWS = [
    ("- [ ]", "## State\nWORKING\n"),  # 1
    ("- [B]", None),  # 2: no status.md
    ("- []", "## State\nBACKLOG\n"),  # 3: alias
    ("[ ]", "## State\n\nbacklog\n"),  # 4: no dash, lowercase, blank line
    ("- [W]", "## State\nBACKLOG\n"),  # 5
    ("- [ ]", "## State\nWAITING\n"),  # 6
    ("- []", "## State\nWAITING\n"),  # 7
    ("-  [ B ]", "## State\n**BLOCKED**\n"),  # 8: bold is not BLOCKED
    ("- [B]", "## State\nBLOCKED\n"),  # 9
]


def test_scan_queue_pending_rule(tmp_path: pathlib.Path) -> None:
    queue, tasks = _scan_tree(tmp_path, _ROWS)
    rules = [parse_scan_rule(" B=,BACKLOG")]
    assert scan_queue(queue, tasks, rules, id_pattern=_ID_PATTERN) == [_id(2), _id(4)]
    assert scan_queue(queue, tasks, rules, id_pattern=_ID_PATTERN, limit=1) == [_id(2)]


def test_scan_queue_waiting_rules(tmp_path: pathlib.Path) -> None:
    queue, tasks = _scan_tree(tmp_path, _ROWS)
    rules = [parse_scan_rule("W=*"), parse_scan_rule(" []=WAITING")]
    assert scan_queue(queue, tasks, rules, id_pattern=_ID_PATTERN) == [_id(5), _id(6), _id(7)]


def test_scan_queue_blocked_rule(tmp_path: pathlib.Path) -> None:
    queue, tasks = _scan_tree(tmp_path, _ROWS)
    assert scan_queue(queue, tasks, [parse_scan_rule("B=BLOCKED")], id_pattern=_ID_PATTERN) == [_id(9)]


def test_scan_queue_without_an_id_pattern_keeps_every_key(tmp_path: pathlib.Path) -> None:
    queue, tasks = _scan_tree(tmp_path, [])
    assert scan_queue(queue, tasks, [parse_scan_rule(" =*")]) == ["../escape"]
    assert scan_queue(queue, tasks, [parse_scan_rule(" =*")], id_pattern=_ID_PATTERN) == []


def test_scan_queue_unreadable_queue_is_io_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(IoError):
        scan_queue(tmp_path / "absent.md", tmp_path, [parse_scan_rule("W=*")])


# ---------------------------------------------------------------------------
# (E) Verbs through pp_run_ops
# ---------------------------------------------------------------------------


def _verb(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("KANBAN_ROOT", "PYTHONPATH")}
    return subprocess.run(
        ["bash", "-c", f'source "{_PP_RUN_OPS}"; pp_run_ops pgai_agent_kanban.ops "$@"', "verb", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(cwd),
    )


def test_render_marker_verb_prints_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    text = f"# Q\n\n- [A] {_KEY} — task\n"
    path = _write(tmp_path / "q.md", text)
    result = _verb(tmp_path, "render_marker", str(path), _KEY, "B")
    assert (result.returncode, result.stdout, result.stderr) == (0, f"# Q\n\n- [B] {_KEY} — task\n", "")
    assert path.read_text(encoding="utf-8") == text


def test_render_marker_verb_missing_line(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path / "q.md", "# Q\n")
    result = _verb(tmp_path, "render_marker", str(path), _KEY, "B")
    assert (result.returncode, result.stdout, result.stderr) == (3, "", f"Could not update backlog entry for {_KEY}\n")


def test_get_markers_verb(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path / "q.md", f"- [W] {_KEY}\n- [] {_KEY}\n- [ ] {_KEY}\n")
    result = _verb(tmp_path, "get_markers", str(path), _KEY)
    assert (result.returncode, result.stdout) == (0, "[W]\n[]\n[ ]\n")
    none = _verb(tmp_path, "get_markers", str(path), "CODER-20260101-009-absent")
    assert (none.returncode, none.stdout, none.stderr) == (3, "", "")


def test_scan_queue_verb(tmp_path: pathlib.Path) -> None:
    queue, tasks = _scan_tree(tmp_path, _ROWS)
    result = _verb(tmp_path, "scan_queue", str(queue), str(tasks), _ID_PATTERN, "0", "W=*", " []=WAITING")
    assert (result.returncode, result.stdout) == (0, f"{_id(5)}\n{_id(6)}\n{_id(7)}\n")
    first = _verb(tmp_path, "scan_queue", str(queue), str(tasks), _ID_PATTERN, "1", " B=,BACKLOG")
    assert first.stdout == f"{_id(2)}\n"


@pytest.mark.parametrize(
    "args",
    [("LIMIT",), ("x", "W=*"), ("0", "Q=*")],
    ids=["too-few", "limit-not-a-number", "bad-rule"],
)
def test_scan_queue_verb_refuses_bad_arguments(tmp_path: pathlib.Path, args: tuple[str, ...]) -> None:
    queue, tasks = _scan_tree(tmp_path, [])
    result = _verb(tmp_path, "scan_queue", str(queue), str(tasks), _ID_PATTERN, *args)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "scan_queue" in result.stderr
