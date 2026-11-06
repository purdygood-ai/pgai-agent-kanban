"""
failing_intake.py -- an intake.sh that always exits 1, for OVERWATCH deposit tests.

overwatch_deposit_bug (team/scripts/lib/overwatch_config.sh) runs the
intake.sh one level above the lib directory its _OVERWATCH_CONFIG_LIB_DIR
names. :func:`failing_intake` builds, under the test's tmp_path, a lib
directory that is a symlink to this tree's scripts/lib and, beside it, an
intake.sh that appends its arguments to a calls log and exits 1. A bash test
puts :attr:`FailingIntake.shell_override` after it sources the check, so the
real deposit path runs -- the draft is staged, intake is invoked -- and
intake fails, without editing any installed or checked-in intake.sh.

Usage:
    stub = failing_intake(tmp_path)
    script = f"source {check}\\n{stub.shell_override}\\noverwatch_check_x\\n"
    ...
    assert stub.calls() == 1
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

_SCRIPTS_LIB = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lib"


@dataclass(frozen=True)
class FailingIntake:
    """Where the stub lives and how a bash test points the deposit at it."""

    lib_dir: pathlib.Path
    calls_log: pathlib.Path

    @property
    def shell_override(self) -> str:
        """The bash line that points overwatch_deposit_bug at the stub intake."""
        return f'_OVERWATCH_CONFIG_LIB_DIR="{self.lib_dir}"'

    def calls(self) -> int:
        """How many times the stub intake has been run."""
        if not self.calls_log.exists():
            return 0
        return len(self.calls_log.read_text(encoding="utf-8").splitlines())


def failing_intake(tmp_path: pathlib.Path) -> FailingIntake:
    """Build the stub under *tmp_path*/failing-intake and return it."""
    base = tmp_path / "failing-intake"
    base.mkdir(parents=True, exist_ok=True)
    lib_dir = base / "lib"
    if not lib_dir.exists():
        lib_dir.symlink_to(_SCRIPTS_LIB, target_is_directory=True)
    calls_log = base / "intake-calls.log"
    intake = base / "intake.sh"
    intake.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> '{calls_log}'\n"
        "echo 'intake.sh: deposit failed (test stub exits 1)' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    intake.chmod(0o755)
    return FailingIntake(lib_dir=lib_dir, calls_log=calls_log)
