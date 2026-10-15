"""
test_sentinel_leak_provenance.py
================================
Behavioral unit tests for the sentinel provenance record and the
sentinel-leak check's use of it.

The sentinel writers in pgai_agent_kanban.ops.write (halt, halt_global,
halt_after, defer) append one record per changing write to
<root>/logs/sentinel-writes.log; team/scripts/lib/sentinel_leak_check.sh
reads it before blaming the suite for a sentinel that appeared during the
run. These tests drive the real writer -- through the ops CLI shim exactly
as halt-after.sh calls it, and in process as the dashboard calls it --
between a real snapshot and a real check, and assert:

* an operator write (no pytest node id in the writer's environment) is
  narrated as ATTRIBUTED, naming the writer, and not counted as a leak;
* a sentinel written straight onto the root with no record is still
  reported ADDED, naming the path;
* a writer call made from inside a test is still reported, naming the test;
* each writer leaves a record naming entry point, pid and time, and a
  repeated identical arm leaves none;
* a provenance failure narrates and never stops the sentinel write.

Every sentinel is written under a sandbox root built with the five-variable
recipe (projects/pgai-agent-kanban/README.md, "Live-install safety"): both
roots and the temp dir point into the sandbox, PGAI_TASKS_DIR and
PGAI_PROJECT_ROOT are cleared -- for subprocesses on the command line, for
in-process calls through monkeypatch.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from pgai_agent_kanban.lib import sentinel_provenance
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/sentinel_leak_check.sh"
_PROJECT = "alpha"


def _sandbox_env_prefix(root: pathlib.Path) -> str:
    """The five-variable sandbox recipe as an ``env`` prefix, plus the
    pytest node id cleared so the write reads as an operator's."""
    return (
        "env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PYTEST_CURRENT_TEST "
        f"KANBAN_ROOT='{root}' PGAI_AGENT_KANBAN_ROOT_PATH='{root}' "
        f"PGAI_AGENT_KANBAN_TEMP_DIR='{root / 'tmp'}'"
    )


def _operator_halt_after(root: pathlib.Path, token: str = "rc") -> str:
    """Bash that arms HALT-AFTER the way halt-after.sh does: the ops CLI
    shim, run through pp_run_ops, which calls ops.write.halt_after."""
    return (
        f"{_sandbox_env_prefix(root)} bash scripts/lib/pp_run_ops.sh "
        f"pgai_agent_kanban.ops halt_after '{root / 'projects' / _PROJECT}' '{token}'"
    )


@pytest.fixture
def sandbox_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A throwaway kanban root with two projects, and the five root
    variables pointed at it (or cleared) for in-process writer calls."""
    root = tmp_path / "sandbox_root"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    (root / "projects" / "beta").mkdir(parents=True)
    (root / "tmp").mkdir()
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root / "tmp"))
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    return root


def _snapshot(tmp_path: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    snap = tmp_path / "presuite.snap"
    result = run_bash(tmp_path, f"source {_LIB} && pgai_sentinel_snapshot '{root}' > '{snap}'")
    assert result.returncode == 0, result.stderr
    return snap


def _check(tmp_path: pathlib.Path, root: pathlib.Path, snap: pathlib.Path):
    return run_bash(tmp_path, f"source {_LIB} && pgai_sentinel_leak_check '{root}' '{snap}'")


def _records(root: pathlib.Path) -> list[dict[str, str]]:
    log = sentinel_provenance.log_path(root)
    if not log.is_file():
        return []
    records = []
    for line in log.read_text(encoding="utf-8").splitlines():
        fields = dict(field.split("=", 1) for field in line.split("\t"))
        records.append(fields)
    return records


def test_an_operator_halt_after_during_the_run_is_attributed_not_reported_as_a_leak(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """The reported shape: snapshot, then the operator arms HALT-AFTER
    through ops.write.halt_after, then the check. The sentinel is narrated
    as the operator's write and the check passes."""
    sentinel = sandbox_root / "projects" / _PROJECT / "HALT-AFTER"
    snap = _snapshot(tmp_path, sandbox_root)

    armed = run_bash(tmp_path, _operator_halt_after(sandbox_root))
    assert armed.returncode == 0, armed.stdout + armed.stderr
    assert sentinel.read_text(encoding="utf-8") == "rc\n", "the sentinel's token must be untouched"

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ADDED:   {sentinel}" not in result.stderr
    assert "left behind" not in result.stderr
    assert f"[pgai-sentinel] ATTRIBUTED: {sentinel} -- added during the run by halt_after" in result.stdout
    assert "entry: " in result.stdout and "pgai_agent_kanban.ops halt_after" in result.stdout
    assert "unchanged by the suite (1 change(s) above attributed" in result.stdout


