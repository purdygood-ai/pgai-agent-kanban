"""
test_container_tier_gate.py
===========================
The container tier (image builds, base-image pulls, containers) is
operator-invoked only, and the operator picks the images. Each of the four
container-tier harnesses (team/scripts/lib/container_tier.sh):

  * without operator authorization (PGAI_CONTAINER_TIER=1 or
    --operator-approved), or from inside an agent wake, prints one SKIPPED
    line and exits 0 -- nothing built, no verdict, no ledger record;
  * with authorization, builds only the images selected with --image NAME /
    --image all (or kanban.cfg [release] container_images), lists the
    images and builds nothing when none is selected, and refuses an image
    name the manifest does not declare;
  * records each run in logs/docker-use.log (START/FINISH for an operator
    run, SKIPPED for an unauthorized call), and keeps working when that log
    cannot be written.

Every harness here runs as a real subprocess in an environment built from
an explicit minimal set of names (tests.fixtures.scrubbed_subprocess_env),
the shape a wake-scrubbed environment has. `docker` and `podman` resolve to
stubs that append every invocation to a log file and exit 0, so "started no
container" is checked as "the engine log is empty": zero builds, pulls,
creates or runs of any kind.

The authorized cases never reach a real engine either: deep-verify.sh runs
a stubbed lane through its documented PGAI_DEEP_VERIFY_COLD_LANE_CMD seam,
run-cold-build-tests.sh hands its pytest launch to a python3 stand-in that
records the launch (and the image selection it was given) and exits 0, and
run-three-host-matrix.sh is pointed at an engine name that does not exist,
which it reports as not-attempted.

The shipped-image test module (tests/unit/test_entrypoint_shipped_image.py,
run by the operator through run-gated-tests.sh) honours the same rule
through tests.fixtures.binary_guard.require_container_tier: it is run here
as a nested pytest against the same recording engine stubs, never a real
engine. So do the team/tests/cold modules, through the
operator_container_tier fixture in tests/cold/conftest.py: unauthorized,
the whole directory is collected; authorized, each module whose image
fixture probes the daemon is run on its own.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from tests.fixtures.binary_guard import container_tier_authorized
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPTS = _TEAM_DIR / "scripts"
_LIB = _SCRIPTS / "lib" / "container_tier.sh"
_DEEP_VERIFY = _SCRIPTS / "deep-verify.sh"
_COLD_RUNNER = _SCRIPTS / "run-cold-build-tests.sh"
_MATRIX = _SCRIPTS / "run-three-host-matrix.sh"

_SKIP_KEY = "key=container_tier.operator_only"
_UNKNOWN_IMAGE_KEY = "error:   container_tier.unknown_image"
_REFUSAL_EXIT = 3
_PROJECT = "demo"

# (id, script path, argv after the script)
_HARNESSES = [
    ("run-cold-build-tests", _COLD_RUNNER, ["--image", "all"]),
    ("run-three-host-matrix", _MATRIX, ["--image", "all"]),
    ("strangers-path-check", _SCRIPTS / "harness" / "strangers-path-check.sh", ["--image", "all"]),
    ("deep-verify", _DEEP_VERIFY, ["--project", _PROJECT, "--image", "all"]),
]
_HARNESS_IDS = [h[0] for h in _HARNESSES]

_ENGINE_STUB = """#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "{log}"
exit 0
"""

# Intercepts only the cold runner's own `python3 -m pytest <args>` launch,
# recording the image selection it was handed; every other python3 call
# (including the `-m pytest --version` probe) is delegated to the
# interpreter running this test, by its unresolved path so a virtualenv's
# site-packages (where pytest lives) stay on its sys.path.
_PYTHON3_STAND_IN = """#!/usr/bin/env bash
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "pytest" && "${{3:-}}" != "--version" ]]; then
  printf 'pytest-launched %s images=%s\\n' "${{*:3}}" "${{PGAI_CONTAINER_IMAGES:-}}" >> "{record}"
  exit "${{STAND_IN_PYTEST_EXIT:-0}}"
