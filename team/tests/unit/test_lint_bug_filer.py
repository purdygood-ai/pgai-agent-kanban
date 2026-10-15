"""
test_lint_bug_filer.py
========================
Unit tests for pgai_agent_kanban.lint.bug_filer -- the hygiene lint's
aggregate bug filer (both gated runners' findings-file write hook, via
team/scripts/lib/lint_bug_filer.sh).

Coverage:
  - A findings file carrying findings across several hygiene classes
    produces exactly one bug PER CLASS, each listing only that class's own
    findings.
  - Re-running against the same findings file while those bugs are still
    open/running cites the existing bug IDs instead of filing duplicates.
  - A findings file with an empty findings array files nothing.
  - A findings file carrying no hygiene-lint findings at all (only
    findings from other lints) files nothing.
  - A bug whose Status has moved to done/wont-do no longer suppresses a
    fresh filing for that class.
  - Findings from a non-hygiene lint (no "class" grouping semantics) never
    factor into this filer's grouping.
  - The tree-cleanliness gate: a finding whose file is untracked, or whose
    file is committed but the finding's own edit is not, files no bug for
    that class; a finding whose file matches HEAD exactly still files a
    bug, naming the commit it was observed at; a class mixing a dirty and
    a committed finding files a bug covering only the committed one.
  - The commit-content proof gate: a finding whose file is clean
    at HEAD but whose cited line does not carry the token it names files no
    bug either; a class mixing a provable and an unprovable finding files a
    bug covering only the provable one; a finding whose file is simply
    absent at the stamped (real, resolvable) commit is still filed -- the
    gate only drops what it can prove wrong, never what it merely cannot
    check.
  - The commit resolution gate: handed a repo_root that is not a git
    repository, or a git repository with no commits yet, this filer files
    nothing at all for the run and names the reason on stderr -- never
    stamping "## Observed At Commit" with the literal "unknown".

Most assertions are against the pure file_or_cite_hygiene_bugs() function
operating on a tmp_path bugs/ directory and a tmp_path findings.json file --
no subprocess, no gated runner invoked. The tree-cleanliness gate tests are
the one exception: they build a real throwaway git repository under
tmp_path and pass it via file_or_cite_hygiene_bugs()'s own repo_root
parameter, since that gate's whole job is reading real git state -- still
no gated runner invoked, and never the live install or this checkout's own
git state. tmp_path is redirected under $PGAI_AGENT_KANBAN_TEMP_DIR by
team/tests/conftest.py's pytest_configure, so every fixture this file
creates already satisfies the framework's temp-file hygiene rule without
any extra plumbing here.

Test names describe behaviour under test, never bug/task IDs (team/roles/
TESTER.md, Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

from pgai_agent_kanban.lint.bug_filer import (
    FilingResult,
    file_or_cite_hygiene_bugs,
    find_existing_bug_for_class,
)

_LINT_HYGIENE_SOURCE = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint_hygiene.py"


def _write_findings_file(path: pathlib.Path, findings: list) -> None:
    doc = {
        "schema_version": 1,
        "generated_at": "2026-09-10T00:00:00+00:00",
        "runner": "run-unit-tests.sh",
        "findings": findings,
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


def _hygiene_finding(cls: str, file: str, line: int, token: str) -> dict:
    return {
        "lint": "hygiene",
        "class": cls,
        "severity": "quality",
        "file": file,
        "line": line,
        "label": "BUG-ID",
        "token": token,
        "cause": f"{file}:{line}: [BUG-ID] provenance reference '{token}' in code",
        "remedy": f"reword {file}:{line} to describe behavior instead of citing {token}",
    }


def _write_bug(
    bugs_dir: pathlib.Path,
    bug_id: str,
    *,
    status: str = "open",
    cls: str = "provenance",
    slug: str = "hygiene-lint-provenance",
) -> pathlib.Path:
    """Write a minimal well-formed bug file directly (bypassing the filer)
    so dedup tests can set up "an existing bug already covers this class"
    without depending on file_or_cite_hygiene_bugs() to have created it
    first."""
    text = (
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        "**Filed By:** hygiene-lint (autonomous) — test fixture.\n"
        "**Date:** 2026-09-01\n"
        "**Severity:** low\n\n"
        f"## Status\n{status}\n\n"
        "## Category\nhygiene-lint\n\n"
        "---\n\n"
        "## Symptom\ntest fixture\n\n"
        "---\n\n"
        "## Hygiene Lint Class\n"
        f"{cls}\n"
    )
    path = bugs_dir / f"{bug_id}-{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Aggregation -- one bug per class, listing every instance of that class
# ---------------------------------------------------------------------------


def test_twelve_findings_across_three_classes_produce_exactly_three_bugs(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"

    findings = (
        [_hygiene_finding("provenance", f"a{i}.py", i, f"BUG-000{i}") for i in range(1, 6)]
        + [_hygiene_finding("comment-drift", f"b{i}.py", i, f"drift{i}") for i in range(1, 5)]
        + [_hygiene_finding("version-named-test", f"c{i}.py", i, f"v1_{i}_0") for i in range(1, 4)]
    )
    assert len(findings) == 12
    _write_findings_file(findings_path, findings)

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path)
    )

    assert len(results) == 3
    assert {r.cls for r in results} == {"provenance", "comment-drift", "version-named-test"}
    assert all(r.outcome == "filed" for r in results)

    filed_files = sorted(bugs_dir.glob("BUG-*.md"))
    assert len(filed_files) == 3

    by_class = {r.cls: r for r in results}
    assert by_class["provenance"].count == 5
    assert by_class["comment-drift"].count == 4
    assert by_class["version-named-test"].count == 3

    provenance_content = pathlib.Path(by_class["provenance"].bug_path).read_text(encoding="utf-8")
    for i in range(1, 6):
        assert f"a{i}.py:{i}" in provenance_content
    # No cross-contamination: the provenance bug does not list comment-drift
    # or version-named-test instances.
    for i in range(1, 5):
        assert f"b{i}.py:{i}" not in provenance_content
    for i in range(1, 4):
        assert f"c{i}.py:{i}" not in provenance_content


def test_filed_bug_carries_hygiene_class_section_and_status_open(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "x.py", 3, "BUG-0009")])

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert len(results) == 1
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")
    assert "## Hygiene Lint Class\nprovenance" in content
    assert "## Status\nopen" in content
    assert "**Filed By:** hygiene-lint (autonomous)" in content
    assert "**Severity:** low" in content


# ---------------------------------------------------------------------------
# Tree-cleanliness gate -- a finding only earns a durable bug when its file
# carries no uncommitted change relative to HEAD (see bug_filer.py's own
# "Tree-cleanliness gate" section). Every test in this block builds a real
# throwaway git repository (never the live install, and never this
# checkout) and passes it explicitly via repo_root, so none of them depend
# on this checkout's own git state.
# ---------------------------------------------------------------------------


def _init_repo(root: pathlib.Path) -> None:
    """Initialize a git repository at *root* with a throwaway committer
    identity set locally (never touching the operator's own git config, and
    never depending on one being configured at all)."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "hygiene-gate-test@example.invalid"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "hygiene-gate-test"], cwd=str(root), check=True)


