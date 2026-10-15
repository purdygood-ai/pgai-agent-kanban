"""
test_tracked_files_respect_ignore_rules.py
==========================================
Generated files stay out of the repository.

  (1) The repository's .gitignore keeps the generated-file classes out of
      ``git status``: a log, coverage JSON, a lock file and the kanban
      runtime directories, including inside the re-included
      team/tests/artifacts/, while a hand-written file there still shows.
  (2) team/scripts/lint_tracked_ignored.py reports a tracked file an ignore
      rule matches and a tracked generated file (a .log), writes them to its
      findings file, and always exits 0; with --base it checks only files
      added since the base for the generated kind.
  (3) The lint is clean on this repository's own tree.

Every fixture repository is a fresh ``git init`` under tmp_path; nothing is
fetched, pulled or pushed.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO = _TEAM_DIR.parent
_LINT = _TEAM_DIR / "scripts" / "lint_tracked_ignored.py"


def _git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=fixture@example.com", "-c", "user.name=Fixture", *args],
        capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def _repo(tmp_path: pathlib.Path, gitignore: str | None = None) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    if gitignore is None:
        shutil.copyfile(_REPO / ".gitignore", repo / ".gitignore")
    else:
        (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore rules")
    return repo


def _write(repo: pathlib.Path, relative: str, text: str = "x\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _lint(repo: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> tuple[subprocess.CompletedProcess, list[dict]]:
    findings = tmp_path / "findings.json"
    done = subprocess.run(
        ["python3", str(_LINT), "--root", str(repo), "--findings-file", str(findings), *args],
        capture_output=True, text=True, timeout=60,
    )
    return done, json.loads(findings.read_text(encoding="utf-8"))


def test_generated_files_are_ignored_and_hand_written_artifacts_are_not(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    for generated in (
        "team/tests/artifacts/run.log",
        "team/tests/artifacts/coverage-final.json",
        "team/tests/artifacts/unit-coverage.json",
        "logs/x.log",
        "locks/a.lock",
        "run/api/api-server.pid",
        "nohup.out",
        "team/tests/artifacts/report.md.previous-RUN-1",
    ):
        _write(repo, generated)
    _write(repo, "team/tests/artifacts/notes.md")
    _write(repo, "team/tasks/TASK-CREATION.md")
    _write(repo, "team/tasks/CODER-20260101-001-x/status.md")

    status = _git(repo, "status", "--porcelain", "--untracked-files=all").split("\n")

    assert sorted(line for line in status if line) == [
        "?? team/tests/artifacts/notes.md",
    ], status


def test_lint_reports_a_tracked_ignored_file_and_a_tracked_log(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    _write(repo, "build/old.bak")
    _write(repo, "keep.md")
    _git(repo, "add", "-f", "build/old.bak")
    _git(repo, "add", "keep.md")
    _git(repo, "commit", "-q", "-m", "fixture")
    plain = tmp_path / "plain"
    plain.mkdir()
    plain_repo = _repo(plain, gitignore="*.bak\n")
    _write(plain_repo, "team/tests/artifacts/run.log")
    _git(plain_repo, "add", "team/tests/artifacts/run.log")
    _git(plain_repo, "commit", "-q", "-m", "a log")

    done, findings = _lint(repo, tmp_path)
    plain_done, plain_findings = _lint(plain_repo, plain)

    assert done.returncode == 0 and plain_done.returncode == 0, done.stderr + plain_done.stderr
    assert [(f["kind"], f["file"]) for f in findings] == [("tracked-ignored", "build/old.bak")], findings
    assert [(f["kind"], f["file"]) for f in plain_findings] == [
        ("generated-file", "team/tests/artifacts/run.log")
    ], plain_findings
    assert "lint.tracked-ignored" in done.stdout + done.stderr
    for finding in findings + plain_findings:
        assert finding["severity"] == "quality" and "<" not in finding["remedy"], finding


def test_with_a_base_only_added_generated_files_are_reported(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path, gitignore="")
    _write(repo, "old.log")
    _git(repo, "add", "old.log")
    _git(repo, "commit", "-q", "-m", "an old log")
    _git(repo, "branch", "base")
    _write(repo, "new.log")
    _git(repo, "add", "new.log")
    _git(repo, "commit", "-q", "-m", "a new log")

    _done, findings = _lint(repo, tmp_path, "--base", "base")

    assert [f["file"] for f in findings] == ["new.log"], findings


def test_lint_is_clean_on_this_repository(tmp_path: pathlib.Path) -> None:
    done, findings = _lint(_REPO, tmp_path)
    assert done.returncode == 0
    assert findings == [], findings
