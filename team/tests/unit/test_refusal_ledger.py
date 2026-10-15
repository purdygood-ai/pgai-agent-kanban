"""
test_refusal_ledger.py
========================
Unit tests for pgai_agent_kanban.lib.refusal_ledger — the most-recent-
refusal record, per project: the library that overwrites (never appends)
one record per project, meant to be the durable, queryable answer to "why
is this lane not moving right now."

Coverage:
  - write_latest()/read_latest() round-trip the record shape: timestamp,
    project, surface, error, message, remedy, context.
  - A second write_latest() call REPLACES the first: the file holds
    exactly one record, and it is the newer one (overwrite-not-append
    semantics).
  - read_latest() on a project that has never recorded a refusal returns
    None ("never recorded"), distinguishable from a malformed ledger.
  - read_latest() raises RefusalLedgerError on non-whitespace unparseable
    content, and on a record missing a required field.
  - write_latest() refuses a record with any blank/missing field
    (including context) or an out-of-set context value, and writes
    nothing.
  - record_refusal() — the safe wrapper the shared emitters call — is
    covered for: writing when project+kanban_root resolve, the no-project
    no-op (neither an explicit project nor PGAI_PROJECT_NAME), the
    kanban-root-missing no-op (never fabricates kanban_root itself, twice
    in a row), and never raising when the underlying write fails
    (unwritable ledger path).
  - context stamping: record_refusal() stamps "test" while
    PYTEST_CURRENT_TEST is set (the ambient case for every test in this
    suite) and "install" when it is cleared; read_latest() on a record
    written before the context field existed reports "unknown" rather
    than raising or guessing "install".
  - ledger_path_for_project() matches the documented path convention.
  - registration: given a sandbox projects.cfg that does not register the
    project, record_refusal() writes nothing, creates no directory,
    narrates one line on stderr naming the project and the registry, and
    the emitter that called it still prints its block unchanged; a
    registered project (INI or colon format) still records, and a root
    with no projects.cfg records as before. The CLI's record --kanban-root
    path refuses the same way with exit 3.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pgai_agent_kanban.lib.error_contract import emit_error_contract, parse_error_contract
from pgai_agent_kanban.lib.refusal_ledger import (
    EXIT_UNREGISTERED_PROJECT,
    RefusalLedgerError,
    RefusalRecord,
    ledger_path_for_project,
    read_latest,
    record_refusal,
    write_latest,
)


def _make_record(**overrides) -> RefusalRecord:
    defaults = dict(
        timestamp="2026-09-10T09:15:22+00:00",
        project="pgai-agent-kanban",
        surface="release.sh:git_step",
        error="gate.rc-branch-mismatch",
        message="the release stopped because the checkout was not on the release branch",
        remedy="git checkout rc/v1.2.3 && re-run the release script",
        context="install",
    )
    defaults.update(overrides)
    return RefusalRecord(**defaults)


# ---------------------------------------------------------------------------
# Never-recorded vs recorded
# ---------------------------------------------------------------------------


def test_read_latest_on_missing_ledger_is_none(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "refusal-ledger.md")
    assert read_latest(ledger_path) is None


def test_read_latest_on_empty_ledger_file_is_none(tmp_path: Path) -> None:
    ledger_path = tmp_path / "refusal-ledger.md"
    ledger_path.write_text("", encoding="utf-8")
    assert read_latest(str(ledger_path)) is None


def test_never_recorded_and_recorded_are_distinguishable_without_parsing_prose(
    tmp_path: Path,
) -> None:
    ledger_path = str(tmp_path / "refusal-ledger.md")
    assert read_latest(ledger_path) is None

    write_latest(ledger_path, _make_record())
    record = read_latest(ledger_path)
    assert record is not None
    assert record["error"] == "gate.rc-branch-mismatch"


# ---------------------------------------------------------------------------
# write_latest — record shape round-trip
# ---------------------------------------------------------------------------


def test_write_latest_creates_ledger_with_header_and_all_fields(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "refusal-ledger.md")
    write_latest(ledger_path, _make_record())

    text = Path(ledger_path).read_text(encoding="utf-8")
    assert "Most-recent-only" in text
    assert text.count("## Refusal:") == 1

    record = read_latest(ledger_path)
    assert record is not None
    assert record["timestamp"] == "2026-09-10T09:15:22+00:00"
    assert record["project"] == "pgai-agent-kanban"
    assert record["surface"] == "release.sh:git_step"
    assert record["error"] == "gate.rc-branch-mismatch"
    assert (
        record["message"]
        == "the release stopped because the checkout was not on the release branch"
    )
    assert record["remedy"] == "git checkout rc/v1.2.3 && re-run the release script"
    assert record["context"] == "install"


# ---------------------------------------------------------------------------
# Overwrite-not-append semantics
# ---------------------------------------------------------------------------


def test_second_write_replaces_first_record_exactly_one_entry(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "refusal-ledger.md")
    write_latest(ledger_path, _make_record(error="gate.first", surface="a.sh:fn_a"))
    write_latest(
        ledger_path,
        _make_record(
            timestamp="2026-09-11T00:00:00+00:00",
            error="gate.second",
            message="second message",
            remedy="second remedy",
            surface="b.sh:fn_b",
        ),
    )

    text = Path(ledger_path).read_text(encoding="utf-8")
    assert text.count("## Refusal:") == 1, "ledger must hold exactly one record, not append"

    record = read_latest(ledger_path)
    assert record is not None
    assert record["error"] == "gate.second"
    assert record["surface"] == "b.sh:fn_b"
    # The first record's error string must not survive anywhere in the file.
    assert "gate.first" not in text


def test_many_writes_never_grow_the_file_beyond_one_record(tmp_path: Path) -> None:
    ledger_path = str(tmp_path / "refusal-ledger.md")
    for i in range(5):
        write_latest(ledger_path, _make_record(error=f"gate.iteration-{i}"))

    text = Path(ledger_path).read_text(encoding="utf-8")
    assert text.count("## Refusal:") == 1
    record = read_latest(ledger_path)
    assert record["error"] == "gate.iteration-4"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("error", ""),
        ("message", ""),
        ("remedy", ""),
        ("project", ""),
        ("surface", ""),
        ("timestamp", ""),
        ("context", ""),
    ],
)
def test_write_latest_refuses_blank_field_and_writes_nothing(
    tmp_path: Path, field: str, value: str
) -> None:
    ledger_path = tmp_path / "refusal-ledger.md"
    kwargs = {field: value}
    with pytest.raises(RefusalLedgerError):
        write_latest(str(ledger_path), _make_record(**kwargs))
    assert not ledger_path.exists()


def test_write_latest_refuses_context_value_outside_install_or_test(tmp_path: Path) -> None:
    """context is a closed two-value field -- a typo or a third
    value must refuse loud rather than write a record the operator surface
    (the next ticket) would have to guess how to interpret."""
    ledger_path = tmp_path / "refusal-ledger.md"
    with pytest.raises(RefusalLedgerError):
        write_latest(str(ledger_path), _make_record(context="staging"))
    assert not ledger_path.exists()


def test_read_latest_raises_on_non_whitespace_unparseable_content(tmp_path: Path) -> None:
    ledger_path = tmp_path / "refusal-ledger.md"
    ledger_path.write_text("this is not a refusal ledger at all\n", encoding="utf-8")
    with pytest.raises(RefusalLedgerError):
        read_latest(str(ledger_path))


def test_read_latest_raises_when_record_missing_remedy(tmp_path: Path) -> None:
    ledger_path = tmp_path / "refusal-ledger.md"
    ledger_path.write_text(
        "# Refusal Ledger — demo\n\n"
        "## Refusal: 2026-09-10T09:15:22+00:00 — demo — release.sh:git_step\n"
        "- Error: gate.x\n"
        "- Message: no remedy line at all\n",
        encoding="utf-8",
    )
    with pytest.raises(RefusalLedgerError):
        read_latest(str(ledger_path))


def test_read_latest_on_record_predating_context_field_reports_context_unknown(
    tmp_path: Path,
) -> None:
    """A record written before this change has no '- Context:' bullet at
    all (this is the exact on-disk shape format_record() produced before
    this field existed). read_latest() must accept it without raising and
    report its context as "unknown" rather than guessing "install" -- a
    reader must not silently treat a record that predates the distinction
    as though it were known-good install context."""
    ledger_path = tmp_path / "refusal-ledger.md"
    ledger_path.write_text(
        "# Refusal Ledger — demo\n\n"
        "## Refusal: 2026-09-10T09:15:22+00:00 — demo — release.sh:git_step\n"
        "- Error: gate.rc-branch-mismatch\n"
        "- Message: the release stopped because the checkout was not on the release branch\n"
        "- Remedy: git checkout rc/v1.2.3 && re-run the release script\n",
        encoding="utf-8",
    )
    record = read_latest(str(ledger_path))
    assert record is not None
    assert record["context"] == "unknown"
    assert record["error"] == "gate.rc-branch-mismatch"


# ---------------------------------------------------------------------------
# record_refusal — the safe wrapper the shared emitters call
# ---------------------------------------------------------------------------


def test_record_refusal_writes_when_project_and_kanban_root_resolve(tmp_path: Path) -> None:
    kanban_root = str(tmp_path)
    written = record_refusal(
        "gate.x",
        "message text",
        "remedy text",
        project="demo-project",
        surface="demo.sh:demo_fn",
        kanban_root=kanban_root,
    )
    assert written is True

    record = read_latest(ledger_path_for_project(kanban_root, "demo-project"))
    assert record is not None
    assert record["error"] == "gate.x"
    assert record["surface"] == "demo.sh:demo_fn"


def test_record_refusal_no_project_case_writes_nothing_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No explicit project and no ambient PGAI_PROJECT_NAME: record_refusal
    must return False and must not create any file, anywhere under
    kanban_root — a project-less refusal goes nowhere, it never guesses."""
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    kanban_root = tmp_path / "isolated_kanban_root"
    kanban_root.mkdir()

    written = record_refusal(
        "gate.x", "message text", "remedy text", kanban_root=str(kanban_root)
    )

    assert written is False
    assert list(kanban_root.rglob("*")) == [], "no file should have been created anywhere"


