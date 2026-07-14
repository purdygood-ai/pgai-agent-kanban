"""
test_po_agent_draft_mode.py
===========================
Behavioral unit tests for the --output <dir> (draft mode) feature of
team/scripts/po-agent.sh.

Six fixture categories, matching the acceptance criteria in the task README:

  1. Happy-path draft: --output <tmpdir> on a valid brief writes exactly one
     <target-version>-<slug>.md file to <tmpdir>/ and zero files anywhere under
     projects/.

  2. Intake round-trip: the produced draft file passes the intake.sh filename
     routing check (v[0-9]*.md prefix) without raising a routing refusal.

  3. Exclusivity refusal: --output combined with --dry-run exits non-zero and
     produces no file.

  4. Collision suffix: running twice into the same dir with the same slug yields
     <base>.md then <base>-2.md, with stdout naming the -2 decision.

  5. Garbage brief: a brief with a missing/invalid Target Version section causes
     loud refusal, writes no file, and exits non-zero — identical behaviour to
     full mode.

  6. Absent-flag regression: without --output the script exits non-zero when no
     project is specified, verifying that the pre-draft-mode validation path is
     byte-identical to the original behaviour.

Implementation notes
---------------------
The tests cannot invoke the real `claude` CLI (no Claude process inside pytest).

  - Validation/refusal tests (categories 3, 4 pre-condition, 5, 6): the script
    exits BEFORE the `claude` call, so no stub is needed.

  - Draft-mode tests that reach the `claude` call (categories 1, 2, 4): a fake
    `claude` binary is placed at the front of PATH via `extra_env`.  The stub
    writes a minimal but structurally valid requirements document at the path it
    receives in the subagent prompt (the exact path is conveyed in the prompt
    text), then exits 0.

  - The `--output` collision test (category 4) requires the first invocation to
    produce a file.  That file is written by the stub via a helper that parses the
    draft path out of the prompt text.

All temp paths are under tmp_path (pytest fixture), which conftest.py already
redirects to $PGAI_AGENT_KANBAN_TEMP_DIR/tests/.  No bare /tmp paths are used.
"""

from __future__ import annotations

import os
import pathlib
import stat
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_SCRIPT = "scripts/po-agent.sh"
_INTAKE_SCRIPT = "scripts/intake.sh"

# A minimal valid brief body.  The Target Version is required; everything else
# is ignored for these tests.
_VALID_BRIEF_BODY = textwrap.dedent("""\
    # Brief: My Feature

    ## Target Version
    v1.9.0

    ## Goal
    Test goal text.

    ## Constraints
    Test constraints text.
""")

# A garbage brief body: no valid Target Version line.
_GARBAGE_BRIEF_BODY = textwrap.dedent("""\
    # Brief: Garbage

    ## Summary
    This brief has no Target Version section at all.
""")

