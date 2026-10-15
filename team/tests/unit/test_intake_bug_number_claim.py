"""
test_intake_bug_number_claim.py -- a BUG-* draft deposited through intake
claims its number atomically.

What is proven here, against throwaway kanban roots only:

  1. A draft whose number is taken is deposited as the next free number by
     the real intake.sh: the file is renamed, its title line and
     **Bug ID:** line name the new number, every other byte is the draft's,
     stdout is one line naming the deposited file, and the file that held
     the number is untouched.
  2. A draft whose number is free is deposited byte-identical under its own
     name.
  3. A BUG-* name off the ledger filename contract -- a dot or an uppercase
     letter in the slug -- is refused with a non-zero exit before anything
     is written, naming the contract's malformed-name text (build_remedy,
     what discovery prints).
  4. Two depositors in separate processes carrying one number end with two
     files under distinct numbers and no claim lock left -- once with the
     interleaving forced (one depositor held while it holds the claim), once
     left to the scheduler.
  5. The claim itself: claim_next_bug_id takes a preferred number when it
     is free, falls back to the next free number when a file or a held lock
     carries it, and never hands out a number a file took between its scan
     and its lock.
  6. The REST twin (POST /operations/intake) renumbers through the same
     function.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time
from unittest.mock import patch

import pytest

from pgai_agent_kanban.lib.bug_filing import load_bug_scanner
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.lib.ledger_filename_contract import build_remedy
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import OpsError, Refused
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_INTAKE_SH = _TEAM_DIR / "scripts" / "intake.sh"
_PROJECT = "proj"

_EXISTING_0010 = b"# BUG-0010-already-filed\n\n**Bug ID:** BUG-0010-already-filed\n\n## Status\nopen\n"


def _draft(stem: str) -> bytes:
    """A bug draft whose title and **Bug ID:** lines name *stem*.

    The body mentions the draft's number elsewhere too, so a rewrite that
    reaches past the two identifier lines shows up as a byte difference.
    """
    return (
        f"# {stem}\n"
        "\n"
        f"**Bug ID:** {stem}\n"
        "**Severity:** medium\n"
        "\n"
        "## Status\nopen\n"
        "\n"
        "## Symptom\n"
        "Seen while filing BUG-0010 by hand; see also BUG-0009.\n"
        "Trailing line without a newline \u2014 kept as is"
    ).encode("utf-8")


def _expected_renumbered(draft: bytes, old_stem: str, new_stem: str) -> bytes:
    """*draft* with exactly its title line and **Bug ID:** line renamed."""
    return draft.replace(
        f"# {old_stem}\n".encode(), f"# {new_stem}\n".encode(), 1
    ).replace(
        f"**Bug ID:** {old_stem}\n".encode(), f"**Bug ID:** {new_stem}\n".encode(), 1
    )


def _make_board(base: pathlib.Path) -> tuple[pathlib.Path, OpsContext]:
    """A throwaway kanban root with one registered project and its intake dirs."""
    root = base / "kanban"
    project = root / "projects" / _PROJECT
    (project / "tasks" / "queues").mkdir(parents=True)
    for sub in ("bugs", "priority", "requirements"):
        (project / sub).mkdir()
    (root / "logs").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    return project, OpsContext(kanban_root=root)


def _stage(path: pathlib.Path, data: bytes) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _intake_env(root: pathlib.Path, base: pathlib.Path, extra: "dict | None" = None) -> dict:
    """The sandbox environment: every root-resolving name points at *root*."""
    env_extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(base / "tmp"),
    }
    env_extra.update(extra or {})
    return build_scrubbed_env(home_dir=base / "home", extra_env=env_extra)


def _run_intake(root: pathlib.Path, base: pathlib.Path, source: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_INTAKE_SH), "--project", _PROJECT, str(source)],
        capture_output=True,
        text=True,
        env=_intake_env(root, base),
        cwd=str(_TEAM_DIR),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def _claim_locks(bugs: pathlib.Path) -> list[str]:
    return sorted(n for n in os.listdir(bugs) if n.startswith(".claim-"))


def _temp_files(bugs: pathlib.Path) -> list[str]:
    return sorted(n for n in os.listdir(bugs) if n.startswith(".deposit_intake_tmp_"))


def _bug_files(bugs: pathlib.Path) -> list[str]:
    return sorted(n for n in os.listdir(bugs) if n.startswith("BUG-"))


# ---------------------------------------------------------------------------
# 1 and 2. Taken number renumbers; free number deposits as named
# ---------------------------------------------------------------------------


def test_intake_sh_renumbers_a_draft_whose_number_is_taken(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    (bugs / "BUG-0010-already-filed.md").write_bytes(_EXISTING_0010)
    draft = _draft("BUG-0010-claim-me")
    source = _stage(tmp_path / "staging" / "BUG-0010-claim-me.md", draft)

    result = _run_intake(ctx.kanban_root, tmp_path, source)

    assert result.returncode == 0, (result.stdout, result.stderr)
    deposited = bugs / "BUG-0011-claim-me.md"
    assert result.stdout.splitlines() == [f"intake: deposited {deposited}"], result.stdout
    assert deposited.read_bytes() == _expected_renumbered(draft, "BUG-0010-claim-me", "BUG-0011-claim-me")
    assert (bugs / "BUG-0010-already-filed.md").read_bytes() == _EXISTING_0010
    assert _bug_files(bugs) == ["BUG-0010-already-filed.md", "BUG-0011-claim-me.md"]
    assert _claim_locks(bugs) == [] and _temp_files(bugs) == []
    assert deposited.stat().st_mode & 0o777 == 0o644
    assert "BUG-0010 is already taken" in result.stderr, result.stderr
    assert source.read_bytes() == draft, "the staged draft must not be modified"


def test_intake_sh_deposits_a_free_number_byte_identical_under_its_own_name(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    (bugs / "BUG-0009-lower-number.md").write_bytes(b"# BUG-0009-lower-number\n")
    draft = _draft("BUG-0010-claim-me")
    source = _stage(tmp_path / "staging" / "BUG-0010-claim-me.md", draft)

    result = _run_intake(ctx.kanban_root, tmp_path, source)

    assert result.returncode == 0, (result.stdout, result.stderr)
    deposited = bugs / "BUG-0010-claim-me.md"
    assert result.stdout.splitlines() == [f"intake: deposited {deposited}"], result.stdout
    assert deposited.read_bytes() == draft
    assert _bug_files(bugs) == ["BUG-0009-lower-number.md", "BUG-0010-claim-me.md"]
    assert _claim_locks(bugs) == [] and _temp_files(bugs) == []
    assert "already taken" not in result.stderr, result.stderr


def test_renumbering_keeps_a_dated_name_s_date_and_slug(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    (bugs / "BUG-0010-already-filed.md").write_bytes(_EXISTING_0010)
    draft = _draft("BUG-0010-20261015-dated-draft")
    source = _stage(tmp_path / "BUG-0010-20261015-dated-draft.md", draft)

    deposited = ops_write.deposit_intake(ctx, _PROJECT, source)

    assert deposited == bugs / "BUG-0011-20261015-dated-draft.md"
    assert deposited.read_bytes() == _expected_renumbered(
        draft, "BUG-0010-20261015-dated-draft", "BUG-0011-20261015-dated-draft"
    )


def test_a_name_taken_at_placement_by_an_unclaimed_writer_is_kept_and_the_draft_renumbers(
    tmp_path: pathlib.Path,
) -> None:
    """A writer that skips the claim lands the draft's name mid-deposit: its file survives."""
    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    draft = _draft("BUG-0010-claim-me")
    source = _stage(tmp_path / "BUG-0010-claim-me.md", draft)
    intruder = b"# landed without a claim\n"
    real_copy2 = shutil.copy2
    fired: list[bool] = []

    def copy_then_land(src, dst, *args, **kwargs):
        result = real_copy2(src, dst, *args, **kwargs)
        if not fired:
            fired.append(True)
            (bugs / "BUG-0010-claim-me.md").write_bytes(intruder)
        return result

    with patch("shutil.copy2", new=copy_then_land):
        deposited = ops_write.deposit_intake(ctx, _PROJECT, source)

    assert deposited == bugs / "BUG-0011-claim-me.md"
    assert (bugs / "BUG-0010-claim-me.md").read_bytes() == intruder
    assert deposited.read_bytes() == _expected_renumbered(draft, "BUG-0010-claim-me", "BUG-0011-claim-me")
    assert _claim_locks(bugs) == [] and _temp_files(bugs) == []