fi
exec "{real}" "$@"
"""

_GREEN_LANE_STUB = """#!/usr/bin/env bash
echo "lane-ran $*" >> "{record}"
echo "Cold-build lane PASSED"
exit 0
"""

_PRIOR_LEDGER = (
    "# Deep-Verify Ledger — demo\n\n"
    "Append-only. One record per deep-verify run.\n\n"
    "## Deep-Verify Run: 2026-09-01T03:15:00+00:00 — demo — v1.0.0\n"
    "- Commit: abc1234\n"
    "- Verdict: built\n"
    "- Detail: docker/debian: built\n"
)


class _Sandbox:
    """A throwaway kanban root, temp root, HOME, and stub bin directory."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban_root"
        (self.root / "projects" / _PROJECT).mkdir(parents=True)
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
        self.temp = tmp_path / "pgai_tmp"
        self.temp.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.engine_log = tmp_path / "engine.log"
        self.record = tmp_path / "record.log"
        for engine in ("docker", "podman"):
            self._write_exec(self.bin / engine, _ENGINE_STUB.format(log=self.engine_log))
        (self.bin / "python3").symlink_to(pathlib.Path(sys.executable).resolve())

    @staticmethod
    def _write_exec(path: pathlib.Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    @property
    def ledger(self) -> pathlib.Path:
        return self.root / "projects" / _PROJECT / "deep-verify-ledger.md"

    @property
    def docker_use_log(self) -> pathlib.Path:
        return self.root / "logs" / "docker-use.log"

    def use_python3_stand_in(self) -> None:
        link = self.bin / "python3"
        link.unlink()
        self._write_exec(
            link,
            _PYTHON3_STAND_IN.format(record=self.record, real=sys.executable),
        )

    def green_lane(self) -> pathlib.Path:
        lane = self.bin.parent / "green_lane.sh"
        self._write_exec(lane, _GREEN_LANE_STUB.format(record=self.record))
        return lane

    def set_cfg_images(self, value: str) -> None:
        (self.root / "kanban.cfg").write_text(f"[release]\ncontainer_images = {value}\n", encoding="utf-8")

    def env(self, **extra: str) -> dict:
        return build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=(self.bin,),
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
                "PGAI_DEV_TREE_PATH": str(_REPO_ROOT),
                **extra,
            },
        )

    def run(self, script: pathlib.Path, args: list[str], **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env=self.env(**extra),
            cwd=str(self.home),
            timeout=120,
        )

    def engine_calls(self) -> str:
        return self.engine_log.read_text(encoding="utf-8") if self.engine_log.exists() else ""

    def recorded(self) -> str:
        return self.record.read_text(encoding="utf-8") if self.record.exists() else ""

    def docker_use_lines(self) -> list[str]:
        if not self.docker_use_log.exists():
            return []
        return self.docker_use_log.read_text(encoding="utf-8").splitlines()


def _detail(result: subprocess.CompletedProcess) -> str:
    return f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _assert_skipped(result: subprocess.CompletedProcess, sandbox: _Sandbox, script: pathlib.Path) -> None:
    detail = _detail(result)
    assert result.returncode == 0, detail
    skipped = [line for line in result.stdout.splitlines() if "SKIPPED" in line]
    assert len(skipped) == 1, detail
    line = skipped[0]
    assert line.startswith(f"{script.name}: SKIPPED "), detail
    assert _SKIP_KEY in line, detail
    assert "container tier is operator-only" in line, detail
    assert f'operator_command="PGAI_CONTAINER_TIER=1 bash {script}' in line, detail
    assert (
        sandbox.engine_calls() == ""
    ), f"a skipped harness must start no container; engine stub saw:\n{sandbox.engine_calls()}"
    assert "container_tier.operator_only" not in result.stderr, detail


# ---------------------------------------------------------------------------
# Unauthorized: SKIPPED, exit 0, nothing built
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,script,args", _HARNESSES, ids=_HARNESS_IDS)
def test_harness_without_authorization_prints_one_skipped_line_and_exits_zero(
    tmp_path: pathlib.Path, name: str, script: pathlib.Path, args: list[str]
) -> None:
    sandbox = _Sandbox(tmp_path)
    result = sandbox.run(script, args)
    _assert_skipped(result, sandbox, script)
    assert "PGAI_CONTAINER_TIER=1 is not set" in result.stdout


