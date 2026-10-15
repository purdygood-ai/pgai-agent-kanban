"""
test_docker_build_script.py
===========================
docker/build.sh builds one docker/<flavor>/Dockerfile image:

  * --dry-run prints exactly one build command -- the flavor's Dockerfile,
    the two pinned CLI versions from docker/cli-versions.env as build args,
    tags pgai-kanban:<flavor> and pgai-kanban:latest, and the repository
    root as context -- the same command from any working directory, and
    without calling the engine;
  * a name with no docker/<flavor>/Dockerfile is refused with the list of
    valid flavors; claude-abliterated (absent from the container tier's
    image manifest) is still buildable;
  * --tag replaces latest, --no-cache reaches the command, --engine picks
    the engine, and with no --engine the kanban's kanban.cfg [docker]
    docker_exe decides;
  * the real build is operator-only: inside an agent wake it prints the
    container tier's SKIPPED line and exits 0 with no engine call;
    authorized, it issues exactly one build through pgai_docker, which
    records START/FINISH in the docker-use log.

No test reaches a real engine. Every subprocess runs with an environment
built from an explicit set (tests.fixtures.scrubbed_subprocess_env), a
throwaway kanban root, and a fake engine (tests.fixtures.fake_container_engine)
first on PATH; the fake records each call and builds nothing. The pure
argv assembly in pgai_agent_kanban.container.image_build is tested in
process at the bottom.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from pgai_agent_kanban.container import image_build
from tests.fixtures.docker_cli_pins import load_cli_pins
from tests.fixtures.fake_container_engine import FakeEngine
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_BUILD_SH = _REPO_ROOT / "docker" / "build.sh"

_EXPECTED_FLAVORS = ("debian", "rocky9", "claude-abliterated")
_SKIP_KEY = "key=container_tier.operator_only"


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"rc={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


class _Sandbox:
    """A throwaway kanban root, HOME, temp dir and fake engines on PATH."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "kanban_root"
        self.root.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.temp = tmp_path / "pgai_tmp"
        self.temp.mkdir()
        self.elsewhere = tmp_path / "elsewhere"
        self.elsewhere.mkdir()
        self.docker = FakeEngine(tmp_path / "docker_engine", name="docker")
        self.podman = FakeEngine(tmp_path / "podman_engine", name="podman")
        # The interpreter running this test answers python3 inside the script.
        self.py_bin = tmp_path / "py_bin"
        self.py_bin.mkdir()
        (self.py_bin / "python3").symlink_to(pathlib.Path(sys.executable))

    def set_docker_exe(self, value: str) -> None:
        (self.root / "kanban.cfg").write_text(f"[docker]\ndocker_exe = {value}\n", encoding="utf-8")

    def env(self, *, wake: bool, **extra: str) -> dict:
        base = {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
        }
        if wake:
            base["PGAI_AGENT_WAKE_ACTIVE"] = "1"
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=(self.docker.bin_dir, self.podman.bin_dir, self.py_bin),
            extra_env={**base, **extra},
        )
        if not wake:
            assert "PGAI_AGENT_WAKE_ACTIVE" not in env
        return env

    def run(self, args: list[str], *, wake: bool = True, cwd: pathlib.Path | None = None,
            script: pathlib.Path = _BUILD_SH, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env=self.env(wake=wake, **extra),
            cwd=str(cwd or self.elsewhere),
            timeout=60,
        )

    def engine_calls(self) -> list[list[str]]:
        return self.docker.calls() + self.podman.calls()

    @property
    def docker_use_log(self) -> pathlib.Path:
        return self.root / "logs" / "docker-use.log"


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> _Sandbox:
    return _Sandbox(tmp_path)


def _one_command(result: subprocess.CompletedProcess) -> list[str]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert result.returncode == 0, _diagnostic(result)
    assert len(lines) == 1, f"--dry-run must print exactly one command\n{_diagnostic(result)}"
    return shlex.split(lines[0])


def _flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == flag]


def _expected_build_args() -> list[str]:
    return [f"{key}={value}" for key, value in load_cli_pins().items()]


# ---------------------------------------------------------------------------
# --dry-run: one command, pinned versions, tags, repository-root context
# ---------------------------------------------------------------------------