def _commit_file(root: pathlib.Path, relpath: str, content: str, message: str) -> str:
    """Write *relpath* under *root* with *content*, commit it, and return
    the resulting commit's full hash."""
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", relpath], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=str(root), check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def test_finding_in_an_untracked_file_files_no_bug_for_that_class(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    # Never added or committed -- the exact working-copy-only shape an
    # in-progress agent edit takes before it is either committed or
    # reverted.
    (repo / "scratch.py").write_text("# a line an agent typed and has not committed\n", encoding="utf-8")

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "scratch.py", 1, "TOKEN-1")])

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(repo)
    )

    assert results == []
    if bugs_dir.exists():
        assert list(bugs_dir.glob("BUG-*.md")) == []


def test_finding_in_a_modified_but_uncommitted_file_files_no_bug(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "existing.py", "def foo():\n    return 1\n", "add existing.py")
    # Modify the already-committed file without committing the change --
    # the file exists in HEAD, but the line the finding cites does not.
    (repo / "existing.py").write_text(
        "def foo():\n    return 1\n\n# an uncommitted edit\n", encoding="utf-8"
    )

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "existing.py", 4, "TOKEN-2")])

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(repo)
    )

    assert results == []


def test_finding_in_a_committed_file_still_files_a_bug_naming_the_observed_commit(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    # The committed line carries the exact token the finding cites -- the
    # commit-content proof gate requires this now, on top of the
    # tree-cleanliness gate this test already exercised.
    commit = _commit_file(repo, "clean.py", "def bar():  # TOKEN-3\n    return 2\n", "add clean.py")

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "clean.py", 1, "TOKEN-3")])

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(repo)
    )

    assert len(results) == 1
    assert results[0].outcome == "filed"
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")
    assert f"## Observed At Commit\n{commit}" in content


