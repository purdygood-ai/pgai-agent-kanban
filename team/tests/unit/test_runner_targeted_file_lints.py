"""
test_runner_targeted_file_lints.py
==================================
Behavioural tests for the per-file test-authoring lints a gated runner's
``--files`` targeted run applies (run-unit-tests.sh and
run-integration-tests.sh).

WHAT A TARGETED RUN PROMISES ABOUT THE PER-FILE LINTS
-----------------------------------------------------
- the test anti-pattern lint (lint_test_anti_patterns.py) and the
  skip-cites-a-real-bug gate (lint_skip_bug_gate.sh) run over exactly the
  validated ``--files`` paths, each invoked with ``--files <path>...`` and a
  scratch ``--findings-file`` and nothing else -- never a tests directory,
  never the runtime scripts;
- a finding in a named file prints in full, naming the file and line, and
  -- read from the lint's findings file, since both lints exit 0 on
  findings -- makes the targeted run exit 1 through the dual-channel
  ``<runner>.targeted-file-lint-findings`` contract even though every named
  test passed; the targeted exit file records that 1;
- a lint that exits non-zero has not checked the named files, so it fails
  the targeted run too;
- a run over clean files prints an OK line per lint and exits 0, even when
  an unnamed sibling in the same directory carries a finding;
- the certification files TESTER reads (``lint/findings-<suite>.json``, its
  ``.runid`` claim, ``<suite>.exit``) and the green ledger are byte-identical
  before and after a targeted run, with or without findings;
- a certification run (no ``--files``) still invokes both lints in their
  whole-tree form, unchanged.

WHY A FIXTURE TREE
------------------
Same reasoning as test_runner_targeted_files.py, whose fixture builder and
sandbox this module reuses: each case runs the REAL runner and the REAL
scripts/lib/ inside a throwaway REPO_ROOT-shaped tree under ``tmp_path``.
Cases that need real findings replace the two lint stand-ins with the REAL
lint scripts; cases that only observe how the lints are invoked replace
them with argv-recording stand-ins.

Every anti-pattern fixture is generated under ``tmp_path`` at test time and
never committed under team/tests/, so the whole-tree guards keep reporting
zero findings for the real tree. For the same reason the fixture text below
is assembled from fragments: no single source line of THIS module matches
the AP7 detector or the skip-placeholder check.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from tests.unit.test_runner_targeted_files import (
    _RUNNER_CONFIG,
    _RUNNER_NAMES,
    _Sandbox,
    _build_fixture,
    _certification_files,
    _describe,
    _exit_file,
)

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_AP_LINT = "lint_test_anti_patterns.py"
_SKIP_GATE = "lint_skip_bug_gate.sh"

# ---------------------------------------------------------------------------
# Fixture text
# ---------------------------------------------------------------------------

# The two halves AP7 pairs -- a golden-comparison marker (a "golden"-named
# identifier assigned through a pathlib join) and an inherited subprocess
# environment -- each split across string fragments.
_GOLDEN_MARKER_LINE = "_GOLD" + "EN_DIR = _HERE / " + '"surface"\n'
_INHERITED_ENV_LINE = "    env = dict(os." + "environ)\n"
_AP7_SOURCE = (
    "import os\n"
    "import pathlib\n"
    "import subprocess\n"
    "\n"
    "_HERE = pathlib.Path(__file__).parent\n" + _GOLDEN_MARKER_LINE + "\n"
    "\n"
    "def test_capture_matches_committed_surface():\n"
    + _INHERITED_ENV_LINE
    + '    subprocess.run(["true"], env=env, check=True)\n'
)
_AP7_ENV_LINENO = _AP7_SOURCE.splitlines().index(_INHERITED_ENV_LINE.rstrip("\n")) + 1

_PLACEHOLDER_ID = "BUG-" + "SKIP-synthetic-placeholder"
_PLACEHOLDER_SKIP_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_waits_on_followup():\n"
    "    pytest.skip(" + repr(_PLACEHOLDER_ID + " pending") + ")\n"
    "\n"
    "\n"
    "def test_passes_beside_the_skip():\n"
    "    assert True\n"
)

_CLEAN_SOURCE = "def test_addition_is_commutative():\n    assert 1 + 2 == 2 + 1\n"

_ARGV_STUB_PY = (
    "#!/usr/bin/env python3\n"
    "import json, os, sys\n"
    "with open(os.environ['GATE_LOG'], 'a', encoding='utf-8') as fh:\n"
    "    fh.write(json.dumps([{name!r}, *sys.argv[1:]]) + '\\n')\n"
)
_ARGV_STUB_SH = (
    "#!/usr/bin/env bash\n"
    "python3 -c 'import json, os, sys\n"
    'with open(os.environ["GATE_LOG"], "a", encoding="utf-8") as fh:\n'
    '    fh.write(json.dumps(sys.argv[1:]) + "\\n")\' {name!r} "$@"\n'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_dir(team: pathlib.Path, runner_name: str) -> pathlib.Path:
    return team / _RUNNER_CONFIG[runner_name]["test_dir"]


def _rel(runner_name: str, name: str) -> str:
    return f"{_RUNNER_CONFIG[runner_name]['test_dir']}/{name}"


# The package modules the real anti-pattern lint imports (the temp root
# resolver and what it imports, the error-contract emitter and the finding
# shape), relative to team/. The lint finds them in
# the pgai_agent_kanban/ directory beside its own scripts/, so the fixture
# tree carries them there too.
_AP_LINT_PACKAGE_MODULES = (
    "pgai_agent_kanban/env.py",
    "pgai_agent_kanban/lib/__init__.py",
    "pgai_agent_kanban/lib/error_contract.py",
    "pgai_agent_kanban/lib/ini.py",
    "pgai_agent_kanban/lib/temp_root.py",
    "pgai_agent_kanban/lint/__init__.py",
    "pgai_agent_kanban/lint/findings.py",
)


def _install_real_lints(team: pathlib.Path) -> None:
    """Replace the two lint stand-ins with the real, shipped lint scripts."""
    for name in (_AP_LINT, _SKIP_GATE):
        shutil.copy2(_SCRIPTS_DIR / name, team / "scripts" / name)
    for rel in _AP_LINT_PACKAGE_MODULES:
        (team / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_TEAM_DIR / rel, team / rel)


def _install_argv_stubs(team: pathlib.Path) -> None:
    """Replace the two lint stand-ins with ones that log their own argv as JSON."""
    for name, body in ((_AP_LINT, _ARGV_STUB_PY), (_SKIP_GATE, _ARGV_STUB_SH)):
        path = team / "scripts" / name
        path.write_text(body.format(name=name), encoding="utf-8")
        path.chmod(0o755)


def _lint_argv(sandbox: _Sandbox) -> dict[str, list[str]]:
    """The argv each argv-recording lint stand-in received, keyed by script
    name, with its ``--findings-file PATH`` pair removed (see
    _findings_file_args for that half)."""
    return {name: argv for name, (argv, _) in _split_findings_file(sandbox).items()}


def _findings_file_args(sandbox: _Sandbox) -> dict[str, list[str]]:
    """The ``--findings-file`` value(s) each argv-recording lint stand-in received."""
    return {name: paths for name, (_, paths) in _split_findings_file(sandbox).items()}


def _split_findings_file(sandbox: _Sandbox) -> dict[str, tuple[list[str], list[str]]]:
    recorded: dict[str, tuple[list[str], list[str]]] = {}
    for line in sandbox.gates_run():
        if line.startswith("["):
            name, *argv = json.loads(line)
            rest: list[str] = []
            paths: list[str] = []
            i = 0
            while i < len(argv):
                if argv[i] == "--findings-file" and i + 1 < len(argv):
                    paths.append(argv[i + 1])
                    i += 2
                    continue
                rest.append(argv[i])
                i += 1
            recorded[name] = (rest, paths)
    return recorded


def _write_test(team: pathlib.Path, runner_name: str, name: str, source: str) -> pathlib.Path:
    path = _test_dir(team, runner_name) / name
    path.write_text(source, encoding="utf-8")
    return path


def _snapshot(paths: list[pathlib.Path]) -> dict[pathlib.Path, tuple[bytes, int]]:
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}


def _seed_certification_state(sandbox: _Sandbox, runner_name: str) -> list[pathlib.Path]:
    """Write the certification files and a green-ledger file; return their paths."""
    suite = _RUNNER_CONFIG[runner_name]["suite"]
    ledger = sandbox.temp_dir / "projects" / "pgai-agent-kanban" / "green-ledger" / "no-active-rc" / "ledger.jsonl"
    seeded = [*_certification_files(sandbox, runner_name), ledger]
    for path in seeded:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{{"suite": "{suite}", "seeded": "{path.name}"}}\n', encoding="utf-8")
        os.utime(path, (1_000_000_000, 1_000_000_000))
    return seeded


def _files_under(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


# ---------------------------------------------------------------------------
# Findings print, name file and line, and fail the targeted run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_ap7_in_a_named_file_prints_file_and_line_and_fails_the_run(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    _install_real_lints(team)
    named = _write_test(team, runner_name, "test_named_golden.py", _AP7_SOURCE)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, named.name))

    assert result.returncode == 1, _describe(result)
    assert (
        f"{named}:{_AP7_ENV_LINENO}: [AP7]" in result.stdout
    ), "the AP7 finding must name the named file and the inherited-environment line\n" + _describe(result)
    assert "1 passed" in result.stdout, "the named test itself passes; the lint alone fails the run"
    assert "[targeted-lint] test_anti_patterns: finding(s) in the named files (1)" in result.stdout, _describe(result)
    assert "[targeted-lint] skip_cites_real_bug: OK" in result.stdout, _describe(result)
    expected_key = f"{runner_name.removesuffix('.sh')}.targeted-file-lint-findings"
    assert f"error:   {expected_key}" in result.stderr.splitlines(), _describe(result)
    remedy = [ln for ln in result.stderr.splitlines() if ln.startswith("remedy:  ")]
    assert remedy and f"{_AP_LINT} --files {named}" in remedy[0], _describe(result)
    assert "<" not in remedy[0], "a remedy must carry no angle-bracket placeholder"
    assert _exit_file(sandbox, runner_name).read_text(encoding="utf-8") == "1\n"


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_placeholder_skip_citation_in_a_named_file_fails_the_run(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    _install_real_lints(team)
    named = _write_test(team, runner_name, "test_named_skip.py", _PLACEHOLDER_SKIP_SOURCE)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, named.name))

    assert result.returncode == 1, _describe(result)
    assert _PLACEHOLDER_ID in result.stdout + result.stderr, _describe(result)
    assert str(named) in result.stdout + result.stderr, "the finding must name the file"
    assert "[targeted-lint] skip_cites_real_bug: finding(s)" in result.stdout, _describe(result)
    assert "[targeted-lint] test_anti_patterns: OK" in result.stdout, _describe(result)
    remedy = [ln for ln in result.stderr.splitlines() if ln.startswith("remedy:  ")]
    assert remedy and f"{_SKIP_GATE} --files {named}" in remedy[0], _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_clean_named_file_passes_and_an_unnamed_sibling_finding_is_not_reported(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    _install_real_lints(team)
    clean = _write_test(team, runner_name, "test_named_clean.py", _CLEAN_SOURCE)
    sibling = _write_test(team, runner_name, "test_unnamed_golden.py", _AP7_SOURCE)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, clean.name))

    assert result.returncode == 0, _describe(result)
    assert "[targeted-lint] test_anti_patterns: OK" in result.stdout, _describe(result)
    assert "[targeted-lint] skip_cites_real_bug: OK" in result.stdout, _describe(result)
    assert "[AP7]" not in result.stdout, _describe(result)
    assert (
        sibling.name not in result.stdout + result.stderr
    ), "a finding in a file the targeted run did not name must not be reported\n" + _describe(result)
    assert "targeted-file-lint-findings" not in result.stderr, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_pytest_failure_keeps_its_own_verdict_when_a_lint_also_reports(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    _install_real_lints(team)
    named = _write_test(
        team,
        runner_name,
        "test_named_failing_golden.py",
        _AP7_SOURCE + "\n\ndef test_fails():\n    assert False\n",
    )
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, named.name))

    assert result.returncode == 1, _describe(result)
    assert f"{named}:{_AP7_ENV_LINENO}: [AP7]" in result.stdout, _describe(result)
    assert "tests FAILED" in result.stderr, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_a_lint_that_exits_non_zero_fails_the_run_as_unchecked(runner_name: str, tmp_path: pathlib.Path) -> None:
    """Both lints exit 0 on findings, so a non-zero exit means the named
    files were never checked -- the targeted run fails rather than passing
    them unchecked."""
    team = _build_fixture(tmp_path, runner_name)
    _install_real_lints(team)
    (team / "scripts" / _SKIP_GATE).write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
    named = _write_test(team, runner_name, "test_named_clean.py", _CLEAN_SOURCE)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, named.name))

    assert result.returncode == 1, _describe(result)
    assert "[targeted-lint] skip_cites_real_bug: could not check the named files (exit 3)" in (
        result.stdout
    ), _describe(result)
    assert "[targeted-lint] test_anti_patterns: OK" in result.stdout, _describe(result)


# ---------------------------------------------------------------------------
# The lints receive the named paths and nothing else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_hands_each_lint_exactly_the_named_paths(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    _install_argv_stubs(team)
    second = _write_test(team, runner_name, "test_second.py", _CLEAN_SOURCE)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(
        team, runner_name, "--files", _rel(runner_name, "test_named.py"), _rel(runner_name, second.name)
    )

    assert result.returncode == 0, _describe(result)
    expected = ["--files", str(_test_dir(team, runner_name) / "test_named.py"), str(second)]
    assert _lint_argv(sandbox) == {_AP_LINT: expected, _SKIP_GATE: expected}, (
        "each lint must receive --files and the validated named paths only -- no "
        "--tests-dir, no runtime-script directory\n" + _describe(result)
    )
    findings_paths = _findings_file_args(sandbox)
    certification = {str(p) for p in _certification_files(sandbox, runner_name)}
    for name in (_AP_LINT, _SKIP_GATE):
        assert len(findings_paths[name]) == 1, findings_paths
        scratch = pathlib.Path(findings_paths[name][0])
        assert str(scratch) not in certification, "a targeted run must not write a certification file"
        assert sandbox.temp_dir in scratch.parents, scratch
        assert not scratch.parent.exists(), "the scratch findings directory is removed afterwards"


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_certification_run_still_invokes_both_lints_in_whole_tree_form(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    """Control: a run without --files keeps the lints' whole-tree invocation."""
    team = _build_fixture(tmp_path, runner_name)
    (_test_dir(team, runner_name) / "test_other.py").unlink()
    _install_argv_stubs(team)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name)

    assert result.returncode == 0, _describe(result)
    assert _lint_argv(sandbox) == {
        _AP_LINT: [],
        _SKIP_GATE: ["--tests-dir", str(team / "tests")],
    }, _describe(result)
    findings_paths = _findings_file_args(sandbox)
    for name in (_AP_LINT, _SKIP_GATE):
        assert len(findings_paths[name]) == 1, findings_paths
        assert pathlib.Path(findings_paths[name][0]).parent == sandbox.temp_dir / "lint", findings_paths


