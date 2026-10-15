"""
upgrade_sandbox.py
==================
Test helper: a throwaway kanban root, a fake crontab on PATH, and a
process-group-owned runner for the scripts that write scheduler state --
team/scripts/upgrade.sh (its phase-2 body), install-crontab.sh and
install-pseudocron.sh.

Every path this helper writes is under the *parent* directory the caller
passes (pytest's ``tmp_path``). The environment it builds is constructed from
an explicit set (``build_scrubbed_env``): HOME is a directory inside the
sandbox, the kanban root and temp dir point into the sandbox, and
CLAUDE_CONFIG_DIR, PGAI_PROJECT_NAME, PGAI_PROJECT_ROOT and PGAI_TASKS_DIR are
never set. The per-user crontab is a file inside the sandbox, read and
written through a shim that is first on PATH and is also named by
PGAI_CRONTAB_CMD, so neither the real crontab binary nor the operator's
crontab is ever reached.

The child runs in its own session (``start_new_session=True``), so it has no
controlling terminal: opening /dev/tty fails exactly as it does under cron or
an agent wake, which is the "no TTY" case upgrade.sh's deposit prompt
handles. On timeout the whole process group is killed and the timeout is
re-raised.

HOW TO USE
----------
::

    from tests.fixtures.upgrade_sandbox import UpgradeSandbox, diagnostic

    sandbox = UpgradeSandbox(tmp_path)
    sandbox.write_crontab("15 3 * * * /usr/local/bin/nightly-backup.sh\\n")
    result = sandbox.run_upgrade_phase2()
    assert result.returncode == 0, diagnostic(result)
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
from typing import Iterable, Optional

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
REPO_ROOT = TEAM_DIR.parent
UPGRADE_SCRIPT = TEAM_DIR / "scripts" / "upgrade.sh"
INSTALL_CRONTAB_SCRIPT = TEAM_DIR / "scripts" / "install-crontab.sh"
INSTALL_PSEUDOCRON_SCRIPT = TEAM_DIR / "scripts" / "install-pseudocron.sh"
PSEUDOCRON_TEMPLATES_DIR = TEAM_DIR / "templates" / "install"

# The shim keeps the whole per-user crontab in one file under the sandbox.
# It implements only the forms the scripts under test use: -l, -r, "-"
# (stdin) and a file argument.
_CRONTAB_SHIM = """\
#!/usr/bin/env bash
# Fake crontab for tests: the crontab is the file named by FAKE_CRONTAB_FILE.
CRONFILE="${FAKE_CRONTAB_FILE:?FAKE_CRONTAB_FILE must be set}"
case "${1:-}" in
  -l)
    if [[ ! -f "$CRONFILE" ]]; then
      printf 'no crontab for sandbox\\n' >&2
      exit 1
    fi
    cat "$CRONFILE"
    ;;
  -r) rm -f "$CRONFILE" ;;
  -) cat > "$CRONFILE" ;;
  "")
    printf 'fake crontab: no arguments given\\n' >&2
    exit 1
    ;;
  -*)
    printf 'fake crontab: unsupported flag: %s\\n' "$1" >&2
    exit 1
    ;;
  *) cp "$1" "$CRONFILE" ;;
esac
"""


def diagnostic(result: subprocess.CompletedProcess) -> str:
    """Render a subprocess result's own words for an assertion message."""
    return (
        f"exit code: {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


class UpgradeSandbox:
    """A throwaway kanban root plus HOME, temp dir and fake crontab.

    ``root`` starts with kanban.cfg, projects.cfg and one registered project
    (``project_name``) whose project.cfg lacks the key the first shipped
    migration seeds, so a test can tell from the file whether migrations ran.
    Nothing else is pre-populated: upgrade.sh's own deposit fills the root.
    """

    PROJECT_CFG = (
        "[project]\n"
        "project_name = {name}\n"
        "workflow_type = release\n"
        "\n"
        "[release]\n"
        "some_other_key = value\n"
    )

    def __init__(self, parent: pathlib.Path, project_name: str = "sandbox-proj") -> None:
        self.parent = pathlib.Path(parent)
        self.project_name = project_name
        self.root = self.parent / "kanban_root"
        self.home = self.parent / "home"
        self.temp_dir = self.parent / "kanban_tmp"
        self.shim_dir = self.parent / "shim_bin"
        self.crontab_file = self.home / ".fake_crontab"
        self.cwd = self.parent / "elsewhere"
        for directory in (self.root, self.home, self.temp_dir, self.shim_dir, self.cwd):
            directory.mkdir(parents=True, exist_ok=True)

        shim = self.shim_dir / "crontab"
        shim.write_text(_CRONTAB_SHIM, encoding="utf-8")
        shim.chmod(0o755)
        self.crontab_shim = shim

        (self.root / "kanban.cfg").write_text("[paths]\n\n[release]\n", encoding="utf-8")
        (self.root / "projects.cfg").write_text(
            f"[project:{project_name}]\npriority=1\n", encoding="utf-8"
        )
        self.project_cfg.parent.mkdir(parents=True, exist_ok=True)
        self.project_cfg.write_text(
            self.PROJECT_CFG.format(name=project_name), encoding="utf-8"
        )

    # -- paths ---------------------------------------------------------------

    @property
    def project_cfg(self) -> pathlib.Path:
        return self.root / "projects" / self.project_name / "project.cfg"

    @property
    def version_file(self) -> pathlib.Path:
        return self.root / "VERSION"

    @property
    def pseudocron_cfg(self) -> pathlib.Path:
        return self.root / "pseudocron.cfg"

    # -- crontab -------------------------------------------------------------

    def write_crontab(self, text: str) -> None:
        self.crontab_file.write_text(text, encoding="utf-8")

    def crontab_bytes(self) -> Optional[bytes]:
        """The fake crontab's bytes, or None when no crontab is installed."""
        if not self.crontab_file.exists():
            return None
        return self.crontab_file.read_bytes()

    # -- running -------------------------------------------------------------

    def env(self, extra_env: Optional[dict] = None) -> dict:
        values = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
            "PGAI_CRONTAB_CMD": str(self.crontab_shim),
            "FAKE_CRONTAB_FILE": str(self.crontab_file),
        }
        if extra_env:
            values.update(extra_env)
        return build_scrubbed_env(
            home_dir=self.home, extra_path_dirs=[self.shim_dir], extra_env=values
        )

    def run(self, argv: Iterable[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
        """Run *argv* in its own session from a cwd outside the repository."""
        argv = [str(a) for a in argv]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env(),
            cwd=str(self.cwd),
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)

    def run_upgrade_phase2(self, *extra_args: str, timeout: int = 300) -> subprocess.CompletedProcess:
        """Run upgrade.sh's phase-2 body (deposit, hooks, stamp, migrations)
        from this tree onto the sandbox root -- the invocation phase 1 execs
        into on an operator run, minus phase 1's backup and bootstrap probes."""
        return self.run(
            [
                "bash",
                UPGRADE_SCRIPT,
                "--phase2",
                "--phase2-protocol",
                "1",
                "--kanban-root",
                self.root,
                "--no-backup",
                *extra_args,
            ],
            timeout=timeout,
        )
