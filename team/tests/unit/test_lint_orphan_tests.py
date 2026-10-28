"""
test_lint_orphan_tests.py — Unit tests for team/scripts/lint_orphan_tests.py.

Every test builds its own fixture ``team/`` tree under pytest's ``tmp_path``
and points the lint at it with ``--team-dir``; the real tree is never
scanned except where a test says so.

Coverage:

  1. **Seeded orphan**: a ``test_*.py`` outside every collected root is
     printed, written to ``--findings-file`` as one quality finding naming
     the file, announced with the ``lint.orphan-tests`` error key, and the
     lint exits 0 (quality class -- a finding is not a failure).

  2. **One finding per orphan**: two orphans in different directories yield
     two findings, in path order.

  3. **Covered and excluded files are not findings**: files under a
     collected root, and files under ``fixtures/`` or ``__pycache__``, are
     never reported; the findings file is written as an empty array.

  4. **Usage error**: a ``--team-dir`` that does not exist exits 2 and
     writes no findings file -- the one non-zero status the lint keeps.

  5. **Direct invocation**: the script run as a subprocess, the way the
     gated runners call it, exits 0 on a seeded orphan and writes the same
     findings file.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_orphan_tests.py"

_spec = importlib.util.spec_from_file_location("lint_orphan_tests", _LINT_SCRIPT)
assert _spec is not None and _spec.loader is not None
_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lint)


def _fixture_team(tmp_path: pathlib.Path, test_files: list[str]) -> pathlib.Path:
    """Create ``<tmp_path>/team`` holding an empty file at each relative path."""
    team = tmp_path / "team"
    for rel in test_files:
        path = team / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return team


def test_seeded_orphan_is_printed_recorded_and_exits_0(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    team = _fixture_team(tmp_path, ["tests/unit/test_covered.py", "stray/test_lost.py"])
    findings_file = tmp_path / "out" / "orphan_tests.json"

    exit_code = _lint.main(["--team-dir", str(team), "--findings-file", str(findings_file)])

    assert exit_code == 0, "an orphan test is a quality finding: main() must return 0"
    captured = capsys.readouterr()
    assert "team/stray/test_lost.py" in captured.out, captured.out
    assert "error:   lint.orphan-tests" in captured.err, captured.err
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert len(findings) == 1, findings
    finding = findings[0]
    assert finding["lint"] == "orphan_tests"
    assert finding["severity"] == "quality"
    assert finding["file"] == "team/stray/test_lost.py"
    assert finding["summary"] == finding["cause"]
    assert "team/stray/test_lost.py" in finding["cause"]
    assert finding["remedy"].startswith(f"git -C {tmp_path.resolve()} mv team/stray/test_lost.py "), finding


def test_each_orphan_is_its_own_finding(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    team = _fixture_team(tmp_path, ["b_dir/test_two.py", "a_dir/test_one.py", "tests/unit/test_ok.py"])
    findings_file = tmp_path / "findings.json"

    assert _lint.main(["--team-dir", str(team), "--findings-file", str(findings_file)]) == 0

    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert [f["file"] for f in findings] == ["team/a_dir/test_one.py", "team/b_dir/test_two.py"]
    capsys.readouterr()


def test_covered_and_excluded_files_are_not_findings(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    team = _fixture_team(
        tmp_path,
        [
            "tests/unit/test_a.py",
            "tests/integration/test_b.py",
            "pgai_agent_kanban/api/tests/test_c.py",
            "tests/fixtures/test_bad_example.py",
            "stray/__pycache__/test_cached.py",
        ],
    )
    findings_file = tmp_path / "findings.json"

    assert _lint.main(["--team-dir", str(team), "--findings-file", str(findings_file)]) == 0

    assert json.loads(findings_file.read_text(encoding="utf-8")) == []
    captured = capsys.readouterr()
    assert "no orphan tests found" in captured.out, captured.out
    assert "lint.orphan-tests" not in captured.err, captured.err


def test_missing_team_dir_is_a_usage_error(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    findings_file = tmp_path / "findings.json"

    exit_code = _lint.main(["--team-dir", str(tmp_path / "absent"), "--findings-file", str(findings_file)])

    assert exit_code == 2
    assert "team directory not found" in capsys.readouterr().err
    assert not findings_file.exists()


def test_direct_invocation_exits_0_and_writes_findings(tmp_path: pathlib.Path) -> None:
    team = _fixture_team(tmp_path, ["stray/test_lost.py"])
    findings_file = tmp_path / "findings.json"
    env = build_scrubbed_env(home_dir=tmp_path / "home")

    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--team-dir", str(team), "--findings-file", str(findings_file)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_TEAM_DIR),
        timeout=60,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "team/stray/test_lost.py" in result.stdout, result.stdout
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert [f["file"] for f in findings] == ["team/stray/test_lost.py"]
