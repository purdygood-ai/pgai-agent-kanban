"""
test_container_mounts_lib.py
============================
Pins the workspace container's standard mounts and environment, assembled
by team/scripts/lib/container_mounts.sh and shared by every script that
starts the kanban container.

Two layers (team/TESTING.md, "Interface testing"):

  * Interface -- source the library and call container_mounts_assemble
    directly: the exact argument list it builds (HOME and the kanban root
    realpath'd and mounted at matching paths, both exported, extra mounts
    after them), its return status for a missing path (1) and a malformed
    extra mount (2), and the order those checks run in. With kanban.cfg
    [docker] docker_exe naming podman the list starts with the
    user-namespace mapping (--userns=keep-id); with docker it is unchanged.
  * Adapter -- run the real container-shell.sh under its own strict mode
    with a fake engine first on a test-controlled PATH and assert the one
    run argv the engine receives, byte for byte, plus the launcher's exit
    codes for a malformed --mount (2, before engine resolution) and a
    missing kanban root (1, engine never called).

No test builds an image, pulls one, or starts a container: the "engine" is
a bash stub that records its argv and exits 0, and every subprocess
environment is built from an explicit set (build_scrubbed_env) with a
throwaway kanban root, so neither the invoking shell's agent-wake signal
nor the operator's live install can reach the script under test.
"""

from __future__ import annotations

import pathlib
import shlex

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import BashResult, run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib" / "container_mounts.sh"
_LAUNCHER = _TEAM_DIR / "scripts" / "container-shell.sh"

_DEFAULT_IMAGE_TAG = "pgai-agent-kanban-debian:latest"
_CALL_END = "--end-of-call--"

# Records each invocation as one argv element per line, closed by a marker
# line, then exits 0 -- enough to recover every call's exact argv.
_FAKE_ENGINE = f"""#!/usr/bin/env bash
{{ printf '%s\\n' "$@"; printf '%s\\n' '{_CALL_END}'; }} >> "${{FAKE_ENGINE_LOG:?}}"
exit 0
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _Sandbox:
    """A throwaway kanban root, home, temp dir and fake-engine bin dir.

    ``root`` and ``home`` are reached through symlinks (``root_link``,
    ``home_link``) so a test can prove the library resolves them with
    realpath before mounting.
    """

    def __init__(self, tmp_path: pathlib.Path) -> None:
        real = tmp_path / "real"
        self.root = real / "kanban_root"
        self.home = real / "home"
        self.temp = tmp_path / "tmp"
        self.bin_dir = tmp_path / "bin"
        links = tmp_path / "links"
        for directory in (self.root, self.home, self.temp, self.bin_dir, links):
            directory.mkdir(parents=True)
        self.root_link = links / "kanban_root"
        self.root_link.symlink_to(self.root)
        self.home_link = links / "home"
        self.home_link.symlink_to(self.home)
        self.engine_log = tmp_path / "engine-calls.log"
        engine = self.bin_dir / "docker"
        engine.write_text(_FAKE_ENGINE, encoding="utf-8")
        engine.chmod(0o755)

    def env(self) -> dict[str, str]:
        """Subprocess env: fake engine first on PATH, no inherited wake signal."""
        return build_scrubbed_env(
            home_dir=self.home_link,
            extra_path_dirs=[self.bin_dir],
            extra_env={
                "KANBAN_ROOT": str(self.root_link),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root_link),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
                "PGAI_CONTAINER_TIER": "1",
                "FAKE_ENGINE_LOG": str(self.engine_log),
            },
        )

    def engine_calls(self) -> list[list[str]]:
        """Every argv the fake engine received, in call order."""
        if not self.engine_log.exists():
            return []
        calls: list[list[str]] = []
        current: list[str] = []
        for line in self.engine_log.read_text(encoding="utf-8").splitlines():
            if line == _CALL_END:
                calls.append(current)
                current = []
            else:
                current.append(line)
        return calls

    def standard_args(self) -> list[str]:
        """The standard mount/env arguments, with both paths realpath'd."""
        home, root = str(self.home), str(self.root)
        return [
            "-v", f"{home}:{home}",
            "-v", f"{root}:{root}",
            "-e", f"HOME={home}",
            "-e", f"PGAI_AGENT_KANBAN_ROOT_PATH={root}",
        ]