def test_record_refusal_falls_back_to_ambient_project_name_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGAI_PROJECT_NAME", "env-project")
    kanban_root = str(tmp_path)

    written = record_refusal(
        "gate.env", "env message", "env remedy", kanban_root=kanban_root
    )

    assert written is True
    record = read_latest(ledger_path_for_project(kanban_root, "env-project"))
    assert record is not None
    assert record["error"] == "gate.env"


def test_record_refusal_never_raises_when_ledger_path_unwritable(tmp_path: Path) -> None:
    kanban_root = tmp_path / "root"
    projects_dir = kanban_root / "projects"
    projects_dir.mkdir(parents=True)
    original_mode = projects_dir.stat().st_mode
    projects_dir.chmod(0)
    try:
        written = record_refusal(
            "gate.unwritable",
            "unwritable message",
            "unwritable remedy",
            project="locked-project",
            kanban_root=str(kanban_root),
        )
        assert written is False
    finally:
        projects_dir.chmod(original_mode)


def test_record_refusal_kanban_root_missing_returns_false_and_creates_nothing(
    tmp_path: Path,
) -> None:
    """A refusal recorded from a guard that fires precisely because
    kanban_root does not exist must not fabricate it as a side effect --
    record_refusal must return False and leave the filesystem exactly as
    it found it, twice in a row (mirrors the two-invocations-in-a-row
    regression this pins at the CLI layer in
    team/tests/unit/test_verb_fixture_matrix.py)."""
    kanban_root = tmp_path / "absent-kanban-root"

    for _ in range(2):
        written = record_refusal(
            "gate.x",
            "message text",
            "remedy text",
            project="demo-project",
            surface="demo.sh:demo_fn",
            kanban_root=str(kanban_root),
        )
        assert written is False
        assert not kanban_root.exists(), (
            f"record_refusal must never create {kanban_root} as a side "
            "effect of recording a refusal whose entire subject is that "
            "path's own absence"
        )


