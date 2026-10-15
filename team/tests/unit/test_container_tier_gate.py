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
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

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
        (self.root / "projects.cfg").write_text(
            f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8"
        )
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
        (self.root / "kanban.cfg").write_text(
            f"[release]\ncontainer_images = {value}\n", encoding="utf-8"
        )

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
    assert sandbox.engine_calls() == "", (
        f"a skipped harness must start no container; engine stub saw:\n{sandbox.engine_calls()}"
    )
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
    result = sandbox.run(
        _DEEP_VERIFY, ["--project", _PROJECT], PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane)
    )
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
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ images=debian"
    assert sandbox.engine_calls() == ""


def test_image_all_selects_every_manifest_image(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(_COLD_RUNNER, ["--image", "all"], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ images=debian rocky9"


def test_repeated_image_flags_select_each_named_image_in_manifest_order(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(
        _COLD_RUNNER, ["--image", "rocky9", "--image=debian"], PGAI_CONTAINER_TIER="1"
    )
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ images=debian rocky9"


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
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ images=rocky9"


def test_image_flag_overrides_the_cfg_default(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    sandbox.set_cfg_images("rocky9")
    result = sandbox.run(_COLD_RUNNER, ["--image", "debian"], PGAI_CONTAINER_TIER="1")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ images=debian"


def test_selection_inherited_from_a_parent_harness_applies_without_a_flag(tmp_path: pathlib.Path) -> None:
    """A harness run by another harness (e.g. under the cold lane) builds
    the images the outer run selected, carried in PGAI_CONTAINER_IMAGES,
    ahead of the kanban.cfg default."""
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    sandbox.set_cfg_images("debian")
    result = sandbox.run(_COLD_RUNNER, [], PGAI_CONTAINER_TIER="1", PGAI_CONTAINER_IMAGES="rocky9")
    assert result.returncode == 0, _detail(result)
    assert sandbox.recorded().strip() == "pytest-launched tests/cold/ images=rocky9"


@pytest.mark.parametrize("name,script,args", _HARNESSES, ids=_HARNESS_IDS)
def test_unknown_image_name_refuses_naming_the_manifest_list(
    tmp_path: pathlib.Path, name: str, script: pathlib.Path, args: list[str]
) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    lane = sandbox.green_lane()
    bad_args = [a if a != "all" else "nope" for a in args]
    result = sandbox.run(
        script, bad_args, PGAI_CONTAINER_TIER="1", PGAI_DEEP_VERIFY_COLD_LANE_CMD=str(lane)
    )
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
    assert re.match(
        r"^\S+Z START actor=operator task=- script=deep-verify\.sh images=debian,rocky9$", lines[0]
    ), lines
    assert re.match(
        r"^\S+Z FINISH actor=operator task=- script=deep-verify\.sh images=debian,rocky9 "
        r"duration=\d+s exit=0$",
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
    assert "pytest-launched tests/cold/ images=debian" in sandbox.recorded()
    events = [line.split()[1] for line in sandbox.docker_use_lines()]
    assert events == ["START", "FINISH"], sandbox.docker_use_lines()


def test_failing_run_records_its_exit_code_on_finish(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(
        _COLD_RUNNER, ["--image", "rocky9"], PGAI_CONTAINER_TIER="1", STAND_IN_PYTEST_EXIT="1"
    )
    assert result.returncode == 1, _detail(result)
    lines = sandbox.docker_use_lines()
    assert [line.split()[1] for line in lines] == ["START", "FINISH"], lines
    assert "script=run-cold-build-tests.sh images=rocky9" in lines[1], lines
    assert lines[1].endswith(" exit=1"), lines


def test_argument_error_ends_the_run_before_any_docker_use_record(tmp_path: pathlib.Path) -> None:
    sandbox = _Sandbox(tmp_path)
    sandbox.use_python3_stand_in()
    result = sandbox.run(
        _COLD_RUNNER, ["--image", "debian", "--no-such-flag"], PGAI_CONTAINER_TIER="1"
    )
    assert result.returncode == 1, _detail(result)
    assert sandbox.docker_use_lines() == []
    assert sandbox.recorded() == ""


@pytest.mark.parametrize("authorized", [False, True], ids=["skips", "runs"])
def test_unwritable_docker_use_log_never_changes_the_harness_outcome(
    tmp_path: pathlib.Path, authorized: bool
) -> None:
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


@pytest.mark.parametrize(
    "value,wake,approved,expected",
    [
        ("1", "", False, 0),
        ("1", "0", False, 0),
        ("", "", True, 0),
        ("", "", False, 1),
        ("yes", "", False, 1),
        ("true", "", False, 1),
        ("1", "1", False, 1),
        ("", "1", True, 1),
    ],
)
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
            f'source "{_LIB}"; container_tier_scrub_env; '
            'bash -c \'printf "%s" "${PGAI_CONTAINER_TIER-unset}"\'',
        ],
        capture_output=True,
        text=True,
        env=build_scrubbed_env(home_dir=tmp_path, extra_env={"PGAI_CONTAINER_TIER": "1"}),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "unset"
