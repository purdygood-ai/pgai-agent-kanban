"""
test_runner_pre_merge.py
========================
Behavioural tests for the gated runners' ``--pre-merge [--base REF]`` mode
(run-unit-tests.sh and run-integration-tests.sh).

WHAT A PRE-MERGE RUN PROMISES
-----------------------------
- the runner hands the run to the pre-merge engine
  (``python3 -m pgai_agent_kanban.testing.pre_merge``) with the suite it
  serves (``unit`` / ``integration``), the repository it lives in, the
  ``[testing] pre_merge_affected_cap`` value as ``--cap`` and the
  ``[testing] workers`` budget (lowered, never raised, by ``--workers``) as
  ``--workers``; ``--base REF`` is passed through, and omitted when not
  given so the engine picks its default base;
- the unit runner's run covers the by-path module test, the five ratchet
  lints, the four always-run whole-tree guards (whatever the diff; a
  failing guard fails the run) and the affected unit tests; the integration
  runner's covers the affected integration tests only; each prints the
  engine's summary block;
- neither runner itself runs the fatal pre-flight gates or a quality-class
  lint; only the engine's own lints and guards run;
- the exit code is the engine's, written to ``pre-merge.exit`` (unit) or
  ``pre-merge-integration.exit`` (integration), and nothing a certification
  or targeted run writes is created or touched: the findings file and its
  ``.runid`` claim, the green ledger, ``targeted-<suite>.exit``;
- ``--pre-merge`` with ``--files``, ``--coverage``, ``--quarantined`` or
  ``--reuse-if-green``, ``--base`` with no ref, ``--base`` without
  ``--pre-merge``, and a malformed ``pre_merge_affected_cap`` are refused
  with the dual-channel error/message/remedy triple before anything runs;
- ``--help`` lists ``--pre-merge``, ``--base`` and the exit file's name, and
  the unit runner's names every always-run guard under ``--pre-merge``.

WHY TWO KINDS OF FIXTURE
------------------------
Each case builds a throwaway REPO_ROOT-shaped tree holding the REAL runner
and the REAL scripts/lib/ (copied byte-for-byte) plus logging stand-ins for
every gate and lint script the runner or the engine shells out to; each
stand-in appends its own name to $GATE_LOG, which is how these tests see
what ran (test_runner_targeted_files.py uses the same shape).

The plumbing cases put a stand-in engine at
``team/pgai_agent_kanban/testing/pre_merge.py`` that records its argv and
exits with $STUB_ENGINE_EXIT, so what the runner passes and what it does
with the exit code are asserted directly. The end-to-end cases copy the
REAL engine and the few package modules it imports into a fixture git
repository (base on ``ai_rc/v9.9.9``, a change to ``scripts/greet.sh`` on a
feature branch) and assert on the engine's own summary block. Every case
runs with ``[testing] workers = 1``, so no pytest-xdist workers start inside
the suite running this test.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from pgai_agent_kanban.testing import pre_merge
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_HEADER = "pre-merge run — not a certification run"
_RC_BRANCH = "ai_rc/v9.9.9"
_FEATURE_BRANCH = "ai_feature/fixture-change"

_RUNNER_CONFIG = {
    "run-unit-tests.sh": {
        "suite": "unit",
        "test_dir": "tests/unit",
        "exit_file": "pre-merge.exit",
    },
    "run-integration-tests.sh": {
        "suite": "integration",
        "test_dir": "tests/integration",
        "exit_file": "pre-merge-integration.exit",
    },
}
_RUNNER_NAMES = list(_RUNNER_CONFIG)

# Every gate and lint script either runner (or the engine) invokes, relative
# to team/scripts/. On a pre-merge run the runner itself starts none of them;
# only the engine's five ratchet lints, its tracked-files lint and four
# always-run guards run, and only on the unit suite.
_STUBBED_SCRIPTS = [
    "check_breakage_class.py",
    "lint_dockerfile_static.py",
    "lint_container_engine_free.py",
    "lint_api_parity.py",
    "lint_test_anti_patterns.py",
    "lint_orphan_tests.py",
    "lint_operator_verb_manifest.py",
    "lint_upgrade_no_temp_copy_guard.py",
    "lint_icd_freshness.py",
    "lint_icd_compat.py",
    "lint_changelog_freshness.py",
    "lint_doc_command_truth.py",
    "lint_engine_size_dupe.py",
    "lint_env_bootstrap.py",
    "lint_help_presence.py",
    "lint_hygiene.py",
    "lint_python_import_completeness.py",
    "lint_dashboard_network_git_gate.py",
    "lint_dashboard_hardcode_colors.py",
    "lint_halt_scope_writers.py",
    "lint_raw_marker_read.py",
    "lint_status_writer_bypass.py",
    "lint_tester_report_census.py",
    "lint_docstring_coverage.py",
    "lint_dual_channel_pair.py",
    "lint_runnable_remedy.py",
    "lint_gate_census_compliance.py",
    "lint_block_record_sites.py",
    "lint_inline_python.py",
    "lint_docker_helper_path.py",
    "lint_python_duplicates.py",
    "lint_api_route_impl.py",
    "lint_api_route_docs.py",
    "lint_echo_pipe_grep_q.py",
    "lint_tracked_ignored.py",
    "lint_rc_empty_commits.py",
    "dashboard/generate_window_docs.py",
    "dashboard/lint_tmux_format_jobs.py",
    "generate_operator_verb_docs.py",
    "check_docs_citations.py",
    "lint_skip_bug_gate.sh",
    "lint_governance_byte_budgets.sh",
]

# The engine's five ratchet lints and its tracked-files lint, in the order
# it runs them.
_RATCHET_LINTS = [
    "lint_inline_python.py",
    "lint_python_duplicates.py",
    "lint_engine_size_dupe.py",
    "lint_api_route_impl.py",
    "lint_echo_pipe_grep_q.py",
    "lint_tracked_ignored.py",
]

# The engine's four always-run whole-tree guards, in the order it runs them.
_GUARD_SCRIPTS = [
    "lint_env_bootstrap.py",
    "generate_operator_verb_docs.py",
    "dashboard/generate_window_docs.py",
    "check_docs_citations.py",
]

# Each guard's summary row name, in the same order.
_GUARD_ROWS = [
    "guard: env-bootstrap", "guard: operator-verb-docs",
    "guard: dashboard-window-docs", "guard: docs-citations",
]

# A guard stand-in that finds something: logs its run, prints one finding
# and exits 1, as a real guard does on a stale tree.
_FINDING_GUARD = """\
#!/usr/bin/env python3
import os, sys
with open(os.environ['GATE_LOG'], 'a', encoding='utf-8') as fh:
    fh.write('generate_operator_verb_docs.py\\n')