def test_record_refusal_never_raises_on_bogus_kanban_root_type(tmp_path: Path) -> None:
    """A defensive case: even an outright resolution failure inside
    record_refusal (simulated here by a kanban_root that cannot be joined
    into a usable path) is swallowed, never propagated to the caller —
    recording is diagnostics, never a gate."""
    written = record_refusal(
        "gate.x", "msg", "remedy", project="p", kanban_root="\0invalid\0path"
    )
    assert written is False


# ---------------------------------------------------------------------------
# Context stamping — install vs test
# ---------------------------------------------------------------------------


def test_record_refusal_stamps_context_test_while_pytest_current_test_is_set(
    tmp_path: Path,
) -> None:
    """PYTEST_CURRENT_TEST is set by pytest's own core for the duration of
    every test -- this test's own execution is already running under it,
    with no monkeypatching required -- so a refusal recorded here with no
    explicit context= argument must be stamped "test"."""
    assert os.environ.get("PYTEST_CURRENT_TEST"), (
        "this assertion documents the ambient precondition the test below "
        "relies on -- if pytest ever stops setting this variable, the "
        "context-stamping behaviour it exercises silently stops being "
        "proven"
    )
    kanban_root = str(tmp_path)
    written = record_refusal(
        "gate.x", "message text", "remedy text", project="demo-project", kanban_root=kanban_root
    )
    assert written is True

    record = read_latest(ledger_path_for_project(kanban_root, "demo-project"))
    assert record is not None
    assert record["context"] == "test"