def test_dry_run_prints_one_build_command_with_pins_tags_and_repo_root_context(sandbox: _Sandbox) -> None:
    result = sandbox.run(["rocky9", "--dry-run"])
    argv = _one_command(result)
    detail = _diagnostic(result)

    assert argv[:2] == ["docker", "build"], detail
    assert _flag_values(argv, "-f") == [str(_REPO_ROOT / "docker" / "rocky9" / "Dockerfile")], detail
    assert _flag_values(argv, "--build-arg") == _expected_build_args(), detail
    assert {"CLAUDE_CODE_VERSION", "CODEX_VERSION"} == {a.split("=", 1)[0] for a in _flag_values(argv, "--build-arg")}
    assert _flag_values(argv, "-t") == ["pgai-kanban:rocky9", "pgai-kanban:latest"], detail
    assert argv[-1] == str(_REPO_ROOT), detail
    assert sandbox.engine_calls() == [], "--dry-run must not call the engine"


def test_dry_run_prints_the_same_command_from_any_working_directory(sandbox: _Sandbox) -> None:
    from_elsewhere = sandbox.run(["rocky9", "--dry-run"], cwd=sandbox.elsewhere)
    from_repo = sandbox.run(["rocky9", "--dry-run"], cwd=_REPO_ROOT)
    from_docker_dir = sandbox.run(["--dry-run", "rocky9"], cwd=_REPO_ROOT / "docker")

    assert _one_command(from_elsewhere) == _one_command(from_repo) == _one_command(from_docker_dir), (
        f"{_diagnostic(from_elsewhere)}\n{_diagnostic(from_repo)}\n{_diagnostic(from_docker_dir)}"
    )
    assert sandbox.engine_calls() == []


@pytest.mark.parametrize("flavor", _EXPECTED_FLAVORS)
def test_every_dockerfile_flavor_dry_runs(sandbox: _Sandbox, flavor: str) -> None:
    argv = _one_command(sandbox.run([flavor, "--dry-run"]))
    assert _flag_values(argv, "-f") == [str(_REPO_ROOT / "docker" / flavor / "Dockerfile")]
    assert _flag_values(argv, "-t")[0] == f"pgai-kanban:{flavor}"


def test_unknown_flavor_is_refused_with_the_valid_flavor_list(sandbox: _Sandbox) -> None:
    result = sandbox.run(["nosuch"])
    detail = _diagnostic(result)

    assert result.returncode != 0, detail
    assert "error:   docker_build.unknown_flavor" in result.stderr, detail
    message = next(line for line in result.stderr.splitlines() if line.startswith("message:"))
    listed = re.findall(r"[\w-]+", message.split("Valid flavors", 1)[1])
    for flavor in _EXPECTED_FLAVORS:
        assert flavor in listed, detail
    remedy = next(line for line in result.stderr.splitlines() if line.startswith("remedy:"))
    assert "<" not in remedy and "--dry-run" in remedy, detail
    assert sandbox.engine_calls() == []


# ---------------------------------------------------------------------------
# Options: --tag, --no-cache, --engine, kanban.cfg default
# ---------------------------------------------------------------------------


def test_tag_replaces_latest(sandbox: _Sandbox) -> None:
    argv = _one_command(sandbox.run(["debian", "--tag", "dev", "--dry-run"]))
    assert _flag_values(argv, "-t") == ["pgai-kanban:debian", "pgai-kanban:dev"]
    assert "pgai-kanban:latest" not in argv


def test_malformed_tag_is_a_usage_refusal(sandbox: _Sandbox) -> None:
    result = sandbox.run(["debian", "--tag", "not a tag", "--dry-run"])
    assert result.returncode == 2, _diagnostic(result)
    assert "error:   docker_build.usage" in result.stderr, _diagnostic(result)
    assert result.stdout == "", _diagnostic(result)


def test_no_cache_reaches_the_build_command(sandbox: _Sandbox) -> None:
    with_flag = _one_command(sandbox.run(["rocky9", "--no-cache", "--dry-run"]))
    without = _one_command(sandbox.run(["rocky9", "--dry-run"]))
    assert "--no-cache" in with_flag
    assert "--no-cache" not in without


def test_engine_podman_selects_podman(sandbox: _Sandbox) -> None:
    argv = _one_command(sandbox.run(["rocky9", "--engine", "podman", "--dry-run"]))
    assert argv[:2] == ["podman", "build"]


def test_engine_outside_docker_and_podman_is_a_usage_refusal(sandbox: _Sandbox) -> None:
    result = sandbox.run(["rocky9", "--engine", "nerdctl", "--dry-run"])
    assert result.returncode == 2, _diagnostic(result)
    assert "error:   docker_build.usage" in result.stderr, _diagnostic(result)


def test_default_engine_follows_kanban_cfg_docker_exe(sandbox: _Sandbox) -> None:
    assert _one_command(sandbox.run(["rocky9", "--dry-run"]))[0] == "docker"

    sandbox.set_docker_exe("podman")
    assert _one_command(sandbox.run(["rocky9", "--dry-run"]))[0] == "podman"

    # An explicit --engine wins over the configured value.
    assert _one_command(sandbox.run(["rocky9", "--engine", "docker", "--dry-run"]))[0] == "docker"


