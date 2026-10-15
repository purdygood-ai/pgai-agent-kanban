"""
test_discovery_next_patch_marker_slot.py
========================================
Unit tests for the materialized-plan slot source of
``discovery_compute_next_patch`` (team/scripts/lib/discovery_steps.sh).

WHAT IS PINNED
--------------
``discovery_compute_next_patch`` walks candidate patch versions upward from
the last released version and skips any candidate already taken. One of its
three slot sources is the set of ``tasks/queues/plans/.materialized.*``
marker files: a candidate named inside any marker's contents (as a delimited
token, so ``v0.0.3`` is not matched by ``v0.0.30``) is taken. If that check
wrongly reports "free", the bundler hands out a version a materialized plan
already claimed.

The behaviours locked in here:

  1. One marker naming the first candidate makes the function skip it.
  2. Many markers naming the first candidate make the function skip it too.
     The marker count is chosen so the newline-separated list of matching
     marker paths is far larger than a pipe buffer plus one reader's read:
     a check that lists matching files into an early-exiting reader
     (``grep -l ... | grep -q .``) under ``pipefail`` must then have its
     lister write after the reader has gone, take SIGPIPE, and report the
     taken slot as free. That makes the losing schedule certain rather
     than likely, with no sleep, load or FIFO handshake needed.
  3. A marker that names one matching version plus an unreadable marker
     still marks the slot taken -- a listing check whose lister exits 2 on
     the unreadable file loses the match under ``pipefail`` regardless of
     scheduling.
  4. A marker naming a version that merely starts with the candidate's
     digits (``v0.0.30`` against candidate ``v0.0.3``) does not take it.
  5. No plans directory, and an empty plans directory, leave the first
     candidate free.

A calibration test proves the many-marker fixture really defeats a
pipe-listing check, so behaviour 2 cannot pass for the wrong reason.

HOW THE FUNCTION IS DRIVEN
--------------------------
Every case runs the real bash function through the shared shell harness
under ``set -euo pipefail`` -- the strict mode pm-agent.sh and the wake run
discovery under, and the mode the listing-pipe defect depends on.

The sandbox is a throwaway kanban root under ``tmp_path`` with one project
and no ``project.cfg``. That leaves the other two slot sources inert:
requirements/ is never created, and with no configured dev tree the git-tag
source falls back to the working directory's repository, so the subprocess
runs from a directory inside the sandbox with ``GIT_CEILING_DIRECTORIES``
stopping repository discovery at ``tmp_path``. ``PGAI_GIT_LOCAL_ONLY=1``
keeps any tag lookup on the host. The starting version comes from the
project's ``release-state.md`` ``## Last Released`` (the resolver's
no-config fallback); without that file it is the ``v0.0.0`` sentinel, so
the first candidate is ``v0.0.1``.

The subprocess environment is built from an explicit set
(``build_scrubbed_env``) with every kanban root name pointed into the
sandbox, so neither the operator's live install nor the invoking shell's
exports can reach the function (docs/test-authoring-guidelines.md,
"Live-install safety" and "Subprocess environments are built from an
explicit set, not inherited").
"""

from __future__ import annotations

import math
import os
import pathlib
import textwrap
from typing import Optional

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import BashResult, run_bash

# team/tests/unit/<this file> -> parents[2] is team/. Library paths are
# absolute because the subprocess runs from inside the sandbox, not team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"

_PROJECT = "marker-slot-demo"

# Marker filenames follow the production convention: .materialized.<sha256 hex>.
_HEX_DIGITS = 64

# Minimum byte length of the matching-path listing in the many-marker case.
# A Linux pipe buffers 64 KiB by default and one grep read takes at most
# what the pipe holds, so a listing four times that size cannot be absorbed
# before an early-exiting reader closes its end.
_LISTING_BYTES_FLOOR = 256 * 1024
_MANY_MARKERS_FLOOR = 300

# The listing-pipe shape of the marker condition, used only by the
# calibration test to prove the many-marker fixture defeats it.
_PIPED_LISTING_CHECK = (
    'grep -rlE "(^|[^.0-9])${candidate//./\\\\.}([^0-9]|$)" '
    '"${plans_dir}"/.materialized.* 2>/dev/null | grep -q .'
)


def _diagnostic(result: BashResult) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


