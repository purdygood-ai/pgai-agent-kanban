"""
test_intake_same_defect_refusal.py -- the bug deposit path refuses a draft
that reports the same defect as a live bug, naming that bug's ID.

What is proven here, against throwaway kanban roots only:

  1. The real intake.sh, given a draft whose body cites a live bug (open,
     running or needs-operator) as the defect it reports, exits 5, leaves
     bugs/ byte-for-byte as it was (no file, no claim lock, no claim log),
     and prints the dual-channel block naming the live bug's ID.
  2. The same for a draft whose slug differs from a live bug's slug only by
     a re-file marker.
  3. A genuinely new draft that mentions the live bug's ID in passing ("as
     BUG-NNNN before it") still deposits with exit 0, as does one citing a
     closed bug, one whose slug differs by a word or number that names the
     defect (still, first, a mid-slug number), and one whose body only
     remarks on the live bug's status or names it in a hypothetical.
  4. The REST twin, POST /operations/intake, returns the refusal's error,
     message and remedy -- the existing ID in the message -- with exit_code
     5, and writes nothing under bugs/.
  5. The ops exit-code table maps the refusal to 5.

Fixture bug IDs are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import (
    SAME_DEFECT_EXIT_CODE,
    Refused,
    SameDefectRefused,
    cli_exit_code,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_INTAKE_SH = _TEAM_DIR / "scripts" / "intake.sh"
_PROJECT = "proj"
_EXISTING_NAME = "BUG-9172-stale-lock-file.md"


def _existing(status: str) -> str:
    return (
        "# BUG-9172-stale-lock-file\n\n**Bug ID:** BUG-9172-stale-lock-file\n"
        f"**Severity:** medium\n\n## Status\n{status}\n\n## Symptom\nThe lock file is left behind.\n"
    )


def _draft(stem: str, symptom: str) -> str:
    return (
        f"# {stem}\n\n**Bug ID:** {stem}\n**Severity:** medium\n\n"
        f"## Status\nopen\n\n## Symptom\n{symptom}\n"
    )


_CITING_SYMPTOM = "Same defect as BUG-9172: the lock file is still left behind after a crash."
_PASSING_SYMPTOM = (
    "The wake truncates the queue file when it is interrupted, failing as BUG-9172 "
    "before it, but on the queue file rather than the lock."
)


def _make_board(base: pathlib.Path, status: str) -> tuple[pathlib.Path, OpsContext]:
    """A throwaway kanban root with one project and one live fixture bug."""
    root = base / "kanban"
    project = root / "projects" / _PROJECT
    (project / "tasks" / "queues").mkdir(parents=True)
    for sub in ("bugs", "priority", "requirements"):
        (project / sub).mkdir()
    (root / "logs").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (project / "bugs" / _EXISTING_NAME).write_text(_existing(status), encoding="utf-8")
    return project, OpsContext(kanban_root=root)


def _snapshot(directory: pathlib.Path) -> dict:
    """Every entry under *directory*, relative name -> bytes (None for a directory)."""
    found = {}
    for path in sorted(directory.rglob("*")):
        found[str(path.relative_to(directory))] = None if path.is_dir() else path.read_bytes()
    return found


def _stage(base: pathlib.Path, name: str, text: str) -> pathlib.Path:
    path = base / "staging" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_intake(root: pathlib.Path, base: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """Run the real intake.sh with *args*, every root-resolving name at *root*."""
    env = build_scrubbed_env(
        home_dir=base / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(base / "tmp"),
        },
    )
    return subprocess.run(
        ["bash", str(_INTAKE_SH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_TEAM_DIR),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# 1 and 2. intake.sh refuses a same-defect draft with exit 5, writing nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["open", "running", "needs-operator"])
def test_intake_sh_refuses_a_draft_citing_a_live_bug_and_writes_nothing(
    tmp_path: pathlib.Path, status: str
) -> None:
    project, ctx = _make_board(tmp_path, status)
    bugs = project / "bugs"
    before = _snapshot(bugs)
    source = _stage(tmp_path, "BUG-9500-lock-not-removed.md", _draft("BUG-9500-lock-not-removed", _CITING_SYMPTOM))

    proc = _run_intake(ctx.kanban_root, tmp_path, "--project", _PROJECT, str(source))

    assert proc.returncode == 5, (proc.stdout, proc.stderr)
    assert _snapshot(bugs) == before
    assert proc.stdout == ""
    contract = parse_error_contract(proc.stderr)
    assert contract is not None, proc.stderr
    assert contract["error"] == "intake.same_defect"
    assert "BUG-9172" in contract["message"]
    assert status in contract["message"]
    assert "<" not in contract["remedy"]
    assert shlex.join(["cat", str(bugs / _EXISTING_NAME)]) in contract["remedy"]


def test_intake_sh_refuses_a_draft_whose_slug_differs_only_by_a_refile_marker(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path, "open")
    bugs = project / "bugs"
    before = _snapshot(bugs)
    source = _stage(
        tmp_path,
        "BUG-9500-stale-lock-file-again.md",
        _draft("BUG-9500-stale-lock-file-again", "The lock file is left behind."),
    )

    proc = _run_intake(ctx.kanban_root, tmp_path, "--project", _PROJECT, str(source))

    assert proc.returncode == 5, (proc.stdout, proc.stderr)
    assert _snapshot(bugs) == before
    contract = parse_error_contract(proc.stderr)
    assert contract is not None, proc.stderr
    assert contract["error"] == "intake.same_defect"
    assert "BUG-9172" in contract["message"]


def test_the_help_and_header_document_exit_five(tmp_path: pathlib.Path) -> None:
    _project, ctx = _make_board(tmp_path, "open")
    proc = _run_intake(ctx.kanban_root, tmp_path, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "  5  Refused: the BUG-* draft reports the same defect as a live bug" in proc.stdout
    header = _INTAKE_SH.read_text(encoding="utf-8")
    assert "#   5   Refused: the BUG-* draft reports the same defect as a live bug" in header


# ---------------------------------------------------------------------------
# 3. A genuinely new draft still deposits
# ---------------------------------------------------------------------------


def test_intake_sh_deposits_a_new_draft_that_mentions_a_live_bug_in_passing(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path, "open")
    bugs = project / "bugs"
    text = _draft("BUG-9500-queue-file-truncated", _PASSING_SYMPTOM)
    source = _stage(tmp_path, "BUG-9500-queue-file-truncated.md", text)

    proc = _run_intake(ctx.kanban_root, tmp_path, "--project", _PROJECT, str(source))

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    deposited = bugs / "BUG-9500-queue-file-truncated.md"
    assert proc.stdout.strip() == f"intake: deposited {deposited}"
    assert deposited.read_text(encoding="utf-8") == text
    assert (bugs / _EXISTING_NAME).read_text(encoding="utf-8") == _existing("open")


@pytest.mark.parametrize(
    ("name", "symptom"),
    [
        ("BUG-9500-stale-lock-file-still.md", "The lock file is left behind."),
        ("BUG-9500-first-stale-lock-file.md", "The lock file is left behind."),
        ("BUG-9500-stale-lock-2-file.md", "The lock file is left behind."),
        ("BUG-9500-queue-file-truncated.md", "Related: BUG-9172 is still open, but this is a different defect."),
        ("BUG-9500-queue-file-truncated.md", "When BUG-9172 recurs the check reads clean."),
    ],
)
def test_intake_sh_deposits_a_distinct_draft_the_same_defect_rule_does_not_match(
    tmp_path: pathlib.Path, name: str, symptom: str
) -> None:
    project, ctx = _make_board(tmp_path, "open")
    bugs = project / "bugs"
    text = _draft(name[:-3], symptom)
    source = _stage(tmp_path, name, text)

    proc = _run_intake(ctx.kanban_root, tmp_path, "--project", _PROJECT, str(source))

    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert (bugs / name).read_text(encoding="utf-8") == text
    assert (bugs / _EXISTING_NAME).read_text(encoding="utf-8") == _existing("open")


@pytest.mark.parametrize("status", ["done", "wont-do", "duplicate"])
def test_a_draft_citing_a_closed_bug_deposits(tmp_path: pathlib.Path, status: str) -> None:
    project, ctx = _make_board(tmp_path, status)
    source = _stage(tmp_path, "BUG-9500-lock-not-removed.md", _draft("BUG-9500-lock-not-removed", _CITING_SYMPTOM))

    deposited = ops_write.deposit_intake(ctx, _PROJECT, source)

    assert deposited == project / "bugs" / "BUG-9500-lock-not-removed.md"
    assert deposited.is_file()


# ---------------------------------------------------------------------------
# 4. The REST twin returns the same refusal
# ---------------------------------------------------------------------------


def test_post_operations_intake_returns_the_same_defect_refusal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    project, ctx = _make_board(tmp_path, "needs-operator")
    bugs = project / "bugs"
    before = _snapshot(bugs)
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(ctx.kanban_root))
    monkeypatch.setenv("KANBAN_ROOT", str(ctx.kanban_root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=ctx.kanban_root)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as client:
        resp = client.post(
            "/operations/intake",
            json={
                "project": _PROJECT,
                "filename": "BUG-9500-lock-not-removed.md",
                "content": _draft("BUG-9500-lock-not-removed", _CITING_SYMPTOM),
            },
        )

    body = resp.json()
    assert resp.status_code == 409, body
    assert body["exit_code"] == SAME_DEFECT_EXIT_CODE == 5
    assert body["error"] == "intake.same_defect"
    assert "BUG-9172" in body["message"]
    assert body["remedy"].strip() and "<" not in body["remedy"]
    assert _EXISTING_NAME in body["remedy"]
    assert _snapshot(bugs) == before


# ---------------------------------------------------------------------------
# 5. The refusal's exception and exit code
# ---------------------------------------------------------------------------


def test_deposit_intake_raises_same_defect_refused_naming_the_live_bug(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path, "running")
    source = _stage(tmp_path, "BUG-9500-lock-not-removed.md", _draft("BUG-9500-lock-not-removed", _CITING_SYMPTOM))

    with pytest.raises(SameDefectRefused) as excinfo:
        ops_write.deposit_intake(ctx, _PROJECT, source)

    assert isinstance(excinfo.value, Refused)
    assert excinfo.value.existing_id == "BUG-9172"
    assert cli_exit_code(excinfo.value) == 5
    assert sorted(os.listdir(project / "bugs")) == [_EXISTING_NAME]