print("DRIFT -- verb='halt' block in docs/operator-commands.md disagrees with its --help output")
sys.exit(1)
"""

# The real engine and what it imports, relative to team/.
_ENGINE_MODULES = [
    "pgai_agent_kanban/__init__.py",
    "pgai_agent_kanban/lib/__init__.py",
    "pgai_agent_kanban/lib/semver.py",
    "pgai_agent_kanban/lib/error_contract.py",
    "pgai_agent_kanban/testing/__init__.py",
    "pgai_agent_kanban/testing/affected.py",
    "pgai_agent_kanban/testing/pre_merge.py",
]

_STUB_ENGINE = """\
import json
import os
import sys

with open(os.environ["ENGINE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
print("pre-merge summary (stand-in engine)")
sys.exit(int(os.environ.get("STUB_ENGINE_EXIT", "0")))
"""

_BY_PATH_STUB = """\
import os


def test_package_modules_run_by_path():
    with open(os.environ["GATE_LOG"], "a", encoding="utf-8") as fh:
        fh.write("by-path\\n")
"""

_GREET_BASE = "#!/usr/bin/env bash\necho hello\n"

# One check's row in the engine's summary block: the check name, its
# verdict and its duration ("by-path   PASS      0.4s").
_SUMMARY_ROW_RE = re.compile(r"^(?P<name>[a-z][a-z: -]*?)\s+(?P<verdict>PASS|FAIL)\s+\d+\.\ds\b")


# ---------------------------------------------------------------------------
# Fixture tree
# ---------------------------------------------------------------------------


def _write_logging_stub(path: pathlib.Path, name: str) -> None:
    """A gate/lint stand-in: records *name* in $GATE_LOG, writes an empty
    findings list when given --findings-file, and exits 0."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".py":
        body = (
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "with open(os.environ['GATE_LOG'], 'a', encoding='utf-8') as fh:\n"
            f"    fh.write({name!r} + '\\n')\n"
            "args = sys.argv[1:]\n"
            "if '--findings-file' in args:\n"
            "    with open(args[args.index('--findings-file') + 1], 'w', encoding='utf-8') as fh:\n"
            "        fh.write('[]\\n')\n"
            "sys.exit(0)\n"
        )
    else:
        body = f"#!/usr/bin/env bash\nprintf '%s\\n' {name!r} >> \"$GATE_LOG\"\nexit 0\n"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write(root: pathlib.Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _build_tree(tmp_path: pathlib.Path, *, real_engine: bool) -> pathlib.Path:
    """Build the throwaway tree; return its repo root (the parent of team/)."""
    repo = tmp_path / "fixture_repo"
    team = repo / "team"
    scripts = team / "scripts"
    scripts.mkdir(parents=True)
    for runner in _RUNNER_NAMES:
        shutil.copy2(_SCRIPTS_DIR / runner, scripts / runner)
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib")
    for name in _STUBBED_SCRIPTS:
        _write_logging_stub(scripts / name, name)
    shutil.copy2(_TEAM_DIR / "pyproject.toml", team / "pyproject.toml")
    if real_engine:
        for relative in _ENGINE_MODULES:
            (team / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_TEAM_DIR / relative, team / relative)
    else:
        _write(team, {
            "pgai_agent_kanban/__init__.py": "",
            "pgai_agent_kanban/testing/__init__.py": "",
            "pgai_agent_kanban/testing/pre_merge.py": _STUB_ENGINE,
        })
    for config in _RUNNER_CONFIG.values():
        (team / config["test_dir"]).mkdir(parents=True, exist_ok=True)
    return repo


def _git(repo: pathlib.Path, home: pathlib.Path, *args: str) -> None:
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={
            "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        },
    )
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env, timeout=60)


def _build_git_fixture(
    tmp_path: pathlib.Path, home: pathlib.Path, *, greet_change: str,
    stale_operator_verb_docs: bool = False,
) -> pathlib.Path:
    """The real-engine tree as a git repository: the base on the RC branch,
    *greet_change* (the new text of scripts/greet.sh) committed on a feature
    branch on top, which is checked out. With *stale_operator_verb_docs* the
    feature commit also makes the operator-verb-docs guard stand-in report a
    finding.

    test_greet.py / test_greet_flow.py cite greet.sh, so the engine selects
    them; each asserts the script still says hello. test_untouched.py /
    test_untouched_flow.py cite nothing changed and fail if ever collected.
    """
    require_binary("git", "a fixture git repository for the pre-merge engine")
    repo = _build_tree(tmp_path, real_engine=True)
    team = repo / "team"
    asserts_hello = (
        "import pathlib\n\n\n"
        "def test_greet_sh_says_hello():\n"
        "    assert 'hello' in pathlib.Path('scripts/greet.sh').read_text(encoding='utf-8')\n"
    )
    never_collected = (
        "def test_not_affected():\n"
        "    assert False, 'collected a test file the change does not affect'\n"
    )
    _write(team, {
        "scripts/greet.sh": _GREET_BASE,
        "tests/unit/test_package_modules_run_by_path.py": _BY_PATH_STUB,
        "tests/unit/test_greet.py": asserts_hello,
        "tests/unit/test_untouched.py": never_collected,
        "tests/integration/test_greet_flow.py": asserts_hello,
        "tests/integration/test_untouched_flow.py": never_collected,
    })
    _write(repo, {".gitignore": "__pycache__/\n.pytest_cache/\n"})
    _git(repo, home, "init", "-q", "-b", _RC_BRANCH)
    _git(repo, home, "add", "-A")
    _git(repo, home, "commit", "-q", "-m", "base")
    _git(repo, home, "checkout", "-q", "-b", _FEATURE_BRANCH)
    (team / "scripts" / "greet.sh").write_text(greet_change, encoding="utf-8")
    if stale_operator_verb_docs:
        (team / "scripts" / "generate_operator_verb_docs.py").write_text(_FINDING_GUARD, encoding="utf-8")
    _git(repo, home, "commit", "-q", "-am", "change greet.sh")
    return repo


class _Sandbox:
    """Throwaway kanban root, temp root and HOME for one runner invocation.

    The kanban root holds only a kanban.cfg with a [testing] section, so
    the run's workers budget and affected-test cap are this test's own.
    """

    def __init__(self, tmp_path: pathlib.Path, testing_cfg: str = "workers = 1\npre_merge_affected_cap = 7\n") -> None:
        self.kanban_root = tmp_path / "kanban_root"
        self.temp_dir = tmp_path / "temp_root"
        self.home = tmp_path / "home"
        self.gate_log = tmp_path / "gate.log"
        self.engine_log = tmp_path / "engine.log"
        for d in (self.kanban_root, self.temp_dir, self.home):
            d.mkdir()
        self.gate_log.touch()
        self.engine_log.touch()
        (self.kanban_root / "kanban.cfg").write_text(f"[testing]\n{testing_cfg}", encoding="utf-8")

    def run(
        self, repo: pathlib.Path, runner_name: str, *args: str, engine_exit: int = 0,
    ) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.kanban_root),
                "KANBAN_ROOT": str(self.kanban_root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "GATE_LOG": str(self.gate_log),
                "ENGINE_LOG": str(self.engine_log),
                "STUB_ENGINE_EXIT": str(engine_exit),
            },
        )
        return subprocess.run(
            ["bash", str(repo / "team" / "scripts" / runner_name), *args],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(repo),
        )

    def gates_run(self) -> list[str]:
        return [ln for ln in self.gate_log.read_text(encoding="utf-8").splitlines() if ln]

    def engine_calls(self) -> list[list[str]]:
        return [json.loads(ln) for ln in self.engine_log.read_text(encoding="utf-8").splitlines() if ln]

    def exit_file(self, runner_name: str) -> pathlib.Path:
        return self.temp_dir / _RUNNER_CONFIG[runner_name]["exit_file"]

    def certification_and_targeted_files(self, runner_name: str) -> list[pathlib.Path]:
        suite = _RUNNER_CONFIG[runner_name]["suite"]
        findings = self.temp_dir / "lint" / f"findings-{suite}.json"
        return [
            findings,
            pathlib.Path(f"{findings}.runid"),
            self.temp_dir / f"{suite}.exit",
            self.temp_dir / f"targeted-{suite}.exit",
            self.temp_dir / "projects" / "pgai-agent-kanban" / "green-ledger" / "v9.9.9" / "ledger.jsonl",
        ]


