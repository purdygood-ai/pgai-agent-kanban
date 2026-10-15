"""
test_docs_citation_manifest_lint_wired.py
============================================
Proves two things a description of the wiring cannot:

  1. **The call site exists.** `team/scripts/run-unit-tests.sh` names
     `team/scripts/check_docs_citations.py` through
     `gated_runner_run_quality_lint "docs_citation_manifest"` -- the same
     quality-class wrapper every other pre-flight lint in that block uses
     (`doc_command_truth` a few lines above is the closest structural
     precedent), so a dangling docs/ citation lands in `findings-unit.json`
     and never aborts the runner.
  2. **Both of the check's verdicts are real, not merely inferred.** A
     green case proves the check is silent against the tree as shipped; a
     red case removes one entry from `team/config/docs-manifest.txt` in a
     throwaway copy of `team/` and proves the check names exactly that one
     dangling citation and the document whose line was removed -- a check
     whose red case is never exercised is not known to work, only assumed
     to (see this suite's own module docstring on the task that added it).

RED-CASE FIXTURE CHOICE
------------------------
The entry removed is `read-order-manifests.md`, cited exactly once across
the whole governance seed set -- `team/README.md` line ~43 (`grep -c` on
the flat root files and every `team/roles/*.md` file confirms the single
occurrence; verified by hand while writing this suite). Removing a
singly-cited entry is what makes "exactly one finding" a meaningful
assertion instead of an accident of a multiply-cited document collapsing
to one finding for an unrelated reason. If a future edit adds a second
citation to this document, `_citation_line_number` below fails loud with a
clear count mismatch rather than silently asserting against a stale
assumption.

WHY ONLY team/ IS COPIED, NOT THE WHOLE REPO
-----------------------------------------------
`check_docs_citations.py` resolves a citation purely by manifest
membership (`docs/<path>` text named in `docs-manifest.txt`), never by
checking that `docs/<path>` exists on disk -- that existence check is
`test_installed_governance_docs_citations_resolve.py`'s job, against a
real installed/upgraded root. A citation's on-disk target is consulted
only to decide whether to walk one hop deeper for nested citations,
which this fixture's single seed-level citation never needs. Copying just
`team/` (never `.git`, which lives at the repo root, so this is never a
`cp -r` of the worktree) is therefore sufficient and keeps the fixture
copy small. The real dev tree's `team/config/docs-manifest.txt` is opened
for writing only inside the throwaway copy below -- never the original.

MECHANISM CHOICE
-----------------
`check_docs_citations.py` is loaded by path (mirrors
`test_lint_halt_scope_writers.py`'s import-by-path pattern for a
standalone `team/scripts/*.py` check) so the red/green assertions below
run against the check's own structured `ClosureReport` -- asserting on
`report.findings`' count and identity, not merely a process exit code,
because a test asserting only the exit code would pass against a check
that reports everything. A second
test additionally drives the check through a real bash subprocess, exactly
the `gated_runner_run_quality_lint` call shape `run-unit-tests.sh` uses,
to prove the wiring's own findings-file behavior (wrapper always exits 0;
exactly one finding recorded under the lint's own name) -- the same two-
layer proof `test_doc_truth_lint_wiring.py` uses for its lint.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths to the real files under test.
# team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CHECK_SCRIPT = _SCRIPTS_DIR / "check_docs_citations.py"
_MANIFEST = _TEAM_DIR / "config" / "docs-manifest.txt"

_RUN_UNIT_TESTS_REL = "scripts/run-unit-tests.sh"
_CHECK_SCRIPT_REL = "scripts/check_docs_citations.py"
_GATED_RUNNER_LIB_REL = "scripts/lib/gated_runner_lint.sh"
_LINT_NAME = "docs_citation_manifest"

# The one manifest entry the red-case fixture removes -- see this module's
# "RED-CASE FIXTURE CHOICE" docstring section above.
_REMOVED_MANIFEST_ENTRY = "read-order-manifests.md"
_EXPECTED_CITED = f"docs/{_REMOVED_MANIFEST_ENTRY}"

# Nothing under team/ that install.sh/upgrade.sh's docs-manifest-driven
# deposit reads needs .git (lives at the repo root, so this is never a
# cp -r of the worktree), bytecode caches, or a tests/ subtree.
_SOURCE_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", "tests"
)


def _import_check_docs_citations_module():
    spec = importlib.util.spec_from_file_location("check_docs_citations", _CHECK_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_CHECK_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_check = _import_check_docs_citations_module()


# ---------------------------------------------------------------------------
# Registration proof
# ---------------------------------------------------------------------------


def test_run_unit_tests_calls_the_docs_citation_check_through_the_quality_wrapper() -> (
    None
):
    """run-unit-tests.sh names check_docs_citations.py through the shared
    quality-class wrapper, so an authoring agent meets the check without
    choosing to, and a finding
    warns and records rather than aborting the runner."""
    text = (_TEAM_DIR / "scripts" / "run-unit-tests.sh").read_text(encoding="utf-8")
    assert f'gated_runner_run_quality_lint "{_LINT_NAME}"' in text, (
        "run-unit-tests.sh must invoke check_docs_citations.py through "
        "gated_runner_run_quality_lint."
    )
    assert "check_docs_citations.py" in text
    # Registered exactly once -- a duplicate registration under a name
    # already used elsewhere in the block would make findings-unit.json
    # carry two entries TESTER cannot tell apart.
    assert text.count(f'gated_runner_run_quality_lint "{_LINT_NAME}"') == 1


def test_check_docs_citations_is_never_registered_as_breakage_class() -> None:
    """The check must never reach check_breakage_class.py -- a dangling
    docs citation is quality-class (warn, record, continue), never fatal
    to the runner."""
    breakage_script = _SCRIPTS_DIR / "check_breakage_class.py"
    text = breakage_script.read_text(encoding="utf-8")
    assert "check_docs_citations" not in text, (
        "check_docs_citations.py must never be referenced from "
        "check_breakage_class.py -- it is a quality-class finding, not a "
        "breakage-class abort."
    )


# ---------------------------------------------------------------------------
# Green case -- silent against the tree as shipped.
# ---------------------------------------------------------------------------


def test_check_docs_citations_is_silent_against_the_shipped_tree() -> None:
    """Run directly against this dev tree's own team/ and
    team/config/docs-manifest.txt (read-only -- nothing here is mutated):
    zero findings, exit code 0, and at least one citation actually
    checked -- a check that scanned nothing is not a passing check, it is
    a broken fixture (mirrors
    test_installed_governance_docs_citations_resolve.py's own
    citations_checked > 0 guard)."""
    exit_code, report, setup_error = _check.run_check(_REPO_ROOT, _TEAM_DIR, _MANIFEST)

    assert setup_error is None, f"Unexpected setup error: {setup_error}"
    assert report is not None
    assert exit_code == 0, (
        "Expected the shipped tree to be clean; found: "
        + "\n".join(f"{f.source}:{f.line} cites {f.cited!r}" for f in report.findings)
    )
    assert report.findings == []
    assert report.citations_checked > 0, (
        "Expected at least one docs/ citation among the governance seed "
        "files; found zero -- the seed-file list or citation regex is "
        "broken, not that the tree has nothing to cite."
    )


# ---------------------------------------------------------------------------
# Red-case fixture builder, shared by the structured and wrapper-level tests.
# ---------------------------------------------------------------------------


def _find_manifest_line_index(manifest_path: Path, entry: str) -> int:
    """Return the 0-based line index of *entry* in *manifest_path*. Fails
    loud (not a silent skip) if the fixture's assumed entry has moved."""
    lines = manifest_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if line.split("#", 1)[0].strip() == entry:
            return idx
    raise AssertionError(
        f"Expected manifest entry {entry!r} in {manifest_path}; not found. "
        "This red-case fixture's assumed entry is gone -- update "
        "_REMOVED_MANIFEST_ENTRY to a still-present, singly-cited entry."
    )


