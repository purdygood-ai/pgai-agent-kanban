"""
test_install_wake_tier_none.py
================================
Behavioral tests for ``install.sh --wake-tier none``: the value is accepted,
it installs no OS crontab (exactly as ``--no-system-cron`` does), and
``--help`` lists it.

WHAT IS TESTED
--------------
1. **``--wake-tier none`` installs no OS crontab.** A real, non-interactive
   install against a throwaway root exits 0, takes the ``--no-system-cron``
   branch of the crontab hook, and never runs ``crontab`` at all -- a
   recording stub placed first on PATH is never invoked, so there is no
   write and no probe. The pseudocron config keeps the default tier
   (``small``), since ``none`` names no cadence.

2. **``--help`` lists ``none`` for ``--wake-tier``.**

SANDBOX DESIGN
--------------
install.sh runs from this checkout against a throwaway root under pytest's
``tmp_path``, with every live-install name handled: ``HOME``, ``KANBAN_ROOT``,
``PGAI_AGENT_KANBAN_ROOT_PATH`` and ``PGAI_AGENT_KANBAN_TEMP_DIR`` point
into the sandbox, and the environment is built from an explicit minimal set
(``build_scrubbed_env``), so ``CLAUDE_CONFIG_DIR``, ``PGAI_PROJECT_NAME``,
``PGAI_TASKS_DIR`` and ``PGAI_PROJECT_ROOT`` are absent. ``--stamp-version``
avoids a git tag resolution call. The ``crontab`` stub is the only thing
the test puts on PATH ahead of the system tools; it records each call to a
file and exits 0, so even a stray call could not reach the host crontab.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_REAL_INSTALL_SH = _REPO_ROOT / "install.sh"

_TIMEOUT = 180

pytestmark = pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)


def _crontab_stub(bin_dir: pathlib.Path, calls_log: pathlib.Path) -> None:
    """Write a ``crontab`` stub that appends its argv to *calls_log*."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "crontab"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{calls_log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


def _sandbox_env(tmp_path: pathlib.Path, root: pathlib.Path, bin_dir: pathlib.Path) -> dict:
    """The sandbox environment: every live-install name points into tmp_path."""
    home = tmp_path / "home"
    temp = tmp_path / "pgai_temp"
    home.mkdir()
    temp.mkdir()
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[bin_dir],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp),
        },
    )


def _run_install(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_REAL_INSTALL_SH), *argv],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
        cwd=str(_REPO_ROOT),
        stdin=subprocess.DEVNULL,
    )


def test_wake_tier_none_installs_no_os_crontab(tmp_path: pathlib.Path) -> None:
    """``--wake-tier none`` completes a real install with no crontab call."""
    root = tmp_path / "kanban_root"
    bin_dir = tmp_path / "stub_bin"
    calls_log = tmp_path / "crontab_calls.log"
    _crontab_stub(bin_dir, calls_log)
    env = _sandbox_env(tmp_path, root, bin_dir)

    result = _run_install(
        ["--force", "--wake-tier", "none", "--stamp-version", "v0.0.0-sandbox-test"], env
    )
    output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, f"install.sh --wake-tier none failed:\n{output}"
    assert "Invalid wake tier" not in output, output
    assert "Crontab: skipped — --no-system-cron or --wake-tier none set" in output, (
        f"the crontab hook did not take the no-OS-crontab branch:\n{output}"
    )
    assert not calls_log.exists(), (
        "crontab was invoked during an install with --wake-tier none:\n"
        f"{calls_log.read_text(encoding='utf-8')}"
    )
    assert "--wake-tier=small" in output, (
        f"the pseudocron hook should keep the default tier (small):\n{output}"
    )
    assert "--wake-tier=none" not in output, output


def test_help_lists_none_for_wake_tier(tmp_path: pathlib.Path) -> None:
    """``install.sh --help`` names ``none`` as a ``--wake-tier`` value."""
    root = tmp_path / "kanban_root"
    bin_dir = tmp_path / "stub_bin"
    _crontab_stub(bin_dir, tmp_path / "crontab_calls.log")
    env = _sandbox_env(tmp_path, root, bin_dir)

    result = _run_install(["--help"], env)

    assert result.returncode == 0, result.stderr
    assert "--wake-tier small|medium|large|none" in result.stdout, result.stdout
    assert "small (default), medium, large, or none" in result.stdout, result.stdout
