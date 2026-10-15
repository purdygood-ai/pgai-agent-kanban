"""
test_bundle_write_refuses_without_valid_items.py
=================================================
Unit coverage for _disc_write_bundle's own fail-closed guard in
team/scripts/lib/discovery_state.sh: the last-line-of-defence check that
runs before anything is written, independent of and behind
discovery_step_bugs / discovery_step_priority's own post-filter shape
guards (_disc_bundle_item_shape_ok in discovery_steps.sh, exercised by
test_discovery_narration_channel.py's
TestContaminatedPostFilterListRefusesTheWholeBundle).

_disc_write_bundle counts how many entries in its own <files> argument
resolve to an existing regular file. When that count is zero -- every
entry is a narration line, a relative path, a path to something that does
not exist, or any other non-file content -- it refuses to write anything
at all: no bundle file is created, the call returns non-zero, and the
refusal speaks the dual-channel error/message/remedy contract via
pgai_emit_error_contract (team/scripts/lib/error_contract.sh) rather than
a bare log line.

This closes a structural hole the two step-level guards do not: a future
caller of _disc_write_bundle that bypasses discovery_step_bugs and
discovery_step_priority entirely (a new step, a direct unit test, a
maintenance script) would otherwise still be able to render a header-only
bundle with an empty "## Bundled Items" section, which would queue PM on
nothing. Without this guard, that caller would proceed straight through
to the workflow-plugin lookup and the render loop below, actually writing
a bundle file whose "## Bundled Items" section is empty (every entry
skipped one at a time by the loop's own per-line WARNING) -- which is
exactly what these tests would observe against a library without the guard.

_disc_write_bundle is invoked directly here (no discovery pipeline, no
temporary kanban tree, no project registration) -- these tests exercise
the guard as a pure function over its own <files> argument, the same
isolation test_discovery_bundle_marker_ordering.py uses for
_disc_order_bundle_items in the same file. Every call stubs
wf_load_plugin/wf_bundle_source_branch (team/scripts/lib/workflow.sh's
public hooks) with a minimal stand-in so a run that reaches past the
guard under test does not additionally depend on real plugin-manifest
resolution -- that path is covered end-to-end by the integration suite
(test_discovery_pipeline.py and friends) instead.

Naming: test names describe the behaviour under test, not the ticket that
motivated them (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern
6).
"""

from __future__ import annotations

import pathlib
import textwrap

from tests.unit.shell_harness import run_bash

_DISCOVERY_STATE_LIB = "scripts/lib/discovery_state.sh"

# Minimal stand-in for team/scripts/lib/workflow.sh's public hooks, plus a
# stderr-only log() -- neither the workflow-plugin manifest lookup nor
# narration-channel contamination is what these tests are about (both are
# covered elsewhere: workflow.sh's real resolution by the integration
# suite, log()'s stdout-tap contamination by
# test_discovery_narration_channel.py). Declaring both up front keeps
# every test below isolated to the one guard under test, on either side of
# it: a refusal (guard fires, nothing downstream ever runs) or a real
# write (guard steps aside, downstream runs against a harmless stub).
_STUBS = textwrap.dedent("""\
    WF_LOAD_ERROR=""
    wf_load_plugin() { WF_LOAD_ERROR=""; return 0; }
    wf_bundle_source_branch() { echo "rc/test-stub"; }
    log() { echo "[test] $*" >&2; }
""")


