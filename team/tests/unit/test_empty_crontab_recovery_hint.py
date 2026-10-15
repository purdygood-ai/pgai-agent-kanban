"""
test_empty_crontab_recovery_hint.py
===================================
When the crontab is empty after an install, ``warn_if_empty_crontab``
(team/scripts/lib/safe_overwrite.sh) prints a recovery command that renders
the tier template before installing it -- the install's own
``install-crontab.sh --wake-tier=<tier> --yes`` -- and never the raw
``crontab.example``, whose ``__KANBAN_ROOT__`` placeholders would make every
installed line point at a path that does not exist.

The crontab binary is a fake shim reached through ``PGAI_CRONTAB_CMD`` (the
library's single chokepoint), so the operator's crontab is never read or
written.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_SAFE_OVERWRITE = _TEAM_DIR / "scripts" / "lib" / "safe_overwrite.sh"


def _shim(tmp_path: pathlib.Path, listing: str) -> pathlib.Path:
    """A fake crontab: ``-l`` prints *listing*; an install records nothing."""
    shim = tmp_path / "fake-crontab"
    shim.write_text(
        "#!/bin/bash\n"
        'if [[ "${1:-}" == "-l" ]]; then\n'
        f"  printf '%s' {listing!r}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def _warn(tmp_path: pathlib.Path, listing: str, *args: str) -> subprocess.CompletedProcess:
    root = tmp_path / "kanban root"
    root.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("KANBAN_ROOT",)}
    env.update(
        PGAI_CRONTAB_CMD=str(_shim(tmp_path, listing)),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
        HOME=str(tmp_path / "home"),
    )
    return subprocess.run(
        ["bash", "-c", f'source "{_SAFE_OVERWRITE}"; warn_if_empty_crontab "$@"', "warn", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_empty_crontab_recovery_names_install_crontab_not_the_raw_template(tmp_path: pathlib.Path) -> None:
    result = _warn(tmp_path, "")

    assert result.returncode == 1, result
    assert "crontab is empty after install" in result.stdout
    assert "crontab.example" not in result.stdout, (
        f"the raw template still carries placeholders and must not be offered\n{result.stdout}"
    )
    root = tmp_path / "kanban root"
    recovery = [line.strip() for line in result.stdout.splitlines() if "install-crontab.sh" in line]
    assert recovery == [f"{root}/scripts/install-crontab.sh --wake-tier=small --yes".replace(" root", "\\ root", 1)], (
        f"the recovery command must be the install's own install-crontab.sh, shell-quoted\n{result.stdout}"
    )


def test_recovery_command_names_the_tier_and_root_it_is_given(tmp_path: pathlib.Path) -> None:
    other_root = tmp_path / "other"
    result = _warn(tmp_path, "", "large", str(other_root))

    assert result.returncode == 1, result
    assert f"{other_root}/scripts/install-crontab.sh --wake-tier=large --yes" in result.stdout, result.stdout


def test_non_empty_crontab_prints_no_warning(tmp_path: pathlib.Path) -> None:
    result = _warn(tmp_path, "*/5 * * * * /bin/true\n")

    assert result.returncode == 0, result
    assert result.stdout == ""
