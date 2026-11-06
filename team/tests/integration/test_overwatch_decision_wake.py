"""
test_overwatch_decision_wake.py
===============================
The decision wake, driven through its real entry points: the scheduled
``scripts/overwatch-sweep.sh`` firing, the on-BLOCK nudge
(``fire_block_triage_nudge`` in ``scripts/lib/wake_project.sh``, which runs
``overwatch-sweep.sh --only block-triage``) and ``check-block-triage.sh``
run directly. Each reaches ``pgai_agent_kanban.overwatch.decision_wake``
through ``python3 -m``.

Covered:
  1. One full sweep firing over two projects -- a BLOCKED task that only a
     human can answer, a BLOCKED task whose row decides a reset, a BACKLOG
     task with Needs Human yes, a chain HALT whose cause is resolved, and an
     RC-level failure (a CM release HALT whose trigger's cause check
     passes) -- decides each stop exactly once and carries the outcome out:
     inbox entries with Needs Human cleared, the reset to BACKLOG, both
     HALTs cleared, one Decision event per carried-out decision. The sweep
     exits 0, and a second firing changes nothing.
  2. The on-BLOCK nudge: the decision is made inside the nudge's own sweep
     firing (only block-triage runs), and no overwatch wake runs.
  3. An operator HALT: decided none, the HALT file is byte-for-byte
     untouched, and the project's other fixes are deferred with a
     "fix deferred" row, the task unchanged.
  4. A runaway counter that trips while nothing is stopped still stops the
     project: one runaway HALT naming the counter and one inbox entry; the
     next firing decides that HALT none.
  5. A block whose row waits a first cool-down records the window once and
     then waits it out.
  6. --dry-run decides and narrates, and changes no task, HALT or inbox.
  7. A project with no release-state.md keeps block triage (escalation).

Every fixture is a throwaway kanban root under pytest's tmp_path with all
root variables set together; subprocess environments are built from an
explicit set. No live install, network or provider is reached.
"""

from __future__ import annotations

import collections
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from pgai_agent_kanban.overwatch.halt_provenance import provenance_lines
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM = pathlib.Path(__file__).resolve().parents[2]
_SWEEP = _TEAM / "scripts" / "overwatch-sweep.sh"
_CHECK = _TEAM / "scripts" / "lib" / "overwatch-checks" / "check-block-triage.sh"
_WAKE_PROJECT = _TEAM / "scripts" / "lib" / "wake_project.sh"

_EPOCH = "2026-01-01T10:00:00+00:00"
_DECISION_LINE = re.compile(r"^check-block-triage: (\S+): decision-wake (\S+): (.*)$", re.M)


def _record(reason_code: str, *, attempts: int = 0) -> str:
    return (
        f"reason_code: {reason_code}\nowner_role: CODER\nattempts: {attempts}\n"
        f"first_blocked_at: {_EPOCH}\nlast_blocked_at: {_EPOCH}\ncooldown_until: none\n"
        "evidence: none\nresume: fresh"
    )


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def _iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class _Project:
    """One registered project of a :class:`_Kanban` root."""

    def __init__(self, kanban: _Kanban, name: str) -> None:
        self.kanban = kanban
        self.name = name
        self.dir = kanban.root / "projects" / name
        self.queue = self.dir / "tasks" / "queues" / "coder_backlog.md"

    def task(self, task_id: str, *, state: str, needs_human: str = "no", record: str | None = None) -> pathlib.Path:
        task_dir = self.dir / "tasks" / task_id
        (task_dir / "artifacts").mkdir(parents=True)
        (task_dir / "README.md").write_text(f"# Task: {task_id}\n\n## Role\nCODER\n", encoding="utf-8")
        status = task_dir / "status.md"
        text = (
            f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nfixture\n\n"
            f"## Blockers\nfixture blocker\n\n## Needs Human\n{needs_human}\n"
        )
        if record is not None:
            text += f"\n## Block Record\n{record}\n"
        status.write_text(text, encoding="utf-8")
        marker = {"BLOCKED": "B", "BACKLOG": " ", "WAITING": "W"}.get(state, "x")
        with self.queue.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{marker}] {task_id} — fixture\n")
        return status

    @property
    def halt(self) -> pathlib.Path:
        return self.dir / "HALT"

    @property
    def release_state(self) -> pathlib.Path:
        return self.dir / "release-state.md"

    def decision_events(self) -> list:
        text = self.release_state.read_text(encoding="utf-8") if self.release_state.is_file() else ""
        return re.findall(r"^\s*- decision: kind=\S+ name=\S+ source=(\S+) result=(\S+)\s*$", text, flags=re.M)

    def open_entries(self) -> list:
        return list(inbox.list_open(OpsContext(kanban_root=self.kanban.root), self.name))

    @property
    def actions(self) -> str:
        path = self.dir / "overwatch" / "actions.log"
        return path.read_text(encoding="utf-8") if path.is_file() else ""