def _citation_line_number(text: str, cited: str) -> int:
    """Return the 1-based line number of the sole line in *text* citing
    *cited*. Asserts exactly one match so a future edit adding a second
    citation to the same document fails this fixture loudly instead of
    silently changing what "exactly one finding" means below."""
    matches = [
        i + 1 for i, line in enumerate(text.splitlines()) if cited in line
    ]
    assert len(matches) == 1, (
        f"Expected exactly one line citing {cited!r}; found {len(matches)}: "
        f"{matches}. This red-case fixture assumes a single citing line."
    )
    return matches[0]


def _build_throwaway_copy_with_manifest_entry_removed(
    tmp_path: Path,
) -> tuple[Path, Path, Path, int]:
    """Copy team/ to a throwaway root under tmp_path (redirected under
    PGAI_AGENT_KANBAN_TEMP_DIR by conftest.py's pytest_configure -- never
    bare /tmp), then remove _REMOVED_MANIFEST_ENTRY from the copy's
    docs-manifest.txt. The real dev tree's team/config/docs-manifest.txt
    is never opened for writing by this fixture.

    Returns (copy_repo_root, copy_team_dir, copy_manifest_path,
    expected_line) -- expected_line is computed fresh from the copy's own
    README.md content, not hardcoded, so a later edit to README.md that
    shifts line numbers does not stale this fixture.
    """
    copy_repo_root = tmp_path / "docs_citation_manifest_red_case"
    copy_team_dir = copy_repo_root / "team"
    shutil.copytree(_TEAM_DIR, copy_team_dir, ignore=_SOURCE_COPY_IGNORE)

    manifest_path = copy_team_dir / "config" / "docs-manifest.txt"
    line_idx = _find_manifest_line_index(manifest_path, _REMOVED_MANIFEST_ENTRY)
    lines = manifest_path.read_text(encoding="utf-8").splitlines(keepends=True)
    del lines[line_idx]
    manifest_path.write_text("".join(lines), encoding="utf-8")

    readme_text = (copy_team_dir / "README.md").read_text(encoding="utf-8")
    expected_line = _citation_line_number(readme_text, _EXPECTED_CITED)

    return copy_repo_root, copy_team_dir, manifest_path, expected_line