def _run_write_bundle(
    tmp_path: pathlib.Path,
    *,
    bundle_path: pathlib.Path,
    files: str,
    target_version: str = "v1.2.3",
    workflow_type: str = "release",
    test_required: str = "true",
    summary: str = "Test bundle",
    kind: str = "BUG",
):
    """Invoke _disc_write_bundle directly and capture its exit code + output.

    Calls _disc_write_bundle with set -e suspended around the one call so
    a non-zero return does not abort the harness script before EXIT_CODE
    is captured -- the same idiom _disc_write_bundle's own wf_load_plugin
    probe uses internally.
    """
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {_STUBS}
        source {_DISCOVERY_STATE_LIB}

        set +e
        _disc_write_bundle "{bundle_path}" "{target_version}" "{workflow_type}" "{test_required}" "{summary}" "{files}" "{kind}"
        rc=$?
        set -e
        echo "EXIT_CODE=${{rc}}"
    """)
    result = run_bash(tmp_path, script)
    assert result.returncode == 0, (
        f"Harness script itself failed unexpectedly (not the guard under test).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def _exit_code(stdout: str) -> int:
    for line in stdout.splitlines():
        if line.startswith("EXIT_CODE="):
            return int(line.split("=", 1)[1])
    raise AssertionError(f"EXIT_CODE= not found in harness stdout:\n{stdout}")


class TestAllNarrationShapedLinesRefuseTheWrite:
    """Every entry in <files> is narration-shaped (not an existing path)."""

    def test_returns_nonzero_and_writes_no_file(self, tmp_path: pathlib.Path) -> None:
        bundle_path = tmp_path / "requirements" / "v1.2.3-bugfix-bundle-test.md"
        narration_lines = "\n".join(
            [
                "[2026-09-23T00:00:00+00:00] wake(test): piggybacking 3 deferred bugs (trigger: BUG-0001 medium)",
                "[2026-09-23T00:00:01+00:00] wake(test): bundle written, v1.2.3 ready for Step 3 next iteration",
            ]
        )

        result = _run_write_bundle(tmp_path, bundle_path=bundle_path, files=narration_lines)

        assert _exit_code(result.stdout) != 0, result.stdout
        assert not bundle_path.exists(), (
            "Expected no bundle file to be created when zero entries resolve "
            f"to an existing regular file; found: {list(bundle_path.parent.glob('*')) if bundle_path.parent.exists() else []}"
        )

    def test_refusal_output_carries_error_message_and_remedy(self, tmp_path: pathlib.Path) -> None:
        bundle_path = tmp_path / "requirements" / "v1.2.3-bugfix-bundle-test.md"
        narration_lines = "[2026-09-23T00:00:00+00:00] wake(test): a narration line, not a path"

        result = _run_write_bundle(tmp_path, bundle_path=bundle_path, files=narration_lines)

        assert _exit_code(result.stdout) != 0, result.stdout
        assert "error:" in result.stderr, result.stderr
        assert "message:" in result.stderr, result.stderr
        assert "remedy:" in result.stderr, result.stderr
        assert "discovery.bundle-write-no-valid-items" in result.stderr, result.stderr


class TestNonexistentPathsRefuseTheWrite:
    """Every entry in <files> is a well-formed absolute path, but none of
    them names a file that actually exists on disk -- the guard checks
    [[ -f "$path" ]], not merely "looks like a path".
    """

    def test_returns_nonzero_and_writes_no_file(self, tmp_path: pathlib.Path) -> None:
        bundle_path = tmp_path / "requirements" / "v1.2.3-bugfix-bundle-test.md"
        missing_paths = "\n".join(
            [
                str(tmp_path / "bugs" / "BUG-0001-does-not-exist.md"),
                str(tmp_path / "bugs" / "BUG-0002-also-missing.md"),
            ]
        )

        result = _run_write_bundle(tmp_path, bundle_path=bundle_path, files=missing_paths)

        assert _exit_code(result.stdout) != 0, result.stdout
        assert not bundle_path.exists()


class TestEmptyFilesArgumentRefusesTheWrite:
    """An empty <files> argument is the degenerate zero-valid-entries case."""

    def test_returns_nonzero_and_writes_no_file(self, tmp_path: pathlib.Path) -> None:
        bundle_path = tmp_path / "requirements" / "v1.2.3-bugfix-bundle-test.md"

        result = _run_write_bundle(tmp_path, bundle_path=bundle_path, files="")

        assert _exit_code(result.stdout) != 0, result.stdout
        assert not bundle_path.exists()


class TestAtLeastOneRealFileAvoidsTheGuard:
    """Positive control: the guard is specifically zero-valid-entries, not
    a blanket refusal -- one real file among narration-shaped noise must
    NOT trip it (the per-line skip further down _disc_write_bundle's own
    render loop is what handles the remaining bad lines, unchanged).
    """

    def test_guard_does_not_fire_when_one_entry_is_a_real_file(self, tmp_path: pathlib.Path) -> None:
        real_bug = tmp_path / "BUG-0001-real.md"
        real_bug.write_text("# BUG-0001: real\n\n## Status\nopen\n", encoding="utf-8")
        bundle_path = tmp_path / "requirements" / "v1.2.3-bugfix-bundle-test.md"
        files = "\n".join(
            [
                "[2026-09-23T00:00:00+00:00] wake(test): a narration line, not a path",
                str(real_bug),
            ]
        )

        result = _run_write_bundle(tmp_path, bundle_path=bundle_path, files=files)

        # The zero-valid-entries guard does not fire (one entry IS real):
        # _disc_write_bundle proceeds all the way through and writes a real
        # bundle, containing the one real file and omitting the narration
        # line (handled, unchanged, by the per-line skip further down the
        # same function's render loop).
        assert _exit_code(result.stdout) == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert bundle_path.exists(), (
            "Expected the guard to step aside and let a normal write proceed "
            "when at least one entry resolves to a real file."
        )
        bundle_text = bundle_path.read_text(encoding="utf-8")
        assert "BUG-0001-real.md" in bundle_text, bundle_text
        assert "a narration line, not a path" not in bundle_text, bundle_text
