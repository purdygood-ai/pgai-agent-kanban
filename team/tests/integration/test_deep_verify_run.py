"""
test_deep_verify_run.py
=========================
End-to-end integration tests for team/scripts/deep-verify.sh and
team/scripts/cold-build-now.sh -- the DEEP-VERIFY tier's on-demand runner
and its narrow operator alias.

These tests invoke the real scripts via subprocess against a temporary
kanban root (PGAI_AGENT_KANBAN_ROOT_PATH redirected to tmp_path). No test
invokes a real container build: the cold lane is stubbed via the
PGAI_DEEP_VERIFY_COLD_LANE_CMD seam deep-verify.sh documents in its own
header, the same idiom team/scripts/lib/safe_overwrite.sh uses for
PGAI_CRONTAB_CMD.

Coverage:
  - A fixture whose stubbed lane exits 0 with every pair built: deep-verify.sh
    exits 0 and a record appears at the documented per-project ledger path
    carrying the timestamp, the version and commit exercised, and a verdict
    naming the built case.
  - A fixture whose stubbed lane exits 1 with a Dockerfile-step failure:
    records the failed-to-build verdict and names the failing engine/flavor
    pair; deep-verify.sh exits 1.
  - A fixture whose stubbed lane exits 1 because a non-build cold test
    failed while every image built: records the built verdict, files a bug
    naming the failing test's file and test id, and exits with the code
    deep-verify.sh --help documents for that case (1).
  - A fixture whose stubbed lane exits 0 with every pair skipped for an
    unavailable engine: records the could-not-attempt verdict and names the
    unavailable resource; deep-verify.sh exits 0. The two records (built vs.
    could-not-attempt) are distinguishable by field value alone.
  - An engine-less run (every pair narrate-skipped, nothing built) whose
    lane exits 1 because a non-build cold test failed: records the
    could-not-attempt verdict, files one bug naming the failing test that
    does not claim the builds were recorded as built, and exits 1; a second
    identical run cites that bug instead of filing a duplicate.
  - An engine-less run whose lane exits 0 (the other cold tests passed),
    and a lane that exits 2 (pytest not installed): each records
    could-not-attempt, files no bug, and exits 0.
  - deep-verify.sh --project <unregistered> refuses dual-channel (a stable
    error key, a plain-language message, a runnable remedy) and writes no
    ledger record at all.
  - cold-build-now.sh --project <name> produces the same record shape as
    deep-verify.sh for the same stubbed outcome -- it is a thin alias, not a
    second implementation.
  - --help on both scripts prints usage and exits 0.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

from tests.fixtures.deep_verify_lane_output import (
    ENGINE_UNAVAILABLE_FAILING_TEST_FILE,
    ENGINE_UNAVAILABLE_FAILING_TEST_ID,
    ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT,
    ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT,
    NON_BUILD_TEST_FAILURE_OUTPUT,
    NON_BUILD_TEST_FILE,
    NON_BUILD_TEST_ID,
)
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's own location.
# test file:  team/tests/integration/test_deep_verify_run.py
# team/:      .../team
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_DEEP_VERIFY_SCRIPT = _SCRIPTS_DIR / "deep-verify.sh"
_COLD_BUILD_NOW_SCRIPT = _SCRIPTS_DIR / "cold-build-now.sh"

_STUB_GREEN = """\
#!/usr/bin/env bash
echo "[cold-build] [docker] [claude-code] Image built successfully: tag:test"
echo "[cold-build] [podman] [claude-code] Image built successfully: tag:test"
echo "Cold-build lane PASSED"
exit 0
"""

_STUB_DOCKERFILE_FAILURE = """\
#!/usr/bin/env bash
echo "E       Failed: docker build of docker/claude-code/Dockerfile failed (exit 1) at step: RUN npm ci; base-image reachability was confirmed for every FROM in this Dockerfile before the build started, so this is a Dockerfile defect, not an environmental gap" 1>&2
echo "Cold-build lane FAILED (exit code: 1)" 1>&2
exit 1
"""

_STUB_ALL_SKIPPED = """\
#!/usr/bin/env bash
echo "[cold-build] SKIP — claude-code base-image reachability probe failed under docker: no such host; treating this as an environmental network gap rather than a Dockerfile defect"
echo "Cold-build lane: all (engine, flavor) pairs skipped (see narration above) — exit 0"
exit 0
"""

# The lane's own pytest-missing refusal: it exits 2 before running any test.
_STUB_PYTEST_MISSING = """\
#!/usr/bin/env bash
echo "FAIL: pytest is not installed in this Python environment." 1>&2
echo "Rule: this runner requires pytest as a hard dependency; it cannot execute" 1>&2
echo "the cold-build lane without it." 1>&2
exit 2
"""


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_kanban_root(tmp_path: pathlib.Path, *, register_project: str | None = "demo") -> pathlib.Path:
    root = tmp_path / "kanban_root"
    (root / "projects").mkdir(parents=True, exist_ok=True)
    if register_project:
        (root / "projects.cfg").write_text(
            f"[project:{register_project}]\npriority = 1\n", encoding="utf-8"
        )
    else:
        (root / "projects.cfg").write_text("", encoding="utf-8")
    return root


def _write_stub(tmp_path: pathlib.Path, name: str, contents: str) -> pathlib.Path:
    stub = tmp_path / name
    stub.write_text(contents, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _run_deep_verify(
    kanban_root: pathlib.Path,
    project: str | None,
    *,
    stub_lane: pathlib.Path | None = None,
    script: pathlib.Path = _DEEP_VERIFY_SCRIPT,
    extra_args: list | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_PROJECT_NAME", None)
    if stub_lane is not None:
        env["PGAI_DEEP_VERIFY_COLD_LANE_CMD"] = str(stub_lane)
        # The stubbed lane stands in for the container tier, which runs only
        # with operator authorization and never inside an agent wake.
        scrub_agent_scoped_env(env)
        env["PGAI_CONTAINER_TIER"] = "1"

    args = ["bash", str(script)]
    if project is not None:
        args += ["--project", project]
    if extra_args:
        args += extra_args
    if stub_lane is not None:
        # An operator run names the images it builds; without a selection
        # the harness lists the images and runs nothing.
        args += ["--image", "all"]

    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)


def _ledger_path(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    return kanban_root / "projects" / project / "deep-verify-ledger.md"


def _bugs_dir(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    return kanban_root / "projects" / project / "bugs"


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_deep_verify_help_prints_usage_and_exits_zero(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    result = _run_deep_verify(kanban_root, None, extra_args=["--help"])
    assert result.returncode == 0
    assert "Usage: deep-verify.sh" in result.stdout


def test_cold_build_now_help_prints_usage_and_exits_zero(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    result = _run_deep_verify(kanban_root, None, script=_COLD_BUILD_NOW_SCRIPT, extra_args=["--help"])
    assert result.returncode == 0
    assert "Usage: cold-build-now.sh" in result.stdout


# ---------------------------------------------------------------------------
# Built verdict
# ---------------------------------------------------------------------------


def test_green_run_exits_zero_and_records_built_verdict_with_tree_state(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_green.sh", _STUB_GREEN)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 0, result.stderr
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "## Deep-Verify Run:" in ledger_text
    assert "- Verdict: built" in ledger_text
    assert re.search(r"- Commit: \S+", ledger_text)
    assert re.search(r"## Deep-Verify Run: \S+ — demo — \S+", ledger_text)


# ---------------------------------------------------------------------------
# Failed-to-build verdict
# ---------------------------------------------------------------------------


def test_dockerfile_failure_records_failed_to_build_naming_the_pair(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_fail.sh", _STUB_DOCKERFILE_FAILURE)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 1
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: failed-to-build" in ledger_text
    assert "docker/claude-code" in ledger_text
    assert "RUN npm ci" in ledger_text


def test_dockerfile_failure_files_exactly_one_bug_naming_engine_flavor_step_and_tree_state(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_fail.sh", _STUB_DOCKERFILE_FAILURE)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 1
    bug_files = sorted(_bugs_dir(kanban_root, "demo").glob("BUG-*.md"))
    assert len(bug_files) == 1
    content = bug_files[0].read_text(encoding="utf-8")
    assert "docker" in content
    assert "claude-code" in content
    assert "RUN npm ci" in content
    assert re.search(r"Commit:\s*\S+", content)
    assert "## Status\nopen" in content
    assert f"BUG-{bug_files[0].name.split('-')[1]}" in result.stdout


def test_built_run_files_no_bug(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_green.sh", _STUB_GREEN)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 0, result.stderr
    assert not _bugs_dir(kanban_root, "demo").exists() or not list(
        _bugs_dir(kanban_root, "demo").glob("BUG-*.md")
    )


def test_second_red_run_against_the_same_failing_pair_cites_the_existing_bug_instead_of_duplicating(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_fail.sh", _STUB_DOCKERFILE_FAILURE)

    first = _run_deep_verify(kanban_root, "demo", stub_lane=stub)
    assert first.returncode == 1
    bug_files_after_first = sorted(_bugs_dir(kanban_root, "demo").glob("BUG-*.md"))
    assert len(bug_files_after_first) == 1
    filed_bug_id = bug_files_after_first[0].name.split("-cold-build")[0]

    second = _run_deep_verify(kanban_root, "demo", stub_lane=stub)
    assert second.returncode == 1

    bug_files_after_second = sorted(_bugs_dir(kanban_root, "demo").glob("BUG-*.md"))
    assert bug_files_after_second == bug_files_after_first, (
        "a second red run against the same failing pair must not file a duplicate bug"
    )
    assert filed_bug_id in second.stdout


# ---------------------------------------------------------------------------
# Built verdict with a failing non-build cold test
# ---------------------------------------------------------------------------


def _write_captured_output_stub(tmp_path: pathlib.Path, output_text: str, exit_code: int) -> pathlib.Path:
    """A stub lane that replays *output_text* verbatim and exits *exit_code*."""
    captured = tmp_path / "captured_lane_output.txt"
    captured.write_text(output_text, encoding="utf-8")
    return _write_stub(
        tmp_path,
        "stub_replay.sh",
        f"#!/usr/bin/env bash\ncat '{captured}'\nexit {exit_code}\n",
    )


def test_non_build_test_failure_records_built_files_a_bug_naming_the_test_and_exits_as_documented(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_captured_output_stub(tmp_path, NON_BUILD_TEST_FAILURE_OUTPUT, 1)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)
    help_text = " ".join(
        _run_deep_verify(kanban_root, None, extra_args=["--help"]).stdout.split()
    )

    assert "1 = verdict is failed-to-build, or verdict is built but a non-build cold test failed" in help_text
    assert result.returncode == 1, result.stdout + result.stderr
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: built" in ledger_text
    assert "- Verdict: failed-to-build" not in ledger_text
    bug_files = sorted(_bugs_dir(kanban_root, "demo").glob("BUG-*.md"))
    assert len(bug_files) == 1
    content = bug_files[0].read_text(encoding="utf-8")
    assert f"team/{NON_BUILD_TEST_FILE}" in content
    assert NON_BUILD_TEST_ID in content
    assert "no structured engine/flavor/step detail" not in content
    assert bug_files[0].name.split("-cold-test")[0] in result.stdout


# ---------------------------------------------------------------------------
# Could-not-attempt verdict
# ---------------------------------------------------------------------------


def test_all_pairs_skipped_records_could_not_attempt_naming_unavailable_resource(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_skip.sh", _STUB_ALL_SKIPPED)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 0, result.stderr
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: could-not-attempt" in ledger_text
    assert "no such host" in ledger_text


def test_all_pairs_skipped_files_no_bug(tmp_path: pathlib.Path) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_skip.sh", _STUB_ALL_SKIPPED)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 0, result.stderr
    assert not _bugs_dir(kanban_root, "demo").exists() or not list(
        _bugs_dir(kanban_root, "demo").glob("BUG-*.md")
    )


def test_built_and_could_not_attempt_records_distinguishable_without_reading_captured_output(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    green_stub = _write_stub(tmp_path, "stub_green.sh", _STUB_GREEN)
    skip_stub = _write_stub(tmp_path, "stub_skip.sh", _STUB_ALL_SKIPPED)

    _run_deep_verify(kanban_root, "demo", stub_lane=green_stub)
    green_verdict = re.search(
        r"- Verdict: (\S+)", _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    ).group(1)

    _run_deep_verify(kanban_root, "demo", stub_lane=skip_stub)
    records = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    verdicts = re.findall(r"- Verdict: (\S+)", records)

    assert verdicts == ["built", "could-not-attempt"]
    assert green_verdict != verdicts[-1]


# ---------------------------------------------------------------------------
# Could-not-attempt verdict from an engine-less run (every pair
# narrate-skipped, nothing built, the engine-free cold modules still ran)
# ---------------------------------------------------------------------------


def _bug_files(kanban_root: pathlib.Path, project: str) -> list:
    bugs_dir = _bugs_dir(kanban_root, project)
    return sorted(bugs_dir.glob("BUG-*.md")) if bugs_dir.exists() else []


def test_engine_less_run_with_a_failing_non_build_test_records_could_not_attempt_files_a_bug_naming_the_test_and_exits_one(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_captured_output_stub(tmp_path, ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT, 1)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 1, result.stdout + result.stderr
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: could-not-attempt" in ledger_text
    assert "- Verdict: built" not in ledger_text
    bug_files = _bug_files(kanban_root, "demo")
    assert len(bug_files) == 1, [p.name for p in bug_files]
    content = bug_files[0].read_text(encoding="utf-8")
    assert f"team/{ENGINE_UNAVAILABLE_FAILING_TEST_FILE}" in content
    assert ENGINE_UNAVAILABLE_FAILING_TEST_ID in content
    assert "records the builds as `built`" not in content
    assert bug_files[0].name.split("-cold-test")[0] in result.stdout


def test_second_engine_less_run_with_the_same_failing_test_cites_the_existing_bug_instead_of_duplicating(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_captured_output_stub(tmp_path, ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT, 1)

    first = _run_deep_verify(kanban_root, "demo", stub_lane=stub)
    assert first.returncode == 1, first.stdout + first.stderr
    bug_files_after_first = _bug_files(kanban_root, "demo")
    assert len(bug_files_after_first) == 1, [p.name for p in bug_files_after_first]
    filed_bug_id = bug_files_after_first[0].name.split("-cold-test")[0]

    second = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert second.returncode == 1, second.stdout + second.stderr
    assert _bug_files(kanban_root, "demo") == bug_files_after_first, (
        "a second engine-less run with the same failing test must not file a duplicate bug"
    )
    assert f"citing existing {filed_bug_id}" in second.stdout


def test_engine_less_run_with_every_cold_test_passing_records_could_not_attempt_files_no_bug_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_captured_output_stub(tmp_path, ENGINE_UNAVAILABLE_OTHER_TESTS_PASSED_OUTPUT, 0)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 0, result.stdout + result.stderr
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: could-not-attempt" in ledger_text
    assert _bug_files(kanban_root, "demo") == []


def test_lane_without_pytest_records_could_not_attempt_files_no_bug_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_pytest_missing.sh", _STUB_PYTEST_MISSING)

    result = _run_deep_verify(kanban_root, "demo", stub_lane=stub)

    assert result.returncode == 0, result.stdout + result.stderr
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: could-not-attempt" in ledger_text
    assert "pytest is not installed" in ledger_text
    assert _bug_files(kanban_root, "demo") == []


# ---------------------------------------------------------------------------
# Unregistered project refusal
# ---------------------------------------------------------------------------


def test_unregistered_project_refuses_dual_channel_and_writes_no_record(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path, register_project=None)
    stub = _write_stub(tmp_path, "stub_green.sh", _STUB_GREEN)

    result = _run_deep_verify(kanban_root, "nonexistent-project", stub_lane=stub)

    assert result.returncode == 1
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr
    assert "error:   project_resolve.not_found" in result.stderr
    assert not _ledger_path(kanban_root, "nonexistent-project").exists()


# ---------------------------------------------------------------------------
# cold-build-now.sh delegation
# ---------------------------------------------------------------------------


def test_cold_build_now_produces_the_same_record_shape_as_deep_verify(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    stub = _write_stub(tmp_path, "stub_green.sh", _STUB_GREEN)

    result = _run_deep_verify(
        kanban_root, "demo", stub_lane=stub, script=_COLD_BUILD_NOW_SCRIPT
    )

    assert result.returncode == 0, result.stderr
    ledger_text = _ledger_path(kanban_root, "demo").read_text(encoding="utf-8")
    assert "- Verdict: built" in ledger_text


def test_cold_build_now_missing_project_flag_refuses_without_delegating(
    tmp_path: pathlib.Path,
) -> None:
    kanban_root = _make_kanban_root(tmp_path)
    result = _run_deep_verify(
        kanban_root, None, script=_COLD_BUILD_NOW_SCRIPT
    )
    assert result.returncode == 1
    assert "--project is required" in result.stderr
