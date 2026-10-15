"""
test_deep_verify_bug_filer.py
===============================
Unit tests for pgai_agent_kanban.deep_verify.bug_filer -- the DEEP-VERIFY
tier's failure-path bug filer (team/scripts/deep-verify.sh's failed-to-build
path, via team/scripts/lib/deep_verify_bug_filer.sh).

Coverage:
  - A failed-to-build run's captured output produces exactly one filed bug
    naming the engine, the flavor, the failing step, and the tree state
    (version, commit) exercised.
  - A second run against the exact same failing (engine, flavor) pair set,
    with the first bug still open, cites the existing bug instead of filing
    a duplicate.
  - A run whose failing pair set differs from an existing open bug's
    recorded set (a new pair, or a different pair entirely) files a fresh
    bug rather than treating it as the same defect.
  - A bug whose Status is no longer open/running (done, wont-do) is not
    treated as covering the pair -- a fresh bug is filed.
  - Filed bugs carry ## Status: open (bundling-eligible) and a Filed By line
    following the self-documenting label convention.
  - Captured output with no structured engine/flavor/step detail still
    files a bug (outcome=no-pairs) rather than silently dropping a real
    tree defect.
  - extract_failing_pairs() returns structured (engine, flavor, step)
    triples, the building block classify.py's own formatted detail string
    is now built from.
  - Captured output where every image built and one non-build cold test
    failed files a bug naming the failing test's file and test id (not the
    "no structured detail" text), keyed on the failing-test set: a repeat
    run cites it, the CLI reports the test on its tests= line.
  - Captured output where no pair built, every pair was narrate-skipped and
    one non-build cold test failed files a bug naming the failing test,
    and the report does not claim the builds were recorded as built.

Every assertion is against the pure file_or_cite_bug() / extract_failing_
pairs() functions operating on a tmp_path bugs/ directory -- no subprocess,
no container engine. tmp_path is redirected under
$PGAI_AGENT_KANBAN_TEMP_DIR by team/tests/conftest.py's pytest_configure, so
every fixture directory this file creates already satisfies the framework's
temp-file hygiene rule without any extra plumbing here.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import pathlib

from pgai_agent_kanban.deep_verify.bug_filer import (
    FilingResult,
    file_or_cite_bug,
    find_existing_bug_for_pairs,
    main,
)
from pgai_agent_kanban.deep_verify.classify import FailingPair, extract_failing_pairs
from tests.fixtures.deep_verify_lane_output import (
    ENGINE_UNAVAILABLE_FAILING_TEST_FILE,
    ENGINE_UNAVAILABLE_FAILING_TEST_ID,
    ENGINE_UNAVAILABLE_FAILING_TEST_NODEID,
    ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT,
    NON_BUILD_TEST_FAILURE_OUTPUT,
    NON_BUILD_TEST_FILE,
    NON_BUILD_TEST_ID,
    NON_BUILD_TEST_NODEID,
)

_DOCKERFILE_FAILURE_OUTPUT = (
    "E       Failed: docker build of docker/claude-code/Dockerfile failed "
    "(exit 1) at step: RUN npm ci; base-image reachability was confirmed "
    "for every FROM in this Dockerfile before the build started, so this "
    "is a Dockerfile defect, not an environmental gap\n"
    "Cold-build lane FAILED (exit code: 1)\n"
)

_UNSTRUCTURED_FAILURE_OUTPUT = "Cold-build lane FAILED (exit code: 1)\n"


def _write_bug(
    bugs_dir: pathlib.Path,
    bug_id: str,
    *,
    status: str = "open",
    pairs: tuple = ("docker/claude-code",),
    slug: str = "cold-build-claude-code-failure",
) -> pathlib.Path:
    """Write a minimal well-formed bug file directly (bypassing the filer)
    so dedup tests can set up "an existing bug already covers this pair"
    without depending on file_or_cite_bug() to have created it first."""
    pairs_block = "\n".join(pairs)
    text = (
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        "**Filed By:** deep-verify (autonomous) — test fixture.\n"
        "**Date:** 2026-09-01\n"
        "**Severity:** critical\n\n"
        f"## Status\n{status}\n\n"
        "## Category\ndeep-verify\n\n"
        "---\n\n"
        "## Symptom\ntest fixture\n\n"
        "---\n\n"
        "## Deep-Verify Pairs\n"
        f"{pairs_block}\n"
    )
    path = bugs_dir / f"{bug_id}-{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# extract_failing_pairs()
# ---------------------------------------------------------------------------


def test_extract_failing_pairs_returns_structured_engine_flavor_step() -> None:
    pairs = extract_failing_pairs(_DOCKERFILE_FAILURE_OUTPUT)
    assert pairs == [FailingPair(engine="docker", flavor="claude-code", step="RUN npm ci")]


def test_extract_failing_pairs_empty_for_unstructured_output() -> None:
    assert extract_failing_pairs(_UNSTRUCTURED_FAILURE_OUTPUT) == []


# ---------------------------------------------------------------------------
# file_or_cite_bug() -- first filing
# ---------------------------------------------------------------------------


def test_first_failure_files_exactly_one_bug_naming_engine_flavor_step_and_tree_state(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.12",
        commit="abc1234",
        output_text=_DOCKERFILE_FAILURE_OUTPUT,
    )

    assert result.outcome == "filed"
    assert result.bug_id is not None and result.bug_id.startswith("BUG-")
    filed_files = sorted(bugs_dir.glob("BUG-*.md"))
    assert len(filed_files) == 1
    content = filed_files[0].read_text(encoding="utf-8")
    assert "docker" in content
    assert "claude-code" in content
    assert "RUN npm ci" in content
    assert "v2.9.12" in content
    assert "abc1234" in content
    assert "## Status\nopen" in content
    assert "**Filed By:** deep-verify (autonomous)" in content


def test_filed_bug_severity_is_one_of_the_canonical_vocabulary(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.12",
        commit="abc1234",
        output_text=_DOCKERFILE_FAILURE_OUTPUT,
    )
    content = next(bugs_dir.glob("BUG-*.md")).read_text(encoding="utf-8")
    assert any(
        f"**Severity:** {sev}" in content
        for sev in ("critical", "high", "medium", "low")
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_second_run_against_same_failing_pair_cites_existing_open_bug_and_files_no_duplicate(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0044", status="open", pairs=("docker/claude-code",))

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.13",
        commit="def5678",
        output_text=_DOCKERFILE_FAILURE_OUTPUT,
    )

    assert result.outcome == "duplicate"
    assert result.bug_id == "BUG-0044"
    assert result.bug_path is None
    assert sorted(bugs_dir.glob("BUG-*.md")) == [bugs_dir / "BUG-0044-cold-build-claude-code-failure.md"]


def test_second_run_against_same_failing_pair_cites_existing_running_bug_too(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0044", status="running", pairs=("docker/claude-code",))

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.13",
        commit="def5678",
        output_text=_DOCKERFILE_FAILURE_OUTPUT,
    )

    assert result.outcome == "duplicate"
    assert result.bug_id == "BUG-0044"


def test_existing_bug_for_a_different_pair_does_not_suppress_a_fresh_filing(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0044", status="open", pairs=("podman/codex",))

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.13",
        commit="def5678",
        output_text=_DOCKERFILE_FAILURE_OUTPUT,
    )

    assert result.outcome == "filed"
    assert result.bug_id != "BUG-0044"


def test_existing_closed_bug_for_the_same_pair_does_not_suppress_a_fresh_filing(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0044", status="done", pairs=("docker/claude-code",))

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.13",
        commit="def5678",
        output_text=_DOCKERFILE_FAILURE_OUTPUT,
    )

    assert result.outcome == "filed"
    assert result.bug_id != "BUG-0044"


def test_find_existing_bug_for_pairs_returns_none_when_bugs_dir_absent(
    tmp_path: pathlib.Path,
) -> None:
    missing_dir = tmp_path / "bugs"
    assert find_existing_bug_for_pairs(str(missing_dir), {"docker/claude-code"}) is None


# ---------------------------------------------------------------------------
# No structured pairs (still files -- never silently drops a real defect)
# ---------------------------------------------------------------------------


def test_unstructured_failure_still_files_a_bug(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.12",
        commit="abc1234",
        output_text=_UNSTRUCTURED_FAILURE_OUTPUT,
    )

    assert result.outcome == "no-pairs"
    assert result.bug_id is not None
    assert result.pairs == ()
    assert len(list(bugs_dir.glob("BUG-*.md"))) == 1


# ---------------------------------------------------------------------------
# Two failing pairs in one run
# ---------------------------------------------------------------------------


def test_two_failing_pairs_in_one_run_produce_one_bug_naming_both(
    tmp_path: pathlib.Path,
) -> None:
    output = (
        "E       Failed: docker build of docker/claude-code/Dockerfile failed "
        "(exit 1) at step: RUN npm ci; base-image reachability was confirmed\n"
        "E       Failed: podman build of docker/codex/Dockerfile failed "
        "(exit 1) at step: COPY entrypoint.sh; base-image reachability was confirmed\n"
    )

    result = file_or_cite_bug(
        str(tmp_path / "bugs"),
        project="demo",
        version="v2.9.12",
        commit="abc1234",
        output_text=output,
    )

    assert result.outcome == "filed"
    assert set(result.pairs) == {"docker/claude-code", "podman/codex"}
    content = next((tmp_path / "bugs").glob("BUG-*.md")).read_text(encoding="utf-8")
    assert "docker/claude-code" in content
    assert "podman/codex" in content


def test_filing_result_is_a_dataclass_with_the_documented_fields() -> None:
    result = FilingResult(outcome="filed", bug_id="BUG-0001", bug_path="/x", pairs=("a/b",))
    assert result.outcome == "filed"
    assert result.bug_id == "BUG-0001"
    assert result.bug_path == "/x"
    assert result.pairs == ("a/b",)


# ---------------------------------------------------------------------------
# A non-build cold test failing while every image built
# ---------------------------------------------------------------------------


def test_non_build_test_failure_files_a_bug_naming_the_test_file_and_test_id(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.12",
        commit="abc1234",
        output_text=NON_BUILD_TEST_FAILURE_OUTPUT,
    )

    assert result.outcome == "filed"
    assert result.pairs == ()
    assert result.tests == (NON_BUILD_TEST_NODEID,)
    filed_files = sorted(bugs_dir.glob("BUG-*.md"))
    assert len(filed_files) == 1
    content = filed_files[0].read_text(encoding="utf-8")
    assert f"file `team/{NON_BUILD_TEST_FILE}`" in content
    assert f"test `{NON_BUILD_TEST_ID}`" in content
    assert f"## Deep-Verify Tests\n{NON_BUILD_TEST_NODEID}\n" in content
    assert "no structured engine/flavor/step detail" not in content
    assert "## Status\nopen" in content
    assert "**Filed By:** deep-verify (autonomous)" in content
    assert "v2.9.12" in content and "abc1234" in content


def test_repeat_non_build_test_failure_cites_the_open_bug_instead_of_duplicating(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    first = file_or_cite_bug(
        str(bugs_dir), project="demo", version="v2.9.12", commit="abc1234",
        output_text=NON_BUILD_TEST_FAILURE_OUTPUT,
    )

    second = file_or_cite_bug(
        str(bugs_dir), project="demo", version="v2.9.13", commit="def5678",
        output_text=NON_BUILD_TEST_FAILURE_OUTPUT,
    )

    assert second.outcome == "duplicate"
    assert second.bug_id == first.bug_id
    assert len(list(bugs_dir.glob("BUG-*.md"))) == 1


def test_bug_filer_cli_reports_the_failing_test_on_its_tests_line(
    tmp_path: pathlib.Path, capsys
) -> None:
    output_file = tmp_path / "lane-output.txt"
    output_file.write_text(NON_BUILD_TEST_FAILURE_OUTPUT, encoding="utf-8")

    exit_code = main([str(tmp_path / "bugs"), "demo", "v2.9.12", "abc1234", str(output_file)])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert "outcome=filed" in lines
    assert "pairs=" in lines
    assert f"tests={NON_BUILD_TEST_NODEID}" in lines


# ---------------------------------------------------------------------------
# A non-build cold test failing while no pair could be built
# ---------------------------------------------------------------------------


def test_engine_unavailable_non_build_failure_files_a_bug_without_claiming_the_builds_built(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"

    result = file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.12",
        commit="abc1234",
        output_text=ENGINE_UNAVAILABLE_NON_BUILD_FAILURE_OUTPUT,
    )

    assert result.outcome == "filed"
    assert result.pairs == ()
    assert result.tests == (ENGINE_UNAVAILABLE_FAILING_TEST_NODEID,)
    filed_files = sorted(bugs_dir.glob("BUG-*.md"))
    assert len(filed_files) == 1
    content = filed_files[0].read_text(encoding="utf-8")
    assert f"file `team/{ENGINE_UNAVAILABLE_FAILING_TEST_FILE}`" in content
    assert f"test `{ENGINE_UNAVAILABLE_FAILING_TEST_ID}`" in content
    assert f"## Deep-Verify Tests\n{ENGINE_UNAVAILABLE_FAILING_TEST_NODEID}\n" in content
    assert "records the builds as `built`" not in content
    assert "`could-not-attempt`" in content


def test_non_build_failure_with_build_evidence_still_notes_the_builds_are_recorded_built(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"

    file_or_cite_bug(
        str(bugs_dir),
        project="demo",
        version="v2.9.12",
        commit="abc1234",
        output_text=NON_BUILD_TEST_FAILURE_OUTPUT,
    )

    content = next(bugs_dir.glob("BUG-*.md")).read_text(encoding="utf-8")
    assert "records the builds as `built`" in content
