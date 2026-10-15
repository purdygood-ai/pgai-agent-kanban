"""
test_docs_citation_excluded_target_spellings.py
=================================================
Pins which spellings the docs manifest's ``EXCLUDED-CITATION-TARGETS``
allowance covers in ``team/scripts/check_docs_citations.py``:

  1. A ``docs/``-prefixed, uncaveated citation of an excluded target from
     a delivered governance document -- a page inside ``docs/`` or a flat
     governance seed file -- is a finding: the script exits non-zero and
     its output names the citing file and line and the cited path. Its
     remedy never tells the author to add a recorded exclusion to the
     delivery list.
  2. A bare, caveated mention and a relative markdown link to the same
     excluded target from a delivered page inside ``docs/`` -- the shape
     ``docs/dashboard.md`` uses for its checkout-only pointers -- still
     pass (exit 0).
  3. A page that names the same excluded target both ways (bare mention
     first, prefixed citation later) is reported at the prefixed line: the
     earlier relaxed mention does not hide it.
  4. On each of those fixtures the checker and the installed-root walker
     (``test_installed_governance_docs_citations_resolve.py``'s own
     adapter over the shared closure walk, run against an install-shaped
     copy carrying only manifest members under ``docs/``) report the same
     findings -- the two surfaces reach one verdict.

FIXTURE SHAPE
--------------
Each test builds a small synthetic repository under ``tmp_path`` (never
the real dev tree, never bare ``/tmp``): ``team/README.md`` as the single
governance seed, ``team/config/docs-manifest.txt`` delivering one page,
and ``docs/`` holding that delivered page plus two checkout-only pages
named on the manifest's ``EXCLUDED-CITATION-TARGETS`` line. The
checkout-only pages exist on disk, because a relaxed spelling counts as a
citation only when its target is a real file under ``docs/``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.test_installed_governance_docs_citations_resolve import (
    _find_unreachable_docs_citations as find_unreachable_on_installed_root,
)

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_CHECK_SCRIPT = _TEAM_DIR / "scripts" / "check_docs_citations.py"

_TIMEOUT = 60

_DELIVERED_PAGE = "guide.md"
_EXCLUDED_PAGE = "PANES-REFERENCE.md"
_OTHER_EXCLUDED_PAGE = "scheduler.md"
_EXCLUDED_CITED = f"docs/{_EXCLUDED_PAGE}"

_MANIFEST_TEXT = (
    "# Fixture docs manifest.\n"
    "#\n"
    f"# EXCLUDED-CITATION-TARGETS: {_EXCLUDED_PAGE}, {_OTHER_EXCLUDED_PAGE}\n"
    "\n"
    f"{_DELIVERED_PAGE}\n"
)

_SEED_TEXT = (
    "# Fixture governance root\n"
    "\n"
    f"Start with docs/{_DELIVERED_PAGE} for the walkthrough.\n"
)

_GUIDE_HEADER = "# Guide\n\nDelivered fixture page.\n"


def _build_repo(tmp_path: Path, guide_body: str, seed_extra: str = "") -> Path:
    """Create the synthetic repository described in FIXTURE SHAPE and
    return its root. *guide_body* is appended to the delivered page,
    *seed_extra* to the governance seed file."""
    repo_root = tmp_path / "repo"
    (repo_root / "team" / "config").mkdir(parents=True)
    (repo_root / "docs").mkdir()
    (repo_root / "team" / "README.md").write_text(
        _SEED_TEXT + seed_extra, encoding="utf-8"
    )
    (repo_root / "team" / "config" / "docs-manifest.txt").write_text(
        _MANIFEST_TEXT, encoding="utf-8"
    )
    (repo_root / "docs" / _DELIVERED_PAGE).write_text(
        _GUIDE_HEADER + guide_body, encoding="utf-8"
    )
    for checkout_only in (_EXCLUDED_PAGE, _OTHER_EXCLUDED_PAGE):
        (repo_root / "docs" / checkout_only).write_text(
            f"# {checkout_only}\n\nRepository checkout only.\n", encoding="utf-8"
        )
    return repo_root


def _line_of(path: Path, needle: str) -> int:
    """1-based line number of the sole line in *path* containing *needle*;
    fails loud on zero or several matches."""
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = [i + 1 for i, line in enumerate(lines) if needle in line]
    assert len(matches) == 1, (
        f"Expected exactly one line in {path} containing {needle!r}; "
        f"found {len(matches)}: {matches}"
    )
    return matches[0]


def _run_checker(repo_root: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run check_docs_citations.py by path against *repo_root*, the way an
    author runs it, with an explicit environment."""
    team_dir = repo_root / "team"
    home_dir = tmp_path / "home"
    home_dir.mkdir(exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            str(_CHECK_SCRIPT),
            "--repo-root",
            str(repo_root),
            "--team-dir",
            str(team_dir),
            "--manifest",
            str(team_dir / "config" / "docs-manifest.txt"),
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=build_scrubbed_env(home_dir=home_dir),
    )


def _installed_root_findings(repo_root: Path, tmp_path: Path) -> set[tuple[str, int, str]]:
    """Lay *repo_root* out the way an install does -- the governance seed
    flat at the root, and only the manifest's members under docs/ -- and
    return the installed-root walker's findings as (source, line, cited)."""
    install_root = tmp_path / "installed"
    (install_root / "docs").mkdir(parents=True)
    shutil.copy2(repo_root / "team" / "README.md", install_root / "README.md")
    shutil.copy2(
        repo_root / "docs" / _DELIVERED_PAGE, install_root / "docs" / _DELIVERED_PAGE
    )
    report = find_unreachable_on_installed_root(install_root)
    assert report.citations_checked > 0, report.describe()
    return {(f.source, f.line, f.cited) for f in report.findings}


