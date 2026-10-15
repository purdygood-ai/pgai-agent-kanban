"""
test_export_kanban_config_archive_mode.py
=========================================
``export-kanban-config.sh`` writes its archive mode 0600 whatever the
caller's umask, so an export carrying the ``secrets`` file (which is 0600
on disk) is never readable by other local users.

Covered, against the real script run as a subprocess under ``umask 022``
with a sandbox kanban root (every root variable pointed into tmp_path,
environment built from an explicit set by ``build_scrubbed_env``):

  * ``--include-secrets``: the archive is mode 0600 and does carry
    ``secrets``;
  * without it: the archive is mode 0600 (neither group- nor
    world-readable) and carries no ``secrets``;
  * an existing world-readable file at ``--file`` is replaced by a 0600
    archive rather than rewritten in place at its old mode;
  * the caller's own umask is left as it was.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import stat
import subprocess
import sys
import tarfile

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_EXPORT_SH = _TEAM_DIR / "scripts" / "export-kanban-config.sh"


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "kanban.cfg").write_text("[wake]\nmax_tasks_per_wake = 1\n", encoding="utf-8")
    (root / "projects.cfg").write_text("", encoding="utf-8")
    secrets = root / "secrets"
    secrets.write_text("EXAMPLE_TOKEN=not-a-real-value\n", encoding="utf-8")
    secrets.chmod(0o600)
    return root


def _export(tmp_path: pathlib.Path, root: pathlib.Path, out: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
        },
    )
    command = " ".join(shlex.quote(a) for a in (str(_EXPORT_SH), "--file", str(out), *args))
    return subprocess.run(
        ["bash", "-c", f"umask 022; {command}; rc=$?; echo \"UMASK_AFTER=$(umask)\"; exit $rc"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120, stdin=subprocess.DEVNULL,
    )


def _mode(path: pathlib.Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _members(path: pathlib.Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        return {pathlib.PurePosixPath(m.name).name for m in archive.getmembers()}


def test_export_with_secrets_is_owner_only(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    out = tmp_path / "out" / "with-secrets.tar.gz"
    out.parent.mkdir()

    result = _export(tmp_path, root, out, "--include-secrets")

    assert result.returncode == 0, result.stderr
    assert _mode(out) == 0o600, f"archive mode is {oct(_mode(out))}"
    assert "secrets" in _members(out)
    assert "UMASK_AFTER=0022" in result.stdout, "the caller's umask must be left as it was"


def test_export_without_secrets_is_not_group_or_world_readable(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    out = tmp_path / "out" / "plain.tar.gz"
    out.parent.mkdir()

    result = _export(tmp_path, root, out)

    assert result.returncode == 0, result.stderr
    assert _mode(out) & 0o077 == 0, f"archive mode is {oct(_mode(out))}"
    assert _mode(out) == 0o600
    assert "secrets" not in _members(out)


def test_existing_world_readable_target_is_replaced_owner_only(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    out = tmp_path / "out" / "reused.tar.gz"
    out.parent.mkdir()
    out.write_text("an older export\n", encoding="utf-8")
    out.chmod(0o644)

    result = _export(tmp_path, root, out, "--include-secrets")

    assert result.returncode == 0, result.stderr
    assert _mode(out) == 0o600, f"archive mode is {oct(_mode(out))}"
    assert "secrets" in _members(out)
