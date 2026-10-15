"""
test_container_tier_remedies.py
===============================
Every printed remedy or suggested command that names a container-tier
harness carries the operator authorization (PGAI_CONTAINER_TIER=1) and an
image selection, so it runs as printed: pasted into an operator's shell
(not an agent wake) it gets past the operator-only check AND past the
image-selection listing step, and actually runs the lane.

The deep-verify freshness gate (team/scripts/deep-verify-freshness-check.sh)
is driven into each of its lane-related refusals; the remedy it prints is
then run verbatim with ``bash -c``, from an unrelated working directory, in
an environment built from an explicit minimal set of names. The lane is the
documented PGAI_DEEP_VERIFY_COLD_LANE_CMD stub and ``docker``/``podman``
resolve to stubs that log every call, so the run makes zero real engine
calls.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPTS = _TEAM_DIR / "scripts"
_FRESHNESS = _SCRIPTS / "deep-verify-freshness-check.sh"
_SUGGESTED = _SCRIPTS / "dashboard" / "suggested-commands.sh"

_PROJECT = "demo"
_NOW = "2026-09-03T12:00:00+00:00"
_TARGET_VERSION = "v2.9.12"

_ENGINE_STUB = """#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "{log}"
exit 0
"""

_GREEN_LANE_STUB = """#!/usr/bin/env bash
echo "lane-ran $*" >> "{record}"
echo "[cold-build] [docker] [debian] Image built successfully: tag:test"
echo "Cold-build lane PASSED"
exit 0
"""


def _record(timestamp: str, version: str, verdict: str, detail: str) -> str:
    return (
        f"# Deep-Verify Ledger — {_PROJECT}\n\n"
        "Append-only. One record per deep-verify run.\n\n"
        f"## Deep-Verify Run: {timestamp} — {_PROJECT} — {version}\n"
        "- Commit: e240ee1\n"
        f"- Verdict: {verdict}\n"
        f"- Detail: {detail}\n"
    )


# (case id, expected error key, ledger text or None for no ledger)
_CASES = [
    ("no-record", "freshness_gate.no_record", None),
    (
        "failed-to-build",
        "freshness_gate.failed_to_build",
        _record("2026-09-02T03:15:00+00:00", _TARGET_VERSION, "failed-to-build", "docker/debian: failed"),
    ),
    (
        "could-not-attempt",
        "freshness_gate.could_not_attempt",
        _record("2026-09-02T03:15:00+00:00", _TARGET_VERSION, "could-not-attempt", "no engine"),
    ),
    (
        "stale",
        "freshness_gate.stale_record",
        _record("2026-08-01T03:15:00+00:00", "v2.9.11", "built", "docker/debian: built"),
    ),
    ("ledger-unreadable", "freshness_gate.ledger_unreadable", "not a deep-verify ledger\n"),
]


class _Sandbox:
    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban_root"
        (self.root / "projects" / _PROJECT).mkdir(parents=True)
        (self.root / "projects.cfg").write_text(
            f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8"
        )
        self.temp = tmp_path / "pgai_tmp"
        self.temp.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.engine_log = tmp_path / "engine.log"
        self.record = tmp_path / "lane.log"
        for engine in ("docker", "podman"):
            path = self.bin / engine
            path.write_text(_ENGINE_STUB.format(log=self.engine_log), encoding="utf-8")
            path.chmod(0o755)
        self.lane = tmp_path / "green_lane.sh"
        self.lane.write_text(_GREEN_LANE_STUB.format(record=self.record), encoding="utf-8")
        self.lane.chmod(0o755)

    @property
    def ledger(self) -> pathlib.Path:
        return self.root / "projects" / _PROJECT / "deep-verify-ledger.md"

    def env(self) -> dict:
        # An operator's own shell: no agent wake, and no authorization
        # exported -- the remedy itself must carry it.
        return build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=(self.bin,),
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
                "PGAI_DEV_TREE_PATH": str(_REPO_ROOT),
                "PGAI_DEEP_VERIFY_COLD_LANE_CMD": str(self.lane),
                "PGAI_DEEP_VERIFY_FRESHNESS_NOW": _NOW,
                "PGAI_AGENT_WAKE_ACTIVE": "0",
            },
        )


def _remedy(stderr: str) -> str:
    match = re.search(r"^remedy:\s+(.*)$", stderr, re.MULTILINE)
    assert match, f"no remedy line in:\n{stderr}"
    return match.group(1)


@pytest.mark.parametrize("case,error_key,ledger_text", _CASES, ids=[c[0] for c in _CASES])
def test_freshness_remedy_runs_as_printed_and_reaches_the_lane(
    tmp_path: pathlib.Path, case: str, error_key: str, ledger_text: str | None
) -> None:
    sandbox = _Sandbox(tmp_path)
    if ledger_text is not None:
        sandbox.ledger.write_text(ledger_text, encoding="utf-8")

    gate = subprocess.run(
        ["bash", str(_FRESHNESS), "--project", _PROJECT, "--version", _TARGET_VERSION],
        capture_output=True,
        text=True,
        env=sandbox.env(),
        cwd=str(sandbox.home),
        timeout=60,
    )
    assert gate.returncode == 1, gate.stderr
    assert f"error:   {error_key}" in gate.stderr, gate.stderr
    remedy = _remedy(gate.stderr)
    assert remedy.startswith(f"PGAI_CONTAINER_TIER=1 bash {_SCRIPTS / 'deep-verify.sh'} "), remedy
    assert f"--project {_PROJECT} --image all" in remedy, remedy

    if case == "ledger-unreadable":
        # The remedy's own trailing comment: run it after removing or
        # repairing the malformed record.
        sandbox.ledger.unlink()

    run = subprocess.run(
        ["bash", "-c", remedy],
        capture_output=True,
        text=True,
        env=sandbox.env(),
        cwd=str(sandbox.home),
        timeout=120,
    )
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "SKIPPED" not in output and "container_tier.operator_only" not in output, output
    assert "no container image selected" not in output, output
    assert sandbox.record.read_text(encoding="utf-8").strip() == "lane-ran --image debian --image rocky9"
    assert "Verdict: built" in sandbox.ledger.read_text(encoding="utf-8")
    assert not sandbox.engine_log.exists(), "a remedy run must make zero real engine calls"


def test_suggested_commands_deep_verify_row_carries_the_authorization_and_an_image() -> None:
    result = subprocess.run(
        ["bash", str(_SUGGESTED), "--no-color"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if "deep-verify.sh" in line]
    assert len(rows) == 1, result.stdout
    assert rows[0].startswith("PGAI_CONTAINER_TIER=1 scripts/deep-verify.sh --project "), rows
    assert "--image all" in rows[0], rows


def test_suggested_commands_tells_the_operator_to_run_a_line_in_the_shell() -> None:
    result = subprocess.run(
        ["bash", str(_SUGGESTED), "--no-color"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    instruction = result.stdout.splitlines()[1]
    assert "Copy a line" in instruction and "run it in the shell" in instruction, instruction
    assert "never runs anything for you" in instruction, instruction
    assert "pane" not in _SUGGESTED.read_text(encoding="utf-8").lower()


_CONTAINER_TIER_COMMAND_RE = re.compile(r"deep-verify\.sh --project|cold-build-now\.sh --project")


def test_no_printed_container_tier_command_lacks_the_authorization_prefix() -> None:
    offenders = []
    for path in sorted(_SCRIPTS.rglob("*")):
        if path.suffix not in (".sh", ".py") or "tests" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if _CONTAINER_TIER_COMMAND_RE.search(line) and "PGAI_CONTAINER_TIER=1" not in line:
                offenders.append(f"{path.relative_to(_TEAM_DIR)}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)
