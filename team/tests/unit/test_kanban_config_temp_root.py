"""
test_kanban_config_temp_root.py
===============================
``export-kanban-config.sh`` and ``import-kanban-config.sh`` stage their
archive work under the framework temp root that ``lib/temp.sh``
(``pgai_temp_subdir``) resolves, never under a root of their own.

Covered, against the real scripts run as subprocesses from a foreign cwd
with a sandbox kanban root and an environment built by
``build_scrubbed_env``. A ``tar`` shim placed first on PATH records the
directory each script hands to ``tar -C`` (its staging directory) and then
runs the real binary, so the assertions read where the staging actually
happened rather than inferring it:

  * with PGAI_AGENT_KANBAN_TEMP_DIR set to a sandbox path, export and
    import each stage under ``<sandbox>/<script-name>/`` and leave nothing
    under an unrelated TMPDIR;
  * with PGAI_AGENT_KANBAN_TEMP_DIR unset, the root comes from the sandbox
    kanban.cfg ``[paths]`` ``tmp_root``/``tmp_subdir`` -- TMPDIR is not
    consulted;
  * a copy of either script whose ``lib/`` has no ``temp.sh`` refuses
    dual-channel, naming the missing path, and writes no archive.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_EXPORT_SH = _SCRIPTS_DIR / "export-kanban-config.sh"
_IMPORT_SH = _SCRIPTS_DIR / "import-kanban-config.sh"
_TIMEOUT_SECONDS = 120


@pytest.fixture(autouse=True)
def _require_tar() -> None:
    require_binary("tar", label="kanban-config-temp-root")


def _kanban_root(tmp_path: pathlib.Path, name: str, cfg: str = "[wake]\nmax_tasks_per_wake = 1\n") -> pathlib.Path:
    root = tmp_path / name
    root.mkdir()
    (root / "kanban.cfg").write_text(cfg, encoding="utf-8")
    (root / "projects.cfg").write_text("", encoding="utf-8")
    return root


def _tar_shim(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A ``tar`` ahead of the real one that logs its ``-C`` directory."""
    real_tar = shutil.which("tar")
    assert real_tar, "tar must be on PATH (guarded by _require_tar)"
    bin_dir = tmp_path / "shim_bin"
    bin_dir.mkdir()
    log = tmp_path / "tar-dirs.log"
    shim = bin_dir / "tar"
    shim.write_text(
        "#!/bin/bash\n"
        "_prev=''\n"
        'for _a in "$@"; do\n'
        f"  [[ \"$_prev\" == -C ]] && printf '%s\\n' \"$_a\" >> '{log}'\n"
        '  _prev="$_a"\n'
        "done\n"
        f"exec '{real_tar}' \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bin_dir, log


def _run(
    script: pathlib.Path,
    tmp_path: pathlib.Path,
    root: pathlib.Path,
    args: list[str],
    env_extra: dict[str, str],
    shim_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    path_dirs = [shim_dir] if shim_dir else []
    path_dirs.append(pathlib.Path(sys.executable).parent)
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=path_dirs,
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": str(root), "KANBAN_ROOT": str(root), **env_extra},
    )
    proc = subprocess.Popen(
        ["bash", str(script), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _diag(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _staging_dirs(log: pathlib.Path) -> list[pathlib.Path]:
    assert log.is_file(), "tar was never invoked through the shim"
    return [pathlib.Path(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _assert_all_under(dirs: list[pathlib.Path], parent: pathlib.Path) -> None:
    assert dirs, "no staging directory was recorded"
    for d in dirs:
        assert d.is_relative_to(parent), f"staging dir {d} is not under {parent}"


def _without_temp_sh(tmp_path: pathlib.Path, script: pathlib.Path) -> pathlib.Path:
    """A copy of *script* in an installed-shape scripts/ dir whose lib/ lacks temp.sh."""
    scripts = tmp_path / "broken_install" / "scripts"
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib", ignore=shutil.ignore_patterns("temp.sh", "__pycache__"))
    shutil.copy2(script, scripts / script.name)
    return scripts / script.name


def test_export_stages_under_the_configured_temp_dir_override(tmp_path: pathlib.Path) -> None:
    root = _kanban_root(tmp_path, "kanban")
    sandbox = tmp_path / "sandbox_tmp"
    other_tmpdir = tmp_path / "other_tmpdir"
    other_tmpdir.mkdir()
    shim_dir, log = _tar_shim(tmp_path)
    out = tmp_path / "export.tar.gz"

    result = _run(
        _EXPORT_SH,
        tmp_path,
        root,
        ["--file", str(out)],
        {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox), "TMPDIR": str(other_tmpdir)},
        shim_dir,
    )

    assert result.returncode == 0, _diag(result)
    assert out.is_file()
    _assert_all_under(_staging_dirs(log), sandbox / "export-kanban-config")
    assert list(other_tmpdir.iterdir()) == [], "nothing may be staged under TMPDIR"


def test_import_stages_under_the_configured_temp_dir_override(tmp_path: pathlib.Path) -> None:
    source_root = _kanban_root(tmp_path, "source_kanban")
    archive = tmp_path / "export.tar.gz"
    exported = _run(
        _EXPORT_SH,
        tmp_path,
        source_root,
        ["--file", str(archive)],
        {"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "export_tmp")},
    )
    assert exported.returncode == 0, _diag(exported)

    target_root = tmp_path / "target_kanban"
    target_root.mkdir()
    sandbox = tmp_path / "sandbox_tmp"
    other_tmpdir = tmp_path / "other_tmpdir"
    other_tmpdir.mkdir()
    shim_dir, log = _tar_shim(tmp_path)

    result = _run(
        _IMPORT_SH,
        tmp_path,
        target_root,
        ["--file", str(archive)],
        {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox), "TMPDIR": str(other_tmpdir)},
        shim_dir,
    )

    assert result.returncode == 0, _diag(result)
    assert (target_root / "kanban.cfg").is_file(), "the archive was restored"
    _assert_all_under(_staging_dirs(log), sandbox / "import-kanban-config")
    assert list(other_tmpdir.iterdir()) == [], "nothing may be staged under TMPDIR"


def test_export_without_override_stages_under_kanban_cfg_paths_not_tmpdir(tmp_path: pathlib.Path) -> None:
    cfg_tmp_root = tmp_path / "cfg_tmp_root"
    root = _kanban_root(tmp_path, "kanban", f"[paths]\ntmp_root = {cfg_tmp_root}\ntmp_subdir = kanban_scratch\n")
    other_tmpdir = tmp_path / "other_tmpdir"
    other_tmpdir.mkdir()
    shim_dir, log = _tar_shim(tmp_path)
    out = tmp_path / "export.tar.gz"

    result = _run(_EXPORT_SH, tmp_path, root, ["--file", str(out)], {"TMPDIR": str(other_tmpdir)}, shim_dir)

    assert result.returncode == 0, _diag(result)
    _assert_all_under(_staging_dirs(log), cfg_tmp_root / "kanban_scratch" / "export-kanban-config")
    assert list(other_tmpdir.iterdir()) == [], "TMPDIR is not part of the temp-root precedence"


@pytest.mark.parametrize(
    ("script", "error_key", "args"),
    [
        (_EXPORT_SH, "export-kanban-config.temp-sh-missing", ["--file", "never.tar.gz"]),
        (_IMPORT_SH, "import-kanban-config.temp-sh-missing", ["--file", "never.tar.gz"]),
    ],
    ids=["export", "import"],
)
def test_missing_temp_sh_refuses_naming_its_path(
    tmp_path: pathlib.Path, script: pathlib.Path, error_key: str, args: list[str]
) -> None:
    root = _kanban_root(tmp_path, "kanban")
    broken = _without_temp_sh(tmp_path, script)
    sandbox = tmp_path / "sandbox_tmp"

    result = _run(broken, tmp_path, root, args, {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)})

    assert result.returncode != 0, _diag(result)
    assert f"error:   {error_key}" in result.stderr, _diag(result)
    assert str(broken.parent / "lib" / "temp.sh") in result.stderr, _diag(result)
    assert "remedy:" in result.stderr, _diag(result)
    assert not (tmp_path / "elsewhere" / "never.tar.gz").exists(), "no archive may be written"
    assert not sandbox.exists(), "no temp root may be guessed"