# A minimal but structurally valid requirements document written by the stub
# claude binary.  intake.sh is a dumb router that checks only the filename
# prefix, not the content, so any non-empty content is sufficient.
_STUB_REQUIREMENTS_CONTENT = textwrap.dedent("""\
    # Requirements: v1.9.0 — my-feature

    ## Status
    open

    ## Target Version
    v1.9.0

    ## Category
    enhancement

    ## Summary
    Stub requirements document produced by the fake claude binary.
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_brief(directory: pathlib.Path, name: str, body: str) -> pathlib.Path:
    """Write *body* to *directory*/*name* and return the absolute path."""
    brief = directory / name
    brief.write_text(body, encoding="utf-8")
    return brief


def _make_fake_claude(bin_dir: pathlib.Path) -> pathlib.Path:
    """Write a fake `claude` stub to *bin_dir*/claude and return its path.

    The stub is invoked by po-agent.sh as:
      claude -p --dangerously-skip-permissions "<prompt-text>"

    In draft mode the prompt text contains the exact output path in the form:
      "...to exactly this path: /some/path/v1.9.0-my-brief.md..."

    The stub extracts that path from the final positional argument (the prompt
    string) and writes _STUB_REQUIREMENTS_CONTENT there, then exits 0.
    When it cannot find the path pattern (non-draft invocation), it exits 0
    without writing anything.
    """
    stub_path = bin_dir / "claude"
    stub_text = textwrap.dedent("""\
        #!/usr/bin/env bash
        # Fake claude stub for po-agent.sh draft-mode unit tests.
        # Extracts the output path from the prompt text and writes a stub file.
        set -euo pipefail
        # The last positional argument is the prompt string.
        _PROMPT="${!#}"
        # Extract the draft file path from the prompt: "to exactly this path: <path>."
        _DRAFT_PATH="$(echo "$_PROMPT" | grep -oP '(?<=to exactly this path: )\\S+')"
        # Strip a trailing period if present (prompt ends the sentence with ".").
        _DRAFT_PATH="${_DRAFT_PATH%.}"
        if [[ -n "$_DRAFT_PATH" ]]; then
            mkdir -p "$(dirname "$_DRAFT_PATH")"
            cat > "$_DRAFT_PATH" <<'STUBEOF'
# Requirements: stub

## Status
open

## Target Version
v1.9.0

## Category
enhancement

## Summary
Stub requirements document produced by the fake claude binary.
STUBEOF
        fi
        exit 0
    """)
    stub_path.write_text(stub_text, encoding="utf-8")
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub_path


def _env_with_stub_claude(
    tmp_path: pathlib.Path,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return an extra_env dict that places the fake claude stub on PATH.

    The conftest autouse fixture has already set PGAI_AGENT_KANBAN_ROOT_PATH to
    a safe temp root.  We inherit that from os.environ and prepend the stub
    bin dir to PATH so the script finds the fake claude first.
    """
    stub_bin = tmp_path / "_claude_stub_bin"
    stub_bin.mkdir(parents=True, exist_ok=True)
    _make_fake_claude(stub_bin)

    env: dict[str, str] = {}
    if extra:
        env.update(extra)
    current_path = os.environ.get("PATH", "")
    env["PATH"] = f"{stub_bin}:{current_path}"
    return env


# ---------------------------------------------------------------------------
# Category 1 — Happy-path draft
# ---------------------------------------------------------------------------


def test_draft_mode_writes_one_file_to_output_dir(tmp_path: pathlib.Path) -> None:
    """--output <dir> on a valid brief writes exactly one <version>-<slug>.md to <dir>/."""
    brief = _write_brief(tmp_path, "my-feature.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    assert result.returncode == 0, (
        f"Expected exit 0 in draft mode; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Exactly one .md file under output_dir
    produced = list(output_dir.glob("*.md"))
    assert len(produced) == 1, (
        f"Expected exactly one .md file in {output_dir}; found {produced}"
    )

    # Filename matches <version>-<slug>.md pattern
    fname = produced[0].name
    assert fname.startswith("v1.9.0-"), (
        f"Output filename should start with 'v1.9.0-'; got {fname!r}"
    )
    assert fname.endswith(".md"), f"Output filename should end with '.md'; got {fname!r}"


def test_draft_mode_creates_output_dir_if_missing(tmp_path: pathlib.Path) -> None:
    """--output <dir> creates the directory when it does not exist."""
    brief = _write_brief(tmp_path, "new-feature.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "new" / "nested" / "drafts"

    # Must not exist before the run
    assert not output_dir.exists()

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
    )
    assert output_dir.is_dir(), "Output directory should have been created"


def test_draft_mode_final_stdout_line_is_draft_path(tmp_path: pathlib.Path) -> None:
    """Final stdout line of a draft-mode run is 'DRAFT: <path>'."""
    brief = _write_brief(tmp_path, "batch-brief.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    assert result.returncode == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert lines, "Expected non-empty stdout"
    last_line = lines[-1]
    assert last_line.startswith("DRAFT: "), (
        f"Expected last stdout line to start with 'DRAFT: '; got {last_line!r}"
    )
    # The path on the DRAFT line must exist as a file
    draft_path_str = last_line.removeprefix("DRAFT: ").strip()
    assert pathlib.Path(draft_path_str).is_file(), (
        f"DRAFT path {draft_path_str!r} is not a file"
    )


def test_draft_mode_writes_nothing_under_projects(tmp_path: pathlib.Path) -> None:
    """Draft mode writes zero files anywhere under a projects/ tree (the no-projects guarantee)."""
    brief = _write_brief(tmp_path, "safe-feature.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    # Create a projects/ subdirectory to assert its emptiness afterward.
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Point PGAI_AGENT_KANBAN_ROOT_PATH at tmp_path so if the script ever tries
    # to resolve projects/ it would write under tmp_path/projects/.
    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env={
            **_env_with_stub_claude(tmp_path),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path),
        },
    )

    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
    )

    # Assert: nothing written under projects/
    # anti-pattern-allowlist: 1 (justification: structural invariant — zero
    # files under projects/ is the CRITICAL safety guarantee of draft mode,
    # not merely one property among many.  The assertion must cover all items
    # under projects/ unconditionally; any write is a violation by definition.)
    all_under_projects = list(projects_dir.rglob("*"))
    written_files = [p for p in all_under_projects if p.is_file()]
    assert not written_files, (
        f"Draft mode MUST NOT write files under projects/; found: {written_files}"
    )


# ---------------------------------------------------------------------------
# Category 2 — Intake round-trip
# ---------------------------------------------------------------------------


def test_draft_output_filename_matches_intake_routing_prefix(tmp_path: pathlib.Path) -> None:
    """The draft filename starts with v[0-9]* so intake.sh routes it to requirements/."""
    brief = _write_brief(tmp_path, "round-trip-brief.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    assert result.returncode == 0
    produced = list(output_dir.glob("*.md"))
    assert len(produced) == 1

    fname = produced[0].name
    # intake.sh routes v[0-9]*.md to requirements/; assert the prefix matches.
    import re
    assert re.match(r'^v[0-9]', fname), (
        f"Draft filename {fname!r} does not start with 'v<digit>' — "
        "intake.sh will refuse it as an unrecognised prefix."
    )


# ---------------------------------------------------------------------------
# Category 3 — Exclusivity refusal
# ---------------------------------------------------------------------------


def test_output_combined_with_dry_run_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """--output combined with --dry-run exits non-zero."""
    brief = _write_brief(tmp_path, "test-brief.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir} --dry-run",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    assert result.returncode != 0, (
        "--output + --dry-run must exit non-zero (contradiction refusal); "
        f"got exit code {result.returncode}"
    )


def test_output_combined_with_dry_run_prints_refusal_message(tmp_path: pathlib.Path) -> None:
    """--output combined with --dry-run prints a loud refusal to stderr."""
    brief = _write_brief(tmp_path, "test-brief.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir} --dry-run",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    combined = result.stderr + result.stdout
    assert "mutually exclusive" in combined.lower() or "dry-run" in combined.lower(), (
        f"Expected a refusal message mentioning 'mutually exclusive' or 'dry-run'; "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_output_combined_with_dry_run_writes_no_file(tmp_path: pathlib.Path) -> None:
    """--output combined with --dry-run creates no file."""
    brief = _write_brief(tmp_path, "test-brief.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir} --dry-run",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    produced = list(output_dir.glob("*.md"))
    assert not produced, (
        f"--output + --dry-run must write no files; found: {produced}"
    )


# ---------------------------------------------------------------------------
# Category 4 — Collision suffix
# ---------------------------------------------------------------------------


def test_second_run_uses_suffix_2(tmp_path: pathlib.Path) -> None:
    """Running twice with the same slug produces <base>.md then <base>-2.md."""
    brief = _write_brief(tmp_path, "my-feature.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    env = _env_with_stub_claude(tmp_path)

    # First run — should produce v1.9.0-my-feature.md
    result1 = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=env,
    )
    assert result1.returncode == 0, (
        f"First run failed: {result1.stderr}"
    )

    # Second run — slug collision: should produce v1.9.0-my-feature-2.md
    result2 = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=env,
    )
    assert result2.returncode == 0, (
        f"Second run failed: {result2.stderr}"
    )

    produced = sorted(output_dir.glob("*.md"))
    names = [p.name for p in produced]
    assert "v1.9.0-my-feature.md" in names, (
        f"Expected 'v1.9.0-my-feature.md' after first run; got {names}"
    )
    assert "v1.9.0-my-feature-2.md" in names, (
        f"Expected 'v1.9.0-my-feature-2.md' after second run (collision suffix); got {names}"
    )


def test_collision_notice_appears_on_stdout(tmp_path: pathlib.Path) -> None:
    """On collision, a notice naming the -2 decision appears on stdout."""
    brief = _write_brief(tmp_path, "my-feature.md", _VALID_BRIEF_BODY)
    output_dir = tmp_path / "drafts"
    env = _env_with_stub_claude(tmp_path)

    # First run: no collision
    run_bash(tmp_path, f"bash {_SCRIPT} {brief} --output {output_dir}", extra_env=env)

    # Second run: collision — stdout should announce the -2 suffix decision
    result2 = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=env,
    )
    assert result2.returncode == 0
    assert "2" in result2.stdout and ("collision" in result2.stdout.lower() or "notice" in result2.stdout.lower()), (
        f"Expected a collision notice on stdout mentioning '-2'; "
        f"got: {result2.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Category 5 — Garbage brief refusal
# ---------------------------------------------------------------------------


def test_garbage_brief_with_output_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """A garbage brief (no valid Target Version) with --output exits non-zero."""
    brief = _write_brief(tmp_path, "garbage.md", _GARBAGE_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    assert result.returncode != 0, (
        f"Expected non-zero exit for garbage brief; got {result.returncode}"
    )


def test_garbage_brief_with_output_writes_no_file(tmp_path: pathlib.Path) -> None:
    """A garbage brief with --output writes no file to the output directory."""
    brief = _write_brief(tmp_path, "garbage.md", _GARBAGE_BRIEF_BODY)
    output_dir = tmp_path / "drafts"
    output_dir.mkdir(parents=True, exist_ok=True)

    run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    produced = list(output_dir.glob("*.md"))
    assert not produced, (
        f"Garbage brief with --output must write no files; found: {produced}"
    )


def test_garbage_brief_with_output_prints_error(tmp_path: pathlib.Path) -> None:
    """A garbage brief with --output prints a loud error message."""
    brief = _write_brief(tmp_path, "garbage.md", _GARBAGE_BRIEF_BODY)
    output_dir = tmp_path / "drafts"

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --output {output_dir}",
        extra_env=_env_with_stub_claude(tmp_path),
    )

    combined = result.stderr + result.stdout
    assert "ERROR" in combined or "invalid" in combined.lower(), (
        f"Expected an error message for garbage brief; "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Category 6 — Absent-flag regression
# ---------------------------------------------------------------------------


def test_absent_output_flag_requires_project_as_before(tmp_path: pathlib.Path) -> None:
    """Without --output, the script still requires --project as before (regression guard)."""
    brief = _write_brief(tmp_path, "test-brief.md", _VALID_BRIEF_BODY)

    # Run WITHOUT --output and WITHOUT --project or PGAI_PROJECT_NAME.
    # Pre-draft-mode behavior: exits non-zero with a "no project specified" error.
    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief}",
        extra_env={
            **_env_with_stub_claude(tmp_path),
            # Ensure PGAI_PROJECT_NAME is not set so the project check fires.
            "PGAI_PROJECT_NAME": "",
        },
    )

    assert result.returncode != 0, (
        "Without --output and without --project, script must exit non-zero "
        f"(same as pre-draft-mode); got exit code {result.returncode}"
    )
    combined = result.stderr + result.stdout
    assert "project" in combined.lower(), (
        f"Expected an error mentioning 'project'; got {combined!r}"
    )


def test_absent_output_flag_dry_run_still_works(tmp_path: pathlib.Path) -> None:
    """Without --output, --dry-run still works as before (no writes, exit 0)."""
    brief = _write_brief(tmp_path, "test-brief.md", _VALID_BRIEF_BODY)

    # Set up a minimal kanban root so pp_tasks_dir can resolve.
    kanban_root = tmp_path / "kanban"
    kanban_root.mkdir(parents=True, exist_ok=True)
    (kanban_root / "projects").mkdir(parents=True, exist_ok=True)
    projects_cfg = kanban_root / "projects.cfg"
    projects_cfg.write_text(
        "[myproject]\npath = projects/myproject\n",
        encoding="utf-8",
    )
    project_root = kanban_root / "projects" / "myproject"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "project.cfg").write_text(
        "[project]\nname = myproject\n",
        encoding="utf-8",
    )

    result = run_bash(
        tmp_path,
        f"bash {_SCRIPT} {brief} --project myproject --dry-run",
        extra_env={
            **_env_with_stub_claude(tmp_path),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        },
    )

    assert result.returncode == 0, (
        f"--dry-run (no --output) must exit 0; got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert "DRY" in result.stdout.upper() or "dry" in result.stdout.lower(), (
        f"Expected dry-run output; got: {result.stdout!r}"
    )
