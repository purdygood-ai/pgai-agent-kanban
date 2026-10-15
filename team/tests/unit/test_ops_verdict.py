"""
test_ops_verdict.py — Unit tests for pgai_agent_kanban.ops.verdict: the one
implementation behind team/scripts/why.sh, team/scripts/verdict-report.sh
and GET /verdicts.

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): GET /verdicts
     calls ops.verdict.resolve_verdicts with no subprocess, and why.sh (plain
     and --details) and verdict-report.sh reach the same function through
     pp_run_ops; the route's verdict line equals the CLI's on twin boards.
  2. Behaviour against the committed golden
     (tests/fixtures/golden/verdicts_cli.json, recorded from the scripts and
     the route before the verdict logic moved to Python): for every scenario
     in tests/fixtures/verdict_cases.py -- every verdict word and cause, the
     --details pane deferred and not deferred, an Active RC set and none, a
     posture that refuses, an empty registry, --help and usage errors --
     why.sh and verdict-report.sh stdout, stderr and exit code, and the
     GET /verdicts status and body, are unchanged. The outcomes that changed
     on purpose are recorded beside the old ones with their reason.
  3. The report: details per project only, a posture refusal carried rather
     than raised (and never printed over), the JSON and block shapes, the
     CLI's refusals, and that resolving reads without writing.
  4. The shell-faithful readers against the shell itself: the xargs-style
     state normaliser, the tab-separated read, the project lock matcher and
     the stale-lock threshold and its derivation.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import defer_posture  # noqa: E402
from pgai_agent_kanban.lib import operator_verb_manifest as ovm  # noqa: E402
from pgai_agent_kanban.ops import locks as ops_locks  # noqa: E402
from pgai_agent_kanban.ops import verdict  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from tests.fixtures import verdict_cases as cases  # noqa: E402
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import (  # noqa: E402
    assert_cli_reaches_function,
    assert_route_cli_parity,
    build_api_client,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_LIB_DIR = _TEAM_DIR / "scripts" / "lib"


def _env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict:
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
        },
    )


def _board(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    board = tmp_path / name
    build_synthetic_board(board)
    return board


# ---------------------------------------------------------------------------
# 1. Route/CLI parity
# ---------------------------------------------------------------------------


def test_manifest_row_names_resolve_verdicts():
    verb = ovm.get_verb("why")
    assert verb.impl == "module:pgai_agent_kanban.ops.verdict.resolve_verdicts"
    assert (verb.rest_method, verb.rest_route, verb.script) == ("GET", "/verdicts", "scripts/why.sh")


def test_verdicts_route_and_why_reach_resolve_verdicts(tmp_path, monkeypatch):
    rest_board = _board(tmp_path, "rest-board")
    cli_board = _board(tmp_path, "cli-board")
    (tmp_path / "scratch").mkdir()
    client = build_api_client(rest_board)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(rest_board))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)

    result = assert_route_cli_parity(
        "why",
        client,
        params={"project": PROJECT_NAME},
        cli_args=["--project", PROJECT_NAME],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.verdict.resolve_verdicts"
    assert result.response.status_code == 200, result.response.text
    (entry,) = result.response.json()["verdicts"]
    assert result.cli is not None
    assert result.cli.completed.returncode == entry["exit_code"], result.cli.completed.stderr
    assert result.cli.completed.stdout == entry["line"].replace(str(rest_board), str(cli_board)) + "\n"


def test_details_route_and_why_details_reach_resolve_verdicts(tmp_path, monkeypatch):
    rest_board = _board(tmp_path, "rest-board")
    cli_board = _board(tmp_path, "cli-board")
    (tmp_path / "scratch").mkdir()
    client = build_api_client(rest_board)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(rest_board))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)

    result = assert_route_cli_parity(
        "why",
        client,
        params={"project": PROJECT_NAME, "details": "true"},
        cli_args=["--project", PROJECT_NAME, "--details"],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    body = result.response.json()
    assert set(body) == {"verdicts", "posture", "release_locks", "queue_tails", "warnings"}
    out = result.cli.completed.stdout
    assert out.startswith(body["verdicts"][0]["line"].replace(str(rest_board), str(cli_board)) + "\n")
    assert "\n== posture ==\n" in out and "\n== docker use ==\n" in out


def test_verdict_report_reaches_resolve_verdicts(tmp_path):
    cli_board = _board(tmp_path, "cli-board")
    (tmp_path / "scratch").mkdir()
    row = ovm.get_verb("why")
    report_row = type("Row", (), {
        "name": "why", "impl": row.impl, "rest_method": row.rest_method,
        "rest_route": row.rest_route, "script": "scripts/verdict-report.sh",
    })()
    run = assert_cli_reaches_function(
        report_row, cli_args=["--project", PROJECT_NAME, "--details"],
        env=_env(tmp_path, cli_board), scratch_dir=tmp_path / "scratch",
    )
    assert run.completed.returncode == 0, run.completed.stderr
    assert run.completed.stdout.startswith(f"project={PROJECT_NAME}\nverdict=")


def test_no_shell_copy_of_the_verdict_remains():
    assert not (_LIB_DIR / "verdict.sh").exists()
    for script in ("why.sh", "verdict-report.sh"):
        text = (_TEAM_DIR / "scripts" / script).read_text(encoding="utf-8")
        assert "verdict_resolve_project" not in text and "lib/verdict.sh" not in text, script
    reads = (_TEAM_DIR / "pgai_agent_kanban" / "api" / "reads.py").read_text(encoding="utf-8")
    assert "_parse_verdict_report_blocks" not in reads
    assert "verdict-report.sh\"" not in reads


# ---------------------------------------------------------------------------
# 2. Behaviour equals the recorded behaviour
# ---------------------------------------------------------------------------


def _golden() -> dict:
    return json.loads(cases.GOLDEN_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(cases.SCENARIOS))
def test_scenario_matches_the_recorded_behaviour(tmp_path, name):
    golden = _golden()
    deliberate = golden["deliberate_changes"].get(name, {})
    got = cases.run_scenario(tmp_path, name)
    expected = {
        label: deliberate[label]["outcome"] if label in deliberate else outcome
        for label, outcome in golden["scenarios"][name].items()
    }
    for label in expected:
        assert got[label] == expected[label], f"{name}: {label}"
    assert set(got) == set(expected)


def test_every_scenario_is_recorded():
    golden = _golden()
    assert set(golden["scenarios"]) == set(cases.SCENARIOS)
    for name, (_builder, commands) in cases.SCENARIOS.items():
        assert set(golden["scenarios"][name]) == {cases.command_label(c) for c in commands}, name


def test_each_deliberate_change_names_its_reason_and_differs_from_the_record():
    golden = _golden()
    assert golden["deliberate_changes"], "the recorded deliberate changes are part of the contract"
    for name, labels in golden["deliberate_changes"].items():
        for label, change in labels.items():
            assert change["reason"].strip()
            assert change["outcome"] != golden["scenarios"][name][label]


def test_the_cr_terminated_halt_reason_now_reaches_the_json_whole():
    walk = _golden()["deliberate_changes"]["walk"]["GET /verdicts"]["outcome"]
    zeta = [e for e in json.loads(walk["body"])["verdicts"] if e["project"] == "zeta"][0]
    assert zeta["cause"].endswith("regression in v1.0.0\r)")
    assert zeta["line"].endswith("--project zeta")


# ---------------------------------------------------------------------------
# 3. The report
# ---------------------------------------------------------------------------


def _small_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "root"
    cases.projects_cfg(root, [("alpha", 1, True), ("beta", 2, True)])
    alpha = cases.project(root, "alpha")
    cases.task(alpha, "CODER-1", "WORKING", cases.NOW)
    cases.project(root, "beta")
    return root


def test_details_need_exactly_one_project(tmp_path):
    root = _small_root(tmp_path)
    with pytest.raises(ValueError):
        verdict.resolve_verdicts(root, ["alpha", "beta"], details=True)
    with pytest.raises(ValueError):
        verdict.resolve_verdicts(root, [], details=True)


def test_registry_walk_and_minutes_zero_are_reported(tmp_path):
    root = _small_root(tmp_path)
    report = verdict.resolve_verdicts(root, now=cases.NOW)
    assert [e.project for e in report.entries] == ["alpha", "beta"]
    body = verdict.verdicts_json(report)
    assert body["verdicts"][0]["minutes_in_state"] == 0
    assert "task_id" not in body["verdicts"][1] and "minutes_in_state" not in body["verdicts"][1]
    assert report.worst_exit_code == verdict.EXIT_IDLE


def test_missing_directory_is_unresolved_unless_asked_to_resolve(tmp_path):
    root = _small_root(tmp_path)
    cases.projects_cfg(root, [("alpha", 1, True), ("ghost", 2, True)])
    unresolved = verdict.resolve_verdicts(root, ["ghost"])
    assert isinstance(unresolved.entries[0], verdict.UnresolvedProject)
    assert verdict.render_report_blocks(unresolved) == (
        "project=ghost\nerror=project directory not found\n"
        f"cause=ghost is registered but has no directory at {root}/projects/ghost\n"
        "remedy=check the project name against projects.cfg, or restore the missing project "
        "directory if it was deleted in error\n"
    )
    resolved = verdict.resolve_verdicts(root, ["ghost"], unresolved_missing=False)
    assert resolved.entries[0].verdict == verdict.VERDICT_IDLE


def test_a_refusing_posture_is_carried_and_never_printed_over(tmp_path):
    root = _small_root(tmp_path)
    cases.write(root / "projects" / "alpha" / "DEFER-LOW-BUGS", "no target\n")
    report = verdict.resolve_verdicts(root, ["alpha"], details=True, now=cases.NOW)
    assert report.details.posture is None
    assert isinstance(report.details.posture_error, defer_posture.DeferPostureError)
    assert report.details.posture_error.error == "defer_posture.campaign_unparseable"
    blocks = verdict.render_report_blocks(report)
    assert "kind=posture" not in blocks and blocks.endswith("none=true\n\n")
    assert set(verdict.verdicts_json(report)) == {"verdicts"}


def test_the_verdicts_route_refuses_a_posture_that_does_not_resolve(tmp_path, monkeypatch):
    root = _small_root(tmp_path)
    cases.write(root / "projects" / "alpha" / "project.cfg", "[release]\ndeferred_bugs = on\n")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    response = cases.api_client(root).get("/verdicts", params={"project": "alpha", "details": "true"})
    assert response.status_code == 500
    assert "defer_posture.invalid_deferred_bugs" in response.json()["detail"]


def test_resolving_reads_without_writing(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    cases._build_details_deferred(root, {"LIVE": str(os.getpid()), "AGO": cases.stamp(cases.NOW)})

    def tree() -> dict:
        return {
            str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()
        }

    before = tree()
    for _ in range(2):
        report = verdict.resolve_verdicts(root, ["alpha"], details=True, now=cases.NOW)
        verdict.render_report_blocks(report)
        verdict.verdicts_json(report)
    assert tree() == before


def test_cli_refusals_exit_one_never_a_verdict_code(tmp_path, capsys):
    root = _small_root(tmp_path)
    assert verdict._cli_main(["resolve_verdicts", "--kanban-root", str(root), "--render", "why", "--details"]) == 1
    assert verdict._cli_main(["resolve_verdicts", "--kanban-root", str(root), "--render", "nope"]) == 1
    assert verdict._cli_main(["resolve_verdicts", "--render", "why", "alpha"]) == 1
    assert verdict._cli_main(["other"]) == 1
    capsys.readouterr()
    assert verdict._cli_main(
        ["resolve_verdicts", "--kanban-root", str(root), "--render", "why", "--", "alpha", "beta"]
    ) == verdict.EXIT_IDLE
    assert capsys.readouterr().out.count("\n") == 2


# ---------------------------------------------------------------------------
# 4. The shell-faithful readers against the shell
# ---------------------------------------------------------------------------


def _bash(script: str, *args: str, env: dict | None = None) -> str:
    run = subprocess.run(
        ["bash", "-c", script, "bash", *args], capture_output=True, env=env, timeout=60, check=False,
    )
    return run.stdout.decode("utf-8", "surrogateescape")


_STATE_LINES = [
    "WORKING", "  WORKING  ", "WORK\tING", "DONE's", 'a "b c" d', "a\\ b", "x\\", "it's \"q\"",
    "-n", "-n x", "-nE x", "-x x", "-e a\\\\tb", "WORKING\r", "a\vb\fc", "",
]


@pytest.mark.parametrize("line", _STATE_LINES)
def test_state_normaliser_matches_xargs(line):
    expected = _bash('out="$(printf "%s\\n" "$1" | xargs 2>/dev/null || true)"; printf "%s" "$out"', line)
    assert verdict._xargs_echo(line) == expected


@pytest.mark.parametrize(
    "line", ["a\tb\tc", "\ta\t\tb\tc\td\t", "a\t\tb", "a", "", "a b\tc d\te\tf g"]
)
def test_tab_fields_match_bash_read(line):
    expected = _bash('IFS=$\'\\t\' read -r a b c <<< "$1"; printf "%s|%s|%s" "$a" "$b" "$c"', line)
    assert "|".join(verdict._read_tab_fields(line, 3)) == expected


def test_task_state_reads_the_first_value_under_the_heading(tmp_path):
    def state(text: str) -> str:
        return verdict.task_state(cases.write(tmp_path / "status.md", text))

    assert state("## State\n\nBLOCKED\n") == "BLOCKED"
    assert state("## Summary\nWORKING\n## State\n  WORKING  \n") == "WORKING"
    assert state("## States\nWORKING\n") == "WORKING"
    assert state("## State\n## Summary\nWORKING\n") == ""
    assert state("## State\nDONE's\n") == ""
    assert verdict.task_state(tmp_path / "missing.md") == ""


def _lock_bash(root: pathlib.Path, body: str, *args: str) -> str:
    script = (
        f'source "{_LIB_DIR}/project_paths.sh"; source "{_LIB_DIR}/lock_provenance.sh"; ' + body
    )
    env = build_scrubbed_env(home_dir=root, extra_env={"KANBAN_ROOT": str(root)})
    return _bash(script, *args, env=env)


def test_project_lock_files_match_the_shell_matcher(tmp_path):
    root = tmp_path / "root"
    locks = root / "locks"
    for name in ("repo-wake-alpha.lock", "overwatch-alpha.lock", "x-alpha.lock", "repo-wake-alphabet.lock",
                 ".hidden-alpha.lock", "alpha.lock", "repo-wake-beta.lock", "notes-alpha.txt"):
        cases.write(locks / name, "")
    (locks / "dir-alpha.lock").mkdir()
    (locks / "dangling-alpha.lock").symlink_to(locks / "nowhere")
    expected = _lock_bash(root, 'pgai_lock_files_for_project "$1"', "alpha").splitlines()
    assert ops_locks.project_lock_files(str(locks), "alpha") == expected
    assert ops_locks.project_lock_files(str(root / "absent"), "alpha") == []


@pytest.mark.parametrize(
    "files, env",
    [
        ({}, {}),
        ({"projects/alpha/project.cfg": "[locks]\nstale_lock_reap_after_wake_periods = 400  # long\n"}, {}),
        ({"projects/alpha/PROJECT.cfg": "stale_lock_reap_after_wake_periods='300'\n"}, {}),
        ({"projects/alpha/project.cfg": "[locks]\nstale_lock_reap_after_wake_periods = 0\n",
          "kanban.cfg": "[locks]\nstale_lock_reap_after_wake_periods = 500\n"}, {}),
        ({"kanban.cfg": "[locks]\nstale_lock_reap_after_wake_periods = 500\n[wake]\nmax_task_seconds = 60\n"}, {}),
        ({}, {"MAX_RUNTIME_SECONDS": "10", "MAX_TASK_SECONDS": "20", "KILL_GRACE_SECONDS": "5"}),
    ],
)
def test_stale_threshold_and_derivation_match_the_shell(tmp_path, monkeypatch, files, env):
    root = tmp_path / "root"
    (root / "projects" / "alpha").mkdir(parents=True)
    for rel, text in files.items():
        cases.write(root / rel, text)
    for name in ("MAX_RUNTIME_SECONDS", "MAX_TASK_SECONDS", "KILL_GRACE_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    script = (
        f'source "{_LIB_DIR}/project_paths.sh"; source "{_LIB_DIR}/lock_provenance.sh"; '
        'printf "%s\\n%s" "$(pgai_lock_stale_age_threshold_seconds "$1")" '
        '"$(pgai_lock_stale_age_threshold_derivation "$1")"'
    )
    shell_env = build_scrubbed_env(home_dir=root, extra_env={"KANBAN_ROOT": str(root), **env})
    seconds, derivation = _bash(script, "alpha", env=shell_env).split("\n", 1)
    threshold = ops_locks.project_stale_age_threshold(OpsContext(kanban_root=root), "alpha")
    assert (str(threshold.seconds), threshold.derivation) == (seconds, derivation)


@pytest.mark.parametrize("seconds, text", [(-5, "0s"), (59, "59s"), (60, "1m 0s"), (3599, "59m 59s"), (3600, "1h 0m"), (90061, "25h 1m")])
def test_age_format(seconds, text):
    assert ops_locks.format_age_seconds(seconds) == text