def _assemble(tmp_path: pathlib.Path, box: _Sandbox, root: str, home: str, *mounts: str) -> BashResult:
    """Source the library, call container_mounts_assemble, print its results.

    stdout carries the three result variables (root, home, then one
    argument per line) and a final ``status=N`` line with the function's
    return status.
    """
    words = " ".join(shlex.quote(word) for word in ("test-caller", root, home, *mounts))
    script = (
        "set -euo pipefail\n"
        f"source {shlex.quote(str(_LIB))}\n"
        "status=0\n"
        f"container_mounts_assemble {words} || status=$?\n"
        'printf "root=%s\\n" "${CONTAINER_MOUNTS_KANBAN_ROOT}"\n'
        'printf "home=%s\\n" "${CONTAINER_MOUNTS_HOME}"\n'
        'printf "arg=%s\\n" "${CONTAINER_MOUNTS_ARGS[@]}"\n'
        'printf "status=%s\\n" "${status}"\n'
    )
    return run_bash(tmp_path, script, base_env=box.env())


def _parsed(result: BashResult) -> dict[str, object]:
    lines = result.stdout.splitlines()
    return {
        "root": next(line[len("root="):] for line in lines if line.startswith("root=")),
        "home": next(line[len("home="):] for line in lines if line.startswith("home=")),
        # printf with an empty array still prints one bare "arg=" line.
        "args": [line[len("arg="):] for line in lines if line.startswith("arg=") and line != "arg="],
        "status": int(next(line[len("status="):] for line in lines if line.startswith("status="))),
    }


def _launch(tmp_path: pathlib.Path, box: _Sandbox, *args: str) -> BashResult:
    command = " ".join(shlex.quote(word) for word in ("bash", str(_LAUNCHER), *args))
    return run_bash(tmp_path, command, base_env=box.env(), timeout=60)


# ---------------------------------------------------------------------------
# Interface: container_mounts_assemble
# ---------------------------------------------------------------------------


