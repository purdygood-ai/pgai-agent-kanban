"""
test_discovery_item_scan_stdout_contract.py
============================================
Unit tests for the stdout contract of _disc_find_unhandled_items in
team/scripts/lib/discovery_items.sh: stdout carries ONLY absolute item
paths, one per line; every narration line -- filename rejections,
quarantine narration, settle-guard deferrals, and whatever log() any helper
inside the finder calls -- lands on stderr.

The contract matters because discovery_step_bugs / discovery_step_priority
capture the finder's stdout with $(...) and render it as bundle content: a
single stray line on stdout either corrupts a bundle or (via the steps'
own guard) skips one that should have been written.

Three angles are exercised, each through the real bash function via the
shared shell harness (no Python re-implementation of the scan):

  1. A fixture directory holding every condition the finder can narrate
     on -- a well-formed open item, one with no ## Status header, a done
     item, a running item, a settle-guarded item, a malformed filename that
     is quarantined on the spot, plus README.md and a non-.md file -- driven
     under a log() that TAPS STDOUT (tests/fixtures/log_stub.py's faithful
     stub). stdout must be exactly the two eligible paths; every narration
     line must be on stderr.
  2. The prevention itself: a python3 shim on PATH that writes a stray line
     before handing off to the real interpreter reproduces "something other
     than a path reached the scan's stdout". The finder must drop that
     line, narrate it verbatim on stderr, and still emit the genuine paths.
  3. The sibling scan the bug step runs beside the finder,
     _disc_check_bug_id_collisions, writes its collision WARNING to stderr
     and nothing to stdout.

Naming: test names describe the behaviour under test, not the ticket that
motivated them (docs/test-authoring-guidelines.md, Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import textwrap

from tests.fixtures.log_stub import make_log_stub_fragment
from tests.unit.shell_harness import run_bash

_TEMP_LIB = "scripts/lib/temp.sh"
_ITEMS_LIB = "scripts/lib/discovery_items.sh"


def _bug_file(bugs_dir: pathlib.Path, name: str, *, status: str | None = "open") -> pathlib.Path:
    """Write a minimal bug file; status=None omits the ## Status header."""
    bugs_dir.mkdir(parents=True, exist_ok=True)
    path = bugs_dir / name
    body = f"# {name}\n\n"
    if status is not None:
        body += f"## Status\n{status}\n\n"
    body += "## Summary\nTest bug file.\n"
    path.write_text(body, encoding="utf-8")
    return path


def _arm_settle_guard(state_dir: pathlib.Path, item: pathlib.Path) -> None:
    """Write a fresh settle marker for *item*, the shape
    _disc_write_settle_marker produces: epoch on line 1, path on line 2."""
    import time

    settle_dir = state_dir / "settle"
    settle_dir.mkdir(parents=True, exist_ok=True)
    (settle_dir / f"{item.name}.settle").write_text(
        f"{int(time.time())}\n{item}\n", encoding="utf-8"
    )


def _finder_script(
    log_fragment: str,
    bugs_dir: pathlib.Path,
    cache_file: pathlib.Path,
    state_dir: pathlib.Path,
) -> str:
    """Source the finder's libraries, install *log_fragment* as log(), and
    invoke the finder exactly as discovery_step_bugs does (state_dir set,
    project name empty so quarantine uses the <dir>/.rejected fallback)."""
    return textwrap.dedent(f"""\
        set -uo pipefail
        source {_TEMP_LIB}
        source {_ITEMS_LIB}
        """) + log_fragment + textwrap.dedent(f"""
        _disc_find_unhandled_items '{bugs_dir}' '{cache_file}' 'BUG-' '{state_dir}' ''
        """)


def _stdout_lines(result) -> list[str]:
    return [line for line in result.stdout.splitlines() if line != ""]


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


