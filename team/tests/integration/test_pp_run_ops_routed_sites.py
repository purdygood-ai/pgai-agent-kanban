"""
test_pp_run_ops_routed_sites.py
===============================
End-to-end proof, on an installed-layout kanban root, that the bash sites
which reach a ``pgai_agent_kanban`` module through the shared ``pp_run_ops``
helper resolve the package from the same root they always did and print the
same operator-facing text.

WHAT IS COVERED
---------------
* ``scripts/api-server.sh status`` probes the port ``load_api_config``
  resolves from the kanban root's ``kanban.cfg`` -- from a working directory
  outside the tree, with a decoy ``pgai_agent_kanban`` package first on the
  caller's ``PYTHONPATH`` that would answer a different port if it won.
* The ``api-server.port.unresolved`` refusal's remedy is a command an
  operator can paste: run as printed it reproduces the resolver's own error,
  and once the config is fixed it prints the port.
* ``scripts/dashboard/status-right.sh`` renders a project's version-pinned
  and event-pinned HALT-AFTER through ``dashboard_halt_describe_event``
  ("until vX.Y.Z" and the bare token), with the describer taken from the
  kanban root's own package even when a decoy sits on ``PYTHONPATH``.

The release-time HALT-AFTER spend narration is covered end-to-end by
``test_halt_after_rc_close_gate.py``, which drives ``cm/release.sh`` itself.

WHY AN INSTALLED LAYOUT
-----------------------
In a live install the scripts live at ``<root>/scripts`` and the package at
``<root>/pgai_agent_kanban``, so ``pp_run_ops``'s own-tree root is the kanban
root itself.  The fixture copies both trees into a synthetic root under the
framework temp space so the scripts run exactly as installed, never against
the dev tree and never against the operator's live install.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import narrate_binary_skip
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.fixtures.two_project import build_two_project_root

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_PKG_DIR = _TEAM_DIR / "pgai_agent_kanban"

# The port a decoy package on PYTHONPATH answers with.  Never a port the
# kanban root's own config names, so a probe of it proves the decoy won.
_DECOY_PORT = 1


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    """Render a subprocess's own words for an assertion message."""
    return (
        f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


def _build_installed_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a two-project kanban root carrying scripts/ and the package.

    Mirrors what install.sh places at a live root: ``scripts/`` and
    ``pgai_agent_kanban/`` side by side, so every script finds its own
    package at the root it is installed under.
    """
    root = build_two_project_root(tmp_path)
    ignore = shutil.ignore_patterns("__pycache__", "tests")
    shutil.copytree(_SCRIPTS_DIR, root / "scripts", ignore=ignore, dirs_exist_ok=True)
    shutil.copytree(_PKG_DIR, root / "pgai_agent_kanban", ignore=ignore)
    return root


def _build_decoy_package(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a directory holding a decoy ``pgai_agent_kanban`` package.

    Its ``api.config`` prints a port the real config never names, and its
    ``halt_after.describe`` prints a marker string, so output that carries
    either one proves the decoy -- not the kanban root's package -- was
    imported.
    """
    decoy = tmp_path / "decoy"
    for sub in ("pgai_agent_kanban", "pgai_agent_kanban/api", "pgai_agent_kanban/halt_after"):
        (decoy / sub).mkdir(parents=True, exist_ok=True)
        (decoy / sub / "__init__.py").write_text("", encoding="utf-8")
    (decoy / "pgai_agent_kanban" / "api" / "config.py").write_text(
        f"print({_DECOY_PORT})\n", encoding="utf-8"
    )
    (decoy / "pgai_agent_kanban" / "halt_after" / "describe.py").write_text(
        "import sys\nsys.stdout.write('answered-by-decoy')\n", encoding="utf-8"
    )
    return decoy


def _private_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a bin directory whose ``python3`` is this test's interpreter.

    The scrubbed PATH's system directories can hold an older ``python3``
    than the package supports; a private symlink in a directory holding
    nothing else always wins and never collides with one.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    link = bin_dir / "python3"
    if not link.exists():
        link.symlink_to(pathlib.Path(sys.executable).resolve())
    return bin_dir


def _port_probe_dirs() -> list:
    """Return the directories of the port probes api-server.sh uses.

    The script tries ss, then lsof; whichever this host has must resolve on
    the child's PATH too (ss commonly lives in an sbin directory the
    scrubbed PATH leaves out).
    """
    dirs = []
    for name in ("ss", "lsof"):
        found = shutil.which(name, path=os.pathsep.join(
            [os.environ.get("PATH", ""), "/usr/sbin", "/sbin"]))
        if found:
            dirs.append(str(pathlib.Path(found).parent))
    return dirs


def _env(root: pathlib.Path, tmp_path: pathlib.Path, **extra: str) -> dict:
    """Build an explicit environment pointing every root name at *root*."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp = tmp_path / "scratch"
    temp.mkdir(exist_ok=True)
    values = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp),
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    values.update(extra)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[_private_bin(tmp_path), *_port_probe_dirs()],
        extra_env=values,
    )


def _foreign_cwd(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a working directory that is neither the tree nor the root."""
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    return cwd


def _run(argv: list, *, env: dict, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def _set_api_port(root: pathlib.Path, port: int) -> None:
    with (root / "kanban.cfg").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[api]\nport = {port}\n")


# ---------------------------------------------------------------------------
# api-server.sh -- port resolution and its refusal remedy
# ---------------------------------------------------------------------------


def test_api_server_status_probes_the_kanban_cfg_port_despite_a_decoy_package(
    tmp_path: pathlib.Path,
) -> None:
    """status probes the kanban.cfg port, never the port a decoy would give.

    A plain listener holds the configured port, so status reports it as
    held by an unmanaged process and names the port it probed.
    """
    if not _port_probe_dirs():
        narrate_binary_skip("ss", "pp-run-ops-routed-sites (neither ss nor lsof)")
    root = _build_installed_root(tmp_path)
    decoy = _build_decoy_package(tmp_path)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        _set_api_port(root, port)

        result = _run(
            ["bash", str(root / "scripts" / "api-server.sh"), "status"],
            env=_env(root, tmp_path, PYTHONPATH=str(decoy)),
            cwd=_foreign_cwd(tmp_path),
        )

    assert f"port {port} held by PID {os.getpid()}" in result.stdout, _diagnostic(result)
    assert f"port {_DECOY_PORT} " not in result.stdout, _diagnostic(result)


def test_api_server_port_refusal_remedy_reruns_the_resolver(tmp_path: pathlib.Path) -> None:
    """The unresolved-port remedy, pasted as printed, runs the same resolver.

    With an unreadable kanban.cfg the remedy reproduces the loader's own
    error; with the config repaired the same command prints the port.
    """
    root = _build_installed_root(tmp_path)
    (root / "kanban.cfg").write_text("[api\nport = 8411\n", encoding="utf-8")
    env = _env(root, tmp_path)
    cwd = _foreign_cwd(tmp_path)

    refused = _run(["bash", str(root / "scripts" / "api-server.sh"), "status"], env=env, cwd=cwd)
    assert refused.returncode == 1, _diagnostic(refused)
    assert "api-server.port.unresolved" in refused.stderr, _diagnostic(refused)
    match = re.search(r"Run the resolver to see its error:\s+(.+)$", refused.stderr, re.MULTILINE)
    assert match, f"no runnable remedy in the refusal:\n{_diagnostic(refused)}"
    remedy = match.group(1).strip()

    rerun = _run(["bash", "-c", remedy], env=env, cwd=cwd)
    assert rerun.returncode != 0, _diagnostic(rerun)
    assert "MissingSectionHeaderError" in rerun.stderr, _diagnostic(rerun)

    (root / "kanban.cfg").write_text("[api]\nport = 8411\n", encoding="utf-8")
    repaired = _run(["bash", "-c", remedy], env=env, cwd=cwd)
    assert repaired.returncode == 0, _diagnostic(repaired)
    assert repaired.stdout == "8411\n", _diagnostic(repaired)


# ---------------------------------------------------------------------------
# status-right.sh -- HALT-AFTER description through the shared describer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "rendered"),
    [
        ("rc:v2.9.15", "HALT-AFTER:project_a until v2.9.15"),
        ("coder", "HALT-AFTER:project_a coder"),
    ],
)
def test_status_right_describes_halt_after_from_the_kanban_root_package(
    tmp_path: pathlib.Path, token: str, rendered: str
) -> None:
    """The bar renders the token through the root's own describer.

    A version-pinned token reads "until vX.Y.Z", an event token renders
    unchanged, and a decoy package on PYTHONPATH never answers.
    """
    root = _build_installed_root(tmp_path)
    (root / "projects" / "project_a" / "HALT-AFTER").write_text(f"{token}\n", encoding="utf-8")
    decoy = _build_decoy_package(tmp_path)

    result = _run(
        ["bash", str(root / "scripts" / "dashboard" / "status-right.sh"), str(root)],
        env=_env(root, tmp_path, PYTHONPATH=str(decoy)),
        cwd=_foreign_cwd(tmp_path),
    )

    assert result.returncode == 0, _diagnostic(result)
    assert rendered in result.stdout, _diagnostic(result)
    assert "answered-by-decoy" not in result.stdout, _diagnostic(result)