# ---------------------------------------------------------------------------
# Red case -- structured assertion against the check's own report.
# ---------------------------------------------------------------------------


def test_removing_one_manifest_entry_produces_exactly_one_named_finding(
    tmp_path: Path,
) -> None:
    """Removing _REMOVED_MANIFEST_ENTRY from a throwaway copy of
    team/config/docs-manifest.txt makes the check report exactly one
    finding, naming the citing file, the line, and the missing document --
    asserted on the finding's identity, not merely a non-zero exit code
    (a test that only checks the exit code would pass against a check
    that reports everything)."""
    original_manifest_bytes = _MANIFEST.read_bytes()

    copy_repo_root, copy_team_dir, copy_manifest, expected_line = (
        _build_throwaway_copy_with_manifest_entry_removed(tmp_path)
    )

    exit_code, report, setup_error = _check.run_check(
        copy_repo_root, copy_team_dir, copy_manifest
    )

    assert setup_error is None, f"Unexpected setup error: {setup_error}"
    assert report is not None
    assert exit_code == 1, (
        "Expected a non-zero exit once the manifest entry is removed; got "
        f"{exit_code} with findings: {report.findings}"
    )
    assert len(report.findings) == 1, (
        f"Expected exactly one finding; got {len(report.findings)}: "
        + "\n".join(f"{f.source}:{f.line} cites {f.cited!r}" for f in report.findings)
    )
    finding = report.findings[0]
    assert finding.cited == _EXPECTED_CITED, finding.cited
    assert finding.source == "team/README.md", finding.source
    assert finding.line == expected_line, (
        f"Expected the finding's line to match the citation's actual line "
        f"({expected_line}) in the copy's README.md; got {finding.line}."
    )

    # The dev tree's own manifest must be byte-identical before and after --
    # only the throwaway copy was ever mutated.
    assert _MANIFEST.read_bytes() == original_manifest_bytes, (
        "team/config/docs-manifest.txt in the dev tree must be untouched by "
        "this test; only the throwaway tmp_path copy is mutated."
    )


# ---------------------------------------------------------------------------
# Red case -- driven through the exact gated_runner_run_quality_lint call
# shape run-unit-tests.sh uses, proving the wiring's own findings-file
# behavior (wrapper never aborts; the finding lands under the lint's name).
# ---------------------------------------------------------------------------


def test_removed_manifest_entry_is_wired_through_the_quality_lint_wrapper(
    tmp_path: Path,
) -> None:
    """Invoked exactly as run-unit-tests.sh now invokes it -- through
    gated_runner_run_quality_lint -- the same red-case fixture produces
    exactly one recorded finding named "docs_citation_manifest" and the
    wrapper's own exit code stays 0 (CONVERT class: warn, file, continue;
    never abort the calling runner), matching
    team/scripts/lib/gated_runner_lint.sh's CONVERT contract."""
    original_manifest_bytes = _MANIFEST.read_bytes()

    copy_repo_root, copy_team_dir, copy_manifest, _expected_line = (
        _build_throwaway_copy_with_manifest_entry_removed(tmp_path)
    )
    findings_file = tmp_path / "findings-unit.json"

    script = f"""
set -uo pipefail
source {_GATED_RUNNER_LIB_REL}
gated_runner_run_quality_lint "{_LINT_NAME}" -- \\
  python3 {_CHECK_SCRIPT_REL} \\
    --repo-root "{copy_repo_root}" \\
    --team-dir "{copy_team_dir}" \\
    --manifest "{copy_manifest}"
echo "WRAPPER_RETURNED=$?"
gated_runner_write_findings_file "{findings_file}" "run-unit-tests.sh"
"""
    result = run_bash(tmp_path, script)

    assert "WRAPPER_RETURNED=0" in result.stdout, (
        "gated_runner_run_quality_lint must return 0 even though the "
        f"wrapped check found a dangling citation.\nstdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert result.returncode == 0

    doc_written = json.loads(findings_file.read_text(encoding="utf-8"))
    findings = [f for f in doc_written["findings"] if f.get("lint") == _LINT_NAME]
    assert len(findings) == 1, (
        f"expected exactly one {_LINT_NAME} finding, got: {doc_written['findings']}"
    )
    assert findings[0]["severity"] == "quality"
    assert _EXPECTED_CITED in findings[0]["detail"]
    assert "team/README.md" in findings[0]["detail"]

    assert _MANIFEST.read_bytes() == original_manifest_bytes, (
        "team/config/docs-manifest.txt in the dev tree must be untouched by "
        "this test; only the throwaway tmp_path copy is mutated."
    )