class TestStdoutCarriesOnlyAbsolutePaths:
    """Every narration condition the finder can hit, under a stdout-tapping
    log(): stdout is still exactly the eligible paths."""

    def test_every_narration_condition_lands_on_stderr_not_stdout(self, tmp_path):
        bugs_dir = tmp_path / "bugs"
        state_dir = tmp_path / ".discovery-state"
        cache_file = tmp_path / "bug_backlog.md"
        log_file = tmp_path / "stub.log"

        open_a = _bug_file(bugs_dir, "BUG-0001-open-a.md", status="open")
        open_b = _bug_file(bugs_dir, "BUG-0002-open-b.md", status=None)
        _bug_file(bugs_dir, "BUG-0003-done.md", status="done")
        running = _bug_file(bugs_dir, "BUG-0004-running.md", status="running")
        # A running item is in flight while a live bundle lists it; one no
        # bundle lists is orphaned and re-offered.
        (tmp_path / "requirements").mkdir()
        (tmp_path / "requirements" / "v0.0.1-bugfix-bundle-20260101.md").write_text(
            f"# bundle\n\n## Status\nrunning\n\n## Bundled Items\n\n- {running.name} (`{running}`)\n",
            encoding="utf-8",
        )
        settling = _bug_file(bugs_dir, "BUG-0005-settling.md", status="open")
        _arm_settle_guard(state_dir, settling)
        _bug_file(bugs_dir, "bug-6-malformed.md", status="open")
        (bugs_dir / "README.md").write_text("# not an item\n", encoding="utf-8")
        (bugs_dir / "notes.txt").write_text("not markdown\n", encoding="utf-8")
        # The cache marks the first open item as already bundled; the
        # ## Status header is the authority and says open, so it must still
        # be emitted.
        cache_file.write_text("- [x] BUG-0001-open-a\n", encoding="utf-8")

        result = run_bash(
            tmp_path,
            _finder_script(make_log_stub_fragment(log_file), bugs_dir, cache_file, state_dir),
            extra_env={
                # Quarantine the malformed name on its first rejection so the
                # multi-line quarantine narration fires in this run.
                "PGAI_DISCOVERY_REJECT_THRESHOLD": "1",
                # Keep the settle window open for the whole test.
                "PGAI_DISCOVERY_SETTLE_SECONDS": "3600",
            },
        )

        assert result.returncode == 0, _diagnostic(result)
        expected = sorted(str(p.resolve()) for p in (open_a, open_b))
        assert sorted(_stdout_lines(result)) == expected, _diagnostic(result)
        for line in _stdout_lines(result):
            assert line.startswith("/"), _diagnostic(result)

        # Each narration condition fired, and on stderr.
        assert "ERROR: rejecting bug-6-malformed.md" in result.stderr, _diagnostic(result)
        assert "quarantined after 1 failed parse(s)" in result.stderr, _diagnostic(result)
        assert "Quarantine path:" in result.stderr, _diagnostic(result)
        assert "settle guard active for BUG-0005-settling.md" in result.stderr, _diagnostic(result)
        assert "terminal state 'done'" in result.stderr, _diagnostic(result)
        # The stub really did tap stdout (it wrote its file), yet none of it
        # reached the finder's stdout.
        assert "[log-stub]" in log_file.read_text(encoding="utf-8")
        assert "[log-stub]" not in result.stdout, _diagnostic(result)
        assert "[log-stub]" in result.stderr, _diagnostic(result)
        # The quarantine move happened (side effects are intact under the
        # stdout rebinding).
        assert (bugs_dir / ".rejected" / "bug-6-malformed.md").is_file(), _diagnostic(result)
        assert not (bugs_dir / "bug-6-malformed.md").exists()

    def test_empty_directory_emits_nothing_on_either_channel(self, tmp_path):
        bugs_dir = tmp_path / "bugs"
        bugs_dir.mkdir()
        state_dir = tmp_path / ".discovery-state"
        cache_file = tmp_path / "bug_backlog.md"

        result = run_bash(
            tmp_path,
            _finder_script(
                'log() { printf "%s\\n" "$*" >&2; }\n', bugs_dir, cache_file, state_dir
            ),
        )

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout == "", _diagnostic(result)


class TestStrayScanLineIsDroppedAndNarrated:
    """A line that is not an absolute path reaching the scan's stdout is
    dropped, narrated verbatim on stderr, and never emitted."""

    def test_python_shim_stray_line_never_reaches_stdout(self, tmp_path):
        real_python = shutil.which("python3")
        assert real_python, "python3 must be on PATH for the finder itself to run"

        shim_dir = tmp_path / "shim-bin"
        shim_dir.mkdir()
        shim = shim_dir / "python3"
        shim.write_text(
            textwrap.dedent(f"""\
                #!/usr/bin/env bash
                # Reproduces "something other than a path reached the scan's
                # stdout": one stray line, then the real interpreter.
                printf '%s\\n' 'stray narration line'
                exec '{real_python}' "$@"
                """),
            encoding="utf-8",
        )
        shim.chmod(0o755)

        bugs_dir = tmp_path / "bugs"
        state_dir = tmp_path / ".discovery-state"
        cache_file = tmp_path / "bug_backlog.md"
        open_a = _bug_file(bugs_dir, "BUG-0001-open-a.md", status="open")
        open_b = _bug_file(bugs_dir, "BUG-0002-open-b.md", status="open")

        result = run_bash(
            tmp_path,
            _finder_script(
                'log() { printf "%s\\n" "$*" >&2; }\n', bugs_dir, cache_file, state_dir
            ),
            extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
        )

        assert result.returncode == 0, _diagnostic(result)
        expected = sorted(str(p.resolve()) for p in (open_a, open_b))
        assert sorted(_stdout_lines(result)) == expected, _diagnostic(result)
        assert "stray narration line" not in result.stdout, _diagnostic(result)
        assert "dropping non-path line" in result.stderr, _diagnostic(result)
        assert "'stray narration line'" in result.stderr, _diagnostic(result)


class TestBugIdCollisionScanNarratesOnStderrOnly:
    """_disc_check_bug_id_collisions, run by the bug step beside the finder,
    reports a duplicate numeric prefix on stderr and writes nothing to
    stdout."""

    def test_duplicate_prefix_warning_goes_to_stderr(self, tmp_path):
        bugs_dir = tmp_path / "bugs"
        _bug_file(bugs_dir, "BUG-0007-first.md")
        _bug_file(bugs_dir, "BUG-0007-second.md")

        result = run_bash(
            tmp_path,
            textwrap.dedent(f"""\
                set -uo pipefail
                source {_TEMP_LIB}
                source {_ITEMS_LIB}
                log() {{ printf "%s\\n" "$*" >&2; }}
                _disc_check_bug_id_collisions '{bugs_dir}'
                """),
        )

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout == "", _diagnostic(result)
        assert "WARNING" in result.stderr, _diagnostic(result)
        assert "0007" in result.stderr, _diagnostic(result)
