"""
test_wake_batch_agent_parse.py
==============================
wake-batch.sh reads the agent from its arguments the way the provider wake
scripts read them (scripts/lib/wake_batch_guard.sh::wake_batch_guard_agent),
and refuses when it cannot:

  1. Every form the provider wake scripts accept -- --agent=coder,
     --agent coder, -a coder, the legacy positional coder -- names coder,
     alone or after any value-taking option (-s 21, --sleep 21, --sleep=21,
     --max-tasks 3, --max-tasks=3): the option's value is never read as
     the agent.
  2. A firing from which no agent can be determined exits 1, prints the
     dual-channel refusal (error key, message, runnable remedy), starts no
     provider wake script and writes no guard record.
  3. --help keeps exiting 0, its Exit codes text names the refusal, and a
     --help after other arguments still prints the usage rather than
     refusing.

The firing tests copy wake-batch.sh, scripts/lib/ and
config/default_palette.txt into a throwaway install whose wake/claude.sh is
a stub, under a sandbox kanban root built from the five live-install names
(docs/test-authoring-guidelines.md "Live-install safety"). No real wake,
provider or project is touched.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_GUARD_LIB = _SCRIPTS / "lib" / "wake_batch_guard.sh"

# The stub provider wake: records that it ran and the project its batch is
# scoped to, then exits 0.
_STUB_WAKE = """#!/usr/bin/env bash
echo "ran pid=$$ args=$*" >> "$PGAI_AGENT_KANBAN_ROOT_PATH/stub-ran"
exit 0
"""

_AGENT_FORMS = (
    ("--agent=coder",),
    ("--agent", "coder"),
    ("-a", "coder"),
    ("coder",),
)

_VALUE_OPTIONS = (
    (),
    ("-s", "21"),
    ("--sleep", "21"),
    ("--sleep=21",),
    ("--max-tasks", "3"),
    ("--max-tasks=3",),
)


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _guard_agent(*args: str) -> subprocess.CompletedProcess:
    """Run wake_batch_guard_agent over *args* in a fresh bash."""
    return subprocess.run(
        ["bash", "-c", 'set -euo pipefail; source "$1"; shift; wake_batch_guard_agent "$@"',
         "guard-agent", str(_GUARD_LIB), *args],
        capture_output=True, text=True, cwd=str(_TEAM_DIR), timeout=30, check=False,
    )


@pytest.mark.parametrize("form", _AGENT_FORMS, ids=lambda f: " ".join(f))
@pytest.mark.parametrize("options", _VALUE_OPTIONS, ids=lambda o: " ".join(o) or "no-option")
def test_every_accepted_form_names_the_agent_after_any_value_option(options, form) -> None:
    result = _guard_agent(*options, *form)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout == "coder\n", (
        f"arguments {[*options, *form]} must name coder\n" + _diagnostic(result)
    )


def test_a_short_sleep_value_before_the_agent_is_not_read_as_the_agent() -> None:
    result = _guard_agent("-s", "21", "--agent=coder")
    assert result.stdout == "coder\n", _diagnostic(result)


def test_the_last_agent_option_wins_as_in_the_provider_wake_scripts() -> None:
    result = _guard_agent("coder", "--agent=pm")
    assert result.stdout == "pm\n", _diagnostic(result)


@pytest.mark.parametrize(
    "args",
    [(), ("--sleep=0",), ("-s", "21"), ("--max-tasks", "3"), ("--agent",), ("-a",), ("--agent=Not A Role",)],
    ids=lambda a: " ".join(a) or "no-arguments",
)
def test_an_argument_list_naming_no_plain_agent_names_none(args) -> None:
    result = _guard_agent(*args)
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)


@pytest.fixture
def install(tmp_path: pathlib.Path) -> dict:
    scripts = tmp_path / "install" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(_SCRIPTS / "wake-batch.sh", scripts / "wake-batch.sh")
    shutil.copytree(_SCRIPTS / "lib", scripts / "lib", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (tmp_path / "install" / "config").mkdir()
    shutil.copy(_TEAM_DIR / "config" / "default_palette.txt", tmp_path / "install" / "config" / "default_palette.txt")
    (scripts / "wake").mkdir()
    stub = scripts / "wake" / "claude.sh"
    stub.write_text(_STUB_WAKE, encoding="utf-8")
    stub.chmod(0o755)
    root = tmp_path / "sandbox"
    (root / "tmp").mkdir(parents=True)
    (root / "projects" / "demo").mkdir(parents=True)
    (root / "projects.cfg").write_text("[project:demo]\npriority=1\nenabled=true\n", encoding="utf-8")
    return {"script": scripts / "wake-batch.sh", "root": root, "tmp": tmp_path}


def _fire(install: dict, *args: str) -> subprocess.CompletedProcess:
    root = install["root"]
    env = build_scrubbed_env(
        home_dir=install["tmp"] / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    return subprocess.run(
        ["bash", str(install["script"]), *args],
        capture_output=True, text=True, env=env, cwd=str(install["tmp"]), timeout=60, check=False,
    )


@pytest.mark.parametrize("args", [("--sleep=0",), ("-s", "21"), ("--agent=Not A Role",)], ids=" ".join)
def test_a_firing_naming_no_agent_refuses_and_starts_nothing(install, args) -> None:
    result = _fire(install, *args)

    assert result.returncode == 1, _diagnostic(result)
    contract = parse_error_contract(result.stderr)
    assert contract is not None, "no dual-channel refusal on stderr\n" + _diagnostic(result)
    assert contract["error"] == "wake_batch.agent_undetermined", _diagnostic(result)
    assert "no agent could be determined" in contract["message"], _diagnostic(result)
    assert "<" not in contract["remedy"] and "--agent=" in contract["remedy"], _diagnostic(result)
    assert not (install["root"] / "stub-ran").exists(), "a provider wake script ran\n" + _diagnostic(result)
    records = sorted((install["root"] / "locks").glob("wake-batch-*.record"))
    assert records == [], f"a guard record was written: {records}\n" + _diagnostic(result)


def test_the_refusal_remedy_runs_as_printed(install) -> None:
    refused = _fire(install, "--sleep=0")
    remedy = parse_error_contract(refused.stderr)["remedy"]
    script = str(install["script"])
    assert remedy.startswith(script + " "), _diagnostic(refused)

    result = _fire(install, *remedy[len(script) + 1:].split())

    assert result.returncode == 0, _diagnostic(result)
    assert (install["root"] / "stub-ran").exists(), "the remedy started no batch\n" + _diagnostic(result)


def test_a_short_sleep_before_the_agent_guards_that_agent(install) -> None:
    result = _fire(install, "-s", "0", "--agent=coder")

    assert result.returncode == 0, _diagnostic(result)
    locks = install["root"] / "locks"
    assert (locks / "wake-batch-coder-demo.record").is_file(), _diagnostic(result)
    assert not (locks / "wake-batch-0-demo.record").exists(), _diagnostic(result)


@pytest.mark.parametrize("args", [("--help",), ("-h",), ("--sleep=0", "--help"), ("-s", "21", "-h")], ids=" ".join)
def test_help_exits_0_and_names_the_refusal(install, args) -> None:
    result = _fire(install, *args)

    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.startswith("Usage: wake-batch.sh"), _diagnostic(result)
    exit_codes = " ".join(result.stdout.split("Exit codes:", 1)[1].split("Examples:", 1)[0].split())
    assert "no agent could be determined from the arguments" in exit_codes, _diagnostic(result)
    assert not (install["root"] / "stub-ran").exists(), "a provider wake script ran\n" + _diagnostic(result)
