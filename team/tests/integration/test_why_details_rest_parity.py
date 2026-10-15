"""
test_why_details_rest_parity.py — golden-pair proof that why.sh --details'
full diagnostic pane (posture, release locks, and queue tails) resolves to
the SAME data through GET /verdicts?details=true.

Neither surface parses the other's prose. Both resolve through
pgai_agent_kanban.ops.verdict.resolve_verdicts: team/scripts/verdict-report.sh
--details (the machine-readable sibling of why.sh's text pane) reaches it
through pp_run_ops and is the "CLI side" of the golden pair; the real
production REST entrypoint (over a real loopback HTTP request), which calls
it in process, is the "REST side". The CLI side's key=value blocks are read
here by the block contract verdict-report.sh documents ("Output shape" in
its header) and compared field by field with the REST JSON -- the route no
longer parses script output at all, so this test's reader is an independent
oracle of that contract rather than a copy of production code.

Integration test contract (team/tests/integration/conftest.py): no
integration point is mocked. The REST server is a real uvicorn subprocess;
the CLI call is a real `bash verdict-report.sh` subprocess; only the
synthetic board's DATA (one lock, one queue entry, on top of
build_synthetic_board's own shape) is fake.

Test-name policy (roles/TESTER.md Anti-pattern 6): names describe behavior,
never bug IDs, task IDs, or scaffolding labels.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request

from tests.fixtures.api_server import api_server_fixture
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board, task_ids_by_state
from tests.fixtures.golden_comparator import assert_json_parity, strip_envelope



def _parse_blocks(stdout: str) -> list[dict[str, str]]:
    """verdict-report.sh's blank-line-separated blocks; each line split on its first "="."""
    blocks = []
    for raw in stdout.strip("\n").split("\n\n"):
        fields = dict(line.split("=", 1) for line in raw.split("\n") if "=" in line)
        if fields:
            blocks.append(fields)
    return blocks


def _int_or_none(value: str):
    return None if value == "none" else int(value)


def _cli_json(blocks: list[dict[str, str]]) -> dict:
    """The JSON GET /verdicts?details=true documents, read off the CLI's blocks."""
    (verdict,) = [b for b in blocks if "kind" not in b]
    entry = {k: verdict[k] for k in ("project", "verdict", "cause", "remedy", "line")}
    entry["exit_code"] = int(verdict["exit_code"])
    for key in ("task_id", "task_state"):
        if key in verdict:
            entry[key] = verdict[key]
    if "minutes_in_state" in verdict:
        entry["minutes_in_state"] = int(verdict["minutes_in_state"])
    (posture_block,) = [b for b in blocks if b.get("kind") == "posture"]
    posture = {"halt_state": posture_block["halt_state"], "active_rc": posture_block["active_rc"]}
    for field in ("deferred_bugs", "deferred_bug_level", "defer_piggyback", "until"):
        posture[field] = {"value": posture_block[field], "tier": posture_block[f"{field}_tier"]}
    if "campaign_file" in posture_block:
        posture["until"]["campaign_file"] = posture_block["campaign_file"]
    if "halt_event" in posture_block:
        posture["halt_event"] = posture_block["halt_event"]
    posture["pending_bundles"] = [
        {"name": b["name"], "status": b["status"]}
        for b in blocks if b.get("kind") == "pending_bundle" and b.get("none") != "true"
    ]
    locks = [
        {
            "path": b["path"], "provenance": b["provenance"],
            "stale_threshold_derivation": b["stale_threshold_derivation"],
            "violation": b["violation"] == "true", "age_seconds": int(b["age_seconds"]),
            "held_seconds": _int_or_none(b["held_seconds"]),
            "stale_threshold_seconds": int(b["stale_threshold_seconds"]),
        }
        for b in blocks if b.get("kind") == "release_lock" and b.get("none") != "true"
    ]
    tails: dict[str, list] = {}
    for b in blocks:
        if b.get("kind") == "queue_tail" and "agent" in b:
            tails.setdefault(b["agent"], [])
        elif b.get("kind") == "queue_entry":
            tails.setdefault(b["agent"], []).append(
                {"marker": b["marker"], "task_id": b["task_id"], "status_state": b["status_state"]}
            )
    return {
        "verdicts": [entry],
        "posture": posture,
        "release_locks": locks,
        "queue_tails": [{"agent": a, "entries": e} for a, e in tails.items()],
    }


