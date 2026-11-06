"""
test_lint_fixed_sleep_and_silent_handler.py
===========================================
Two checks in team/scripts/lint_test_anti_patterns.py:

AP9 -- a fixed sleep over 10 seconds in a test:
  1. ``time.sleep(60)`` and a sleep on a module-level constant bound to 60
     are flagged at their lines; ``time.sleep(10)``, a sleep on a computed
     value and a ``sleep 600`` written inside a string are not.
  2. The opt-out marker within five lines above suppresses it.

AP10 -- a silent broad exception handler in a package module:
  3. ``except Exception: pass``, a bare ``except: continue`` and one whose
     only comment is ``# noqa: BLE001`` are flagged; a handler with a
     justification comment, a narrow ``except OSError: pass`` and one that
     logs are not.
  4. A test module inside the package (under ``tests/``) is not checked.
  5. The whole-tree run over the real package reports no AP10 finding in
     cm/unwind.py or dashboard/scan_attention.py.

Each case runs the real lint as a subprocess against a tree built under
pytest's ``tmp_path``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = Path(__file__).resolve().parents[2]
_AP_LINT = _TEAM_DIR / "scripts" / "lint_test_anti_patterns.py"

_SLEEPY_TEST = """\
import time

_WINDOW_SECONDS = 60
_SHORT = 10


def test_waits_a_minute():
    time.sleep(60)


def test_waits_on_a_constant():
    time.sleep(_WINDOW_SECONDS)


def test_short_and_computed_waits_are_fine():
    time.sleep(_SHORT)
    time.sleep(10)
    time.sleep(_WINDOW_SECONDS / 30)
    child = "import time; time.sleep(600)"
    shell = "sleep 600 &"
    assert child and shell


def test_opted_out():
    # anti-pattern-allowlist: 9 (justification: measures a full minute window)
    time.sleep(61)
"""

_SILENT_MODULE = """\
import logging


def a():
    try:
        return 1
    except Exception:
        pass


def b(items):
    for item in items:
        try:
            item()
        except:
            continue


def c():
    try:
        return 1
    except Exception:  # noqa: BLE001
        pass


def fine():
    try:
        return 1
    except Exception:  # noqa: BLE001 -- a closed stderr must not fail the caller
        pass
    try:
        return 2
    except OSError:
        pass
    try:
        return 3
    except Exception as exc:
        logging.warning("failed: %s", exc)
"""


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_AP_LINT), *args],
        env=build_scrubbed_env(home_dir=tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(tmp_path),
        check=False,
    )


def _finding_lines(output: str, code: str) -> list[tuple[str, int]]:
    found = []
    for line in output.splitlines():
        marker = f": [{code}] "
        if marker in line:
            head = line.split(marker, 1)[0]
            path, _sep, lineno = head.rpartition(":")
            found.append((Path(path).name, int(lineno)))
    return found


def _line_of(text: str, needle: str, occurrence: int = 1) -> int:
    seen = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            seen += 1
            if seen == occurrence:
                return number
    raise AssertionError(f"{needle!r} not in source")


def test_ap9_flags_fixed_sleeps_over_ten_seconds_only(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sleepy.py"
    test_file.write_text(_SLEEPY_TEST, encoding="utf-8")

    result = _run(tmp_path, "--files", str(test_file))

    assert result.returncode == 0, result.stdout + result.stderr
    assert _finding_lines(result.stdout, "AP9") == [
        ("test_sleepy.py", _line_of(_SLEEPY_TEST, "time.sleep(60)")),
        ("test_sleepy.py", _line_of(_SLEEPY_TEST, "time.sleep(_WINDOW_SECONDS)")),
    ], result.stdout


def test_ap10_flags_unexplained_broad_handlers_in_package_modules(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "tests").mkdir(parents=True)
    (package / "mod.py").write_text(_SILENT_MODULE, encoding="utf-8")
    (package / "tests" / "test_mod.py").write_text(_SILENT_MODULE, encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    result = _run(tmp_path, "--tests-dir", str(tests_dir), "--scripts-dirs", "--package-dirs", str(package))

    assert result.returncode == 0, result.stdout + result.stderr
    assert _finding_lines(result.stdout, "AP10") == [
        ("mod.py", _line_of(_SILENT_MODULE, "except Exception:", 1)),
        ("mod.py", _line_of(_SILENT_MODULE, "except:")),
        ("mod.py", _line_of(_SILENT_MODULE, "except Exception:  # noqa: BLE001", 1)),
    ], result.stdout


def test_the_real_package_has_no_silent_handler_in_unwind_or_attention(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    result = _run(
        tmp_path,
        "--tests-dir",
        str(tests_dir),
        "--scripts-dirs",
        "--package-dirs",
        str(_TEAM_DIR / "pgai_agent_kanban"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    flagged = {name for name, _line in _finding_lines(result.stdout, "AP10")}
    assert "unwind.py" not in flagged and "scan_attention.py" not in flagged, result.stdout
