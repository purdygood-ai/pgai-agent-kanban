"""
test_coder_stale_literal_check_recorded_output.py
==================================================
End-to-end coverage for team/scripts/coder-stale-literal-check.sh's
recorded-output fixture search: a production diff that reworks a narration
sentence, leaving a recorded-output golden holding the old sentence
verbatim, must be named under ## Stale Literal Risks -- something the
assertion-context-only search could never see, because a recorded golden's
lines carry no assertion syntax to anchor on.

Each test drives the real script against a real, disposable git repository
under tmp_path (no seam mocked) and asserts on stdout and exit code, the
same surface a CODER agent pastes into status.md.
"""

from __future__ import annotations

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


def _run_check(repo: pathlib.Path, diff_range: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT), "--diff", diff_range, "--project", "pgai-agent-kanban"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )


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