def test_class_with_one_dirty_and_one_committed_finding_files_only_the_committed_one(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    # Same reason as the previous test: the committed line must actually
    # carry the cited token now that the commit-content proof gate reads it
    # back at the stamped commit.
    commit = _commit_file(repo, "clean.py", "def baz():  # TOKEN-4\n    return 3\n", "add clean.py")
    (repo / "dirty.py").write_text("# never committed\n", encoding="utf-8")

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(
        findings_path,
        [
            _hygiene_finding("provenance", "clean.py", 1, "TOKEN-4"),
            _hygiene_finding("provenance", "dirty.py", 1, "TOKEN-5"),
        ],
    )

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(repo)
    )

    assert len(results) == 1
    assert results[0].outcome == "filed"
    assert results[0].count == 1
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")
    assert "clean.py:1" in content
    assert "dirty.py:1" not in content
    assert f"## Observed At Commit\n{commit}" in content


# ---------------------------------------------------------------------------
# Commit-content proof gate -- a finding whose file is clean at HEAD (so it
# passes the tree-cleanliness gate above) but whose own cited line does not
# carry the text it names, at the very commit the filing is about to stamp,
# must not be filed either: a bug nobody can reproduce from the commit it
# names costs a decomposition and an implementer's session for nothing.
# Every test in this block builds a real throwaway git repository, same
# convention as the tree-cleanliness block above.
# ---------------------------------------------------------------------------


def test_finding_whose_cited_text_is_absent_at_head_files_no_bug(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    # The file is genuinely clean at HEAD (passes the tree-cleanliness gate)
    # -- but its committed line 1 never carried the cited token at all: a
    # resolvable commit whose tree disagrees with the finding.
    _commit_file(
        repo,
        "fixture.py",
        "# fictitious BUG-9001-style identifier, never a real one\n",
        "add fixture.py",
    )

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "fixture.py", 1, "BUG-0001")])

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(repo)
    )

    assert results == []
    if bugs_dir.exists():
        assert list(bugs_dir.glob("BUG-*.md")) == []


def test_class_with_one_provable_and_one_unprovable_finding_files_only_the_provable_one(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "provable.py", "x = 1  # TOKEN-A\n", "add provable.py")
    # HEAD after this second commit is the commit this run stamps.
    commit = _commit_file(repo, "unprovable.py", "x = 2  # not the cited token\n", "add unprovable.py")

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(
        findings_path,
        [
            _hygiene_finding("provenance", "provable.py", 1, "TOKEN-A"),
            _hygiene_finding("provenance", "unprovable.py", 1, "TOKEN-B"),
        ],
    )

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(repo)
    )

    assert len(results) == 1
    assert results[0].outcome == "filed"
    assert results[0].count == 1
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")
    assert "provable.py:1" in content
    assert "unprovable.py:1" not in content
    assert f"## Observed At Commit\n{commit}" in content


