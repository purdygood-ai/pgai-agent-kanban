"""
test_helloworld_plan.py
=======================
The decomposition plan the hello-world flow harness writes:
``pgai_agent_kanban.testing.helloworld_plan``.

Covered behaviour:

  * build_plan carries the given project name, version, repo path and
    requirements path into their fields, and holds exactly one CODER task
    with the fixed slug, title, sequence, inputs and acceptance criteria;
  * write_plan writes that plan as two-space-indented JSON in the plan's own
    key order with no trailing newline;
  * the command line writes the same file, prints nothing and exits 0, and
    exits 1 with one stderr line when the plan path cannot be written.

The command line runs as a subprocess with an environment built from an
explicit set and a throwaway HOME under tmp_path.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from pgai_agent_kanban.testing import helloworld_plan
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE = "pgai_agent_kanban.testing.helloworld_plan"

_ARGS = {
    "project_name": "pgai-helloworld",
    "version": "v0.1.0",
    "repo_path": "/scratch/repo",
    "requirements_path": "/scratch/root/requirements/v0.1.0-helloworld.md",
}


def _run_cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )


def test_plan_carries_the_given_arguments_into_its_fields() -> None:
    plan = helloworld_plan.build_plan(**_ARGS)

    assert plan["project_name"] == "pgai-helloworld"
    assert plan["target_version"] == "v0.1.0"
    assert plan["requirements_path"] == _ARGS["requirements_path"]
    assert plan["workflow_type"] == "release"
    assert plan["test_required"] == "true"
    assert plan["human_approval_required"] == "auto"

    assert len(plan["tasks"]) == 1
    task = plan["tasks"][0]
    assert task["working_directory"] == "/scratch/repo"
    assert task["inputs"] == [_ARGS["requirements_path"]]


def test_plan_holds_one_fixed_coder_task_for_hello_py() -> None:
    task = helloworld_plan.build_plan(**_ARGS)["tasks"][0]

    assert task["sequence"] == 1
    assert task["slug"] == "hello-world-script"
    assert task["title"] == "Implement hello.py and test_hello.py"
    assert task["role"] == "CODER"
    assert task["git_repo"] == "none"
    assert task["source_branch"] == "none"
    assert task["context_paths"] == []
    assert task["depends_on"] == []
    assert task["required_output"] == "hello.py and test_hello.py in the repo root."
    assert task["acceptance_criteria"] == [
        "python3 hello.py outputs exactly 'Hello, World!'",
        "python3 -m pytest test_hello.py -v exits 0",
        "test asserts stdout equality to 'Hello, World!\\n', not a substring",
        "python3 -m py_compile hello.py compiles clean",
    ]


def test_write_plan_writes_indented_json_without_a_trailing_newline(tmp_path: pathlib.Path) -> None:
    plan_path = tmp_path / "plan.json"

    helloworld_plan.write_plan(str(plan_path), **_ARGS)

    text = plan_path.read_text(encoding="utf-8")
    assert text == json.dumps(helloworld_plan.build_plan(**_ARGS), indent=2)
    assert not text.endswith("\n")
    assert list(json.loads(text)) == [
        "project_name", "workflow_type", "test_required", "target_version",
        "human_approval_required", "requirements_path", "tasks",
    ]


def test_write_plan_replaces_an_existing_file(tmp_path: pathlib.Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("stale content that is longer than nothing\n" * 200, encoding="utf-8")

    helloworld_plan.write_plan(str(plan_path), **_ARGS)

    assert json.loads(plan_path.read_text(encoding="utf-8")) == helloworld_plan.build_plan(**_ARGS)


def test_command_line_writes_the_plan_silently(tmp_path: pathlib.Path) -> None:
    plan_path = tmp_path / "plan.json"

    result = _run_cli(
        tmp_path, str(plan_path), _ARGS["project_name"], _ARGS["version"],
        _ARGS["repo_path"], _ARGS["requirements_path"],
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert plan_path.read_text(encoding="utf-8") == json.dumps(
        helloworld_plan.build_plan(**_ARGS), indent=2
    )


def test_command_line_reports_an_unwritable_plan_path(tmp_path: pathlib.Path) -> None:
    plan_path = tmp_path / "no-such-dir" / "plan.json"

    result = _run_cli(
        tmp_path, str(plan_path), _ARGS["project_name"], _ARGS["version"],
        _ARGS["repo_path"], _ARGS["requirements_path"],
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert result.stderr.strip().startswith(f"helloworld_plan: cannot write {plan_path}")
    assert not plan_path.exists()
