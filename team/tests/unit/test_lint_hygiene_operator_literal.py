"""
test_lint_hygiene_operator_literal.py
======================================
Tests for the "operator-literal" class of team/scripts/lint_hygiene.py:
operator-specific home-directory literals, private SSH host aliases and
operator forge handles in tracked files.

Tests cover:

  1. **Detection, both directions** -- a fixture doc carrying a real
     account's home path and a private ``git@<alias>:`` remote yields
     exactly one finding for each, naming file and line; the same doc
     rewritten with the neutral forms yields none.

  2. **Scope** -- a seeded home path under release-notes/, in the root
     CHANGELOG.md, and under team/tests/unit/ is not reported; one under
     team/tests/artifacts/ is. Home paths and private host aliases stay
     unreported anywhere on team/tests/ outside artifacts/, and the scope
     function names the labels each kind of path takes.

  3. **Never blocks** -- the lint exits 0 with findings present.

  4. **The tracked-file surface** -- an untracked file is not read, a
     binary file is skipped, a ``--repo-root`` that is not itself a git
     work-tree top scans nothing (no ancestor repository is borrowed), and
     the directory-surface flags do not narrow the scan.

  5. **Placeholder handling and finding shape** -- separator/case variants
     of the placeholder names are neutral, a line carrying a placeholder
     and a real name is reported under the real name, every finding
     carries a remedy free of angle-bracket placeholders, and the sidecar
     suppresses an operator-literal finding the same way it does for every
     other class.

  6. **Report wording follows the classes reported** -- an
     operator-literal-only report closes with the neutral-placeholder rule
     and suggests a literal-path suppression justification, never the
     provenance rule or its internal-identifier justification; a report
     holding both a provenance and an operator-literal finding states both
     rules; and every registered class carries a rule and an example
     justification of its own.

  7. **Operator handle** -- the operator's forge login as a git author, an
     email local part or a backticked identity, in any case, is reported
     once per line under ``operator-handle`` with a placeholder-free
     remedy; the organisation name built on it (the clone URL over https
     and ssh, and the licence's domain form) is not. The handle is
     reported on the test tree too -- a corpus fixture, its JSON twin, a
     test module docstring -- where the organisation forms stay unflagged
     and a line carrying every label yields the handle finding alone.

Every fixture is a throwaway git repository under pytest's ``tmp_path``;
files are staged with ``git add`` (never committed), which is all
``git ls-files`` needs. No remote is configured and nothing is pushed.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_hygiene.py as a module under a name of its own, so this module's
# import never replaces the one test_lint_hygiene.py registers.
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_hygiene.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_hygiene_operator_literal_under_test", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve their module through sys.modules while the module
    # executes, so it must be registered first.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()

_CLASS = "operator-literal"

_DIRTY_DOC = "# Setup\n" "\n" "Install under /home/alice/x first.\n" "\n" "git clone git@github-work:org/repo.git\n"

_NEUTRAL_DOC = (
    "# Setup\n"
    "\n"
    "Install under $HOME/pgai_agent_kanban first.\n"
    "JSON samples may show /home/<user>/pgai_agent_kanban.\n"
    "A literal example home is /home/you/ here.\n"
    "git clone git@github.com:org/repo.git\n"
    "git clone git@example.com:org/repo.git\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    return repo


def _write(repo: Path, relpath: str, content: str) -> Path:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _stage_all(repo: Path) -> None:
    _git(repo, "add", "-A")


def _run(tmp_path: Path, repo_root: Path, *, sidecar: Path | None = None) -> tuple[int, list[dict]]:
    """Run only the operator-literal class with --repo-root at *repo_root*
    and every directory surface pinned to an empty directory outside it.
    Returns (exit_code, findings)."""
    empty = tmp_path / "empty_surface"
    empty.mkdir(exist_ok=True)
    findings_path = tmp_path / "findings.json"
    if findings_path.exists():
        findings_path.unlink()
    argv = [
        "--code-dirs",
        str(empty),
        "--test-tree-dirs",
        str(empty),
        "--prose-dirs",
        str(empty),
        "--docs-dirs",
        str(empty),
        "--config-dirs",
        str(empty),
        "--classes",
        _CLASS,
        "--repo-root",
        str(repo_root),
        "--sidecar",
        str(sidecar or (tmp_path / "sidecar.json")),
        "--findings-file",
        str(findings_path),
    ]
    result = _lint.main(argv)
    findings = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path.exists() else []
    return result, findings


def _by_label(findings: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in findings:
        out.setdefault(f["label"], []).append(f)
    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_operator_literal_class_is_registered() -> None:
    assert _CLASS in _lint.registered_class_names()


# ---------------------------------------------------------------------------
# 1. Detection, both directions
# ---------------------------------------------------------------------------


def test_home_path_and_private_host_alias_each_reported_once(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/setup.md", _DIRTY_DOC)
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    labels = _by_label(findings)
    assert sorted(labels) == ["home-path", "ssh-host-alias"], findings
    assert len(labels["home-path"]) == 1, findings
    assert len(labels["ssh-host-alias"]) == 1, findings

    home = labels["home-path"][0]
    assert home["class"] == _CLASS
    assert home["file"] == "docs/setup.md"
    assert home["line"] == 3
    assert home["token"] == "/home/alice"

    alias = labels["ssh-host-alias"][0]
    assert alias["class"] == _CLASS
    assert alias["file"] == "docs/setup.md"
    assert alias["line"] == 5
    assert alias["token"] == "git@github-work:"


def test_neutral_placeholders_yield_no_findings(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/setup.md", _NEUTRAL_DOC)
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    assert findings == [], f"neutral placeholders must not be findings: {findings}"


# ---------------------------------------------------------------------------
# 2. Scope
# ---------------------------------------------------------------------------


def test_home_path_in_release_notes_changelog_and_test_tree_is_unreported_but_test_artifacts_are_in(
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    seeded = "Seeded path: /home/alice/x\n"
    _write(repo, "release-notes/v9.9.9.md", seeded)
    _write(repo, "CHANGELOG.md", seeded)
    _write(repo, "team/tests/unit/test_seeded.py", "# " + seeded)
    _write(repo, "team/tests/artifacts/snapshot.md", seeded)
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    assert [(f["file"], f["line"], f["label"]) for f in findings] == [
        ("team/tests/artifacts/snapshot.md", 1, "home-path")
    ], findings


def test_changelog_exclusion_is_the_root_file_only(tmp_path: Path) -> None:
    """Only the rendered root CHANGELOG.md is out of scope; a same-named file
    elsewhere is ordinary shipped content."""
    repo = _make_repo(tmp_path)
    _write(repo, "team/demos/app/CHANGELOG.md", "Built in /home/alice/app\n")
    _stage_all(repo)

    _, findings = _run(tmp_path, repo)

    assert [f["file"] for f in findings] == ["team/demos/app/CHANGELOG.md"], findings


def test_home_path_and_host_alias_stay_unreported_on_the_test_tree(tmp_path: Path) -> None:
    """Only the operator handle is reported on team/tests/ outside artifacts/;
    a home path or a private host alias in a fixture or a test docstring is
    not."""
    repo = _make_repo(tmp_path)
    _write(repo, "team/tests/fixtures/tester_reports/corpus/report.md", _DIRTY_DOC)
    _write(
        repo,
        "team/tests/unit/test_seeded.py",
        '"""Clone git@github-work:org/repo.git into /home/alice/x first."""\n',
    )
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    assert findings == [], findings


@pytest.mark.parametrize(
    ("relpath", "expected"),
    [
        ("team/scripts/run.sh", {"home-path", "ssh-host-alias", "operator-handle"}),
        ("team/tests/artifacts/snapshot.md", {"home-path", "ssh-host-alias", "operator-handle"}),
        ("team/tests/fixtures/tester_reports/corpus/report.md", {"operator-handle"}),
        ("team/tests/unit/test_x.py", {"operator-handle"}),
        ("release-notes/v9.9.9.md", set()),
        ("CHANGELOG.md", set()),
    ],
)
def test_scope_names_the_labels_each_path_takes(relpath: str, expected: set[str]) -> None:
    assert _lint._operator_literal_in_scope(relpath) == expected


# ---------------------------------------------------------------------------
# 3. Never blocks
# ---------------------------------------------------------------------------


def test_exits_zero_when_findings_exist(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "team/scripts/run.sh", "#!/usr/bin/env bash\ncd /home/alice/work\n")
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert findings, "fixture must produce at least one finding for this test to mean anything"
    assert result == 0, "a quality-class lint must exit 0 whatever it finds"


# ---------------------------------------------------------------------------
# 4. The tracked-file surface
# ---------------------------------------------------------------------------


def test_untracked_file_is_not_scanned(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/tracked.md", "clean\n")
    _stage_all(repo)
    _write(repo, "docs/scratch.md", "left behind: /home/alice/x\n")

    _, findings = _run(tmp_path, repo)

    assert findings == [], findings


def test_binary_file_is_skipped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\x00\x01/home/alice/x\x00")
    _stage_all(repo)

    _, findings = _run(tmp_path, repo)

    assert findings == [], findings


def test_non_git_root_scans_nothing(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    _write(root, "docs/setup.md", _DIRTY_DOC)

    result, findings = _run(tmp_path, root)

    assert result == 0
    assert findings == [], findings


def test_subdirectory_of_a_repository_does_not_borrow_the_ancestor(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "sub/docs/setup.md", _DIRTY_DOC)
    _stage_all(repo)

    _, findings = _run(tmp_path, repo / "sub")

    assert findings == [], findings


def test_directory_surface_flags_do_not_narrow_the_tracked_surface(tmp_path: Path) -> None:
    """_run pins every --*-dirs flag to one empty directory; a tracked file
    under an arbitrary top-level directory is still found."""
    repo = _make_repo(tmp_path)
    _write(repo, "anywhere/config.example", "# export HOME=/home/alice\n")
    _stage_all(repo)

    _, findings = _run(tmp_path, repo)

    assert [(f["file"], f["line"]) for f in findings] == [("anywhere/config.example", 1)], findings


# ---------------------------------------------------------------------------
# 5. Placeholder handling and finding shape
# ---------------------------------------------------------------------------


def test_placeholder_name_variants_and_variable_segments_are_neutral(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(
        repo,
        "docs/variants.md",
        "export HOME=/home/your-user\n"
        "export HOME=/home/USERNAME\n"
        "cd /home/op/pgai_agent_kanban\n"
        "cd /home/user/develop\n"
        "cd /home/youruser/develop\n"
        "cd /home/$USER/develop\n"
        "cd ~/develop/my-app\n"
        "see https://example.org/home/alice for the web page\n"
        "git remote add origin git@example.invalid:org/repo.git\n"
        "git remote add origin git@forge.test:org/repo.git\n"
        "mail git@github-work.example.com without a colon is not a remote\n",
    )
    _stage_all(repo)

    _, findings = _run(tmp_path, repo)

    assert findings == [], findings


def test_line_with_placeholder_and_real_name_is_reported_under_the_real_name(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/mixed.md", "copy /home/you/a to /home/bob/b\n")
    _stage_all(repo)

    _, findings = _run(tmp_path, repo)

    assert [(f["line"], f["token"]) for f in findings] == [(1, "/home/bob")], findings


def test_every_finding_has_a_remedy_without_angle_bracket_placeholders(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/setup.md", _DIRTY_DOC)
    _stage_all(repo)

    _, findings = _run(tmp_path, repo)

    assert len(findings) == 2, findings
    for f in findings:
        assert f["remedy"], f
        assert "<" not in f["remedy"] and ">" not in f["remedy"], f["remedy"]
        assert f"{f['file']}:{f['line']}" in f["remedy"], f["remedy"]


def test_sidecar_suppresses_an_operator_literal_finding(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/setup.md", _DIRTY_DOC)
    _stage_all(repo)
    sidecar = tmp_path / "sidecar.json"

    rc = _lint.main(
        [
            "--add-suppression",
            "docs/setup.md",
            "3",
            _CLASS,
            "kept as a worked example",
            "--sidecar",
            str(sidecar),
            "--repo-root",
            str(repo),
        ]
    )
    assert rc == 0

    _, findings = _run(tmp_path, repo, sidecar=sidecar)

    assert [(f["line"], f["label"]) for f in findings] == [(5, "ssh-host-alias")], findings


# ---------------------------------------------------------------------------
# 6. Report wording follows the classes reported
# ---------------------------------------------------------------------------

_PROVENANCE_RULE_FRAGMENT = "bug/task/priority IDs"
_PROVENANCE_JUSTIFICATION = "internal identifier reference"
_NEUTRAL_RULE_FRAGMENT = "neutral placeholders"


def _report(
    tmp_path: Path,
    repo_root: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    classes: list[str],
    code_dir: Path | None = None,
) -> str:
    """Run the lint for *classes* and return the printed report. Every
    directory surface is pinned to an empty directory except --code-dirs,
    which is *code_dir* when given."""
    empty = tmp_path / "empty_surface"
    empty.mkdir(exist_ok=True)
    capsys.readouterr()
    rc = _lint.main(
        [
            "--code-dirs",
            str(code_dir or empty),
            "--test-tree-dirs",
            str(empty),
            "--prose-dirs",
            str(empty),
            "--docs-dirs",
            str(empty),
            "--config-dirs",
            str(empty),
            "--classes",
            *classes,
            "--repo-root",
            str(repo_root),
            "--sidecar",
            str(tmp_path / "sidecar.json"),
        ]
    )
    assert rc == 0, "a quality-class lint must exit 0 whatever it finds"
    return capsys.readouterr().out


def _rule_lines(report: str) -> list[str]:
    return [line for line in report.splitlines() if line.startswith("Rule")]


def test_operator_literal_report_states_its_own_rule_and_justification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/guide.md", "/home/alice/x\n")
    _stage_all(repo)

    report = _report(tmp_path, repo, capsys, classes=[_CLASS])

    assert "docs/guide.md:1" in report, report
    assert _PROVENANCE_RULE_FRAGMENT not in report, report
    assert _PROVENANCE_JUSTIFICATION not in report, report
    rules = _rule_lines(report)
    assert len(rules) == 1, rules
    assert rules[0].startswith("Rule: "), rules
    assert _NEUTRAL_RULE_FRAGMENT in rules[0], rules
    suggestion = next(line for line in report.splitlines() if "--add-suppression" in line)
    assert f'--add-suppression "docs/guide.md" 1 {_CLASS} ' in suggestion, suggestion
    assert "deliberate literal path" in suggestion, suggestion


def test_report_with_provenance_and_operator_literal_findings_states_both_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/guide.md", "/home/alice/x\n")
    code_dir = repo / "code"
    _write(repo, "code/dirty.py", "def foo():\n    # This references BUG-0042 directly in a comment.\n    return 1\n")
    _stage_all(repo)

    report = _report(tmp_path, repo, capsys, classes=["provenance", _CLASS], code_dir=code_dir)

    rules = _rule_lines(report)
    assert len(rules) == 2, rules
    provenance_rule = next(r for r in rules if r.startswith("Rule (provenance): "))
    operator_rule = next(r for r in rules if r.startswith(f"Rule ({_CLASS}): "))
    assert _PROVENANCE_RULE_FRAGMENT in provenance_rule, rules
    assert _NEUTRAL_RULE_FRAGMENT in operator_rule, rules
    suggestions = [line for line in report.splitlines() if "--add-suppression" in line]
    assert any(" provenance " in s and _PROVENANCE_JUSTIFICATION in s for s in suggestions), suggestions
    assert any(f" {_CLASS} " in s and "deliberate literal path" in s for s in suggestions), suggestions


def test_every_registered_class_carries_its_own_rule_and_justification() -> None:
    entries = [_lint._REGISTRY[name] for name in _lint.registered_class_names()]
    for hc in entries:
        assert hc.rule.strip(), f"{hc.name} has no rule sentence"
        assert hc.example_justification.strip(), f"{hc.name} has no example justification"
    assert len({hc.rule for hc in entries}) == len(entries), "each class states a rule of its own"
    assert len({hc.example_justification for hc in entries}) == len(
        entries
    ), "each class suggests a justification of its own"


# ---------------------------------------------------------------------------
# 7. Operator handle
# ---------------------------------------------------------------------------

# The handle the lint flags, written as two adjacent literals so this file
# never carries it whole (a tree-wide search for it finds only the
# organisation name).
_HANDLE = "purdy" "good"
_ORG = f"{_HANDLE}-ai"


def test_operator_handle_is_one_the_lint_flags() -> None:
    assert _HANDLE in _lint._OPERATOR_HANDLES


def test_operator_handle_outside_the_organisation_name_is_reported_once_per_line(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(
        repo,
        "docs/history.md",
        f"Commits are authored by {_HANDLE} <operator@example.invalid>.\n"
        f"Mail {_HANDLE.upper()}@example.com about it.\n"
        f"The identity (`{_HANDLE}`) and again {_HANDLE} on one line.\n"
        f"Clone https://github.com/{_ORG}/pgai-agent-kanban.git as {_HANDLE}.\n",
    )
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    assert [(f["line"], f["label"], f["token"].lower()) for f in findings] == [
        (1, "operator-handle", _HANDLE),
        (2, "operator-handle", _HANDLE),
        (3, "operator-handle", _HANDLE),
        (4, "operator-handle", _HANDLE),
    ], findings
    for f in findings:
        assert f["class"] == _CLASS
        assert f"{f['file']}:{f['line']}" in f["remedy"], f["remedy"]
        assert "<" not in f["remedy"] and ">" not in f["remedy"], f["remedy"]


def test_organisation_url_and_name_built_on_the_handle_are_not_reported(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "LICENSE", f"Copyright (c) 2026 {_HANDLE}.ai\n")
    _write(
        repo,
        "team/scripts/upgrade.sh",
        "#!/usr/bin/env bash\n"
        f"remedy=\"git clone https://github.com/{_ORG}/pgai-agent-kanban.git ~/src\"\n"
        f"# Upstream: https://github.com/{_ORG.upper()}/pgai-agent-kanban\n",
    )
    _write(repo, "docs/clone.md", f"git clone git@github.com:{_ORG}/pgai-agent-kanban.git\n")
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    assert findings == [], f"the organisation name must not be a finding: {findings}"


def test_handle_inside_a_longer_word_is_not_reported(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, "docs/words.md", f"x{_HANDLE} and {_HANDLE}9 are other words\n")
    _stage_all(repo)

    _, findings = _run(tmp_path, repo)

    assert findings == [], findings


@pytest.mark.parametrize(
    ("relpath", "content"),
    [
        ("team/tests/fixtures/tester_reports/corpus/report.md", f"**Author:** {_HANDLE}\n"),
        ("team/tests/fixtures/tester_reports/corpus/report.json", f'{{"author": "{_HANDLE}"}}\n'),
        ("team/tests/unit/test_planted.py", f'"""Fixtures recorded by {_HANDLE}."""\n'),
        ("team/tests/artifacts/snapshot.md", f"Recorded by {_HANDLE}.\n"),
    ],
)
def test_operator_handle_is_reported_on_the_test_tree(tmp_path: Path, relpath: str, content: str) -> None:
    repo = _make_repo(tmp_path)
    _write(repo, relpath, content)
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    assert [(f["file"], f["line"], f["label"], f["token"]) for f in findings] == [
        (relpath, 1, "operator-handle", _HANDLE)
    ], findings


def test_organisation_forms_are_not_reported_on_the_test_tree(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _write(
        repo,
        "team/tests/fixtures/tester_reports/corpus/report.md",
        f"Clone https://github.com/{_ORG}/pgai-agent-kanban.git\n"
        f"git clone git@github.com:{_ORG}/pgai-agent-kanban.git\n"
        f"Copyright (c) 2026 {_HANDLE}.ai\n",
    )
    _write(repo, "team/tests/unit/test_clone.py", f'"""Clones https://github.com/{_ORG.upper()}/x."""\n')
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    assert findings == [], f"the organisation name must not be a finding on the test tree: {findings}"


def test_test_tree_line_carrying_every_label_reports_only_the_handle(tmp_path: Path) -> None:
    """The same line yields all three labels in shipped files and the test
    tree's artifacts/, and the handle alone elsewhere on the test tree."""
    repo = _make_repo(tmp_path)
    line = f"{_HANDLE} cloned git@github-work:org/repo.git into /home/alice/x\n"
    for relpath in ("docs/setup.md", "team/tests/artifacts/snapshot.md", "team/tests/fixtures/sample.md"):
        _write(repo, relpath, line)
    _stage_all(repo)

    result, findings = _run(tmp_path, repo)

    assert result == 0
    by_file: dict[str, list[str]] = {}
    for f in findings:
        by_file.setdefault(f["file"], []).append(f["label"])
    assert {k: sorted(v) for k, v in by_file.items()} == {
        "docs/setup.md": ["home-path", "operator-handle", "ssh-host-alias"],
        "team/tests/artifacts/snapshot.md": ["home-path", "operator-handle", "ssh-host-alias"],
        "team/tests/fixtures/sample.md": ["operator-handle"],
    }, findings
