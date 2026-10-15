"""
test_installed_governance_docs_citations_resolve.py
=====================================================
Mechanical guard for one property: every path a shipped governance file
names by concatenating it onto a real filesystem location must resolve to
something an agent reading only the installed tree can actually open —
never a dangling pointer. Three dimensions of that property are checked,
each with its own green (property holds) and red (the check can actually
fail, not just pass) case:

1. **docs/ citations under a fresh install.** Every ``docs/<path>.md``
   reference reachable by walking the transitive closure starting at the
   installed root-governance files (``README.md``, ``DIRECTIVES.md``,
   ``OVERVIEW.md``, ``SOP-core.md``) and every installed ``roles/*.md``
   resolves to a file under a root ``install.sh`` just produced.
2. **docs/ citations under an upgraded root.** The same property, but for
   a root that started without ``docs/`` (the shape of an install predating
   the docs/ deposit fix) and was brought current by ``upgrade.sh`` alone —
   ``install.sh`` and ``upgrade.sh`` are two independent code paths to a
   delivered root, and a property proven on one says nothing about the
   other.
3. **Root-variable command paths.** Every ``$PGAI_AGENT_KANBAN_ROOT_PATH/...``
   or ``$KANBAN_ROOT/...`` command path a role file instructs an agent to
   run — a ``source`` target, a ``python3`` script argument — resolves to a
   real file under the installed root. This is the same defect shape as
   dimension 1, one variable substitution over: a governance file naming a
   path that does not exist once the tree is flattened onto a live root.

SCOPE CHOICE — how far each walk reaches
-----------------------------------------
Dimensions 1 and 2 seed from, and dimension 3 scans, the same governance
set: the four flat root files plus every installed ``roles/*.md``. That
set is exactly what an installed root delivers as citable/instructable
prose — the broader repository (``docs/`` itself, ``team/config/``, and so
on) is read by scripts, not quoted verbatim at an agent as a thing to open
or run. Dimension 1's walk is transitive (a resolved ``docs/`` citation is
itself scanned for further citations, which is what catches a citation
made by a cited document such as ``docs/blocking-site-census.md`` without
hardcoding that edge);
dimension 3's walk is not, because a sourced ``.sh`` or invoked ``.py`` file
is a script, not a governance document that can itself carry a
markdown-style citation to chase further.

Dimension 3's path pattern is deliberately narrower than "every
``$PGAI_AGENT_KANBAN_ROOT_PATH/...`` mention": it matches only paths ending
in ``.sh`` or ``.py`` — the two extensions a role file actually sources or
executes. A role file also names plenty of root-relative locations that are
runtime state, not installed content (a HALT flag, a per-project ledger, a
release-state file, an artifacts directory keyed by a placeholder like
``<project-name>``) — none of those is something the installer delivers, so
asserting file-existence against them would flag correct prose as broken
and train the next reader to turn the check off. Restricting the pattern to
loadable-script extensions keeps the check aimed at exactly the failure
this dimension exists to catch: a role file telling an agent to run
something that silently is not there. A bare repository-relative
``team/scripts/...`` mention with no root variable in front of it is
correct as written (it names a dev-tree checkout path, not an installed
one) and is out of scope for the same reason — the pattern only matches
when the path is concatenated onto ``$PGAI_AGENT_KANBAN_ROOT_PATH`` or
``$KANBAN_ROOT``.

MECHANISM CHOICE
-----------------
A pytest module under ``team/tests/unit/`` rather than a standalone
``team/scripts/lint_*`` sibling: ``run-unit-tests.sh`` already discovers
everything under ``tests/unit/``, so a module placed here runs on every RC
with zero additional wiring — no new line in a runner script, no new entry
in a lint-invocation list to keep in sync. This check is also not a CLI
refusal site an operator invokes directly — it is suite-internal
verification, so it carries no gate census anchor;
``team/scripts/lint_gate_census_compliance.py`` itself excludes every
``test_*.py`` file and anything under a ``tests/`` path from its scan for
exactly this reason.

SHARED CITATION LOGIC (dimensions 1 and 2 only)
-------------------------------------------------
Dimensions 1 and 2's citation regex, seed-file rule, and closure walk live
in ``pgai_agent_kanban.lib.docs_citation_scan`` — the same module
``team/scripts/check_docs_citations.py`` imports to prove the same
property directly against the source tree in seconds, with no install.
This module supplies that shared walk with "resolved" meaning "resolves
to a real file under the freshly installed/upgraded root" (checked here);
the standalone check supplies the same walk with "resolved" meaning
"named in team/config/docs-manifest.txt" instead. See that module's
docstring for the full rationale. Dimension 3 (root-variable command
paths) is untouched by this — its regex and walk are not shared with the
standalone check and remain local to this file.

Dimension 2's upgrade case drives the REAL two-phase ``upgrade.sh``
bootstrap end to end rather than a hand-seeded minimal root, and needs no
git tag state to do it: passing no positional ``VERSION`` argument takes
``upgrade.sh``'s own documented no-argument path ("lays down whatever the
dev tree currently has checked out"), which skips the forward-only
comparison and its git-tag resolution entirely. That makes a full,
unmodified ``install.sh`` run the cheapest possible way to build the
"existing installation" ``upgrade.sh`` requires: it produces every library
``upgrade.sh`` sources (``argparse.sh``, ``env_bootstrap.sh``,
``project_paths.sh``, ``temp.sh``, and the rest) as genuine installed
content, so the fixture can never drift from what ``install.sh`` actually
lays down the way a hand-curated subset of libraries could.

COST (measure this if either fixture shape changes)
-----------------------------------------------------
Seven real subprocess invocations run across the module's six test
functions: five ``install.sh`` runs (dimension 1's green and red cases,
dimension 2's green and red cases, dimension 3's red case) and two
``upgrade.sh`` runs (dimension 2's green and red cases); dimension 3's
green case and the repository-relative-reference check add no subprocess
at all (the former reuses dimension 1's install, the latter is a bare
regex assertion). Measured on this host: the full module completes in
about 12 seconds wall-clock (``pytest``'s own "6 passed in 11.16s", ~13s
including interpreter startup). That is proportionate for a check that
runs once per RC alongside hundreds of other unit tests; it is not run
per-file or in a loop.

SANDBOX DESIGN
--------------
Every fresh-install fixture runs ``install.sh`` directly out of THIS dev
tree (the same pattern ``test_subagent_deploy_dir_creation.py`` and
``test_install_version_targeting.py`` already use for install.sh
behavioral tests) against a throwaway ``PGAI_AGENT_KANBAN_ROOT_PATH`` and
throwaway ``HOME``, both under pytest's ``tmp_path`` (redirected under the
configured framework temp root by ``conftest.py``'s ``pytest_configure`` —
never bare ``/tmp``). ``--stamp-version`` bypasses git tag resolution so
the run needs no git state at all. Every upgrade fixture runs the
INSTALLED ``upgrade.sh`` (the phase-1 bootstrap ``install.sh`` just laid
down) with ``--force --no-backup --dev-tree <path>`` and no positional
``VERSION`` argument, against either this real dev tree unmodified or a
throwaway copy of it with one file removed.

A red case that needs a modified source tree builds a throwaway COPY
(``shutil.copytree``, excluding ``.git``, ``__pycache__``,
``.pytest_cache`` and any ``tests`` subdirectory — none of which
``install.sh`` or ``upgrade.sh`` reads) so it can edit a copied role file's
citations, or remove a copied manifest file, without touching this
checkout. The operator's live install at ``$HOME/pgai_agent_kanban``
and this dev tree are never touched by any test in this module, on any
path (pass or fail) — every fixture root and copy lives under ``tmp_path``,
and no file this module opens for writing is outside it.

NAMING CONVENTION
-----------------
Function names describe the behavior under test.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

import pytest

# ---------------------------------------------------------------------------
# Paths to the real scripts under test
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_REAL_INSTALL_SH = _REPO_ROOT / "install.sh"

# team/ import path -- needed for the shared docs-citation scan module
# (dimensions 1 and 2; dimension 3's root-variable walk stays local to
# this file -- see this module's "SHARED CITATION LOGIC" section above).
_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib.docs_citation_scan import (  # noqa: E402
    governance_seed_files as _governance_seed_files,
    walk_docs_citation_closure as _walk_docs_citation_closure,
)

_TIMEOUT = 120

# install.sh's committed VERSION content is irrelevant to this check
# (--stamp-version bypasses git tag resolution entirely); the literal
# value only needs to be a syntactically acceptable version string.
_STAMP_VERSION = "v0.0.0-docs-reachability-check"

# Directories/files a source-tree copy never needs to include for
# install.sh or upgrade.sh to run: version control metadata, Python
# bytecode caches, and every "tests" directory (neither script's copy
# steps read a tests subtree; excluding it keeps a fixture copy
# proportionate).
_SOURCE_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", "tests"
)

# Loose match for a root-variable command path: $VAR/<path> or
# ${VAR}/<path>, VAR one of the two kanban-root variables, ending in a
# loadable script extension. Restricted to .sh/.py — see this module's
# "SCOPE CHOICE" section above for why a bare $VAR/<anything> pattern would
# flag correct prose (runtime state paths, placeholder-bearing templates)
# instead of the command-path defect this dimension targets. The single
# capture group is the relative path portion, so .findall() on this
# pattern yields relative paths directly, ready to resolve against a root.
_ROOT_VAR_PATH_RE = re.compile(
    r"\$\{?(?:PGAI_AGENT_KANBAN_ROOT_PATH|KANBAN_ROOT)\}?/([A-Za-z0-9._/-]+\.(?:sh|py))"
)

@dataclass
class _RootVariablePathReport:
    """What the root-variable command-path check verified, so a reader
    never sees a bare pass/fail."""

    scanned_files: list[str] = field(default_factory=list)
    paths_checked: int = 0
    unreachable: list[tuple[str, str]] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"Scanned {len(self.scanned_files)} installed governance file(s) for "
            f"$PGAI_AGENT_KANBAN_ROOT_PATH/... and $KANBAN_ROOT/... command "
            f"paths: {', '.join(sorted(self.scanned_files))}",
            f"Checked {self.paths_checked} distinct root-variable command "
            f"path(s) for resolution under the installed root.",
        ]
        if self.unreachable:
            lines.append(f"{len(self.unreachable)} path(s) did NOT resolve:")
            for source, named in self.unreachable:
                lines.append(f"  - {source} names {named!r}, which is not installed")
        else:
            lines.append(
                "Every root-variable command path resolved to a file under the "
                "installed root."
            )
        return "\n".join(lines)


def _find_unreachable_docs_citations(kanban_root: pathlib.Path):
    """Walk the transitive closure of docs/ citations starting from the
    installed governance seed files, and report every citation that does
    not resolve to a real file under *kanban_root*.

    A thin adapter over the shared
    ``pgai_agent_kanban.lib.docs_citation_scan.walk_docs_citation_closure``:
    "resolved" here means "is a real file under kanban_root" — the
    installed-root resolution scheme. A resolved citation is itself
    scanned for further citations (this is what catches a citation
    made by a cited document such as docs/blocking-site-census.md
    without hardcoding that edge); the returned report's ``.findings`` list carries every
    citation that did not resolve, and ``.describe()`` renders it for a
    human reader — see that module's ``ClosureReport``.
    """

    def display_name(path: pathlib.Path) -> str:
        try:
            return str(path.relative_to(kanban_root))
        except ValueError:
            return str(path)

    def resolved_target(cited: str) -> pathlib.Path | None:
        # walk_docs_citation_closure calls resolved_target only for a
        # citation is_resolved already accepted, and is_resolved (the
        # is_file() lambda passed below) IS the existence check -- re-
        # testing it here would just repeat the same filesystem stat for
        # no new information.
        return kanban_root / cited

    return _walk_docs_citation_closure(
        _governance_seed_files(kanban_root),
        display_name=display_name,
        is_resolved=lambda cited: (kanban_root / cited).is_file(),
        resolved_target=resolved_target,
    )


def _find_unreachable_root_variable_paths(
    kanban_root: pathlib.Path,
) -> _RootVariablePathReport:
    """Scan the installed governance seed files for a
    $PGAI_AGENT_KANBAN_ROOT_PATH/<path> or $KANBAN_ROOT/<path> command path
    ending in .sh or .py, and report every one that does not resolve to a
    real file under *kanban_root*.

    Unlike the docs-citation walk, this scan is not transitive: a sourced
    .sh or invoked .py file is a script, not a governance document that can
    itself cite further docs/ or root-variable paths, so there is nothing
    to chase beyond the seed set.
    """
    report = _RootVariablePathReport()
    checked_paths: set[str] = set()

    for seed in _governance_seed_files(kanban_root):
        try:
            rel = str(seed.relative_to(kanban_root))
        except ValueError:
            rel = str(seed)
        report.scanned_files.append(rel)

        text = seed.read_text(encoding="utf-8", errors="replace")
        for named_path in sorted(set(_ROOT_VAR_PATH_RE.findall(text))):
            checked_paths.add(named_path)
            if not (kanban_root / named_path).is_file():
                report.unreachable.append((rel, named_path))

    report.paths_checked = len(checked_paths)
    return report


# ---------------------------------------------------------------------------
# Shared install / upgrade invocation
# ---------------------------------------------------------------------------


def _run_install(
    install_sh: pathlib.Path,
    *,
    kanban_root: pathlib.Path,
    home: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run *install_sh* non-interactively into a throwaway root.

    --force --no-system-cron keeps the run non-interactive and off the
    host crontab; --stamp-version bypasses git tag resolution so the run
    needs no git state in the source tree at all.
    """
    home.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "HOME": str(home),
        }
    )

    return subprocess.run(
        [
            "bash",
            str(install_sh),
            "--force",
            "--no-system-cron",
            "--stamp-version",
            _STAMP_VERSION,
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
        # Anchored to this test file's own self-located repo root -- not the
        # inherited process cwd -- for the same reason test_doc_lint_harness.py
        # anchors its subprocess calls: install.sh already self-locates via
        # BASH_SOURCE, so this has no behavioral effect today, but it keeps
        # every doc-surface suite's subprocess launches under one explicit
        # cwd contract instead of leaving it an unstated inherited property.
        cwd=str(_REPO_ROOT),
    )


def _run_upgrade(
    installed_upgrade_sh: pathlib.Path,
    *,
    dev_tree: pathlib.Path,
    kanban_root: pathlib.Path,
    home: pathlib.Path,
    pgai_temp: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Run the INSTALLED *installed_upgrade_sh* (the phase-1 bootstrap
    install.sh just laid down at kanban_root/scripts/upgrade.sh), handing
    off to *dev_tree*'s own upgrade.sh for phase 2.

    No positional VERSION argument is passed: this is upgrade.sh's own
    documented no-argument path ("upgrade using whatever the dev tree
    currently has checked out"), which needs no git tag state and no
    forward-only comparison at all — the cheapest way to exercise the real
    deposit code for this module's purposes. --force --no-backup keeps the
    run non-interactive and skips the pre-upgrade tarball (irrelevant to a
    throwaway fixture root).
    """
    home.mkdir(parents=True, exist_ok=True)
    pgai_temp.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.pop("PGAI_DEV_TREE_PATH", None)
    env.update(
        {
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(pgai_temp),
            "HOME": str(home),
        }
    )

    return subprocess.run(
        [
            "bash",
            str(installed_upgrade_sh),
            "--force",
            "--no-backup",
            "--dev-tree",
            str(dev_tree),
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env=env,
        # See the matching cwd note on _run_install above.
        cwd=str(_REPO_ROOT),
    )


def _combined_output(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


# ---------------------------------------------------------------------------
# Dimension 1 — docs/ citations under a fresh install.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_every_installed_governance_docs_citation_resolves_under_fresh_install(
    tmp_path: pathlib.Path,
) -> None:
    """Every docs/ citation reachable from the installed root-governance
    files and roles/*.md resolves to a real file under a freshly installed
    root, and every $PGAI_AGENT_KANBAN_ROOT_PATH/... or $KANBAN_ROOT/...
    command path those same files name resolves there too. Both properties
    are checked against the one install this test performs — see this
    module's header for why splitting them into separate fresh installs
    would cost a second real install run for no added coverage."""
    kanban_root = tmp_path / "kanban_root"
    home = tmp_path / "home"

    result = _run_install(_REAL_INSTALL_SH, kanban_root=kanban_root, home=home)
    assert result.returncode == 0, (
        f"install.sh must succeed before the reachability checks can run.\n"
        f"{_combined_output(result)}"
    )

    docs_report = _find_unreachable_docs_citations(kanban_root)
    print(docs_report.describe())  # names what was verified; visible under pytest -s

    assert not docs_report.findings, (
        "The following docs/ citation(s) from an installed governance file "
        "do not resolve under the freshly installed root — an agent reading "
        "only the installed tree cannot open them:\n" + docs_report.describe()
    )
    # A check that finds nothing to scan is not a passing check — it is a
    # broken fixture. Fail loud if the seed walk somehow saw no citations.
    assert docs_report.citations_checked > 0, (
        "Expected at least one docs/ citation among the installed governance "
        "files; found zero — the seed file list or citation regex is broken."
    )

    root_var_report = _find_unreachable_root_variable_paths(kanban_root)
    print(root_var_report.describe())

    assert not root_var_report.unreachable, (
        "The following $PGAI_AGENT_KANBAN_ROOT_PATH/... or $KANBAN_ROOT/... "
        "command path(s) named by an installed governance file do not "
        "resolve under the freshly installed root — copying the file's own "
        "command line verbatim would fail:\n" + root_var_report.describe()
    )
    assert root_var_report.paths_checked > 0, (
        "Expected at least one root-variable command path among the "
        "installed governance files; found zero — the seed file list or the "
        "root-variable regex is broken."
    )


def test_repository_relative_team_scripts_reference_produces_no_root_variable_finding() -> (
    None
):
    """A bare repository-relative team/scripts/... mention in prose — no
    $PGAI_AGENT_KANBAN_ROOT_PATH or $KANBAN_ROOT prefix in front of it — is
    correct as written (team/ does not exist on an installed root; the
    reference names a dev-tree checkout path, not an installed one) and
    must produce no finding from the root-variable scan. Only a path
    actually concatenated onto one of the two root variables is in scope."""
    prose = (
        "Run the census lint yourself: "
        "`team/scripts/lint_gate_census_compliance.py` walks every gate.\n"
        "See also team/scripts/upgrade.sh for the deposit sequence, and "
        "team/scripts/lib/temp.sh for the shared temp-root helper.\n"
    )
    assert _ROOT_VAR_PATH_RE.findall(prose) == [], (
        "A repository-relative team/scripts/... reference must never match "
        "the root-variable pattern — it is correct as written in prose, and "
        "flagging it would train the next reader to turn the check off."
    )


# ---------------------------------------------------------------------------
# Dimension 1, red case — a docs/ citation to a document the installer does
# not deliver.
# ---------------------------------------------------------------------------


def _build_source_copy_with_dangling_citation(
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, str]:
    """Copy this dev tree to a throwaway root, then add one line to an
    installed role file citing a docs/ document the installer does not
    deliver. Returns (copied repo root, the bogus citation added).

    The citation is added to team/roles/TESTER.md, which install.sh's
    roles/ copy step installs verbatim as roles/TESTER.md — the same file
    the green-case test already scans, so the only variable between the
    two tests is the one new dangling citation.
    """
    repo = tmp_path / "source_repo_with_dangling_citation"
    shutil.copytree(_REPO_ROOT, repo, ignore=_SOURCE_COPY_IGNORE)

    bogus_citation = "docs/does-not-exist-docs-reachability-guard-fixture.md"
    target_role_file = repo / "team" / "roles" / "TESTER.md"
    with target_role_file.open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n<!-- docs-reachability-guard fixture: intentionally dangling "
            f"citation for the red-case test. See `{bogus_citation}`. -->\n"
        )

    return repo, bogus_citation


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_citation_to_undelivered_document_is_reported_as_unreachable(
    tmp_path: pathlib.Path,
) -> None:
    """Adding a docs/ citation the installer does not deliver, on a
    synthetic copy of the source tree, makes the check report exactly
    that citation as unreachable — proving the check can fail, not just
    pass, and that it fails specifically on the injected gap rather than
    on unrelated noise."""
    repo, bogus_citation = _build_source_copy_with_dangling_citation(tmp_path)
    install_sh = repo / "install.sh"

    kanban_root = tmp_path / "kanban_root_with_dangling_citation"
    home = tmp_path / "home_with_dangling_citation"

    result = _run_install(install_sh, kanban_root=kanban_root, home=home)
    assert result.returncode == 0, (
        f"Setup: the fixture install itself must succeed (the injected "
        f"citation is prose, not a script change, so install.sh is "
        f"unaffected by it).\n{_combined_output(result)}"
    )

    report = _find_unreachable_docs_citations(kanban_root)
    print(report.describe())

    unreachable_targets = [finding.cited for finding in report.findings]
    assert bogus_citation in unreachable_targets, (
        f"Expected the injected citation {bogus_citation!r} to be reported "
        f"as unreachable; it was not found among the reported unreachable "
        f"citations. This means the check failed to catch the exact gap "
        f"this red-case test injects.\n{report.describe()}"
    )
    unreachable_sources = [
        finding.source for finding in report.findings if finding.cited == bogus_citation
    ]
    assert any(source.endswith("TESTER.md") for source in unreachable_sources), (
        f"Expected the unreachable citation to be attributed to roles/TESTER.md "
        f"(where the fixture injected it), got: {unreachable_sources}"
    )


# ---------------------------------------------------------------------------
# Dimension 3, red case — a root-variable command path to a file the
# installer does not deliver.
# ---------------------------------------------------------------------------


def _build_source_copy_with_dangling_root_variable_path(
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, str]:
    """Copy this dev tree to a throwaway root, then add one line to an
    installed role file naming a $PGAI_AGENT_KANBAN_ROOT_PATH/<path> script
    the installer does not deliver. Returns (copied repo root, the bogus
    relative path added).

    The reference is added to team/roles/TESTER.md, the same file
    dimension 1's red case injects into, so the two fixtures read as
    parallel cases for parallel dimensions.
    """
    repo = tmp_path / "source_repo_with_dangling_root_variable_path"
    shutil.copytree(_REPO_ROOT, repo, ignore=_SOURCE_COPY_IGNORE)

    bogus_rel_path = "scripts/lib/does-not-exist-root-variable-guard-fixture.sh"
    target_role_file = repo / "team" / "roles" / "TESTER.md"
    with target_role_file.open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n<!-- root-variable-guard fixture: intentionally dangling "
            f"command path for the red-case test. Run `source "
            f'"$PGAI_AGENT_KANBAN_ROOT_PATH/{bogus_rel_path}"`. -->\n'
        )

    return repo, bogus_rel_path


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_root_variable_command_path_to_undelivered_file_is_reported_as_unreachable(
    tmp_path: pathlib.Path,
) -> None:
    """Adding a $PGAI_AGENT_KANBAN_ROOT_PATH/<path> command path the
    installer does not deliver, on a synthetic copy of the source tree,
    makes the check report exactly that path as unreachable — proving the
    root-variable dimension can fail, not just pass, on the exact gap it
    injects."""
    repo, bogus_rel_path = _build_source_copy_with_dangling_root_variable_path(tmp_path)
    install_sh = repo / "install.sh"

    kanban_root = tmp_path / "kanban_root_with_dangling_root_variable_path"
    home = tmp_path / "home_with_dangling_root_variable_path"

    result = _run_install(install_sh, kanban_root=kanban_root, home=home)
    assert result.returncode == 0, (
        f"Setup: the fixture install itself must succeed (the injected "
        f"reference is prose, not a script change, so install.sh is "
        f"unaffected by it).\n{_combined_output(result)}"
    )

    report = _find_unreachable_root_variable_paths(kanban_root)
    print(report.describe())

    unreachable_targets = [named for _source, named in report.unreachable]
    assert bogus_rel_path in unreachable_targets, (
        f"Expected the injected path {bogus_rel_path!r} to be reported as "
        f"unreachable; it was not found among the reported unreachable "
        f"paths. This means the check failed to catch the exact gap this "
        f"red-case test injects.\n{report.describe()}"
    )
    unreachable_sources = [
        source for source, named in report.unreachable if named == bogus_rel_path
    ]
    assert any(source.endswith("TESTER.md") for source in unreachable_sources), (
        f"Expected the unreachable path to be attributed to roles/TESTER.md "
        f"(where the fixture injected it), got: {unreachable_sources}"
    )


# ---------------------------------------------------------------------------
# Dimension 2 — docs/ citations under an upgraded root.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_every_installed_governance_docs_citation_resolves_under_upgraded_root(
    tmp_path: pathlib.Path,
) -> None:
    """The same citation-reachability property dimension 1 proves for a
    fresh install also holds for an existing root taken through the
    upgrade route: every docs/ citation reachable from the installed
    governance files resolves under a root that started without docs/
    (the shape of an install whose install.sh never deposited docs/) and was
    brought current by upgrade.sh alone.

    Builds one real installed root via install.sh (so every library
    upgrade.sh needs is genuine installed content, never a hand-curated
    subset), deletes its docs/ directory to stand in for an install
    that never received docs/, then runs the installed upgrade.sh with no VERSION argument
    against this dev tree unmodified — see this module's header for why
    that needs no git tag state at all.
    """
    kanban_root = tmp_path / "kanban_root"
    home = tmp_path / "home"
    pgai_temp = tmp_path / "pgai_temp"

    install_result = _run_install(_REAL_INSTALL_SH, kanban_root=kanban_root, home=home)
    assert install_result.returncode == 0, (
        f"Setup: the fixture install must succeed before docs/ can be "
        f"stripped and re-delivered by upgrade.\n"
        f"{_combined_output(install_result)}"
    )

    docs_dir = kanban_root / "docs"
    assert docs_dir.is_dir(), (
        "Setup: install.sh must deliver docs/ before this test can simulate "
        "an install without docs/ by removing it."
    )
    shutil.rmtree(docs_dir)

    installed_upgrade_sh = kanban_root / "scripts" / "upgrade.sh"
    assert installed_upgrade_sh.is_file(), (
        "Setup: install.sh must deliver scripts/upgrade.sh for the phase-1 "
        "bootstrap to exist."
    )

    upgrade_result = _run_upgrade(
        installed_upgrade_sh,
        dev_tree=_REPO_ROOT,
        kanban_root=kanban_root,
        home=home,
        pgai_temp=pgai_temp,
    )
    assert upgrade_result.returncode == 0, (
        f"upgrade.sh must succeed before the citation check can run.\n"
        f"{_combined_output(upgrade_result)}"
    )

    report = _find_unreachable_docs_citations(kanban_root)
    print(report.describe())

    assert not report.findings, (
        "The following docs/ citation(s) from an installed governance file "
        "do not resolve under a root taken through the upgrade route — an "
        "operator who upgrades instead of reinstalling would hit the same "
        "dangling pointer:\n" + report.describe()
    )
    assert report.citations_checked > 0, (
        "Expected at least one docs/ citation among the installed governance "
        "files; found zero — the seed file list or citation regex is broken."
    )


# ---------------------------------------------------------------------------
# Dimension 2, red case — the upgrade route stops delivering a cited
# document.
# ---------------------------------------------------------------------------


def _build_dev_tree_copy_missing_docs_manifest(tmp_path: pathlib.Path) -> pathlib.Path:
    """Copy this dev tree to a throwaway root with its docs manifest
    removed — the shared, single-source-of-truth list both install.sh and
    upgrade.sh read to know which docs/ paths to deliver. Both scripts
    treat a missing manifest as tolerable ("an older dev tree that never
    shipped this file either"), so removing it reproduces exactly the
    class of regression this dimension guards: the deposit code runs
    cleanly, exits 0, and silently delivers nothing, while every other
    installed file — including the role files that cite docs/ paths —
    proceeds normally and ends up carrying dangling citations."""
    repo = tmp_path / "dev_tree_missing_docs_manifest"
    shutil.copytree(_REPO_ROOT, repo, ignore=_SOURCE_COPY_IGNORE)
    manifest = repo / "team" / "config" / "docs-manifest.txt"
    assert manifest.is_file(), (
        f"Setup: expected a docs manifest at {manifest} to remove; the "
        f"fixture is broken if it is not there to begin with."
    )
    manifest.unlink()
    return repo


@pytest.mark.skipif(
    not _REAL_INSTALL_SH.exists(),
    reason=f"Real install.sh not found at {_REAL_INSTALL_SH}; skipping.",
)
def test_citation_undelivered_by_upgrade_route_is_reported_as_unreachable(
    tmp_path: pathlib.Path,
) -> None:
    """When the upgrade route's docs/ deposit stops delivering — here,
    because its manifest input is missing, exercising the tolerant-skip
    path upgrade.sh documents for an older dev tree — the check reports
    every citation that consequently dangles, proving the upgraded-root
    dimension can fail, not merely pass.

    The manifest is removed only from a COPY used as the upgrade's
    --dev-tree; the installed root itself is built from this real,
    unmodified dev tree, so the missing manifest is the only variable
    between this test and the green case above.
    """
    kanban_root = tmp_path / "kanban_root"
    home = tmp_path / "home"
    pgai_temp = tmp_path / "pgai_temp"

    install_result = _run_install(_REAL_INSTALL_SH, kanban_root=kanban_root, home=home)
    assert install_result.returncode == 0, (
        f"Setup: the fixture install must succeed.\n"
        f"{_combined_output(install_result)}"
    )
    shutil.rmtree(kanban_root / "docs")

    installed_upgrade_sh = kanban_root / "scripts" / "upgrade.sh"
    broken_dev_tree = _build_dev_tree_copy_missing_docs_manifest(tmp_path)

    upgrade_result = _run_upgrade(
        installed_upgrade_sh,
        dev_tree=broken_dev_tree,
        kanban_root=kanban_root,
        home=home,
        pgai_temp=pgai_temp,
    )
    assert upgrade_result.returncode == 0, (
        f"Setup: upgrade.sh must itself succeed even when its docs "
        f"manifest is missing — the missing-manifest case is a tolerant "
        f"skip, not a failure.\n{_combined_output(upgrade_result)}"
    )
    assert not (kanban_root / "docs").exists(), (
        "Setup: docs/ must remain absent after an upgrade whose manifest "
        "input is missing — if this assertion fails, the fixture is not "
        "actually exercising the gap this test injects."
    )

    report = _find_unreachable_docs_citations(kanban_root)
    print(report.describe())

    assert report.findings, (
        "Expected at least one docs/ citation to be reported unreachable "
        "after an upgrade whose docs manifest input went missing; none "
        "was. This means the check failed to catch the exact gap this "
        "red-case test injects.\n" + report.describe()
    )