@pytest.mark.parametrize("name,script,args", _HARNESSES, ids=_HARNESS_IDS)
def test_harness_inside_an_agent_wake_skips_even_with_authorization_set(
    tmp_path: pathlib.Path, name: str, script: pathlib.Path, args: list[str]
) -> None:
    sandbox = _Sandbox(tmp_path)
    result = sandbox.run(
        script,
        [*args, "--operator-approved"],
        PGAI_CONTAINER_TIER="1",
        PGAI_AGENT_WAKE_ACTIVE="1",
        PGAI_AGENT_ROLE="tester",
        PGAI_TASK_ID="TESTER-20990101-001-fixture",
    )
    _assert_skipped(result, sandbox, script)
    assert "inside an agent wake" in result.stdout
    lines = sandbox.docker_use_lines()
    assert len(lines) == 1, lines
    assert re.match(
        rf"^\d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\dZ SKIPPED actor=tester "
        rf"task=TESTER-20990101-001-fixture script={re.escape(script.name)} images=-$",
        lines[0],
    ), lines


def test_skipped_deep_verify_leaves_the_ledger_exactly_as_it_was(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.ledger.write_text(_PRIOR_LEDGER, encoding="utf-8")
    lane = sandbox.green_lane()
    result = sandbox.run(
        _DEEP_VERIFY,
        ["--project", _PROJECT, "--image", "all"],
        PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane),
    )
    _assert_skipped(result, sandbox, _DEEP_VERIFY)
    assert sandbox.recorded() == "", "the lane must not run when the call is skipped"
    assert sandbox.ledger.read_text(encoding="utf-8") == _PRIOR_LEDGER
    assert "verdict=" not in result.stdout


def test_skipped_deep_verify_with_no_prior_ledger_writes_none(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    lane = sandbox.green_lane()
    result = sandbox.run(_DEEP_VERIFY, ["--project", _PROJECT], PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane))
    _assert_skipped(result, sandbox, _DEEP_VERIFY)
    assert not sandbox.ledger.exists()


# ---------------------------------------------------------------------------
# Authorized: environment variable or --operator-approved
# ---------------------------------------------------------------------------


def test_deep_verify_runs_its_lane_with_operator_authorization(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    lane = sandbox.green_lane()
    result = sandbox.run(
        _DEEP_VERIFY,
        ["--project", _PROJECT, "--image", "debian"],
        PGAI_CONTAINER_TIER="1",
        PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane),
    )
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "lane-ran --image debian"
    assert "SKIPPED" not in result.stdout
    assert "Verdict: built" in sandbox.ledger.read_text(encoding="utf-8")


def test_operator_approved_flag_authorizes_like_the_environment_variable(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    lane = sandbox.green_lane()
    result = sandbox.run(
        _DEEP_VERIFY,
        ["--operator-approved", "--project", _PROJECT, "--image", "all"],
        PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane),
    )
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "lane-ran --image debian --image rocky9"
    assert "SKIPPED" not in result.stdout


# ---------------------------------------------------------------------------
# Image selection (run-cold-build-tests.sh reaching a stubbed pytest launch)
# ---------------------------------------------------------------------------