def test_finding_in_a_file_absent_at_the_commit_is_still_filed(tmp_path: pathlib.Path) -> None:
    """A finding whose file cannot be read back at all at the stamped commit
    -- present in this real, resolvable repository, but never at this
    particular path -- is treated as provable -- the gate only drops a
    finding it can PROVE wrong, never one it simply cannot check (see
    bug_filer.py's "Commit-content proof gate" docstring section, "narrower
    than 'the file must exist here'"). This is what keeps the module's own
    pure-aggregation coverage above (fictitious files such as "a1.py",
    checked against this suite's real running checkout when repo_root is
    not supplied) passing unaffected by this gate."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    # A real, resolvable commit exists -- it just never carried "ghost.py".
    commit = _commit_file(repo, "unrelated.py", "x = 1\n", "add unrelated.py")

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "ghost.py", 1, "TOKEN-C")])

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(repo)
    )

    assert len(results) == 1
    assert results[0].outcome == "filed"
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")
    assert f"## Observed At Commit\n{commit}" in content


# ---------------------------------------------------------------------------
# Commit resolution gate -- handed a repo_root this run cannot resolve any
# commit for at all (not a git repository, or a git repository with no
# commits yet), file_or_cite_hygiene_bugs must file nothing for the entire
# run and name the reason on stderr, rather than stamping "## Observed At
# Commit" with the literal "unknown" (see bug_filer.py's "Commit resolution
# gate" and "Repository root resolution" docstring sections).
# ---------------------------------------------------------------------------


def test_repo_root_that_is_not_a_git_repository_files_nothing_and_says_why(
    tmp_path: pathlib.Path, capsys
) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "ghost.py", 1, "TOKEN-C")])

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(not_a_repo)
    )

    assert results == []
    if bugs_dir.exists():
        assert list(bugs_dir.glob("BUG-*.md")) == []
    stderr = capsys.readouterr().err
    assert str(not_a_repo) in stderr
    assert "cannot resolve a commit" in stderr


def test_git_repository_with_no_commits_yet_files_nothing_and_says_why(
    tmp_path: pathlib.Path, capsys
) -> None:
    empty_repo = tmp_path / "empty-repo"
    _init_repo(empty_repo)
    # _init_repo only runs `git init`; no commit has been made, so HEAD
    # does not resolve to anything yet.

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "ghost.py", 1, "TOKEN-D")])

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(findings_path), repo_root=str(empty_repo)
    )

    assert results == []
    stderr = capsys.readouterr().err
    assert "cannot resolve a commit" in stderr


def test_pgai_dev_tree_path_env_var_is_used_when_repo_root_argument_is_omitted(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """A caller that knows its own scan root but calls through the CLI-style
    interface (no repo_root kwarg) still gets that root respected via
    PGAI_DEV_TREE_PATH -- the same variable the gated runners already
    export to their own checkout (see bug_filer.py's "Repository root
    resolution")."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    commit = _commit_file(repo, "clean.py", "def qux():  # TOKEN-E\n    return 4\n", "add clean.py")
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(repo))

    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "clean.py", 1, "TOKEN-E")])

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))

    assert len(results) == 1
    assert results[0].outcome == "filed"
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")
    assert f"## Observed At Commit\n{commit}" in content


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_rerun_against_same_findings_cites_existing_open_bugs_and_files_nothing(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"

    findings = (
        [_hygiene_finding("provenance", "a.py", 1, "BUG-0001")]
        + [_hygiene_finding("comment-drift", "b.py", 2, "drift")]
        + [_hygiene_finding("version-named-test", "c.py", 3, "v1_0_0")]
    )
    _write_findings_file(findings_path, findings)

    first = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert {r.outcome for r in first} == {"filed"}
    filed_ids = {r.cls: r.bug_id for r in first}

    files_after_first = sorted(bugs_dir.glob("BUG-*.md"))

    second = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert len(second) == 3
    assert {r.outcome for r in second} == {"duplicate"}
    assert {r.cls: r.bug_id for r in second} == filed_ids
    assert all(r.bug_path is None for r in second)

    # No new files were written on the re-run.
    assert sorted(bugs_dir.glob("BUG-*.md")) == files_after_first


def test_existing_running_bug_also_suppresses_a_fresh_filing(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0044", status="running", cls="provenance")

    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "a.py", 1, "BUG-0009")])

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert len(results) == 1
    assert results[0].outcome == "duplicate"
    assert results[0].bug_id == "BUG-0044"


def test_bug_moved_to_done_no_longer_suppresses_a_fresh_filing(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0044", status="done", cls="provenance")

    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "a.py", 1, "BUG-0009")])

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert len(results) == 1
    assert results[0].outcome == "filed"
    assert results[0].bug_id != "BUG-0044"


def test_find_existing_bug_for_class_returns_none_when_bugs_dir_absent(
    tmp_path: pathlib.Path,
) -> None:
    missing_dir = tmp_path / "bugs"
    assert find_existing_bug_for_class(str(missing_dir), "provenance") is None


def test_existing_bug_for_a_different_class_does_not_suppress_a_fresh_filing(
    tmp_path: pathlib.Path,
) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    _write_bug(bugs_dir, "BUG-0044", status="open", cls="comment-drift")

    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [_hygiene_finding("provenance", "a.py", 1, "BUG-0009")])

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert len(results) == 1
    assert results[0].outcome == "filed"
    assert results[0].bug_id != "BUG-0044"


