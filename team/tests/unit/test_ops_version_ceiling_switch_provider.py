"""
test_ops_version_ceiling_switch_provider.py — Unit tests for
pgai_agent_kanban.ops.write.set_version_ceiling and switch_provider, the one
implementation behind team/scripts/set-version-ceiling.sh,
team/scripts/switch-provider.sh, POST /operations/set-version-ceiling and
POST /operations/switch-provider.

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): each route calls
     its ops.write function with no subprocess, and each CLI script reaches
     the same function through pp_run_ops; both surfaces leave the same cfg
     bytes on their own board.
  2. set-version-ceiling on a sandbox root: stdout, exit code and resulting
     project.cfg bytes for --show, --dry-run, --minor N, --major N,
     --no-minor (writes the explicit ``none``), an invalid value (refused
     dual-channel, file unchanged) and a project with no project.cfg
     (exit 2).
  3. File mode: a cfg at 0600 and one at 0640 keep their mode after each
     verb writes it, with comments and unrelated keys byte-preserved.
  4. switch-provider: the confirmation and the rewritten line, an unknown
     provider refused with error/message/remedy and kanban.cfg unchanged, a
     provider whose CLI is not on PATH refused with kanban.cfg unchanged.
  5. The route's refusals carry the CLI's exit code and stderr.
  6. The functions directly: a lifted (``none``) ceiling set again is
     rewritten in place; lifting an absent ceiling writes ``none`` (absent
     is the default, not "no ceiling") and lifting one already ``none``
     writes nothing; --show reports the default, ``none`` and a malformed
     value; and the no-action refusal.

Every board is a throwaway directory under tmp_path. A stub ``claude``
executable on PATH stands in for the provider CLI.
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
from pgai_agent_kanban.ops.errors import NotFound, Refused  # noqa: E402
from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_CEILING_SH = _TEAM_DIR / "scripts" / "set-version-ceiling.sh"
_SWITCH_SH = _TEAM_DIR / "scripts" / "switch-provider.sh"
_PROJECT = "demo-app"

_PROJECT_CFG = """\
# project.cfg fixture -- every comment and unrelated key must survive
[project]
project_name = demo-app
workflow_type = release

[versioning]
# max_minor: cap on the minor component (this comment names the key)
max_patch = 21
max_minor = 13
max_major = 0

# release settings follow
[release]
max_rc_retries = 3
"""

_KANBAN_CFG = """\
# kanban.cfg fixture
[dashboard]
active = not-the-provider-key

[providers]

# Active provider -- to switch, edit this value or run switch-provider.sh.
# active = gemini
active = codex

default = claude
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    """A directory holding a stub ``claude`` executable (and no ``gemini``)."""
    directory = tmp_path / "stub-bin"
    directory.mkdir()
    stub = directory / "claude"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return directory


def _env(tmp_path: pathlib.Path, root: pathlib.Path, stub: pathlib.Path) -> dict:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[stub],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        },
    )


def _sandbox(tmp_path: pathlib.Path, mode: int = 0o640) -> pathlib.Path:
    """A root registering demo-app (with project.cfg) and nocfg (without), plus kanban.cfg."""
    root = tmp_path / "root"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    (root / "projects" / "nocfg").mkdir(parents=True)
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority = 1\n\n[project:nocfg]\npriority = 2\n", encoding="utf-8"
    )
    for path, text in ((_project_cfg(root), _PROJECT_CFG), (root / "kanban.cfg", _KANBAN_CFG)):
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
    return root