# ---------------------------------------------------------------------------
# Certification state is never touched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
@pytest.mark.parametrize("with_finding", [False, True], ids=["clean", "finding"])
def test_targeted_run_leaves_findings_file_and_ledger_byte_identical(
    runner_name: str, with_finding: bool, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    _install_real_lints(team)
    source = _AP7_SOURCE if with_finding else _CLEAN_SOURCE
    named = _write_test(team, runner_name, "test_named_case.py", source)
    sandbox = _Sandbox(tmp_path)
    seeded = _seed_certification_state(sandbox, runner_name)
    before = _snapshot(seeded)
    ledger_root = sandbox.temp_dir / "projects"
    ledger_files_before = _files_under(ledger_root)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, named.name))

    assert result.returncode == (1 if with_finding else 0), _describe(result)
    assert _snapshot(seeded) == before, "a targeted run changed a certification file or the ledger"
    assert _files_under(ledger_root) == ledger_files_before, "a targeted run created ledger state"


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_with_a_finding_creates_no_certification_state(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    _install_real_lints(team)
    named = _write_test(team, runner_name, "test_named_golden.py", _AP7_SOURCE)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _rel(runner_name, named.name))

    assert result.returncode == 1, _describe(result)
    for path in _certification_files(sandbox, runner_name):
        assert not path.exists(), f"a targeted run with a lint finding created {path.name}"
    assert not (sandbox.temp_dir / "projects").exists(), "a targeted run created ledger state"


# ---------------------------------------------------------------------------
# Usage text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_help_names_the_per_file_lints_a_targeted_run_applies(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--help")

    assert result.returncode == 0, _describe(result)
    text = " ".join(result.stdout.split())
    assert "test anti-pattern lint and the skip-cites-a-real-bug gate" in text, _describe(result)
    assert "skip-citation finding in a named file" in text, _describe(result)