class _Kanban:
    """A throwaway kanban root; projects are registered as they are added."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        for sub in ("tmp", "locks", "logs"):
            (self.root / sub).mkdir(parents=True)
        (self.root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
        (self.root / "projects.cfg").write_text("", encoding="utf-8")
        (tmp_path / "home").mkdir()

    def project(self, name: str, *, release_state: bool = True, dev_tree: bool = False) -> _Project:
        project = _Project(self, name)
        project.queue.parent.mkdir(parents=True)
        project.queue.write_text("# CODER Backlog\n\n## Queue\n\n", encoding="utf-8")
        (project.dir / "overwatch" / "backups").mkdir(parents=True)
        cfg = f"[project]\nproject_name = {name}\n"
        if dev_tree:
            # A full sweep runs every check: the repository-facing ones read
            # this throwaway dev tree, and push_to_remote = false keeps the
            # push-lag check off any remote.
            tree = self.tmp_path / f"dev-tree-{name}"
            git = ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
            subprocess.run([*git, "init", "-q", "-b", "main", str(tree)], check=True, capture_output=True)
            subprocess.run(
                [*git, "-C", str(tree), "commit", "-q", "--allow-empty", "-m", "initial"],
                check=True,
                capture_output=True,
            )
            cfg += f"dev_tree_path = {tree}\npush_to_remote = false\n"
        (project.dir / "project.cfg").write_text(cfg, encoding="utf-8")
        if release_state:
            project.release_state.write_text(
                "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
                "## RC Opened By Task\nnone\n\n## Last Released\nv1.0.0\n",
                encoding="utf-8",
            )
        with (self.root / "projects.cfg").open("a", encoding="utf-8") as handle:
            handle.write(f"[project:{name}]\npriority = 1\n\n")
        return project

    def env(self) -> dict:
        return build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            },
        )

    def run(self, argv: list, timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            cwd=str(self.tmp_path),
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def sweep(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(["bash", str(_SWEEP), *args])


def _decisions(output: str) -> collections.Counter:
    """How many decision lines each subject got."""
    return collections.Counter(subject for subject, _item, _rest in _DECISION_LINE.findall(output))


def _decision_of(output: str, subject: str) -> str:
    found = [(item, rest) for s, item, rest in _DECISION_LINE.findall(output) if s == subject]
    assert len(found) == 1, f"{subject}: {found}\n{output}"
    item, rest = found[0]
    return f"{item}: {rest}"


# ---------------------------------------------------------------------------
# 1. One sweep firing: every stop decided once, every outcome carried out
# ---------------------------------------------------------------------------


def test_one_sweep_firing_decides_each_stop_once_and_carries_it_out(tmp_path):
    kanban = _Kanban(tmp_path)
    alpha = kanban.project("alpha", dev_tree=True)
    human = alpha.task("CODER-20260101-001-plugin-broken", state="BLOCKED", record=_record("plugin_load_failed"))
    reset = alpha.task("CODER-20260101-002-bad-state", state="BLOCKED", record=_record("invalid_state"))
    asks = alpha.task("CODER-20260101-003-asks-a-human", state="BACKLOG", needs_human="yes")
    alpha.task("CODER-20260101-004-was-capped", state="BACKLOG")
    alpha.halt.write_text(
        "\n".join(provenance_lines("zero-progress-cap", "zero-progress cap reached on CODER-20260101-004-was-capped"))
        + "\n",
        encoding="utf-8",
    )

    beta = kanban.project("beta", dev_tree=True)
    beta.halt.write_text(
        "# HALT created by CM at 2026-01-01T10:00:00+00:00\n"
        + "\n".join(provenance_lines("cm-release", "cm-release trigger 4: the pre-squash hook failed", "4"))
        + "\n",
        encoding="utf-8",
    )
    (beta.dir / "release-state").mkdir()
    (beta.dir / "release-state" / "v1.1.0.json").write_text(
        json.dumps(
            {
                "rc": "v1.1.0",
                "outcome": "cancelled",
                "opened_at": "2026-01-01T09:00:00+00:00",
                "closed_at": "2026-01-01T11:00:00+00:00",
                "cargo": [],
            }
        ),
        encoding="utf-8",
    )

    result = kanban.sweep()
    out = result.stderr
    assert result.returncode == 0, out
    assert "running push-lag" in out, "a full sweep runs every check"

    counts = _decisions(out)
    expected = {"halt:alpha", "halt:beta", human.parent.name, reset.parent.name, asks.parent.name}
    assert set(counts) == expected, out
    assert all(n == 1 for n in counts.values()), counts

    # The chain HALT, decided first: its task was reset, so it is cleared.
    assert _decision_of(out, "halt:alpha").startswith("halt: verb clear-chain-halt -> executed"), out
    assert not alpha.halt.exists()
    # The RC-level failure: CM's trigger-4 HALT, cleared on its rc-cancelled check.
    assert _decision_of(out, "halt:beta").startswith("rc-failure: verb clear-chain-halt -> executed"), out
    assert not beta.halt.exists()

    # A block only a human can answer: parked in the inbox, Needs Human cleared.
    assert _decision_of(out, human.parent.name).startswith("block: inbox -> inbox"), out
    text = human.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "no"
    # The row whose decision is a reset: back to BACKLOG with Needs Human no.
    assert _decision_of(out, reset.parent.name).startswith("block: verb reset-to-backlog -> executed"), out
    text = reset.read_text(encoding="utf-8")
    assert _section(text, "State") == "BACKLOG" and _section(text, "Needs Human") == "no"
    assert f"- [ ] {reset.parent.name}" in alpha.queue.read_text(encoding="utf-8")
    # Needs Human on a BACKLOG task: parked in the inbox, Needs Human cleared.
    assert _decision_of(out, asks.parent.name).startswith("needs-human: inbox -> inbox"), out
    assert _section(asks.read_text(encoding="utf-8"), "Needs Human") == "no"
    assert {e.source for e in alpha.open_entries()} == {human.parent.name, asks.parent.name}

    # One Decision event per carried-out decision.
    assert sorted(alpha.decision_events()) == sorted(
        [
            ("halt:alpha", "executed"),
            (human.parent.name, "inbox"),
            (reset.parent.name, "executed"),
            (asks.parent.name, "inbox"),
        ]
    )
    assert beta.decision_events() == [("halt:beta", "executed")]

    # A second firing decides the stop that is left and changes nothing.
    files = [human, reset, asks, alpha.release_state, beta.release_state, alpha.dir / "operator-inbox.md"]
    before = {str(p): p.read_bytes() for p in files}
    again = kanban.sweep("--only", "block-triage")
    assert again.returncode == 0, again.stderr
    assert _decision_of(again.stderr, human.parent.name).startswith("block: inbox -> nothing"), again.stderr
    assert {str(p): p.read_bytes() for p in files} == before


# ---------------------------------------------------------------------------
# 2. The on-BLOCK nudge decides inside its own sweep firing
# ---------------------------------------------------------------------------


def test_nudge_decides_inside_its_own_sweep_firing(tmp_path):
    kanban = _Kanban(tmp_path)
    project = kanban.project("nudged")
    status = project.task("CODER-20260101-001-nudged-block", state="BLOCKED", record=_record("plugin_load_failed"))
    script = (
        "set -euo pipefail\n"
        'log() { echo "LOG: $*"; }\n'
        "AGENT=coder\n"
        f'SCRIPT_DIR="{_TEAM / "scripts" / "wake"}"\n'
        f'source "{_WAKE_PROJECT}"\n'
        "fire_block_triage_nudge nudged\n"
    )
    fired = kanban.run(["bash", "-c", script], timeout=60)
    assert fired.returncode == 0, fired.stderr
    assert "nudging OVERWATCH: overwatch-sweep.sh --only block-triage" in fired.stdout

    trigger_log = kanban.root / "logs" / "overwatch-trigger.log"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if trigger_log.is_file() and "overwatch-sweep: complete" in trigger_log.read_text(encoding="utf-8"):
            break
        time.sleep(0.5)
    log = trigger_log.read_text(encoding="utf-8")
    assert "overwatch-sweep: complete" in log, log
    assert "(only block-triage;" in log, log
    assert _decision_of(log, status.parent.name).startswith("block: inbox -> inbox"), log
    assert _section(status.read_text(encoding="utf-8"), "Needs Human") == "no"
    assert [e.source for e in project.open_entries()] == [status.parent.name]
    assert project.decision_events() == [(status.parent.name, "inbox")]
    # No overwatch wake ran: its block-triage log is never written, and no
    # wake-batch dispatch left a log anywhere under the root.
    assert not (kanban.root / "logs" / "overwatch-block-triage.log").exists()
    assert not list(kanban.root.rglob("*wake-batch*")) and not list(kanban.root.rglob("overwatch-batch-*"))


# ---------------------------------------------------------------------------
# 3. An operator HALT: decided none, untouched, the project's fixes deferred
# ---------------------------------------------------------------------------


def test_operator_halt_is_decided_none_and_defers_the_project(tmp_path):
    kanban = _Kanban(tmp_path)
    project = kanban.project("held")
    status = project.task("CODER-20260101-001-held-block", state="BLOCKED", record=_record("plugin_load_failed"))
    project.halt.write_text("Stopped by the operator for maintenance.\n", encoding="utf-8")
    halt_before = project.halt.read_bytes()
    status_before = status.read_bytes()

    result = kanban.sweep("--only", "block-triage")
    assert result.returncode == 0, result.stderr
    assert _decision_of(result.stderr, "halt:held").startswith("halt: none -> nothing"), result.stderr
    assert project.halt.read_bytes() == halt_before
    assert _decision_of(result.stderr, status.parent.name).startswith("block: inbox -> deferred"), result.stderr
    assert status.read_bytes() == status_before
    assert project.open_entries() == [] and project.decision_events() == []
    assert "fix-deferred" in project.actions and "fix deferred: HALT present" in project.actions


# ---------------------------------------------------------------------------
# 4. A runaway with nothing stopped still stops the project
# ---------------------------------------------------------------------------


def test_runaway_counter_stops_a_project_with_nothing_stopped(tmp_path):
    kanban = _Kanban(tmp_path)
    project = kanban.project("runner")
    shipped = project.dir / "release-state"
    shipped.mkdir()
    now = datetime.datetime.now(datetime.UTC)
    for n in range(6):
        closed = now - datetime.timedelta(minutes=50 - n * 5)
        (shipped / f"v1.0.{n + 1}.json").write_text(
            json.dumps({"rc": f"v1.0.{n + 1}", "outcome": "shipped", "closed_at": _iso(closed)}), encoding="utf-8"
        )

    result = kanban.sweep("--only", "block-triage")
    assert result.returncode == 0, result.stderr
    assert _decision_of(result.stderr, "project:runner").startswith("project: runaway -> runaway"), result.stderr
    body = project.halt.read_text(encoding="utf-8")
    assert "# halt-writer: overwatch-runaway" in body and "releases_per_hour" in body
    assert len(project.open_entries()) == 1

    again = kanban.sweep("--only", "block-triage")
    assert again.returncode == 0, again.stderr
    assert _decision_of(again.stderr, "halt:runner").startswith("halt: none -> nothing"), again.stderr
    assert project.halt.read_text(encoding="utf-8") == body
    assert len(project.open_entries()) == 1


# ---------------------------------------------------------------------------
# 5. A first cool-down is recorded once, then waited out
# ---------------------------------------------------------------------------


def test_first_cool_down_is_recorded_then_waited_out(tmp_path):
    kanban = _Kanban(tmp_path)
    project = kanban.project("cooling")
    status = project.task("CODER-20260101-001-refused", state="BLOCKED", record=_record("provider_refusal"))

    result = kanban.sweep("--only", "block-triage")
    assert result.returncode == 0, result.stderr
    line = _decision_of(result.stderr, status.parent.name)
    assert line.startswith("block: none -> nothing") and "recorded the first cool-down" in line, line
    record = _section(status.read_text(encoding="utf-8"), "Block Record")
    cooldown = re.search(r"^cooldown_until:\s*(\S+)", record, flags=re.M).group(1)
    assert cooldown != "none"
    assert "block-triage:provider_refusal:record" in project.actions

    before = status.read_bytes()
    again = kanban.sweep("--only", "block-triage")
    assert again.returncode == 0, again.stderr
    assert "inside the cool-down window" in _decision_of(again.stderr, status.parent.name), again.stderr
    assert status.read_bytes() == before


# ---------------------------------------------------------------------------
# 6. --dry-run writes nothing but its narration
# ---------------------------------------------------------------------------


def test_dry_run_decides_and_writes_nothing(tmp_path):
    kanban = _Kanban(tmp_path)
    project = kanban.project("dry")
    status = project.task("CODER-20260101-001-dry-block", state="BLOCKED", record=_record("plugin_load_failed"))
    before = (status.read_bytes(), project.release_state.read_bytes())
    script = (
        f'source "{_CHECK}"\nOVERWATCH_PROJECT=dry OVERWATCH_DECISION_WAKE=1 overwatch_check_block_triage --dry-run\n'
    )
    result = kanban.run(["bash", "-c", script], timeout=120)
    assert result.returncode == 0, result.stderr
    assert _decision_of(result.stderr, status.parent.name).startswith("block: inbox -> dry-run"), result.stderr
    assert (status.read_bytes(), project.release_state.read_bytes()) == before
    assert not (project.dir / "operator-inbox.md").exists()
    assert "decision-wake-dry-run" in project.actions


# ---------------------------------------------------------------------------
# 7. Without release-state.md the sweep keeps block triage
# ---------------------------------------------------------------------------


def test_project_without_release_state_keeps_block_triage(tmp_path):
    kanban = _Kanban(tmp_path)
    project = kanban.project("unreleased", release_state=False)
    status = project.task("CODER-20260101-001-old-path", state="BLOCKED", record=_record("plugin_load_failed"))
    result = kanban.sweep("--only", "block-triage")
    assert result.returncode == 0, result.stderr
    assert "no release-state.md to record and count decisions in" in result.stderr
    assert not _DECISION_LINE.search(result.stderr), result.stderr
    assert "block-triage:plugin_load_failed:escalate" in project.actions
    assert _section(status.read_text(encoding="utf-8"), "Needs Human") == "yes"
