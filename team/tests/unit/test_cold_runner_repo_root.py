"""
test_cold_runner_repo_root.py
=============================
Which repository the test runners work in, by layout.

Four runners resolve the dev tree and then work inside <repo>/team:
run-cold-build-tests.sh, run-gated-tests.sh, run-unit-tests.sh and
run-integration-tests.sh. They share one rule, resolve_runner_repo_root in
team/scripts/lib/dev_tree.sh:

  * install layout (<kanban-root>/scripts/<runner>, no repository above it):
    the resolved dev tree, PGAI_DEV_TREE_PATH;
  * checkout layout (<repo>/team/scripts/<runner>): that checkout, even when
    PGAI_DEV_TREE_PATH names a different tree -- a worktree run tests the
    worktree.

Each runner is copied, with the libs it sources, into a sandbox shaped like
one layout or the other, and run as a real subprocess under a rebuilt
environment whose kanban root, temp root and HOME are all sandbox paths.
Nothing reaches a container engine or a real pytest session: `docker` and
`podman` are stubs that log every call, and `python3` is a stand-in that
records the working directory of the runner's own `python3 -m pytest`
launch and exits 0 (every other python3 call goes to the real
interpreter). The cold and gated runners are judged by that recorded
directory; the unit and integration runners by the absolute path their
--files check looks for, which is built from the same repository root.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_LIB = _SCRIPTS / "lib"
_IMAGE_MANIFEST = _TEAM_DIR / "config" / "container-images.txt"

_TIMEOUT_SECONDS = 120

_ENGINE_STUB = """#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "{log}"
exit 0
"""

# Records the working directory of the runner's own pytest launch and
# exits 0; the `-m pytest --version` probe and every other python3 call go
# to the interpreter running this test, by its unresolved path so a
# virtualenv's site-packages stay on its sys.path.
_PYTHON3_STAND_IN = """#!/usr/bin/env bash
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "pytest" && "${{3:-}}" != "--version" ]]; then
  printf '%s\\n' "$PWD" >> "{record}"
  exit 0
fi
exec "{real}" "$@"
"""

# Sibling runners whose pytest launch is observed directly, beyond the cold
# runner's own tests below: (runner, argv, extra env).
_LAUNCHING_RUNNERS = [
    ("run-gated-tests.sh", [], {}),
]
_LAUNCHING_IDS = [r[0] for r in _LAUNCHING_RUNNERS]

# Runners observed through their --files check: (runner, test path under team/).
_TARGETED_RUNNERS = [
    ("run-unit-tests.sh", "tests/unit/test_absent_from_every_tree.py"),
    ("run-integration-tests.sh", "tests/integration/test_absent_from_every_tree.py"),
]
_TARGETED_IDS = [r[0] for r in _TARGETED_RUNNERS]


class _Sandbox:
    """A throwaway kanban root (which is also the install root), temp root,
    HOME, stub bin directory, and fixture trees."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.base = tmp_path
        self.root = tmp_path / "kanban_root"
        self.root.mkdir()
        self.temp = tmp_path / "pgai_tmp"
        self.temp.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.engine_log = tmp_path / "engine.log"
        self.record = tmp_path / "pytest-cwd.log"
        for engine in ("docker", "podman"):
            self._write_exec(self.bin / engine, _ENGINE_STUB.format(log=self.engine_log))
        self._write_exec(
            self.bin / "python3",
            _PYTHON3_STAND_IN.format(record=self.record, real=sys.executable),
        )

    @staticmethod
    def _write_exec(path: pathlib.Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    @staticmethod
    def _copy_runner(scripts_dir: pathlib.Path, runner: str) -> pathlib.Path:
        """Copy *runner*, the libs it sources and the image manifest into
        *scripts_dir* and its sibling config/ (the shape install.sh gives a
        root and a checkout gives team/)."""
        scripts_dir.mkdir(parents=True)
        shutil.copytree(_LIB, scripts_dir / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        config = scripts_dir.parent / "config"
        config.mkdir()
        shutil.copy2(_IMAGE_MANIFEST, config / _IMAGE_MANIFEST.name)
        target = scripts_dir / runner
        shutil.copy2(_SCRIPTS / runner, target)
        return target

    def install_runner(self, runner: str) -> pathlib.Path:
        """<kanban-root>/scripts/<runner>, with no repository above it."""
        return self._copy_runner(self.root / "scripts", runner)

    def checkout_runner(self, checkout: pathlib.Path, runner: str) -> pathlib.Path:
        """<checkout>/team/scripts/<runner>."""
        return self._copy_runner(checkout / "team" / "scripts", runner)

    def tree(self, name: str) -> pathlib.Path:
        """A fixture checkout with the test directories the runners enter."""
        tree = self.base / name
        for sub in ("cold", "unit", "integration"):
            (tree / "team" / "tests" / sub).mkdir(parents=True, exist_ok=True)
        return tree

    def run(self, script: pathlib.Path, args: list[str], dev_tree: pathlib.Path, **extra: str) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=(self.bin,),
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
                "PGAI_DEV_TREE_PATH": str(dev_tree),
                **extra,
            },
        )
        return _run_owned(["bash", str(script), *args], env=env, cwd=self.home)

    def launch_dirs(self) -> list[pathlib.Path]:
        if not self.record.exists():
            return []
        return [pathlib.Path(line) for line in self.record.read_text(encoding="utf-8").splitlines()]

    def engine_calls(self) -> str:
        return self.engine_log.read_text(encoding="utf-8") if self.engine_log.exists() else ""