def test_priority_and_requirements_deposits_are_not_claimed_or_renumbered(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    (project / "priority" / "PRIORITY-0010-taken.md").write_text("first\n", encoding="utf-8")
    source = _stage(tmp_path / "PRIORITY-0010-taken.md", b"second\n")

    with pytest.raises(OpsError, match="no clobber"):
        ops_write.deposit_intake(ctx, _PROJECT, source)

    assert sorted(os.listdir(project / "priority")) == ["PRIORITY-0010-taken.md"]
    assert os.listdir(project / "bugs") == []


# ---------------------------------------------------------------------------
# 3. A slug off the filename contract is refused before anything is written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["BUG-0010-dotted.slug.md", "BUG-0010-Upper-Case.md", "BUG-0010-v1.2.3-notes.md"],
)
def test_intake_sh_refuses_a_slug_with_a_dot_or_uppercase_before_writing(
    name: str, tmp_path: pathlib.Path
) -> None:
    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    (bugs / "BUG-0010-already-filed.md").write_bytes(_EXISTING_0010)
    before = sorted(os.listdir(bugs))
    source = _stage(tmp_path / "staging" / name, _draft(name[:-3]))

    result = _run_intake(ctx.kanban_root, tmp_path, source)

    assert result.returncode != 0, (result.stdout, result.stderr)
    assert result.stdout == "", result.stdout
    assert sorted(os.listdir(bugs)) == before, "a refused draft left something in bugs/"
    triple = parse_error_contract(result.stderr)
    assert triple is not None, result.stderr
    assert triple["error"] == "intake.bug_filename_malformed", triple
    assert build_remedy(name, "BUG", bugs) in triple["message"], triple["message"]
    assert "BUG-NNNN-<slug>.md" in triple["message"], triple["message"]
    assert "<" not in triple["remedy"], triple["remedy"]


