"""
test_state_file_writes_survive_interrupted_replace.py
=====================================================
Behavioral tests for the shared state files more than one process reads or
writes, each replaced through pgai_agent_kanban.lib.atomic_write:

  - the per-RC release-state JSON (cm.write_rc_state write_ship / write_cancel)
  - a project's HALT-AFTER sentinel, written by its one writer
    (halt_after.sentinel.write_halt_after) from both the operator verb
    (ops.write.halt_after) and the bare ``rc`` drain capture
    (halt_after.drain.check_drain)
  - the discovery rejection-count TSV (discovery.items record_rejection /
    forget_rejection)

The interruption tests stub ``os.replace`` to raise -- the last step of an
atomic write, after the new bytes are on disk in a scratch file -- and
assert the previous file is byte-for-byte intact and no scratch file is
left beside it. A plain truncate-then-write would already have destroyed
the old content by that point, so these tests fail against one.

All fixtures live under pytest's tmp_path; nothing outside it is read or
written.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from pathlib import Path

import pytest

from pgai_agent_kanban.cm.write_rc_state import write_cancel, write_ship
from pgai_agent_kanban.discovery.items import forget_rejection, record_rejection
from pgai_agent_kanban.halt_after.drain import check_drain
from pgai_agent_kanban.halt_after.sentinel import write_halt_after
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import OpsError
from pgai_agent_kanban.ops.write import halt_after

_RC_JSON_BEFORE = (
    '{\n  "closed_at": null,\n  "opened_at": "2026-01-01T00:00:00Z",\n'
    '  "outcome": "in_progress",\n  "rc": "v1.2.3"\n}\n'
)
_REJECTIONS_BEFORE = "BUG-1-a.md\t1\t2026-01-01\twhy\nBUG-2-b.md\t3\t2026-01-02\t\n"


def _own_dir(tmp_path: Path) -> Path:
    """A fresh directory holding only what the test puts there.

    tmp_path itself may carry the harness's own stubs, so directory-listing
    assertions run against this subdirectory instead.
    """
    path = tmp_path / "state"
    path.mkdir()
    return path


@pytest.fixture
def replace_raises(monkeypatch: pytest.MonkeyPatch) -> list:
    """Make every ``os.replace`` raise, as an interruption at the rename step.

    Returns the list of (src, dst) pairs the stub was called with, so a test
    can show the write really reached the rename and was not skipped.
    """
    calls: list = []

    def _raise(src, dst, *args, **kwargs):
        calls.append((os.fspath(src), os.fspath(dst)))
        raise OSError(errno.EIO, "simulated interruption at rename")

    monkeypatch.setattr(os, "replace", _raise)
    return calls


def _assert_intact(path: Path, before: bytes, calls: list) -> None:
    """*path* still holds *before*, the rename was attempted, no scratch remains."""
    assert calls, f"the write to {path} never reached os.replace"
    assert calls[-1][1] == os.fspath(path), f"os.replace targeted {calls[-1][1]}, not {path}"
    assert path.read_bytes() == before, f"{path} was changed by an interrupted write"
    leftovers = sorted(p.name for p in path.parent.iterdir() if p.name != path.name)
    assert leftovers == [], f"scratch files left beside {path.name}: {leftovers}"


def _demo_project(tmp_path: Path, active_rc: str = "none") -> tuple[OpsContext, Path]:
    """A kanban root holding one project ``demo`` with a release-state.md."""
    kanban_root = tmp_path / "kanban"
    root = kanban_root / "projects" / "demo"
    (root / "tasks").mkdir(parents=True)
    (root / "release-state.md").write_text(f"# Release State\n\n## Active RC\n{active_rc}\n", encoding="utf-8")
    return OpsContext(kanban_root=kanban_root), root


# ---------------------------------------------------------------------------
# per-RC release-state JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("writer", [write_ship, write_cancel], ids=["ship", "cancel"])
def test_interrupted_rc_json_write_keeps_previous_record(
    tmp_path: Path, replace_raises: list, writer, capsys: pytest.CaptureFixture
) -> None:
    state_dir = tmp_path / "release-state"
    state_dir.mkdir()
    path = state_dir / "v1.2.3.json"
    path.write_text(_RC_JSON_BEFORE, encoding="utf-8")

    with pytest.raises(OSError):
        writer(str(path), "v1.2.3", "2026-01-02T00:00:00Z")

    _assert_intact(path, _RC_JSON_BEFORE.encode("utf-8"), replace_raises)
    assert "updated" not in capsys.readouterr().out, "a failed write must not report success"


@pytest.mark.parametrize(
    "writer, outcome", [(write_ship, "shipped"), (write_cancel, "cancelled")], ids=["ship", "cancel"]
)
def test_rc_json_write_keeps_existing_mode_and_leaves_no_scratch(
    tmp_path: Path, writer, outcome: str, capsys: pytest.CaptureFixture
) -> None:
    state_dir = _own_dir(tmp_path)
    path = state_dir / "v1.2.3.json"
    path.write_text(_RC_JSON_BEFORE, encoding="utf-8")
    os.chmod(path, 0o640)

    writer(str(path), "v1.2.3", "2026-01-02T00:00:00Z")

    expected = {
        "closed_at": "2026-01-02T00:00:00Z",
        "opened_at": "2026-01-01T00:00:00Z",
        "outcome": outcome,
        "rc": "v1.2.3",
    }
    assert path.read_text(encoding="utf-8") == json.dumps(expected, indent=2, sort_keys=True) + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert [p.name for p in state_dir.iterdir()] == ["v1.2.3.json"]


# ---------------------------------------------------------------------------
# HALT-AFTER sentinel
# ---------------------------------------------------------------------------


def test_interrupted_operator_arm_keeps_previous_token(tmp_path: Path, replace_raises: list) -> None:
    ctx, root = _demo_project(tmp_path)
    sentinel = root / "HALT-AFTER"
    sentinel.write_text("coder\n", encoding="utf-8")
    latch = root / "HALT-AFTER.rc-gate"
    latch.write_text("coder\n", encoding="utf-8")

    with pytest.raises(OpsError, match="failed to write HALT-AFTER sentinel"):
        halt_after(ctx, "demo", "writer")

    assert sentinel.read_bytes() == b"coder\n"
    assert latch.exists(), "a failed re-arm must not clear the latch of the arm still in place"
    assert replace_raises and replace_raises[-1][1] == os.fspath(sentinel)
    leftovers = sorted(p.name for p in root.iterdir())
    assert leftovers == ["HALT-AFTER", "HALT-AFTER.rc-gate", "release-state.md", "tasks"], leftovers


def test_interrupted_drain_capture_keeps_previous_token(tmp_path: Path, replace_raises: list) -> None:
    _ctx, root = _demo_project(tmp_path, active_rc="rc/v0.55.0")
    sentinel = root / "HALT-AFTER"
    sentinel.write_text("rc\n", encoding="utf-8")

    assert check_drain("rc", root) is False, "an RC is in flight, so the drain is not satisfied"

    assert sentinel.read_bytes() == b"rc\n"
    assert replace_raises and replace_raises[-1][1] == os.fspath(sentinel)
    assert sorted(p.name for p in root.iterdir()) == ["HALT-AFTER", "release-state.md", "tasks"]


def test_interrupted_sentinel_write_keeps_previous_token(tmp_path: Path, replace_raises: list) -> None:
    root = _own_dir(tmp_path)
    sentinel = root / "HALT-AFTER"
    sentinel.write_text("rc:v1.0.0\n", encoding="utf-8")

    with pytest.raises(OSError):
        write_halt_after(root, "pm")

    _assert_intact(sentinel, b"rc:v1.0.0\n", replace_raises)


def test_both_halt_after_writers_produce_the_same_bytes(tmp_path: Path) -> None:
    ctx, root = _demo_project(tmp_path, active_rc="rc/v0.55.0")

    halt_after(ctx, "demo", "rc")
    assert (root / "HALT-AFTER").read_bytes() == b"rc\n"

    assert check_drain("rc", root) is False
    assert (root / "HALT-AFTER").read_bytes() == b"rc:v0.55.0\n"
    assert not [p.name for p in root.iterdir() if p.name.startswith(".")], "scratch file left behind"


def test_sentinel_writer_reports_whether_the_bytes_changed(tmp_path: Path) -> None:
    root = _own_dir(tmp_path)
    sentinel = root / "HALT-AFTER"

    assert write_halt_after(root, "coder") is True, "absent file: the write is a change"
    os.chmod(sentinel, 0o640)
    assert write_halt_after(root, "coder") is False, "same token: no change"
    assert write_halt_after(root, "rc:v2.0.0") is True, "new token: a change"

    assert sentinel.read_bytes() == b"rc:v2.0.0\n"
    assert stat.S_IMODE(sentinel.stat().st_mode) == 0o640, "an existing sentinel keeps its mode"


# ---------------------------------------------------------------------------
# discovery rejection-count state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rewrite",
    [
        lambda path: record_rejection(path, "BUG-3-c.md", "1", "2026-01-03", "bad name"),
        lambda path: forget_rejection(path, "BUG-1-a.md"),
    ],
    ids=["record", "forget"],
)
def test_interrupted_rejection_state_write_keeps_previous_table(tmp_path: Path, replace_raises: list, rewrite) -> None:
    state_dir = _own_dir(tmp_path)
    path = state_dir / ".discovery-rejections.tsv"
    path.write_text(_REJECTIONS_BEFORE, encoding="utf-8")

    with pytest.raises(OSError):
        rewrite(path)

    _assert_intact(path, _REJECTIONS_BEFORE.encode("utf-8"), replace_raises)


def test_rejection_state_rewrites_land_whole(tmp_path: Path) -> None:
    state_dir = _own_dir(tmp_path)
    path = state_dir / ".discovery-rejections.tsv"
    path.write_text(_REJECTIONS_BEFORE, encoding="utf-8")
    os.chmod(path, 0o640)

    record_rejection(path, "BUG-1-a.md", "2", "2026-01-04", "still bad")
    assert path.read_text(encoding="utf-8") == ("BUG-2-b.md\t3\t2026-01-02\t\nBUG-1-a.md\t2\t2026-01-04\tstill bad\n")

    forget_rejection(path, "BUG-2-b.md")
    assert path.read_text(encoding="utf-8") == "BUG-1-a.md\t2\t2026-01-04\tstill bad\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert [p.name for p in state_dir.iterdir()] == [path.name]
