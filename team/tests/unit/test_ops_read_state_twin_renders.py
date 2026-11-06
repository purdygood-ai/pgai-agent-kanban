"""
test_ops_read_state_twin_renders.py — what ``release-state.sh`` and
``deep-verify-status.sh`` print, rendered by ``pgai_agent_kanban.ops.read``.

``render_release_state`` and ``render_deep_verify_status`` turn the
``get_release_state`` / ``get_deep_verify_status`` reads into the
``ReadOutput`` (exit code, stdout, stderr) the two operator scripts print;
the scripts reach them through ``pp_run_ops`` as the ``get_release_state``
and ``get_deep_verify_status`` verbs of ``python3 -m pgai_agent_kanban.ops``.
What is proven here:

  1. ``render_release_state``: the four ``key=value`` lines in schema order
     (exit 0), an unset field printing ``none``; a project with no directory
     refused with exit 2 and ``release-state.sh: Project not found: ...``; a
     field the schema refuses refused with exit 3 and the error-contract
     block (error, message, remedy) on stderr; a refusal text with no
     house-shape block falls back to the shared key and remedy.
  2. ``render_deep_verify_status``: the never-run and populated-ledger
     renderings (exit 0); a project with no directory refused with exit 2;
     a malformed ledger and an unrecognized ``deep_verify`` value refused
     with exit 3 and the refusal text verbatim on stderr.
  3. The ops CLI verbs print the render result and exit with its code; a
     wrong argument count exits 1.
  4. The shipped scripts, run from a directory outside the repository under
     a rebuilt environment, print exactly what the render functions return.

Every board is a throwaway directory under ``tmp_path``.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops import read as ops_read
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import Refused

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/tests/unit/ -> team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_PROJECT = "alpha"
_UNBUILT = "ghost"  # registered in projects.cfg, but no project directory

_RELEASE_STATE = (
    "## Active RC\nv1.2.3\n\n## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
    "## RC Opened By Task\nCM-20260101-001-open\n\n## Last Released\nv1.2.2\n"
)
_RELEASE_STATE_STDOUT = (
    "active_rc=v1.2.3\n"
    "rc_opened_at=2026-01-01T00:00:00+00:00\n"
    "rc_opened_by_task=CM-20260101-001-open\n"
    "last_released=v1.2.2\n"
)
_MALFORMED_RELEASE_STATE = (
    "## Active RC\nNOTAVERSION\n\n## RC Opened At\nnone\n\n" "## RC Opened By Task\nnone\n\n## Last Released\nnone\n"
)
_LEDGER_STDOUT = (
    f"project: {_PROJECT}\n"
    "mode: off\n"
    "latest: failed-to-build (2026-01-08T00:00:00+00:00, v1.1.0, bbbbbbb)\n"
    "\n"
    "Ledger (2 entries):\n"
    f"## Deep-Verify Run: 2026-01-01T00:00:00+00:00 — {_PROJECT} — v1.0.0\n"
    "- Commit: aaaaaaa\n"
    "- Verdict: built\n"
    "- Detail: all pairs built\n"
    "\n"
    f"## Deep-Verify Run: 2026-01-08T00:00:00+00:00 — {_PROJECT} — v1.1.0\n"
    "- Commit: bbbbbbb\n"
    "- Verdict: failed-to-build\n"
    "- Detail: docker/x: failed at step: boom\n"
    "\n"
)
_NEVER_RUN_STDOUT = f"project: {_PROJECT}\nmode: weekly\nlatest: never-run\n\nLedger (0 entries):\n"


@pytest.fixture
def board(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root registering ``alpha`` (with a directory) and ``ghost`` (without one)."""
    root = tmp_path / "board"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n[project:{_UNBUILT}]\npriority=2\nenabled=true\n",
        encoding="utf-8",
    )
    return root


