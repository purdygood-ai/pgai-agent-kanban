"""
test_gated_runner_run_evidence_isolation.py
===========================================
A certification run's evidence files are invisible to every later run.

WHAT IS PROMISED
----------------
run-unit-tests.sh and run-integration-tests.sh write their evidence at fixed
paths under the temp root: lint/findings-<suite>.json (and its .runid),
one lint/<lint>_<suite>.json per structured lint, lint/durations-<suite>.json,
and, when coverage is honoured, coverage/<suite>.json. Every certification
run first removes whatever an earlier run of its suite left at those paths
(gated_runner_clear_run_evidence in team/scripts/lib/gated_runner_lint.sh),
so after a run each file there was written by that run:

- a per-lint file the second run's lint did not write (it crashed, was
  skipped, or writes only when it has findings) is gone, and none of the
  first run's findings reaches the second run's aggregate findings file;
- a coverage report from a first run that measured coverage is gone after
  a second run that measured none;
- the other suite's files, and every file of a --files targeted run's
  contract, are left alone.

HOW
---
Each case drives the REAL runner twice into one sandbox temp root, over the
fixture tree test_runner_targeted_files.py builds (the real runner and
scripts/lib, every other script a stand-in). Selected lint stand-ins are
replaced with ones that write a findings array naming the run, but only
when the run asks them to: the first run does, the second does not. The
first run also measures real coverage (pytest-cov is a declared test
dependency, requirements-test.txt). The helper itself is then driven
directly to pin its scope and its fail-closed refusal.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.test_runner_targeted_files import _build_fixture, _describe

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"

_FIRST_RUN = "first certification run"

# Per suite: the lint stand-ins that write a findings array, and the per-lint
# file each writes. The unit list carries a lint whose block removes its file
# first (anti-pattern) and two whose blocks do not (census, hygiene); the
# integration list carries one of each (anti-pattern, hygiene).
_SUITES = {
    "run-unit-tests.sh": {
        "suite": "unit",
        "writers": {
            "lint_test_anti_patterns.py": "test_anti_patterns_unit.json",
            "lint_tester_report_census.py": "tester_report_census_unit.json",
            "lint_hygiene.py": "hygiene_unit.json",
        },
    },
    "run-integration-tests.sh": {
        "suite": "integration",
        "writers": {
            "lint_test_anti_patterns.py": "test_anti_patterns_integration.json",
            "lint_hygiene.py": "hygiene_integration.json",
        },
    },
}
_RUNNER_NAMES = list(_SUITES)

# A lint stand-in that writes one finding naming the run to the path after
# --findings-file, only when EVIDENCE_RUN names a run; otherwise it writes
# nothing, as a lint that crashed or found nothing would.
_WRITER_STUB = r"""#!/usr/bin/env python3
import json, os, sys
run = os.environ.get("EVIDENCE_RUN", "")
argv = sys.argv[1:]
if run and "--findings-file" in argv:
    path = argv[argv.index("--findings-file") + 1]
    finding = {"lint": __LINT__, "severity": "quality", "summary": "written by the " + run}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([finding], fh)
sys.exit(0)
"""


def _fixture(tmp_path: pathlib.Path, runner_name: str) -> pathlib.Path:
    """The targeted-files fixture tree with one passing test and the evidence
    writers in place of their logging stand-ins."""
    team = _build_fixture(tmp_path, runner_name)
    suite_dir = "tests/unit" if runner_name == "run-unit-tests.sh" else "tests/integration"
    (team / suite_dir / "test_other.py").unlink()
    for script in _SUITES[runner_name]["writers"]:
        path = team / "scripts" / script
        path.write_text(_WRITER_STUB.replace("__LINT__", repr(script)), encoding="utf-8")
        path.chmod(0o755)
    return team


class _Sandbox:
    """Throwaway kanban root, temp root and HOME shared by both runs."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.kanban_root = tmp_path / "kanban_root"
        self.temp_dir = tmp_path / "temp_root"
        self.home = tmp_path / "home"
        self.gate_log = tmp_path / "gate.log"
        for d in (self.kanban_root, self.temp_dir, self.home):
            d.mkdir()
        self.gate_log.touch()
        self.configure("workers = 1\n")

    def configure(self, testing: str) -> None:
        (self.kanban_root / "kanban.cfg").write_text(f"[testing]\n{testing}", encoding="utf-8")

    def run(self, team: pathlib.Path, runner_name: str, *args: str, **extra: str) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.kanban_root),
                "KANBAN_ROOT": str(self.kanban_root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "GATE_LOG": str(self.gate_log),
                **extra,
            },
        )
        return subprocess.run(
            ["bash", str(team / "scripts" / runner_name), *args],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(team.parent),
        )

    def lint(self, name: str) -> pathlib.Path:
        return self.temp_dir / "lint" / name

    def coverage(self, suite: str) -> pathlib.Path:
        return self.temp_dir / "coverage" / f"{suite}.json"