def test_record_refusal_stamps_context_install_when_pytest_current_test_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With PYTEST_CURRENT_TEST cleared -- simulating a refusal raised by
    the install's own operation, outside any pytest run -- record_refusal()
    must stamp context "install"."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    kanban_root = str(tmp_path)
    written = record_refusal(
        "gate.x", "message text", "remedy text", project="demo-project", kanban_root=kanban_root
    )
    assert written is True

    record = read_latest(ledger_path_for_project(kanban_root, "demo-project"))
    assert record is not None
    assert record["context"] == "install"


def test_record_refusal_explicit_context_argument_overrides_auto_detection(
    tmp_path: Path,
) -> None:
    """The context= keyword lets a caller pin the value explicitly -- used
    here to prove auto-detection is bypassed, not merely coincide with it."""
    kanban_root = str(tmp_path)
    written = record_refusal(
        "gate.x",
        "message text",
        "remedy text",
        project="demo-project",
        kanban_root=kanban_root,
        context="install",
    )
    assert written is True

    record = read_latest(ledger_path_for_project(kanban_root, "demo-project"))
    assert record is not None
    assert record["context"] == "install"


# ---------------------------------------------------------------------------
# Path convention
# ---------------------------------------------------------------------------


def test_ledger_path_for_project_matches_documented_convention(tmp_path: Path) -> None:
    path = ledger_path_for_project(str(tmp_path), "pgai-agent-kanban")
    assert path == str(tmp_path / "projects" / "pgai-agent-kanban" / "refusal-ledger.md")


# ---------------------------------------------------------------------------
# Field discipline — no credential-shaped data ever accepted
# ---------------------------------------------------------------------------


def test_record_only_carries_the_seven_documented_fields(tmp_path: Path) -> None:
    """A record has exactly seven fields (timestamp, project, surface,
    error, message, remedy, context) -- no room for an environment dump, a
    token, or a command line to be smuggled in as an extra attribute."""
    record = _make_record()
    assert set(record.to_dict().keys()) == {
        "timestamp",
        "project",
        "surface",
        "error",
        "message",
        "remedy",
        "context",
    }


# ---------------------------------------------------------------------------
# Registration — no folder for a project the root's projects.cfg does not name
# ---------------------------------------------------------------------------

_TEAM_DIR = Path(__file__).resolve().parents[2]


def _sandbox_root(tmp_path: Path, registry: str | None) -> Path:
    """A throwaway kanban root, with *registry* as its projects.cfg (none
    when *registry* is None)."""
    root = tmp_path / "sandbox_root"
    root.mkdir()
    if registry is not None:
        (root / "projects.cfg").write_text(registry, encoding="utf-8")
    return root


