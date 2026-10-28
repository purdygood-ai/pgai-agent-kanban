"""
test_lint_ap3_temp_root_literal.py
==================================
AP3 in team/scripts/lint_test_anti_patterns.py: the framework's default temp
root spelled out as a literal, anywhere but the files that define it.

Each case runs the real lint as a subprocess against a tree built under
pytest's ``tmp_path``:

  1. The literal in a Python module under a package-like directory is
     flagged by the package scan (``--package-dirs``), naming file and line.
  2. The same literal in the Python resolver module (``temp_root.py``) is
     not flagged -- permitted by filename, as temp.sh is.
  3. The same literal in ``temp.sh`` is not flagged by the runtime-script
     scan, while a sibling shell script carrying it is.
  4. An opt-out marker within five lines above the literal suppresses it;
     one six lines above does not.
  5. The whole-tree default (no ``--package-dirs``) scans the real
     ``team/pgai_agent_kanban/`` package, so the gated runner's plain
     invocation checks the package's Python sources; the default runtime
     scan (no ``--scripts-dirs``) covers ``team/verification/``.
  6. ``--package-dirs`` is a directory option: combining it with ``--files``
     is a usage error.
  7. The temp-root basename the AP2 self-exemption keys on comes from the
     temp root resolver: the override when set, kanban.cfg ``[paths]`` when
     it is not.

The literal is assembled from fragments (``"/tmp/" + "pgai_kanban_tmp"``),
mirroring team/tests/fixtures/fixture_hardcoded_tmp_and_mkdtemp.py, so no
line of THIS file contains it and the whole-tree scan stays clean.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from tests.fixtures.fixture_hardcoded_tmp_and_mkdtemp import make_hardcoded_literal_src
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_AP_LINT = _TEAM_DIR / "scripts" / "lint_test_anti_patterns.py"
_PACKAGE_DIR = _TEAM_DIR / "pgai_agent_kanban"

# The default temp root, assembled so this file never contains it whole.
_LITERAL = "/tmp/" + "pgai_kanban_tmp"

# make_hardcoded_literal_src() puts the literal on its last line.
_PY_LITERAL_SRC = make_hardcoded_literal_src()
_PY_LITERAL_LINENO = len(_PY_LITERAL_SRC.splitlines())

_SH_LITERAL_SRC = (
    "#!/usr/bin/env bash\n"
    "# Synthetic shell script carrying the temp-root literal.\n"
    f'root="{_LITERAL}"\n'
)
_SH_LITERAL_LINENO = 3


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _empty_tests_dir(tmp_path: Path) -> Path:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    return tests_dir


def _run_ap_lint(tmp_path: Path, *args: str):
    quoted = " ".join(shlex.quote(a) for a in args)
    return run_bash(tmp_path, f"python3 {shlex.quote(str(_AP_LINT))} {quoted}")


def _run_package_scan(tmp_path: Path, package_dir: Path):
    """Whole-tree mode over an empty test tree, no runtime scripts, one package dir."""
    return _run_ap_lint(
        tmp_path,
        "--tests-dir",
        str(_empty_tests_dir(tmp_path)),
        "--scripts-dirs",
        "--package-dirs",
        str(package_dir),
    )


def _ap3_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if "[AP3]" in line]


# ---------------------------------------------------------------------------
# 1-2. Package scan: flagged in an ordinary module, permitted in the resolver
# ---------------------------------------------------------------------------


def test_literal_in_package_module_is_flagged(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg" / "pgai_agent_kanban"
    module = _write(package_dir / "ops" / "scratch_paths.py", _PY_LITERAL_SRC)

    result = _run_package_scan(tmp_path, package_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{module}:{_PY_LITERAL_LINENO}: [AP3]" in result.stdout, result.stdout
    assert len(_ap3_lines(result.stdout)) == 1, result.stdout


def test_literal_in_resolver_module_is_not_flagged(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg" / "pgai_agent_kanban"
    resolver = _write(package_dir / "lib" / "temp_root.py", _PY_LITERAL_SRC)

    result = _run_package_scan(tmp_path, package_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _ap3_lines(result.stdout) == [], result.stdout
    assert str(resolver) not in result.stdout
    assert "(0 test, 0 runtime, 1 package)" in result.stdout, result.stdout


def test_resolver_permission_is_by_filename_not_by_directory(tmp_path: Path) -> None:
    """A sibling of the resolver in the same lib/ directory is still flagged."""
    package_dir = tmp_path / "pkg" / "pgai_agent_kanban"
    _write(package_dir / "lib" / "temp_root.py", _PY_LITERAL_SRC)
    sibling = _write(package_dir / "lib" / "temp_root_helpers.py", _PY_LITERAL_SRC)

    result = _run_package_scan(tmp_path, package_dir)

    ap3 = _ap3_lines(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(ap3) == 1, result.stdout
    assert ap3[0].startswith(f"{sibling}:{_PY_LITERAL_LINENO}: [AP3]"), result.stdout


# ---------------------------------------------------------------------------
# 3. Runtime-script scan: temp.sh is permitted, any other script is not
# ---------------------------------------------------------------------------


def test_literal_in_temp_sh_is_not_flagged_but_sibling_script_is(tmp_path: Path) -> None:
    scripts_lib = tmp_path / "scripts" / "lib"
    temp_sh = _write(scripts_lib / "temp.sh", _SH_LITERAL_SRC)
    other_sh = _write(scripts_lib / "scratch_paths.sh", _SH_LITERAL_SRC)

    result = _run_ap_lint(
        tmp_path,
        "--tests-dir",
        str(_empty_tests_dir(tmp_path)),
        "--scripts-dirs",
        str(scripts_lib),
        "--package-dirs",
    )

    # The runtime scan runs AP2 on these scripts as well; only AP3 is the
    # subject here, so only [AP3] lines are inspected.
    ap3 = _ap3_lines(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(ap3) == 1, result.stdout
    assert ap3[0].startswith(f"{other_sh}:{_SH_LITERAL_LINENO}: [AP3]"), result.stdout
    assert not any(line.startswith(f"{temp_sh}:") for line in ap3), result.stdout


# ---------------------------------------------------------------------------
# 4. The opt-out marker window
# ---------------------------------------------------------------------------


def _module_with_marker_above(gap_lines: int) -> str:
    """A module whose literal sits *gap_lines* lines below an opt-out marker."""
    return (
        "# anti-pattern-allowlist: 2 (justification: inert example text)\n"
        + "".join(f"_filler_{i} = {i}\n" for i in range(gap_lines - 1))
        + f'EXAMPLE = "{_LITERAL}"\n'
    )


def test_opt_out_marker_within_five_lines_suppresses_the_finding(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg" / "pgai_agent_kanban"
    _write(package_dir / "near_marker.py", _module_with_marker_above(5))

    result = _run_package_scan(tmp_path, package_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _ap3_lines(result.stdout) == [], result.stdout


def test_opt_out_marker_six_lines_above_does_not_suppress(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg" / "pgai_agent_kanban"
    module = _write(package_dir / "far_marker.py", _module_with_marker_above(6))

    result = _run_package_scan(tmp_path, package_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{module}:7: [AP3]" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# 5-6. Default package directory and the --files exclusivity rule
# ---------------------------------------------------------------------------


def test_default_scan_covers_the_team_package(tmp_path: Path) -> None:
    """Without --package-dirs the lint scans team/pgai_agent_kanban/ itself.

    Asserts only that the scan reached the package -- not that the real
    package is finding-free, which is the quality-class lint's own verdict.
    """
    result = _run_ap_lint(
        tmp_path, "--tests-dir", str(_empty_tests_dir(tmp_path)), "--scripts-dirs"
    )

    assert (
        f"lint_test_anti_patterns: scanning package modules (AP3) in {_PACKAGE_DIR}"
        in result.stdout
    ), result.stdout + result.stderr


def test_default_runtime_scan_covers_team_verification(tmp_path: Path) -> None:
    """Without --scripts-dirs the runtime scan reaches team/verification/.

    Asserts only that the scan reached the directory, as the package case
    above does.
    """
    result = _run_ap_lint(
        tmp_path, "--tests-dir", str(_empty_tests_dir(tmp_path)), "--package-dirs"
    )

    scanned = [
        line for line in result.stdout.splitlines()
        if line.startswith("lint_test_anti_patterns: scanning runtime scripts in ")
    ]
    assert len(scanned) == 1, result.stdout + result.stderr
    assert str(_TEAM_DIR / "verification") in scanned[0].split(" in ", 1)[1].split(", ")


def test_files_with_package_dirs_is_a_usage_error(tmp_path: Path) -> None:
    named = _write(tmp_path / "tests" / "test_clean.py", "def test_ok():\n    assert True\n")

    result = _run_ap_lint(
        tmp_path, "--files", str(named), "--package-dirs", str(tmp_path / "pkg")
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "--files cannot be combined" in result.stderr


# ---------------------------------------------------------------------------
# 7. The AP2 self-exemption basename comes from the temp root resolver
# ---------------------------------------------------------------------------

_PRINT_BASENAME = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('ap_lint', sys.argv[1])\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(mod)\n"
    "print(mod._TEMP_ROOT_BASENAME)\n"
)


def _lint_temp_root_basename(tmp_path: Path, extra_env: dict) -> str:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-c", _PRINT_BASENAME, str(_AP_LINT)],
        env=build_scrubbed_env(home_dir=home, extra_env=extra_env),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_temp_root_basename_follows_the_override(tmp_path: Path) -> None:
    override = tmp_path / "scratch" / "operator_temp_root"

    basename = _lint_temp_root_basename(
        tmp_path, {"PGAI_AGENT_KANBAN_TEMP_DIR": str(override)}
    )

    assert basename == "operator_temp_root"


def test_temp_root_basename_follows_kanban_cfg_paths_without_override(
    tmp_path: Path,
) -> None:
    kanban_root = tmp_path / "kanban"
    _write(
        kanban_root / "kanban.cfg",
        "[paths]\n"
        f"tmp_root = {tmp_path / 'scratch'}\n"
        "tmp_subdir = configured_temp_root\n",
    )

    basename = _lint_temp_root_basename(
        tmp_path, {"PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root)}
    )

    assert basename == "configured_temp_root"


# ---------------------------------------------------------------------------
# The bash resolver and the provider invoker need no filename permission
# ---------------------------------------------------------------------------


def test_temp_sh_and_wake_driver_invoke_are_ap3_clean_without_the_whitelist(tmp_path: Path) -> None:
    """temp.sh names its last-resort root through its per-key defaults and
    wake_driver_invoke.sh asks the resolver, so copies under names the
    filename whitelist does not know carry no AP3 finding."""
    scripts = tmp_path / "scripts"
    for source, name in (
        (_TEAM_DIR / "scripts" / "lib" / "temp.sh", "temp_resolver_copy.sh"),
        (_TEAM_DIR / "scripts" / "lib" / "wake_driver_invoke.sh", "wake_driver_invoke_copy.sh"),
    ):
        _write(scripts / name, source.read_text(encoding="utf-8"))
    result = _run_ap_lint(
        tmp_path, "--tests-dir", str(_empty_tests_dir(tmp_path)), "--scripts-dirs", str(scripts),
        "--package-dirs",
    )
    assert _ap3_lines(result.stdout) == [], result.stdout


def test_temp_sh_last_resort_root_is_the_joined_per_key_defaults(tmp_path: Path) -> None:
    """With no override and no readable kanban.cfg the bash resolver still
    answers the documented last-resort root."""
    temp_sh = _TEAM_DIR / "scripts" / "lib" / "temp.sh"
    result = run_bash(
        tmp_path,
        f'source {shlex.quote(str(temp_sh))}\n'
        f'printf "%s\\n" "$_PGAI_TEMP_FALLBACK_ROOT" "$(pgai_configured_temp_root /nonexistent/kanban.cfg)"\n',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [_LITERAL, _LITERAL]