def _describe(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _expected_argv(repo: pathlib.Path, runner_name: str, *, cap: str, workers: str) -> list[str]:
    return [
        "--repo", os.path.realpath(repo), "--suite", _RUNNER_CONFIG[runner_name]["suite"],
        "--cap", cap, "--workers", workers,
    ]


def _normalised(argv: list[str]) -> list[str]:
    """*argv* with the --repo value resolved, so a symlinked temp root
    compares equal to the path the runner computed with `pwd`."""
    out = list(argv)
    if "--repo" in out:
        i = out.index("--repo") + 1
        out[i] = os.path.realpath(out[i])
    return out


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_help_lists_pre_merge_base_and_the_exit_file(runner_name: str, tmp_path: pathlib.Path) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(repo, runner_name, "--help")

    assert result.returncode == 0, _describe(result)
    assert "--pre-merge [--base REF]" in result.stdout, _describe(result)
    assert "--base REF" in result.stdout, _describe(result)
    assert _RUNNER_CONFIG[runner_name]["exit_file"] in result.stdout, _describe(result)
    assert "pre_merge_affected_cap" in result.stdout, _describe(result)
    assert sandbox.engine_calls() == [], "a help invocation must not start the engine"


def test_unit_help_names_every_always_run_guard_under_pre_merge(tmp_path: pathlib.Path) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(repo, "run-unit-tests.sh", "--help")

    assert result.returncode == 0, _describe(result)
    section = result.stdout[result.stdout.index("  --pre-merge "):result.stdout.index("  --base REF ")]
    flowed = " ".join(section.split())
    for guard in pre_merge.WHOLE_TREE_GUARDS:
        assert guard.name in flowed, f"--pre-merge help does not name the {guard.name} guard\n{section}"
        assert pathlib.PurePath(guard.script).relative_to("scripts").as_posix() in flowed, (
            f"--pre-merge help does not name {guard.script}\n{section}"
        )
    assert f"{pre_merge.GUARD_BUDGET_SECONDS:.1f}s budget" in flowed, section


# ---------------------------------------------------------------------------
# What the runner hands the engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_pre_merge_hands_the_engine_its_suite_cap_and_workers(runner_name: str, tmp_path: pathlib.Path) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(repo, runner_name, "--pre-merge")

    assert result.returncode == 0, _describe(result)
    assert result.stdout.splitlines()[0] == _HEADER, _describe(result)
    assert "pre-merge summary (stand-in engine)" in result.stdout, _describe(result)
    calls = [_normalised(c) for c in sandbox.engine_calls()]
    assert calls == [_expected_argv(repo, runner_name, cap="7", workers="1")], (
        "with no --base the runner must leave the base to the engine's default, "
        f"and pass [testing] pre_merge_affected_cap and workers; got {calls}"
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_pre_merge_passes_base_through(runner_name: str, tmp_path: pathlib.Path) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(repo, runner_name, "--pre-merge", "--base", "ai_main")

    assert result.returncode == 0, _describe(result)
    calls = [_normalised(c) for c in sandbox.engine_calls()]
    assert calls == [_expected_argv(repo, runner_name, cap="7", workers="1") + ["--base", "ai_main"]], calls
    assert "Pre-merge base: ai_main" in result.stdout, _describe(result)


@pytest.mark.parametrize(
    ("testing_cfg", "extra_args", "cap", "workers"),
    [
        pytest.param("workers = 3\n", [], "60", "3", id="cap-default-60"),
        pytest.param("workers = 3\npre_merge_affected_cap = 0\n", [], "0", "3", id="cap-0-means-none"),
        pytest.param("workers = 3\n", ["--workers", "2"], "60", "2", id="workers-flag-lowers"),
        pytest.param("workers = 1\n", ["--workers", "5"], "60", "1", id="workers-flag-never-raises"),
    ],
)
def test_pre_merge_cap_and_workers_come_from_testing_config(
    testing_cfg: str, extra_args: list[str], cap: str, workers: str, tmp_path: pathlib.Path,
) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path, testing_cfg=testing_cfg)

    result = sandbox.run(repo, "run-unit-tests.sh", "--pre-merge", *extra_args)

    assert result.returncode == 0, _describe(result)
    calls = [_normalised(c) for c in sandbox.engine_calls()]
    assert calls == [_expected_argv(repo, "run-unit-tests.sh", cap=cap, workers=workers)], calls


# ---------------------------------------------------------------------------
# Exit code, exit file, and what is never written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
@pytest.mark.parametrize("engine_exit", [0, 1, 2])
def test_pre_merge_exit_code_is_the_engines_and_lands_in_its_own_exit_file(
    runner_name: str, engine_exit: int, tmp_path: pathlib.Path,
) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(repo, runner_name, "--pre-merge", engine_exit=engine_exit)

    assert result.returncode == engine_exit, _describe(result)
    assert sandbox.exit_file(runner_name).read_text(encoding="utf-8") == f"{engine_exit}\n"
    other = "run-integration-tests.sh" if runner_name == "run-unit-tests.sh" else "run-unit-tests.sh"
    assert not sandbox.exit_file(other).exists(), (
        f"{runner_name} wrote the other runner's {_RUNNER_CONFIG[other]['exit_file']}"
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_pre_merge_runs_no_gate_and_creates_no_certification_or_targeted_file(
    runner_name: str, tmp_path: pathlib.Path,
) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(repo, runner_name, "--pre-merge", engine_exit=1)

    assert result.returncode == 1, _describe(result)
    assert sandbox.gates_run() == [], (
        "a pre-merge run must start no fatal pre-flight gate and no quality-class "
        f"lint of its own; ran {sandbox.gates_run()}"
    )
    for path in sandbox.certification_and_targeted_files(runner_name):
        assert not path.exists(), f"a pre-merge run created {path.relative_to(sandbox.temp_dir)}"


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_pre_merge_leaves_existing_certification_and_targeted_files_untouched(
    runner_name: str, tmp_path: pathlib.Path,
) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path)
    seeded = {}
    for path in sandbox.certification_and_targeted_files(runner_name):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"seeded content of {path.name}\n", encoding="utf-8")
        os.utime(path, (1_000_000_000, 1_000_000_000))
        seeded[path] = (path.read_bytes(), path.stat().st_mtime_ns)

    result = sandbox.run(repo, runner_name, "--pre-merge")

    assert result.returncode == 0, _describe(result)
    for path, (content, mtime_ns) in seeded.items():
        assert path.read_bytes() == content, f"{path.name} content changed by a pre-merge run"
        assert path.stat().st_mtime_ns == mtime_ns, f"{path.name} mtime changed by a pre-merge run"
    assert "findings file written" not in result.stdout + result.stderr, _describe(result)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