def _project_cfg(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT / "project.cfg"


def _run(tmp_path, root, stub, script, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True,
        env=_env(tmp_path, root, stub), cwd=str(tmp_path), timeout=60, stdin=subprocess.DEVNULL,
    )


def _mode(path: pathlib.Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# 1. Route/CLI parity
# ---------------------------------------------------------------------------


def _parity_boards(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    boards = []
    for name in ("rest-board", "cli-board"):
        board = tmp_path / name
        project_dir = build_synthetic_board(board)
        (project_dir / "project.cfg").write_text(_PROJECT_CFG, encoding="utf-8")
        (board / "kanban.cfg").write_text(_KANBAN_CFG, encoding="utf-8")
        boards.append(board)
    return boards[0], boards[1]


def test_set_version_ceiling_route_and_cli_reach_ops_write_set_version_ceiling(tmp_path, stub_bin):
    rest_board, cli_board = _parity_boards(tmp_path)

    result = assert_route_cli_parity(
        "set-version-ceiling",
        build_api_client(rest_board),
        json={"project": PROJECT_NAME, "minor": 7, "show": True},
        cli_args=["--project", PROJECT_NAME, "--minor", "7", "--show"],
        env=_env(tmp_path, cli_board, stub_bin),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.write.set_version_ceiling"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.calls
    assert result.cli.completed.returncode == 0, result.cli.completed.stderr
    assert result.cli.completed.stdout == "max_minor=7\nmax_major=0\n"
    assert result.response.json()["stdout"] == result.cli.completed.stdout
    expected = _PROJECT_CFG.replace("max_minor = 13\n", "max_minor=7\n")
    for board in (rest_board, cli_board):
        assert (board / "projects" / PROJECT_NAME / "project.cfg").read_text(encoding="utf-8") == expected


def test_switch_provider_route_and_cli_reach_ops_write_switch_provider(tmp_path, stub_bin, monkeypatch):
    rest_board, cli_board = _parity_boards(tmp_path)
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    result = assert_route_cli_parity(
        "switch-provider",
        build_api_client(rest_board),
        json={"provider": "claude"},
        cli_args=["--provider", "claude"],
        env=_env(tmp_path, cli_board, stub_bin),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.write.switch_provider"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.calls
    assert result.cli.completed.returncode == 0, result.cli.completed.stderr
    assert result.response.json()["stdout"] == result.cli.completed.stdout.replace(
        str(cli_board), str(rest_board)
    )
    expected = _KANBAN_CFG.replace("\nactive = codex\n", "\nactive = claude\n")
    assert (rest_board / "kanban.cfg").read_text(encoding="utf-8") == expected
    assert (cli_board / "kanban.cfg").read_text(encoding="utf-8") == expected


# ---------------------------------------------------------------------------
# 2. set-version-ceiling on a sandbox root
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, stdout, expected_cfg",
    [
        pytest.param(["--show"], "max_minor=13\nmax_major=0\n", _PROJECT_CFG, id="show"),
        pytest.param(
            ["--dry-run", "--minor", "7"],
            "[DRY-RUN] Would apply to: {cfg}\n  set max_minor=7\n",
            _PROJECT_CFG,
            id="dry-run",
        ),
        pytest.param(
            ["--dry-run", "--show", "--minor", "7", "--no-major"],
            "[DRY-RUN] Would apply to: {cfg}\n  set max_minor=7\n  set max_major=none\n"
            "  [--show would print after changes are applied]\n",
            _PROJECT_CFG,
            id="dry-run-show",
        ),
        pytest.param(["--minor", "7"], "", _PROJECT_CFG.replace("max_minor = 13\n", "max_minor=7\n"), id="minor"),
        pytest.param(["--major", "3"], "", _PROJECT_CFG.replace("max_major = 0\n", "max_major=3\n"), id="major"),
        pytest.param(
            ["--no-minor"], "", _PROJECT_CFG.replace("max_minor = 13\n", "max_minor = none\n"), id="no-minor",
        ),
        pytest.param(
            ["--minor", "07", "--major", "3", "--show"],
            "max_minor=07\nmax_major=3\n",
            _PROJECT_CFG.replace("max_minor = 13\n", "max_minor=07\n").replace("max_major = 0\n", "max_major=3\n"),
            id="both-show",
        ),
        pytest.param(
            ["--no-minor", "--show"],
            "max_minor=none\nmax_major=0\n",
            _PROJECT_CFG.replace("max_minor = 13\n", "max_minor = none\n"),
            id="no-minor-show",
        ),
    ],
)
def test_cli_stdout_exit_code_and_cfg_bytes(tmp_path, stub_bin, args, stdout, expected_cfg):
    root = _sandbox(tmp_path)
    cfg = _project_cfg(root)

    result = _run(tmp_path, root, stub_bin, _CEILING_SH, "--project", _PROJECT, *args)

    assert (result.returncode, result.stderr) == (0, ""), result.stderr
    assert result.stdout == stdout.format(cfg=_project_cfg(root.resolve()))
    assert cfg.read_text(encoding="utf-8") == expected_cfg


@pytest.mark.parametrize("flag, value", [("--minor", "abc"), ("--major", "1.5"), ("--minor", "-1")])
def test_cli_invalid_value_is_refused_dual_channel_and_the_file_is_unchanged(tmp_path, stub_bin, flag, value):
    root = _sandbox(tmp_path)
    cfg = _project_cfg(root)
    before = (cfg.read_bytes(), cfg.stat().st_mtime_ns)

    result = _run(tmp_path, root, stub_bin, _CEILING_SH, "--project", _PROJECT, flag, value)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "error:   set_version_ceiling.invalid_ceiling\n"
        f"message: set-version-ceiling.sh: {flag} requires a non-negative integer; got: '{value}'.\n"
        "remedy:  Re-run with a non-negative integer, e.g. team/scripts/set-version-ceiling.sh "
        f"--project {_PROJECT} {flag} 5\n"
    )
    assert (cfg.read_bytes(), cfg.stat().st_mtime_ns) == before
    assert sorted(p.name for p in cfg.parent.iterdir()) == ["project.cfg"]


def test_cli_project_without_project_cfg_exits_2(tmp_path, stub_bin):
    root = _sandbox(tmp_path)
    result = _run(tmp_path, root, stub_bin, _CEILING_SH, "--project", "nocfg", "--minor", "5")
    assert result.returncode == 2
    assert result.stderr == (
        f"ERROR: project 'nocfg' not found — no project.cfg at: {root.resolve()}/projects/nocfg/project.cfg\n"
    )


def test_cli_no_action_prints_the_refusal_and_usage(tmp_path, stub_bin):
    root = _sandbox(tmp_path)
    result = _run(tmp_path, root, stub_bin, _CEILING_SH, "--project", _PROJECT)
    assert result.returncode == 1
    assert result.stderr.startswith(
        "ERROR: at least one action is required (--show, --minor, --major, --no-minor, --no-major)\n"
        "Usage: set-version-ceiling.sh"
    )


# ---------------------------------------------------------------------------
# 3. File mode and byte preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [0o600, 0o640], ids=["0600", "0640"])
@pytest.mark.parametrize(
    "script, args, target, expected",
    [
        pytest.param(
            _CEILING_SH, ["--project", _PROJECT, "--minor", "9"], "project",
            _PROJECT_CFG.replace("max_minor = 13\n", "max_minor=9\n"), id="set-version-ceiling-set",
        ),
        pytest.param(
            _CEILING_SH, ["--project", _PROJECT, "--no-major"], "project",
            _PROJECT_CFG.replace("max_major = 0\n", "max_major = none\n"), id="set-version-ceiling-lift",
        ),
        pytest.param(
            _SWITCH_SH, ["--provider", "claude"], "kanban",
            _KANBAN_CFG.replace("\nactive = codex\n", "\nactive = claude\n"), id="switch-provider",
        ),
    ],
)
def test_each_verb_keeps_the_cfg_mode_and_every_other_byte(tmp_path, stub_bin, mode, script, args, target, expected):
    root = _sandbox(tmp_path, mode)
    cfg = _project_cfg(root) if target == "project" else root / "kanban.cfg"

    result = _run(tmp_path, root, stub_bin, script, *args)

    assert result.returncode == 0, result.stderr
    assert cfg.read_text(encoding="utf-8") == expected
    assert _mode(cfg) == mode
    assert not any(p.name.startswith(f".{cfg.name}.") for p in cfg.parent.iterdir())


# ---------------------------------------------------------------------------
# 4. switch-provider
# ---------------------------------------------------------------------------


def test_cli_switch_provider_confirms_and_rewrites_only_the_providers_active_line(tmp_path, stub_bin):
    root = _sandbox(tmp_path)
    result = _run(tmp_path, root, stub_bin, _SWITCH_SH, "--provider", "CLAUDE")
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout == (
        "Active provider switched: codex → claude\n"
        f"  Updated: {root.resolve()}/kanban.cfg [providers] active\n"
        "  CLI confirmed in PATH: claude\n"
        "\n"
        "The change takes effect on the next cron tick (typically 1-2 minutes).\n"
    )
    text = (root / "kanban.cfg").read_text(encoding="utf-8")
    assert "\n# active = gemini\nactive = claude\n" in text
    assert "\nactive = not-the-provider-key\n" in text


def test_cli_switch_provider_unknown_provider_is_refused_and_kanban_cfg_unchanged(tmp_path, stub_bin):
    root = _sandbox(tmp_path)
    cfg = root / "kanban.cfg"
    before = (cfg.read_bytes(), cfg.stat().st_mtime_ns, _mode(cfg))

    result = _run(tmp_path, root, stub_bin, _SWITCH_SH, "--provider", "nobody")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "error:   switch_provider.unknown_provider\n"
        "message: switch-provider.sh: unrecognized provider 'nobody'; valid providers: claude, codex, gemini.\n"
        "remedy:  Re-run with a valid provider, e.g. team/scripts/switch-provider.sh --provider claude\n"
    )
    assert (cfg.read_bytes(), cfg.stat().st_mtime_ns, _mode(cfg)) == before


def test_cli_switch_provider_without_the_providers_cli_on_path_is_refused(tmp_path, stub_bin):
    root = _sandbox(tmp_path)
    before = (root / "kanban.cfg").read_bytes()
    result = _run(tmp_path, root, stub_bin, _SWITCH_SH, "--provider", "gemini")
    assert result.returncode == 1
    assert result.stderr == (
        "switch-provider.sh: error: cannot switch to provider 'gemini'\n"
        "  CLI binary 'gemini' not found in PATH\n"
        "  Install gemini before switching to this provider.\n"
    )
    assert (root / "kanban.cfg").read_bytes() == before


def test_cli_switch_provider_refuses_a_kanban_cfg_with_no_active_key(tmp_path, stub_bin):
    root = _sandbox(tmp_path)
    cfg = root / "kanban.cfg"
    cfg.write_text("[providers]\n# active = codex\n\n[dashboard]\nactive = x\n", encoding="utf-8")
    before = cfg.read_bytes()
    result = _run(tmp_path, root, stub_bin, _SWITCH_SH, "--provider", "claude")
    assert result.returncode == 1
    assert result.stderr == (
        "switch-provider.sh: error: [providers] section in kanban.cfg has no 'active' key\n"
        f"  Edit {root.resolve()}/kanban.cfg manually and add: active = claude\n"
    )
    assert cfg.read_bytes() == before


# ---------------------------------------------------------------------------
# 5. Route refusals carry the CLI's exit code and stderr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route, body, script, cli_args, error, http_status",
    [
        pytest.param(
            "/operations/set-version-ceiling", {"project": _PROJECT, "minor": -1},
            _CEILING_SH, ["--project", _PROJECT, "--minor", "-1"],
            "set_version_ceiling.invalid_ceiling", 422, id="invalid-ceiling",
        ),
        pytest.param(
            "/operations/set-version-ceiling", {"project": "nocfg", "minor": 5},
            _CEILING_SH, ["--project", "nocfg", "--minor", "5"],
            "set_version_ceiling.refused", 404, id="no-project-cfg",
        ),
        pytest.param(
            "/operations/set-version-ceiling", {"project": "ghost", "minor": 5},
            _CEILING_SH, ["--project", "ghost", "--minor", "5"],
            "project_resolve.not_found", 404, id="unknown-project",
        ),
        pytest.param(
            "/operations/switch-provider", {"provider": "nobody"},
            _SWITCH_SH, ["--provider", "nobody"],
            "switch_provider.unknown_provider", 409, id="unknown-provider",
        ),
    ],
)
def test_route_refusal_matches_the_cli_refusal(tmp_path, stub_bin, monkeypatch, route, body, script, cli_args, error, http_status):
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    rest_root = _sandbox(tmp_path / "rest")
    cli_root = _sandbox(tmp_path / "cli")
    before = {p: p.read_bytes() for p in (_project_cfg(rest_root), rest_root / "kanban.cfg")}

    cli = _run(tmp_path, cli_root, stub_bin, script, *cli_args)
    response = build_api_client(rest_root).post(route, json=body)

    payload = response.json()
    assert response.status_code == http_status, payload
    assert cli.returncode != 0
    assert payload["exit_code"] == cli.returncode
    assert payload["error"] == error
    assert payload["stderr"] == cli.stderr.replace(str(cli_root.resolve()), str(rest_root.resolve()))
    assert {p: p.read_bytes() for p in before} == before


# ---------------------------------------------------------------------------
# 6. The functions directly
# ---------------------------------------------------------------------------


def test_a_lifted_ceiling_set_again_is_rewritten_in_place(tmp_path):
    root = _sandbox(tmp_path)
    ctx = OpsContext(kanban_root=root)
    ops_write.set_version_ceiling(ctx, _PROJECT, no_minor=True)
    report = ops_write.set_version_ceiling(ctx, _PROJECT, minor=4, show=True)
    assert report.changed is True
    assert report.lines == ("max_minor=4", "max_major=0")
    assert _project_cfg(root).read_text(encoding="utf-8") == _PROJECT_CFG.replace(
        "max_minor = 13\n", "max_minor=4\n"
    )


def test_no_major_writes_none_and_keeps_every_other_line_byte_identical(tmp_path):
    root = _sandbox(tmp_path)
    cfg = _project_cfg(root)
    before = cfg.read_bytes().split(b"\n")

    report = ops_write.set_version_ceiling(OpsContext(kanban_root=root), _PROJECT, no_major=True)

    after = cfg.read_bytes().split(b"\n")
    assert report.changed is True
    assert len(after) == len(before)
    changed = [(old, new) for old, new in zip(before, after) if old != new]
    assert changed == [(b"max_major = 0", b"max_major = none")]


def test_set_version_ceiling_refusals_and_their_exit_codes(tmp_path):
    ctx = OpsContext(kanban_root=_sandbox(tmp_path))
    with pytest.raises(Refused, match="at least one action is required") as no_action:
        ops_write.set_version_ceiling(ctx, _PROJECT)
    with pytest.raises(Refused, match="set_version_ceiling.invalid_ceiling") as invalid:
        ops_write.set_version_ceiling(ctx, _PROJECT, major="x")
    with pytest.raises(NotFound) as no_cfg:
        ops_write.set_version_ceiling(ctx, "nocfg", show=True)
    assert [ops_write.set_version_ceiling_exit_code(e.value) for e in (no_action, invalid, no_cfg)] == [1, 1, 2]


def test_lifting_an_absent_ceiling_writes_none(tmp_path):
    """An absent key is the default (13), not "no ceiling", so --no-minor must write none."""
    root = _sandbox(tmp_path)
    cfg = _project_cfg(root)
    cfg.write_text("[versioning]\nmax_patch = 1", encoding="utf-8")
    report = ops_write.set_version_ceiling(OpsContext(kanban_root=root), _PROJECT, no_minor=True, show=True)
    assert report.changed is True
    assert report.lines == ("max_minor=none", "max_major=0 (default)")
    assert cfg.read_text(encoding="utf-8") == "[versioning]\nmax_patch = 1\nmax_minor = none\n"


def test_lifting_a_ceiling_already_none_writes_nothing(tmp_path):
    root = _sandbox(tmp_path)
    cfg = _project_cfg(root)
    cfg.write_text("[versioning]\nmax_minor = none\n", encoding="utf-8")
    before = cfg.stat().st_mtime_ns
    report = ops_write.set_version_ceiling(OpsContext(kanban_root=root), _PROJECT, no_minor=True)
    assert report.changed is False
    assert cfg.read_text(encoding="utf-8") == "[versioning]\nmax_minor = none\n"
    assert cfg.stat().st_mtime_ns == before


@pytest.mark.parametrize(
    "versioning, lines",
    [
        pytest.param("[project]\nproject_name = demo-app\n", ("max_minor=13 (default)", "max_major=0 (default)"),
                     id="absent-keys-show-the-defaults"),
        pytest.param("[versioning]\nmax_minor = NONE\nmax_major = \"2\"\n", ("max_minor=none", "max_major=2"),
                     id="none-any-case-and-quoted-number"),
        pytest.param("[versioning]\nmax_major = O\n",
                     ("max_minor=13 (default)",
                      "max_major=O (invalid: not a non-negative whole number or none; "
                      "the project is ceiling-blocked)"),
                     id="malformed-is-marked-invalid"),
    ],
)
def test_cli_show_reports_the_ceiling_in_effect(tmp_path, stub_bin, versioning, lines):
    root = _sandbox(tmp_path)
    _project_cfg(root).write_text(versioning, encoding="utf-8")

    result = _run(tmp_path, root, stub_bin, _CEILING_SH, "--project", _PROJECT, "--show")

    assert (result.returncode, result.stderr) == (0, ""), result.stderr
    assert result.stdout == "".join(f"{line}\n" for line in lines)


def test_switch_provider_reports_an_empty_previous_value_as_none(tmp_path, stub_bin, monkeypatch):
    monkeypatch.setenv("PATH", str(stub_bin))
    root = _sandbox(tmp_path)
    (root / "kanban.cfg").write_text("[providers]\nactive =\n", encoding="utf-8")
    change = ops_write.switch_provider(OpsContext(kanban_root=root), "claude")
    assert (change.previous, change.provider, change.changed) == ("", "claude", True)
    assert change.narration.startswith("Active provider switched: (none) → claude\n")
    assert (root / "kanban.cfg").read_text(encoding="utf-8") == "[providers]\nactive = claude\n"


def test_ops_cli_verbs_check_their_argument_count(tmp_path):
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    argv = [sys.executable, "-m", "pgai_agent_kanban.ops"]
    ceiling = subprocess.run(argv + ["set_version_ceiling", str(tmp_path)],
                             capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    provider = subprocess.run(argv + ["switch_provider", str(tmp_path)],
                              capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert ceiling.returncode == 1 and "DRY_RUN arguments are required" in ceiling.stderr
    assert provider.returncode == 1 and "KANBAN_ROOT and PROVIDER" in provider.stderr
