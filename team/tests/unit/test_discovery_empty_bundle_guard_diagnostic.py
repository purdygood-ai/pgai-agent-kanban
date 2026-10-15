"""
test_discovery_empty_bundle_guard_diagnostic.py
================================================
Unit tests for the empty-bundle guard shared by discovery_step_bugs and
discovery_step_priority in team/scripts/lib/discovery_steps.sh
(_disc_scan_output_has_item_paths): when the item scan's captured stdout is
non-empty but holds no line starting with '/', the step skips the bundle
AND the log names every rejected line, quoted verbatim, so the occurrence
diagnoses itself instead of reading only "no valid item paths".

Two layers:
  1. The helper on its own -- a mixed stdout of narration lines is refused
     with each line quoted; a stdout holding one real path among narration
     is accepted silently.
  2. Both steps end to end -- the finder is replaced by a stub that returns
     narration only (the pp_* lookups stubbed to the fixture tree), and each
     step must return 1, name the rejected lines, and write no bundle.

Every test drives the real bash functions through the shared shell harness.

Naming: test names describe the behaviour under test, not the ticket that
motivated them (docs/test-authoring-guidelines.md, Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import textwrap

from tests.unit.shell_harness import run_bash

_STATE_LIB = "scripts/lib/discovery_state.sh"
_STEPS_LIB = "scripts/lib/discovery_steps.sh"

_PRELUDE = textwrap.dedent(f"""\
    set -uo pipefail
    source {_STATE_LIB}
    source {_STEPS_LIB}
    log() {{ printf '%s\\n' "$*" >&2; }}
    """)

_MIXED_NARRATION = (
    "[2026-01-01T00:00:00+00:00] discovery: settle guard active for BUG-0001-a.md\n"
    "WARNING: something upstream echoed this\n"
    "  Quarantine path: relative/looking/path.md"
)


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _call_helper(tmp_path: pathlib.Path, step_name: str, scan_output: str):
    scan_file = tmp_path / "scan-output.txt"
    scan_file.write_text(scan_output, encoding="utf-8")
    return run_bash(
        tmp_path,
        _PRELUDE
        + textwrap.dedent(f"""\
            _scan="$(cat '{scan_file}')"
            _disc_scan_output_has_item_paths '{step_name}' "$_scan"
            """),
    )


class TestHelperNamesEveryRejectedLine:
    def test_narration_only_stdout_is_refused_with_each_line_quoted(self, tmp_path):
        result = _call_helper(tmp_path, "discovery_step_bugs", _MIXED_NARRATION)

        assert result.returncode == 1, _diagnostic(result)
        assert result.stdout == "", _diagnostic(result)
        assert "discovery_step_bugs: no valid item paths after filtering" in result.stderr, (
            _diagnostic(result)
        )
        assert "3 non-path line(s)" in result.stderr, _diagnostic(result)
        for line in _MIXED_NARRATION.splitlines():
            assert f"rejected non-path line: '{line}'" in result.stderr, _diagnostic(result)

    def test_one_real_path_among_narration_is_accepted_silently(self, tmp_path):
        scan_output = "not a path\n/abs/BUG-0001-a.md\nalso not a path"

        result = _call_helper(tmp_path, "discovery_step_bugs", scan_output)

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout == "", _diagnostic(result)
        assert "rejected non-path line" not in result.stderr, _diagnostic(result)

    def test_step_name_prefixes_every_diagnostic_line(self, tmp_path):
        result = _call_helper(tmp_path, "discovery_step_priority", "stray line")

        assert result.returncode == 1, _diagnostic(result)
        assert "discovery_step_priority: rejected non-path line: 'stray line'" in result.stderr, (
            _diagnostic(result)
        )


def _step_script(root: pathlib.Path, step: str, scan_output_file: pathlib.Path) -> str:
    """Run *step* with the project lookups and the item scan stubbed: the
    scan returns the contents of *scan_output_file* verbatim, so the guard
    sees exactly a narration-only stdout."""
    return _PRELUDE + textwrap.dedent(f"""\
        pp_require_project_context() {{ printf '%s\\n' "$1"; }}
        pp_project_root() {{ printf '%s\\n' '{root}'; }}
        pp_bugs_dir() {{ printf '%s\\n' '{root}/bugs'; }}
        pp_priority_dir() {{ printf '%s\\n' '{root}/priority'; }}
        pp_queue_path() {{ printf '%s\\n' '{root}/tasks/queues/'"$2"'_backlog.md'; }}
        pp_requirements_dir() {{ printf '%s\\n' '{root}/requirements'; }}
        pp_max_priorities_per_run() {{ printf '1\\n'; }}
        _disc_check_bug_id_collisions() {{ :; }}
        _disc_find_unhandled_items() {{ cat '{scan_output_file}'; }}
        {step} project_a
        """)


def _fixture_tree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    root = tmp_path / "project_a"
    for sub in ("bugs", "priority", "requirements", "tasks/queues"):
        (root / sub).mkdir(parents=True)
    scan_output_file = tmp_path / "scan-output.txt"
    scan_output_file.write_text(_MIXED_NARRATION + "\n", encoding="utf-8")
    return root, scan_output_file


class TestBugStepGuardSkipsBundleAndNamesRejectedLines:
    def test_bug_step_returns_one_names_lines_and_writes_no_bundle(self, tmp_path):
        root, scan_output_file = _fixture_tree(tmp_path)

        result = run_bash(tmp_path, _step_script(root, "discovery_step_bugs", scan_output_file))

        assert result.returncode == 1, _diagnostic(result)
        assert "discovery_step_bugs: no valid item paths after filtering" in result.stderr, (
            _diagnostic(result)
        )
        for line in _MIXED_NARRATION.splitlines():
            assert f"rejected non-path line: '{line}'" in result.stderr, _diagnostic(result)
        assert list((root / "requirements").iterdir()) == [], _diagnostic(result)


class TestPriorityStepGuardSkipsBundleAndNamesRejectedLines:
    def test_priority_step_returns_one_names_lines_and_writes_no_bundle(self, tmp_path):
        root, scan_output_file = _fixture_tree(tmp_path)

        result = run_bash(
            tmp_path, _step_script(root, "discovery_step_priority", scan_output_file)
        )

        assert result.returncode == 1, _diagnostic(result)
        assert "discovery_step_priority: no valid item paths after filtering" in result.stderr, (
            _diagnostic(result)
        )
        for line in _MIXED_NARRATION.splitlines():
            assert f"rejected non-path line: '{line}'" in result.stderr, _diagnostic(result)
        assert list((root / "requirements").iterdir()) == [], _diagnostic(result)
