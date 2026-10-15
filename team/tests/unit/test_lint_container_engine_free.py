"""
test_lint_container_engine_free.py
====================================
Tests for team/scripts/lint_container_engine_free.py -- the fatal
(BLOCK-WORTHY) lint that refuses any test in the per-RC sweep set that
invokes or probes a container engine.

Tests cover:

  1. **Seeded violation is flagged**: the violation/ fixture (a direct,
     unguarded ``subprocess.run(["docker", ...])`` call) exits 1 and names
     the fixture file and line number in its output.

  2. **Clean sweep set passes**: the clean/ fixture (no container-engine
     shape at all) exits 0, reporting zero violations.

  3. **Deliberate absent-binary test is exempt**: the exempt/ fixture
     (mirrors team/tests/unit/test_binary_guard_contract.py's real
     pattern -- PATH redirected to a test-owned tmp_path before the
     docker call) exits 0 -- the PATH-redirect exemption in the module
     docstring holds.

  4. **The real per-RC sweep set (unit + integration + api/tests) is
     clean** as it exists on disk right now -- the acceptance-criterion
     proof that both gated runners' fatal wiring passes on the unmodified
     tree.

  5. **The deep-verify tier is never scanned by default**: team/tests/cold/
     (which legitimately invokes docker/podman) is excluded from the
     lint's default sweep dirs, and an explicit --sweep-dir pointed at it
     is unaffected by the "cold" path-segment skip inside a scanned root
     (the skip only matters for a root that CONTAINS a cold/ subdirectory,
     e.g. team/tests/ itself).

  6. **Missing --sweep-dir returns exit 2** (usage error).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_container_engine_free.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_container_engine_free.py"
_FIXTURE_ROOT = _TEAM_DIR / "tests" / "fixtures" / "container_engine_free"


def _import_lint_module():
    """Import lint_container_engine_free as a module."""
    spec = importlib.util.spec_from_file_location(
        "lint_container_engine_free", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Test 1: seeded violation is flagged, naming the file
# ---------------------------------------------------------------------------


def test_seeded_container_invocation_is_reported() -> None:
    fixture_dir = _FIXTURE_ROOT / "violation"
    findings = []
    for f in _lint.find_sweep_files(fixture_dir):
        findings.extend(_lint.scan_file(f))

    assert len(findings) == 1, (
        f"expected exactly one violation in {fixture_dir}, got: {findings!r}"
    )
    finding = findings[0]
    assert finding.path.name == "test_seeded_container_invocation.py", (
        f"expected the finding to name the seeded fixture file; got: {finding.path}"
    )
    assert finding.line > 0


def test_seeded_container_invocation_fails_the_cli_naming_the_file() -> None:
    """End-to-end CLI proof (acceptance criterion): running the lint as a
    subprocess-equivalent (direct main() call) against the violation
    fixture exits 1 and its printed cause names the offending file."""
    import io
    import contextlib

    fixture_dir = _FIXTURE_ROOT / "violation"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = _lint.main(["--sweep-dir", str(fixture_dir)])

    assert exit_code == 1
    output = buf.getvalue()
    assert "test_seeded_container_invocation.py" in output, (
        f"lint output does not name the offending file:\n{output}"
    )


# ---------------------------------------------------------------------------
# Test 2: clean sweep set reports nothing
# ---------------------------------------------------------------------------


def test_clean_sweep_set_reports_nothing() -> None:
    fixture_dir = _FIXTURE_ROOT / "clean"
    findings = []
    for f in _lint.find_sweep_files(fixture_dir):
        findings.extend(_lint.scan_file(f))

    assert findings == [], (
        f"expected zero violations in a clean sweep set; got: {findings!r}"
    )


def test_clean_sweep_set_exits_zero_via_cli() -> None:
    fixture_dir = _FIXTURE_ROOT / "clean"
    exit_code = _lint.main(["--sweep-dir", str(fixture_dir)])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Test 3: deliberate absent-binary test is exempt
# ---------------------------------------------------------------------------


def test_deliberate_absent_binary_test_is_not_a_violation() -> None:
    fixture_dir = _FIXTURE_ROOT / "exempt"
    findings = []
    for f in _lint.find_sweep_files(fixture_dir):
        findings.extend(_lint.scan_file(f))

    assert findings == [], (
        "a test that redirects PATH to a location it controls before "
        f"calling docker must not be reported; got: {findings!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: the real per-RC sweep set is clean right now
# ---------------------------------------------------------------------------


def test_real_per_rc_sweep_set_is_currently_clean() -> None:
    """Acceptance-criterion proof: both gated runners' fatal wiring of this
    lint exits 0 against the tree as it exists right now."""
    exit_code = _lint.main([])
    assert exit_code == 0, (
        "the real per-RC sweep set (team/tests/unit/, team/tests/integration/, "
        "team/pgai_agent_kanban/api/tests/) must be container-engine-free"
    )


# ---------------------------------------------------------------------------
# Test 5: the deep-verify tier is never scanned by default
# ---------------------------------------------------------------------------


def test_default_sweep_dirs_never_include_the_cold_tier() -> None:
    defaults = _lint._default_sweep_dirs()
    assert not any("cold" in p.parts for p in defaults), (
        f"a default sweep dir resolves through team/tests/cold/: {defaults!r}"
    )


def test_cold_tier_container_use_does_not_fail_either_gated_runner() -> None:
    """The relocated deep-verify tier (team/tests/cold/) legitimately
    invokes docker/podman -- proves the lint's default scope never reaches
    it, so a container-engine call there is invisible to this check."""
    cold_dir = _TEAM_DIR / "tests" / "cold"
    assert cold_dir.is_dir(), f"expected {cold_dir} to exist"

    defaults = _lint._default_sweep_dirs()
    for d in defaults:
        assert cold_dir != d and cold_dir not in d.parents, (
            f"default sweep dir {d} reaches the cold tier"
        )


# ---------------------------------------------------------------------------
# Test 6: missing --sweep-dir returns exit 2
# ---------------------------------------------------------------------------


def test_missing_sweep_dir_returns_usage_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    exit_code = _lint.main(["--sweep-dir", str(missing)])
    assert exit_code == 2
