"""
test_docs_citation_scan_relaxed_forms.py
==========================================
Proves the widened citation matching ``pgai_agent_kanban.lib.docs_citation_scan``
carries (a bare sibling filename, a relative markdown link) behaves as
intended, on fixtures built for the purpose rather than inferred from
the real tree's own current, already-clean state:

  1. A manifest-listed document that cites an omitted sibling document by
     bare filename is reported.
  2. A second, independent manifest-listed document that cites the same
     omitted sibling by relative markdown link is reported too, and that
     single link produces exactly one finding — not one per occurrence of
     the target within the link (its text repeats its href).
  3. A bare markdown filename mentioned from a file outside the
     documentation directory (this suite stands in a role file) produces
     no documentation-citation finding at all — the scan's explicit scope
     guard.
  4. Restoring the two real citations once found dangling (docs/OPERATIONS.md
     and docs/operator-commands.md, both naming
     operator-troubleshooting.md with no "docs/" prefix) on a throwaway
     copy makes the widened check report exactly those two, by file and
     line — proving the scan now sees what the narrow, prefix-only
     pattern used to miss, on the exact real-world citations that
     motivated this widening.

FIXTURE SHAPE
--------------
Every fixture here copies both ``team/`` and ``docs/`` into a throwaway
root (``check_docs_citations.py``'s ``run_check`` resolves a "docs/<path>"
citation against ``repo_root``, and ``docs/`` is a repository-root
sibling of ``team/``, not a ``team/`` subdirectory — a fixture that copied
only ``team/``, as ``test_docs_citation_manifest_lint_wired.py``'s does,
would have no ``docs/`` tree for the relaxed forms to resolve against).
The real dev tree's own ``team/`` and ``docs/`` are opened for writing
nowhere in this module — every mutation lands on a ``tmp_path`` copy,
redirected under the framework temp root by ``conftest.py``'s
``pytest_configure`` (never bare ``/tmp``).

A citation the widened scan recognises must resolve to a real file under
``docs_root`` (``docs_citation_scan.py``'s own on-disk-existence guard,
documented at ``_normalize_relative_citation``), so every fixture sibling
this module cites by a relaxed spelling is a real file it writes into the
copied ``docs/`` tree — never a name that merely looks like one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pgai_agent_kanban.lib.docs_citation_scan import scan_citations

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_DOCS_DIR = _REPO_ROOT / "docs"

_SOURCE_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", "tests"
)

_FIXTURE_SIBLING_NAME = "citation-scan-fixture-sibling.md"
_FIXTURE_SIBLING_CITED = f"docs/{_FIXTURE_SIBLING_NAME}"


def _import_check_docs_citations_module():
    import importlib.util

    check_script = _TEAM_DIR / "scripts" / "check_docs_citations.py"
    spec = importlib.util.spec_from_file_location(
        "check_docs_citations_relaxed_forms", check_script
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {check_script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_check = _import_check_docs_citations_module()


def _copy_team_and_docs(tmp_path: Path, name: str) -> tuple[Path, Path, Path]:
    """Copy this dev tree's team/ and docs/ into a throwaway repo root
    under tmp_path. Returns (repo_root, team_dir, manifest_path)."""
    repo_root = tmp_path / name
    team_dir = repo_root / "team"
    shutil.copytree(_TEAM_DIR, team_dir, ignore=_SOURCE_COPY_IGNORE)
    shutil.copytree(_DOCS_DIR, repo_root / "docs")
    return repo_root, team_dir, team_dir / "config" / "docs-manifest.txt"


def _append_line(path: Path, text: str) -> int:
    """Append *text* as a new line to *path* and return its 1-based line
    number."""
    existing = path.read_text(encoding="utf-8")
    if not existing.endswith("\n"):
        existing += "\n"
    new_line_no = existing.count("\n") + 1
    path.write_text(existing + text + "\n", encoding="utf-8")
    return new_line_no


def _line_number_of(path: Path, needle: str) -> int:
    """Return the 1-based line number of the sole line in *path* containing
    *needle*. Fails loud on zero or more than one match, matching
    test_docs_citation_manifest_lint_wired.py's own
    _citation_line_number convention."""
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = [i + 1 for i, line in enumerate(lines) if needle in line]
    assert len(matches) == 1, (
        f"Expected exactly one line in {path} containing {needle!r}; "
        f"found {len(matches)}: {matches}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# 1. Bare sibling filename — reported.
# ---------------------------------------------------------------------------


def test_bare_sibling_filename_citation_to_omitted_document_is_reported(
    tmp_path: Path,
) -> None:
    repo_root, team_dir, manifest_path = _copy_team_and_docs(
        tmp_path, "bare_filename_fixture"
    )

    # The omitted sibling: a real file under the fixture's docs/, never
    # named in the manifest — a document the checkout carries but
    # install.sh/upgrade.sh never deliver.
    (repo_root / "docs" / _FIXTURE_SIBLING_NAME).write_text(
        "# Fixture sibling\n\nOmitted from the manifest on purpose.\n",
        encoding="utf-8",
    )

    citing_doc = repo_root / "docs" / "coding-standards.md"
    citing_line = _append_line(
        citing_doc,
        f"See `{_FIXTURE_SIBLING_NAME}` for the fixture this test injects.",
    )

    exit_code, report, setup_error = _check.run_check(
        repo_root, team_dir, manifest_path
    )

    assert setup_error is None, f"Unexpected setup error: {setup_error}"
    assert report is not None
    assert exit_code == 1, (
        "Expected the bare-filename citation to be reported; findings: "
        f"{report.findings}"
    )
    matching = [f for f in report.findings if f.cited == _FIXTURE_SIBLING_CITED]
    assert len(matching) == 1, (
        f"Expected exactly one finding for {_FIXTURE_SIBLING_CITED!r}; got "
        f"{len(matching)}: {report.findings}"
    )
    assert matching[0].source == "docs/coding-standards.md", matching[0].source
    assert matching[0].line == citing_line, (
        f"Expected the finding on line {citing_line}; got {matching[0].line}"
    )


# ---------------------------------------------------------------------------
# 2. Relative markdown link — reported once, not once per occurrence.
# ---------------------------------------------------------------------------


def test_relative_markdown_link_citation_to_omitted_document_is_reported_once(
    tmp_path: Path,
) -> None:
    repo_root, team_dir, manifest_path = _copy_team_and_docs(
        tmp_path, "relative_link_fixture"
    )

    (repo_root / "docs" / _FIXTURE_SIBLING_NAME).write_text(
        "# Fixture sibling\n\nOmitted from the manifest on purpose.\n",
        encoding="utf-8",
    )

    # A different manifest-listed document from the bare-filename case
    # above, so the two fixtures are independent proofs, not the same
    # citing file exercising both spellings.
    citing_doc = repo_root / "docs" / "promotion-playbook.md"
    citing_line = _append_line(
        citing_doc,
        f"See [{_FIXTURE_SIBLING_NAME}]({_FIXTURE_SIBLING_NAME}) for the "
        "fixture this test injects.",
    )

    exit_code, report, setup_error = _check.run_check(
        repo_root, team_dir, manifest_path
    )

    assert setup_error is None, f"Unexpected setup error: {setup_error}"
    assert report is not None
    assert exit_code == 1, (
        "Expected the relative-link citation to be reported; findings: "
        f"{report.findings}"
    )
    matching = [f for f in report.findings if f.cited == _FIXTURE_SIBLING_CITED]
    # The link's text and href repeat the same fixture name on one line —
    # this is the exact "count it once" case. Asserting length 1 here is
    # the behavioral proof;
    # asserting length 2 would mean the link's target was validated twice.
    assert len(matching) == 1, (
        f"Expected exactly one finding for the link's target (text and "
        f"href repeat the same name on one line — it must be validated "
        f"once, not twice); got {len(matching)}: {report.findings}"
    )
    assert matching[0].source == "docs/promotion-playbook.md", matching[0].source
    assert matching[0].line == citing_line, (
        f"Expected the finding on line {citing_line}; got {matching[0].line}"
    )


# ---------------------------------------------------------------------------
# 3. A bare filename outside the documentation directory is not a citation.
# ---------------------------------------------------------------------------


def test_bare_filename_outside_documentation_directory_produces_no_citation(
    tmp_path: Path,
) -> None:
    """A bare markdown filename mentioned from a file that does not itself
    live under docs_root — this fixture stands in a role file, and a bare
    markdown filename in a role file or a task README is not a
    documentation citation — must never be read
    as a documentation citation, even when the name it mentions resolves
    to a real file under docs_root. Driven directly against
    scan_citations, the shared primitive both callers use, rather than
    through the full walk, for the most targeted possible proof of the
    scope guard alone."""
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / _FIXTURE_SIBLING_NAME).write_text("# Sibling\n", encoding="utf-8")

    role_file = tmp_path / "team" / "roles" / "FIXTURE.md"
    role_file.parent.mkdir(parents=True)
    role_file.write_text(
        f"See `{_FIXTURE_SIBLING_NAME}` for unrelated fixture prose.\n",
        encoding="utf-8",
    )

    occurrences = scan_citations(role_file, docs_root=docs_root)

    assert occurrences == [], (
        "A bare markdown filename mentioned outside docs_root must produce "
        f"no citation occurrence at all; got {occurrences}"
    )


# ---------------------------------------------------------------------------
# 4. Revert fixture — the two real dangling citations once found.
# ---------------------------------------------------------------------------


def test_reverting_the_documentation_fix_reveals_the_two_real_dangling_citations(
    tmp_path: Path,
) -> None:
    """Restoring the exact earlier wording of docs/OPERATIONS.md and
    docs/operator-commands.md ("docs: stop sending installed readers to a
    page the installer withholds") reproduces the original repro state:
    two delivered documents naming operator-troubleshooting.md — a real
    file the checkout carries but the manifest deliberately never
    delivers — with no "docs/" prefix. The widened check must report
    both, by file and line; the narrow, prefix-only pattern reported
    neither."""
    repo_root, team_dir, manifest_path = _copy_team_and_docs(
        tmp_path, "revert_fixture"
    )

    operations_doc = repo_root / "docs" / "OPERATIONS.md"
    operator_commands_doc = repo_root / "docs" / "operator-commands.md"

    current_operations_text = operations_doc.read_text(encoding="utf-8")
    fixed_operations_sentence = (
        "The three tells above — consecutive versions, one dateline, "
        "narration where item files should be — are what separate this "
        "run from ordinary backlog, and the script's `--help` carries the "
        "authoritative eligibility rule: a bundle is retirable only when "
        "its `## Status` is neither `done` nor `running` and no line "
        "under `## Bundled Items` resolves to a file on disk. Report "
        "first, read the list, then `--apply`."
    )
    pre_fix_operations_sentence = (
        "The full diagnosis-and-remedy walkthrough, including how to tell "
        "this run from ordinary backlog, is in `operator-troubleshooting.md` "
        'under "The requirements queue is full of near-identical bundles"; '
        "the script's `--help` carries the authoritative eligibility rule."
    )
    assert fixed_operations_sentence in current_operations_text, (
        "Fixture assumption stale: the fixed OPERATIONS.md sentence this "
        "test reverts is no longer present verbatim."
    )
    operations_doc.write_text(
        current_operations_text.replace(
            fixed_operations_sentence, pre_fix_operations_sentence
        ),
        encoding="utf-8",
    )

    current_commands_text = operator_commands_doc.read_text(encoding="utf-8")
    fixed_commands_block = (
        "- [OPERATIONS.md](OPERATIONS.md) — diagnosing and recovering from stuck\n"
        '  states: "Why is this lane not moving?" for a lane that has stopped\n'
        "  advancing, \"When the chain halts\" for CM's nine autonomous halt\n"
        "  triggers and what clears each one.\n"
    )
    pre_fix_commands_block = (
        "- [operator-troubleshooting.md](operator-troubleshooting.md) — "
        "diagnosing and\n  recovering from stuck states.\n"
    )
    assert fixed_commands_block in current_commands_text, (
        "Fixture assumption stale: the fixed operator-commands.md block "
        "this test reverts is no longer present verbatim."
    )
    operator_commands_doc.write_text(
        current_commands_text.replace(fixed_commands_block, pre_fix_commands_block),
        encoding="utf-8",
    )

    expected_operations_line = _line_number_of(
        operations_doc, "operator-troubleshooting.md"
    )
    expected_commands_line = _line_number_of(
        operator_commands_doc, "operator-troubleshooting.md"
    )

    exit_code, report, setup_error = _check.run_check(
        repo_root, team_dir, manifest_path
    )

    assert setup_error is None, f"Unexpected setup error: {setup_error}"
    assert report is not None
    assert exit_code == 1, (
        "Expected the reverted tree to report the two real dangling "
        f"citations; findings: {report.findings}"
    )
    findings_by_source = {f.source: f for f in report.findings}
    assert set(findings_by_source) == {
        "docs/OPERATIONS.md",
        "docs/operator-commands.md",
    }, (
        "Expected exactly the two real dangling citations, one per file; "
        f"got: {report.findings}"
    )
    for source, expected_line in (
        ("docs/OPERATIONS.md", expected_operations_line),
        ("docs/operator-commands.md", expected_commands_line),
    ):
        finding = findings_by_source[source]
        assert finding.cited == "docs/operator-troubleshooting.md", (
            f"{source}: expected citation of operator-troubleshooting.md, "
            f"got {finding.cited!r}"
        )
        assert finding.line == expected_line, (
            f"{source}: expected the finding on line {expected_line}, got "
            f"{finding.line}"
        )
