"""
test_lint_dockerfile_static.py
===============================
Tests for team/scripts/lint_dockerfile_static.py -- the engine-free static
Dockerfile lint that catches two defect shapes (a malformed FROM
instruction and a non-octal COPY/ADD --chmod mode) previously seen only by
the DEEP-VERIFY cold-build lane.

Tests cover:

  1. **Real-tree positive case**: the repository's own current, corrected
     docker/ directory exits 0. This is run with PATH stripped of any
     directory containing a docker/podman binary, proving the check never
     depends on a container engine.

  2. **Duplicated-FROM fixture is flagged**: the bad_from/ fixture exits 1,
     names the file and line number, and the printed remedy is a concrete,
     runnable instruction.

  3. **Symbolic-chmod fixture is flagged**: the bad_chmod/ fixture exits 1,
     names the file and line number, with the same remedy shape.

  4. **Clean fixture passes**: the clean/ fixture (single-stage, octal
     chmod) exits 0.

  5. **Multi-stage clean fixture passes**: a second, independent, well-formed
     FROM line (the three-token "AS" shape) does not trip the arity check,
     and a COPY --from=<stage> flag (unrelated to --chmod) does not trip the
     chmod check.

  6. **Missing --docker-dir returns exit 2** (usage error).

  7. **check_from_arity / check_chmod_octal unit-level behavior**: the two
     check functions are also exercised directly against small in-memory
     line lists, independent of file I/O, to pin down the exact arity and
     octal-mode boundaries (0 args, 1 arg, 2 args, 3 args with/without AS;
     octal vs symbolic chmod values).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_dockerfile_static.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent  # repo root
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_dockerfile_static.py"
_FIXTURE_ROOT = _TEAM_DIR / "tests" / "fixtures" / "dockerfile_static"


def _import_lint_module():
    """Import lint_dockerfile_static as a module."""
    spec = importlib.util.spec_from_file_location(
        "lint_dockerfile_static", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Test 1: real-tree positive case, with PATH stripped of any engine binary
# ---------------------------------------------------------------------------


def test_real_docker_tree_passes_with_no_engine_on_path() -> None:
    """The repository's real docker/ directory exits 0, run as a subprocess
    with PATH restricted to a directory containing no docker/podman binary.

    This is the acceptance-criterion proof that the lint is genuinely
    engine-free: it must pass even when no container runtime is reachable.
    """
    real_docker_dir = _REPO_ROOT / "docker"
    assert real_docker_dir.is_dir(), (
        f"Expected the real docker/ directory at {real_docker_dir}; "
        "test tree layout may have changed."
    )

    # Build a PATH containing only a directory with `python3` (so the
    # subprocess itself can run) and nothing else -- specifically no
    # directory that could plausibly hold a `docker` or `podman` binary.
    python3_dir = str(Path(sys.executable).resolve().parent)
    restricted_path = python3_dir

    # Deliberately exercises the real docker/ directory (not a fixture):
    # this is the acceptance-criterion proof that the shipped tree passes
    # engine-free, not an isolated behavior a fixture could stand in for.
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--docker-dir", str(real_docker_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": restricted_path},
    )

    assert result.returncode == 0, (
        "lint_dockerfile_static.py must exit 0 against the real docker/ tree "
        "with PATH restricted to contain no docker/podman binary.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    for binary in ("docker", "podman"):
        assert (
            f"which {binary}" not in result.stdout
            and f"which {binary}" not in result.stderr
        ), "The lint must never probe for a container engine binary."


# ---------------------------------------------------------------------------
# Test 2: duplicated-FROM fixture is flagged
# ---------------------------------------------------------------------------


def test_duplicated_from_fixture_is_flagged(capsys: pytest.CaptureFixture[str]) -> None:
    """The bad_from/ fixture (a duplicated-keyword 'FROM FROM <image>' line)
    exits 1, names the file and line number, and prints a runnable remedy."""
    fixture_dir = _FIXTURE_ROOT / "bad_from"
    result = _lint.main(["--docker-dir", str(fixture_dir)])

    assert result == 1, (
        "lint_dockerfile_static.main() must return 1 against the bad_from/ "
        "fixture -- its duplicated FROM keyword is exactly the shape this "
        "check exists to catch."
    )

    out = capsys.readouterr().out
    dockerfile_path = fixture_dir / "app" / "Dockerfile"

    assert str(dockerfile_path) in out, (
        f"Output does not name the offending file.\nstdout: {out}"
    )
    assert ":6" in out, (
        f"Output does not name line 6 (the FROM FROM line).\nstdout: {out}"
    )
    assert "To fix:" in out, f"Output missing a 'To fix:' remedy section.\nstdout: {out}"
    assert "FROM" in out


# ---------------------------------------------------------------------------
# Test 3: symbolic-chmod fixture is flagged
# ---------------------------------------------------------------------------


def test_symbolic_chmod_fixture_is_flagged(capsys: pytest.CaptureFixture[str]) -> None:
    """The bad_chmod/ fixture (a symbolic 'COPY --chmod=+x' mode) exits 1,
    names the file and line number, and prints a runnable remedy."""
    fixture_dir = _FIXTURE_ROOT / "bad_chmod"
    result = _lint.main(["--docker-dir", str(fixture_dir)])

    assert result == 1, (
        "lint_dockerfile_static.main() must return 1 against the bad_chmod/ "
        "fixture -- its symbolic --chmod=+x is exactly the shape this "
        "check exists to catch."
    )

    out = capsys.readouterr().out
    dockerfile_path = fixture_dir / "app" / "Dockerfile"

    assert str(dockerfile_path) in out, (
        f"Output does not name the offending file.\nstdout: {out}"
    )
    assert ":8" in out, (
        f"Output does not name line 8 (the COPY --chmod=+x line).\nstdout: {out}"
    )
    assert "To fix:" in out, f"Output missing a 'To fix:' remedy section.\nstdout: {out}"
    assert "--chmod=755" in out, (
        f"Remedy must suggest a concrete octal replacement.\nstdout: {out}"
    )


# ---------------------------------------------------------------------------
# Test 4: clean single-stage fixture passes
# ---------------------------------------------------------------------------


def test_clean_fixture_passes() -> None:
    """The clean/ fixture (single valid FROM, octal chmod) exits 0."""
    fixture_dir = _FIXTURE_ROOT / "clean"
    result = _lint.main(["--docker-dir", str(fixture_dir)])

    assert result == 0, (
        "lint_dockerfile_static.main() must return 0 against the clean/ "
        "fixture -- it contains neither checked defect shape."
    )


# ---------------------------------------------------------------------------
# Test 5: multi-stage clean fixture passes
# ---------------------------------------------------------------------------


def test_multistage_clean_fixture_passes() -> None:
    """A second, independent, well-formed 'FROM <image> AS <name>' line does
    not trip the arity check, and a COPY --from=<stage> flag (unrelated to
    --chmod) does not trip the chmod check."""
    fixture_dir = _FIXTURE_ROOT / "multistage_clean"
    result = _lint.main(["--docker-dir", str(fixture_dir)])

    assert result == 0, (
        "lint_dockerfile_static.main() must return 0 against the "
        "multistage_clean/ fixture -- both FROM lines are well-formed and "
        "the COPY --from=<stage> flag is unrelated to --chmod."
    )


# ---------------------------------------------------------------------------
# Test 6: missing --docker-dir returns exit 2
# ---------------------------------------------------------------------------


def test_missing_docker_dir_returns_exit_2(tmp_path: Path) -> None:
    """A non-existent --docker-dir causes the lint to return 2 (usage error)."""
    nonexistent = tmp_path / "no_such_dir"
    result = _lint.main(["--docker-dir", str(nonexistent)])

    assert result == 2, (
        f"lint_dockerfile_static.main() must return 2 when --docker-dir "
        f"does not exist. Got {result!r}."
    )


# ---------------------------------------------------------------------------
# Test 7: check_from_arity / check_chmod_octal unit-level boundaries
# ---------------------------------------------------------------------------


class TestFromArityBoundaries:
    """Direct, in-memory exercise of check_from_arity's argument-count
    boundary -- independent of file I/O."""

    def test_single_argument_is_valid(self) -> None:
        findings = _lint.check_from_arity(
            Path("Dockerfile"), ["FROM rockylinux:9"]
        )
        assert findings == []

    def test_three_argument_as_form_is_valid(self) -> None:
        findings = _lint.check_from_arity(
            Path("Dockerfile"), ["FROM node:20-bookworm AS nodejs"]
        )
        assert findings == []

    def test_duplicated_keyword_two_args_is_invalid(self) -> None:
        findings = _lint.check_from_arity(
            Path("Dockerfile"),
            ["FROM FROM rockylinux:9"],
        )
        assert len(findings) == 1
        assert findings[0].kind == "from-arity"
        assert findings[0].line == 1

    def test_zero_arguments_is_invalid(self) -> None:
        findings = _lint.check_from_arity(Path("Dockerfile"), ["FROM"])
        assert len(findings) == 1

    def test_three_args_without_as_is_invalid(self) -> None:
        findings = _lint.check_from_arity(
            Path("Dockerfile"), ["FROM image extra garbage"]
        )
        assert len(findings) == 1

    def test_comment_line_is_ignored(self) -> None:
        findings = _lint.check_from_arity(
            Path("Dockerfile"), ["# FROM FROM broken:example"]
        )
        assert findings == []

    def test_case_insensitive_keyword(self) -> None:
        findings = _lint.check_from_arity(
            Path("Dockerfile"), ["from rockylinux:9"]
        )
        assert findings == []


class TestChmodOctalBoundaries:
    """Direct, in-memory exercise of check_chmod_octal's mode-validation
    boundary -- independent of file I/O."""

    @pytest.mark.parametrize("mode", ["755", "0755", "777", "0", "07"])
    def test_octal_modes_are_valid(self, mode: str) -> None:
        findings = _lint.check_chmod_octal(
            Path("Dockerfile"), [f"COPY --chmod={mode} src dest"]
        )
        assert findings == [], f"mode {mode!r} should be accepted as octal"

    @pytest.mark.parametrize("mode", ["+x", "u+x", "755x", "rwx", ""])
    def test_symbolic_modes_are_invalid(self, mode: str) -> None:
        findings = _lint.check_chmod_octal(
            Path("Dockerfile"), [f"COPY --chmod={mode} src dest"]
        )
        assert len(findings) == 1, f"mode {mode!r} should be rejected"
        assert findings[0].kind == "chmod-mode"

    def test_add_instruction_is_also_checked(self) -> None:
        findings = _lint.check_chmod_octal(
            Path("Dockerfile"), ["ADD --chmod=+x src dest"]
        )
        assert len(findings) == 1

    def test_unrelated_from_flag_is_ignored(self) -> None:
        findings = _lint.check_chmod_octal(
            Path("Dockerfile"), ["COPY --from=nodejs /a /b"]
        )
        assert findings == []

    def test_comment_line_is_ignored(self) -> None:
        findings = _lint.check_chmod_octal(
            Path("Dockerfile"), ["# COPY --chmod=+x src dest"]
        )
        assert findings == []
