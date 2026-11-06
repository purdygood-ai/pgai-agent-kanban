"""
test_audit_coding_standards_exit_contract.py
============================================
The exit contract of team/scripts/audit_coding_standards.sh: the audit is
quality class, so every FINDINGS / MANUAL-REVIEW-REQUIRED verdict is printed,
written to the results file and -- with --findings-file -- recorded as one
per-instance finding, and the audit exits 0 whatever it found. A missing
repo root or team/scripts/ is a finding too; only a malformed command line
exits 2.

Each test audits a synthetic repo root under tmp_path (never the live
tree), with the results and findings files beside it.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

# Self-located: team/tests/unit/<this file> -> team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_AUDIT_SH = _TEAM_DIR / "scripts" / "audit_coding_standards.sh"


def _run_audit(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    # Without PGAI_PROJECT_NAME the dual-channel emitter on a precondition
    # finding records nothing in any project's refusal ledger.
    env = {k: v for k, v in os.environ.items() if k != "PGAI_PROJECT_NAME"}
    return subprocess.run(
        ["bash", str(_AUDIT_SH), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
        env=env,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _fake_repo(root: pathlib.Path, *, with_lib: bool) -> pathlib.Path:
    """A repo root holding team/scripts/ -- and, with *with_lib*, the
    team/scripts/lib/ helpers and team/pgai_agent_kanban/ package whose
    absence is directive D03's finding."""
    (root / "team" / "scripts").mkdir(parents=True)
    if with_lib:
        (root / "team" / "scripts" / "lib").mkdir()
        (root / "team" / "scripts" / "lib" / "helper.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (root / "team" / "pgai_agent_kanban").mkdir()
    return root


def _findings_by_directive(findings_file: pathlib.Path) -> dict:
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    return {f["directive"]: f for f in findings if "directive" in f}


def test_seeded_finding_is_printed_recorded_and_exits_zero(tmp_path: pathlib.Path) -> None:
    """A repo root with no team/scripts/lib/ seeds directive D03's FINDINGS
    verdict: it is printed, recorded as one finding, and the exit is 0."""
    repo = _fake_repo(tmp_path / "repo", with_lib=False)
    results = tmp_path / "results.md"
    findings_file = tmp_path / "out" / "findings.json"

    result = _run_audit(
        "--repo-root",
        str(repo),
        "--output-file",
        str(results),
        "--findings-file",
        str(findings_file),
        "--verbose",
        cwd=tmp_path,
    )

    assert result.returncode == 0, _diagnostic(result)
    assert "D03 FINDINGS: Put shared code in lib/" in result.stdout, _diagnostic(result)
    assert "team/scripts/lib/ directory not found" in result.stdout, _diagnostic(result)
    d03 = _findings_by_directive(findings_file)["D03"]
    assert d03["lint"] == "coding_standards_audit"
    assert d03["severity"] == "quality"
    assert d03["kind"] == "directive-findings"
    assert d03["verdict"] == "FINDINGS"
    assert d03["file"] == str(results)
    assert d03["summary"] == d03["cause"]
    assert "--repo-root" in d03["remedy"]


def test_clearing_the_seeded_finding_removes_it(tmp_path: pathlib.Path) -> None:
    """With team/scripts/lib/ and the Python package present, D03 is no
    longer a FINDINGS verdict -- the finding tracks the tree, not the run."""
    repo = _fake_repo(tmp_path / "repo", with_lib=True)
    findings_file = tmp_path / "findings.json"

    result = _run_audit(
        "--repo-root",
        str(repo),
        "--output-file",
        str(tmp_path / "results.md"),
        "--findings-file",
        str(findings_file),
        cwd=tmp_path,
    )

    assert result.returncode == 0, _diagnostic(result)
    assert _findings_by_directive(findings_file)["D03"]["verdict"] == "MANUAL-REVIEW-REQUIRED"


def test_every_non_pass_directive_is_one_finding(tmp_path: pathlib.Path) -> None:
    """The findings file holds exactly one finding per FINDINGS or
    MANUAL-REVIEW-REQUIRED directive the summary counts."""
    repo = _fake_repo(tmp_path / "repo", with_lib=False)
    findings_file = tmp_path / "findings.json"

    result = _run_audit(
        "--repo-root",
        str(repo),
        "--output-file",
        str(tmp_path / "results.md"),
        "--findings-file",
        str(findings_file),
        cwd=tmp_path,
    )

    assert result.returncode == 0, _diagnostic(result)
    counts = {}
    for line in result.stdout.splitlines():
        label, _, value = line.strip().partition(":")
        if label in ("FINDINGS", "MANUAL-REVIEW-REQUIRED") and value.strip().isdigit():
            counts[label] = int(value.strip())
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert len(findings) == counts["FINDINGS"] + counts["MANUAL-REVIEW-REQUIRED"], _diagnostic(result)


def test_missing_repo_root_is_a_finding_and_exits_zero(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "no-such-repo"
    findings_file = tmp_path / "findings.json"

    result = _run_audit("--repo-root", str(missing), "--findings-file", str(findings_file), cwd=tmp_path)

    assert result.returncode == 0, _diagnostic(result)
    assert "error:   audit_coding_standards.repo_root_missing" in result.stderr, _diagnostic(result)
    (finding,) = json.loads(findings_file.read_text(encoding="utf-8"))
    assert finding["kind"] == "repo-root-missing"
    assert finding["repo_root"] == str(missing)


def test_missing_scripts_dir_is_a_finding_and_exits_zero(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    findings_file = tmp_path / "findings.json"

    result = _run_audit("--repo-root", str(repo), "--findings-file", str(findings_file), cwd=tmp_path)

    assert result.returncode == 0, _diagnostic(result)
    assert "error:   audit_coding_standards.scripts_dir_missing" in result.stderr, _diagnostic(result)
    (finding,) = json.loads(findings_file.read_text(encoding="utf-8"))
    assert finding["kind"] == "scripts-dir-missing"


def test_unknown_argument_still_exits_two(tmp_path: pathlib.Path) -> None:
    result = _run_audit("--no-such-flag", cwd=tmp_path)

    assert result.returncode == 2, _diagnostic(result)
    assert "unrecognized argument: --no-such-flag" in result.stderr


def test_help_states_the_exit_contract(tmp_path: pathlib.Path) -> None:
    result = _run_audit("--help", cwd=tmp_path)

    assert result.returncode == 0, _diagnostic(result)
    assert "0  The audit ran, whatever its verdicts" in result.stdout
    assert "2  Usage error" in result.stdout
    assert "--findings-file" in result.stdout
