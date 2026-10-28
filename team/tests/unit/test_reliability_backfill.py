"""
test_reliability_backfill.py
============================
Unit coverage for ``team/scripts/reliability-backfill.sh`` and the
``pgai_agent_kanban.metrics.reliability_backfill`` module behind it: the
operator back-fill that rebuilds block-ledger records (class, cause,
reason_code) from existing evidence into a sidecar, and merges it with
``--apply``.

The fixture, ``team/tests/fixtures/reliability_backfill/``, is a read-only
copy of this project's own evidence: the block ledger, the sentinel-writes
log (``sentinel-writes.txt``), release-state.md, the RC rollups the ledger's
blocks fall in plus the RC after the one with no rollup (``rc/``), and the
status.md and triage JSON of the two archived task folders that hold triage
evidence (``task-folders/``). It is stored flat because the repository
ignores ``projects/``, ``tasks/``, ``artifacts/`` and ``*.log``, and copied
byte for byte except the operator's home directory and account name,
rewritten to the placeholder ``op``. Each test lays it out as a kanban root
under tmp_path (_LAYOUT), with ``/home/op/pgai_agent_kanban`` rewritten to
the sandbox root so the sentinel-writes log names the sandbox's own
sentinels, and runs the script with the five live-install names repointed or
cleared.

What is pinned here:
  - Without --apply, the sidecar holds the 2026-10-08 block (the v2.32.3
    halt:project interval and the CM release task's block) as class human,
    chain-caused, and the 2026-10-12 v2.37.7 halt:project interval as class
    any; the ledger is byte-identical and nothing else is written. The
    sidecar, stripped of its review annotations, reads as a ledger with
    those classes.
  - --apply appends records; a second --apply and a second run without it
    add none; every original ledger line is still there, unchanged.
  - After --apply, metrics/reliability.json exists and its Hands-off last
    block is the 2026-10-08 block.
  - Every refusal (no project, unknown project, unknown argument, malformed
    ledger, no sidecar, malformed sidecar) is dual-channel, exits 2 and
    writes nothing.
  - On synthetic ledgers: a HALT the sentinel-writes log attributes to an
    operator reads cause operator and stays class any; a task BLOCKED now
    keeps its block open and a Block Record with no ledger block opens one;
    a sidecar end line whose block the ledger has since closed is skipped,
    not written onto another block.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import shlex
import shutil

import pytest

from pgai_agent_kanban.metrics.blocked_ledger import (
    append_block_end,
    append_block_start,
    block_intervals,
    parse_records,
)
from pgai_agent_kanban.metrics.reliability_backfill import (
    ANNOTATION_KEY,
    SIDECAR_FILENAME,
    apply_sidecar,
    load_evidence,
    read_sidecar,
    rebuild,
    write_sidecar,
)
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_DIR / "scripts" / "reliability-backfill.sh"
_FIXTURE = _TEAM_DIR / "tests" / "fixtures" / "reliability_backfill"
_FIXTURE_ROOT_PATH = "/home/op/pgai_agent_kanban"

PROJECT = "pgai-agent-kanban"

#: Where each fixture entry lands in the sandbox kanban root.
_LAYOUT = {
    "blocked-intervals.jsonl": f"projects/{PROJECT}/metrics/blocked-intervals.jsonl",
    "rc": f"projects/{PROJECT}/metrics/rc",
    "release-state.md": f"projects/{PROJECT}/release-state.md",
    "sentinel-writes.txt": "logs/sentinel-writes.log",
}
CM_TASK = "task:CM-20261007-060-release-2-32-3"


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------


def _materialise(tmp_path: pathlib.Path) -> pathlib.Path:
    """Lay the evidence fixture out as a sandbox kanban root under *tmp_path*."""
    root = tmp_path / "root"
    for name, target in _LAYOUT.items():
        source, dest = _FIXTURE / name, root / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if source.is_dir() else shutil.copyfile)(source, dest)
    for folder in sorted((_FIXTURE / "task-folders").iterdir()):
        task = root / "archive" / PROJECT / "tasks" / folder.name
        evidence = task / "artifacts" / "block-evidence"
        evidence.mkdir(parents=True)
        shutil.copyfile(folder / "status.md", task / "status.md")
        for item in sorted((folder / "block-evidence").iterdir()):
            shutil.copyfile(item, evidence / item.name)
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if _FIXTURE_ROOT_PATH in text:
                path.write_text(text.replace(_FIXTURE_ROOT_PATH, str(root)), encoding="utf-8")
    (root / "tmp").mkdir()
    return root


def _metrics(root: pathlib.Path, project: str = PROJECT) -> pathlib.Path:
    return root / "projects" / project / "metrics"


def _ledger(root: pathlib.Path) -> pathlib.Path:
    return _metrics(root) / "blocked-intervals.jsonl"


def _sidecar(root: pathlib.Path) -> pathlib.Path:
    return _metrics(root) / SIDECAR_FILENAME


_REFUSAL_LEDGER = "refusal-ledger.md"


def _tree(root: pathlib.Path) -> dict:
    """Every file under *root* outside its tmp/ scratch, with its bytes. A
    project's refusal-ledger.md, where every dual-channel refusal records
    itself, is left out: a refusal writing it is the house contract."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).parts[0] != "tmp" and p.name != _REFUSAL_LEDGER
    }


