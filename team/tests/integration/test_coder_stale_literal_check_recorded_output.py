"""
test_coder_stale_literal_check_recorded_output.py
==================================================
End-to-end coverage for team/scripts/coder-stale-literal-check.sh's
recorded-output fixture search: a production diff that reworks a narration
sentence, leaving a recorded-output golden holding the old sentence
verbatim, must be named under ## Stale Literal Risks -- something the
assertion-context-only search could never see, because a recorded golden's
lines carry no assertion syntax to anchor on.

It also covers the generated-artifact exclusion: a frozen ICD baseline
snapshot added under docs/api/baselines/ contributes no literal to the
search, even when a test pins one of its strings.

Each test drives the real script against a real, disposable git repository
under tmp_path (no seam mocked) and asserts on stdout and exit code, the
same surface a CODER agent pastes into status.md -- and, with
--findings-file, on the per-instance findings array the gated runners merge.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds and diffs a real git repository; a module-wide
    autouse guard keeps that requirement in one place.
    """
    require_binary("git", label="coder-stale-literal-check-recorded-output")


# ---------------------------------------------------------------------------
# Path helpers — resolve from this file's own location.
#
# File structure:
#   team/tests/integration/test_coder_stale_literal_check_recorded_output.py
#                    └── team/tests/integration/    (this file)
#                        └── team/tests/
#                            └── team/
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPT = _TEAM_DIR / "scripts" / "coder-stale-literal-check.sh"