@pytest.mark.parametrize(
    "args",
    [[], ["rocky9", "--bogus"], ["rocky9", "debian"], ["rocky9", "--tag"]],
    ids=["no-flavor", "unknown-option", "two-flavors", "tag-without-value"],
)
def test_usage_errors_refuse_with_exit_2(sandbox: _Sandbox, args: list[str]) -> None:
    result = sandbox.run(args)
    detail = _diagnostic(result)
    assert result.returncode == 2, detail
    assert "error:   docker_build.usage" in result.stderr, detail
    assert f"remedy:  {_BUILD_SH} --help" in result.stderr, detail
    assert sandbox.engine_calls() == []


def test_help_describes_every_option_and_exits_0(sandbox: _Sandbox) -> None:
    result = sandbox.run(["--help"])
    assert result.returncode == 0, _diagnostic(result)
    for option in ("FLAVOR", "--tag", "--no-cache", "--engine", "--dry-run", "--help", "docker_exe"):
        assert option in result.stdout, _diagnostic(result)
    assert sandbox.engine_calls() == []


# ---------------------------------------------------------------------------
# The real build: operator-only, one build through pgai_docker
# ---------------------------------------------------------------------------


def test_build_inside_an_agent_wake_prints_skipped_and_calls_no_engine(sandbox: _Sandbox) -> None:
    result = sandbox.run(["rocky9"], wake=True, PGAI_CONTAINER_TIER="1")
    detail = _diagnostic(result)

    assert result.returncode == 0, detail
    skipped = [line for line in result.stdout.splitlines() if "SKIPPED" in line]
    assert len(skipped) == 1, detail
    assert skipped[0].startswith("build.sh: SKIPPED "), detail
    assert _SKIP_KEY in skipped[0], detail
    assert f'operator_command="PGAI_CONTAINER_TIER=1 bash {_BUILD_SH} rocky9"' in skipped[0], detail
    assert sandbox.engine_calls() == [], "a skipped build must not call the engine"


def test_authorized_build_issues_exactly_one_build_through_pgai_docker(sandbox: _Sandbox) -> None:
    expected = _one_command(sandbox.run(["rocky9", "--tag", "dev", "--dry-run"], wake=False))

    result = sandbox.run(["rocky9", "--tag", "dev"], wake=False, PGAI_CONTAINER_TIER="1")
    detail = _diagnostic(result)

    assert result.returncode == 0, detail
    assert sandbox.docker.calls() == [expected[1:]], detail
    assert sandbox.podman.calls() == [], detail
    lines = sandbox.docker_use_log.read_text(encoding="utf-8").splitlines()
    starts = [line for line in lines if " START " in line and "script=build.sh" in line]
    finishes = [line for line in lines if " FINISH " in line and "script=build.sh" in line]
    assert len(starts) == 1 and len(finishes) == 1, "\n".join(lines)
    assert "command=docker_build" in starts[0], "\n".join(lines)
    assert "exit=0" in finishes[0], "\n".join(lines)
    assert "actor=operator" in starts[0], "\n".join(lines)


def test_authorized_build_returns_the_engine_exit_status(sandbox: _Sandbox) -> None:
    sandbox.docker.set_scenario({"failures": {"build": {"exit": 17, "stderr": "fake build failure"}}})
    result = sandbox.run(["debian"], wake=False, PGAI_CONTAINER_TIER="1")
    assert result.returncode == 17, _diagnostic(result)
    assert "failed with exit 17" in result.stderr, _diagnostic(result)


def test_authorized_build_with_an_engine_not_on_path_is_refused(sandbox: _Sandbox) -> None:
    sandbox.set_docker_exe("pgai-test-engine-that-does-not-exist")
    result = sandbox.run(["rocky9"], wake=False, PGAI_CONTAINER_TIER="1")
    detail = _diagnostic(result)

    assert result.returncode == 1, detail
    assert "error:   docker_build.engine_not_found" in result.stderr, detail
    assert "--engine podman" in result.stderr, detail
    assert sandbox.engine_calls() == []


# ---------------------------------------------------------------------------
# A checkout whose pin file is broken refuses before any engine call
# ---------------------------------------------------------------------------