def _run(root: pathlib.Path, args: list, tmp_path: pathlib.Path):
    """Run the script against the sandbox *root*, every live-install name repointed or cleared."""
    argv = " ".join(shlex.quote(a) for a in args)
    script = (
        "env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PGAI_PROJECT_NAME -u PYTHONPATH "
        f"KANBAN_ROOT={shlex.quote(str(root))} "
        f"PGAI_AGENT_KANBAN_ROOT_PATH={shlex.quote(str(root))} "
        f"PGAI_AGENT_KANBAN_TEMP_DIR={shlex.quote(str(root / 'tmp'))} "
        f"{shlex.quote(str(_SCRIPT))} {argv}"
    )
    return run_bash(tmp_path, script, timeout=120)


def _ok(result) -> None:
    assert result.returncode == 0, f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _sidecar_records(root: pathlib.Path) -> list:
    return [json.loads(line) for line in _sidecar(root).read_text(encoding="utf-8").splitlines() if line.strip()]


def _block_lines(records: list, version: str, source: str) -> list:
    return [r for r in records if r["version"] == version and r["source"] == source]


def _as_ledger(records: list, path: pathlib.Path) -> list:
    """The sidecar records stripped of their annotation, read back as a ledger."""
    path.write_text(
        "".join(json.dumps({k: v for k, v in r.items() if k != ANNOTATION_KEY}) + "\n" for r in records),
        encoding="utf-8",
    )
    return block_intervals(str(path))


# ---------------------------------------------------------------------------
# This project's evidence
# ---------------------------------------------------------------------------