def _project(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _seed_ledger(root: pathlib.Path) -> None:
    """Two ledger records and a project-level ``deep_verify = off``."""
    from pgai_agent_kanban.deep_verify.ledger import DeepVerifyRecord, append_record

    project = _project(root)
    (project / "project.cfg").write_text("[release]\ndeep_verify = off\n", encoding="utf-8")
    ledger = str(project / "deep-verify-ledger.md")
    append_record(
        ledger,
        DeepVerifyRecord(
            timestamp="2026-01-01T00:00:00+00:00",
            project=_PROJECT,
            version="v1.0.0",
            commit="aaaaaaa",
            verdict="built",
            detail="all pairs built",
        ),
    )
    append_record(
        ledger,
        DeepVerifyRecord(
            timestamp="2026-01-08T00:00:00+00:00",
            project=_PROJECT,
            version="v1.1.0",
            commit="bbbbbbb",
            verdict="failed-to-build",
            detail="docker/x: failed at step: boom",
        ),
    )


def _diagnostic(result: subprocess.CompletedProcess | ops_read.ReadOutput) -> str:
    code = getattr(result, "returncode", getattr(result, "exit_code", None))
    return f"--- exit {code} ---\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


# ---------------------------------------------------------------------------
# 1. render_release_state
# ---------------------------------------------------------------------------


def test_release_state_prints_the_four_fields_in_schema_order(board: pathlib.Path) -> None:
    (_project(board) / "release-state.md").write_text(_RELEASE_STATE, encoding="utf-8")

    output = ops_read.render_release_state(OpsContext(kanban_root=board), _PROJECT)

    assert output == ops_read.ReadOutput(exit_code=0, stdout=_RELEASE_STATE_STDOUT, stderr=""), _diagnostic(output)


def test_release_state_prints_none_for_an_unset_field(board: pathlib.Path) -> None:
    (_project(board) / "release-state.md").write_text(
        "## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n", encoding="utf-8"
    )

    output = ops_read.render_release_state(OpsContext(kanban_root=board), _PROJECT)

    assert output.exit_code == 0, _diagnostic(output)
    assert output.stdout == "active_rc=none\nrc_opened_at=none\nrc_opened_by_task=none\nlast_released=none\n"


def test_release_state_refuses_a_project_with_no_directory_with_exit_2(board: pathlib.Path) -> None:
    output = ops_read.render_release_state(OpsContext(kanban_root=board), _UNBUILT)

    expected_dir = board / "projects" / _UNBUILT
    assert (output.exit_code, output.stdout) == (2, ""), _diagnostic(output)
    assert output.stderr == f"release-state.sh: Project not found: {_UNBUILT!r} (expected at {expected_dir})\n"


@pytest.mark.parametrize(
    "release_state_text", [_MALFORMED_RELEASE_STATE, None], ids=["malformed-field", "missing-file"]
)
def test_release_state_refuses_a_schema_failure_with_the_error_contract_and_exit_3(
    board: pathlib.Path, release_state_text: str | None
) -> None:
    if release_state_text is not None:
        (_project(board) / "release-state.md").write_text(release_state_text, encoding="utf-8")

    output = ops_read.render_release_state(OpsContext(kanban_root=board), _PROJECT)

    assert (output.exit_code, output.stdout) == (3, ""), _diagnostic(output)
    fields = parse_error_contract(output.stderr)
    assert fields is not None, _diagnostic(output)
    assert fields["error"] == ops_read.RELEASE_STATE_REFUSAL_ERROR_KEY
    assert "release-state.md failed validation" in fields["message"]
    assert fields["remedy"].strip(), _diagnostic(output)
    assert output.stderr.startswith("error:") and output.stderr.endswith("\n")
    if release_state_text is not None:
        assert "NOTAVERSION" in fields["message"]


def test_release_state_refusal_without_a_house_shape_block_uses_the_shared_fallbacks(
    board: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(ctx: OpsContext, project: str) -> dict:
        raise Refused("release-state.md could not be validated")

    monkeypatch.setattr(ops_read, "get_release_state", _refuse)

    output = ops_read.render_release_state(OpsContext(kanban_root=board), _PROJECT)

    assert output.exit_code == 3, _diagnostic(output)
    assert parse_error_contract(output.stderr) == {
        "error": ops_read.RELEASE_STATE_REFUSAL_ERROR_KEY,
        "message": "release-state.md could not be validated",
        "remedy": ops_read.RELEASE_STATE_REFUSAL_FALLBACK_REMEDY,
    }


# ---------------------------------------------------------------------------
# 2. render_deep_verify_status
# ---------------------------------------------------------------------------


def test_deep_verify_status_for_a_never_run_project(board: pathlib.Path) -> None:
    output = ops_read.render_deep_verify_status(OpsContext(kanban_root=board), _PROJECT)

    assert output == ops_read.ReadOutput(exit_code=0, stdout=_NEVER_RUN_STDOUT, stderr=""), _diagnostic(output)


def test_deep_verify_status_prints_the_mode_latest_and_every_ledger_record(board: pathlib.Path) -> None:
    _seed_ledger(board)

    output = ops_read.render_deep_verify_status(OpsContext(kanban_root=board), _PROJECT)

    assert output == ops_read.ReadOutput(exit_code=0, stdout=_LEDGER_STDOUT, stderr=""), _diagnostic(output)


def test_deep_verify_status_refuses_a_project_with_no_directory_with_exit_2(board: pathlib.Path) -> None:
    output = ops_read.render_deep_verify_status(OpsContext(kanban_root=board), _UNBUILT)

    expected_dir = board / "projects" / _UNBUILT
    assert (output.exit_code, output.stdout) == (2, ""), _diagnostic(output)
    assert output.stderr == (f"deep-verify-status.sh: Project not found: {_UNBUILT!r} (expected at {expected_dir})\n")


def test_deep_verify_status_prints_a_malformed_ledger_refusal_verbatim_with_exit_3(board: pathlib.Path) -> None:
    ledger = _project(board) / "deep-verify-ledger.md"
    ledger.write_text("not a ledger record\n", encoding="utf-8")
    ctx = OpsContext(kanban_root=board)
    with pytest.raises(Refused) as refused:
        ops_read.get_deep_verify_status(ctx, _PROJECT)

    output = ops_read.render_deep_verify_status(ctx, _PROJECT)

    assert (output.exit_code, output.stdout) == (3, ""), _diagnostic(output)
    assert output.stderr == f"{refused.value}\n"
    assert str(ledger) in output.stderr


def test_deep_verify_status_refuses_an_unrecognized_mode_naming_the_file_with_exit_3(board: pathlib.Path) -> None:
    project_cfg = _project(board) / "project.cfg"
    project_cfg.write_text("[release]\ndeep_verify = bogus\n", encoding="utf-8")

    output = ops_read.render_deep_verify_status(OpsContext(kanban_root=board), _PROJECT)

    assert (output.exit_code, output.stdout) == (3, ""), _diagnostic(output)
    assert output.stderr.startswith("deep_verify: unrecognized value 'bogus'"), _diagnostic(output)
    assert f"read from {project_cfg} [release] deep_verify" in output.stderr


# ---------------------------------------------------------------------------
# 3. The ops CLI verbs
# ---------------------------------------------------------------------------


def _ops_cli(*args: str) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_TEAM_DIR), "LANG": "C.UTF-8"}
    return subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.ops", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_TEAM_DIR),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def test_cli_verbs_print_the_render_result_and_exit_with_its_code(board: pathlib.Path) -> None:
    (_project(board) / "release-state.md").write_text(_MALFORMED_RELEASE_STATE, encoding="utf-8")
    _seed_ledger(board)
    ctx = OpsContext(kanban_root=board)

    cases = [
        (("get_release_state", str(board), _PROJECT), ops_read.render_release_state(ctx, _PROJECT)),
        (("get_release_state", str(board), _UNBUILT), ops_read.render_release_state(ctx, _UNBUILT)),
        (("get_deep_verify_status", str(board), _PROJECT), ops_read.render_deep_verify_status(ctx, _PROJECT)),
        (("get_deep_verify_status", str(board), _UNBUILT), ops_read.render_deep_verify_status(ctx, _UNBUILT)),
    ]

    for argv, expected in cases:
        result = _ops_cli(*argv)
        assert (result.returncode, result.stdout, result.stderr) == (
            expected.exit_code,
            expected.stdout,
            expected.stderr,
        ), f"{argv}\n{_diagnostic(result)}"
    assert {expected.exit_code for _argv, expected in cases} == {2, 3, 0}


