"""
bug_filer.py — the DEEP-VERIFY tier's failure-path bug filer.

team/SOP-core.md ("Gates and Refusals" -- "Unusable build blocks; an
unavailable build environment does not") separates a verification lane's outcome into three cases; only one of
them — failed-to-build, a real tree defect — warrants a follow-up filing.
This module is the thing that turns a failed-to-build deep-verify run into a
bug report in the project's bugs/ directory, the same on-ramp TESTER's Path C
(autonomous follow-up filing — non-blocking; team/roles/TESTER.md, Step 10)
already uses, so the discovery pipeline bundles the finding into the next RC
through the ordinary path. A could-not-attempt verdict describes the
verifying host, not the tree, and files no build bug (SOP-core.md's outcome
3). team/scripts/deep-verify.sh reaches this module when the classifier
returns VERDICT_FAILED_TO_BUILD, when it returns VERDICT_BUILT for a lane
that still exited non-zero because a non-build cold test failed (the builds
are recorded as built), and when it returns VERDICT_COULD_NOT_ATTEMPT for a
lane that exited 1 because a non-build cold test failed while no pair could
be built. In the last two cases the failing test is filed here as a test
failure naming its file and test id; the report says the builds were
recorded as built only when the captured output shows build evidence
(classify.shows_build_evidence).

What this module does NOT do: touch release-state.md, any task status.md,
any queue file, or any HALT-family signal file, or create/delete a branch.
Filing a bug is writing one new file into <project>/bugs/ — nothing else is
in scope, which is what keeps a deep-verify run's failure path isolated from
whatever RC happens to be open when it fires (team/roles/PM.md's
RC-immutability guarantee already keeps discovery from bundling the filed
bug until the RC closes; this module does not need to know or care
whether an RC is open).

Deduplication ("same failing pair" rule)
-----------------------------------------
Repeated red runs against the same broken (engine, flavor) pair must not
file a fresh duplicate bug on every firing when an open or running bug
already covers it — team/roles/TESTER.md's own "cites an OPEN bug" rule
(Step 6) generalizes here mechanically: this module keys identity on the
exact SET of (engine, flavor) pairs a run's captured output names as
failing, recorded in each filed bug's "## Deep-Verify Pairs" section (one
`engine/flavor` line per pair — not part of the standard BUG-TEMPLATE.md
shape, but additive: TESTER.md's own Path C template note establishes
that "additional sections... may be added if useful" beyond the required
minimum). A later run whose failing-pair set exactly matches an existing
open/running bug's recorded set cites that bug's BUG-NNNN id instead of
filing a new one. A run whose failing-pair set differs (a new pair joined
the failure, or the failure moved to a different pair) files a fresh bug,
because "same failing pair" no longer describes it.

A run that names no structured pair but whose pytest short test summary
names failing tests is keyed the same way on its exact SET of failing
pytest node ids, recorded in a "## Deep-Verify Tests" section (one node id
per line): a later run failing the same tests cites the open/running bug,
and a different set files a fresh one.

Filed-bug shape
----------------
Follows team/templates/project/release/BUG-TEMPLATE.md's header/section
order (Bug ID, Filed By, Date, Severity, Status, Category, then Symptom /
Expected / Actual / Reproduction / Files Involved), using the numbering
convention TESTER's Path C-A already uses: claim_next_bug_id() from
team/pm-agent/lib/bug_scanner.py, imported via
pgai_agent_kanban.lib.bug_filing.import_bug_scanner(), which self-locates
pm-agent/ from its own file location ("each tree finds itself" —
team/roles/CODER.md Principle 6) since pm-agent/ is a sibling of
pgai_agent_kanban/ in both the dev tree (team/pgai_agent_kanban,
team/pm-agent) and the live install (<root>/pgai_agent_kanban,
<root>/pm-agent), never a package this module is normally importable from.
The claim helper and the open/running bugs_dir scan are shared with
pgai_agent_kanban.lint.bug_filer via that same module (docs/coding-
standards.md directive 1); only this filer's own dedup key (a failing-pair
set) and its report content are specific to this module.

The `**Filed By:**` line follows the self-documenting label convention
(team/roles/TESTER.md, "Self-documenting label convention"): the token
`deep-verify (autonomous)` names the filer, followed by ` — ` and one plain
sentence naming the defect in human terms.

`## Status` is written as `open` (never `running`) — an autonomous filing
has no in-flight fix ticket yet, and `open` is what makes the file eligible
for the discovery pipeline's bug bundler on its very next scan
(team/roles/PM.md, "The discovery pipeline").

Usage (import):
    from pgai_agent_kanban.deep_verify.bug_filer import file_or_cite_bug
    result = file_or_cite_bug(bugs_dir, project="demo", version="v1.2.3",
                               commit="abc1234", output_text=captured_text)
    result.outcome       # "filed" | "duplicate" | "no-pairs"
    result.bug_id        # the claimed "BUG-<NNNN>" id (filed or duplicate),
                          # or None (no-pairs)
    result.bug_path      # str path to the filed file, or None
    result.pairs         # tuple of "engine/flavor" strings this run's
                          # output named as failing
    result.tests         # tuple of pytest node ids named as failing when
                          # no pair was named

Usage (CLI, consumed by team/scripts/lib/deep_verify_bug_filer.sh via
team/scripts/lib/pp_run_ops.sh):
    python3 -m pgai_agent_kanban.deep_verify.bug_filer \\
        <bugs_dir> <project> <version> <commit> <captured_output_file>
    Prints "key=value" lines: outcome=..., bug_id=... (or bug_id= when
    none), bug_path=... (or bug_path= when none), pairs=<comma-joined
    engine/flavor list, or empty>, tests=<comma-joined pytest node ids, or
    empty>.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Run by file path (``python3 .../bug_filer.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.deep_verify.classify import (  # noqa: E402
    FailingPair,
    extract_failing_pairs,
    extract_failing_tests,
    shows_build_evidence,
)
from pgai_agent_kanban.lib.bug_filing import iter_open_or_running_bugs  # noqa: E402
from pgai_agent_kanban.lib.bug_filing import import_bug_scanner as _import_bug_scanner  # noqa: E402

# ---------------------------------------------------------------------------
# Bug-file field parsing (reads a small, deliberately narrow slice of an
# existing bug file's shape — the Deep-Verify Pairs section this module
# itself writes — never the full BUG-TEMPLATE.md field set). Status parsing
# and the open/running walk over bugs_dir are shared with
# pgai_agent_kanban.lint.bug_filer via pgai_agent_kanban.lib.bug_filing
# (docs/coding-standards.md directive 1) — only the pairs-section
# extraction and the match predicate below are specific to this filer's own
# dedup key.
# ---------------------------------------------------------------------------

_PAIRS_SECTION_RE = re.compile(
    r"^##\s+Deep-Verify Pairs\s*\n(?P<body>(?:.*\n?)*?)(?=^##\s|\Z)",
    re.MULTILINE,
)

_TESTS_SECTION_RE = re.compile(
    r"^##\s+Deep-Verify Tests\s*\n(?P<body>(?:.*\n?)*?)(?=^##\s|\Z)",
    re.MULTILINE,
)


def _read_section_lines(section_re: "re.Pattern", text: str) -> frozenset:
    match = section_re.search(text)
    if not match:
        return frozenset()
    lines = (line.strip() for line in match.group("body").splitlines())
    return frozenset(line for line in lines if line)


def _read_recorded_pairs(text: str) -> frozenset:
    return _read_section_lines(_PAIRS_SECTION_RE, text)


def find_existing_bug_for_tests(bugs_dir: str, nodeids) -> "str | None":
    """Return the BUG-NNNN id of an open/running bug in *bugs_dir* whose
    recorded '## Deep-Verify Tests' set is exactly *nodeids* (a set of
    pytest node ids), or None when no such bug exists. Same scan order and
    exact-set rule as find_existing_bug_for_pairs()."""
    nodeids = frozenset(nodeids)
    if not nodeids:
        return None

    for bug_id, text in iter_open_or_running_bugs(bugs_dir):
        if _read_section_lines(_TESTS_SECTION_RE, text) == nodeids:
            return bug_id
    return None


def find_existing_bug_for_pairs(bugs_dir: str, pair_keys) -> "str | None":
    """Return the BUG-NNNN id of an open/running bug in *bugs_dir* whose
    recorded '## Deep-Verify Pairs' set is exactly *pair_keys* (a set of
    "engine/flavor" strings), or None when no such bug exists.

    Scans in filename-sorted order (via iter_open_or_running_bugs) so a
    caller comparing two candidate matches (should the input ever be
    ambiguous) sees a deterministic pick; in practice exactly one bug is
    expected to carry a given pair set.
    """
    pair_keys = frozenset(pair_keys)
    if not pair_keys:
        return None

    for bug_id, text in iter_open_or_running_bugs(bugs_dir):
        if _read_recorded_pairs(text) == pair_keys:
            return bug_id
    return None


# ---------------------------------------------------------------------------
# Bug content rendering
# ---------------------------------------------------------------------------


def _pair_key(pair: FailingPair) -> str:
    return f"{pair.engine}/{pair.flavor}"


def _render_bug_content(
    *,
    bug_id: str,
    slug: str,
    project: str,
    version: str,
    commit: str,
    pairs: list,
    filed_at: str,
) -> str:
    pair_keys = [_pair_key(p) for p in pairs]
    pair_summary = "; ".join(pair_keys) if pair_keys else "unknown pair"
    step_lines = "\n".join(
        f"- `{_pair_key(p)}` failed at step: {p.step}" for p in pairs
    ) or "- no structured engine/flavor/step detail could be extracted from captured output"
    reproduction = "\n".join(
        f"{i}. From an operator shell (the container tier is operator-only), run "
        f"`PGAI_CONTAINER_TIER=1 team/scripts/deep-verify.sh --project {project} --image all` (or wait for the "
        f"weekly `--scheduled` firing) against commit `{commit}` ({version}) and observe "
        f"`{_pair_key(p)}` fail cold at step: {p.step}"
        for i, p in enumerate(pairs, start=1)
    ) or (
        f"1. From an operator shell (the container tier is operator-only), run "
        f"`PGAI_CONTAINER_TIER=1 team/scripts/deep-verify.sh --project {project} --image all` against commit "
        f"`{commit}` ({version}); the cold-build lane exits non-zero but no structured "
        "engine/flavor/step detail is present in the captured output -- see the "
        "project's deep-verify-ledger.md for the run's raw detail line"
    )

    pairs_section = "\n".join(pair_keys) if pair_keys else ""

    return (
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        f"**Filed By:** deep-verify (autonomous) — the cold-build lane failed to build "
        f"{pair_summary} for {project} at {version} (commit {commit}).\n"
        f"**Date:** {filed_at}\n"
        "**Severity:** critical\n\n"
        "## Status\nopen\n\n"
        "## Category\ndeep-verify\n\n"
        "---\n\n"
        "## Symptom\n"
        f"The DEEP-VERIFY tier's cold-build lane failed to build cold for {project} at "
        f"{version} (commit {commit}). Failing pair(s):\n"
        f"{step_lines}\n\n"
        "## Expected\n"
        "Every (engine, flavor) pair declared in team/config/container-images.txt "
        "builds cold (no cache, "
        "freshly pulled base image) without error.\n\n"
        "## Actual\n"
        f"{step_lines}\n\n"
        "## Reproduction\n"
        f"{reproduction}\n\n"
        "## Files Involved\n"
        + "\n".join(f"- docker/{p.flavor}/Dockerfile" for p in pairs)
        + ("\n" if pairs else "- (no structured pair extracted; see captured output)\n")
        + "\n"
        "## Hypothesis (optional)\n"
        "Speculative -- see the failing step named above for the Dockerfile line to "
        "start from.\n\n"
        "---\n\n"
        "## Deep-Verify Pairs\n"
        f"{pairs_section}\n\n"
        "## Deep-Verify Run\n"
        f"- Project: {project}\n"
        f"- Version: {version}\n"
        f"- Commit: {commit}\n"
    )


def _slug_for_pairs(pairs: list) -> str:
    if not pairs:
        return "cold-build-failure"
    first = pairs[0]
    flavor_slug = re.sub(r"[^a-z0-9]+", "-", first.flavor.lower()).strip("-") or "image"
    return f"cold-build-{flavor_slug}-failure"


def _repo_path(test_file: str) -> str:
    """A pytest node-id file path (relative to team/, where the cold lane
    runs pytest) as a repository path."""
    return test_file if test_file.startswith("team/") else f"team/{test_file}"


def _slug_for_tests(tests: list) -> str:
    module = tests[0].file.replace("\\", "/").rsplit("/", 1)[-1]
    module = re.sub(r"\.py$", "", module)
    module = re.sub(r"^test_", "", module)
    module_slug = re.sub(r"[^a-z0-9]+", "-", module.lower()).strip("-") or "test"
    return f"cold-test-{module_slug}-failure"


def _render_test_bug_content(
    *,
    bug_id: str,
    slug: str,
    project: str,
    version: str,
    commit: str,
    tests: list,
    filed_at: str,
    build_evidence: bool = True,
) -> str:
    """Bug content for a cold-lane run whose failure is a named test rather
    than a structured Dockerfile-step failure.

    *build_evidence* is classify.shows_build_evidence() of the run's
    captured output. When every failing test is a non-build test, the note
    under the symptom says the ledger records the builds as built only when
    it is True; when it is False (no pair built, pairs narrate-skipped) the
    note says no image was built and the run is recorded as
    could-not-attempt."""
    nodeids = [t.nodeid for t in tests]
    first = tests[0]
    more = f" and {len(tests) - 1} more" if len(tests) > 1 else ""
    test_lines = "\n".join(
        f"- `{t.nodeid}` {t.outcome} -- file `{_repo_path(t.file)}`, test "
        f"`{t.test_id or '(module collection)'}`"
        + (f": {t.reason}" if t.reason else "")
        for t in tests
    )
    files = sorted({_repo_path(t.file) for t in tests})
    all_non_build = not any(t.is_build_test for t in tests)
    if not all_non_build:
        build_note = ""
    elif build_evidence:
        build_note = (
            "No image-build test failed, so the deep-verify ledger records the "
            "builds as `built`; this report is for the failing test(s) alone.\n\n"
        )
    else:
        build_note = (
            "No image was built: every (engine, flavor) pair the run reached was "
            "narrate-skipped for an unavailable build environment, so the "
            "deep-verify ledger records the run as `could-not-attempt`; this "
            "report is for the failing test(s) alone.\n\n"
        )

    return (
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        f"**Filed By:** deep-verify (autonomous) — the cold-build lane test "
        f"{first.nodeid}{more} failed for {project} at {version} (commit {commit}).\n"
        f"**Date:** {filed_at}\n"
        "**Severity:** medium\n\n"
        "## Status\nopen\n\n"
        "## Category\ndeep-verify\n\n"
        "---\n\n"
        "## Symptom\n"
        f"The DEEP-VERIFY tier's cold-build lane failed for {project} at {version} "
        f"(commit {commit}). Failing test(s):\n"
        f"{test_lines}\n\n"
        f"{build_note}"
        "## Expected\n"
        "Every test the cold-build lane collects under team/tests/cold/ passes.\n\n"
        "## Actual\n"
        f"{test_lines}\n\n"
        "## Reproduction\n"
        "1. From an operator shell (the container tier is operator-only), run "
        f"`PGAI_CONTAINER_TIER=1 team/scripts/deep-verify.sh --project {project} --image all` "
        f"against commit `{commit}` ({version}).\n"
        "2. Read pytest's short test summary in the output: it names "
        + ", ".join(f"`{n}`" for n in nodeids)
        + " as failed.\n\n"
        "## Files Involved\n"
        + "\n".join(f"- {f}" for f in files)
        + "\n\n"
        "## Hypothesis (optional)\n"
        "Speculative -- the test may be stale against the script it exercises, "
        "or that script may have regressed; the failure message pytest prints "
        "for the test is the place to start.\n\n"
        "---\n\n"
        "## Deep-Verify Tests\n"
        + "\n".join(nodeids)
        + "\n\n"
        "## Deep-Verify Run\n"
        f"- Project: {project}\n"
        f"- Version: {version}\n"
        f"- Commit: {commit}\n"
    )


# ---------------------------------------------------------------------------
# Public result type and entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingResult:
    """Outcome of one file_or_cite_bug() call.

    outcome   -- "filed" (a new bug file was written), "duplicate" (an
                 existing open/running bug already covers the exact same
                 failing-pair set, or failing-test set; nothing was
                 written), or "no-pairs" (the captured output carried
                 neither a structured failing pair nor a failing test
                 pytest named; a bug is still filed under a generic
                 identity, but dedup has nothing to key on, so this outcome
                 is informational only -- filing still happens).
    bug_id    -- "BUG-NNNN" for "filed" or "duplicate"; None only when no
                 bugs_dir could be used (never expected in practice, since
                 the caller always resolves a project's bugs_dir first).
    bug_path  -- absolute path to the filed file (new filings only); None
                 for "duplicate" (no file was written).
    pairs     -- tuple of "engine/flavor" strings this run's output named
                 as failing (empty for the no-structured-detail case).
    tests     -- tuple of pytest node ids the run's output named as failed
                 or errored, when no structured pair was named (empty
                 otherwise); the identity a test-failure bug is keyed on.
    """

    outcome: str
    bug_id: "str | None"
    bug_path: "str | None"
    pairs: tuple
    tests: tuple = ()


def file_or_cite_bug(
    bugs_dir: str,
    *,
    project: str,
    version: str,
    commit: str,
    output_text: str,
    filed_at: "str | None" = None,
) -> FilingResult:
    """File a bug for a failed deep-verify run, or cite an existing one that
    already covers the same failure.

    A run whose output names structured Dockerfile-step failures is filed
    (and de-duplicated) by its failing (engine, flavor) pair set. A run that
    names none, but whose pytest summary names failing tests -- a non-build
    cold test failing while every image built or while no pair could be
    built, or a build test failing without a structured step line -- is
    filed by its failing-test set, naming each test's file and test id.

    Creates *bugs_dir* if it does not yet exist (a fresh project's first
    deep-verify failure has no bugs/ directory yet). Never touches anything
    outside *bugs_dir* -- no ledger write, no release-state.md, no task or
    queue file, no HALT-family signal file (see module docstring).
    """
    os.makedirs(bugs_dir, exist_ok=True)

    pairs = extract_failing_pairs(output_text)
    pair_keys = frozenset(_pair_key(p) for p in pairs)
    tests = [] if pairs else extract_failing_tests(output_text)
    test_keys = frozenset(t.nodeid for t in tests)

    existing = None
    if pair_keys:
        existing = find_existing_bug_for_pairs(bugs_dir, pair_keys)
    elif test_keys:
        existing = find_existing_bug_for_tests(bugs_dir, test_keys)
    if existing:
        return FilingResult(
            outcome="duplicate",
            bug_id=existing,
            bug_path=None,
            pairs=tuple(sorted(pair_keys)),
            tests=tuple(sorted(test_keys)),
        )

    claim_next_bug_id, release_bug_id_claim = _import_bug_scanner()
    slug = _slug_for_tests(tests) if tests else _slug_for_pairs(pairs)
    bug_id, bug_path, lock_path = claim_next_bug_id(
        bugs_dir, slug, claimed_by="deep-verify-cold-build-filer"
    )
    filed_at = filed_at or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if tests:
        content = _render_test_bug_content(
            bug_id=bug_id,
            slug=slug,
            project=project,
            version=version,
            commit=commit,
            tests=tests,
            filed_at=filed_at,
            build_evidence=shows_build_evidence(output_text),
        )
    else:
        content = _render_bug_content(
            bug_id=bug_id,
            slug=slug,
            project=project,
            version=version,
            commit=commit,
            pairs=pairs,
            filed_at=filed_at,
        )
    try:
        Path(bug_path).write_text(content, encoding="utf-8")
    finally:
        release_bug_id_claim(lock_path)

    return FilingResult(
        outcome="filed" if pair_keys or test_keys else "no-pairs",
        bug_id=bug_id,
        bug_path=str(bug_path),
        pairs=tuple(sorted(pair_keys)),
        tests=tuple(sorted(test_keys)),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_CLI_HELP = """\
usage: bug_filer.py <bugs_dir> <project> <version> <commit> <captured_output_file>

File a bug for a failed deep-verify run, or cite the open or running bug
that already records the same failing engine/flavor pairs (or, when no
structured pair is named, the same failing cold-lane tests).

Positional arguments (all five required, in this order):
  bugs_dir              the project's bugs/ directory; created when absent
  project               the project name the run verified
  version               the version the run verified, e.g. v1.2.3
  commit                the commit the run verified
  captured_output_file  a file holding the run's captured output, read for
                        the failing engine/flavor pairs, else the failing
                        tests pytest's short test summary names

Prints five key=value lines: outcome, bug_id, bug_path, pairs (the failing
pairs, comma-joined, empty when none), tests (the failing pytest node ids,
comma-joined, empty when none or when pairs were named).

Options:
  -h, --help            print this help and exit 0

Exit codes: 0 filed, cited or no pairs found; 2 wrong argument list or an
unreadable captured_output_file.
"""


def main(argv: "list | None" = None) -> int:
    """CLI: bug_filer.py <bugs_dir> <project> <version> <commit> <captured_output_file>

    Prints five "key=value" lines to stdout: outcome, bug_id, bug_path,
    pairs (comma-joined "engine/flavor" list, empty when none), tests
    (comma-joined pytest node ids, empty when none). Used by
    team/scripts/lib/deep_verify_bug_filer.sh, which cannot import Python
    directly from bash.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(_CLI_HELP, end="")
        return 0
    if len(argv) != 5:
        print(
            "usage: bug_filer.py <bugs_dir> <project> <version> <commit> "
            "<captured_output_file>",
            file=sys.stderr,
        )
        return 2

    bugs_dir, project, version, commit, output_file = argv
    try:
        with open(output_file, "r", encoding="utf-8", errors="replace") as handle:
            output_text = handle.read()
    except OSError as exc:
        print(f"bug_filer.py: could not read {output_file}: {exc}", file=sys.stderr)
        return 2

    result = file_or_cite_bug(
        bugs_dir, project=project, version=version, commit=commit, output_text=output_text
    )
    print(f"outcome={result.outcome}")
    print(f"bug_id={result.bug_id or ''}")
    print(f"bug_path={result.bug_path or ''}")
    print(f"pairs={','.join(result.pairs)}")
    print(f"tests={','.join(result.tests)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