def _summaries(path: pathlib.Path) -> list[str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    findings = document["findings"] if isinstance(document, dict) else document
    return [str(f.get("summary", "")) for f in findings]


# ---------------------------------------------------------------------------
# Two certification runs into one temp root
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_second_certification_run_reads_nothing_the_first_run_wrote(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    suite = _SUITES[runner_name]["suite"]
    writers = _SUITES[runner_name]["writers"]
    team = _fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)
    aggregate = sandbox.lint(f"findings-{suite}.json")

    sandbox.configure("workers = 1\ncoverage = true\n")
    first = sandbox.run(team, runner_name, "--coverage", EVIDENCE_RUN=_FIRST_RUN)

    assert first.returncode == 0, _describe(first)
    for name in writers.values():
        assert _summaries(sandbox.lint(name)) == [f"written by the {_FIRST_RUN}"], name
    assert sum(f"written by the {_FIRST_RUN}" in s for s in _summaries(aggregate)) == len(writers), (
        "precondition: the first run's aggregate carries each writer's finding\n" + _describe(first)
    )
    assert sandbox.coverage(suite).is_file(), (
        "precondition: the first run measured coverage and wrote its report\n" + _describe(first)
    )

    sandbox.configure("workers = 1\n")
    second = sandbox.run(team, runner_name)

    assert second.returncode == 0, _describe(second)
    for name in writers.values():
        assert not sandbox.lint(name).exists(), (
            f"lint/{name} from the first run is still where the second run's consumers look; "
            "the second run's lint wrote nothing, so the file must be absent\n" + _describe(second)
        )
    leaked = [s for s in _summaries(aggregate) if _FIRST_RUN in s]
    assert leaked == [], (
        f"the second run's lint/findings-{suite}.json carries the first run's findings {leaked}\n"
        + _describe(second)
    )
    assert not sandbox.coverage(suite).exists(), (
        f"coverage/{suite}.json from the first run survived a second run that measured no coverage\n"
        + _describe(second)
    )
    announce = f"[gated-runner] run evidence: this {suite} run writes its findings to {aggregate}"
    assert announce in second.stdout, _describe(second)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_leaves_earlier_certification_evidence_untouched(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    suite = _SUITES[runner_name]["suite"]
    team = _fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)
    seeded = [
        sandbox.lint(name) for name in _SUITES[runner_name]["writers"].values()
    ] + [sandbox.coverage(suite), sandbox.lint(f"durations-{suite}.json")]
    for path in seeded:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("certification evidence\n", encoding="utf-8")
    suite_dir = "tests/unit" if runner_name == "run-unit-tests.sh" else "tests/integration"

    result = sandbox.run(team, runner_name, "--files", f"{suite_dir}/test_named.py")

    assert result.returncode == 0, _describe(result)
    for path in seeded:
        assert path.read_text(encoding="utf-8") == "certification evidence\n", f"a targeted run changed {path.name}"
    assert "[gated-runner] run evidence:" not in result.stdout, _describe(result)


# ---------------------------------------------------------------------------
# The helper: scope and refusal
# ---------------------------------------------------------------------------


def _clear(temp_root: str, suite: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    script = (
        "set -euo pipefail\n"
        f'source "{_LIB_DIR}/error_contract.sh"\n'
        f'source "{_LIB_DIR}/gated_runner_lint.sh"\n'
        'gated_runner_clear_run_evidence "$1" "$2"\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "clear", temp_root, suite],
        capture_output=True,
        text=True,
        timeout=30,
        env=build_scrubbed_env(home_dir=tmp_path),
    )


def _evidence_tree(root: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    files = {
        "unit": [
            "lint/findings-unit.json", "lint/findings-unit.json.runid", "lint/durations-unit.json",
            "lint/hygiene_unit.json", "lint/test_anti_patterns_unit.json", "coverage/unit.json",
            "coverage/unit-data/.coverage", "coverage/unit-data/.coverage.host.1.abc",
        ],
        "integration": [
            "lint/findings-integration.json", "lint/findings-integration.json.runid",
            "lint/hygiene_integration.json", "coverage/integration.json", "coverage/integration-data/.coverage",
        ],
        "neither": ["lint/notes.txt", "targeted-unit.exit", "pre-merge.exit"],
    }
    made: dict[str, list[pathlib.Path]] = {}
    for key, rels in files.items():
        made[key] = []
        for rel in rels:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("earlier run\n", encoding="utf-8")
            made[key].append(path)
    return made


@pytest.mark.parametrize(("suite", "other"), [("unit", "integration"), ("integration", "unit")])
def test_clear_removes_only_the_named_suites_evidence(suite: str, other: str, tmp_path: pathlib.Path) -> None:
    root = tmp_path / "temp_root"
    made = _evidence_tree(root)

    result = _clear(str(root), suite, tmp_path)

    assert result.returncode == 0, _describe(result)
    for path in made[suite]:
        assert not path.exists(), f"{path.relative_to(root)} from an earlier {suite} run survived"
    for path in made[other] + made["neither"]:
        assert path.exists(), f"clearing {suite} removed {path.relative_to(root)}"
    assert (root / "coverage" / f"{suite}-data").is_dir(), "the coverage data directory itself must stay"
    assert f"removed {len(made[suite])} file(s) an earlier run left there" in result.stdout, _describe(result)


@pytest.mark.parametrize(("temp_root", "suite"), [("", "unit"), ("ROOT", "*"), ("ROOT", "")])
def test_clear_with_an_unexpected_scope_removes_nothing_and_says_so(
    temp_root: str, suite: str, tmp_path: pathlib.Path
) -> None:
    root = tmp_path / "temp_root"
    made = _evidence_tree(root)
    before = sorted(p for p in root.rglob("*") if p.is_file())

    result = _clear(str(root) if temp_root == "ROOT" else temp_root, suite, tmp_path)

    assert result.returncode == 0, "an unexpected scope is narrated, never fatal\n" + _describe(result)
    assert sorted(p for p in root.rglob("*") if p.is_file()) == before, "an unexpected scope removed files"
    assert all(path.exists() for paths in made.values() for path in paths)
    for field in ("error:   gated_runner.run_evidence_scope", "message:", "remedy:  grep -n gated_runner_clear_run_evidence"):
        assert field in result.stderr, _describe(result)
    assert os.path.isabs(result.stderr.split("remedy:", 1)[1].split()[3]), _describe(result)
