"""
test_lint_engine_size_dupe.py
==============================
Tests for team/scripts/lint_engine_size_dupe.py.

ENFORCEMENT CLASS THIS MODULE IMPLEMENTS (docs/blocking-site-census.md row
53: CONVERT for the lint site as a whole; see main()'s docstring in the
lint module for the exit-code split this module relies on):

  - The **size** reading (CHECK 1) is advisory only. A real engine file
    drifting over the line ceiling is a quality finding, never a pytest
    failure -- this module narrates it, it does not assert on it.
  - The **duplicate-function** reading (CHECK 2) stays a hard assertion,
    here and in main()'s own exit code (1), because two files defining the
    same shell function name in one reachability set is real damage
    (bash's last-sourced-wins semantics silently shadow an implementation),
    not a heuristic -- SOP-core.md "Blocking Severity: only real damage
    blocks".

Tests cover:

  1. **Clean tree, duplication half** (positive case): the real
     team/scripts/ directory has no cross-file function-name collisions
     with the default allow-lists -- a hard assertion.
  1b. **Clean tree, size half**: the real team/scripts/ directory's size
     reading is recorded but never asserted on -- a file drifting over the
     ceiling must not fail this suite.
  2. **Engine-size behavioral negative**: the seeded 900-line fixture under
     team/tests/fixtures/engine_size_dupe/oversized_engine/lib/discovery.sh
     is reported as exceeding the ceiling, naming the file, and main()
     returns the advisory code (3), not the hard one.
  3. **Duplicate-function behavioral negative**: the seeded fixture under
     team/tests/fixtures/engine_size_dupe/duplicate_function/ (a driver that
     sources two stage files defining the same function) is reported,
     naming both files, and main() returns the hard code (1).
  4. **Reachability model rejects false positives**: two files that define
     the same function name but are never reachable from a common ancestor
     (never sourced together) are NOT reported -- the generalization from
     lint_lib_function_dedupe.py's flat lib/*.sh scan to all of
     team/scripts/ must not reintroduce the noise that scope restriction
     was built to avoid.
  5. **Allow-list exempts the provider interface and the test-harness
     stub**: the default duplication allow-list covers provider_claude.sh /
     provider_codex.sh / harness.sh.
  6. **main() exit codes**: 0 clean, 1 duplicate-function violation (hard),
     2 missing --scripts-dir (usage), 3 size-only violation (advisory) --
     and the dupe class wins when both fire in the same run.
  7. **--help works** (Directive 6 / help-presence lint).

All synthetic fixtures use pytest's ``tmp_path`` for isolation; the two
acceptance-criteria fixtures (oversized file, duplicate function) are
committed as seeded files under team/tests/fixtures/engine_size_dupe/.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load lint_engine_size_dupe.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_engine_size_dupe.py"
_REAL_SCRIPTS_DIR = _SCRIPTS_DIR
_FIXTURES_DIR = _TEAM_DIR / "tests" / "fixtures" / "engine_size_dupe"
_OVERSIZED_FIXTURE_DIR = _FIXTURES_DIR / "oversized_engine"
_DUPLICATE_FIXTURE_DIR = _FIXTURES_DIR / "duplicate_function"


def _import_lint_module():
    """Import lint_engine_size_dupe as a module."""
    spec = importlib.util.spec_from_file_location(
        "lint_engine_size_dupe", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Helper: write a minimal synthetic *.sh fixture file.
# ---------------------------------------------------------------------------


def _write_sh(path: Path, func_names: list[str], source_lines: list[str] | None = None) -> None:
    """Write a synthetic *.sh file defining the given function names and
    optionally sourcing the given self-locating relative paths."""
    lines = ["#!/usr/bin/env bash", f"# {path.name} -- synthetic fixture"]
    lines.append('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"')
    for rel in source_lines or []:
        lines.append(f'source "${{SCRIPT_DIR}}/{rel}"')
    for name in func_names:
        lines.append(f"{name}() {{")
        lines.append(f'    echo "{name} called"')
        lines.append("}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CHECK 1: engine size
# ---------------------------------------------------------------------------


def test_clean_real_tree_size_reading_is_advisory_only() -> None:
    """CONVERT class (docs/blocking-site-census.md row 53): read the real
    team/scripts/ tree's engine-manifest line counts and narrate any file
    over the ceiling, but never fail this suite on the reading alone --
    an advisory size heuristic is not evidence of breakage. The
    only thing this test asserts is that the reading itself runs cleanly
    and returns the expected shape; a real violation is printed for a
    human to see, not raised.
    """
    if not _REAL_SCRIPTS_DIR.is_dir():
        pytest.skip(f"Real scripts dir not found at {_REAL_SCRIPTS_DIR}; skipping.")

    violations = _lint.check_engine_sizes(_REAL_SCRIPTS_DIR)

    assert isinstance(violations, dict)
    if violations:
        print(
            "\nlint_engine_size_dupe (CONVERT, advisory only -- not a "
            f"suite failure): {len(violations)} engine file(s) over the "
            f"line ceiling: {violations}"
        )


def test_seeded_oversized_fixture_triggers_violation() -> None:
    """Acceptance criterion: a seeded 900-line fixture under
    team/tests/fixtures/ at the lib/discovery.sh manifest path is reported,
    naming the file."""
    fixture_file = _OVERSIZED_FIXTURE_DIR / "lib" / "discovery.sh"
    assert fixture_file.is_file(), f"Seeded fixture missing: {fixture_file}"
    line_count = sum(1 for _ in fixture_file.read_text(encoding="utf-8").splitlines())
    assert line_count > 800, (
        f"Seeded fixture must exceed 800 lines to exercise the ceiling, "
        f"but has {line_count}."
    )

    violations = _lint.check_engine_sizes(_OVERSIZED_FIXTURE_DIR)

    assert "lib/discovery.sh" in violations, (
        f"Expected 'lib/discovery.sh' to be reported as oversized, "
        f"but got: {violations}"
    )
    assert violations["lib/discovery.sh"] == line_count


def test_seeded_oversized_fixture_main_exits_advisory_code() -> None:
    """main() returns the advisory size-only code (3), not the hard
    duplicate-function code (1), for a fixture with only a size
    violation."""
    exit_code = _lint.main(["--scripts-dir", str(_OVERSIZED_FIXTURE_DIR)])
    assert exit_code == 3, f"Expected exit code 3 (size-only, advisory), got {exit_code}."


def test_size_allow_file_suppresses_violation() -> None:
    """--size-allow-file exempts a manifest entry from the ceiling."""
    violations = _lint.check_engine_sizes(
        _OVERSIZED_FIXTURE_DIR, allow_files=frozenset({"lib/discovery.sh"})
    )
    assert not violations, (
        f"Expected the size allow-list to suppress the violation, got: {violations}"
    )


def test_nonexistent_manifest_entry_is_silently_skipped(tmp_path: Path) -> None:
    """A manifest entry absent from --scripts-dir is not an error."""
    empty_dir = tmp_path / "empty_scripts"
    empty_dir.mkdir()

    violations = _lint.check_engine_sizes(empty_dir)

    assert not violations, (
        f"Expected no violations when no manifest files exist, got: {violations}"
    )


# ---------------------------------------------------------------------------
# CHECK 2: tree-wide duplication
# ---------------------------------------------------------------------------


def test_clean_real_tree_has_no_duplicate_violations() -> None:
    """The real team/scripts/ tree has no cross-file function-name
    collisions today, using the default allow-list (provider interface +
    the harness.sh test-stub override)."""
    if not _REAL_SCRIPTS_DIR.is_dir():
        pytest.skip(f"Real scripts dir not found at {_REAL_SCRIPTS_DIR}; skipping.")

    # Deliberately exercises the real team/scripts/ tree (not a fixture):
    # this is a live regression guard, not an isolated behavior a fixture
    # could stand in for.
    violations = _lint.find_duplicate_functions(_REAL_SCRIPTS_DIR)

    assert not violations, (
        f"Expected no duplicate-function violations on the real tree, "
        f"but find_duplicate_functions() reported {len(violations)}:\n"
        + "\n".join(f"  {name}() -- {files}" for name, files in sorted(violations.items()))
    )


def test_clean_real_tree_main_never_returns_the_dupe_class() -> None:
    """main() against the real team/scripts/ tree must never return the
    hard duplicate-function code (1) or the usage code (2). 0 (fully
    clean) and 3 (size-only, advisory) are both acceptable:
    the duplication half stays a hard assertion here, the size half does
    not, matching docs/blocking-site-census.md row 53's CONVERT class.
    """
    if not _REAL_SCRIPTS_DIR.is_dir():
        pytest.skip(f"Real scripts dir not found at {_REAL_SCRIPTS_DIR}; skipping.")

    # Deliberately exercises the real team/scripts/ tree (not a fixture):
    # this is a live regression guard, not an isolated behavior a fixture
    # could stand in for.
    exit_code = _lint.main(["--scripts-dir", str(_REAL_SCRIPTS_DIR)])

    assert exit_code in (0, 3), (
        f"Expected exit code 0 (clean) or 3 (size-only, advisory), got "
        f"{exit_code} -- a duplicate-function collision (1) is real damage "
        "and must be fixed, not waived by this assertion."
    )


def test_seeded_duplicate_fixture_triggers_violation() -> None:
    """Acceptance criterion: a seeded function defined in two files that are
    both reachable from a common driver is reported, naming both files."""
    violations = _lint.find_duplicate_functions(
        _DUPLICATE_FIXTURE_DIR, allow_files=frozenset()
    )

    assert "seeded_shared_helper" in violations, (
        f"Expected 'seeded_shared_helper' to be reported, got: {sorted(violations.keys())}"
    )
    assert violations["seeded_shared_helper"] == [
        "lib/stage_a.sh",
        "lib/stage_b.sh",
    ], violations["seeded_shared_helper"]


def test_seeded_duplicate_fixture_main_exits_nonzero_and_names_both_files() -> None:
    """main() returns 1 and stdout names both colliding files."""
    exit_code = _lint.main([
        "--scripts-dir", str(_DUPLICATE_FIXTURE_DIR),
        "--allow-file", "__no_file_allowed__",
    ])
    assert exit_code == 1, f"Expected exit code 1, got {exit_code}."


def test_size_and_dupe_violations_together_return_the_hard_code(tmp_path: Path) -> None:
    """When both classes fire in the same run, main() returns the hard
    duplicate-function code (1), not the advisory size code (3) -- the
    duplication half must never be masked by a size finding riding
    alongside it."""
    scripts_dir = tmp_path / "scripts"
    lib_dir = scripts_dir / "lib"
    lib_dir.mkdir(parents=True)

    _write_sh(lib_dir / "other_stage.sh", ["combined_case_helper"])

    # lib/discovery.sh is an ENGINE_FILES manifest entry, so padding it past
    # 800 lines also exercises the size check in the same scan.
    lines = [
        "#!/usr/bin/env bash",
        "# discovery.sh -- synthetic oversized+colliding fixture",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'source "${SCRIPT_DIR}/other_stage.sh"',
    ]
    while len(lines) < 810:
        lines.append(f"# padding line {len(lines)}")
    lines += ["combined_case_helper() {", '    echo "shadowed"', "}"]
    (lib_dir / "discovery.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")

    size_violations = _lint.check_engine_sizes(scripts_dir)
    dupe_violations = _lint.find_duplicate_functions(scripts_dir, allow_files=frozenset())
    assert "lib/discovery.sh" in size_violations, size_violations
    assert "combined_case_helper" in dupe_violations, dupe_violations

    exit_code = _lint.main(["--scripts-dir", str(scripts_dir), "--allow-file", "__no_file_allowed__"])

    assert exit_code == 1, (
        f"Expected the hard duplicate-function code (1) to win over the "
        f"advisory size code (3) when both fire, got {exit_code}."
    )


def test_unreachable_duplicate_is_not_a_false_positive(tmp_path: Path) -> None:
    """Two files defining the same function name, never sourced together
    (no common ancestor), must NOT be reported.

    This is the behavioral proof that widening the scan from flat lib/*.sh
    to all of team/scripts/ does not reintroduce the false-positive noise
    (independent verify-*.sh/install-*.sh/dashboard show-*.sh helpers) the
    original lint's narrow scope was built to avoid.
    """
    scripts_dir = tmp_path / "scripts"
    _write_sh(scripts_dir / "standalone_a.sh", ["shared_but_unreachable"])
    _write_sh(scripts_dir / "standalone_b.sh", ["shared_but_unreachable"])
    # Neither sources the other, and nothing sources both.

    violations = _lint.find_duplicate_functions(scripts_dir, allow_files=frozenset())

    assert not violations, (
        f"Expected no violation for two never-co-sourced files, got: {violations}"
    )


def test_reachable_duplicate_via_third_party_driver_is_flagged(tmp_path: Path) -> None:
    """Two stage files sharing a duplicated function name, both sourced by a
    third driver file, are flagged (the general case beyond direct
    A-sources-B)."""
    scripts_dir = tmp_path / "scripts"
    _write_sh(scripts_dir / "lib" / "alpha.sh", ["dup_func"])
    _write_sh(scripts_dir / "lib" / "beta.sh", ["dup_func"])
    _write_sh(
        scripts_dir / "driver.sh",
        [],
        source_lines=["lib/alpha.sh", "lib/beta.sh"],
    )

    violations = _lint.find_duplicate_functions(scripts_dir, allow_files=frozenset())

    assert "dup_func" in violations
    assert sorted(violations["dup_func"]) == sorted(["lib/alpha.sh", "lib/beta.sh"])


def test_default_allow_list_covers_provider_interface_and_harness() -> None:
    """The default duplication allow-list names the provider interface and
    the wake/harness.sh test-stub override."""
    assert "provider_claude.sh" in _lint._DEFAULT_ALLOW_FILES
    assert "provider_codex.sh" in _lint._DEFAULT_ALLOW_FILES
    assert "harness.sh" in _lint._DEFAULT_ALLOW_FILES


def test_allow_listed_file_is_never_a_violation_source(tmp_path: Path) -> None:
    """A function defined only in allow-listed files (even if they'd
    otherwise collide) never appears in the violations."""
    scripts_dir = tmp_path / "scripts"
    _write_sh(scripts_dir / "lib" / "provider_claude.sh", ["provider_dispatch"])
    _write_sh(scripts_dir / "lib" / "provider_codex.sh", ["provider_dispatch"])
    _write_sh(
        scripts_dir / "driver.sh",
        [],
        source_lines=["lib/provider_claude.sh", "lib/provider_codex.sh"],
    )

    violations = _lint.find_duplicate_functions(scripts_dir)  # default allow-list

    assert not violations, (
        f"Expected the default allow-list to suppress the provider pair, got: {violations}"
    )


def test_allow_listed_file_removed_does_not_blind_remaining_pair(tmp_path: Path) -> None:
    """Removing an allow-listed file from candidacy must not blind the scan
    to a SEPARATE, genuine duplicate among the remaining (non-allow-listed)
    files that also collide with each other.

    Three files define the same name: harness.sh (allow-listed, shadows
    intentionally), and two unrelated production files that both also
    define it and are reachable from the same driver -- that second
    collision is a real, unintentional duplicate and must still surface
    even though one of the three definers is exempt.
    """
    scripts_dir = tmp_path / "scripts"
    _write_sh(scripts_dir / "lib" / "harness.sh", ["shared_name"])
    _write_sh(scripts_dir / "lib" / "unexpected_dup_a.sh", ["shared_name"])
    _write_sh(scripts_dir / "lib" / "unexpected_dup_b.sh", ["shared_name"])
    _write_sh(
        scripts_dir / "driver.sh",
        [],
        source_lines=[
            "lib/harness.sh",
            "lib/unexpected_dup_a.sh",
            "lib/unexpected_dup_b.sh",
        ],
    )

    violations = _lint.find_duplicate_functions(
        scripts_dir, allow_files=frozenset({"harness.sh"})
    )

    assert "shared_name" in violations
    assert violations["shared_name"] == sorted(
        ["lib/unexpected_dup_a.sh", "lib/unexpected_dup_b.sh"]
    )


# ---------------------------------------------------------------------------
# find_functions_in_file / graph primitives
# ---------------------------------------------------------------------------


def test_find_functions_skips_comments(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.sh"
    fixture.write_text(
        "#!/usr/bin/env bash\n"
        "# not_a_func() -- comment\n"
        "real_func() {\n"
        '    echo "defined"\n'
        "}\n",
        encoding="utf-8",
    )

    names = _lint.find_functions_in_file(fixture)

    assert names == ["real_func"]


def test_find_functions_in_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.sh"
    with pytest.raises(FileNotFoundError):
        _lint.find_functions_in_file(missing)


def test_source_graph_resolves_dirname_bash_source_pattern(tmp_path: Path) -> None:
    """The self-locating $(dirname "${BASH_SOURCE[0]}")/rel/path.sh pattern
    resolves to a real forward edge."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    lib_dir = scripts_dir / "lib"
    lib_dir.mkdir()
    (lib_dir / "target.sh").write_text("#!/usr/bin/env bash\ntarget_func() { :; }\n", encoding="utf-8")
    (scripts_dir / "caller.sh").write_text(
        "#!/usr/bin/env bash\n"
        'source "$(dirname "${BASH_SOURCE[0]}")/lib/target.sh"\n',
        encoding="utf-8",
    )

    files = _lint.find_shell_files(scripts_dir)
    graph = _lint.build_source_graph(scripts_dir, files)

    caller = scripts_dir / "caller.sh"
    target = (lib_dir / "target.sh").resolve()
    assert target in graph[caller], (
        f"Expected caller.sh to have a resolved edge to lib/target.sh, "
        f"got edges: {graph[caller]}"
    )


def test_unresolvable_source_argument_is_skipped_not_guessed(tmp_path: Path) -> None:
    """A source argument this lint cannot resolve (e.g. a fully dynamic
    variable with no locally-assigned literal suffix) produces no edge --
    under-connection, never a guessed (and possibly wrong) one."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "caller.sh").write_text(
        "#!/usr/bin/env bash\n"
        'source "$COMPUTED_ELSEWHERE"\n',
        encoding="utf-8",
    )

    files = _lint.find_shell_files(scripts_dir)
    graph = _lint.build_source_graph(scripts_dir, files)

    assert graph[scripts_dir / "caller.sh"] == set()


# ---------------------------------------------------------------------------
# main() usage/CLI
# ---------------------------------------------------------------------------


def test_main_returns_two_for_missing_scripts_dir(tmp_path: Path) -> None:
    missing_dir = tmp_path / "nonexistent_scripts"
    exit_code = _lint.main(["--scripts-dir", str(missing_dir)])
    assert exit_code == 2


def test_main_returns_zero_for_clean_synthetic_tree(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    _write_sh(scripts_dir / "solo.sh", ["solo_func"])

    exit_code = _lint.main(["--scripts-dir", str(scripts_dir)])

    assert exit_code == 0


def test_help_flag_exits_zero() -> None:
    """--help works without invoking the scan (Directive 6, help-presence)."""
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--scripts-dir" in result.stdout
    assert "--allow-file" in result.stdout


def test_main_verbose_produces_output(tmp_path: Path, capsys) -> None:
    scripts_dir = tmp_path / "scripts"
    _write_sh(scripts_dir / "solo.sh", ["solo_func"])

    exit_code = _lint.main(["--scripts-dir", str(scripts_dir), "--verbose"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "solo.sh" in captured.out
