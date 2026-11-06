"""
test_harness_temp_root.py
=========================
``harness/engine-behavior-baseline.sh`` and ``harness/helloworld-flow-check.sh``
put their scratch under the framework temp root that ``lib/temp.sh``
(``pgai_temp_dir``) resolves, never under a root of their own.

Covered, against the real harness scripts run as subprocesses from a
foreign cwd with an environment built by ``build_scrubbed_env``:

  * engine-behavior-baseline ``--compare`` over a directory already holding
    the committed goldens (compared as-is, no capture) makes its scratch
    parent under ``<sandbox>/harness/engine-behavior-baseline/`` when
    PGAI_AGENT_KANBAN_TEMP_DIR names a sandbox, leaves it empty, and
    writes nothing under an unrelated TMPDIR;
  * with the override unset, that scratch parent follows the sandbox
    kanban.cfg ``[paths]`` ``tmp_root``/``tmp_subdir``;
  * a bare helloworld-flow-check run builds its throwaway kanban root under
    ``<sandbox>/harness/helloworld-flow-check/``;
  * a copy of either harness whose ``scripts/lib/`` has no ``temp.sh``
    refuses dual-channel, naming the missing path, with that harness's own
    failure code (2 for the baseline's capture failure, 1 for the flow
    check's setup failure) and creates nothing under the sandbox.

The full three-surface capture under a sandbox override is exercised by
test_engine_behavior_baseline_harness.py, and the full flow's --compare by
test_helloworld_flow_harness.py; this module adds the location assertions.
Neither harness is container-tier: no image is built and no container
started.
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
_BASELINE_SH = _SCRIPTS_DIR / "harness" / "engine-behavior-baseline.sh"
_FLOW_SH = _SCRIPTS_DIR / "harness" / "helloworld-flow-check.sh"
_BASELINE_GOLDEN_DIR = _TEAM_DIR / "tests" / "fixtures" / "engine-baseline"
_TIMEOUT_SECONDS = 180


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """The flow check drives cm/open-rc.sh and cm/release.sh over a real repo."""
    require_binary("git", label="harness-temp-root")


def _run(
    script: pathlib.Path, tmp_path: pathlib.Path, args: list[str], env_extra: dict[str, str]
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        # The flow's wake/pm-agent substrate needs a 3.12+ python3 with
        # pytest importable: the interpreter running this suite.
        extra_path_dirs=(pathlib.Path(sys.executable).parent,),
        extra_env=env_extra,
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


def _seeded_capture_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A capture directory holding the goldens, so --compare diffs without capturing."""
    target = tmp_path / "seeded-capture"
    shutil.copytree(_BASELINE_GOLDEN_DIR, target)
    return target


def _without_temp_sh(tmp_path: pathlib.Path, script: pathlib.Path) -> pathlib.Path:
    """A copy of a harness in an installed-shape scripts/ dir whose lib/ lacks temp.sh."""
    scripts = tmp_path / "broken_install" / "scripts"
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib", ignore=shutil.ignore_patterns("temp.sh", "__pycache__"))
    shutil.copytree(_SCRIPTS_DIR / "harness", scripts / "harness", ignore=shutil.ignore_patterns("__pycache__"))
    return scripts / "harness" / script.name


def test_baseline_scratch_lands_under_the_temp_dir_override(tmp_path: pathlib.Path) -> None:
    sandbox = tmp_path / "sandbox_tmp"
    other_tmpdir = tmp_path / "other_tmpdir"
    other_tmpdir.mkdir()

    result = _run(
        _BASELINE_SH,
        tmp_path,
        ["--compare", str(_seeded_capture_dir(tmp_path))],
        {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox), "TMPDIR": str(other_tmpdir)},
    )

    assert result.returncode == 0, _diag(result)
    scratch_parent = sandbox / "harness" / "engine-behavior-baseline"
    assert scratch_parent.is_dir(), _diag(result)
    assert list(scratch_parent.iterdir()) == [], "the compare's diff buffer is removed on return"
    assert list(other_tmpdir.iterdir()) == [], "nothing may be written under TMPDIR"


def test_baseline_scratch_follows_kanban_cfg_paths_without_an_override(tmp_path: pathlib.Path) -> None:
    configured = tmp_path / "configured"
    root = tmp_path / "kanban"
    root.mkdir()
    (root / "kanban.cfg").write_text(f"[paths]\ntmp_root = {configured}\ntmp_subdir = scratch\n", encoding="utf-8")

    result = _run(
        _BASELINE_SH,
        tmp_path,
        ["--compare", str(_seeded_capture_dir(tmp_path))],
        {"PGAI_AGENT_KANBAN_ROOT_PATH": str(root)},
    )

    assert result.returncode == 0, _diag(result)
    assert (configured / "scratch" / "harness" / "engine-behavior-baseline").is_dir(), _diag(result)


def test_flow_check_throwaway_root_lands_under_the_temp_dir_override(tmp_path: pathlib.Path) -> None:
    sandbox = tmp_path / "sandbox_tmp"

    result = _run(_FLOW_SH, tmp_path, [], {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)})

    assert result.returncode == 0, _diag(result)
    runs = sorted((sandbox / "harness" / "helloworld-flow-check").glob("run.*"))
    assert len(runs) == 1, f"expected one throwaway root under the sandbox, found {runs}\n{_diag(result)}"
    assert (runs[0] / "projects.cfg").is_file(), "the throwaway kanban root was built there"


@pytest.mark.parametrize(
    ("script", "error_key", "exit_code"),
    [
        (_BASELINE_SH, "engine-behavior-baseline.temp-sh-missing", 2),
        (_FLOW_SH, "helloworld-flow-check.temp-sh-missing", 1),
    ],
    ids=["engine-behavior-baseline", "helloworld-flow-check"],
)
def test_missing_temp_sh_refuses_naming_the_path(
    tmp_path: pathlib.Path, script: pathlib.Path, error_key: str, exit_code: int
) -> None:
    broken = _without_temp_sh(tmp_path, script)
    sandbox = tmp_path / "sandbox_tmp"

    result = _run(
        broken, tmp_path, ["--capture", str(tmp_path / "capture")], {"PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox)}
    )

    assert result.returncode == exit_code, _diag(result)
    assert f"error:   {error_key}" in result.stderr, _diag(result)
    assert str(broken.parents[1] / "lib" / "temp.sh") in result.stderr, _diag(result)
    assert "remedy:" in result.stderr
    assert not sandbox.exists(), "nothing may be created under the temp root"
    assert not (tmp_path / "capture").exists()