class TestProjectEvidence:
    def test_dry_run_writes_only_the_sidecar(self, tmp_path: pathlib.Path) -> None:
        root = _materialise(tmp_path)
        before = _tree(root)

        result = _run(root, ["--project", PROJECT], tmp_path)

        _ok(result)
        after = _tree(root)
        sidecar_rel = str(_sidecar(root).relative_to(root))
        assert set(after) == set(before) | {sidecar_rel}
        for rel, data in before.items():
            assert after[rel] == data, f"{rel} changed without --apply"
        assert str(_sidecar(root)) in result.stdout

    def test_sidecar_classes_the_known_blocks(self, tmp_path: pathlib.Path) -> None:
        root = _materialise(tmp_path)
        _ok(_run(root, ["--project", PROJECT], tmp_path))
        records = _sidecar_records(root)

        halt_1008 = _block_lines(records, "v2.32.3", "halt:project")
        task_1008 = _block_lines(records, "v2.32.3", CM_TASK)
        halt_1012 = _block_lines(records, "v2.37.7", "halt:project")
        assert halt_1008 and task_1008 and halt_1012
        for line in halt_1008 + task_1008:
            assert line[ANNOTATION_KEY]["block_class"] == "human", line
        for line in halt_1012:
            assert line[ANNOTATION_KEY]["block_class"] == "any", line
        assert {line["cause"] for line in halt_1008 + task_1008 if "cause" in line} == {"chain"}
        assert halt_1008[0]["timestamp"].startswith("2026-10-08")
        assert halt_1012[0]["timestamp"].startswith("2026-10-12")

        blocks = {(b["version"], b["source"], b["start"]): b for b in _as_ledger(records, tmp_path / "read.jsonl")}
        assert blocks[("v2.32.3", "halt:project", halt_1008[0]["timestamp"])]["class"] == "human"
        assert blocks[("v2.32.3", CM_TASK, task_1008[0]["timestamp"])]["class"] == "human"
        assert blocks[("v2.37.7", "halt:project", halt_1012[0]["timestamp"])]["class"] == "any"

    def test_apply_merges_once_and_keeps_every_original_line(self, tmp_path: pathlib.Path) -> None:
        root = _materialise(tmp_path)
        original = _ledger(root).read_bytes()
        _ok(_run(root, ["--project", PROJECT], tmp_path))

        first = _run(root, ["--project", PROJECT, "--apply"], tmp_path)
        _ok(first)
        merged = _ledger(root).read_bytes()
        assert merged != original
        assert merged.startswith(original), "an original ledger line was rewritten"
        assert "appended: 0;" not in first.stdout

        second = _run(root, ["--project", PROJECT, "--apply"], tmp_path)
        _ok(second)
        assert "appended: 0;" in second.stdout
        assert _ledger(root).read_bytes() == merged

        rerun = _run(root, ["--project", PROJECT], tmp_path)
        _ok(rerun)
        assert all(r[ANNOTATION_KEY]["in_ledger"] for r in _sidecar_records(root))
        assert _ledger(root).read_bytes() == merged

        original_lines = original.decode("utf-8").splitlines()
        assert _ledger(root).read_text(encoding="utf-8").splitlines()[: len(original_lines)] == original_lines

    def test_apply_refreshes_reliability_json(self, tmp_path: pathlib.Path) -> None:
        root = _materialise(tmp_path)
        _ok(_run(root, ["--project", PROJECT], tmp_path))
        _ok(_run(root, ["--project", PROJECT, "--apply"], tmp_path))

        document = json.loads((_metrics(root) / "reliability.json").read_text(encoding="utf-8"))
        last = document["hands_off"]["last_block"]
        assert last["time"].startswith("2026-10-08")
        assert last["rc"] == "v2.32.3"
        assert last["class"] == "human"
        clean = document["clean"]["last_block"]
        assert (clean["rc"], clean["source"]) == ("v2.37.7", "halt:project")

    def test_help_names_the_sidecar(self, tmp_path: pathlib.Path) -> None:
        root = _materialise(tmp_path)
        result = _run(root, ["--help"], tmp_path)
        _ok(result)
        assert SIDECAR_FILENAME in result.stdout
        assert "--apply" in result.stdout


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestRefusals:
    @pytest.mark.parametrize(
        "args, error",
        [
            ([], "reliability-backfill.project_required"),
            (["--project", "no-such-project"], "reliability-backfill.unknown-project"),
            (["--project", PROJECT, "--bogus"], "reliability-backfill.unknown_argument"),
            (["--project", PROJECT, "--apply"], "reliability-backfill.no-sidecar"),
        ],
    )
    def test_refused_and_nothing_written(self, tmp_path: pathlib.Path, args: list, error: str) -> None:
        root = _materialise(tmp_path)
        before = _tree(root)

        result = _run(root, args, tmp_path)

        assert result.returncode == 2, result.stdout + result.stderr
        assert f"error:   {error}" in result.stderr
        assert "message: " in result.stderr and "remedy:  " in result.stderr
        assert _tree(root) == before
        assert not (root / "projects" / "no-such-project").exists()

    @pytest.mark.parametrize("apply", [False, True])
    def test_malformed_ledger_refused(self, tmp_path: pathlib.Path, apply: bool) -> None:
        root = _materialise(tmp_path)
        if apply:
            _ok(_run(root, ["--project", PROJECT], tmp_path))
        with _ledger(root).open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        before = _tree(root)

        result = _run(root, ["--project", PROJECT] + (["--apply"] if apply else []), tmp_path)

        assert result.returncode == 2, result.stdout + result.stderr
        assert "error:   reliability-backfill.malformed-ledger" in result.stderr
        assert f"remedy:  cat -n {_ledger(root)}" in result.stderr
        assert _tree(root) == before

    def test_malformed_sidecar_refused(self, tmp_path: pathlib.Path) -> None:
        root = _materialise(tmp_path)
        _ok(_run(root, ["--project", PROJECT], tmp_path))
        with _sidecar(root).open("a", encoding="utf-8") as handle:
            handle.write('{"version": "v2.37.7", "kind": "block-pause"}\n')
        before = _tree(root)

        result = _run(root, ["--project", PROJECT, "--apply"], tmp_path)

        assert result.returncode == 2, result.stdout + result.stderr
        assert "error:   reliability-backfill.malformed-sidecar" in result.stderr
        assert _tree(root) == before