def test_record_refusal_unregistered_project_writes_nothing_and_narrates(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = _sandbox_root(tmp_path, "[project:real-project]\npriority=1\n")

    written = record_refusal(
        "gate.x", "message text", "remedy text", project="a", kanban_root=str(root)
    )

    assert written is False
    assert not (root / "projects").exists(), "no projects/ entry may be created"
    err = capsys.readouterr().err
    assert err.count("\n") == 1, f"exactly one narration line expected, got {err!r}"
    assert err.startswith("refusal_ledger: ")
    assert "'a'" in err
    assert str(root / "projects.cfg") in err
    assert "real-project" in err


def test_record_refusal_unregistered_project_is_refused_twice_in_a_row(tmp_path: Path) -> None:
    """The refusal holds on a second call -- the first refusal must not
    have left anything behind that the second one then writes into."""
    root = _sandbox_root(tmp_path, "[project:real-project]\npriority=1\n")
    for _ in range(2):
        assert record_refusal("gate.x", "m", "r", project="no-such-project", kanban_root=str(root)) is False
    assert sorted(p.name for p in root.iterdir()) == ["projects.cfg"]


def test_record_refusal_header_only_registry_refuses_every_project(tmp_path: Path) -> None:
    root = _sandbox_root(tmp_path, "# projects.cfg -- no project registered yet\n")
    assert record_refusal("gate.x", "m", "r", project="demo-project", kanban_root=str(root)) is False
    assert not (root / "projects").exists()


def test_record_refusal_registered_project_still_records(tmp_path: Path) -> None:
    root = _sandbox_root(tmp_path, "[project:demo-project]\npriority=1\n")
    assert record_refusal("gate.x", "m", "r", project="demo-project", kanban_root=str(root)) is True
    record = read_latest(ledger_path_for_project(str(root), "demo-project"))
    assert record is not None and record["error"] == "gate.x"


def test_record_refusal_colon_format_registry_still_records_registered_project(tmp_path: Path) -> None:
    root = _sandbox_root(tmp_path, "# legacy header\ndemo-project:1:#378ADD\n")
    assert record_refusal("gate.x", "m", "r", project="demo-project", kanban_root=str(root)) is True
    assert record_refusal("gate.x", "m", "r", project="a", kanban_root=str(root)) is False
    assert not (root / "projects" / "a").exists()


def test_record_refusal_without_projects_cfg_records_as_before(tmp_path: Path) -> None:
    """No registry to consult -- the pre-existing behaviour holds."""
    root = _sandbox_root(tmp_path, None)
    assert record_refusal("gate.x", "m", "r", project="demo-project", kanban_root=str(root)) is True


def test_emitter_block_is_unchanged_when_ledger_refuses_unregistered_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The caller of the writer -- emit_error_contract -- still prints its
    exact block and returns normally; the narration comes first, so a
    reader parsing the block gets the remedy unextended."""
    root = _sandbox_root(tmp_path, "[project:real-project]\npriority=1\n")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))

    assert emit_error_contract("gate.x", "message text", "remedy text", project="a") is None

    err = capsys.readouterr().err
    narration, block = err.split("error:", 1)
    assert narration.startswith("refusal_ledger: ") and "'a'" in narration
    assert "error:" + block == "error:   gate.x\nmessage: message text\nremedy:  remedy text\n"
    assert parse_error_contract(err)["remedy"] == "remedy text"
    assert not (root / "projects").exists()


def _run_ledger_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.refusal_ledger", *args],
        capture_output=True,
        text=True,
        cwd=str(_TEAM_DIR),
        env={**os.environ, "PYTHONPATH": str(_TEAM_DIR)},
        timeout=60,
    )


def test_cli_record_with_kanban_root_refuses_unregistered_project_exit_3(tmp_path: Path) -> None:
    root = _sandbox_root(tmp_path, "[project:real-project]\npriority=1\n")
    ledger = ledger_path_for_project(str(root), "a")

    result = _run_ledger_cli("record", "--kanban-root", str(root), ledger, "a", "k", "m", "r", "s")

    assert result.returncode == EXIT_UNREGISTERED_PROJECT == 3
    assert result.stdout == ""
    assert result.stderr.startswith("refusal_ledger: ") and "'a'" in result.stderr
    assert not (root / "projects").exists()


def test_cli_record_with_kanban_root_records_registered_project(tmp_path: Path) -> None:
    root = _sandbox_root(tmp_path, "[project:real-project]\npriority=1\n")
    ledger = ledger_path_for_project(str(root), "real-project")

    result = _run_ledger_cli(
        "record", "--kanban-root", str(root), ledger, "real-project", "k", "m", "r", "s"
    )

    assert result.returncode == 0, result.stderr
    assert read_latest(ledger)["error"] == "k"
