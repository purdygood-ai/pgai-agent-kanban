"""
test_ops_defer_undefer.py — Unit tests for pgai_agent_kanban.ops.write.defer /
undefer, the one implementation behind team/scripts/defer.sh, undefer.sh,
POST /operations/defer and POST /operations/undefer.

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): each route calls
     its ops.write function with no subprocess, and each CLI script reaches
     the same function through pp_run_ops; both surfaces leave the same
     campaign file on their own board.
  2. CLI behaviour on a sandbox root: stdout, exit code and the
     DEFER-LOW-BUGS bytes and mode for defer with --until, defer when
     already deferred, undefer, and undefer when not deferred, plus the
     refusals (each exits 1 and writes nothing).
  3. The functions directly: validation order (until before project),
     prefix resolution, re-arming over a campaign file that has no until=
     line, and the narration text.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.ops import write as ops_write  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import Ambiguous, NotFound, Refused  # noqa: E402
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_DEFER_SH = _TEAM_DIR / "scripts" / "defer.sh"
_UNDEFER_SH = _TEAM_DIR / "scripts" / "undefer.sh"
_PROJECT = "demo-app"

# The bytes defer.sh has always written for a fresh arm.
_ARMED_BODY = (
    "# DEFER-LOW-BUGS campaign file -- armed by team/scripts/defer.sh,\n"
    "# cleared by team/scripts/undefer.sh, read by\n"
    "# team/scripts/lib/defer_posture.sh.\n"
    "until={until}\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env(tmp_path: pathlib.Path, root: pathlib.Path, **extra: str) -> dict:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
            **extra,
        },
    )


def _sandbox(tmp_path: pathlib.Path, *, registered: bool = True) -> pathlib.Path:
    root = tmp_path / "root"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    (root / "projects" / "other").mkdir(parents=True)
    if registered:
        (root / "projects.cfg").write_text(
            f"[project:{_PROJECT}]\npriority = 1\n\n[project:other]\npriority = 2\n", encoding="utf-8"
        )
    return root


def _run(tmp_path, root, script, *args, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True,
        env=_env(tmp_path, root, **extra), timeout=60,
    )


def _campaign(root: pathlib.Path, project: str = _PROJECT) -> pathlib.Path:
    return root / "projects" / project / "DEFER-LOW-BUGS"


# ---------------------------------------------------------------------------
# 1. Route/CLI parity
# ---------------------------------------------------------------------------


def _parity_boards(tmp_path):
    rest_board = tmp_path / "rest-board"
    rest_project = build_synthetic_board(rest_board)
    cli_board = tmp_path / "cli-board"
    cli_project = build_synthetic_board(cli_board)
    return rest_board, rest_project, cli_board, cli_project


def test_defer_route_and_cli_reach_ops_write_defer(tmp_path):
    rest_board, rest_project, cli_board, cli_project = _parity_boards(tmp_path)

    result = assert_route_cli_parity(
        "defer",
        build_api_client(rest_board),
        json={"project": PROJECT_NAME, "until": "v7.1.0"},
        cli_args=["--project", PROJECT_NAME, "--until", "v7.1.0"],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.write.defer"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.calls
    assert result.cli.completed.returncode == 0, result.cli.completed.stderr
    expected = _ARMED_BODY.format(until="v7.1.0")
    assert (rest_project / "DEFER-LOW-BUGS").read_text(encoding="utf-8") == expected
    assert (cli_project / "DEFER-LOW-BUGS").read_text(encoding="utf-8") == expected
    assert result.response.json()["stdout"] == result.cli.completed.stdout.replace(
        str(cli_board), str(rest_board)
    )


def test_undefer_route_and_cli_reach_ops_write_undefer(tmp_path):
    rest_board, rest_project, cli_board, cli_project = _parity_boards(tmp_path)
    assert (rest_project / "DEFER-LOW-BUGS").is_file() and (cli_project / "DEFER-LOW-BUGS").is_file()

    result = assert_route_cli_parity(
        "undefer",
        build_api_client(rest_board),
        json={"project": PROJECT_NAME},
        cli_args=["--project", PROJECT_NAME],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.write.undefer"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.calls
    assert result.cli.completed.returncode == 0, result.cli.completed.stderr
    assert not (rest_project / "DEFER-LOW-BUGS").exists()
    assert not (cli_project / "DEFER-LOW-BUGS").exists()


def test_defer_route_refusal_matches_the_cli_refusal(tmp_path):
    rest_board, _, cli_board, _ = _parity_boards(tmp_path)
    client = build_api_client(rest_board)

    cli = _run(tmp_path, cli_board, _DEFER_SH, "--project", "ghost", "--until", "1.2")
    response = client.post("/operations/defer", json={"project": "ghost", "until": "1.2"})

    assert cli.returncode == 1
    body = response.json()
    assert response.status_code == 422
    assert body["exit_code"] == 1
    assert body["error"] == "defer.until_malformed"
    assert body["stderr"] == cli.stderr


# ---------------------------------------------------------------------------
# 2. CLI behaviour on a sandbox root
# ---------------------------------------------------------------------------


def test_cli_defer_until_then_already_deferred_then_undefer_then_not_deferred(tmp_path):
    root = _sandbox(tmp_path)
    campaign = _campaign(root)

    armed = _run(tmp_path, root, _DEFER_SH, "--project", _PROJECT, "--until", "v2.40.0")
    assert (armed.returncode, armed.stderr) == (0, "")
    assert armed.stdout == (
        f"defer: DEFER-LOW-BUGS armed for project {_PROJECT} (until: v2.40.0, path: {campaign})\n"
    )
    assert campaign.read_text(encoding="utf-8") == _ARMED_BODY.format(until="v2.40.0")
    assert stat.S_IMODE(campaign.stat().st_mode) == 0o600
    first_mtime = campaign.stat().st_mtime_ns

    again = _run(tmp_path, root, _DEFER_SH, "--project", _PROJECT, "--until", "2.40.0")
    assert (again.returncode, again.stderr) == (0, "")
    assert again.stdout == (
        f"defer: DEFER-LOW-BUGS already armed for project {_PROJECT} with until=v2.40.0 "
        f"({campaign} unchanged; no change made)\n"
    )
    assert campaign.stat().st_mtime_ns == first_mtime

    cleared = _run(tmp_path, root, _UNDEFER_SH, "--project", _PROJECT)
    assert (cleared.returncode, cleared.stderr) == (0, "")
    assert cleared.stdout == f"undefer: DEFER-LOW-BUGS cleared for project {_PROJECT} ({campaign} removed)\n"
    assert not campaign.exists()

    nothing = _run(tmp_path, root, _UNDEFER_SH, "--project", _PROJECT)
    assert (nothing.returncode, nothing.stderr) == (0, "")
    assert nothing.stdout == (
        f"undefer: nothing to clear for project {_PROJECT} (no campaign file at {campaign})\n"
    )
    assert sorted(p.name for p in campaign.parent.iterdir()) == []


def test_cli_rearm_with_a_different_target_replaces_the_file(tmp_path):
    root = _sandbox(tmp_path)
    _run(tmp_path, root, _DEFER_SH, "--project", _PROJECT, "--until", "v1.0.0")
    result = _run(tmp_path, root, _DEFER_SH, "--project", _PROJECT, "--until", "V1.1.0")
    assert result.returncode == 0
    assert _campaign(root).read_text(encoding="utf-8") == _ARMED_BODY.format(until="v1.1.0")
    assert [p.name for p in _campaign(root).parent.iterdir()] == ["DEFER-LOW-BUGS"]


def test_cli_already_armed_keeps_an_operator_written_file_untouched(tmp_path):
    root = _sandbox(tmp_path)
    campaign = _campaign(root)
    campaign.write_text("# operator note\nuntil = v5.0.0  \n", encoding="utf-8")
    before = campaign.read_bytes()
    result = _run(tmp_path, root, _DEFER_SH, "--project", _PROJECT, "--until", "v5.0.0")
    assert result.returncode == 0
    assert "already armed" in result.stdout
    assert campaign.read_bytes() == before


@pytest.mark.parametrize(
    "args, error",
    [
        (["--project", _PROJECT, "--until", "2.14"], "defer.until_malformed"),
        (["--project", _PROJECT], "defer.until_required"),
        (["--until", "v2.40.0"], "defer.project_required"),
        (["--project", "ghost", "--until", "v2.40.0"], "project_resolve.not_found"),
        (["--project", "ghost", "--until", "1.2"], "defer.until_malformed"),
    ],
)
def test_cli_defer_refusals_exit_1_and_write_nothing(tmp_path, args, error):
    root = _sandbox(tmp_path)
    result = _run(tmp_path, root, _DEFER_SH, *args)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"error:   {error}\n"), result.stderr
    assert not any(root.glob("projects/*/DEFER-LOW-BUGS*"))


def test_cli_without_a_registry_refuses_a_project_with_no_directory(tmp_path):
    root = _sandbox(tmp_path, registered=False)
    defer = _run(tmp_path, root, _DEFER_SH, "--project", "demo", "--until", "v1.0.0")
    undefer = _run(tmp_path, root, _UNDEFER_SH, "--project", "demo")
    assert defer.returncode == undefer.returncode == 1
    assert defer.stderr == (
        "error:   defer.project_not_found\n"
        f"message: defer.sh: project 'demo' has no directory at {root}/projects/demo.\n"
        "remedy:  Check the project name against projects.cfg, or register the project first.\n"
    )
    assert undefer.stderr == (
        "error:   undefer.project_not_found\n"
        f"message: undefer.sh: project 'demo' has no directory at {root}/projects/demo.\n"
        "remedy:  Check the project name against projects.cfg.\n"
    )


def test_cli_project_falls_back_to_the_ambient_project_name(tmp_path):
    root = _sandbox(tmp_path)
    result = _run(tmp_path, root, _DEFER_SH, "--until", "v6.0.0", PGAI_PROJECT_NAME="other")
    assert result.returncode == 0, result.stderr
    assert _campaign(root, "other").is_file()


# ---------------------------------------------------------------------------
# 3. The functions directly
# ---------------------------------------------------------------------------


def test_defer_checks_until_before_resolving_the_project(tmp_path):
    ctx = OpsContext(kanban_root=_sandbox(tmp_path))
    with pytest.raises(Refused, match="defer.until_required"):
        ops_write.defer(ctx, "ghost", "")
    with pytest.raises(NotFound, match="project_resolve.not_found"):
        ops_write.defer(ctx, "ghost", "v1.0.0")


def test_defer_and_undefer_resolve_a_project_prefix(tmp_path):
    root = _sandbox(tmp_path)
    ctx = OpsContext(kanban_root=root)
    armed = ops_write.defer(ctx, "demo", "3.0.0")
    assert (armed.project, armed.until, armed.changed) == (_PROJECT, "v3.0.0", True)
    assert armed.campaign_file == _campaign(ctx.kanban_root)
    cleared = ops_write.undefer(ctx, "demo")
    assert (cleared.project, cleared.changed) == (_PROJECT, True)
    assert ops_write.undefer(ctx, "demo").changed is False


def test_defer_refuses_an_ambiguous_prefix(tmp_path):
    root = _sandbox(tmp_path)
    (root / "projects" / "demo-two").mkdir()
    with open(root / "projects.cfg", "a", encoding="utf-8") as cfg:
        cfg.write("\n[project:demo-two]\npriority = 3\n")
    with pytest.raises(Ambiguous):
        ops_write.defer(OpsContext(kanban_root=root), "demo", "v1.0.0")


def test_defer_rearms_over_a_campaign_file_with_no_until_line(tmp_path):
    root = _sandbox(tmp_path)
    campaign = _campaign(root)
    campaign.write_text("no target here\n", encoding="utf-8")
    change = ops_write.defer(OpsContext(kanban_root=root), _PROJECT, "v5.0.0")
    assert change.changed is True
    assert campaign.read_text(encoding="utf-8") == _ARMED_BODY.format(until="v5.0.0")


def test_campaign_change_narration_lines(tmp_path):
    path = tmp_path / "DEFER-LOW-BUGS"
    assert ops_write.CampaignChange("defer", "p", path, "v1.2.3", True).narration == (
        f"defer: DEFER-LOW-BUGS armed for project p (until: v1.2.3, path: {path})"
    )
    assert ops_write.CampaignChange("undefer", "p", path, "", False).narration == (
        f"undefer: nothing to clear for project p (no campaign file at {path})"
    )


def test_ops_cli_verbs_exit_1_on_a_refusal_and_print_the_narration_on_success(tmp_path):
    root = _sandbox(tmp_path)
    env = _env(tmp_path, root, PYTHONPATH=str(_TEAM_DIR))
    argv = [sys.executable, "-m", "pgai_agent_kanban.ops"]
    refused = subprocess.run(argv + ["defer", str(root), _PROJECT, "nope"],
                             capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    armed = subprocess.run(argv + ["defer", str(root), _PROJECT, "v1.0.0"],
                           capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    missing = subprocess.run(argv + ["undefer", str(root)],
                             capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert refused.returncode == 1 and "defer.until_malformed" in refused.stderr
    assert armed.returncode == 0 and armed.stdout.startswith("defer: DEFER-LOW-BUGS armed")
    assert missing.returncode == 1 and "KANBAN_ROOT and PROJECT" in missing.stderr
    assert os.path.isfile(_campaign(root))