# ---------------------------------------------------------------------------
# Zero-findings and non-hygiene-lint paths
# ---------------------------------------------------------------------------


def test_empty_findings_array_files_no_bug(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, [])

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))

    assert results == []
    assert not bugs_dir.exists()


def test_findings_from_a_non_hygiene_lint_are_ignored(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    _write_findings_file(
        findings_path,
        [
            {
                "lint": "dual_channel_pair",
                "severity": "quality",
                "exit_code": 1,
                "summary": "some other lint's own finding shape",
                "detail": "not a hygiene finding",
            }
        ],
    )

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))

    assert results == []
    assert not bugs_dir.exists()


def test_missing_findings_file_files_no_bug_and_does_not_raise(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    missing_findings_path = tmp_path / "does-not-exist.json"

    results = file_or_cite_hygiene_bugs(
        str(bugs_dir), project="demo", findings_path=str(missing_findings_path)
    )

    assert results == []


def test_malformed_findings_file_files_no_bug_and_does_not_raise(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    findings_path = tmp_path / "findings.json"
    findings_path.write_text("{not valid json", encoding="utf-8")

    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))

    assert results == []


# ---------------------------------------------------------------------------
# FilingResult shape
# ---------------------------------------------------------------------------


def test_filing_result_is_a_dataclass_with_the_documented_fields() -> None:
    result = FilingResult(cls="provenance", outcome="filed", bug_id="BUG-0001", bug_path="/x", count=3)
    assert result.cls == "provenance"
    assert result.outcome == "filed"
    assert result.bug_id == "BUG-0001"
    assert result.bug_path == "/x"
    assert result.count == 3


# ---------------------------------------------------------------------------
# Remedy portability -- a filed bug must not name the ephemeral tree the
# lint happened to run in, and every command it persists must still run
# from any fresh checkout carrying the same finding.
# ---------------------------------------------------------------------------


def _build_checkout(root: pathlib.Path, name: str) -> pathlib.Path:
    """Create a throwaway checkout at *root* / *name* with lint_hygiene.py
    copied to the same repo-relative location it occupies in a real dev
    tree (team/scripts/lint_hygiene.py), so invoking it via subprocess
    reproduces the real default-repo-root computation instead of a
    synthetic scan root decoupled from the script's own location."""
    checkout = root / name
    scripts_dir = checkout / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy(_LINT_HYGIENE_SOURCE, scripts_dir / "lint_hygiene.py")
    return checkout


def test_filed_bug_remedies_omit_the_scan_root_and_run_from_a_fresh_checkout(
    tmp_path: pathlib.Path,
) -> None:
    checkout = _build_checkout(tmp_path, "checkout")
    code_dir = checkout / "code"
    code_dir.mkdir()
    (code_dir / "dirty.py").write_text(
        "def foo():\n    # This references BUG-0042 directly in a comment.\n    return 1\n",
        encoding="utf-8",
    )
    (checkout / "empty_tests").mkdir()
    (checkout / "empty_prose").mkdir()
    raw_findings_path = checkout / "raw-findings.json"

    lint_result = subprocess.run(
        [
            sys.executable, "team/scripts/lint_hygiene.py",
            "--code-dirs", "code",
            "--test-tree-dirs", "empty_tests",
            "--prose-dirs", "empty_prose",
            "--findings-file", str(raw_findings_path),
        ],
        cwd=str(checkout),
        capture_output=True,
        text=True,
    )
    assert lint_result.returncode == 0, lint_result.stderr

    raw_findings = json.loads(raw_findings_path.read_text(encoding="utf-8"))
    assert len(raw_findings) == 1, raw_findings

    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, raw_findings)

    bugs_dir = tmp_path / "bugs"
    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert len(results) == 1
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")

    scan_root = str(checkout)
    assert scan_root not in content, (
        f"filed bug still names the ephemeral scan root {scan_root!r} that produced it: {content}"
    )
    assert "repository root" in content.lower(), (
        "the ## Remedies section must tell the reader where to run its commands from"
    )

    match = re.search(r'^\d+\. .*(python3 "team/scripts/lint_hygiene\.py".*)$', content, re.MULTILINE)
    assert match, f"no --add-suppression command found in filed bug: {content}"
    remedy_command = match.group(1).strip()

    # Run the persisted command from a SECOND, independent checkout that
    # carries the same finding -- not the tree that generated the bug --
    # to prove the command is portable rather than merely relative to a
    # tree that happens to still exist.
    second_checkout = _build_checkout(tmp_path, "second-checkout")
    second_code_dir = second_checkout / "code"
    second_code_dir.mkdir()
    shutil.copy(code_dir / "dirty.py", second_code_dir / "dirty.py")

    add_result = subprocess.run(
        remedy_command, shell=True, cwd=str(second_checkout), capture_output=True, text=True,
    )
    assert add_result.returncode == 0, add_result.stderr

    sidecar_path = second_checkout / "team" / "scripts" / "hygiene-lint-allowlist.json"
    sidecar_entries = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert len(sidecar_entries) == 1
    assert sidecar_entries[0]["file"] == "code/dirty.py"
    assert sidecar_entries[0]["line_hint"] == 2
    assert sidecar_entries[0]["class"] == "provenance"


