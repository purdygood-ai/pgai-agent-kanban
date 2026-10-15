"""
test_cm_release_hook_failure_remedy.py
=======================================
Retrofit: a blocking release-lifecycle hook that exits
non-zero (the `_run_release_hook` failure path in
team/scripts/lib/cm/release_finalize.sh — see "WHY EXTRACTION" below) is
classified non-deterministic (exempt) — the hook is project-supplied, so its failure cause is not knowable from the
caller.  Per docs/coding-standards.md "The non-deterministic exemption", the
failure is exempt from a numbered fix-command block but must still name the
next diagnostic step and the rule the gate enforces.

WHY EXTRACTION, NOT REPLICATION
--------------------------------
`_run_release_hook` is a private helper defined at file scope in
release_finalize.sh (moved there from release.sh by the v2.9.20
engine-decomposition, since every one of its call sites is one of the
hook-step callbacks release.sh threads through by name — see that file's
"Why two callbacks"). It is not exposed as part of the lib's public API, so
it cannot be `source`d and called directly the way cm_release_finalize can.
Rather than hand-retype its body into the test (risking silent drift from
the real script), this file extracts the function verbatim from the live
release_finalize.sh text at test time and sources the extracted text into a
bash harness. The test therefore exercises the production function body,
not a copy of it.
"""

from __future__ import annotations

import pathlib
import stat

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_RELEASE_FINALIZE_SH = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_finalize.sh"
_LIB_ERROR_CONTRACT = _TEAM_DIR / "scripts" / "lib" / "error_contract.sh"

_FUNCTION_NAME = "_run_release_hook"


def _extract_function(source_path: pathlib.Path, function_name: str) -> str:
    """Extract a top-level bash function definition verbatim.

    Scans for a line exactly matching "<function_name>() {" and returns
    every line up to and including the next line that is exactly "}"
    (the closing brace of a function defined at column 0 — the convention
    release_finalize.sh's file-scope helpers follow). Raises AssertionError
    if the function or its closing brace cannot be found, so a rename or
    reformat upstream fails this test loudly rather than silently testing
    nothing.
    """
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = f"{function_name}() {{"
    start = next((i for i, l in enumerate(lines) if l == header), None)
    assert start is not None, (
        f"Could not find '{header}' in {source_path}. "
        "The function may have been renamed or reformatted."
    )
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), None)
    assert end is not None, (
        f"Could not find the closing brace for {function_name}() in {source_path}."
    )
    return "\n".join(lines[start : end + 1])


def _write_hook(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_hook_call(
    tmp_path: pathlib.Path,
    hook_path: pathlib.Path,
    *,
    no_block: bool = False,
) -> object:
    """Source the real _run_release_hook() and invoke it against hook_path."""
    fn_body = _extract_function(_RELEASE_FINALIZE_SH, _FUNCTION_NAME)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    no_block_arg = " '--no-block'" if no_block else ""
    script = f"""\
set -uo pipefail
REPO_ROOT={str(repo_root)!r}
ACTIVE_RC='v9.9.9'
PROJECT_NAME='remedy-test-proj'
_CM_PROJECT_ROOT={str(tmp_path / "kanban_project")!r}
RC_BRANCH='rc/v9.9.9'
KANBAN_ROOT={str(tmp_path / "kanban")!r}
source {str(_LIB_ERROR_CONTRACT)!r}

{fn_body}

_run_release_hook 'cm-release-pre-tag' {str(hook_path)!r}{no_block_arg}
"""
    return run_bash(tmp_path, script)


def test_failing_hook_names_cause_with_path_and_rc(tmp_path: pathlib.Path) -> None:
    """Failure output names the hook path and its exit code as the Cause."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\necho 'hook stdout line' \nexit 7\n")

    result = _run_hook_call(tmp_path, hook)
    assert result.returncode == 1
    assert "Cause(s):" in result.stderr
    assert str(hook) in result.stderr
    assert "rc=7" in result.stderr


def test_failing_hook_names_next_diagnostic_step_not_a_command_block(
    tmp_path: pathlib.Path,
) -> None:
    """Non-deterministic exemption: no numbered 'To fix:' command block, but a
    'Next diagnostic step' pointer naming the printed [hook ...] lines."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 3\n")

    result = _run_hook_call(tmp_path, hook)
    assert "Next diagnostic step" in result.stderr
    assert "[hook cm-release-pre-tag]" in result.stderr
    # Exempt from the numbered fix-command block (this is NOT a full-remedy gate).
    assert "To fix:" not in result.stderr


def test_failing_hook_prints_rule_line(tmp_path: pathlib.Path) -> None:
    """Failure output states the one-line invariant the gate enforces."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 1\n")

    result = _run_hook_call(tmp_path, hook)
    assert "Rule:" in result.stderr
    assert "must exit 0" in result.stderr


def test_failing_hook_emits_dual_channel_error_contract(tmp_path: pathlib.Path) -> None:
    """The blocking failure path also emits the house-shape error/message/remedy
    triple via team/scripts/lib/error_contract.sh,
    in addition to the pre-existing Cause(s)/Next-diagnostic-step/Rule block —
    a forced hook failure must speak both channels."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 9\n")

    result = _run_hook_call(tmp_path, hook)
    assert result.returncode == 1
    assert "error:   cm-release.hook-failure.cm-release-pre-tag" in result.stderr
    assert "message: " in result.stderr
    assert "remedy:  " in result.stderr
    # Message names the exit code and phase in a stranger's words, not a bare id.
    assert "rc=9" in result.stderr


def test_no_block_failure_does_not_print_remedy_and_continues(
    tmp_path: pathlib.Path,
) -> None:
    """--no-block failures (post-tag) are a warning only; the new remedy
    block is specific to the blocking path and must not fire here."""
    hook = tmp_path / "hooks" / "cm-release-post-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 1\n")

    result = _run_hook_call(tmp_path, hook, no_block=True)
    assert result.returncode == 0
    assert "Cause(s):" not in result.stderr
    assert "WARNING: hook" in result.stderr


def test_passing_hook_reports_success(tmp_path: pathlib.Path) -> None:
    """A hook that exits 0 reports success and prints no remedy block."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 0\n")

    result = _run_hook_call(tmp_path, hook)
    assert result.returncode == 0
    assert "completed successfully" in result.stdout
    assert "Cause(s):" not in result.stdout + result.stderr
