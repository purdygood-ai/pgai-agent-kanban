"""
test_show_queues_rest_parity.py — golden-pair proof that show-queues'
per-agent, per-state ``--details`` listing resolves to the SAME data
through the CLI (``dashboard/show-queues.sh --details``) and its REST twin
(``GET /show-queues?details=true``).

Neither surface re-derives the grouping/reconciliation logic: both call
``pgai_agent_kanban.ops.read.get_queues``, whose ``details`` is
``pgai_agent_kanban.dashboard.queue_details.resolve_queue_details``'s result
-- show-queues.sh reaches it through ``pp_run_ops`` and renders it as text
(never a second, independent scan of tasks/queues), and
``GET /show-queues?details=true`` (team/pgai_agent_kanban/api/reads.py)
returns it as JSON. This test proves the two callers agree
end to end: a real ``bash show-queues.sh --details`` subprocess (exercising
argument parsing, project resolution, and PYTHONPATH wiring, not just the
resolver in isolation) versus a real HTTP request against the production
REST entrypoint, over the SAME seeded board.

The board is seeded on top of build_synthetic_board's own six-state shape
with an extra queue/README layer that exercises every warning the resolver
can produce -- a clean match, a marker/status mismatch, a missing queue
entry, and a queue entry with no task folder -- so a parity test collapsed
to the trivial "no warnings anywhere" case would not catch a resolver
regression that only shows up on a real discrepancy.

Integration test contract (team/tests/integration/conftest.py): no
integration point is mocked. The REST server is a real uvicorn subprocess;
the CLI call is a real `bash show-queues.sh` subprocess; only the synthetic
board's DATA (README Role fields, queue backlog lines) is fake.

Test-name policy (roles/TESTER.md Anti-pattern 6): names describe behavior,
never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import urllib.request

from tests.fixtures.api_server import api_server_fixture
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board, task_ids_by_state
from tests.fixtures.golden_comparator import assert_json_parity

AGENT_ORDER = ["pm", "coder", "writer", "tester", "cm", "po", "human", "unknown"]
STATE_ORDER = ["WORKING", "BLOCKED", "WAITING", "BACKLOG"]

_GHOST_TASK_ID = "PM-20260101-999-ghost-task"


# ---------------------------------------------------------------------------
# Fixture layering: on top of build_synthetic_board's own six-state shape,
# assign roles to a few tasks and seed queue-backlog lines that exercise
# every warning the resolver can produce.
# ---------------------------------------------------------------------------


def _seed_queue_reconciliation(project_dir: pathlib.Path) -> None:
    ids = task_ids_by_state()

    # WORKING task: role coder, queue marker "B" (BLOCKED) disagrees with
    # status.md's WORKING -- a real marker_mismatch.
    (project_dir / "tasks" / ids["WORKING"] / "README.md").write_text(
        "## Role\nCODER\n", encoding="utf-8"
    )
    # BACKLOG task: role coder, queue marker " " agrees with BACKLOG -- a
    # clean match (no warning).
    (project_dir / "tasks" / ids["BACKLOG"] / "README.md").write_text(
        "## Role\nCODER\n", encoding="utf-8"
    )
    # WAITING task: role writer, no writer_backlog.md at all -- no_queue_entry.
    (project_dir / "tasks" / ids["WAITING"] / "README.md").write_text(
        "## Role\nWRITER\n", encoding="utf-8"
    )
    # BLOCKED task: no README.md at all -- falls back to "unknown", and
    # (no queue entry for it either) -- also no_queue_entry, under a
    # different agent bucket than the WAITING task above.

    queues_dir = project_dir / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "coder_backlog.md").write_text(
        f"- [B] {ids['WORKING']}\n"
        f"- [ ] {ids['BACKLOG']}\n",
        encoding="utf-8",
    )
    # A queue entry with no matching task folder anywhere -- folder_missing,
    # under pm (queue_file_to_agent maps "pm_backlog" -> "pm").
    (queues_dir / "pm_backlog.md").write_text(
        f"- [ ] {_GHOST_TASK_ID}\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# CLI side: real `bash show-queues.sh --details` subprocess, text parsed
# into the same {agent: {state: [{"task_id", "warning"}]}} shape the
# resolver returns.
# ---------------------------------------------------------------------------

_WARNING_TEXT_TO_TOKEN = {
    "task folder missing": "folder_missing",
    "no queue entry": "no_queue_entry",
    "marker/status mismatch": "marker_mismatch",
}
_UNKNOWN_STATE_RE = re.compile(r"^\[UNKNOWN STATE: (?P<state>.+)\]$")
_AGENT_HEADER_RE = re.compile(r"^=== (?P<agent>\S+) ===$")
_STATE_HEADER_RE = re.compile(r"^\[(?P<state>\S+)\]$")
_TASK_LINE_RE = re.compile(r"^  (?P<task_id>\S+)(?:  ⚠ (?P<warning>.+))?$")


def _cli_env(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def _warning_text_to_token(text: "str | None") -> "str | None":
    if text is None:
        return None
    if text in _WARNING_TEXT_TO_TOKEN:
        return _WARNING_TEXT_TO_TOKEN[text]
    m = _UNKNOWN_STATE_RE.match(text)
    if m:
        return f"unknown_state:{m.group('state')}"
    raise AssertionError(f"unrecognized warning text: {text!r}")


def _cli_show_queues_details(board_root: pathlib.Path) -> dict:
    """Run the real show-queues.sh --details script and parse its stdout
    into the same {agent: {state: [{"task_id", "warning"}]}} shape
    resolve_queue_details returns (every AGENT_ORDER x STATE_ORDER key
    present, possibly with an empty list)."""
    show_queues_sh = board_root / "scripts" / "dashboard" / "show-queues.sh"
    result = subprocess.run(
        [
            "bash", str(show_queues_sh),
            "--project", PROJECT_NAME,
            "--details",
            "--kanban-root", str(board_root),
        ],
        capture_output=True,
        text=True,
        env=_cli_env(board_root),
        timeout=15,
    )
    assert result.returncode == 0, f"show-queues.sh --details failed: {result.stderr!r}"

    grouped: dict = {agent: {state: [] for state in STATE_ORDER} for agent in AGENT_ORDER}
    current_agent: "str | None" = None
    current_state: "str | None" = None
    for line in result.stdout.splitlines():
        m = _AGENT_HEADER_RE.match(line)
        if m:
            current_agent = m.group("agent")
            current_state = None
            continue
        m = _STATE_HEADER_RE.match(line)
        if m and current_agent is not None:
            current_state = m.group("state")
            continue
        m = _TASK_LINE_RE.match(line)
        if m and current_agent is not None and current_state is not None:
            grouped[current_agent][current_state].append(
                {
                    "task_id": m.group("task_id"),
                    "warning": _warning_text_to_token(m.group("warning")),
                }
            )
    return grouped


# ---------------------------------------------------------------------------
# REST side: real HTTP request against the production entrypoint.
# ---------------------------------------------------------------------------


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200, f"GET {url} returned HTTP {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def _rest_show_queues_details(port: int) -> dict:
    body = _get_json(
        f"http://127.0.0.1:{port}/show-queues?project={PROJECT_NAME}&details=true"
    )
    return body["details"]


# ---------------------------------------------------------------------------
# The golden pair
# ---------------------------------------------------------------------------


def test_per_agent_details_listing_matches_between_cli_and_rest(
    tmp_path: pathlib.Path,
) -> None:
    """``show-queues.sh --details`` (CLI) and ``GET /show-queues?details=true``
    (REST) resolve to the identical per-agent, per-state task listing --
    including a clean match, a marker/status mismatch, a missing queue
    entry, and a folder-missing queue entry -- over the SAME seeded board,
    through the REAL production entrypoint on a loopback ephemeral port
    and a REAL script subprocess."""
    board_root = tmp_path / "board"
    project_dir = build_synthetic_board(board_root)
    _seed_queue_reconciliation(project_dir)

    cli_details = _cli_show_queues_details(board_root)

    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board_root) as handle:
        rest_details = _rest_show_queues_details(handle.port)

    assert_json_parity(cli_details, rest_details)

    # Sanity: the fixture actually exercised every warning class, not a
    # collapsed trivial "nothing to reconcile" board that would pass even
    # if the resolver ignored queue reconciliation entirely.
    ids = task_ids_by_state()
    assert cli_details["coder"]["WORKING"] == [
        {"task_id": ids["WORKING"], "warning": "marker_mismatch"}
    ]
    assert cli_details["coder"]["BACKLOG"] == [
        {"task_id": ids["BACKLOG"], "warning": None}
    ]
    assert cli_details["writer"]["WAITING"] == [
        {"task_id": ids["WAITING"], "warning": "no_queue_entry"}
    ]
    assert cli_details["unknown"]["BLOCKED"] == [
        {"task_id": ids["BLOCKED"], "warning": "no_queue_entry"}
    ]
    assert cli_details["pm"]["BACKLOG"] == [
        {"task_id": _GHOST_TASK_ID, "warning": "folder_missing"}
    ]
    # DONE/WONT-DO tasks never appear in the details view on either surface.
    all_ids = {
        entry["task_id"]
        for agent in AGENT_ORDER
        for state in STATE_ORDER
        for entry in cli_details[agent][state]
    }
    assert ids["DONE"] not in all_ids
    assert ids["WONT-DO"] not in all_ids