# ---------------------------------------------------------------------------
# A finding whose class has no --classes spelling (the malformed-sidecar
# finding) must still yield a filed bug with no scan-root leak and a
# Reproduction command the lint's own parser accepts.
# ---------------------------------------------------------------------------


def test_filed_bug_for_an_unregistered_class_omits_the_scan_root_and_runs(
    tmp_path: pathlib.Path,
) -> None:
    checkout = _build_checkout(tmp_path, "checkout")
    scripts_dir = checkout / "team" / "scripts"
    (checkout / "empty_code").mkdir()
    (checkout / "empty_tests").mkdir()
    (checkout / "empty_prose").mkdir()

    # A sidecar entry missing its justification -- the malformed shape
    # the lint reports as a finding with no --classes spelling.
    (scripts_dir / "hygiene-lint-allowlist.json").write_text(
        json.dumps(
            [
                {
                    "file": "team/scripts/why.sh",
                    "class": "provenance",
                    "content_hash": "deadbeef0000",
                    "justification": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    raw_findings_path = checkout / "raw-findings.json"

    lint_result = subprocess.run(
        [
            sys.executable, "team/scripts/lint_hygiene.py",
            "--code-dirs", "empty_code",
            "--test-tree-dirs", "empty_tests",
            "--prose-dirs", "empty_prose",
            "--findings-file", str(raw_findings_path),
        ],
        cwd=str(checkout),
        capture_output=True,
        text=True,
    )
    assert lint_result.returncode == 0, lint_result.stderr

    raw_findings = json.loads(raw_findings_path.read_text(encoding="utf-8"))
    assert len(raw_findings) == 1, raw_findings
    assert raw_findings[0]["class"] == "hygiene-sidecar"
    unregistered_class = raw_findings[0]["class"]

    findings_path = tmp_path / "findings.json"
    _write_findings_file(findings_path, raw_findings)

    bugs_dir = tmp_path / "bugs"
    results = file_or_cite_hygiene_bugs(str(bugs_dir), project="demo", findings_path=str(findings_path))
    assert len(results) == 1
    content = pathlib.Path(results[0].bug_path).read_text(encoding="utf-8")

    scan_root = str(checkout)
    assert scan_root not in content, (
        f"filed bug still names the ephemeral scan root {scan_root!r} that produced it: {content}"
    )

    reproduction_section = content.split("## Reproduction", 1)[1].split("## Files Involved", 1)[0]
    match = re.search(r"`([^`]*python3[^`]*)`", reproduction_section)
    assert match, f"no runnable command found in the Reproduction section: {reproduction_section}"
    reproduction_command = match.group(1)
    assert "--classes" not in reproduction_command, (
        f"reproduction command still passes an unregistered class to --classes: {reproduction_command}"
    )

    repro_result = subprocess.run(
        reproduction_command, shell=True, cwd=str(checkout), capture_output=True, text=True,
    )
    assert repro_result.returncode == 0, (
        f"reproduction command {reproduction_command!r} did not exit 0: {repro_result.stderr}"
    )
    assert unregistered_class in repro_result.stdout or "hygiene-lint-allowlist.json" in repro_result.stdout, (
        f"reproduction command output does not surface the finding it claims to reproduce: {repro_result.stdout}"
    )