_REFUSAL_CASES = [
    pytest.param(["--pre-merge", "--files", "{named}"], "pre-merge-incompatible-flags", "--files", id="with-files"),
    pytest.param(["--coverage", "--pre-merge"], "pre-merge-incompatible-flags", "--coverage", id="with-coverage"),
    pytest.param(["--pre-merge", "--quarantined"], "pre-merge-incompatible-flags", "--quarantined", id="with-quarantined"),
    pytest.param(["--pre-merge", "--reuse-if-green"], "pre-merge-incompatible-flags", "--reuse-if-green", id="with-reuse"),
    pytest.param(["--pre-merge", "--base"], "pre-merge-base-missing", "--base needs a git ref", id="base-no-ref"),
    pytest.param(["--pre-merge", "--base", "--verbose"], "pre-merge-base-missing", "'--verbose'", id="base-dash-value"),
    pytest.param(["--base", "ai_main"], "base-without-pre-merge", "without --pre-merge", id="base-alone"),
]


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
@pytest.mark.parametrize(("argv", "key_suffix", "message_names"), _REFUSAL_CASES)
def test_pre_merge_refusals_speak_the_dual_channel_contract(
    runner_name: str, argv: list[str], key_suffix: str, message_names: str, tmp_path: pathlib.Path,
) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    named = f"{_RUNNER_CONFIG[runner_name]['test_dir']}/test_named.py"
    (repo / "team" / named).write_text("def test_named():\n    pass\n", encoding="utf-8")
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(repo, runner_name, *[a.format(named=named) for a in argv])

    assert result.returncode == 1, _describe(result)
    lines = result.stderr.splitlines()
    assert f"error:   {runner_name.removesuffix('.sh')}.{key_suffix}" in lines, _describe(result)
    message = [ln for ln in lines if ln.startswith("message: ")]
    remedy = [ln for ln in lines if ln.startswith("remedy:  ")]
    assert message and message_names in message[0], _describe(result)
    assert remedy and f'{runner_name}" --pre-merge' in remedy[0], (
        "the remedy must be a corrected pre-merge run of this runner\n" + _describe(result)
    )
    assert "<" not in remedy[0], "a remedy must carry no angle-bracket placeholder"
    assert sandbox.engine_calls() == [], "a refused run must not start the engine"
    assert sandbox.gates_run() == [], "a refused run must refuse before any gate runs"
    if "--pre-merge" in argv:
        assert sandbox.exit_file(runner_name).read_text(encoding="utf-8") == "1\n", (
            "a refused pre-merge run records its exit code in its own exit file"
        )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_pre_merge_refuses_a_malformed_cap(runner_name: str, tmp_path: pathlib.Path) -> None:
    repo = _build_tree(tmp_path, real_engine=False)
    sandbox = _Sandbox(tmp_path, testing_cfg="workers = 1\npre_merge_affected_cap = abc\n")

    result = sandbox.run(repo, runner_name, "--pre-merge")

    assert result.returncode == 1, _describe(result)
    assert "error:   config_loader.testing.invalid_pre_merge_affected_cap" in result.stderr.splitlines(), (
        _describe(result)
    )
    assert sandbox.engine_calls() == [], "a malformed cap must stop the run before the engine starts"
    assert sandbox.exit_file(runner_name).read_text(encoding="utf-8") == "1\n"