def test_the_refusal_remedy_renames_to_a_name_intake_accepts(tmp_path: pathlib.Path) -> None:
    project, ctx = _make_board(tmp_path)
    source = _stage(tmp_path / "staging" / "BUG-0010-My.Odd_Slug.md", _draft("BUG-0010-My.Odd_Slug"))

    with pytest.raises(Refused) as caught:
        ops_write.deposit_intake(ctx, _PROJECT, source)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None, str(caught.value)
    renamed = source.with_name("BUG-0010-my-odd-slug.md")
    assert f"mv {source} {renamed}" in triple["remedy"], triple["remedy"]
    renamed.write_bytes(source.read_bytes())
    assert ops_write.deposit_intake(ctx, _PROJECT, renamed) == project / "bugs" / "BUG-0010-my-odd-slug.md"


# ---------------------------------------------------------------------------
# 4. Concurrent depositors in separate processes take distinct numbers
# ---------------------------------------------------------------------------

# Loaded into depositor A's interpreter through its PYTHONPATH: after A has
# claimed its number and copied the draft, it touches ``reached`` and waits
# for ``release``, so depositor B runs while A holds the claim.
_HOLD_AFTER_COPY = (
    "import os\n"
    "import shutil\n"
    "import time\n"
    "_barrier = os.environ.get('INTAKE_TEST_BARRIER_DIR')\n"
    "if _barrier:\n"
    "    _real_copy2 = shutil.copy2\n"
    "    def _held_copy2(src, dst, *args, **kwargs):\n"
    "        result = _real_copy2(src, dst, *args, **kwargs)\n"
    "        open(os.path.join(_barrier, 'reached'), 'w').close()\n"
    "        deadline = time.monotonic() + 30\n"
    "        while not os.path.exists(os.path.join(_barrier, 'release')):\n"
    "            if time.monotonic() > deadline:\n"
    "                raise RuntimeError('barrier release never arrived')\n"
    "            time.sleep(0.01)\n"
    "        return result\n"
    "    shutil.copy2 = _held_copy2\n"
)