def _cli_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """Build the subprocess environment for a real CLI script invocation.

    Same contract as test_golden_pair_harness.py's own ``_cli_env``: clears
    ``PGAI_PROJECT_NAME`` (would make the script assume a default project)
    and points ``PGAI_AGENT_KANBAN_ROOT_PATH`` at the synthetic board.
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


def _seed_locks_and_queue_tail(board_root: pathlib.Path, project_dir: pathlib.Path) -> str:
    """Add one release lock and one queue-tail entry on top of
    ``build_synthetic_board``'s own shape, so the golden pair exercises a
    populated ``release_locks``/``queue_tails`` shape rather than only their
    "none held" / "no queues" empty states -- a comparison over an entirely
    empty shape would pass even if the grouping logic silently dropped every
    real record (golden_board.py's own module docstring: "or the comparison
    is trivial and would not catch a verb that only handles the happy-path
    subset").

    Returns the WORKING task's id, so the caller can assert the queue-tail
    entry actually carries it.
    """
    locks_dir = board_root / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    # Naming convention: "*-<project>.lock" (team/scripts/lib/lock_provenance.sh's
    # pgai_lock_files_for_project). Content shape: "holder=...\npid=...\nop=...\ntask=...\n"
    # (that same file's pgai_lock_acquire).
    (locks_dir / f"wake-{PROJECT_NAME}.lock").write_text(
        "holder=golden-fixture-holder\npid=999999\nop=wake\ntask=none\n",
        encoding="utf-8",
    )

    working_task_id = task_ids_by_state()["WORKING"]
    queues_dir = project_dir / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    (queues_dir / "coder_backlog.md").write_text(
        f"- [A] {working_task_id}\n", encoding="utf-8"
    )
    return working_task_id


def test_why_details_posture_locks_and_queue_tails_match_between_cli_and_rest(
    tmp_path: pathlib.Path,
) -> None:
    """``verdict-report.sh --project NAME --details`` (the CLI side) and
    ``GET /verdicts?project=NAME&details=true`` (the REST side) resolve to
    the identical ``posture``, ``release_locks``, and ``queue_tails`` shape
    over the SAME synthetic board, through the REAL production entrypoint
    and a REAL script subprocess."""
    board_root = tmp_path / "board"
    project_dir = build_synthetic_board(board_root)
    working_task_id = _seed_locks_and_queue_tail(board_root, project_dir)

    # --- CLI side: the real verdict-report.sh script, as a real subprocess ---
    verdict_report_sh = board_root / "scripts" / "verdict-report.sh"
    result = subprocess.run(
        ["bash", str(verdict_report_sh), "--project", PROJECT_NAME, "--details"],
        capture_output=True,
        text=True,
        env=_cli_env(board_root),
        timeout=15,
    )
    assert result.returncode == 0, f"verdict-report.sh --details failed: {result.stderr!r}"

    blocks = _parse_blocks(result.stdout)
    verdict_blocks = [b for b in blocks if "kind" not in b]
    assert len(verdict_blocks) == 1, f"expected exactly one verdict block: {blocks}"
    assert "error" not in verdict_blocks[0], f"project failed to resolve: {verdict_blocks[0]}"
    cli_value = _cli_json(blocks)

    # Sanity: the fixture actually exercised a real lock and a real queue
    # entry, not a collapsed trivial case that would pass even if the
    # details dropped every real record.
    assert len(cli_value["release_locks"]) == 1, cli_value["release_locks"]
    assert cli_value["release_locks"][0]["violation"] is False
    # The fixture record carries no acquired_at, so held time is "not
    # recorded": null, never the mtime-derived age_seconds standing in.
    assert cli_value["release_locks"][0]["held_seconds"] is None, cli_value["release_locks"]
    assert len(cli_value["queue_tails"]) == 1, cli_value["queue_tails"]
    assert cli_value["queue_tails"][0]["agent"] == "coder"
    assert cli_value["queue_tails"][0]["entries"] == [
        {"marker": "A", "task_id": working_task_id, "status_state": "WORKING"}
    ]
    # The deferred-bug campaign build_synthetic_board seeds is a posture fact,
    # not a pending requirements bundle -- pending_bundles stays empty.
    assert cli_value["posture"]["deferred_bugs"]["value"] == "true"
    assert cli_value["posture"]["pending_bundles"] == []

    # --- REST side: the real production entrypoint, on a loopback ephemeral port ---
    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board_root) as handle:
        body = _get_json(
            f"http://127.0.0.1:{handle.port}/verdicts?project={PROJECT_NAME}&details=true"
        )

    rest_value = strip_envelope(body, envelope_keys=("warnings",))

    # release_locks[].age_seconds is the refresh age, wall-clock-derived
    # (now - lock file mtime), and can legitimately differ by a second or
    # two between the CLI subprocess call and the later REST call -- a
    # timing flake, not a parity regression (same rationale
    # test_golden_pair_harness.py's own verdict pair excludes
    # minutes_in_state for). held_seconds stays compared: it is null on
    # both sides for this fixture's record, which has no acquired_at.
    assert_json_parity(
        cli_value, rest_value, ignore_keys=("age_seconds",)
    )


def test_why_details_requires_project_over_rest_and_cli_alike(
    tmp_path: pathlib.Path,
) -> None:
    """``?details=true`` with no ``?project=`` refuses over REST (422), the
    same refusal ``verdict-report.sh --details`` gives over the CLI (exit 1,
    dual-channel) when it is given no ``--project`` -- neither surface
    silently defaults to walking every project under ``--details``."""
    board_root = tmp_path / "board"
    build_synthetic_board(board_root)

    verdict_report_sh = board_root / "scripts" / "verdict-report.sh"
    result = subprocess.run(
        ["bash", str(verdict_report_sh), "--details"],
        capture_output=True,
        text=True,
        env=_cli_env(board_root),
        timeout=15,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "details" in result.stderr.lower()

    server_logs = tmp_path / "server_logs"
    server_logs.mkdir(parents=True, exist_ok=True)
    with api_server_fixture(server_logs, kanban_root=board_root) as handle:
        req = urllib.request.Request(f"http://127.0.0.1:{handle.port}/verdicts?details=true")
        try:
            urllib.request.urlopen(req, timeout=10)
            assert False, "expected HTTP 422 for ?details=true with no ?project="
        except urllib.error.HTTPError as exc:
            assert exc.code == 422