# ---------------------------------------------------------------------------
# End to end, with the real engine
# ---------------------------------------------------------------------------


def test_unit_pre_merge_runs_by_path_ratchet_lints_guards_and_affected_tests(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    repo = _build_git_fixture(tmp_path, sandbox.home, greet_change="#!/usr/bin/env bash\necho hello there\n")

    result = sandbox.run(repo, "run-unit-tests.sh", "--pre-merge")

    assert result.returncode == 0, _describe(result)
    out = result.stdout
    assert "pre-merge summary" in out, _describe(result)
    rows = [m.group("name", "verdict") for m in map(_SUMMARY_ROW_RE.match, out.splitlines()) if m]
    assert rows == [
        ("by-path", "PASS"), ("lint: inline-python", "PASS"), ("lint: python-duplicates", "PASS"),
        ("lint: engine-size", "PASS"), ("lint: api-route-impl", "PASS"),
        ("lint: echo-pipe-grep-q", "PASS"),
        ("lint: tracked-ignored", "PASS"),
        *((row, "PASS") for row in _GUARD_ROWS), ("affected tests", "PASS"),
    ], "the summary block must list each unit pre-merge check, passing\n" + _describe(result)
    assert re.search(
        r"^guards:  4 always-run guard\(s\) added \d+\.\ds of the 5\.0s budget$", out, re.M,
    ), "the summary must report the guards' added time against the budget\n" + _describe(result)
    assert "pre-merge: PASS -- all 12 check(s) passed" in out, _describe(result)
    assert f"base:    {_RC_BRANCH}" in out, "the summary must name the default base\n" + _describe(result)
    assert "cap 7" in out, "the summary must name the cap from [testing] pre_merge_affected_cap\n" + _describe(result)
    assert "  tests/unit/test_greet.py" in out, _describe(result)
    assert sandbox.gates_run() == ["by-path", *_RATCHET_LINTS, *_GUARD_SCRIPTS], (
        "a unit pre-merge run runs the by-path test, the five ratchet lints and the four "
        f"always-run guards, and no fatal gate or other lint; ran {sandbox.gates_run()}"
    )
    assert sandbox.exit_file("run-unit-tests.sh").read_text(encoding="utf-8") == "0\n"
    for path in sandbox.certification_and_targeted_files("run-unit-tests.sh"):
        assert not path.exists(), f"a pre-merge run created {path.relative_to(sandbox.temp_dir)}"


def test_unit_pre_merge_fails_when_an_affected_test_fails(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    repo = _build_git_fixture(tmp_path, sandbox.home, greet_change="#!/usr/bin/env bash\necho goodbye\n")

    result = sandbox.run(repo, "run-unit-tests.sh", "--pre-merge", "--base", _RC_BRANCH)

    assert result.returncode == 1, _describe(result)
    assert "failing test file: tests/unit/test_greet.py" in result.stdout, _describe(result)
    assert "pre-merge: FAIL" in result.stdout, _describe(result)
    assert sandbox.exit_file("run-unit-tests.sh").read_text(encoding="utf-8") == "1\n"


def test_unit_pre_merge_fails_when_an_always_run_guard_finds_something(tmp_path: pathlib.Path) -> None:
    # The change touches only greet.sh, which no guard names: the guard runs
    # because it always runs, and its finding alone fails the run.
    sandbox = _Sandbox(tmp_path)
    repo = _build_git_fixture(
        tmp_path, sandbox.home, greet_change="#!/usr/bin/env bash\necho hello there\n",
        stale_operator_verb_docs=True,
    )

    result = sandbox.run(repo, "run-unit-tests.sh", "--pre-merge", "--base", _RC_BRANCH)

    assert result.returncode == 1, _describe(result)
    rows = dict(m.group("name", "verdict") for m in map(_SUMMARY_ROW_RE.match, result.stdout.splitlines()) if m)
    assert [name for name, verdict in rows.items() if verdict == "FAIL"] == ["guard: operator-verb-docs"], (
        _describe(result)
    )
    assert "    DRIFT -- verb='halt' block in docs/operator-commands.md" in result.stdout, _describe(result)
    assert "    fix: regenerate, then commit docs/operator-commands.md and docs/operator-api.md: " \
        "python3 team/scripts/generate_operator_verb_docs.py" in result.stdout, _describe(result)
    assert "pre-merge: FAIL -- 1 of 12 check(s) failed (guard: operator-verb-docs)" in result.stdout, (
        _describe(result)
    )
    assert "error:   pre_merge.checks_failed" in result.stderr.splitlines(), _describe(result)
    # Every check still ran after the guard failed.
    assert sandbox.gates_run() == ["by-path", *_RATCHET_LINTS, *_GUARD_SCRIPTS], sandbox.gates_run()
    assert sandbox.exit_file("run-unit-tests.sh").read_text(encoding="utf-8") == "1\n"
    for path in sandbox.certification_and_targeted_files("run-unit-tests.sh"):
        assert not path.exists(), f"a pre-merge run created {path.relative_to(sandbox.temp_dir)}"


def test_integration_pre_merge_runs_only_the_affected_integration_tests(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    repo = _build_git_fixture(tmp_path, sandbox.home, greet_change="#!/usr/bin/env bash\necho hello there\n")

    result = sandbox.run(repo, "run-integration-tests.sh", "--pre-merge")

    assert result.returncode == 0, _describe(result)
    rows = [m.group("name", "verdict") for m in map(_SUMMARY_ROW_RE.match, result.stdout.splitlines()) if m]
    assert rows == [("affected integration tests", "PASS")], (
        "the integration pre-merge summary has exactly one check, the affected integration tests\n"
        + _describe(result)
    )
    assert "  tests/integration/test_greet_flow.py" in result.stdout, _describe(result)
    assert sandbox.gates_run() == [], f"an integration pre-merge run ran {sandbox.gates_run()}"
    assert sandbox.exit_file("run-integration-tests.sh").read_text(encoding="utf-8") == "0\n"
    assert not sandbox.exit_file("run-unit-tests.sh").exists()
    for path in sandbox.certification_and_targeted_files("run-integration-tests.sh"):
        assert not path.exists(), f"a pre-merge run created {path.relative_to(sandbox.temp_dir)}"


def test_pre_merge_with_an_unknown_base_exits_2_dual_channel(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    repo = _build_git_fixture(tmp_path, sandbox.home, greet_change="#!/usr/bin/env bash\necho hello there\n")

    result = sandbox.run(repo, "run-unit-tests.sh", "--pre-merge", "--base", "no-such-branch")

    assert result.returncode == 2, _describe(result)
    lines = result.stderr.splitlines()
    assert any(ln.startswith("error:   ") for ln in lines), _describe(result)
    assert any(ln.startswith("remedy:  ") for ln in lines), _describe(result)
    assert "no-such-branch" in result.stderr, _describe(result)
    assert sandbox.exit_file("run-unit-tests.sh").read_text(encoding="utf-8") == "2\n"