def _git(repo: pathlib.Path, *args: str) -> str:
    """Run a git command inside repo and return stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_repo_with_recorded_output_fixture(
    repo: pathlib.Path,
    *,
    narration: str,
) -> None:
    """Build a disposable repo with a production narration line and a
    recorded-output golden that captures it, mirroring the tree's own
    fixtures/engine-baseline convention (team/tests/fixtures/engine-baseline)."""
    prod_dir = repo / "team" / "scripts" / "dashboard" / "lib"
    prod_dir.mkdir(parents=True)
    fixture_dir = repo / "team" / "tests" / "fixtures" / "engine-baseline"
    fixture_dir.mkdir(parents=True)

    (prod_dir / "snapshot.sh").write_text(
        "#!/usr/bin/env bash\n"
        "render_missing_snapshot() {\n"
        f'    echo "{narration}" >&2\n'
        "}\n",
        encoding="utf-8",
    )
    (fixture_dir / "board-render.txt").write_text(
        f"=== CODER ===\n{narration}\n[harn] 20260101-001\n",
        encoding="utf-8",
    )

    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline: production narration and golden agree")


def _run_check(
    repo: pathlib.Path, diff_range: str, *extra: str
) -> subprocess.CompletedProcess:
    # Without PGAI_PROJECT_NAME the dual-channel emitter on a check that
    # cannot run records nothing in any project's refusal ledger.
    env = {k: v for k, v in os.environ.items() if k != "PGAI_PROJECT_NAME"}
    return subprocess.run(
        ["bash", str(_SCRIPT), "--diff", diff_range, "--project", "pgai-agent-kanban", *extra],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _reword_narration(repo: pathlib.Path, new_narration: str) -> None:
    """Commit a production change that rewords the narration sentence."""
    prod_file = repo / "team" / "scripts" / "dashboard" / "lib" / "snapshot.sh"
    prod_file.write_text(
        "#!/usr/bin/env bash\n"
        "render_missing_snapshot() {\n"
        f'    echo "{new_narration}" >&2\n'
        "}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "reword the snapshot-miss narration sentence")


# ---------------------------------------------------------------------------
# Hit case: a reworded narration sentence with a stale recorded golden
# ---------------------------------------------------------------------------


def test_reworded_narration_names_the_stale_recorded_output_fixture(
    tmp_path: pathlib.Path,
) -> None:
    """A production diff that rewords a narration sentence, with the golden
    still holding the old sentence verbatim, names the golden's path and
    line number under ## Stale Literal Risks."""
    repo = tmp_path / "repo"
    old_narration = "no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly"
    new_narration = "no fresh snapshot (missing or unreadable) -- rendering directly"
    _make_repo_with_recorded_output_fixture(repo, narration=old_narration)

    prod_file = repo / "team" / "scripts" / "dashboard" / "lib" / "snapshot.sh"
    prod_file.write_text(
        "#!/usr/bin/env bash\n"
        "render_missing_snapshot() {\n"
        f'    echo "{new_narration}" >&2\n'
        "}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "reword the snapshot-miss narration sentence")

    result = _run_check(repo, "HEAD~1..HEAD")

    assert result.returncode == 0
    assert "## Stale Literal Risks" in result.stdout
    fixture_path = repo / "team" / "tests" / "fixtures" / "engine-baseline" / "board-render.txt"
    assert f"{fixture_path}:2" in result.stdout
    assert old_narration in result.stdout


# ---------------------------------------------------------------------------
# No-hit case: a diff that touches no test-visible literal
# ---------------------------------------------------------------------------


def test_diff_with_no_test_visible_literal_still_prints_none(
    tmp_path: pathlib.Path,
) -> None:
    """A production change that introduces no literal any test or fixture
    holds still prints the ## Stale Literal Risks / (none ...) shape."""
    repo = tmp_path / "repo"
    narration = "no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly"
    _make_repo_with_recorded_output_fixture(repo, narration=narration)

    other_file = repo / "team" / "scripts" / "dashboard" / "lib" / "other.sh"
    other_file.write_text(
        "#!/usr/bin/env bash\nnoop_helper() {\n    return 0\n}\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add an unrelated helper with no test-visible literal")

    result = _run_check(repo, "HEAD~1..HEAD")

    assert result.returncode == 0
    assert "## Stale Literal Risks" in result.stdout
    assert "(none" in result.stdout


# ---------------------------------------------------------------------------
# The check is advisory: exit 0 in both the hit and the no-hit case
# ---------------------------------------------------------------------------


def test_exits_zero_in_both_hit_and_no_hit_cases(tmp_path: pathlib.Path) -> None:
    """The check never gates on findings -- CENSUS class CONVERT, always exit 0."""
    hit_repo = tmp_path / "hit_repo"
    narration = "no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly"
    _make_repo_with_recorded_output_fixture(hit_repo, narration=narration)
    prod_file = hit_repo / "team" / "scripts" / "dashboard" / "lib" / "snapshot.sh"
    prod_file.write_text(
        "#!/usr/bin/env bash\n"
        "render_missing_snapshot() {\n"
        '    echo "no fresh snapshot (missing or unreadable) -- rendering directly" >&2\n'
        "}\n",
        encoding="utf-8",
    )
    _git(hit_repo, "add", ".")
    _git(hit_repo, "commit", "-m", "reword the snapshot-miss narration sentence")
    hit_result = _run_check(hit_repo, "HEAD~1..HEAD")

    no_hit_repo = tmp_path / "no_hit_repo"
    _make_repo_with_recorded_output_fixture(no_hit_repo, narration=narration)
    other_file = no_hit_repo / "team" / "scripts" / "dashboard" / "lib" / "other.sh"
    other_file.write_text(
        "#!/usr/bin/env bash\nnoop_helper() {\n    return 0\n}\n",
        encoding="utf-8",
    )
    _git(no_hit_repo, "add", ".")
    _git(no_hit_repo, "commit", "-m", "add an unrelated helper with no test-visible literal")
    no_hit_result = _run_check(no_hit_repo, "HEAD~1..HEAD")

    assert hit_result.returncode == 0
    assert no_hit_result.returncode == 0


# ---------------------------------------------------------------------------
# Exit contract: a hit is recorded per instance; a check that cannot run
# says so and still exits 0
# ---------------------------------------------------------------------------


def test_stale_hit_is_printed_recorded_as_one_finding_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """One stale recorded-output line yields one per-instance finding naming
    the literal, file and line, and the check exits 0."""
    repo = tmp_path / "repo"
    old_narration = "no fresh snapshot (missing, unreadable, or older than 5s) -- rendering directly"
    _make_repo_with_recorded_output_fixture(repo, narration=old_narration)
    _reword_narration(repo, "no fresh snapshot (missing or unreadable) -- rendering directly")
    findings_file = tmp_path / "out" / "findings.json"

    result = _run_check(repo, "HEAD~1..HEAD", "--findings-file", str(findings_file))

    assert result.returncode == 0, result.stderr
    fixture_path = repo / "team" / "tests" / "fixtures" / "engine-baseline" / "board-render.txt"
    assert f"{fixture_path}:2" in result.stdout
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert len(findings) == 1, findings
    finding = findings[0]
    assert finding["lint"] == "coder_stale_literal"
    assert finding["severity"] == "quality"
    assert finding["kind"] == "stale-literal"
    assert finding["file"] == str(fixture_path)
    assert finding["line"] == 2
    assert old_narration in finding["literal"]
    assert "--diff HEAD~1..HEAD" in finding["remedy"]


def test_no_hit_writes_an_empty_findings_array(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    _make_repo_with_recorded_output_fixture(repo, narration="a narration sentence the diff never touches")
    (repo / "team" / "scripts" / "dashboard" / "lib" / "other.sh").write_text(
        "#!/usr/bin/env bash\nnoop_helper() {\n    return 0\n}\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add an unrelated helper")
    findings_file = tmp_path / "findings.json"

    result = _run_check(repo, "HEAD~1..HEAD", "--findings-file", str(findings_file))

    assert result.returncode == 0, result.stderr
    assert "(none" in result.stdout
    assert json.loads(findings_file.read_text(encoding="utf-8")) == []


def test_outside_a_git_repository_says_not_checked_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """Run outside any repository, the section says the check did not run
    (never "(none ...)"), the dual-channel contract names the cause, one
    check-not-run finding is recorded, and the exit is 0."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    findings_file = tmp_path / "findings.json"

    result = _run_check(not_a_repo, "HEAD~1..HEAD", "--findings-file", str(findings_file))

    assert result.returncode == 0, result.stderr
    assert "## Stale Literal Risks" in result.stdout
    assert "(not checked" in result.stdout
    assert "(none" not in result.stdout
    assert "error:   coder_stale_literal_check.not_a_repo" in result.stderr
    (finding,) = json.loads(findings_file.read_text(encoding="utf-8"))
    assert finding["kind"] == "check-not-run"


def test_unresolvable_range_says_not_checked_names_the_range_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """A --diff range naming a ref that does not exist is a check that did
    not run: the section says so naming the range and git's reason (never
    "(none ...)"), one check-not-run finding is recorded, and the exit is 0."""
    repo = tmp_path / "repo"
    _make_repo_with_recorded_output_fixture(repo, narration="a narration sentence")
    findings_file = tmp_path / "findings.json"

    result = _run_check(
        repo, "no_such_ref_xyz..HEAD", "--findings-file", str(findings_file)
    )

    assert result.returncode == 0, result.stderr
    assert "## Stale Literal Risks" in result.stdout
    assert "(not checked — diff range no_such_ref_xyz..HEAD does not resolve: " in result.stdout
    assert "no_such_ref_xyz" in result.stdout.split("does not resolve: ", 1)[1]
    assert "(none" not in result.stdout
    assert "error:   coder_stale_literal_check.range_unresolved" in result.stderr
    (finding,) = json.loads(findings_file.read_text(encoding="utf-8"))
    assert finding["kind"] == "check-not-run"
    assert "no_such_ref_xyz..HEAD" in finding["summary"]


def test_range_with_no_production_change_prints_none_and_empty_findings(
    tmp_path: pathlib.Path,
) -> None:
    """A range that resolves but changes only test files has an empty
    production diff: the section reads "(none ...)" and the findings array
    is empty."""
    repo = tmp_path / "repo"
    _make_repo_with_recorded_output_fixture(repo, narration="a narration sentence")
    test_file = repo / "team" / "tests" / "test_snapshot.py"
    test_file.write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add a test file only")
    findings_file = tmp_path / "findings.json"

    result = _run_check(repo, "HEAD~1..HEAD", "--findings-file", str(findings_file))

    assert result.returncode == 0, result.stderr
    assert "(none — no test assertions match literals" in result.stdout
    assert "(not checked" not in result.stdout
    assert json.loads(findings_file.read_text(encoding="utf-8")) == []


# ---------------------------------------------------------------------------
# Generated-artifact exclusion: a frozen ICD baseline snapshot is not
# authored production code, so none of its strings reaches the search
# ---------------------------------------------------------------------------

_BASELINE_LITERALS = (
    "frozen-baseline-sentinel-title",
    "frozen_baseline_sentinel_operation",
)
_AUTHORED_LITERAL = "authored-production-sentinel"


def _pin_literals_in_a_test(repo: pathlib.Path) -> None:
    """Commit a test asserting on every sentinel literal, so any sentinel
    that reaches the search is reported as a stale-literal hit."""
    (repo / "team" / "tests" / "test_icd_contract.py").write_text(
        "def test_contract(spec, op):\n"
        f'    assert spec["title"] == "{_BASELINE_LITERALS[0]}"\n'
        f'    assert op["operationId"] == "{_BASELINE_LITERALS[1]}"\n'
        f'    assert op["summary"] == "{_AUTHORED_LITERAL}"\n',
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "pin the sentinel literals in a test")


def _freeze_baseline(repo: pathlib.Path) -> None:
    """Write a frozen ICD baseline snapshot holding the baseline sentinels."""
    baselines = repo / "docs" / "api" / "baselines"
    baselines.mkdir(parents=True)
    (baselines / "icd-v9.9.9.json").write_text(
        "{\n"
        f'  "info": {{"title": "{_BASELINE_LITERALS[0]}", "version": "9.9.9"}},\n'
        f'  "paths": {{"/x": {{"post": {{"operationId": "{_BASELINE_LITERALS[1]}"}}}}}}\n'
        "}\n",
        encoding="utf-8",
    )


def test_frozen_icd_baseline_contributes_no_literal_to_the_search(
    tmp_path: pathlib.Path,
) -> None:
    """A diff adding a baseline snapshot under docs/api/baselines/ next to an
    authored production change reports the authored literal a test pins and
    none of the baseline's literals, although the same test pins those too --
    so they never reached the search, rather than reaching it and missing."""
    repo = tmp_path / "repo"
    _make_repo_with_recorded_output_fixture(repo, narration="a narration sentence")
    _pin_literals_in_a_test(repo)
    _freeze_baseline(repo)
    (repo / "team" / "scripts" / "contract.sh").write_text(
        f'#!/usr/bin/env bash\necho "{_AUTHORED_LITERAL}"\n', encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "freeze an ICD baseline beside an authored change")
    findings_file = tmp_path / "findings.json"

    result = _run_check(repo, "HEAD~1..HEAD", "--findings-file", str(findings_file))

    assert result.returncode == 0, result.stderr
    assert "## Stale Literal Risks" in result.stdout
    assert _AUTHORED_LITERAL in result.stdout, (
        "the authored literal a test pins must be reported, or this test "
        "proves nothing about the search: " + result.stdout
    )
    for literal in _BASELINE_LITERALS:
        assert literal not in result.stdout, (
            f"baseline literal {literal!r} reached the search: " + result.stdout
        )
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert [f["literal"] for f in findings] == [f'"{_AUTHORED_LITERAL}"'], findings


def test_diff_touching_only_a_frozen_icd_baseline_prints_none(
    tmp_path: pathlib.Path,
) -> None:
    """A range whose only change is a new baseline snapshot has no authored
    production diff: the section reads "(none ...)" and no finding is
    recorded, though a test pins the snapshot's strings."""
    repo = tmp_path / "repo"
    _make_repo_with_recorded_output_fixture(repo, narration="a narration sentence")
    _pin_literals_in_a_test(repo)
    _freeze_baseline(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "freeze an ICD baseline")
    findings_file = tmp_path / "findings.json"

    result = _run_check(repo, "HEAD~1..HEAD", "--findings-file", str(findings_file))

    assert result.returncode == 0, result.stderr
    assert "(none — no test assertions match literals" in result.stdout
    assert json.loads(findings_file.read_text(encoding="utf-8")) == []


def test_missing_required_argument_still_exits_one(tmp_path: pathlib.Path) -> None:
    result = subprocess.run(
        ["bash", str(_SCRIPT), "--diff", "HEAD~1..HEAD"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "--project is required" in result.stderr


def test_help_states_the_exit_contract() -> None:
    result = subprocess.run(
        ["bash", str(_SCRIPT), "--help"], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0
    assert "--findings-file" in result.stdout
    assert "the check could not run" in result.stdout
