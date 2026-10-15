"""
test_lint_tester_report_census.py
=================================
team/scripts/lint_tester_report_census.py reports non-test team/ code
outside team/pgai_agent_kanban/reports/tester_report.py that matches a
TESTER report-parsing pattern -- the finding heading, the recommendation
heading, and the bold Systemic Risk / Fix Effort / Filed As / Regression
field labels -- in their literal and regex spellings. It is quality class:
it exits 0 whatever it finds, in every case below.

Synthetic files are seeded under tmp_path with the repository layout the
lint expects (``<root>/team/...``) and fed through ``--paths`` (the explicit
file override) or scanned whole with ``--root``. The last case runs a
fixture copy of run-unit-tests.sh to show the runner carries the lint's
findings into its aggregate without changing its own exit status.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT = _SCRIPTS_DIR / "lint_tester_report_census.py"
_PREFIX = "tester_report_census: "

# Each report-parsing pattern, spelled the way report.md carries it. The
# strings are assembled from parts so this test's own source never spells
# a pattern in full.
_HEADING_FINDING = "#" * 3 + " Finding 1: title"
_HEADING_RECOMMENDATION = "#" * 2 + " Recommendation"
_PATTERN_TEXTS = {
    "finding-heading": _HEADING_FINDING,
    "systemic-risk": "**" + "Systemic Risk:** high",
    "fix-effort": "**" + "Fix Effort:** large",
    "filed-as": "**" + "Filed As:** BUG-0001",
    "regression": "**" + "Regression:** yes",
    "recommendation-heading": _HEADING_RECOMMENDATION,
}


def _seed(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT), *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(cwd),
    )


def _findings(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith(_PREFIX)]


def _scan(tmp_path: pathlib.Path, *paths: pathlib.Path, allowlist: pathlib.Path | None = None):
    args = ["--root", str(tmp_path), "--paths", *(str(p) for p in paths)]
    if allowlist is not None:
        args += ["--allowlist", str(allowlist)]
    result = _run(*args, cwd=tmp_path)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result


# ---------------------------------------------------------------------------
# Each pattern, in a .py and a .sh file, is reported
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern_id", sorted(_PATTERN_TEXTS))
def test_each_pattern_in_a_python_file_is_reported(tmp_path: pathlib.Path, pattern_id: str) -> None:
    text = _PATTERN_TEXTS[pattern_id]
    path = _seed(tmp_path, "team/pgai_agent_kanban/cm/reader.py", f"import re\nMARK = {text!r}\n")
    result = _scan(tmp_path, path)
    findings = _findings(result)
    assert len(findings) == 1, (pattern_id, result.stdout)
    assert findings[0].startswith(_PREFIX + "team/pgai_agent_kanban/cm/reader.py:2: matches "), findings
    assert "tester_report" in findings[0]


@pytest.mark.parametrize("pattern_id", sorted(_PATTERN_TEXTS))
def test_each_pattern_in_a_shell_file_is_reported(tmp_path: pathlib.Path, pattern_id: str) -> None:
    text = _PATTERN_TEXTS[pattern_id]
    path = _seed(
        tmp_path,
        "team/scripts/lib/cm/reader.sh",
        f"#!/usr/bin/env bash\ngrep -F -- '{text}' \"$1\"\n",
    )
    result = _scan(tmp_path, path)
    findings = _findings(result)
    assert len(findings) == 1, (pattern_id, result.stdout)
    assert findings[0].startswith(_PREFIX + "team/scripts/lib/cm/reader.sh:2: matches "), findings


@pytest.mark.parametrize(
    "line",
    [
        # A heading held as a regex: quantified hashes, grouping, \s+.
        r'_HEADING_RE = re.compile(r"^(#{3,6})\s+finding\b", re.IGNORECASE)',
        r'_REC_RE = re.compile(r"^##+\s*Recommendation")',
        # Bold labels with escaped markers, colon inside or outside the bold.
        r'_RISK_RE = re.compile(r"\*\*Systemic Risk:\*\*\s*(\w+)")',
        r'_FILED_RE = re.compile(r"\*\*Filed\s+As\*\*\s*:\s*(.*)")',
        # One alternation covering several labels.
        r'_FIELDS_RE = re.compile(r"\*\*(?:Fix Effort|Regression):\*\*")',
        r'_EFFORT_RE = re.compile(r"[*]{2}Fix[ ]+Effort:[*]{2}")',
    ],
)
def test_regex_spellings_of_the_patterns_are_reported(tmp_path: pathlib.Path, line: str) -> None:
    path = _seed(tmp_path, "team/pgai_agent_kanban/overwatch/reader.py", f"import re\n{line}\n")
    findings = _findings(_scan(tmp_path, path))
    assert len(findings) == 1, (line, findings)
    assert ":2: matches " in findings[0]


def test_shell_grep_regex_with_posix_space_class_is_reported(tmp_path: pathlib.Path) -> None:
    path = _seed(
        tmp_path,
        "team/scripts/lib/cm/reader.sh",
        "#!/usr/bin/env bash\ngrep -E '^#{3,}[[:space:]]+Finding' \"$1\"\n",
    )
    findings = _findings(_scan(tmp_path, path))
    assert len(findings) == 1, findings
    assert "reader.sh:2:" in findings[0]


def test_inline_python_in_a_shell_heredoc_is_reported(tmp_path: pathlib.Path) -> None:
    body = (
        "#!/usr/bin/env bash\n"
        "python3 - \"$1\" <<'PY'\n"
        "import sys\n"
        "# a comment line in the heredoc is not code\n"
        f"print({_PATTERN_TEXTS['regression']!r} in open(sys.argv[1]).read())\n"
        "PY\n"
    )
    path = _seed(tmp_path, "team/scripts/lib/cm/reader.sh", body)
    findings = _findings(_scan(tmp_path, path))
    assert [f.split(": matches")[0] for f in findings] == [
        _PREFIX + "team/scripts/lib/cm/reader.sh:5"
    ], findings


# ---------------------------------------------------------------------------
# What is not reported
# ---------------------------------------------------------------------------


def test_allowlisted_path_is_not_reported(tmp_path: pathlib.Path) -> None:
    allowed = _seed(tmp_path, "team/scripts/allowed.py", f"MARK = {_HEADING_FINDING!r}\n")
    other = _seed(tmp_path, "team/scripts/other.py", f"MARK = {_HEADING_FINDING!r}\n")
    allowlist = _seed(
        tmp_path,
        "team/config/tester-report-census-allowlist.txt",
        "# header comment\n\n# reason for the entry\nteam/scripts/allowed.py\n",
    )
    result = _scan(tmp_path, allowed, other, allowlist=allowlist)
    findings = _findings(result)
    assert len(findings) == 1, result.stdout
    assert "team/scripts/other.py:1:" in findings[0]
    assert "allowed.py" not in result.stdout.replace("allowlisted", "")


@pytest.mark.parametrize(
    "rel",
    [
        "team/tests/unit/test_reader.py",
        "team/tests/fixtures/reader.sh",
        "team/pgai_agent_kanban/api/tests/test_reader.py",
        "team/pgai_agent_kanban/reports/tester_report.py",
    ],
)
def test_test_code_and_the_parser_module_are_not_reported(tmp_path: pathlib.Path, rel: str) -> None:
    path = _seed(tmp_path, rel, f"MARK = {_HEADING_FINDING!r}\n")
    result = _scan(tmp_path, path)
    assert _findings(result) == [], result.stdout
    assert "0 finding(s)" in result.stdout


def test_markdown_is_not_code_and_is_not_reported(tmp_path: pathlib.Path) -> None:
    path = _seed(tmp_path, "team/templates/report-template.md", _HEADING_FINDING + "\n")
    assert _findings(_scan(tmp_path, path)) == []


def test_comments_and_docstrings_are_not_reported(tmp_path: pathlib.Path) -> None:
    py = _seed(
        tmp_path,
        "team/pgai_agent_kanban/cm/documented.py",
        f'"""Reads {_PATTERN_TEXTS["systemic-risk"]} through tester_report."""\n'
        f"# {_HEADING_FINDING} is the parser's business\n"
        f"VALUE = 1  # {_PATTERN_TEXTS['fix-effort']}\n"
        "def f():\n"
        f'    """{_HEADING_RECOMMENDATION} is read by parse_report."""\n'
        "    return VALUE\n",
    )
    sh = _seed(
        tmp_path,
        "team/scripts/lib/documented.sh",
        f"#!/usr/bin/env bash\n  # {_PATTERN_TEXTS['filed-as']} comes from the parser\ntrue\n",
    )
    result = _scan(tmp_path, py, sh)
    assert _findings(result) == [], result.stdout


def test_bold_label_mentioned_without_bold_is_not_reported(tmp_path: pathlib.Path) -> None:
    path = _seed(
        tmp_path,
        "team/scripts/lib/cm/narrate.sh",
        '#!/usr/bin/env bash\necho "  Systemic Risk:       ${RISK}"\necho "  Regression:  ${REG}"\n',
    )
    assert _findings(_scan(tmp_path, path)) == []


# ---------------------------------------------------------------------------
# Whole-tree scan, allowlist staleness, findings file
# ---------------------------------------------------------------------------


def test_whole_tree_scan_reports_only_in_scope_code(tmp_path: pathlib.Path) -> None:
    _seed(tmp_path, "team/scripts/reader.sh", f"grep -F '{_HEADING_RECOMMENDATION}' r.md\n")
    _seed(tmp_path, "team/tests/unit/test_x.py", f"M = {_HEADING_FINDING!r}\n")
    _seed(tmp_path, "team/pgai_agent_kanban/reports/tester_report.py", f"M = {_HEADING_FINDING!r}\n")
    _seed(tmp_path, "team/docs/notes.md", _HEADING_FINDING + "\n")
    _seed(tmp_path, "outside/reader.py", f"M = {_HEADING_FINDING!r}\n")
    result = _run("--root", str(tmp_path), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    findings = _findings(result)
    assert len(findings) == 1, result.stdout
    assert findings[0].startswith(_PREFIX + "team/scripts/reader.sh:1: matches "), result.stdout
    assert "1 file(s) scanned" in result.stdout


def test_stale_allowlist_entry_is_reported_on_a_whole_tree_scan(tmp_path: pathlib.Path) -> None:
    _seed(tmp_path, "team/scripts/still_reads.py", f"M = {_HEADING_FINDING!r}\n")
    _seed(tmp_path, "team/scripts/migrated.py", "from x import parse_report\n")
    _seed(
        tmp_path,
        "team/config/tester-report-census-allowlist.txt",
        "team/scripts/still_reads.py\nteam/scripts/migrated.py\nteam/scripts/deleted.sh\n",
    )
    result = _run("--root", str(tmp_path), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    findings = _findings(result)
    assert len(findings) == 2, result.stdout
    assert any("'team/scripts/migrated.py'" in f for f in findings), findings
    assert any("'team/scripts/deleted.sh'" in f for f in findings), findings
    assert not any("still_reads" in f for f in findings), findings


def test_findings_file_carries_each_finding(tmp_path: pathlib.Path) -> None:
    path = _seed(tmp_path, "team/scripts/reader.py", f"M = {_PATTERN_TEXTS['filed-as']!r}\n")
    out = tmp_path / "out" / "findings.json"
    result = _run("--root", str(tmp_path), "--paths", str(path), "--findings-file", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    records = json.loads(out.read_text(encoding="utf-8"))
    assert len(records) == 1, records
    record = records[0]
    assert record["lint"] == "tester_report_census"
    assert record["severity"] == "quality"
    assert record["kind"] == "report-parsing-outside-parser"
    assert record["file"] == "team/scripts/reader.py"
    assert record["line"] == 1
    assert record["remedy"] == "python3 team/scripts/lint_tester_report_census.py"


def test_clean_run_writes_an_empty_findings_file(tmp_path: pathlib.Path) -> None:
    path = _seed(tmp_path, "team/scripts/clean.py", "from x import parse_report\n")
    out = tmp_path / "findings.json"
    result = _run("--root", str(tmp_path), "--paths", str(path), "--findings-file", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_missing_path_is_narrated_and_still_exits_zero(tmp_path: pathlib.Path) -> None:
    result = _run("--root", str(tmp_path), "--paths", str(tmp_path / "team" / "gone.py"), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "not a file" in result.stderr


def test_checkout_scan_exits_zero_and_prints_totals() -> None:
    """The real checkout is scanned with its own allowlist; quality class
    means exit 0 whatever the count, so only the run itself is asserted."""
    result = _run(cwd=_TEAM_DIR.parent)
    assert result.returncode == 0, result.stderr
    assert re.search(r"lint_tester_report_census: totals: \d+ file\(s\) scanned", result.stdout), result.stdout


# ---------------------------------------------------------------------------
# Runner wiring: findings reach the aggregate, the exit status is unchanged
# ---------------------------------------------------------------------------

_SCRIPT_REF_RE = re.compile(r"((?:dashboard/)?[\w-]+\.(?:py|sh))\b")


def _runner_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway repo holding the real unit runner, its real lib/, the
    real census lint, a stand-in (exit 0) for every other script the runner
    names, one passing test, and one seeded report reader."""
    team = tmp_path / "fixture_repo" / "team"
    scripts = team / "scripts"
    scripts.mkdir(parents=True)
    runner_text = (_SCRIPTS_DIR / "run-unit-tests.sh").read_text(encoding="utf-8")
    shutil.copy2(_SCRIPTS_DIR / "run-unit-tests.sh", scripts / "run-unit-tests.sh")
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib")
    for name in sorted(set(_SCRIPT_REF_RE.findall(runner_text))):
        real = _SCRIPTS_DIR / name
        if name in ("run-unit-tests.sh", _LINT.name) or not real.is_file():
            continue
        stub = scripts / name
        stub.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".py"):
            stub.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
        else:
            stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    shutil.copy2(_LINT, scripts / _LINT.name)
    _seed(team, "scripts/seeded_reader.py", f"MARK = {_HEADING_FINDING!r}\n")
    for rel in ("tests/unit", "pgai_agent_kanban/api/tests"):
        (team / rel).mkdir(parents=True, exist_ok=True)
    (team / "tests/unit/test_fixture_marker.py").write_text(
        "def test_fixture_marker():\n    assert True\n", encoding="utf-8"
    )
    return team


def test_unit_runner_merges_census_findings_and_keeps_exit_zero(tmp_path: pathlib.Path) -> None:
    team = _runner_fixture(tmp_path)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text("[testing]\nworkers = 1\n", encoding="utf-8")
    temp_dir = tmp_path / "scratch"
    temp_dir.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
    }
    result = subprocess.run(
        ["bash", str(team / "scripts" / "run-unit-tests.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(team),
    )
    detail = f"exit {result.returncode}\nstdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
    assert result.returncode == 0, detail
    assert "team/scripts/seeded_reader.py:1: matches" in result.stdout, detail

    aggregate = json.loads((temp_dir / "lint" / "findings-unit.json").read_text(encoding="utf-8"))
    records = aggregate["findings"] if isinstance(aggregate, dict) else aggregate
    census = [r for r in records if r.get("lint") == "tester_report_census"]
    assert len(census) == 1, json.dumps(aggregate, indent=2)[:4000]
    assert census[0]["file"] == "team/scripts/seeded_reader.py"