def _checker_findings(output: str) -> set[tuple[str, int, str]]:
    """Parse the checker's '  - <source>:<line> cites '<cited>'' lines."""
    found: set[tuple[str, int, str]] = set()
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ") or " cites " not in stripped:
            continue
        location, rest = stripped[2:].split(" cites ", 1)
        source, line_no = location.rsplit(":", 1)
        cited = rest.split("'", 2)[1]
        found.add((source, int(line_no), cited))
    return found


# ---------------------------------------------------------------------------
# 1. A docs/-prefixed citation of an excluded target is a finding.
# ---------------------------------------------------------------------------


def test_prefixed_citation_of_excluded_target_in_delivered_page_fails(
    tmp_path: Path,
) -> None:
    repo_root = _build_repo(
        tmp_path, f"Per-pane detail is in {_EXCLUDED_CITED}.\n"
    )
    citing_line = _line_of(repo_root / "docs" / _DELIVERED_PAGE, _EXCLUDED_CITED)

    result = _run_checker(repo_root, tmp_path)

    assert result.returncode == 1, (
        f"Expected exit 1 for a prefixed citation of a recorded exclusion; "
        f"got {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    expected = f"docs/{_DELIVERED_PAGE}:{citing_line} cites '{_EXCLUDED_CITED}'"
    assert expected in result.stdout, (
        f"Expected the finding to name the citing file, line and cited path "
        f"({expected!r}).\nstdout:\n{result.stdout}"
    )
    # The remedy must not tell the author to deliver a recorded exclusion:
    # no bare manifest line naming it is offered to add.
    offered_manifest_lines = {line.strip() for line in result.stdout.splitlines()}
    assert _EXCLUDED_PAGE not in offered_manifest_lines, (
        "The remedy offered a recorded exclusion as a manifest line to add."
        f"\nstdout:\n{result.stdout}"
    )
    assert _checker_findings(result.stdout) == _installed_root_findings(
        repo_root, tmp_path
    )


def test_prefixed_citation_of_excluded_target_in_governance_seed_fails(
    tmp_path: Path,
) -> None:
    repo_root = _build_repo(
        tmp_path,
        "Nothing further.\n",
        seed_extra=f"Per-pane detail is in {_EXCLUDED_CITED}.\n",
    )
    citing_line = _line_of(repo_root / "team" / "README.md", _EXCLUDED_CITED)

    result = _run_checker(repo_root, tmp_path)

    assert result.returncode == 1, (
        f"Expected exit 1; got {result.returncode}.\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}"
    )
    expected = f"team/README.md:{citing_line} cites '{_EXCLUDED_CITED}'"
    assert expected in result.stdout, (
        f"Expected {expected!r} in the output.\nstdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# 2. The relaxed, caveated spellings dashboard.md uses still pass.
# ---------------------------------------------------------------------------


def test_bare_caveated_mention_and_relative_link_of_excluded_target_pass(
    tmp_path: Path,
) -> None:
    repo_root = _build_repo(
        tmp_path,
        (
            f"For what populates each pane, see [`{_EXCLUDED_PAGE}`]"
            f"({_EXCLUDED_PAGE}).\n"
            "\n"
            "## Where to go next\n"
            "\n"
            f"- `{_EXCLUDED_PAGE}` — the per-pane reference. Repository "
            "checkout only.\n"
            f"- `{_OTHER_EXCLUDED_PAGE}` — the scheduler. Repository "
            "checkout only.\n"
        ),
    )

    result = _run_checker(repo_root, tmp_path)

    assert result.returncode == 0, (
        f"Expected exit 0 for caveated relaxed mentions of recorded "
        f"exclusions; got {result.returncode}.\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}"
    )
    assert "check_docs_citations: ok" in result.stdout, result.stdout
    assert _installed_root_findings(repo_root, tmp_path) == set()


# ---------------------------------------------------------------------------
# 3. An earlier relaxed mention does not hide a later prefixed citation.
# ---------------------------------------------------------------------------


def test_prefixed_citation_after_relaxed_mention_of_same_target_is_reported(
    tmp_path: Path,
) -> None:
    repo_root = _build_repo(
        tmp_path,
        (
            f"- `{_EXCLUDED_PAGE}` — the per-pane reference. Repository "
            "checkout only.\n"
            "\n"
            f"Column meanings are in {_EXCLUDED_CITED}.\n"
        ),
    )
    citing_line = _line_of(repo_root / "docs" / _DELIVERED_PAGE, _EXCLUDED_CITED)

    result = _run_checker(repo_root, tmp_path)

    assert result.returncode == 1, (
        f"Expected exit 1; got {result.returncode}.\nstdout:\n{result.stdout}"
        f"\nstderr:\n{result.stderr}"
    )
    findings = _checker_findings(result.stdout)
    assert findings == {(f"docs/{_DELIVERED_PAGE}", citing_line, _EXCLUDED_CITED)}, (
        f"Expected exactly the prefixed citation on line {citing_line}; got "
        f"{findings}.\nstdout:\n{result.stdout}"
    )
    assert findings == _installed_root_findings(repo_root, tmp_path)