def test_a_depositor_holding_the_claim_forces_a_concurrent_one_to_the_next_number(
    tmp_path: pathlib.Path,
) -> None:
    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    draft_a = _draft("BUG-0010-depositor-a")
    draft_b = _draft("BUG-0010-depositor-b")
    source_a = _stage(tmp_path / "a" / "BUG-0010-depositor-a.md", draft_a)
    source_b = _stage(tmp_path / "b" / "BUG-0010-depositor-b.md", draft_b)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(_HOLD_AFTER_COPY, encoding="utf-8")

    proc_a = subprocess.Popen(
        ["bash", str(_INTAKE_SH), "--project", _PROJECT, str(source_a)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_intake_env(
            ctx.kanban_root, tmp_path, {"PYTHONPATH": str(shim), "INTAKE_TEST_BARRIER_DIR": str(barrier)}
        ),
        cwd=str(_TEAM_DIR),
        stdin=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while not (barrier / "reached").exists():
            if proc_a.poll() is not None:
                out, err = proc_a.communicate()
                pytest.fail(f"depositor A exited before the barrier (rc={proc_a.returncode}).\n{out}\n{err}")
            assert time.monotonic() < deadline, "depositor A never reached the barrier"
            time.sleep(0.01)
        assert _claim_locks(bugs) == [".claim-0010.lock"], "depositor A is not holding its claim"

        result_b = _run_intake(ctx.kanban_root, tmp_path, source_b)
    finally:
        (barrier / "release").touch()
        out_a, err_a = proc_a.communicate(timeout=60)

    assert proc_a.returncode == 0, (out_a, err_a)
    assert result_b.returncode == 0, (result_b.stdout, result_b.stderr)
    assert _bug_files(bugs) == ["BUG-0010-depositor-a.md", "BUG-0011-depositor-b.md"]
    assert (bugs / "BUG-0010-depositor-a.md").read_bytes() == draft_a
    assert (bugs / "BUG-0011-depositor-b.md").read_bytes() == _expected_renumbered(
        draft_b, "BUG-0010-depositor-b", "BUG-0011-depositor-b"
    )
    assert _claim_locks(bugs) == [] and _temp_files(bugs) == []


def test_two_unsynchronised_depositors_of_one_name_end_with_two_distinct_numbers(
    tmp_path: pathlib.Path,
) -> None:
    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    sources = [
        _stage(tmp_path / side / "BUG-0010-same-draft.md", _draft("BUG-0010-same-draft"))
        for side in ("a", "b")
    ]
    env = _intake_env(ctx.kanban_root, tmp_path)

    procs = [
        subprocess.Popen(
            ["bash", str(_INTAKE_SH), "--project", _PROJECT, str(src)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(_TEAM_DIR),
            stdin=subprocess.DEVNULL,
        )
        for src in sources
    ]
    outputs = [p.communicate(timeout=60) for p in procs]

    assert [p.returncode for p in procs] == [0, 0], outputs
    assert _bug_files(bugs) == ["BUG-0010-same-draft.md", "BUG-0011-same-draft.md"]
    assert _claim_locks(bugs) == [] and _temp_files(bugs) == []
    printed = sorted(out.strip() for out, _err in outputs)
    assert printed == sorted(f"intake: deposited {bugs / n}" for n in _bug_files(bugs)), outputs


# ---------------------------------------------------------------------------
# 5. claim_next_bug_id with a preferred number
# ---------------------------------------------------------------------------


@pytest.fixture
def scanner():
    return load_bug_scanner()


@pytest.fixture
def bugs_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """An empty bugs/ directory of this test's own."""
    path = tmp_path / "bugs"
    path.mkdir()
    return path


def test_claim_takes_the_preferred_number_when_it_is_free(scanner, bugs_dir: pathlib.Path) -> None:
    (bugs_dir / "BUG-0020-higher.md").write_text("x\n", encoding="utf-8")

    bug_id, bug_path, lock = scanner.claim_next_bug_id(str(bugs_dir), "slug", "t", preferred_num=7)
    try:
        assert (bug_id, bug_path.name, lock.name) == ("BUG-0007", "BUG-0007-slug.md", ".claim-0007.lock")
    finally:
        scanner.release_bug_id_claim(lock)


def test_claim_falls_back_past_a_preferred_number_a_file_carries(scanner, bugs_dir: pathlib.Path) -> None:
    (bugs_dir / "BUG-0007-Has.Dots.md").write_text("x\n", encoding="utf-8")
    (bugs_dir / "BUG-0012-highest.md").write_text("x\n", encoding="utf-8")

    bug_id, _path, lock = scanner.claim_next_bug_id(str(bugs_dir), "slug", "t", preferred_num=7)
    scanner.release_bug_id_claim(lock)

    assert bug_id == "BUG-0013"
    assert _claim_locks(bugs_dir) == []


def test_claim_falls_back_past_a_preferred_number_another_caller_holds(scanner, bugs_dir: pathlib.Path) -> None:
    held_id, _path, held_lock = scanner.claim_next_bug_id(str(bugs_dir), "first", "t", preferred_num=7)
    try:
        bug_id, _path, lock = scanner.claim_next_bug_id(str(bugs_dir), "second", "t", preferred_num=7)
        scanner.release_bug_id_claim(lock)
    finally:
        scanner.release_bug_id_claim(held_lock)

    assert (held_id, bug_id) == ("BUG-0007", "BUG-0008")


def test_claim_never_returns_a_number_a_file_took_after_the_scan(
    scanner, bugs_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing claimer wrote its file and released its lock between this caller's scan and lock."""
    (bugs_dir / "BUG-0001-landed-in-the-window.md").write_text("x\n", encoding="utf-8")
    scans: list[int] = []
    real_scan = scanner._scan_highest_bug_num

    def stale_first_scan(path):
        scans.append(1)
        return 0 if len(scans) == 1 else real_scan(path)

    monkeypatch.setattr(scanner, "_scan_highest_bug_num", stale_first_scan)

    bug_id, _path, lock = scanner.claim_next_bug_id(str(bugs_dir), "slug", "t")
    scanner.release_bug_id_claim(lock)

    assert bug_id == "BUG-0002"
    assert len(scans) == 2, "the stale scan's number was not re-checked after locking"
    assert _claim_locks(bugs_dir) == []


@pytest.mark.parametrize("bad", [0, 10000, -1, "7", True])
def test_claim_refuses_a_preferred_number_outside_the_four_digit_field(
    scanner, bad, bugs_dir: pathlib.Path
) -> None:
    with pytest.raises(ValueError, match="preferred_num"):
        scanner.claim_next_bug_id(str(bugs_dir), "slug", "t", preferred_num=bad)
    assert os.listdir(bugs_dir) == []


# ---------------------------------------------------------------------------
# 6. The REST twin renumbers through the same function
# ---------------------------------------------------------------------------


def test_post_operations_intake_renumbers_a_taken_number(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    project, ctx = _make_board(tmp_path)
    bugs = project / "bugs"
    (bugs / "BUG-0010-already-filed.md").write_bytes(_EXISTING_0010)
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(ctx.kanban_root))
    monkeypatch.setenv("KANBAN_ROOT", str(ctx.kanban_root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "server_tmp"))
    draft = _draft("BUG-0010-over-rest").decode("utf-8")

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=ctx.kanban_root)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as client:
        resp = client.post(
            "/operations/intake",
            json={"project": _PROJECT, "filename": "BUG-0010-over-rest.md", "content": draft},
        )

    body = resp.json()
    assert resp.status_code == 200, body
    deposited = bugs / "BUG-0011-over-rest.md"
    assert body["stdout"] in (
        f"intake: deposited {deposited}\n",
        f"intake: deposited {deposited.resolve()}\n",
    ), body
    assert deposited.read_text(encoding="utf-8") == _expected_renumbered(
        draft.encode("utf-8"), "BUG-0010-over-rest", "BUG-0011-over-rest"
    ).decode("utf-8")
    assert (bugs / "BUG-0010-already-filed.md").read_bytes() == _EXISTING_0010
    assert _claim_locks(bugs) == []
