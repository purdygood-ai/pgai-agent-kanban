"""
test_lint_lib_function_dedupe.py
=================================
Tests for team/scripts/lint_lib_function_dedupe.py.

Tests cover:

  1. **Clean tree passes** (positive case): the real team/scripts/lib/ directory
     produces no violations (exit 0).  This confirms that the canonical
     implementations from an earlier defect are in place and the lint is satisfied.

  2. **Behavioral negative** (planted duplicate): inject two synthetic lib
     files that both define the same function name and assert the lint exits 1
     with a clear message naming the function and the files.  This is the proof
     that the guard guards — without it, a silent scanner bug would make the
     gate always green.

  3. **Allow-list exempts provider-polymorphic pairs**: a synthetic lib dir
     containing two files that both define the same function, where both files
     are in the allow-list, must not trigger a violation.

  4. **Partial allow-list does not suppress violations**: if only ONE file in
     a duplicate pair is allow-listed, the duplicate is still reported.

  5. **find_functions_in_file extracts function names correctly**: comments and
     non-definition lines are ignored; only ``name()`` at column 0 is captured.

  6. **find_functions_in_file raises FileNotFoundError for a missing path**.

  7. **main() returns 2 for a missing lib-dir**.

  8. **main() --verbose produces per-file output** without raising an exception.

All tests use pytest's ``tmp_path`` and importlib for isolation — no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load lint_lib_function_dedupe.py as a module.
# Path: team/tests/unit/ → team/ (three levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent   # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_lib_function_dedupe.py"
_REAL_LIB_DIR = _SCRIPTS_DIR / "lib"


def _import_lint_module():
    """Import lint_lib_function_dedupe as a module."""
    spec = importlib.util.spec_from_file_location(
        "lint_lib_function_dedupe", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Helper: write a minimal lib/*.sh fixture file.
# ---------------------------------------------------------------------------


def _write_lib_sh(path: Path, func_names: list[str], extra_lines: str = "") -> None:
    """Write a synthetic lib/*.sh file defining the given function names."""
    lines = ["#!/usr/bin/env bash", f"# {path.name} — synthetic fixture"]
    for name in func_names:
        lines.append(f"{name}() {{")
        lines.append(f'    echo "{name} called"')
        lines.append("}")
    if extra_lines:
        lines.append(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_clean_real_lib_dir_produces_no_violations() -> None:
    """The real team/scripts/lib/ directory contains no duplicate function names.

    Confirms that an earlier defect's canonical deduplication is intact: after removing
    the duplicate definitions of overwatch_backup_file and overwatch_log_action,
    no function name should appear in more than one lib/*.sh file (outside the
    intentional provider-interface allow-list).
    """
    if not _REAL_LIB_DIR.is_dir():
        pytest.skip(f"Real lib dir not found at {_REAL_LIB_DIR}; skipping.")

    violations = _lint.find_duplicate_functions(_REAL_LIB_DIR, verbose=False)

    assert not violations, (
        f"Expected no duplicate function names in {_REAL_LIB_DIR}, "
        f"but find_duplicate_functions() reported {len(violations)} violation(s):\n"
        + "\n".join(
            f"  {name}() — defined in: {', '.join(files)}"
            for name, files in sorted(violations.items())
        )
    )


def test_clean_real_lib_dir_main_exits_zero() -> None:
    """main() returns 0 when run against the real team/scripts/lib/ directory."""
    if not _REAL_LIB_DIR.is_dir():
        pytest.skip(f"Real lib dir not found at {_REAL_LIB_DIR}; skipping.")

    exit_code = _lint.main(["--lib-dir", str(_REAL_LIB_DIR)])

    assert exit_code == 0, (
        f"Expected lint main() to return 0 for the clean real lib dir, "
        f"but got exit code {exit_code}."
    )


def test_planted_duplicate_triggers_violation(tmp_path: Path) -> None:
    """Behavioral negative: a lib dir with a planted duplicate exits 1 with a clear message.

    This is the proof that the guard guards: without it, a silent scanner bug
    would make the gate always green even after a reintroduction of duplicates.
    """
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()

    # Two files both define my_shared_func — the planted duplicate.
    _write_lib_sh(lib_dir / "alpha_lib.sh", ["unique_alpha_func", "my_shared_func"])
    _write_lib_sh(lib_dir / "beta_lib.sh", ["unique_beta_func", "my_shared_func"])

    violations = _lint.find_duplicate_functions(
        lib_dir,
        allow_files=frozenset(),  # no allow-list for this test
        verbose=False,
    )

    assert violations, (
        f"Expected find_duplicate_functions() to report a violation for "
        f"'my_shared_func' defined in both alpha_lib.sh and beta_lib.sh, "
        f"but got an empty result."
    )
    assert "my_shared_func" in violations, (
        f"Expected 'my_shared_func' to appear in violations, "
        f"but got: {sorted(violations.keys())}"
    )
    assert sorted(violations["my_shared_func"]) == ["alpha_lib.sh", "beta_lib.sh"], (
        f"Expected violations['my_shared_func'] to be ['alpha_lib.sh', 'beta_lib.sh'], "
        f"but got: {violations['my_shared_func']}"
    )


def test_planted_duplicate_main_exits_nonzero(tmp_path: Path) -> None:
    """main() returns exit code 1 when the lib dir contains a duplicate function.

    This verifies the end-to-end integration: find_duplicate_functions() results
    cause main() to return 1 (not 0 or 2).
    """
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_lib_sh(lib_dir / "a.sh", ["shared_func"])
    _write_lib_sh(lib_dir / "b.sh", ["shared_func"])

    exit_code = _lint.main([
        "--lib-dir", str(lib_dir),
        "--allow-file", "__no_file_allowed__",  # empty effective allow-list
    ])

    assert exit_code == 1, (
        f"Expected lint main() to return 1 for a lib dir with a duplicated "
        f"function, but got exit code {exit_code}."
    )


def test_allow_list_suppresses_duplicate_for_allowed_pair(tmp_path: Path) -> None:
    """A duplicate whose all defining files are in the allow-list is not reported.

    Models the provider-interface pattern: wake_claude_provider.sh and
    wake_codex_provider.sh both define provider_preflight; both are in the
    default allow-list; the duplicate must not trigger a violation.
    """
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_lib_sh(lib_dir / "provider_a.sh", ["provider_shared_func"])
    _write_lib_sh(lib_dir / "provider_b.sh", ["provider_shared_func"])

    violations = _lint.find_duplicate_functions(
        lib_dir,
        allow_files=frozenset({"provider_a.sh", "provider_b.sh"}),
        verbose=False,
    )

    assert not violations, (
        f"Expected no violations when all files defining 'provider_shared_func' "
        f"are in the allow-list, but got: {violations}"
    )


def test_partial_allow_list_does_not_suppress_violation(tmp_path: Path) -> None:
    """If only one file in a duplicate pair is allow-listed, the violation is still reported.

    The allow-list must exempt a duplicate ONLY when ALL defining files are listed.
    If a non-allow-listed file also defines the name, the duplicate is a real
    violation (an unintentional copy has appeared alongside the sanctioned pair).
    """
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_lib_sh(lib_dir / "allowed_file.sh", ["shared_name"])
    _write_lib_sh(lib_dir / "disallowed_file.sh", ["shared_name"])

    violations = _lint.find_duplicate_functions(
        lib_dir,
        allow_files=frozenset({"allowed_file.sh"}),  # only one file is exempted
        verbose=False,
    )

    assert violations, (
        f"Expected a violation for 'shared_name' because 'disallowed_file.sh' "
        f"is not in the allow-list, but find_duplicate_functions() returned no violations."
    )
    assert "shared_name" in violations, (
        f"Expected 'shared_name' in violations, got: {sorted(violations.keys())}"
    )


def test_find_functions_skips_comments(tmp_path: Path) -> None:
    """Comment lines beginning with '#' are not extracted as function definitions."""
    fixture = tmp_path / "fixture_lib.sh"
    fixture.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # this_is_a_comment_func() — not a real definition
            real_func() {
                echo "defined"
            }
            # another_comment_func() still not real
            """
        ),
        encoding="utf-8",
    )

    names = _lint.find_functions_in_file(fixture)

    assert names == ["real_func"], (
        f"Expected only ['real_func'] but got {names!r}."
    )


def test_find_functions_ignores_non_definition_lines(tmp_path: Path) -> None:
    """Lines that are not top-level function definitions are not extracted."""
    fixture = tmp_path / "fixture_nondef.sh"
    fixture.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            MY_VAR="hello"
            export MY_VAR
            # Inline call — not a definition:
            some_util_func
            # Definition with body on same line:
            actual_func() { echo "ok"; }
            """
        ),
        encoding="utf-8",
    )

    names = _lint.find_functions_in_file(fixture)

    assert "actual_func" in names, (
        f"Expected 'actual_func' to be extracted but got {names!r}."
    )
    # Variable assignments and bare invocations must not be included.
    assert "MY_VAR" not in names, (
        f"Variable 'MY_VAR' should not appear in extracted function names."
    )
    assert "some_util_func" not in names, (
        f"Bare invocation 'some_util_func' should not appear in extracted function names."
    )


def test_find_functions_in_missing_file_raises(tmp_path: Path) -> None:
    """find_functions_in_file() raises FileNotFoundError for a missing path."""
    missing = tmp_path / "does_not_exist.sh"

    with pytest.raises(FileNotFoundError):
        _lint.find_functions_in_file(missing)


def test_main_returns_two_for_missing_lib_dir(tmp_path: Path) -> None:
    """main() returns exit code 2 when --lib-dir does not exist."""
    missing_dir = tmp_path / "nonexistent_lib"

    exit_code = _lint.main(["--lib-dir", str(missing_dir)])

    assert exit_code == 2, (
        f"Expected lint main() to return 2 for a missing lib-dir, "
        f"but got {exit_code}."
    )


def test_main_verbose_produces_output(tmp_path: Path, capsys) -> None:
    """main() --verbose runs without raising and produces per-file output."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _write_lib_sh(lib_dir / "only_lib.sh", ["solo_func"])

    exit_code = _lint.main(["--lib-dir", str(lib_dir), "--verbose"])

    assert exit_code == 0, (
        f"Expected exit code 0 for a clean single-file lib dir with --verbose, "
        f"but got {exit_code}."
    )
    captured = capsys.readouterr()
    assert "only_lib.sh" in captured.out, (
        f"Expected 'only_lib.sh' to appear in verbose output but got:\n{captured.out}"
    )


def test_default_allow_list_covers_provider_files() -> None:
    """The default allow-list includes both provider-polymorphic lib files.

    The two wake-provider files define the same function names intentionally.
    The default allow-list must include both so the lint does not report them
    as a violation when running against the real lib/ directory.
    """
    assert "wake_claude_provider.sh" in _lint._DEFAULT_ALLOW_FILES, (
        "wake_claude_provider.sh must be in the default allow-list to suppress "
        "the provider-polymorphic function-name duplicates."
    )
    assert "wake_codex_provider.sh" in _lint._DEFAULT_ALLOW_FILES, (
        "wake_codex_provider.sh must be in the default allow-list to suppress "
        "the provider-polymorphic function-name duplicates."
    )
