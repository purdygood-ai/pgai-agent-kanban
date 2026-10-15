#!/usr/bin/env python3
"""
lint_rc_empty_commits.py
=========================
Flags a non-merge commit in a release branch's range against its main
branch that changes no file — the signature of a stray
``git commit --allow-empty`` (or an edit reverted to identical bytes)
landing on the branch instead of a task's declared work.

WHERE THIS RUNS
----------------
Wired into ``team/scripts/run-integration-tests.sh`` through
``gated_runner_run_quality_lint``, so the existing "run the gated suites"
step every TESTER verification already performs exercises this lint on
every invocation — no separate command for an agent to remember. Range
discovery is local-branch-based (see ``discover_rc_ranges`` below): a dev
tree carrying no ``<prefix>rc/vX.Y.Z`` branch has nothing to scan, and the
lint prints one informational line and exits 0. That is a legitimate
no-op, not a skip that needs narrating — a tree with no RC in flight has
nothing for this check to have an opinion about.

WHAT COUNTS AS "EMPTY"
------------------------
A non-merge commit whose full tree is byte-identical to its single
parent's, determined with
``git diff-tree -r --no-commit-id --name-only <sha>`` — which compares
blob content hashes, not "was some path's name touched." A commit that
rewrites a file back to byte-identical content is therefore ALSO flagged,
deliberately rather than by oversight: the resulting blob hash is
unchanged, diff-tree reports no path under it, and the commit is exactly
as accidental as one that touched nothing at all.

A merge commit (two or more parents) is never inspected for emptiness:
``git rev-list --no-merges`` excludes it before this lint ever sees it,
because a merge commit is legitimately "empty" of its own tree changes by
definition — it exists to record two histories joining, not to carry
independent content.

CLASSIFICATION
---------------
CONVERT. An empty non-merge commit changes no file, so the shipped
artefact is byte-for-byte identical whether or not the commit is present
— zero runtime-failure risk. The cost is to provenance (a commit nothing
accounts for), exactly the class of finding the gated runner's
quality-class lane exists to carry forward without stopping the loop (see
team/SOP-core.md, "Gates and Refusals" and "The Loop"). This lint's own
process exit code is therefore never treated as fatal by its caller:
``gated_runner_run_quality_lint`` records a non-zero exit as one finding
and continues to pytest regardless.

Usage:
    lint_rc_empty_commits.py [--repo PATH] [--range REV_RANGE ...]

    --repo PATH        Git repository to inspect (default: this script's
                        own repo, resolved via
                        ``git rev-parse --show-toplevel``).
    --range REV_RANGE  Explicit "main..rc" range to check (e.g.
                        "ai_main..ai_rc/v9.9.9"). May be repeated. When
                        omitted, every locally present
                        ``<prefix>rc/vX.Y.Z`` branch is paired with its
                        same-prefix ``<prefix>main`` branch and checked
                        automatically.

Exit codes:
    0   No empty non-merge commit found in any range checked (including
        the no-RC-branch no-op case).
    1   At least one empty non-merge commit found; each is printed.
    2   Pre-flight failure — not a git repository, or an explicit
        --range does not resolve.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# team/ import path -- needed for the dual-channel error-contract emitter.
# ---------------------------------------------------------------------------
_TEAM_DIR = Path(__file__).resolve().parent.parent  # team/
_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

# ---------------------------------------------------------------------------
# A local branch named "<prefix>rc/vX.Y.Z" pairs with "<prefix>main". The
# prefix (e.g. "ai_") is whatever precedes the literal "rc/v" token, and may
# be empty.
# ---------------------------------------------------------------------------
_RC_BRANCH_RE = re.compile(r"^(?P<prefix>.*)rc/v\d+\.\d+\.\d+$")


class EmptyCommit(NamedTuple):
    """One empty non-merge commit found in a checked range."""

    sha: str
    subject: str
    rev_range: str


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )


def discover_rc_ranges(repo: Path) -> list[str]:
    """Pair every local ``<prefix>rc/vX.Y.Z`` branch with ``<prefix>main``.

    Returns one ``"<prefix>main..<prefix>rc/vX.Y.Z"`` range string per pair
    where both refs exist locally. A repo with no matching RC branch (or
    whose RC branch has no matching main branch) yields ``[]``.
    """
    result = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if result.returncode != 0:
        return []

    branches = [line for line in result.stdout.splitlines() if line.strip()]
    branch_set = set(branches)
    ranges: list[str] = []
    for branch in branches:
        match = _RC_BRANCH_RE.match(branch)
        if not match:
            continue
        main_branch = f"{match.group('prefix')}main"
        if main_branch in branch_set:
            ranges.append(f"{main_branch}..{branch}")
    return ranges


def find_empty_commits(repo: Path, rev_range: str) -> list[EmptyCommit]:
    """Return every non-merge commit in *rev_range* whose tree is
    byte-identical to its parent's.

    Raises ValueError if *rev_range* does not resolve in *repo*.
    """
    rev_list = _git(repo, "rev-list", "--no-merges", rev_range)
    if rev_list.returncode != 0:
        raise ValueError(
            f"git rev-list --no-merges {rev_range!r} failed: "
            f"{rev_list.stderr.strip()}"
        )

    empty: list[EmptyCommit] = []
    for sha in (line.strip() for line in rev_list.stdout.splitlines() if line.strip()):
        diff = _git(repo, "diff-tree", "-r", "--no-commit-id", "--name-only", sha)
        if diff.returncode != 0:
            raise ValueError(f"git diff-tree failed for {sha}: {diff.stderr.strip()}")
        if diff.stdout.strip():
            continue  # at least one path changed against the parent tree

        subject = _git(repo, "log", "-1", "--format=%s", sha).stdout.strip()
        empty.append(EmptyCommit(sha=sha, subject=subject, rev_range=rev_range))
    return empty


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_rc_empty_commits",
        description=(
            "Flag a non-merge commit in a release branch's range against "
            "its main branch that changes no file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  No empty non-merge commit found (including no RC branch present).\n"
            "  1  At least one empty non-merge commit found.\n"
            "  2  Pre-flight failure (bad --repo or an explicit --range does not resolve).\n"
        ),
    )
    parser.add_argument(
        "--repo",
        metavar="PATH",
        default=None,
        help="Git repository to inspect (default: this script's own repo).",
    )
    parser.add_argument(
        "--range",
        dest="ranges",
        metavar="REV_RANGE",
        action="append",
        default=None,
        help=(
            "Explicit main..rc range to check; may be repeated. Default: "
            "auto-discover every local <prefix>rc/vX.Y.Z branch."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the RC empty-commit lint over each RC branch range (or each ``--range``).

    Exit codes: 0 no empty non-merge commit (including a tree with no RC
    branch); 1 at least one, each printed; 2 not a git repository, or an
    explicit ``--range`` does not resolve.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.repo:
        repo = Path(args.repo).resolve()
    else:
        here = Path(__file__).resolve().parent
        toplevel = _git(here, "rev-parse", "--show-toplevel")
        if toplevel.returncode != 0:
            print(
                f"lint_rc_empty_commits: {here} is not inside a git repository: "
                f"{toplevel.stderr.strip()}",
                file=sys.stderr,
            )
            return 2
        repo = Path(toplevel.stdout.strip())

    if _git(repo, "rev-parse", "--git-dir").returncode != 0:
        print(f"lint_rc_empty_commits: {repo} is not a git repository", file=sys.stderr)
        return 2

    if args.ranges:
        ranges = args.ranges
    else:
        ranges = discover_rc_ranges(repo)

    if not ranges:
        print(
            "lint_rc_empty_commits: no <prefix>rc/vX.Y.Z branch found locally "
            "-- nothing to check."
        )
        return 0

    hits: list[EmptyCommit] = []
    for rev_range in ranges:
        try:
            hits.extend(find_empty_commits(repo, rev_range))
        except ValueError as exc:
            print(f"lint_rc_empty_commits: {exc}", file=sys.stderr)
            return 2

    if not hits:
        print(
            f"lint_rc_empty_commits: OK -- checked {len(ranges)} range(s) "
            f"({', '.join(ranges)}), no empty non-merge commits found."
        )
        return 0

    for hit in hits:
        print(
            f"lint_rc_empty_commits: empty non-merge commit {hit.sha[:7]} "
            f"in {hit.rev_range} -- \"{hit.subject}\" changes no file "
            "against its parent."
        )

    # gated_runner_run_quality_lint records this exit as one non-fatal
    # finding and the runner continues to pytest regardless -- see the
    # module docstring's "CLASSIFICATION" section for the full rationale.
    emit_error_contract(
        "lint.rc-empty-commits",
        f"{len(hits)} empty non-merge commit(s) found in the checked "
        "range(s); each changes no file relative to its parent, so no "
        "task's declared work accounts for it.",
        "Read the commit(s) named above with 'git show --stat <sha>' to "
        "confirm which task session produced it, then record the finding "
        "in that task's status.md (or file a bug) -- the commit itself is "
        "harmless and rewriting a branch other agents have merged into is "
        "not proposed.",
    )
    # CONVERT: zero runtime-failure risk to the shipped artefact -- an
    # empty commit changes no file. The cost is to provenance only.
    # CENSUS-ANCHOR: rc-empty-commits-01
    return 1


if __name__ == "__main__":
    sys.exit(main())
