"""
test_runner_pytest_scope_excludes_cold_build.py
===================================================
Pins the "RC verification runs NO docker builds" guarantee mechanically:
the unit runner (run-unit-tests.sh), the integration runner
(run-integration-tests.sh), and the gated runner (run-gated-tests.sh) must
never collect a test under team/tests/cold/ -- the cold-build module is the
DEEP-VERIFY tier's only member and lives outside the RC-verification path
entirely (team/scripts/deep-verify.sh; team/scripts/run-cold-build-tests.sh
is the only thing that builds a container image cold).

team/scripts/lint_orphan_tests.py already knows team/tests/cold/ belongs to
the cold runner's pytest scope and no other -- its _COLLECTED_ROOTS mapping
is the single source this test cross-checks against, so the two files
cannot drift apart silently.

Two layers of proof, both against the runner scripts as they exist on disk
right now (never a hardcoded restatement of their PYTEST_ARGS -- a restated
copy would not notice a regression in the real script):

  1. Static -- each runner's own PYTEST_ARGS array is parsed directly out of
     its script source. None of the three may reference "cold" anywhere in
     that array. This is the assertion that actually fails when the cold
     module is added back to a runner's scope.
  2. Behavioral -- each runner's extracted PYTEST_ARGS is handed to a real
     `python3 -m pytest ... --collect-only` invocation (never a full run --
     collect-only never executes a test body, so this cannot itself trigger
     a container build) with a fake docker/podman pair placed first on PATH
     that records any invocation. Both the collected test IDs and the
     invocation recorder are asserted empty of anything cold-build-shaped,
     proving the runners' configured scope is unreachable to the cold
     module and to a container engine, not merely that nobody happened to
     run one this time.

A third test proves capability #1 would actually catch a regression: fed a
synthetic PYTEST_ARGS array containing "tests/cold/", the same parsing
+ detection logic this file uses against the real scripts flags it.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_RUNNERS = {
    "unit": _SCRIPTS_DIR / "run-unit-tests.sh",
    "integration": _SCRIPTS_DIR / "run-integration-tests.sh",
    "gated": _SCRIPTS_DIR / "run-gated-tests.sh",
}

_PYTEST_ARGS_ARRAY_RE = re.compile(r"PYTEST_ARGS=\(\s*((?:\"[^\"]*\"\s*)*)\)")
_QUOTED_ARG_RE = re.compile(r'"([^"]*)"')

_FAKE_ENGINE_STUB = (
    "#!/usr/bin/env bash\n"
    '{ echo "invoked: $0 $*"; } >> "%(marker)s"\n'
    "exit 1\n"
)


def _extract_pytest_args_from_text(script_text: str) -> list:
    """Parse the FIRST `PYTEST_ARGS=(...)` array literal out of *script_text*
    and return its quoted string entries, in order. Returns [] if the shape
    is not found -- callers treat that as "could not verify" rather than
    silently passing (see the tests below, which assert non-empty results
    for all three real runner scripts)."""
    match = _PYTEST_ARGS_ARRAY_RE.search(script_text)
    if not match:
        return []
    return _QUOTED_ARG_RE.findall(match.group(1))


def _extract_pytest_args(script_path: pathlib.Path) -> list:
    return _extract_pytest_args_from_text(script_path.read_text(encoding="utf-8"))


def _write_fake_engine(bin_dir: pathlib.Path, name: str, marker: pathlib.Path) -> None:
    stub = bin_dir / name
    stub.write_text(_FAKE_ENGINE_STUB % {"marker": marker}, encoding="utf-8")
    stub.chmod(0o755)


# ---------------------------------------------------------------------------
# Layer 1: static -- PYTEST_ARGS never references the cold module
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", sorted(_RUNNERS))
def test_runner_pytest_args_never_reference_the_cold_module(runner_name: str) -> None:
    script_path = _RUNNERS[runner_name]
    assert script_path.is_file(), f"expected {script_path} to exist"

    args = _extract_pytest_args(script_path)
    assert args, f"could not parse a PYTEST_ARGS=(...) array out of {script_path}"

    joined = " ".join(args).lower()
    assert "cold" not in joined, (
        f"{script_path.name}'s PYTEST_ARGS references the cold module "
        f"({args!r}) -- the DEEP-VERIFY tier's cold-build lane must stay "
        "outside RC verification"
    )


def test_cold_module_root_belongs_only_to_the_orphan_lints_cold_bucket() -> None:
    """Cross-checks against lint_orphan_tests.py's own mapping so the two
    files cannot drift apart silently (team/scripts/lint_orphan_tests.py
    already knows team/tests/cold/ belongs to the cold runner's pytest
    scope and no other)."""
    sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        from lint_orphan_tests import _COLLECTED_ROOTS  # type: ignore
    finally:
        if str(_SCRIPTS_DIR) in sys.path:
            sys.path.remove(str(_SCRIPTS_DIR))

    assert "tests/cold" in _COLLECTED_ROOTS

    for runner_name, script_path in _RUNNERS.items():
        args = _extract_pytest_args(script_path)
        joined = " ".join(args)
        assert "tests/cold" not in joined, (
            f"{runner_name} runner ({script_path.name}) must not collect "
            "tests/cold -- that root belongs exclusively to the cold "
            "runner's pytest scope per lint_orphan_tests.py's own mapping"
        )


# ---------------------------------------------------------------------------
# Layer 2: behavioral -- collection reaches no cold test, invokes no engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", sorted(_RUNNERS))
def test_runner_collection_invokes_no_container_engine_and_collects_no_cold_test(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    script_path = _RUNNERS[runner_name]
    args = _extract_pytest_args(script_path)
    assert args, f"could not parse a PYTEST_ARGS=(...) array out of {script_path}"

    marker = tmp_path / "engine-invocations.log"
    fake_bin = tmp_path / "fake-engine-bin"
    fake_bin.mkdir()
    _write_fake_engine(fake_bin, "docker", marker)
    _write_fake_engine(fake_bin, "podman", marker)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", *args, "--collect-only", "-q"],
        cwd=str(_TEAM_DIR),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, (
        f"collect-only failed for {runner_name} runner's PYTEST_ARGS={args!r}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert not marker.exists(), (
        f"collecting {runner_name} runner's pytest scope invoked a container "
        f"engine: {marker.read_text(encoding='utf-8') if marker.exists() else ''}"
    )
    assert "tests/cold" not in result.stdout, (
        f"{runner_name} runner's collected scope includes a tests/cold test.\n"
        f"stdout: {result.stdout}"
    )
    assert "tests" in result.stdout and "collected" in result.stdout, (
        f"expected a non-trivial collected-test summary; got:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Regression-detection capability: the static check actually catches it
# ---------------------------------------------------------------------------


def test_detection_logic_flags_the_cold_module_if_it_is_ever_added_back() -> None:
    synthetic_script = (
        "#!/usr/bin/env bash\n"
        "PYTEST_ARGS=(\n"
        '  "tests/unit/"\n'
        '  "tests/cold/"\n'
        ")\n"
    )
    args = _extract_pytest_args_from_text(synthetic_script)
    assert args == ["tests/unit/", "tests/cold/"]
    assert "cold" in " ".join(args).lower(), (
        "the same detection logic this file runs against the real runner "
        "scripts must flag a synthetic PYTEST_ARGS that re-adds tests/cold/"
    )