def test_a_sentinel_written_directly_with_no_record_is_still_reported_added(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """The fixture-leak shape keeps failing the check, naming the path,
    even when an attributed operator write lands in the same run."""
    operator_written = sandbox_root / "projects" / _PROJECT / "HALT-AFTER"
    leaked = sandbox_root / "projects" / "beta" / "HALT"
    snap = _snapshot(tmp_path, sandbox_root)

    assert run_bash(tmp_path, _operator_halt_after(sandbox_root)).returncode == 0
    leaked.write_text("", encoding="utf-8")

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[pgai-sentinel] ADDED:   {leaked} -- a sentinel the suite left behind" in result.stderr
    assert "1 sentinel difference(s)" in result.stderr
    assert f"ATTRIBUTED: {operator_written}" in result.stdout


def test_a_writer_call_made_from_a_test_is_still_reported_naming_the_test(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """A fixture that resolved the live root and called the writer is still
    a leak: the record carries its pytest node id, so the check reports it
    ADDED and names the test that wrote it."""
    sentinel = sandbox_root / "projects" / _PROJECT / "HALT-AFTER"
    snap = _snapshot(tmp_path, sandbox_root)

    ops_write.halt_after(OpsContext(kanban_root=sandbox_root), _PROJECT)

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[pgai-sentinel] ADDED:   {sentinel}" in result.stderr
    assert "Written by halt_after under test " in result.stderr
    assert "test_a_writer_call_made_from_a_test_is_still_reported_naming_the_test" in result.stderr
    assert "ATTRIBUTED" not in result.stdout


def test_an_operator_rearm_of_an_existing_sentinel_is_attributed_as_changed(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    sentinel = sandbox_root / "projects" / _PROJECT / "HALT-AFTER"
    sentinel.write_text("rc\n", encoding="utf-8")
    snap = _snapshot(tmp_path, sandbox_root)

    assert run_bash(tmp_path, _operator_halt_after(sandbox_root, "coder")).returncode == 0

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ATTRIBUTED: {sentinel} -- changed during the run by halt_after" in result.stdout
    assert "CHANGED:" not in result.stderr


def test_a_record_older_than_the_snapshot_does_not_excuse_a_later_leak(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """Only records written during the run explain a difference: an old
    operator record for the same path leaves a direct leak reported."""
    sentinel = sandbox_root / "projects" / _PROJECT / "HALT-AFTER"
    log = sentinel_provenance.log_path(sandbox_root)
    log.parent.mkdir(parents=True)
    log.write_text(
        f"time=2001-09-09T01:46:40+00:00\tepoch=1000000000\top=halt_after\tpath={sentinel}\t"
        "sha256=-\tentry=old\tpid=1\tppid=0\tparent=-\tuser=op\ttest=-\n",
        encoding="utf-8",
    )
    snap = _snapshot(tmp_path, sandbox_root)
    sentinel.write_text("rc\n", encoding="utf-8")

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"ADDED:   {sentinel}" in result.stderr
    assert "ATTRIBUTED" not in result.stdout


def test_each_sentinel_writer_leaves_a_record_naming_entry_point_pid_and_time(
    sandbox_root: pathlib.Path,
) -> None:
    ctx = OpsContext(kanban_root=sandbox_root)
    project_dir = sandbox_root / "projects" / _PROJECT

    ops_write.halt(ctx, _PROJECT)
    ops_write.halt_global(ctx)
    ops_write.halt_after(ctx, _PROJECT, "rc")
    ops_write.defer(ctx, _PROJECT, "v9.0.0")

    records = _records(sandbox_root)
    assert [r["op"] for r in records] == ["halt", "halt_global", "halt_after", "defer"]
    expected_paths = [
        project_dir / "HALT",
        sandbox_root.resolve() / "HALT",
        project_dir / "HALT-AFTER",
        project_dir / "DEFER-LOW-BUGS",
    ]
    for record, path in zip(records, expected_paths):
        assert list(record) == list(sentinel_provenance.FIELDS)
        assert pathlib.Path(record["path"]).resolve() == path.resolve()
        assert record["pid"] == str(os.getpid())
        assert record["entry"] not in ("", "-")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", record["time"])
        assert record["epoch"].isdigit()
        assert "test_each_sentinel_writer_leaves_a_record" in record["test"]
    # The sentinels' own bytes carry nothing but what their readers parse.
    assert (project_dir / "HALT").read_bytes() == b""
    assert (project_dir / "HALT-AFTER").read_text(encoding="utf-8") == "rc\n"


def test_a_repeated_identical_write_adds_no_record(sandbox_root: pathlib.Path) -> None:
    """Idempotent re-runs leave the tree as they found it: no second record."""
    ctx = OpsContext(kanban_root=sandbox_root)
    ops_write.halt(ctx, _PROJECT)
    ops_write.halt_after(ctx, _PROJECT, "rc")
    ops_write.defer(ctx, _PROJECT, "v9.0.0")
    before = sentinel_provenance.log_path(sandbox_root).read_bytes()

    ops_write.halt(ctx, _PROJECT)
    ops_write.halt_after(ctx, _PROJECT, "rc")
    ops_write.defer(ctx, _PROJECT, "v9.0.0")
    assert sentinel_provenance.log_path(sandbox_root).read_bytes() == before

    ops_write.halt_after(ctx, _PROJECT, "coder")
    assert [r["op"] for r in _records(sandbox_root)][-1] == "halt_after"
    assert len(_records(sandbox_root)) == 4


def test_a_provenance_failure_narrates_and_never_stops_the_sentinel_write(
    sandbox_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (sandbox_root / "logs").write_text("not a directory\n", encoding="utf-8")  # log cannot be created
    ctx = OpsContext(kanban_root=sandbox_root)

    ops_write.halt_after(ctx, _PROJECT, "rc")

    sentinel = sandbox_root / "projects" / _PROJECT / "HALT-AFTER"
    assert sentinel.read_text(encoding="utf-8") == "rc\n"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "halt_after: WARNING:" in captured.err
    assert "provenance record could not be appended" in captured.err


# ---------------------------------------------------------------------------
# The HALT-AFTER drain and promote steps record their sentinel writes too.
# They are handed a project scope, not a kanban root; the record lands in the
# kanban root's log all the same.
# ---------------------------------------------------------------------------


def _write_release_state(project_dir: pathlib.Path, active_rc: str) -> None:
    """A release-state.md with the three required headings."""
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n"
        f"## Active RC\n{active_rc}\n\n"
        "## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )


def _operator_python(root: pathlib.Path, statement: str) -> str:
    """Bash that runs one Python *statement* the way the wake loop and CM's
    ship step reach drain and promote: a separate process in the sandbox,
    with no pytest node id in its environment."""
    return f'{_sandbox_env_prefix(root)} python3 -c "{statement}"'


def test_a_drain_capture_rewrite_during_the_run_is_attributed_not_reported_as_a_leak(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """The operator armed a bare rc HALT-AFTER before the suite; the drain
    step captures the in-flight RC during it. The rewrite is narrated as
    drain's, and the check passes."""
    project_dir = sandbox_root / "projects" / _PROJECT
    sentinel = project_dir / "HALT-AFTER"
    sentinel.write_text("rc\n", encoding="utf-8")
    _write_release_state(project_dir, "v9.9.9")
    snap = _snapshot(tmp_path, sandbox_root)

    drained = run_bash(
        tmp_path,
        _operator_python(
            sandbox_root,
            "from pgai_agent_kanban.halt_after.drain import check_drain; "
            f"print(check_drain('rc', '{project_dir}'))",
        ),
    )
    assert drained.returncode == 0, drained.stdout + drained.stderr
    assert drained.stdout.strip() == "False", "a capture is never a satisfied drain"
    assert sentinel.read_text(encoding="utf-8") == "rc:v9.9.9\n"

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHANGED:" not in result.stderr
    assert f"[pgai-sentinel] ATTRIBUTED: {sentinel} -- changed during the run by drain_capture" in result.stdout
    assert "unchanged by the suite (1 change(s) above attributed" in result.stdout

    records = _records(sandbox_root)
    assert [r["op"] for r in records] == ["drain_capture"]
    assert pathlib.Path(records[0]["path"]) == sentinel
    assert records[0]["test"] == "-"


def test_a_promotion_during_the_run_attributes_both_the_halt_and_the_removed_halt_after(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """A promotion landing mid-run creates HALT and removes HALT-AFTER.
    Neither is counted as a suite leak, and the check passes."""
    project_dir = sandbox_root / "projects" / _PROJECT
    halt_after = project_dir / "HALT-AFTER"
    halt = project_dir / "HALT"
    halt_after.write_text("rc:v9.9.9\n", encoding="utf-8")
    _write_release_state(project_dir, "none")
    snap = _snapshot(tmp_path, sandbox_root)

    promoted = run_bash(
        tmp_path,
        _operator_python(
            sandbox_root,
            "from pgai_agent_kanban.halt_after.promote import promote; "
            f"promote('{project_dir}', 'rc:v9.9.9')",
        ),
    )
    assert promoted.returncode == 0, promoted.stdout + promoted.stderr
    assert halt.is_file() and not halt_after.exists()

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ADDED:" not in result.stderr and "REMOVED:" not in result.stderr
    assert f"[pgai-sentinel] ATTRIBUTED: {halt} -- added during the run by promote" in result.stdout
    assert f"[pgai-sentinel] ATTRIBUTED: {halt_after} -- removed during the run by promote" in result.stdout
    assert "unchanged by the suite (2 change(s) above attributed" in result.stdout

    records = _records(sandbox_root)
    assert [(r["op"], pathlib.Path(r["path"])) for r in records] == [
        ("promote", halt),
        ("promote", halt_after),
    ]
    assert records[1]["sha256"] == sentinel_provenance.ABSENT_DIGEST


def test_a_sentinel_removed_with_no_record_is_still_reported_removed(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """A fixture deleting a live sentinel is still a finding, even when a
    recorded promotion in the same run is attributed."""
    project_dir = sandbox_root / "projects" / _PROJECT
    (project_dir / "HALT-AFTER").write_text("rc:v9.9.9\n", encoding="utf-8")
    deleted = sandbox_root / "projects" / "beta" / "HALT"
    deleted.write_text("", encoding="utf-8")
    snap = _snapshot(tmp_path, sandbox_root)

    assert run_bash(
        tmp_path,
        _operator_python(
            sandbox_root,
            "from pgai_agent_kanban.halt_after.promote import promote; "
            f"promote('{project_dir}', 'rc:v9.9.9')",
        ),
    ).returncode == 0
    deleted.unlink()

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[pgai-sentinel] REMOVED: {deleted} -- a sentinel present before the suite is gone" in result.stderr
    assert "1 sentinel difference(s)" in result.stderr
    assert f"ATTRIBUTED: {project_dir / 'HALT-AFTER'} -- removed during the run by promote" in result.stdout


def test_a_removal_record_does_not_excuse_a_sentinel_written_back_without_one(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """The latest record for HALT-AFTER says promote removed it; a fixture
    then writes it back directly. The file present now is unexplained."""
    project_dir = sandbox_root / "projects" / _PROJECT
    halt_after = project_dir / "HALT-AFTER"
    halt_after.write_text("rc:v9.9.9\n", encoding="utf-8")
    snap = _snapshot(tmp_path, sandbox_root)

    assert run_bash(
        tmp_path,
        _operator_python(
            sandbox_root,
            "from pgai_agent_kanban.halt_after.promote import promote; "
            f"promote('{project_dir}', 'rc:v9.9.9')",
        ),
    ).returncode == 0
    halt_after.write_text("coder\n", encoding="utf-8")

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[pgai-sentinel] CHANGED: {halt_after}" in result.stderr
    assert f"ATTRIBUTED: {halt_after}" not in result.stdout


def test_a_promotion_made_from_a_test_is_still_reported_naming_the_test(
    tmp_path: pathlib.Path, sandbox_root: pathlib.Path
) -> None:
    """A fixture that promoted on the live root is a leak: both changes are
    reported, each naming the test that made it."""
    from pgai_agent_kanban.halt_after.promote import promote

    project_dir = sandbox_root / "projects" / _PROJECT
    (project_dir / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    snap = _snapshot(tmp_path, sandbox_root)

    promote(project_dir, "coder")

    result = _check(tmp_path, sandbox_root, snap)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[pgai-sentinel] ADDED:   {project_dir / 'HALT'}" in result.stderr
    assert "Written by promote under test " in result.stderr
    assert f"[pgai-sentinel] REMOVED: {project_dir / 'HALT-AFTER'}" in result.stderr
    assert "Removed by promote under test " in result.stderr
    assert "test_a_promotion_made_from_a_test_is_still_reported_naming_the_test" in result.stderr
    assert "ATTRIBUTED" not in result.stdout


def test_drain_and_promote_record_nothing_when_they_change_nothing(
    sandbox_root: pathlib.Path,
) -> None:
    """A capture that rewrites the same bytes and a promotion onto an
    existing HALT with no HALT-AFTER left to remove add no record."""
    from pgai_agent_kanban.halt_after.drain import check_drain
    from pgai_agent_kanban.halt_after.promote import promote

    project_dir = sandbox_root / "projects" / _PROJECT
    (project_dir / "HALT-AFTER").write_text("rc:v9.9.9\n", encoding="utf-8")
    (project_dir / "HALT").write_text("", encoding="utf-8")
    _write_release_state(project_dir, "v9.9.9")

    assert check_drain("rc", project_dir) is False
    (project_dir / "HALT-AFTER").unlink()
    promote(project_dir, "rc:v9.9.9")

    assert _records(sandbox_root) == []


def test_a_provenance_failure_never_changes_what_drain_and_promote_do(
    sandbox_root: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the log uncreatable, the capture and the promotion still happen,
    return what they always return, and narrate one warning each."""
    from pgai_agent_kanban.halt_after.drain import check_drain
    from pgai_agent_kanban.halt_after.promote import promote

    (sandbox_root / "logs").write_text("not a directory\n", encoding="utf-8")
    project_dir = sandbox_root / "projects" / _PROJECT
    halt_after = project_dir / "HALT-AFTER"
    halt_after.write_text("rc\n", encoding="utf-8")
    _write_release_state(project_dir, "v9.9.9")

    assert check_drain("rc", project_dir) is False
    assert halt_after.read_text(encoding="utf-8") == "rc:v9.9.9\n"

    assert promote(project_dir, "rc:v9.9.9") is None
    assert (project_dir / "HALT").is_file()
    assert not halt_after.exists()
    assert "## HALT Event" in (project_dir / "release-state.md").read_text(encoding="utf-8")

    err = capsys.readouterr().err
    assert "drain_capture: WARNING:" in err
    assert err.count("promote: WARNING:") == 2
    assert "provenance record could not be appended" in err


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("root/projects/alpha", "root"),
        ("root/projects/alpha/", "root"),
        ("root", "root"),
        ("root/projects", "root/projects"),
        ("/", "/"),
    ],
)
def test_kanban_root_for_scope_maps_a_project_scope_to_its_root(scope: str, expected: str) -> None:
    """A project scope maps to the root above projects/; a root-scope or
    single-project directory is the kanban root itself."""
    assert sentinel_provenance.kanban_root_for_scope(scope) == pathlib.Path(expected)