# ---------------------------------------------------------------------------
# Synthetic evidence
# ---------------------------------------------------------------------------


def _project(tmp_path: pathlib.Path, *, active_rc: str = "none", opened_at: str = "none") -> pathlib.Path:
    root = tmp_path / "root"
    project = root / "projects" / "demo"
    (project / "metrics" / "rc").mkdir(parents=True)
    (project / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{active_rc}\n\n## RC Opened At\n{opened_at}\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    return root


def _rollup(root: pathlib.Path, version: str, opened: str, closed: str) -> None:
    (root / "projects" / "demo" / "metrics" / "rc" / f"{version}.json").write_text(
        json.dumps({"rc": version, "outcome": "shipped", "opened_at": opened, "closed_at": closed}),
        encoding="utf-8",
    )


def _status(root: pathlib.Path, task: str, state: str, block_record: str = "") -> None:
    folder = root / "projects" / "demo" / "tasks" / task
    folder.mkdir(parents=True)
    (folder / "status.md").write_text(
        f"# Status\n\n## Task\n{task}\n\n## State\n{state}\n" + (f"\n## Block Record\n{block_record}\n" if block_record else ""),
        encoding="utf-8",
    )


def _demo_ledger(root: pathlib.Path) -> str:
    return str(root / "projects" / "demo" / "metrics" / "blocked-intervals.jsonl")


class TestSyntheticEvidence:
    def test_operator_halt_reads_operator_and_any(self, tmp_path: pathlib.Path) -> None:
        root = _project(tmp_path)
        _rollup(root, "v1.0.0", "2026-10-01T00:00:00+00:00", "2026-10-01T12:00:00+00:00")
        ledger = _demo_ledger(root)
        append_block_start(ledger, "v1.0.0", "halt:project", "2026-10-01T02:00:00+00:00")
        append_block_end(ledger, "v1.0.0", "halt:project", "2026-10-01T03:00:00+00:00")
        append_block_start(ledger, "v1.0.0", "task:CM-demo-release", "2026-10-01T02:00:05+00:00")
        halt = root / "projects" / "demo" / "HALT"
        (root / "logs").mkdir()
        armed = datetime.datetime(2026, 10, 1, 1, 59, 59, tzinfo=datetime.timezone.utc)
        (root / "logs" / "sentinel-writes.log").write_text(
            f"time={armed.isoformat()}\tepoch={int(armed.timestamp())}\top=halt\tpath={halt}\tsha256=-\t"
            "entry=-\tpid=1\tppid=1\tparent=-\tuser=op\ttest=-\n",
            encoding="utf-8",
        )

        records, _ = rebuild(ledger, load_evidence(str(root), "demo"))

        halt_lines = _block_lines(records, "v1.0.0", "halt:project")
        assert halt_lines[0]["cause"] == "operator"
        assert {r[ANNOTATION_KEY]["block_class"] for r in halt_lines} == {"any"}
        task_lines = _block_lines(records, "v1.0.0", "task:CM-demo-release")
        assert task_lines[-1]["kind"] == "block-end"
        assert task_lines[-1]["class"] == "human"
        assert task_lines[-1]["timestamp"] == "2026-10-01T12:00:00+00:00"

    def test_blocked_task_stays_open_and_block_record_opens_a_block(self, tmp_path: pathlib.Path) -> None:
        root = _project(tmp_path, active_rc="v1.1.0", opened_at="2026-10-02T00:00:00+00:00")
        _rollup(root, "v1.0.0", "2026-10-01T00:00:00+00:00", "2026-10-01T12:00:00+00:00")
        ledger = _demo_ledger(root)
        append_block_start(ledger, "v1.0.0", "task:CODER-still-blocked", "2026-10-01T04:00:00+00:00")
        _status(root, "CODER-still-blocked", "BLOCKED")
        _status(
            root, "CODER-new-block", "BLOCKED",
            "reason_code: worktree_create_failed\nowner_role: CODER\nattempts: 0\n"
            "first_blocked_at: 2026-10-02T05:00:00+00:00\nlast_blocked_at: 2026-10-02T05:00:00+00:00\n"
            "cooldown_until: none\nevidence: none\nresume: fresh",
        )

        records, _ = rebuild(ledger, load_evidence(str(root), "demo"))

        still = _block_lines(records, "v1.0.0", "task:CODER-still-blocked")
        assert [r["kind"] for r in still] == ["block-start"]
        new = _block_lines(records, "v1.1.0", "task:CODER-new-block")
        assert [r["kind"] for r in new] == ["block-start"]
        assert new[0]["reason_code"] == "worktree_create_failed"
        assert new[0][ANNOTATION_KEY]["in_ledger"] is False

    def test_stale_end_is_skipped_not_written_onto_another_block(self, tmp_path: pathlib.Path) -> None:
        root = _project(tmp_path)
        _rollup(root, "v1.0.0", "2026-10-01T00:00:00+00:00", "2026-10-01T12:00:00+00:00")
        ledger = _demo_ledger(root)
        source = "task:CM-demo-release"
        append_block_start(ledger, "v1.0.0", source, "2026-10-01T02:00:00+00:00")
        records, _ = rebuild(ledger, load_evidence(str(root), "demo"))
        sidecar = str(root / "projects" / "demo" / "metrics" / SIDECAR_FILENAME)
        write_sidecar(sidecar, records)
        # A live writer closes the block and a new one opens before --apply.
        append_block_end(ledger, "v1.0.0", source, "2026-10-01T05:00:00+00:00")
        append_block_start(ledger, "v1.0.0", source, "2026-10-01T06:00:00+00:00")
        before = pathlib.Path(ledger).read_bytes()

        result = apply_sidecar(ledger, read_sidecar(sidecar, "demo"))

        assert result.appended == []
        assert len(result.skipped) == 1
        assert pathlib.Path(ledger).read_bytes() == before
        assert [b["end"] for b in block_intervals(ledger)] == ["2026-10-01T05:00:00+00:00", None]
        assert len(parse_records(ledger)) == 3
