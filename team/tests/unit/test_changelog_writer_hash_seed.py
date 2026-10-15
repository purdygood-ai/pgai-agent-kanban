"""
test_changelog_writer_hash_seed.py
==================================
The CHANGELOG writer (team/pgai_agent_kanban/cm/changelog_writer.py) renders
byte-identical output under any ``PYTHONHASHSEED``: it sorts its heading
frozensets before iterating them, so set iteration order never reaches the
output. ``PYTHONHASHSEED=0`` on the release commands is defense in depth,
not a requirement for a byte-stable render.

The test renders one fixture -- the committed bug ledger under
tests/fixtures/bug_ledger plus release notes that put content under every
heading of the implemented and fixed heading sets -- through the writer's
CLI in two subprocesses with different seeds, and compares the bytes. It
first confirms the two seeds iterate those frozensets in different orders
in this interpreter, so a pass shows the sort is what keeps the output
stable rather than two seeds that happen to agree.

All paths live under pytest's tmp_path; each render gets its own copy of
the repository and ledger (the writer writes any Public ID it assigns back
into the ledger it reads), and every subprocess runs from a working
directory outside the repository with an environment built from an
explicit set.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import textwrap

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_WRITER = _TEAM_DIR / "pgai_agent_kanban" / "cm" / "changelog_writer.py"
_FIXTURE_BUGS_DIR = _TEAM_DIR / "tests" / "fixtures" / "bug_ledger"

# The baseline seed the release scripts pin, and the candidates searched for
# a second seed whose frozenset iteration order differs from it.
_BASELINE_SEED = "0"
_CANDIDATE_SEEDS = tuple(str(n) for n in range(1, 65))

# Release notes keyed by version, newest first. v1.23.0 carries one distinct
# marker line under every implemented heading and every fixed heading, so
# the rendered Implemented and Fixed lists depend on the order those
# headings are visited if the writer ever stops sorting them. The versions
# match the fixture ledger's Affects / Fixed In values, so the ledger's
# Known Issues disclosures render too.
_NOTES = {
    "v1.23.0": """\
        ## Summary
        Release with content under every implemented and fixed heading.

        ## Features
        - Marker line from the Features heading.

        ## What Shipped
        - Marker line from the What Shipped heading.

        ## Implemented
        - Marker line from the Implemented heading.

        ## Bug Fixes
        - Marker line from the Bug Fixes heading.

        ## Bugs Resolved
        - Marker line from the Bugs Resolved heading.

        ## Fixed
        - Marker line from the Fixed heading.
        """,
    "v1.16.2": """\
        ## Summary
        Intermediate release.

        ## Features
        None
        """,
    "v1.0.0": """\
        ## Summary
        First published release.

        ## Features
        - Initial release.
        """,
}

_MARKER_HEADINGS = (
    "Features",
    "What Shipped",
    "Implemented",
    "Bug Fixes",
    "Bugs Resolved",
    "Fixed",
)


def _scrubbed_env(tmp_path: pathlib.Path, seed: str) -> dict[str, str]:
    """Return a subprocess environment from an explicit set, with *seed* pinned."""
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(_TEAM_DIR)},
    )


def _heading_iteration_order(tmp_path: pathlib.Path, seed: str) -> list[list[str]]:
    """Return the raw (unsorted) iteration order of the writer's heading sets under *seed*."""
    probe = (
        "import json\n"
        "from pgai_agent_kanban.cm import changelog_writer as w\n"
        "print(json.dumps([list(w._IMPLEMENTED_HEADINGS), list(w._FIXED_HEADINGS)]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=_scrubbed_env(tmp_path, seed),
        timeout=60,
    )
    assert result.returncode == 0, (
        f"heading-order probe failed under PYTHONHASHSEED={seed}:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def _fixture_tree(tmp_path: pathlib.Path, name: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Build one independent repository + ledger copy under tmp_path/<name>.

    Returns:
        (repo_root, bugs_dir)
    """
    base = tmp_path / name
    notes_dir = base / "repo" / "release-notes"
    notes_dir.mkdir(parents=True)
    for version, body in _NOTES.items():
        header = (
            f"# Release Notes: test-project {version}\n\n"
            f"**Release Date:** 2026-01-01\n"
            f"**Released By:** test\n\n"
        )
        (notes_dir / f"{version}.md").write_text(
            header + textwrap.dedent(body), encoding="utf-8"
        )
    (notes_dir / "PUBLISHED").write_text(
        "v1.0.0\nv1.16.2\nv1.23.0\n", encoding="utf-8"
    )
    bugs_dir = base / "bugs"
    shutil.copytree(_FIXTURE_BUGS_DIR, bugs_dir)
    return base / "repo", bugs_dir


def _render(tmp_path: pathlib.Path, seed: str) -> subprocess.CompletedProcess[bytes]:
    """Render a fresh fixture tree through the writer CLI under *seed*."""
    repo_root, bugs_dir = _fixture_tree(tmp_path, f"seed-{seed}")
    cwd = tmp_path / f"cwd-{seed}"
    cwd.mkdir()
    return subprocess.run(
        [sys.executable, str(_WRITER), str(repo_root), str(bugs_dir)],
        capture_output=True,
        cwd=str(cwd),
        env=_scrubbed_env(tmp_path, seed),
        timeout=60,
    )


def test_render_is_byte_identical_under_two_hash_seeds(tmp_path: pathlib.Path) -> None:
    baseline_order = _heading_iteration_order(tmp_path, _BASELINE_SEED)
    other_seed = next(
        (
            seed
            for seed in _CANDIDATE_SEEDS
            if _heading_iteration_order(tmp_path, seed) != baseline_order
        ),
        None,
    )
    assert other_seed is not None, (
        f"No PYTHONHASHSEED in {_CANDIDATE_SEEDS[0]}..{_CANDIDATE_SEEDS[-1]} iterates "
        f"the writer's heading frozensets in an order different from seed "
        f"{_BASELINE_SEED} ({baseline_order}); the test cannot show seed-independence. "
        "Widen _CANDIDATE_SEEDS."
    )

    first = _render(tmp_path, _BASELINE_SEED)
    second = _render(tmp_path, other_seed)

    for seed, result in ((_BASELINE_SEED, first), (other_seed, second)):
        assert result.returncode == 0, (
            f"writer exited {result.returncode} under PYTHONHASHSEED={seed}:\n"
            f"{result.stderr.decode('utf-8', 'replace')}"
        )
    text = first.stdout.decode("utf-8")
    missing = [h for h in _MARKER_HEADINGS if f"Marker line from the {h} heading." not in text]
    assert not missing, (
        f"render under PYTHONHASHSEED={_BASELINE_SEED} dropped the marker lines for "
        f"{missing}; the fixture no longer exercises every heading.\n{text}"
    )
    assert "KI-1.0.0." in text, (
        f"render under PYTHONHASHSEED={_BASELINE_SEED} carries no fixture-ledger "
        f"Known Issues disclosure; the ledger did not reach the output.\n{text}"
    )
    assert first.stdout == second.stdout, (
        f"render differs between PYTHONHASHSEED={_BASELINE_SEED} and "
        f"PYTHONHASHSEED={other_seed}.\n"
        f"--- seed {_BASELINE_SEED} ---\n{text}\n"
        f"--- seed {other_seed} ---\n{second.stdout.decode('utf-8', 'replace')}"
    )
