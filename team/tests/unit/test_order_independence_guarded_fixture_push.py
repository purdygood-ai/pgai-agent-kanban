"""
test_order_independence_guarded_fixture_push.py
================================================
verify-test-order-independence.sh must run each pass in an environment
where a test fixture's push into its own throwaway bare repository is
exempt from the git boundary guard, even when the sweep is run by a
non-CM agent with the guard shim first on PATH.

The guard exempts a fetch/pull/push only when the destination resolves
under the PGAI_AGENT_KANBAN_TEMP_DIR it sees. The sweep gives each pytest
pass its own temp root, so pytest's basetemp -- where every fixture's
tmp_path lives -- has to sit inside that same root. If it does not, every
fixture-local push is refused, the affected tests fail identically in
both orders, and the forward/reverse diff reports PASS without ever
having evaluated them.

Coverage (behavioural, end to end): the real script is copied into a
throwaway team tree whose only test pushes HEAD into a bare repository
under its own tmp_path. The script runs under PGAI_AGENT_ROLE=coder with
this tree's guard shim ahead of git on PATH and an evidence sink
configured -- both halves the guard needs to refuse anything at all. A
conftest in the throwaway tree records each test outcome per pass to a
file outside the script's own workdir (which the script deletes on
exit), so the test can assert the fixture PASSED in both orders and the
guard wrote no refusal record.

The script's subprocess environment is built from an explicit set
(tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env) so the
verdict cannot depend on what the invoking shell exported.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_VOI_SCRIPT = _SCRIPTS_DIR / "verify-test-order-independence.sh"
_GUARD_SHIM_DIR = _SCRIPTS_DIR / "wake" / "git-guard"

# A fixture test that does exactly what the real CM-release fixtures do:
# build a local repository plus a bare "origin" under its own tmp_path and
# push to it through whatever `git` resolves first on PATH (the guard shim
# here). Setup uses the real git binary directly so only the push itself
# goes through the guard.
_FIXTURE_PUSH_TEST = '''
import os
import subprocess


def _real_git(*args):
    subprocess.run([os.environ["GIT_GUARD_REAL_GIT"], *args], check=True)


def test_push_to_fixture_local_bare_repository(tmp_path):
    origin = tmp_path / "origin.git"
    local = tmp_path / "local"
    _real_git("init", "-q", "--bare", str(origin))
    _real_git("init", "-q", "--initial-branch=main", str(local))
    (local / "README.md").write_text("fixture\\n", encoding="utf-8")
    _real_git("-C", str(local), "add", ".")
    _real_git("-C", str(local), "commit", "-q", "-m", "initial")
    _real_git("-C", str(local), "remote", "add", "origin", str(origin))

    result = subprocess.run(
        ["git", "-C", str(local), "push", "-q", "origin", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
'''

# Records every test's call-phase outcome, labelled by the pass that ran
# it, to a file the outer test owns. The script removes its own workdir
# (logs and junit reports included) on exit, so this is the only surface
# that survives to say what each pass actually concluded.
_FIXTURE_CONFTEST = '''
import json
import os


def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    record = {
        "pass": os.environ.get("PGAI_TEST_ORDER") or "forward",
        "nodeid": report.nodeid,
        "outcome": report.outcome,
    }
    with open(os.environ["VOI_FIXTURE_OUTCOME_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\\n")
'''


def _build_throwaway_team_tree(root: pathlib.Path) -> pathlib.Path:
    """Copy the script and its sourced libraries into ``root/team`` beside
    a one-test fixture suite, and return the copied script path.

    The script resolves its test paths relative to its own location
    (``scripts/..``), so a copy placed here sweeps only the fixture suite,
    never the real estate.
    """
    team = root / "team"
    scripts = team / "scripts"
    scripts.mkdir(parents=True)
    script_copy = scripts / _VOI_SCRIPT.name
    shutil.copy2(_VOI_SCRIPT, script_copy)
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib")

    unit = team / "tests" / "unit"
    unit.mkdir(parents=True)
    (team / "tests" / "conftest.py").write_text(_FIXTURE_CONFTEST, encoding="utf-8")
    (unit / "test_fixture_push.py").write_text(_FIXTURE_PUSH_TEST, encoding="utf-8")
    # The unit target also names this path; an empty directory keeps the
    # invocation identical to the real one without adding tests.
    (team / "pgai_agent_kanban" / "api" / "tests").mkdir(parents=True)
    return script_copy


def _python3_wrapper_dir(root: pathlib.Path) -> pathlib.Path:
    """A directory holding only a ``python3`` that execs this interpreter.

    A wrapper rather than a resolved symlink: when sys.executable lives in
    a virtualenv, resolving it lands on the base interpreter and loses the
    environment's site-packages (pytest included).
    """
    bin_dir = root / "pybin"
    bin_dir.mkdir()
    wrapper = bin_dir / "python3"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return bin_dir


def test_fixture_local_push_passes_in_both_orders_under_guarded_non_cm_role(
    tmp_path: pathlib.Path,
) -> None:
    script = _build_throwaway_team_tree(tmp_path / "tree")
    framework_temp = tmp_path / "framework_tmp"
    framework_temp.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    evidence = tmp_path / "guard-evidence.jsonl"
    outcome_log = tmp_path / "outcomes.jsonl"

    real_git = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
    assert real_git, "no git binary found in the standard toolchain directories"

    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=(_GUARD_SHIM_DIR, _python3_wrapper_dir(tmp_path)),
        extra_env={
            "PGAI_AGENT_ROLE": "coder",
            "PGAI_TASK_ID": "fixture-task",
            "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE": str(evidence),
            "GIT_GUARD_REAL_GIT": real_git,
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(framework_temp),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "kanban_root"),
            "PGAI_DEV_TREE_PATH": str(tmp_path / "tree"),
            "VOI_FIXTURE_OUTCOME_LOG": str(outcome_log),
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.com",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.com",
        },
    )

    result = subprocess.run(
        ["bash", str(script), "--target", "unit"],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=str(tmp_path),
    )
    diagnostics = (
        f"exit: {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.returncode == 0, diagnostics

    assert outcome_log.is_file(), f"the fixture suite recorded no outcomes\n{diagnostics}"
    records = [
        json.loads(line)
        for line in outcome_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_pass = {r["pass"]: r["outcome"] for r in records}
    assert by_pass == {"forward": "passed", "reverse": "passed"}, (
        f"the fixture-local push must pass in both orders; recorded: {records}\n"
        f"guard evidence: "
        f"{evidence.read_text(encoding='utf-8') if evidence.exists() else '(none)'}"
    )

    assert not evidence.exists() or not evidence.read_text(encoding="utf-8").strip(), (
        "the guard refused a fixture-local push during the sweep:\n"
        + evidence.read_text(encoding="utf-8")
    )
