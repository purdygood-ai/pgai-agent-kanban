"""
test_token_capture_module_calls_from_install_root.py
====================================================
End-to-end coverage for the token-capture call sites that reach
pgai_agent_kanban.lib.token_capture through pp_run_ops, run from an
install-shaped kanban root rather than from this dev tree:

  team/scripts/lib/token_capture.sh
      token_capture_parse_result (the claude JSON usage parse),
      token_capture_merge_into_task_artifacts on its no-jq path (the
      tokens.json write)
  team/scripts/lib/wake_driver_finalize.sh
      resolve_dominant_output_token_model (the dominant-model read)

Each lib is sourced on its own, from a copy of scripts/ and
pgai_agent_kanban/ laid out the way install.sh lays out a live root, in a
subprocess whose environment is built from an explicit minimal set (no
PYTHONPATH) and whose working directory is outside that root. That is the
shape in which a wrong root, a missing self-source of pp_run_ops.sh, or a
dependency on the caller's cwd would break a migrated site while the unit
tests (run from team/) still pass.

The assertions are the bytes the wake depends on: the usage record on
stdout, the result-text file, the tokens.json written across two
invocations, and the model the finalize stage reads back from it.

Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import textwrap

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_RESPONSE = {
    "type": "result",
    "result": "work finished",
    "usage": {"input_tokens": 4, "output_tokens": 8},
    "total_cost_usd": 0.03,
    "modelUsage": {
        "model-requested": {"inputTokens": 1, "outputTokens": 2, "costUSD": 0.01},
        "model-worker": {"inputTokens": 3, "outputTokens": 600, "costUSD": 0.02},
    },
}


@pytest.fixture()
def install_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root holding copies of scripts/ and pgai_agent_kanban/."""
    root = tmp_path / "kanban-root"
    root.mkdir()
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(
        _TEAM_DIR / "pgai_agent_kanban",
        root / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    return root


def _run(tmp_path: pathlib.Path, root: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    """Run *body* in bash from a directory outside *root*, scrubbed env."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    temp_dir = tmp_path / "kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir), "KANBAN_ROOT": str(root)},
    )
    script = "set -euo pipefail\nlog() { printf 'LOG: %s\\n' \"$*\"; }\n" + textwrap.dedent(body)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(elsewhere),
    )


def _explain(proc: subprocess.CompletedProcess) -> str:
    return f"rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"


def test_usage_parse_write_and_dominant_read_run_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    response = tmp_path / "raw.json"
    response.write_text(json.dumps(_RESPONSE), encoding="utf-8")
    result_text = tmp_path / "result.txt"
    artifacts = tmp_path / "artifacts"

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/token_capture.sh"
        source "{install_root}/scripts/lib/wake_driver_finalize.sh"
        command() {{ if [[ "$1" == "-v" && "$2" == "jq" ]]; then return 1; fi; builtin command "$@"; }}
        usage="$(token_capture_parse_result "{response}" "{result_text}" model-requested)"
        echo "USAGE=$usage"
        token_capture_merge_into_task_artifacts "$usage" "{artifacts}" model-requested claude coder 40 v9.9.9 high
        token_capture_merge_into_task_artifacts "$usage" "{artifacts}" model-requested claude coder abc ""
        echo "DOMINANT=$(resolve_dominant_output_token_model "{artifacts}/tokens.json")"
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    lines = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    usage = json.loads(lines["USAGE"])
    assert usage["canonical_model_id"] == "model-requested"
    assert usage["model_usage"]["model-worker"]["output_tokens"] == 600
    assert result_text.read_text(encoding="utf-8") == "work finished"
    tokens = json.loads((artifacts / "tokens.json").read_text(encoding="utf-8"))
    assert tokens["invocations"] == 2
    assert tokens["elapsed_seconds"] == 40, "a non-digit elapsed time adds 0"
    assert tokens["rc_version"] == "v9.9.9", "an empty RC version keeps the recorded one"
    assert tokens["effort"] == "high"
    assert tokens["model_usage"]["model-worker"]["output_tokens"] == 1200
    assert lines["DOMINANT"] == "model-worker"