class _Sandbox:
    """A throwaway kanban root holding one project and its plans directory."""

    def __init__(self, tmp_path: pathlib.Path, last_released: Optional[str] = None):
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban-root"
        self.project_root = self.root / "projects" / _PROJECT
        self.plans_dir = self.project_root / "tasks" / "queues" / "plans"
        self.workdir = self.root / "work"
        self.temp_dir = self.root / "tmp"
        self.home = self.root / "home"
        for path in (self.project_root, self.workdir, self.temp_dir, self.home):
            path.mkdir(parents=True, exist_ok=True)
        if last_released is not None:
            (self.project_root / "release-state.md").write_text(
                "# Release State\n\n"
                "## Active RC\nnone\n\n"
                "## RC Opened At\nnone\n\n"
                "## RC Opened By Task\nnone\n\n"
                f"## Last Released\n{last_released}\n",
                encoding="utf-8",
            )

    def make_plans_dir(self) -> pathlib.Path:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        return self.plans_dir

    def add_marker(self, index: int, version: str) -> pathlib.Path:
        """Write one marker whose contents name *version*.

        The filename is a fixed-width lowercase hex string, unique per
        *index*, so it can never contain the version itself.
        """
        plans_dir = self.make_plans_dir()
        marker = plans_dir / f".materialized.{index:0{_HEX_DIGITS}x}"
        marker.write_text(
            f"plan: bundle-{index}\nversion: {version}\n", encoding="utf-8"
        )
        return marker

    def many_marker_count(self) -> int:
        """How many markers make the matching-path listing exceed the floor."""
        one_path = len(f"{self.plans_dir}/.materialized.{0:0{_HEX_DIGITS}x}\n")
        return max(_MANY_MARKERS_FLOOR, math.ceil(_LISTING_BYTES_FLOOR / one_path))

    def env(self) -> dict[str, str]:
        """The complete subprocess environment, every kanban root sandboxed."""
        return build_scrubbed_env(
            home_dir=self.home,
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "PGAI_GIT_LOCAL_ONLY": "1",
                "GIT_CEILING_DIRECTORIES": str(self.tmp_path),
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        )

    def run(self, body: str) -> BashResult:
        """Run *body* under strict mode with the discovery libraries sourced."""
        script = textwrap.dedent(f"""\
            set -euo pipefail
            source '{_LIB_DIR}/project_paths.sh'
            source '{_LIB_DIR}/discovery.sh'
            """) + body
        return run_bash(
            self.tmp_path, script, base_env=self.env(), cwd=self.workdir
        )

    def compute_next_patch(self) -> BashResult:
        return self.run(f"discovery_compute_next_patch '{_PROJECT}'\n")


def _assert_slot(result: BashResult, expected: str, why: str) -> None:
    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.strip() == expected, (
        f"expected discovery_compute_next_patch to return {expected}: {why}\n"
        + _diagnostic(result)
    )


class TestMarkerNamingTheCandidateTakesTheSlot:
    def test_one_matching_marker_skips_the_candidate(self, tmp_path):
        sandbox = _Sandbox(tmp_path)
        sandbox.add_marker(0, "v0.0.1")

        _assert_slot(
            sandbox.compute_next_patch(),
            "v0.0.2",
            "a materialized marker naming v0.0.1 means v0.0.1 is taken",
        )

    def test_many_matching_markers_skip_the_candidate(self, tmp_path):
        sandbox = _Sandbox(tmp_path)
        count = sandbox.many_marker_count()
        for index in range(count):
            sandbox.add_marker(index, "v0.0.1")

        _assert_slot(
            sandbox.compute_next_patch(),
            "v0.0.2",
            f"{count} materialized markers name v0.0.1, so v0.0.1 is taken -- "
            "a v0.0.1 result means the marker check lost its match once many "
            "markers matched (a listing pipe under pipefail takes SIGPIPE)",
        )

    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root can read a mode-000 file, so no marker is unreadable"
    )
    def test_matching_marker_beside_an_unreadable_marker_skips_the_candidate(
        self, tmp_path
    ):
        sandbox = _Sandbox(tmp_path)
        sandbox.add_marker(0, "v0.0.1")
        unreadable = sandbox.add_marker(1, "v0.0.9")
        unreadable.chmod(0)
        try:
            result = sandbox.compute_next_patch()
        finally:
            unreadable.chmod(0o644)

        _assert_slot(
            result,
            "v0.0.2",
            "one readable marker names v0.0.1, so an unreadable sibling marker "
            "must not turn the taken slot into a free one",
        )


class TestCandidateNotNamedByAnyMarkerIsFree:
    def test_marker_naming_a_longer_version_with_the_same_digits_leaves_the_candidate_free(
        self, tmp_path
    ):
        sandbox = _Sandbox(tmp_path, last_released="v0.0.2")
        sandbox.add_marker(0, "v0.0.30")

        _assert_slot(
            sandbox.compute_next_patch(),
            "v0.0.3",
            "the only marker names v0.0.30, which must not count as v0.0.3",
        )

    def test_absent_plans_directory_leaves_the_first_candidate_free(self, tmp_path):
        sandbox = _Sandbox(tmp_path)
        assert not sandbox.plans_dir.exists()

        _assert_slot(
            sandbox.compute_next_patch(),
            "v0.0.1",
            "with no plans directory no marker can take a slot",
        )

    def test_empty_plans_directory_leaves_the_first_candidate_free(self, tmp_path):
        sandbox = _Sandbox(tmp_path)
        sandbox.make_plans_dir()

        _assert_slot(
            sandbox.compute_next_patch(),
            "v0.0.1",
            "an empty plans directory holds no marker to take a slot",
        )


class TestManyMarkerFixtureDefeatsAPipedListingCheck:
    def test_piped_listing_check_reports_the_taken_slot_as_free(self, tmp_path):
        # Calibration: the many-marker fixture must really force the losing
        # schedule for a listing piped into grep -q under pipefail, or the
        # many-marker test above could pass for the wrong reason.
        sandbox = _Sandbox(tmp_path)
        for index in range(sandbox.many_marker_count()):
            sandbox.add_marker(index, "v0.0.1")

        result = sandbox.run(textwrap.dedent(f"""\
            candidate=v0.0.1
            plans_dir='{sandbox.plans_dir}'
            if {_PIPED_LISTING_CHECK}; then
                echo taken
            else
                echo free
            fi
            """))

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "free", (
            "the many-marker fixture no longer makes a piped listing check "
            "lose its match under pipefail, so it cannot prove the marker "
            "condition is pipe-safe\n" + _diagnostic(result)
        )