def test_image_debian_selects_only_debian(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(_COLD_RUNNER, ["--image", "debian"], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ --capture=no images=debian"
    assert sandbox.engine_calls() == ""


def test_image_all_selects_every_manifest_image(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(_COLD_RUNNER, ["--image", "all"], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ --capture=no images=debian rocky9"


def test_repeated_image_flags_select_each_named_image_in_manifest_order(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(_COLD_RUNNER, ["--image", "rocky9", "--image=debian"], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ --capture=no images=debian rocky9"


def test_no_image_flag_and_no_cfg_default_lists_the_images_and_builds_nothing(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(_COLD_RUNNER, [], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert "no container image selected -- nothing was built" in result.stdout
    assert "debian rocky9" in result.stdout
    assert "--image all" in result.stdout
    assert sandbox.recorded() == "", "nothing may be launched without an image selection"
    assert sandbox.engine_calls() == ""
    assert sandbox.docker_use_lines() == []


def test_cfg_default_selects_images_when_no_flag_is_given(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    sandbox.set_cfg_images("rocky9")
    result = sandbox.run(_COLD_RUNNER, [], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ --capture=no images=rocky9"


def test_image_flag_overrides_the_cfg_default(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    sandbox.set_cfg_images("rocky9")
    result = sandbox.run(_COLD_RUNNER, ["--image", "debian"], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ --capture=no images=debian"


def test_selection_inherited_from_a_parent_harness_applies_without_a_flag(tmp_path: pathlib.Path) -> None:
    """A harness run by another harness (e.g. under the cold lane) builds
    the images the outer run selected, carried in PGAI_CONTAINER_IMAGES,
    ahead of the kanban.cfg default."""
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    sandbox.set_cfg_images("debian")
    result = sandbox.run(_COLD_RUNNER, [], PGAI_CONTAINER_TIER="1", PGAI_CONTAINER_IMAGES="rocky9")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ --capture=no images=rocky9"


@pytest.mark.parametrize("name,script,args", _HARNESSES, ids=_HARNESS_IDS)
def test_unknown_image_name_refuses_naming_the_manifest_list(
    tmp_path: pathlib.Path, name: str, script: pathlib.Path, args: list[str]
) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    lane = sandbox.green_lane()
    bad_args = [a if a != "all" else "nope" for a in args]
    result = sandbox.run(script, bad_args, PGAI_CONTAINER_TIER="1", PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane))
    detail = _detail(result)
    assert result.returncode == _REFUSAL_EXIT, detail
    assert _UNKNOWN_IMAGE_KEY in result.stderr, detail
    assert "'nope'" in result.stderr and "Declared images: debian rocky9" in result.stderr, detail
    assert f"remedy:  PGAI_CONTAINER_TIER=1 bash {script}" in result.stderr, detail
    assert "--image all" in result.stderr.split("remedy:", 1)[1], detail
    assert sandbox.recorded() == "" and sandbox.engine_calls() == "", detail
    assert not sandbox.ledger.exists()
    lines = sandbox.docker_use_lines()
    assert len(lines) == 1 and " REFUSED actor=operator " in lines[0], lines
    assert "images=nope" in lines[0] and "reason=unknown-image" in lines[0], lines


def test_unknown_image_in_cfg_default_is_refused_too(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    sandbox.set_cfg_images("debian, ubuntu")
    result = sandbox.run(_COLD_RUNNER, [], PGAI_CONTAINER_TIER="1")
    assert result.returncode == _REFUSAL_EXIT, _detail(result)
    assert "kanban.cfg [release] container_images names 'ubuntu'" in result.stderr
    assert sandbox.recorded() == ""


def test_three_host_matrix_builds_each_container_host_once_per_selected_image(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    result = sandbox.run(
        _MATRIX,
        ["--host", "gitless", "--image", "debian", "--engine", "pgai-no-such-engine"],
        PGAI_CONTAINER_TIER="1",
    )
    # Past the gate, a missing engine is the harness's own not-attempted verdict.
    assert result.returncode == 1, _detail(result)
    assert "--- host: gitless@debian ---" in result.stdout
    assert "gitless@rocky9" not in result.stdout
    assert "not-attempted" in result.stdout
    assert sandbox.engine_calls() == ""


# ---------------------------------------------------------------------------
# docker-use log
# ---------------------------------------------------------------------------


def test_operator_run_writes_start_then_finish_with_images_duration_and_exit(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    lane = sandbox.green_lane()
    result = sandbox.run(
        _DEEP_VERIFY,
        ["--project", _PROJECT, "--image", "all"],
        PGAI_CONTAINER_TIER="1",
        PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane),
    )
    assert result.returncode == 0, _detail(result)
    lines = sandbox.docker_use_lines()
    assert len(lines) == 2, lines
    assert re.match(r"^\S+Z START actor=operator task=- script=deep-verify\.sh images=debian,rocky9$", lines[0]), lines
    assert re.match(
        r"^\S+Z FINISH actor=operator task=- script=deep-verify\.sh images=debian,rocky9 " r"duration=\d+s exit=0$",
        lines[1],
    ), lines


def test_nested_harness_run_is_recorded_once_by_the_outer_session(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(
        _DEEP_VERIFY,
        ["--project", _PROJECT, "--image", "debian"],
        PGAI_CONTAINER_TIER="1",
        PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(_COLD_RUNNER),
    )
    assert result.returncode == 0, _detail(result)
    assert "pytest-launched tests/cold/ --capture=no images=debian" in sandbox.recorded()
    events = [line.split()[1] for line in sandbox.docker_use_lines()]
    assert events == ["START", "FINISH"], sandbox.docker_use_lines()


def test_failing_run_records_its_exit_code_on_finish(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(_COLD_RUNNER, ["--image", "rocky9"], PGAI_CONTAINER_TIER="1", STAND_IN_PYTEST_EXIT="1")
    assert result.returncode == 1, _detail(result)
    lines = sandbox.docker_use_lines()
    assert [line.split()[1] for line in lines] == ["START", "FINISH"], lines
    assert "script=run-cold-build-tests.sh images=rocky9" in lines[1], lines
    assert lines[1].endswith(" exit=1"), lines


def test_argument_error_ends_the_run_before_any_docker_use_record(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(_COLD_RUNNER, ["--image", "debian", "--no-such-flag"], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 1, _detail(result)
    assert sandbox.docker_use_lines() == []
    assert sandbox.recorded() == ""


@pytest.mark.parametrize("authorized", [False, True], ids=["skips", "runs"])
def test_unwritable_docker_use_log_never_changes_the_harness_outcome(tmp_path: pathlib.Path, authorized: bool) -> None:
    sandbox = _Sandbox(tmp_path)
    # A plain file where the logs/ directory should be: nothing can be
    # created or appended beneath it.
    (sandbox.root / "logs").write_text("not a directory\n", encoding="utf-8")
    lane = sandbox.green_lane()
    extra = {"PGAI_DEEP_VERIFY_COLD_LANE_CMD": str(lane)}
    if authorized:
        extra["PGAI_CONTAINER_TIER"] = "1"
    result = sandbox.run(_DEEP_VERIFY, ["--project", _PROJECT, "--image", "all"], **extra)
    assert result.returncode == 0, _detail(result)
    assert "docker-use log: WARNING: cannot append to" in result.stderr
    if authorized:
        assert sandbox.recorded().startswith("lane-ran")
        assert "Verdict: built" in sandbox.ledger.read_text(encoding="utf-8")
    else:
        _assert_skipped(result, sandbox, _DEEP_VERIFY)
        assert sandbox.recorded() == ""


# ---------------------------------------------------------------------------
# Library functions
# ---------------------------------------------------------------------------


def test_harness_help_needs_no_authorization(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    result = sandbox.run(_COLD_RUNNER, ["--help"])
    assert result.returncode == 0, result.stderr
    assert "PGAI_CONTAINER_TIER=1" in result.stdout
    assert "--operator-approved" in result.stdout
    assert "--image NAME" in result.stdout
    assert sandbox.engine_calls() == ""


# (PGAI_CONTAINER_TIER, PGAI_AGENT_WAKE_ACTIVE, --operator-approved, exit status)
_AUTHORIZATION_CASES = [
    ("1", "", False, 0),
    ("1", "0", False, 0),
    ("", "", True, 0),
    ("", "", False, 1),
    ("yes", "", False, 1),
    ("true", "", False, 1),
    ("1", "1", False, 1),
    ("", "1", True, 1),
]


@pytest.mark.parametrize("value,wake,approved,expected", _AUTHORIZATION_CASES)
def test_authorized_requires_operator_approval_and_no_agent_wake(
    tmp_path: pathlib.Path, value: str, wake: str, approved: bool, expected: int
) -> None:
    extra = {"PGAI_CONTAINER_TIER": value}
    if wake:
        extra["PGAI_AGENT_WAKE_ACTIVE"] = wake
    take = "container_tier_take_args --operator-approved; " if approved else ""
    result = subprocess.run(
        ["bash", "-c", f'source "{_LIB}"; {take}container_tier_authorized'],
        capture_output=True,
        text=True,
        env=build_scrubbed_env(home_dir=tmp_path, extra_env=extra),
        timeout=30,
    )
    assert result.returncode == expected, result.stderr
    assert result.stdout == ""


def test_take_args_separates_container_tier_flags_from_harness_flags(tmp_path: pathlib.Path) -> None:
    script = (
        f'source "{_LIB}"; '
        "container_tier_take_args --project p --image debian --operator-approved --image=rocky9 -v; "
        'printf "rest=%s\\n" "${CONTAINER_TIER_REST_ARGS[*]}"; '
        'printf "images=%s\\n" "${CONTAINER_TIER_IMAGE_REQUEST[*]}"; '
        'printf "approved=%s\\n" "$CONTAINER_TIER_OPERATOR_APPROVED"; '
        'printf "pass=%s\\n" "${CONTAINER_TIER_PASSTHROUGH_ARGS[*]}"'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=build_scrubbed_env(home_dir=tmp_path),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "rest=--project p -v",
        "images=debian rocky9",
        "approved=1",
        "pass=--operator-approved --image debian --image rocky9",
    ]


def test_scrub_removes_the_authorization_from_the_shell_and_its_children(
    tmp_path: pathlib.Path,
) -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{_LIB}"; container_tier_scrub_env; ' 'bash -c \'printf "%s" "${PGAI_CONTAINER_TIER-unset}"\'',
        ],
        capture_output=True,
        text=True,
        env=build_scrubbed_env(home_dir=tmp_path, extra_env={"PGAI_CONTAINER_TIER": "1"}),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "unset"


@pytest.mark.parametrize(
    "value,wake,expected",
    [(value, wake, expected) for value, wake, approved, expected in _AUTHORIZATION_CASES if not approved],
)
def test_python_twin_of_authorized_agrees_with_the_library(value: str, wake: str, expected: int) -> None:
    """tests.fixtures.binary_guard.container_tier_authorized applies the
    library's rule to an environment (pytest has no --operator-approved)."""
    env = {"PGAI_CONTAINER_TIER": value}
    if wake:
        env["PGAI_AGENT_WAKE_ACTIVE"] = wake
    assert container_tier_authorized(env) is (expected == 0)


# ---------------------------------------------------------------------------
# The shipped-image test module honours the gate
# ---------------------------------------------------------------------------

_SHIPPED_IMAGE_MODULE = "tests/unit/test_entrypoint_shipped_image.py"
_SHIPPED_IMAGE_TESTS = (
    "test_missing_root_env_var_exits_one_naming_it",
    "test_unmounted_root_path_exits_one_naming_the_path",
    "test_mounted_root_lands_shell_in_that_directory",
)
_DOCKERFILE_TESTS = (
    "test_dockerfile_has_baked_entrypoint_declaration",
    "test_dockerfile_copies_entrypoint_with_exec_permission",
)
_SHIPPED_IMAGE_FLAVORS = ("debian", "rocky9")


def _run_test_modules(
    sandbox: _Sandbox, targets: tuple[str, ...], **extra: str
) -> tuple[subprocess.CompletedProcess, dict[str, tuple[str, str]]]:
    """Run *targets* (test files, as --files names them) as a nested pytest
    with the sandbox's recording docker/podman stubs first on PATH.

    Returns the completed process and, per test id (`<module>::name[param]`,
    module without its package path or .py), its outcome from the junit
    report: ("passed", ""), ("skipped", message) or ("failed", message).
    """
    junit = sandbox.bin.parent / "nested-junit.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *targets,
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            f"--basetemp={sandbox.bin.parent / 'nested-basetemp'}",
            f"--junitxml={junit}",
            "-q",
            "-rs",
        ],
        capture_output=True,
        text=True,
        env=sandbox.env(PYTHONDONTWRITEBYTECODE="1", **extra),
        cwd=str(_TEAM_DIR),
        timeout=300,
    )
    outcomes: dict[str, tuple[str, str]] = {}
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            test_id = f"{case.get('classname', '').rsplit('.', 1)[-1]}::{case.get('name', '')}"
            skipped = case.find("skipped")
            failed = case.find("failure") if case.find("failure") is not None else case.find("error")
            if skipped is not None:
                outcomes[test_id] = ("skipped", skipped.get("message", ""))
            elif failed is not None:
                outcomes[test_id] = ("failed", failed.get("message", ""))
            else:
                outcomes[test_id] = ("passed", "")
    return result, outcomes


def _ids(names: tuple[str, ...]) -> list[str]:
    module = pathlib.Path(_SHIPPED_IMAGE_MODULE).stem
    return [f"{module}::{name}[{flavor}]" for name in names for flavor in _SHIPPED_IMAGE_FLAVORS]


@pytest.mark.parametrize(
    "extra,why",
    [
        ({}, "PGAI_CONTAINER_TIER=1 is not set"),
        ({"PGAI_CONTAINER_TIER": "yes"}, "PGAI_CONTAINER_TIER=1 is not set"),
        (
            {"PGAI_CONTAINER_TIER": "1", "PGAI_AGENT_WAKE_ACTIVE": "1"},
            "inside an agent wake, and agents never run the container tier",
        ),
    ],
    ids=["unset", "not-exactly-1", "inside-agent-wake"],
)
def test_shipped_image_module_without_authorization_builds_and_runs_nothing(
    tmp_path: pathlib.Path, extra: dict[str, str], why: str
) -> None:
    """Named explicitly (as --files names it, which bypasses the unit
    runner's --ignore), the module calls no engine command at all and every
    image test skips naming the operator-only gate; the Dockerfile
    structural tests still run and pass."""
    sandbox = _Sandbox(tmp_path)
    result, outcomes = _run_test_modules(sandbox, (_SHIPPED_IMAGE_MODULE,), **extra)
    detail = f"{_detail(result)}\noutcomes: {outcomes}"
    assert result.returncode == 0, detail
    assert (
        sandbox.engine_calls() == ""
    ), f"an unauthorized run must call no engine command; engine stub saw:\n{sandbox.engine_calls()}"
    for test_id in _ids(_SHIPPED_IMAGE_TESTS):
        status, message = outcomes.get(test_id, ("absent", ""))
        assert status == "skipped", f"{test_id}: {status}\n{detail}"
        assert "container tier is operator-only" in message, f"{test_id}: {message}"
        assert _SKIP_KEY in message, f"{test_id}: {message}"
        assert why in message, f"{test_id}: {message}"
        assert 'operator_command="PGAI_CONTAINER_TIER=1 bash ' in message, f"{test_id}: {message}"
        assert "run-gated-tests.sh" in message, f"{test_id}: {message}"
    for test_id in _ids(_DOCKERFILE_TESTS):
        assert outcomes.get(test_id) == ("passed", ""), f"{test_id}\n{detail}"
    summary = [line for line in result.stdout.splitlines() if line.startswith("SKIPPED ")]
    assert summary and all(f"container tier is operator-only ({_SKIP_KEY})" in line for line in summary), detail


def test_shipped_image_module_with_authorization_reaches_its_daemon_check_and_build(
    tmp_path: pathlib.Path,
) -> None:
    """With the operator's authorization the fixture proceeds exactly as
    before: the daemon probe, then one build per flavor, then the image
    tests' runs and the teardown's removal -- all answered by the recording
    stub, so the image tests' verdicts against it are not under test."""
    sandbox = _Sandbox(tmp_path)
    result, outcomes = _run_test_modules(sandbox, (_SHIPPED_IMAGE_MODULE,), PGAI_CONTAINER_TIER="1")
    detail = f"{_detail(result)}\noutcomes: {outcomes}\nengine:\n{sandbox.engine_calls()}"
    calls = sandbox.engine_calls().splitlines()
    assert calls and calls[0] == "docker info", detail
    for flavor in _SHIPPED_IMAGE_FLAVORS:
        tag = f"pgai-kanban-entrypoint-shipped-{flavor}:test"
        assert any(c.startswith(f"docker build --tag {tag} --file ") for c in calls), detail
        assert any(c.startswith("docker run ") and c.endswith(tag) for c in calls), detail
        assert f"docker image rm --force {tag}" in calls, detail
    for test_id in _ids(_SHIPPED_IMAGE_TESTS):
        status, message = outcomes.get(test_id, ("absent", ""))
        assert status != "absent", f"{test_id}\n{detail}"
        assert _SKIP_KEY not in message, f"{test_id}: {message}"
    for test_id in _ids(_DOCKERFILE_TESTS):
        assert outcomes.get(test_id) == ("passed", ""), f"{test_id}\n{detail}"


# ---------------------------------------------------------------------------
# The team/tests/cold modules honour the gate in-file
# ---------------------------------------------------------------------------

_COLD_DIR = "tests/cold"
# Every test that can reach a container engine, one per gate site: the
# tests behind each image-building fixture, and each test that calls the
# engine itself. Parametrized ids (`name[...]`) all count.
_COLD_GATED_TESTS = (
    ("test_dashboard_smoke_container", "test_create_sh_no_tmux_produces_no_command_not_found"),
    ("test_dashboard_operator_window", "test_window0_section_headers_match_fixture"),
    ("test_dashboard_operator_window", "test_kill_sh_destroys_entire_session"),
    ("test_docker_build_fixture", "test_image_builds_without_error"),
    ("test_docker_build_fixture", "test_build_without_pinned_versions_fails_naming_the_missing_arg"),
    ("test_docker_cold_build", "test_image_builds_without_error"),
    ("test_docker_cold_build", "test_broken_run_step_fails_loud_on_reachable_base_image"),
    ("test_docker_cold_build", "test_unreachable_base_image_skips_naming_the_probe"),
    ("test_docker_cold_build", "test_bad_codex_version_fails_the_smoke_check_layer_naming_codex"),
    ("test_docker_cold_build", "test_real_flavor_build_without_pins_fails_naming_the_unset_arg"),
    ("test_docker_cold_build", "test_real_flavor_bad_codex_version_fails_the_smoke_check_layer_naming_codex"),
    ("test_strangers_path_check_harness", "test_seeded_break_in_install_step_fails_and_cites_doc_and_line"),
)
# The cold modules whose image fixtures probe the daemon before building.
_COLD_DAEMON_CHECK_MODULES = (
    "tests/cold/test_dashboard_smoke_container.py",
    "tests/cold/test_dashboard_operator_window.py",
    "tests/cold/test_docker_build_fixture.py",
    "tests/cold/test_docker_cold_build.py",
)


@pytest.mark.parametrize(
    "extra,why",
    [
        ({}, "PGAI_CONTAINER_TIER=1 is not set"),
        (
            {"PGAI_CONTAINER_TIER": "1", "PGAI_AGENT_WAKE_ACTIVE": "1"},
            "inside an agent wake, and agents never run the container tier",
        ),
    ],
    ids=["unset", "inside-agent-wake"],
)
def test_cold_modules_without_authorization_call_no_engine_and_skip_naming_the_gate(
    tmp_path: pathlib.Path, extra: dict[str, str], why: str
) -> None:
    """Collected outside run-cold-build-tests.sh, every team/tests/cold
    module calls no engine command at all -- `docker info` included -- and
    every test that could reach an engine skips naming the operator-only
    gate; tests that only read files or drive a stub engine still pass."""
    sandbox = _Sandbox(tmp_path)
    result, outcomes = _run_test_modules(sandbox, (_COLD_DIR,), **extra)
    detail = f"{_detail(result)}\noutcomes: {outcomes}"
    assert result.returncode == 0, detail
    assert (
        sandbox.engine_calls() == ""
    ), f"an unauthorized run must call no engine command; engine stub saw:\n{sandbox.engine_calls()}"
    for module, name in _COLD_GATED_TESTS:
        ids = [test_id for test_id in outcomes if test_id.split("[", 1)[0] == f"{module}::{name}"]
        assert ids, f"{module}::{name} was not collected\n{detail}"
        for test_id in ids:
            status, message = outcomes[test_id]
            assert status == "skipped", f"{test_id}: {status}\n{detail}"
            assert _SKIP_KEY in message and why in message, f"{test_id}: {message}"
            assert "run-cold-build-tests.sh --image all" in message, f"{test_id}: {message}"
    for test_id, (status, message) in outcomes.items():
        assert status == "passed" or _SKIP_KEY in message, f"{test_id}: {status} {message}\n{detail}"


@pytest.mark.parametrize("module", _COLD_DAEMON_CHECK_MODULES, ids=lambda m: pathlib.Path(m).stem)
def test_cold_module_with_authorization_reaches_its_daemon_check(tmp_path: pathlib.Path, module: str) -> None:
    """With the operator's authorization the gate returns and the module
    proceeds exactly as before: its first engine command is the existing
    daemon probe. Every later engine command is answered by the recording
    stub, so the verdicts against it are not under test."""
    sandbox = _Sandbox(tmp_path)
    result, outcomes = _run_test_modules(sandbox, (module,), PGAI_CONTAINER_TIER="1")
    calls = sandbox.engine_calls().splitlines()
    detail = f"{_detail(result)}\noutcomes: {outcomes}\nengine:\n{sandbox.engine_calls()}"
    assert calls and calls[0] == "docker info", detail
    assert any(c.startswith("docker build ") for c in calls), detail
    assert outcomes, detail
    assert all(_SKIP_KEY not in message for _status, message in outcomes.values()), detail