def test_assemble_mounts_resolved_home_and_root_at_matching_paths_then_extras(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    result = _assemble(tmp_path, box, str(box.root_link), str(box.home_link), "/srv/a:/a", "/srv/b:/b:ro")
    got = _parsed(result)

    assert got["status"] == 0, result.stderr
    assert got["root"] == str(box.root), "kanban root was not resolved through its symlink"
    assert got["home"] == str(box.home), "home was not resolved through its symlink"
    assert got["args"] == box.standard_args() + ["-v", "/srv/a:/a", "-v", "/srv/b:/b:ro"], got["args"]


def test_assemble_without_extra_mounts_yields_only_the_standard_arguments(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    result = _assemble(tmp_path, box, str(box.root), str(box.home))
    got = _parsed(result)

    assert got["status"] == 0, result.stderr
    assert got["args"] == box.standard_args(), got["args"]


def test_assemble_refuses_an_extra_mount_without_a_colon_with_status_two(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    result = _assemble(tmp_path, box, str(box.root), str(box.home), "/srv/a:/a", "no-separator")
    got = _parsed(result)

    assert got["status"] == 2, result.stdout + result.stderr
    assert "test-caller: --mount value must be '<host-path>:<container-path>': 'no-separator'" in result.stderr
    assert got["args"] == [] and got["root"] == "" and got["home"] == "", got


def test_assemble_refuses_a_missing_or_empty_kanban_root_with_status_one(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    missing = tmp_path / "no-such-root"
    for root in (str(missing), ""):
        result = _assemble(tmp_path, box, root, str(box.home))
        got = _parsed(result)
        assert got["status"] == 1, (root, result.stdout + result.stderr)
        assert (
            f"test-caller: PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory: '{root}'"
            in result.stderr
        ), result.stderr
        assert got["args"] == [], got


def test_assemble_refuses_a_missing_home_with_status_one(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    missing = tmp_path / "no-such-home"
    result = _assemble(tmp_path, box, str(box.root), str(missing))
    got = _parsed(result)

    assert got["status"] == 1, result.stdout + result.stderr
    assert f"test-caller: $HOME is not set to an existing directory: '{missing}'" in result.stderr
    assert got["args"] == [], got


def test_assemble_checks_the_standard_paths_before_the_extra_mounts(tmp_path: pathlib.Path) -> None:
    """A missing kanban root wins over a malformed extra mount: status 1, not 2."""
    box = _Sandbox(tmp_path)
    result = _assemble(tmp_path, box, str(tmp_path / "no-such-root"), str(box.home), "no-separator")

    assert _parsed(result)["status"] == 1, result.stdout + result.stderr
    assert "--mount" not in result.stderr, result.stderr


@pytest.mark.parametrize("engine", ["podman", "/usr/bin/podman"])
def test_assemble_maps_the_operator_user_when_the_engine_is_podman(tmp_path: pathlib.Path, engine: str) -> None:
    box = _Sandbox(tmp_path)
    (box.root / "kanban.cfg").write_text(f"[docker]\ndocker_exe = {engine}\n", encoding="utf-8")
    result = _assemble(tmp_path, box, str(box.root), str(box.home), "/srv/a:/a")
    got = _parsed(result)

    assert got["status"] == 0, result.stderr
    assert got["args"] == ["--userns=keep-id", *box.standard_args(), "-v", "/srv/a:/a"], got["args"]


@pytest.mark.parametrize("cfg", [None, "[docker]\ndocker_exe = docker\n", "[docker]\ndocker_exe =\n"])
def test_assemble_adds_no_user_mapping_when_the_engine_is_docker(tmp_path: pathlib.Path, cfg: "str | None") -> None:
    box = _Sandbox(tmp_path)
    if cfg is not None:
        (box.root / "kanban.cfg").write_text(cfg, encoding="utf-8")
    result = _assemble(tmp_path, box, str(box.root), str(box.home))
    got = _parsed(result)

    assert got["status"] == 0, result.stderr
    assert got["args"] == box.standard_args(), got["args"]


# ---------------------------------------------------------------------------
# Adapter: container-shell.sh with a fake engine
# ---------------------------------------------------------------------------


def test_container_shell_issues_the_standard_run_argv_with_the_default_image(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    result = _launch(tmp_path, box)

    assert result.returncode == 0, result.stderr
    assert box.engine_calls() == [["run", "--rm", "-it", *box.standard_args(), _DEFAULT_IMAGE_TAG]]
    assert (
        f"container-shell.sh: engine=docker image={_DEFAULT_IMAGE_TAG} "
        f"kanban_root={box.root} home={box.home}"
    ) in result.stderr, result.stderr


def test_container_shell_appends_each_mount_flag_after_the_standard_arguments(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    result = _launch(
        tmp_path, box,
        "--mount", "/srv/a:/a", "--mount=/srv/b:/b", "--kanban-root", str(box.root_link), "-t", "custom:v9",
    )

    assert result.returncode == 0, result.stderr
    assert box.engine_calls() == [
        ["run", "--rm", "-it", *box.standard_args(), "-v", "/srv/a:/a", "-v", "/srv/b:/b", "custom:v9"]
    ]


def test_container_shell_malformed_mount_exits_two_before_engine_resolution(tmp_path: pathlib.Path) -> None:
    """The configured engine cannot resolve (it would exit 1), so exit 2 proves
    the --mount shape is checked first, on any host."""
    box = _Sandbox(tmp_path)
    (box.root / "kanban.cfg").write_text("[docker]\ndocker_exe = no-such-container-engine\n", encoding="utf-8")
    result = _launch(tmp_path, box, "--mount", "no-separator")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "--mount value must be '<host-path>:<container-path>': 'no-separator'" in result.stderr
    assert "docker_exe" not in result.stderr, "engine resolution ran before the --mount usage check"
    assert box.engine_calls() == []


def test_container_shell_missing_kanban_root_exits_one_without_calling_the_engine(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    missing = tmp_path / "no-such-root"
    result = _launch(tmp_path, box, "--kanban-root", str(missing))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory: '{missing}'" in result.stderr
    assert box.engine_calls() == []


def test_container_shell_under_podman_runs_with_the_user_mapping(tmp_path: pathlib.Path) -> None:
    box = _Sandbox(tmp_path)
    podman = box.bin_dir / "podman"
    podman.write_text(_FAKE_ENGINE, encoding="utf-8")
    podman.chmod(0o755)
    (box.root / "kanban.cfg").write_text("[docker]\ndocker_exe = podman\n", encoding="utf-8")
    result = _launch(tmp_path, box)

    assert result.returncode == 0, result.stderr
    assert box.engine_calls() == [
        ["run", "--rm", "-it", "--userns=keep-id", *box.standard_args(), _DEFAULT_IMAGE_TAG]
    ]