def _scratch_checkout(tmp_path: pathlib.Path, pins: str) -> pathlib.Path:
    """A minimal checkout: this tree's team/ (linked), build.sh, two flavors."""
    repo = tmp_path / "checkout"
    (repo / "docker" / "alpha").mkdir(parents=True)
    (repo / "docker" / "alpha" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "docker" / "not-a-flavor").mkdir()
    (repo / "docker" / "cli-versions.env").write_text(pins, encoding="utf-8")
    shutil.copy2(_BUILD_SH, repo / "docker" / "build.sh")
    (repo / "team").symlink_to(_TEAM_DIR)
    return repo


def test_missing_pinned_version_refuses_before_any_engine_call(sandbox: _Sandbox, tmp_path: pathlib.Path) -> None:
    repo = _scratch_checkout(tmp_path, "CLAUDE_CODE_VERSION=1.0.0\n")
    result = sandbox.run(["alpha", "--dry-run"], script=repo / "docker" / "build.sh")
    detail = _diagnostic(result)

    assert result.returncode == 1, detail
    assert "error:   docker_build.cli_pins_unreadable" in result.stderr, detail
    assert "CODEX_VERSION" in result.stderr, detail
    assert result.stdout == "", detail
    assert sandbox.engine_calls() == []


def test_flavors_are_the_directories_holding_a_dockerfile(sandbox: _Sandbox, tmp_path: pathlib.Path) -> None:
    repo = _scratch_checkout(tmp_path, "CLAUDE_CODE_VERSION=1.0.0\nCODEX_VERSION=2.0.0\n")
    script = repo / "docker" / "build.sh"

    argv = _one_command(sandbox.run(["alpha", "--dry-run"], script=script))
    assert argv[-1] == str(repo)
    assert _flag_values(argv, "--build-arg") == ["CLAUDE_CODE_VERSION=1.0.0", "CODEX_VERSION=2.0.0"]

    refused = sandbox.run(["not-a-flavor", "--dry-run"], script=script)
    assert refused.returncode == 2, _diagnostic(refused)
    assert "Valid flavors (docker/<flavor>/Dockerfile): alpha." in refused.stderr, _diagnostic(refused)


# ---------------------------------------------------------------------------
# pgai_agent_kanban.container.image_build, in process
# ---------------------------------------------------------------------------


def test_module_lists_this_trees_flavors() -> None:
    flavors = image_build.dockerfile_flavors(_REPO_ROOT)
    assert set(_EXPECTED_FLAVORS) <= set(flavors)
    assert flavors == sorted(flavors)


def test_module_build_arguments_shape() -> None:
    argv = image_build.build_arguments("debian", repo_root=_REPO_ROOT, tag="t1", no_cache=True)
    assert argv[:2] == ["build", "--no-cache"]
    assert _flag_values(argv, "-t") == ["pgai-kanban:debian", "pgai-kanban:t1"]
    assert argv[-1] == str(_REPO_ROOT)


def test_module_refuses_unknown_flavor_and_bad_tag() -> None:
    with pytest.raises(image_build.UnknownFlavorError) as info:
        image_build.build_arguments("nosuch", repo_root=_REPO_ROOT)
    assert set(_EXPECTED_FLAVORS) <= set(info.value.flavors)
    with pytest.raises(image_build.InvalidTagError):
        image_build.build_arguments("debian", repo_root=_REPO_ROOT, tag="-leading-dash")


# ---------------------------------------------------------------------------
# An installed copy of the package -- no tests/ tree beside it
# ---------------------------------------------------------------------------


def test_installed_package_copy_prints_the_build_argv_without_the_test_tree(tmp_path: pathlib.Path) -> None:
    """install.sh lays down pgai_agent_kanban/ without tests/; the module run
    from that copy, with no PYTHONPATH, still reads the pins of the checkout
    named by --repo-root and prints the full build argv."""
    installed = tmp_path / "installed"
    shutil.copytree(
        _TEAM_DIR / "pgai_agent_kanban", installed / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    assert not (installed / "tests").exists()
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    assert "PYTHONPATH" not in env

    def module(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pgai_agent_kanban.container.image_build", *args],
            capture_output=True, text=True, env=env, cwd=str(installed), timeout=60,
        )

    result = module("build-args", "debian", "--repo-root", str(_REPO_ROOT))
    detail = _diagnostic(result)
    assert result.returncode == 0, detail
    argv = result.stdout.splitlines()
    assert _flag_values(argv, "--build-arg") == _expected_build_args(), detail
    assert argv[-1] == str(_REPO_ROOT), detail

    flavors = module("flavors", "--repo-root", str(_REPO_ROOT))
    assert flavors.returncode == 0, _diagnostic(flavors)
    assert set(_EXPECTED_FLAVORS) <= set(flavors.stdout.splitlines()), _diagnostic(flavors)
