"""
test_discovery_mint_governor_predicate.py
==========================================
Unit tests for _disc_pending_bundle_of_type in
team/scripts/lib/discovery_steps.sh: the shared predicate the mint governor
in discovery_step_bugs / discovery_step_priority calls before minting a new
bundle, to detect a same-type bundle already pending in requirements/.

_disc_pending_bundle_of_type is a pure function over a directory and a type
slug ("bugfix" or "priority") -- it globs for
"*-<type_slug>-bundle-*.md", reads each candidate's own "## Status" header
via _disc_read_bundle_field (discovery_state.sh), and echoes the first one
that is not "done". These tests invoke it directly via the bash harness (no
discovery pipeline, no project registration, no bug/priority intake), which
isolates the predicate from discovery_step_bugs / discovery_step_priority's
own guard ordering and item-marking concerns -- that end-to-end behavior
(the governor actually declining a mint, narrating the pending bundle's
path, and leaving the incoming item and the pending bundle untouched) is
covered by team/tests/integration/test_discovery_pipeline.py's
TestMintGovernorDeclinesForPendingBundleOfSameType and
TestMintGovernorLeavesASingleBugAcrossPasses.

Naming: test names describe the behaviour under test, not the ticket that
motivated them (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

_STATE_LIB = "scripts/lib/discovery_state.sh"
_STEPS_LIB = "scripts/lib/discovery_steps.sh"


def _bundle(
    req_dir: pathlib.Path,
    name: str,
    *,
    status: str | None = "open",
) -> pathlib.Path:
    """Write a minimal requirements bundle file with (or without) a ## Status field."""
    req_dir.mkdir(parents=True, exist_ok=True)
    path = req_dir / name
    body = f"# {name}\n\n"
    if status is not None:
        body += f"## Status\n{status}\n\n"
    body += "## Summary\nTest bundle.\n"
    path.write_text(body, encoding="utf-8")
    return path


def _query(tmp_path: pathlib.Path, req_dir: pathlib.Path, type_slug: str):
    result = run_bash(
        tmp_path,
        f"source {_STATE_LIB} && source {_STEPS_LIB} && "
        f"_disc_pending_bundle_of_type '{req_dir}' '{type_slug}'",
    )
    return result


class TestNoCandidatesRefusesQuietly:
    """An empty (or nonexistent) requirements directory has nothing pending."""

    def test_returns_nonzero_and_no_stdout_when_directory_is_empty(self, tmp_path):
        req_dir = tmp_path / "requirements"
        req_dir.mkdir()

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode != 0, result.stdout
        assert result.stdout.strip() == "", result.stdout

    def test_returns_nonzero_when_directory_does_not_exist(self, tmp_path):
        req_dir = tmp_path / "requirements-does-not-exist"

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode != 0, result.stdout
        assert result.stdout.strip() == "", result.stdout


class TestOpenOrRunningBundleIsPending:
    """A bundle whose ## Status is anything but 'done' holds the slot."""

    def test_open_status_is_reported_pending(self, tmp_path):
        req_dir = tmp_path / "requirements"
        bundle = _bundle(req_dir, "v1.2.3-bugfix-bundle-20260923.md", status="open")

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode == 0, result.stdout
        assert result.stdout.strip() == str(bundle), result.stdout

    def test_running_status_is_reported_pending(self, tmp_path):
        req_dir = tmp_path / "requirements"
        bundle = _bundle(req_dir, "v1.2.3-bugfix-bundle-20260923.md", status="running")

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode == 0, result.stdout
        assert result.stdout.strip() == str(bundle), result.stdout

    def test_missing_status_field_defaults_to_pending(self, tmp_path):
        """A bundle with no ## Status header reads as 'open' (pending) --
        the same default _disc_read_bundle_field's other callers apply.
        """
        req_dir = tmp_path / "requirements"
        bundle = _bundle(req_dir, "v1.2.3-bugfix-bundle-20260923.md", status=None)

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode == 0, result.stdout
        assert result.stdout.strip() == str(bundle), result.stdout


class TestDoneBundleIsNotPending:
    """A bundle whose ## Status has reached a terminal value ('done' or
    'wont-do') no longer holds the slot -- both are the same two-value
    terminal set discovery_items.sh's is_terminal() and
    discovery_state.sh's eligibility scan already treat as definitively
    resolved.
    """

    def test_done_status_is_not_reported(self, tmp_path):
        req_dir = tmp_path / "requirements"
        _bundle(req_dir, "v1.2.3-bugfix-bundle-20260923.md", status="done")

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode != 0, result.stdout
        assert result.stdout.strip() == "", result.stdout

    def test_wont_do_status_is_not_reported(self, tmp_path):
        """A bundle the operator explicitly abandoned (wont-do) no longer
        holds its slot any more than a shipped ('done') one does -- both
        are terminal, and a discovery pass must be free to mint a new
        bundle of the same type once the only prior one has reached
        either terminal value.
        """
        req_dir = tmp_path / "requirements"
        _bundle(req_dir, "v1.2.3-bugfix-bundle-20260923.md", status="wont-do")

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode != 0, result.stdout
        assert result.stdout.strip() == "", result.stdout

    def test_done_bundle_alongside_a_pending_one_reports_only_the_pending_one(
        self, tmp_path
    ):
        req_dir = tmp_path / "requirements"
        _bundle(req_dir, "v1.2.1-bugfix-bundle-20260901.md", status="done")
        pending = _bundle(req_dir, "v1.2.3-bugfix-bundle-20260923.md", status="open")

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode == 0, result.stdout
        assert result.stdout.strip() == str(pending), result.stdout


class TestTypeSlugIsolatesCategories:
    """A pending bundle of one category never blocks the other."""

    def test_pending_priority_bundle_does_not_block_a_bugfix_query(self, tmp_path):
        req_dir = tmp_path / "requirements"
        _bundle(req_dir, "v1.2.3-priority-bundle-20260923.md", status="open")

        result = _query(tmp_path, req_dir, "bugfix")

        assert result.returncode != 0, result.stdout
        assert result.stdout.strip() == "", result.stdout

    def test_pending_bugfix_bundle_does_not_block_a_priority_query(self, tmp_path):
        req_dir = tmp_path / "requirements"
        _bundle(req_dir, "v1.2.3-bugfix-bundle-20260923.md", status="open")

        result = _query(tmp_path, req_dir, "priority")

        assert result.returncode != 0, result.stdout
        assert result.stdout.strip() == "", result.stdout