def _run_owned(argv: list[str], *, env: dict, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    """Run *argv* in its own process group; on timeout kill the whole group
    and re-raise, so a hang fails the test and leaves no descendant alive."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(cwd),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _detail(result: subprocess.CompletedProcess) -> str:
    return f"rc={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _looked_for(tree: pathlib.Path, rel: str) -> str:
    return f"looked for {tree / 'team' / rel}"


# ---------------------------------------------------------------------------
# Cold-build runner
# ---------------------------------------------------------------------------


def test_installed_cold_runner_builds_the_resolved_dev_tree(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    fixture = sandbox.tree("fixture_checkout")
    runner = sandbox.install_runner("run-cold-build-tests.sh")

    result = sandbox.run(runner, ["--image", "debian"], fixture, PGAI_CONTAINER_TIER="1")

    assert result.returncode == 0, _detail(result)
    launches = sandbox.launch_dirs()
    assert [d.resolve() for d in launches] == [(fixture / "team").resolve()], (
        f"the installed cold lane must change into the dev tree's team/ "
        f"({fixture / 'team'}), not {sandbox.root.parent / 'team'}; "
        f"pytest launched in {launches}\n{_detail(result)}"
    )
    assert sandbox.engine_calls() == "", sandbox.engine_calls()


def test_checkout_cold_runner_builds_its_own_checkout_not_the_configured_dev_tree(
    tmp_path: pathlib.Path,
) -> None:
    sandbox = _Sandbox(tmp_path)
    checkout = sandbox.tree("worktree_checkout")
    elsewhere = sandbox.tree("configured_dev_tree")
    runner = sandbox.checkout_runner(checkout, "run-cold-build-tests.sh")

    result = sandbox.run(runner, ["--image", "debian"], elsewhere, PGAI_CONTAINER_TIER="1")

    assert result.returncode == 0, _detail(result)
    launches = sandbox.launch_dirs()
    assert [d.resolve() for d in launches] == [(checkout / "team").resolve()], (
        f"a checkout-layout cold lane must change into its own checkout's team/ "
        f"({checkout / 'team'}), not PGAI_DEV_TREE_PATH's ({elsewhere / 'team'}); "
        f"pytest launched in {launches}\n{_detail(result)}"
    )
    assert sandbox.engine_calls() == "", sandbox.engine_calls()


# ---------------------------------------------------------------------------
# The same rule in every sibling runner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner,args,extra", _LAUNCHING_RUNNERS, ids=_LAUNCHING_IDS)
def test_installed_runner_launches_pytest_in_the_resolved_dev_tree(
    tmp_path: pathlib.Path, runner: str, args: list[str], extra: dict
) -> None:
    sandbox = _Sandbox(tmp_path)
    fixture = sandbox.tree("fixture_checkout")
    result = sandbox.run(sandbox.install_runner(runner), args, fixture, **extra)

    assert result.returncode == 0, _detail(result)
    assert [d.resolve() for d in sandbox.launch_dirs()] == [(fixture / "team").resolve()], _detail(result)


@pytest.mark.parametrize("runner,args,extra", _LAUNCHING_RUNNERS, ids=_LAUNCHING_IDS)
def test_checkout_runner_launches_pytest_in_its_own_checkout(
    tmp_path: pathlib.Path, runner: str, args: list[str], extra: dict
) -> None:
    sandbox = _Sandbox(tmp_path)
    checkout = sandbox.tree("worktree_checkout")
    elsewhere = sandbox.tree("configured_dev_tree")
    result = sandbox.run(sandbox.checkout_runner(checkout, runner), args, elsewhere, **extra)

    assert result.returncode == 0, _detail(result)
    assert [d.resolve() for d in sandbox.launch_dirs()] == [(checkout / "team").resolve()], _detail(result)


@pytest.mark.parametrize("runner,rel", _TARGETED_RUNNERS, ids=_TARGETED_IDS)
def test_installed_runner_resolves_files_in_the_resolved_dev_tree(
    tmp_path: pathlib.Path, runner: str, rel: str
) -> None:
    sandbox = _Sandbox(tmp_path)
    fixture = sandbox.tree("fixture_checkout")
    result = sandbox.run(sandbox.install_runner(runner), ["--files", rel], fixture)

    assert result.returncode == 1, _detail(result)
    assert _looked_for(fixture, rel) in result.stderr, _detail(result)
    assert sandbox.launch_dirs() == [], _detail(result)


@pytest.mark.parametrize("runner,rel", _TARGETED_RUNNERS, ids=_TARGETED_IDS)
def test_checkout_runner_resolves_files_in_its_own_checkout(
    tmp_path: pathlib.Path, runner: str, rel: str
) -> None:
    sandbox = _Sandbox(tmp_path)
    checkout = sandbox.tree("worktree_checkout")
    elsewhere = sandbox.tree("configured_dev_tree")
    result = sandbox.run(sandbox.checkout_runner(checkout, runner), ["--files", rel], elsewhere)

    assert result.returncode == 1, _detail(result)
    assert _looked_for(checkout, rel) in result.stderr, _detail(result)
    assert _looked_for(elsewhere, rel) not in result.stderr, _detail(result)


# ---------------------------------------------------------------------------
# The shared rule itself (lib/dev_tree.sh resolve_runner_repo_root)
# ---------------------------------------------------------------------------


def _resolve(script_dir: pathlib.Path, home: pathlib.Path, dev_tree: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=home, extra_env={"PGAI_DEV_TREE_PATH": dev_tree})
    return _run_owned(
        [
            "bash",
            "-c",
            'source "$1" && resolve_runner_repo_root "$2" "$3"',
            "resolve",
            str(_LIB / "dev_tree.sh"),
            str(script_dir),
            "sandbox-kanban.cfg",
        ],
        env=env,
        cwd=home,
    )


def test_rule_checkout_layout_is_the_scripts_own_checkout(tmp_path: pathlib.Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "team" / "scripts").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    result = _resolve(checkout / "team" / "scripts", tmp_path, str(other))
    assert result.returncode == 0, _detail(result)
    assert pathlib.Path(result.stdout.strip()).resolve() == checkout.resolve(), _detail(result)


def test_rule_install_layout_is_the_resolved_dev_tree(tmp_path: pathlib.Path) -> None:
    (tmp_path / "kanban_root" / "scripts").mkdir(parents=True)
    dev_tree = tmp_path / "dev_tree"
    dev_tree.mkdir()
    result = _resolve(tmp_path / "kanban_root" / "scripts", tmp_path, str(dev_tree))
    assert result.returncode == 0, _detail(result)
    assert pathlib.Path(result.stdout.strip()).resolve() == dev_tree.resolve(), _detail(result)


def test_rule_install_layout_refuses_a_dev_tree_that_does_not_exist(tmp_path: pathlib.Path) -> None:
    (tmp_path / "kanban_root" / "scripts").mkdir(parents=True)
    missing = tmp_path / "moved_away"
    result = _resolve(tmp_path / "kanban_root" / "scripts", tmp_path, str(missing))
    assert result.returncode == 1, _detail(result)
    assert result.stdout == "", _detail(result)
    assert f"dev_tree_path '{missing}' does not exist (resolved from sandbox-kanban.cfg)" in result.stderr, _detail(
        result
    )
