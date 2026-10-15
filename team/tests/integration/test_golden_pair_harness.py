"""
test_golden_pair_harness.py — end-to-end proof of the golden-pair fixture
infrastructure: synthetic board + real loopback REST server + comparator.

This is the harness ticket, not a per-verb parity ticket (see
team/tests/fixtures/golden_board.py's and golden_comparator.py's own
docstrings) -- subsequent tickets add one golden pair per read verb on top
of what this file proves works. What this file demonstrates, in order:

  1. The api_server_fixture starts the REAL production REST server
     (pgai_agent_kanban.api.main's entrypoint, via ``uvicorn ...:create_app
     --factory``) on an ephemeral loopback port, serves a real HTTP request,
     and shuts down cleanly -- and two concurrent instances never collide on
     a port.
  2. build_synthetic_board seeds a board with a task in every state, one
     deferred bug, and one active campaign file.
  3. assert_json_parity reports equality for a matching CLI/REST pair and
     names the differing key path for a deliberately mismatched pair.
  4. One golden pair -- the "why" verb / GET /verdicts -- passes end to end:
     the REAL why.sh CLI script and the REAL REST route, over the SAME
     synthetic board, resolve to the same verdict.

Integration test contract (team/tests/integration/conftest.py): no
integration point is mocked here. The REST server is a real uvicorn
subprocess: real socket, real HTTP, real production entrypoint. The CLI
call is a real `bash why.sh` subprocess. Only the synthetic board's DATA is
fake; the code paths exercising it are the production ones (entry-point
law).

Test-name policy (roles/TESTER.md Anti-pattern 6): names describe behavior,
never bug IDs, version numbers, or scaffolding labels.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import subprocess
import urllib.request

import pytest

from tests.fixtures.api_server import api_server_fixture
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board, task_ids_by_state
from tests.fixtures.golden_comparator import assert_json_parity, diff_json, strip_envelope

_PRODUCTION_DEFAULT_PORT = 8300


def _cli_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """Build the subprocess environment for a real CLI script invocation.

    Clears PGAI_PROJECT_NAME (would make the script assume a default
    project) and points PGAI_AGENT_KANBAN_ROOT_PATH at the synthetic board
    -- env_bootstrap.sh's idempotency guard honors an already-exported value
    (the operator env wins) without deriving anything from shell-env, so no
    other kanban env var is required for a script this narrow.
    """
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    return env


def _get_json(url: str) -> dict:
    """GET *url* and return the decoded JSON body. Raises on a non-2xx status."""
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200, f"GET {url} returned HTTP {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 1. Loopback ephemeral-port server lifecycle
# ---------------------------------------------------------------------------


def test_fixture_serves_a_request_on_an_ephemeral_port_and_shuts_down_cleanly(
    tmp_path: pathlib.Path,
) -> None:
    """The real production entrypoint starts on an ephemeral loopback port,
    answers a real HTTP request, and releases the port on teardown."""
    board_root = tmp_path / "board"
    build_synthetic_board(board_root)

    recorded_port: int | None = None
    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board_root) as handle:
        assert handle.port != _PRODUCTION_DEFAULT_PORT, (
            "Fixture must never bind the production default port 8300."
        )
        recorded_port = handle.port

        body = _get_json(f"http://127.0.0.1:{handle.port}/health")
        assert body["service"] == "pgai-agent-kanban-api"

    # Context manager has exited; the server must have released the port.
    assert recorded_port is not None
    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", recorded_port), timeout=2):
            pass


def test_two_concurrent_fixture_instances_do_not_collide_on_a_port(
    tmp_path: pathlib.Path,
) -> None:
    """Two api_server_fixture instances, live at the same time over two
    different synthetic boards, are assigned different ports and each
    answers its own request correctly."""
    board_a = tmp_path / "board_a"
    board_b = tmp_path / "board_b"
    build_synthetic_board(board_a, project="golden-fixture-a")
    build_synthetic_board(board_b, project="golden-fixture-b")

    server_a_logs = tmp_path / "server_a_logs"
    server_b_logs = tmp_path / "server_b_logs"
    server_a_logs.mkdir(parents=True, exist_ok=True)
    server_b_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_a_logs, kanban_root=board_a) as handle_a:
        with api_server_fixture(server_b_logs, kanban_root=board_b) as handle_b:
            assert handle_a.port != handle_b.port, (
                f"Two concurrent fixture instances collided on port {handle_a.port}."
            )

            body_a = _get_json(f"http://127.0.0.1:{handle_a.port}/health")
            body_b = _get_json(f"http://127.0.0.1:{handle_b.port}/health")
            assert body_a["kanban_root"] != body_b["kanban_root"], (
                "Both server instances reported the same kanban_root -- they "
                "are not actually isolated from one another."
            )


# ---------------------------------------------------------------------------
# 2. Synthetic board shape
# ---------------------------------------------------------------------------


def test_synthetic_board_seeds_every_task_state_a_deferred_bug_and_a_campaign_file(
    tmp_path: pathlib.Path,
) -> None:
    """build_synthetic_board's board contains a task in every state, one
    open low-severity (deferred) bug, and one active DEFER-LOW-BUGS
    campaign file -- and writes nothing outside the board directory it was
    given (besides the outward-pointing scripts/ symlink)."""
    board_root = tmp_path / "board"
    project_dir = build_synthetic_board(board_root)

    for state, task_id in task_ids_by_state().items():
        status_file = project_dir / "tasks" / task_id / "status.md"
        assert status_file.is_file(), f"missing status.md for state {state} ({task_id})"
        assert f"## State\n{state}" in status_file.read_text(encoding="utf-8")

    bug_files = sorted((project_dir / "bugs").glob("*.md"))
    assert len(bug_files) == 1, f"expected exactly one bug file, found {bug_files}"
    bug_text = bug_files[0].read_text(encoding="utf-8")
    assert "**Severity:** low" in bug_text
    assert "## Status\nopen" in bug_text

    campaign_file = project_dir / "DEFER-LOW-BUGS"
    assert campaign_file.is_file()
    assert re.search(r"^until=v\d+\.\d+\.\d+$", campaign_file.read_text(encoding="utf-8"), re.MULTILINE)

    # The scripts/ symlink points at the real tree (entry-point law) -- prove
    # a real script is reachable through it, not a stub.
    why_sh = board_root / "scripts" / "why.sh"
    assert why_sh.is_file()

    # Nothing was written outside board_root itself.
    for path in board_root.rglob("*"):
        resolved = path if path.is_symlink() else path.resolve()
        assert str(resolved).startswith(str(board_root.resolve())) or path.is_symlink(), (
            f"fixture wrote outside its own board directory: {path}"
        )


# ---------------------------------------------------------------------------
# 3. Comparator behavior
# ---------------------------------------------------------------------------


def test_comparator_reports_equality_for_a_matching_cli_rest_pair() -> None:
    """Two structurally identical values (modulo an ignored envelope key)
    produce zero diffs and assert_json_parity does not raise."""
    cli_value = {"project": "demo", "verdict": "RUNNING", "task_id": "CODER-1"}
    rest_value = {
        "project": "demo",
        "verdict": "RUNNING",
        "task_id": "CODER-1",
        "warnings": [],  # envelope-only field the CLI never carries
    }

    assert diff_json(cli_value, strip_envelope(rest_value, envelope_keys=("warnings",))) == []
    assert_json_parity(cli_value, rest_value, ignore_keys=("warnings",))  # must not raise


def test_comparator_reports_a_diff_naming_the_differing_key_path_for_a_mismatched_pair() -> None:
    """A deliberately mismatched pair raises AssertionError naming the exact
    JSON key path where CLI and REST disagree."""
    cli_value = {"verdicts": [{"project": "demo", "verdict": "RUNNING"}]}
    rest_value = {"verdicts": [{"project": "demo", "verdict": "BLOCKED"}]}

    diffs = diff_json(cli_value, rest_value)
    assert diffs == ["$.verdicts[0].verdict: CLI='RUNNING' REST='BLOCKED'"]

    with pytest.raises(AssertionError, match=re.escape("$.verdicts[0].verdict")):
        assert_json_parity(cli_value, rest_value)


# ---------------------------------------------------------------------------
# 4. The golden pair: why.sh <-> GET /verdicts
# ---------------------------------------------------------------------------


def test_golden_pair_why_verdict_matches_between_real_cli_and_real_rest_route(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end proof of the harness for one read verb: the REAL why.sh
    CLI script and the REAL GET /verdicts route, run against the SAME
    synthetic board (through the REAL production entrypoint on a loopback
    ephemeral port), resolve to the same project/verdict/task_id/task_state.

    "why" is both an existing working twin (why.sh <-> GET /verdicts,
    both calling the single resolve_verdicts resolver) and
    the verb the v2.25.0 requirements document names first among the golden
    pairs later tickets must cover -- an appropriate choice to prove the
    harness against.
    """
    board_root = tmp_path / "board"
    build_synthetic_board(board_root)
    working_task_id = task_ids_by_state()["WORKING"]

    # --- CLI side: the real why.sh script, as a real subprocess ---
    why_sh = board_root / "scripts" / "why.sh"
    result = subprocess.run(
        ["bash", str(why_sh), "--project", PROJECT_NAME],
        capture_output=True,
        text=True,
        env=_cli_env(board_root),
        timeout=15,
    )
    assert result.returncode == 0, f"why.sh failed: {result.stderr!r}"
    line = result.stdout.strip()
    match = re.search(r"^(\S+): (RUNNING) — task (\S+) is (WORKING), \d+m in that state", line)
    assert match, f"why.sh line did not match the expected RUNNING shape: {line!r}"
    cli_value = {
        "project": match.group(1),
        "verdict": match.group(2),
        "task_id": match.group(3),
        "task_state": match.group(4),
    }

    # --- REST side: the real production entrypoint, on a loopback ephemeral port ---
    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board_root) as handle:
        body = _get_json(
            f"http://127.0.0.1:{handle.port}/verdicts?project={PROJECT_NAME}"
        )

    entries = [entry for entry in body["verdicts"] if entry.get("project") == PROJECT_NAME]
    assert len(entries) == 1, f"expected exactly one verdict entry for {PROJECT_NAME}: {body}"
    rest_value = {
        "project": entries[0]["project"],
        "verdict": entries[0]["verdict"],
        "task_id": entries[0]["task_id"],
        "task_state": entries[0]["task_state"],
    }

    # minutes_in_state is deliberately excluded from both sides above: it is
    # wall-clock-derived (now - status.md mtime) and can legitimately tick
    # over a minute boundary between the CLI call and the REST call, which
    # would be a timing flake, not a parity regression.
    assert_json_parity(cli_value, rest_value)

    # Sanity: the fixture actually exercised the WORKING task, not a
    # collapsed trivial case that would pass even if the resolver ignored
    # task state entirely.
    assert cli_value["task_id"] == working_task_id