@pytest.mark.parametrize(
    "argv",
    [
        ["get_release_state", "root"],
        ["get_release_state", "root", "p", "extra"],
        ["get_deep_verify_status", "root"],
        ["get_deep_verify_status", "root", "p", "extra"],
    ],
)
def test_cli_wrong_argument_counts_exit_one(argv: list[str], capfd: pytest.CaptureFixture) -> None:
    assert ops_read._cli_main(argv) == 1
    assert f"read {argv[0]}: wrong number of arguments" in capfd.readouterr().err


# ---------------------------------------------------------------------------
# 4. The shipped scripts
# ---------------------------------------------------------------------------


def _run_script(script: str, root: pathlib.Path, project: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """Run a shipped script from a directory outside the repository, environment rebuilt."""
    home = tmp_path / "home"
    foreign = tmp_path / "elsewhere"
    home.mkdir(exist_ok=True)
    foreign.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    return subprocess.run(
        ["bash", str(_SCRIPTS_DIR / script), "--project", project],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(foreign),
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


@pytest.mark.parametrize(
    "script, render, seed, project",
    [
        pytest.param("release-state.sh", "render_release_state", "release-state", _PROJECT, id="release-state-ok"),
        pytest.param("release-state.sh", "render_release_state", "malformed", _PROJECT, id="release-state-refused"),
        pytest.param("release-state.sh", "render_release_state", None, _UNBUILT, id="release-state-not-found"),
        pytest.param("deep-verify-status.sh", "render_deep_verify_status", "ledger", _PROJECT, id="deep-verify-ok"),
        pytest.param(
            "deep-verify-status.sh", "render_deep_verify_status", "bad-ledger", _PROJECT, id="deep-verify-refused"
        ),
        pytest.param("deep-verify-status.sh", "render_deep_verify_status", None, _UNBUILT, id="deep-verify-not-found"),
    ],
)
def test_script_prints_exactly_what_its_render_function_returns(
    board: pathlib.Path, tmp_path: pathlib.Path, script: str, render: str, seed: str | None, project: str
) -> None:
    project_dir = _project(board)
    if seed == "release-state":
        (project_dir / "release-state.md").write_text(_RELEASE_STATE, encoding="utf-8")
    elif seed == "malformed":
        (project_dir / "release-state.md").write_text(_MALFORMED_RELEASE_STATE, encoding="utf-8")
    elif seed == "ledger":
        _seed_ledger(board)
    elif seed == "bad-ledger":
        (project_dir / "deep-verify-ledger.md").write_text("not a ledger record\n", encoding="utf-8")
    expected = getattr(ops_read, render)(OpsContext(kanban_root=board), project)

    result = _run_script(script, board, project, tmp_path)

    assert (result.returncode, result.stdout, result.stderr) == (
        expected.exit_code,
        expected.stdout,
        expected.stderr,
    ), f"expected:\n{_diagnostic(expected)}\nactual:\n{_diagnostic(result)}"
