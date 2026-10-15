"""
Unit tests for the queue-marker write helpers in pgai_agent_kanban.ops.write:
set_marker / remove_marker and their pp_run_ops verbs.

Covered:
  (A) set_marker called directly: every one of the five markers written over
      every one of the five (plus the "[]" alias), the "- " prefix and the
      description suffix preserved byte-for-byte, a missing entry and a
      missing file reported as False with nothing written, a key that is a
      prefix of another key left alone, every duplicate line rewritten,
      case-insensitive matching on request only, a non-marker refused.
  (B) remove_marker called directly: the line and its newline removed, a
      last line with no trailing newline, a missing entry, case-insensitive
      matching on request only.
  (C) Each pp_run_ops verb produces the same queue file as the function, and
      maps "no line for KEY" to exit 3 and a non-marker to exit 1.
  (D) set_marker leaves a queue file byte-identical to the one the wake's
      bash marker writer (mark_backlog, wake_task_state.sh) leaves after the
      same transition, for each of the five markers.
  (F) ensure_marker and its render_ensure_marker verb: an existing line
      rewritten by the set_marker entry match, a missing file rendered as a
      new cache, a missing line added under ## Queue (or in a new ## Queue
      section), a non-marker refused.
  (E) wake_task_transition.sh and wake_reconcile_sweeps.sh carry no
      queue-line rewrite of their own: no sed, no regex substitution — their
      marker writes go through mark_backlog / transition_task_state.

Paths are relative to team/, the CWD under run-unit-tests.sh.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.ops import CANONICAL_MARKERS, OpsError, ensure_marker, remove_marker, set_marker

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_PP_RUN_OPS = _SCRIPTS_DIR / "lib" / "pp_run_ops.sh"
_WAKE_TASK_STATE_LIB = _SCRIPTS_DIR / "lib" / "wake_task_state.sh"
_MARKER_CALLER_LIBS = (
    _SCRIPTS_DIR / "lib" / "wake_task_transition.sh",
    _SCRIPTS_DIR / "lib" / "wake_reconcile_sweeps.sh",
)

_KEY = "CODER-20260101-001-sample-task"
_OTHER = "WRITER-20260101-002-other-task"
_DESC = " — a description with [brackets] and trailing text"

# Source bracket contents: the five markers plus the "[]" alias.
_SOURCE_BRACKETS = [" ", "W", "A", "B", "x", ""]


def _queue(marker_bracket: str, key: str = _KEY, desc: str = _DESC) -> str:
    return (
        "# Coder backlog\n"
        "\n"
        f"- [x] {_OTHER} — earlier\n"
        f"- [{marker_bracket}] {key}{desc}\n"
        f"- [ ] {_OTHER}-later — later\n"
    )


def _write(tmp_path: pathlib.Path, name: str, text: str) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# (A) set_marker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", _SOURCE_BRACKETS, ids=lambda s: f"from[{s}]")
@pytest.mark.parametrize("target", CANONICAL_MARKERS, ids=lambda t: f"to[{t}]")
def test_set_marker_rewrites_only_the_bracket(tmp_path: pathlib.Path, source: str, target: str) -> None:
    path = _write(tmp_path, "coder_backlog.md", _queue(source))
    assert set_marker(path, _KEY, target) is True
    assert path.read_text(encoding="utf-8") == _queue(target)


def test_set_marker_canonical_markers_are_the_five_queue_states() -> None:
    assert CANONICAL_MARKERS == (" ", "W", "A", "B", "x")


def test_set_marker_empty_marker_writes_canonical_pending(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "q.md", _queue("A"))
    assert set_marker(path, _KEY, "") is True
    assert path.read_text(encoding="utf-8") == _queue(" ")


def test_set_marker_preserves_description_suffix_and_prefix_spacing(tmp_path: pathlib.Path) -> None:
    text = f"  -  [W]\t{_KEY}  —  keep   this\tsuffix [x] exactly  \n"
    path = _write(tmp_path, "q.md", text)
    assert set_marker(path, _KEY, "A") is True
    assert path.read_text(encoding="utf-8") == f"  -  [A]\t{_KEY}  —  keep   this\tsuffix [x] exactly  \n"


def test_set_marker_entry_without_description(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "q.md", f"- [W] {_KEY}")
    assert set_marker(path, _KEY, "x") is True
    assert path.read_text(encoding="utf-8") == f"- [x] {_KEY}"


def test_set_marker_missing_entry_returns_false_and_leaves_file(tmp_path: pathlib.Path) -> None:
    text = _queue("A", key="CODER-20260101-009-different")
    path = _write(tmp_path, "q.md", text)
    before = path.stat().st_mtime_ns
    assert set_marker(path, _KEY, "x") is False
    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mtime_ns == before


def test_set_marker_missing_file_returns_false(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "absent.md"
    assert set_marker(path, _KEY, "x") is False
    assert not path.exists()


def test_set_marker_key_prefix_does_not_match_longer_key(tmp_path: pathlib.Path) -> None:
    text = f"- [A] {_KEY}-extended — another task\n"
    path = _write(tmp_path, "q.md", text)
    assert set_marker(path, _KEY, "x") is False
    assert path.read_text(encoding="utf-8") == text


def test_set_marker_rewrites_every_duplicate_line(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "q.md", f"- [A] {_KEY} — one\n- [W] {_KEY} — two\n")
    assert set_marker(path, _KEY, "B") is True
    assert path.read_text(encoding="utf-8") == f"- [B] {_KEY} — one\n- [B] {_KEY} — two\n"


def test_set_marker_same_marker_is_found_and_not_rewritten(tmp_path: pathlib.Path) -> None:
    text = _queue("x")
    path = _write(tmp_path, "q.md", text)
    before = path.stat().st_mtime_ns
    assert set_marker(path, _KEY, "x") is True
    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mtime_ns == before


def test_set_marker_case_sensitive_unless_asked(tmp_path: pathlib.Path) -> None:
    text = "- [ ] BUG-0042-Sample-Defect — intake\n"
    path = _write(tmp_path, "bug_backlog.md", text)
    assert set_marker(path, "bug-0042-sample-defect", "x") is False
    assert path.read_text(encoding="utf-8") == text
    assert set_marker(path, "bug-0042-sample-defect", "x", ignore_case=True) is True
    assert path.read_text(encoding="utf-8") == "- [x] BUG-0042-Sample-Defect — intake\n"


@pytest.mark.parametrize("bad", ["X", "R", "done", "[x]", "?"])
def test_set_marker_refuses_a_non_marker(tmp_path: pathlib.Path, bad: str) -> None:
    text = _queue("A")
    path = _write(tmp_path, "q.md", text)
    with pytest.raises(OpsError, match="not one of the queue markers"):
        set_marker(path, _KEY, bad)
    assert path.read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# (B) remove_marker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", _SOURCE_BRACKETS, ids=lambda s: f"from[{s}]")
def test_remove_marker_deletes_the_line(tmp_path: pathlib.Path, source: str) -> None:
    path = _write(tmp_path, "q.md", _queue(source))
    assert remove_marker(path, _KEY) is True
    assert path.read_text(encoding="utf-8") == (
        "# Coder backlog\n\n" f"- [x] {_OTHER} — earlier\n" f"- [ ] {_OTHER}-later — later\n"
    )


def test_remove_marker_last_line_without_newline(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, "q.md", f"- [x] {_OTHER}\n- [A] {_KEY}")
    assert remove_marker(path, _KEY) is True
    assert path.read_text(encoding="utf-8") == f"- [x] {_OTHER}\n"


def test_remove_marker_missing_entry_and_missing_file(tmp_path: pathlib.Path) -> None:
    text = f"- [A] {_KEY}-extended\n"
    path = _write(tmp_path, "q.md", text)
    assert remove_marker(path, _KEY) is False
    assert path.read_text(encoding="utf-8") == text
    assert remove_marker(tmp_path / "absent.md", _KEY) is False


def test_remove_marker_case_sensitive_unless_asked(tmp_path: pathlib.Path) -> None:
    text = "- [x] PRIORITY-Sample — intake\n- [ ] keep\n"
    path = _write(tmp_path, "priority_backlog.md", text)
    assert remove_marker(path, "priority-sample") is False
    assert remove_marker(path, "priority-sample", ignore_case=True) is True
    assert path.read_text(encoding="utf-8") == "- [ ] keep\n"


# ---------------------------------------------------------------------------
# (C) pp_run_ops verbs agree with the functions
# ---------------------------------------------------------------------------


def _pp_run_ops(*args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("KANBAN_ROOT", "PYTHONPATH")}
    script = f'source "{_PP_RUN_OPS}"; pp_run_ops pgai_agent_kanban.ops "$@"'
    return subprocess.run(
        ["bash", "-c", script, "pp_run_ops", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd="/",
    )


@pytest.mark.parametrize("source", _SOURCE_BRACKETS, ids=lambda s: f"from[{s}]")
@pytest.mark.parametrize("target", CANONICAL_MARKERS, ids=lambda t: f"to[{t}]")
def test_set_marker_verb_matches_function(tmp_path: pathlib.Path, source: str, target: str) -> None:
    via_verb = _write(tmp_path, "verb.md", _queue(source))
    via_func = _write(tmp_path, "func.md", _queue(source))
    result = _pp_run_ops("set_marker", str(via_verb), _KEY, target)
    assert result.returncode == 0, result.stderr
    set_marker(via_func, _KEY, target)
    assert via_verb.read_bytes() == via_func.read_bytes()


def test_set_marker_verb_ignore_case_flag(tmp_path: pathlib.Path) -> None:
    via_verb = _write(tmp_path, "verb.md", "- [ ] BUG-7-Upper\n")
    via_func = _write(tmp_path, "func.md", "- [ ] BUG-7-Upper\n")
    assert _pp_run_ops("set_marker", str(via_verb), "bug-7-upper", "x").returncode == 3
    result = _pp_run_ops("set_marker", str(via_verb), "bug-7-upper", "x", "1")
    assert result.returncode == 0, result.stderr
    set_marker(via_func, "bug-7-upper", "x", ignore_case=True)
    assert via_verb.read_bytes() == via_func.read_bytes()


def test_remove_marker_verb_matches_function(tmp_path: pathlib.Path) -> None:
    via_verb = _write(tmp_path, "verb.md", _queue("A"))
    via_func = _write(tmp_path, "func.md", _queue("A"))
    result = _pp_run_ops("remove_marker", str(via_verb), _KEY)
    assert result.returncode == 0, result.stderr
    remove_marker(via_func, _KEY)
    assert via_verb.read_bytes() == via_func.read_bytes()


def test_marker_verbs_report_missing_entry_as_exit_3(tmp_path: pathlib.Path) -> None:
    text = _queue("A", key="CODER-20260101-009-different")
    path = _write(tmp_path, "q.md", text)
    set_result = _pp_run_ops("set_marker", str(path), _KEY, "x")
    assert set_result.returncode == 3
    assert _KEY in set_result.stderr and str(path) in set_result.stderr
    remove_result = _pp_run_ops("remove_marker", str(path), _KEY)
    assert remove_result.returncode == 3
    assert path.read_text(encoding="utf-8") == text


def test_set_marker_verb_refuses_non_marker_with_exit_1(tmp_path: pathlib.Path) -> None:
    text = _queue("A")
    path = _write(tmp_path, "q.md", text)
    result = _pp_run_ops("set_marker", str(path), _KEY, "Q")
    assert result.returncode == 1
    assert "not one of the queue markers" in result.stderr
    assert path.read_text(encoding="utf-8") == text


def test_marker_verbs_require_their_arguments(tmp_path: pathlib.Path) -> None:
    assert _pp_run_ops("set_marker", str(tmp_path / "q.md"), _KEY).returncode == 1
    assert _pp_run_ops("remove_marker", str(tmp_path / "q.md")).returncode == 1


# ---------------------------------------------------------------------------
# (D) Byte-identical to the wake's bash marker writer
# ---------------------------------------------------------------------------


def _run_mark_backlog(backlog: pathlib.Path, key: str, marker: str, temp_dir: pathlib.Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{temp_dir}"
        source "{_SCRIPTS_DIR}/lib/error_contract.sh"
        source "{_SCRIPTS_DIR}/lib/temp.sh"
        source "{_SCRIPTS_DIR}/lib/atomic_write.sh"
        eval "$(awk '/^mark_backlog\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' '{_WAKE_TASK_STATE_LIB}')"
        BACKLOG="{backlog}"
        mark_backlog "$1" "$2"
    """)
    return subprocess.run(
        ["bash", "-c", script, "mark_backlog", key, marker],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("source", [" ", "W", "A", "B", "x"], ids=lambda s: f"from[{s}]")
@pytest.mark.parametrize("target", CANONICAL_MARKERS, ids=lambda t: f"to[{t}]")
def test_set_marker_matches_wake_mark_backlog_bytes(tmp_path: pathlib.Path, source: str, target: str) -> None:
    temp_dir = tmp_path / "kanban_temp"
    temp_dir.mkdir()
    via_bash = _write(tmp_path, "bash.md", _queue(source))
    via_python = _write(tmp_path, "python.md", _queue(source))
    result = _run_mark_backlog(via_bash, _KEY, target, temp_dir)
    assert result.returncode == 0, result.stderr
    assert set_marker(via_python, _KEY, target) is True
    assert via_python.read_bytes() == via_bash.read_bytes()


# ---------------------------------------------------------------------------
# (E) The wake's marker-calling libs carry no queue-line rewrite of their own
# ---------------------------------------------------------------------------

_REWRITE_SHAPES = re.compile(r"\bsed\b|\bre\.subn?\(|\.subn?\(|\bgsub\(|\bsub\(")


@pytest.mark.parametrize("lib", _MARKER_CALLER_LIBS, ids=lambda p: p.name)
def test_marker_caller_libs_have_no_queue_line_rewrite(lib: pathlib.Path) -> None:
    offenders = [
        f"{lib.name}:{n}: {line.strip()}"
        for n, line in enumerate(lib.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#") and _REWRITE_SHAPES.search(line)
    ]
    assert offenders == [], "queue-line rewrites belong to set_marker, not the caller:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# (F) ensure_marker / render_ensure_marker
# ---------------------------------------------------------------------------

_CACHE_HEAD = "# Backlog Cache\n\n## Queue\n\n"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, _CACHE_HEAD + "- [x] BUG-1\n"),
        (_CACHE_HEAD + "- [ ] BUG-1 — d\n", _CACHE_HEAD + "- [x] BUG-1 — d\n"),
        (_CACHE_HEAD + "- [W] BUG-1\n- [B] BUG-1\n", _CACHE_HEAD + "- [x] BUG-1\n- [x] BUG-1\n"),
        (_CACHE_HEAD + "- [x] BUG-2\n", _CACHE_HEAD + "- [x] BUG-2\n- [x] BUG-1\n"),
        (_CACHE_HEAD + "- [ ] BUG-1-x\n", _CACHE_HEAD + "- [ ] BUG-1-x\n- [x] BUG-1\n"),
        (_CACHE_HEAD + "- [x] BUG-2\n\n## Notes\nn\n", _CACHE_HEAD + "- [x] BUG-2\n- [x] BUG-1\n\n## Notes\nn\n"),
        ("# Backlog Cache\n", "# Backlog Cache\n\n## Queue\n\n- [x] BUG-1\n"),
        ("# Backlog Cache", "# Backlog Cache\n\n## Queue\n\n- [x] BUG-1\n"),
    ],
    ids=[
        "missing-file", "pending-line", "every-duplicate", "added-after-entries",
        "prefix-key-left-alone", "added-before-next-section", "no-queue-section", "no-queue-no-newline",
    ],
)
def test_ensure_marker_rewrites_or_adds_the_line(before: "str | None", after: str) -> None:
    assert ensure_marker(before, "BUG-1", "x") == after


def test_ensure_marker_refuses_a_non_marker() -> None:
    with pytest.raises(OpsError):
        ensure_marker(None, "BUG-1", "Q")


def test_render_ensure_marker_verb_prints_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    queue = tmp_path / "bug_backlog.md"
    queue.write_text(_CACHE_HEAD + "- [ ] BUG-1\n", encoding="utf-8")
    present = _pp_run_ops("render_ensure_marker", str(queue), "BUG-1", "x")
    absent = _pp_run_ops("render_ensure_marker", str(tmp_path / "absent.md"), "BUG-1", "x")
    assert present.returncode == 0 == absent.returncode, present.stderr + absent.stderr
    assert present.stdout == absent.stdout == _CACHE_HEAD + "- [x] BUG-1\n"
    assert queue.read_text(encoding="utf-8") == _CACHE_HEAD + "- [ ] BUG-1\n"
    assert not (tmp_path / "absent.md").exists()
