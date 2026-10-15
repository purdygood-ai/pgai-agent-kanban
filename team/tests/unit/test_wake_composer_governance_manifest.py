"""
test_wake_composer_governance_manifest.py
===========================================
Behavioral tests for the wake composer's manifest-driven governance read
list: _wd_build_governance_line in team/scripts/lib/wake_governance_composer.sh
(self-sourced by team/scripts/lib/wake_driver_invoke.sh).

The composer replaces the hardcoded
governance-stack sentence _wd_build_task_prompt used to embed in every
wake prompt with a list assembled, at dispatch time, from the dispatched
role's read-order manifest (read-order/<role>.txt, resolved by
scripts/lib/read_order.sh).
Adding a document to a role's wake is now an explicit manifest edit and
nothing else; a role with no manifest (file absent, or every listed line
resolves to a missing file) degrades to the pre-manifest hardcoded
five-document list rather than emitting an empty governance stack or
aborting the wake.

Acceptance criteria exercised (task README):
  - With coder.txt present, a dispatched CODER wake prompt names exactly
    the documents listed in coder.txt, in manifest order.
  - With coder.txt renamed away, the same dispatch emits the pre-release
    hardcoded list and logs that it fell back -- proving degradation, not
    breakage.
  - A document added to a manifest appears in that role's next assembled
    prompt with no code change anywhere.
  - The prompt's non-governance sections are unchanged.
  - bash -n passes on the modified script (exercised directly by the CODER
    procedure, not repeated here as a pytest case).

_wd_build_governance_line writes its result into the caller's own
`_wd_governance_line` local (bash dynamic scoping) rather than echoing it,
so every test below declares `_wd_governance_line=""` before calling the
function and reads it back afterward with a bare `echo` -- never via
command substitution around the call itself, which would instead capture
the fallback branch's log() output (log() tees to stdout; see the
function's own header comment and tests/fixtures/log_stub.py for the
documented contamination pattern this avoids).
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.fixtures.log_stub import make_log_stub_fragment
from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths relative to team/, the CWD when run via run-unit-tests.sh.
# ---------------------------------------------------------------------------
_READ_ORDER_LIB = "scripts/lib/read_order.sh"
_WAKE_DRIVER_INVOKE_LIB = "scripts/lib/wake_driver_invoke.sh"

# The real, checked-in manifest and role file this task's fixtures compare
# against for the end-to-end (real-tree) cases.
_REAL_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REAL_CODER_MANIFEST = _REAL_TEAM_ROOT / "read-order" / "coder.txt"


def _governance_script(call: str, log_file: pathlib.Path) -> str:
    """Return a bash script that sources the production libs (with a
    faithful log() stub injected first, since wake_driver_invoke.sh
    documents log() as a dependency it expects already sourced) and then
    runs *call* -- typically a `_wd_build_governance_line ...` invocation
    followed by `echo "$_wd_governance_line"`.
    """
    stub = make_log_stub_fragment(log_file)
    return (
        stub
        + "\n"
        + f"source {_READ_ORDER_LIB}\n"
        + f"source {_WAKE_DRIVER_INVOKE_LIB}\n"
        + textwrap.dedent(call)
    )


def _make_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a fixture root with its own read-order/ manifest dir and the
    five files a hardcoded-fallback governance stack would reference."""
    root = tmp_path / "fixture_root"
    (root / "read-order").mkdir(parents=True)
    (root / "roles").mkdir(parents=True)
    (root / "DIRECTIVES.md").write_text("directives\n", encoding="utf-8")
    (root / "OVERVIEW.md").write_text("overview\n", encoding="utf-8")
    (root / "SOP-core.md").write_text("sop\n", encoding="utf-8")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "roles" / "TESTROLE.md").write_text("role file\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# AC1 + AC3: manifest present -> exact documents, in manifest order; a
# document added to the manifest appears with no code change.
# ---------------------------------------------------------------------------


def test_manifest_present_names_exact_documents_in_manifest_order(
    tmp_path: pathlib.Path,
) -> None:
    """A fixture manifest's files appear in the assembled line, in order,
    with the last entry (the role file) phrased as 'your role file at'."""
    root = _make_root(tmp_path)
    (root / "read-order" / "testrole.txt").write_text(
        "DIRECTIVES.md\nSOP-core.md\nroles/TESTROLE.md\n", encoding="utf-8"
    )
    log_file = tmp_path / "wake.log"

    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line testrole TESTROLE '{root}' ""
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]

    expected = (
        f"{root}/DIRECTIVES.md, {root}/SOP-core.md, "
        f"and your role file at {root}/roles/TESTROLE.md"
    )
    assert line == expected, f"Got:\n{line}\nExpected:\n{expected}"
    # OVERVIEW.md and README.md exist on disk but are NOT in this manifest --
    # a document not listed in the manifest must never appear.
    assert "OVERVIEW.md" not in line
    assert "README.md" not in line


def test_document_added_to_manifest_appears_with_no_code_change(
    tmp_path: pathlib.Path,
) -> None:
    """Editing only the manifest text (no code change) changes the
    assembled line -- proving the composer reads the manifest, not a
    hardcoded copy of its contents."""
    root = _make_root(tmp_path)
    (root / "EXTRA.md").write_text("extra doc\n", encoding="utf-8")
    (root / "read-order" / "testrole.txt").write_text(
        "DIRECTIVES.md\nroles/TESTROLE.md\n", encoding="utf-8"
    )
    log_file = tmp_path / "wake.log"

    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line testrole TESTROLE '{root}' ""
        echo "$_wd_governance_line"
    """
    before = run_bash(tmp_path, _governance_script(call, log_file))
    assert before.returncode == 0, before.stderr
    assert "EXTRA.md" not in before.stdout

    # Manifest edit only -- append the new document, in order, before the
    # role file (which must stay last for the "your role file at" phrasing).
    (root / "read-order" / "testrole.txt").write_text(
        "DIRECTIVES.md\nEXTRA.md\nroles/TESTROLE.md\n", encoding="utf-8"
    )
    after = run_bash(tmp_path, _governance_script(call, log_file))
    assert after.returncode == 0, after.stderr
    after_line = after.stdout.strip().splitlines()[-1]
    assert f"{root}/EXTRA.md" in after_line
    # Order preserved: EXTRA.md between DIRECTIVES.md and the role file.
    assert after_line.index(str(root / "DIRECTIVES.md")) < after_line.index(
        str(root / "EXTRA.md")
    )
    assert after_line.index(str(root / "EXTRA.md")) < after_line.index(
        str(root / "roles" / "TESTROLE.md")
    )


# ---------------------------------------------------------------------------
# AC2: manifest absent (e.g. "renamed away") -> pre-release hardcoded list,
# with a logged fallback narration -- degradation, not breakage.
# ---------------------------------------------------------------------------


def test_missing_manifest_falls_back_to_hardcoded_list_and_logs(
    tmp_path: pathlib.Path,
) -> None:
    """No read-order/<role>.txt file at all: the hardcoded five-document
    list is emitted unchanged, and the fallback is narrated via log()."""
    root = _make_root(tmp_path)
    # Deliberately do NOT write read-order/testrole.txt -- simulates the
    # manifest having been renamed away.
    log_file = tmp_path / "wake.log"

    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line testrole TESTROLE '{root}' ""
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]

    expected = (
        f"{root}/DIRECTIVES.md, {root}/OVERVIEW.md, {root}/SOP-core.md, "
        f"{root}/README.md, and your role file at {root}/roles/TESTROLE.md"
    )
    assert line == expected, f"Got:\n{line}\nExpected:\n{expected}"

    log_content = log_file.read_text(encoding="utf-8")
    assert "no manifest resolved" in log_content
    assert "testrole" in log_content
    assert "fell back" in log_content


def test_manifest_naming_only_missing_files_also_falls_back(
    tmp_path: pathlib.Path,
) -> None:
    """A manifest file that exists but names only absent files resolves to
    zero files -- read_order_resolve_role's own contract -- and must
    degrade exactly like a wholly-absent manifest, not emit an empty
    governance stack."""
    root = _make_root(tmp_path)
    (root / "read-order" / "testrole.txt").write_text(
        "GHOST-ONE.md\nGHOST-TWO.md\n", encoding="utf-8"
    )
    log_file = tmp_path / "wake.log"

    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line testrole TESTROLE '{root}' ""
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]
    assert line != "", "Governance stack must never be empty."
    assert f"{root}/DIRECTIVES.md" in line
    assert f"{root}/roles/TESTROLE.md" in line


# ---------------------------------------------------------------------------
# project_readme_line: resolved at runtime, appended after the role file
# (the project layer is read after the role layer), never a manifest line
# itself.
# ---------------------------------------------------------------------------


def test_project_readme_line_follows_the_role_file(
    tmp_path: pathlib.Path,
) -> None:
    root = _make_root(tmp_path)
    (root / "read-order" / "testrole.txt").write_text(
        "DIRECTIVES.md\nREADME.md\nroles/TESTROLE.md\n", encoding="utf-8"
    )
    log_file = tmp_path / "wake.log"
    readme_line = " and your project README at /projects/demo/README.md"

    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line testrole TESTROLE '{root}' '{readme_line}'
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]

    expected = (
        f"{root}/DIRECTIVES.md, {root}/README.md, "
        f"and your role file at {root}/roles/TESTROLE.md, "
        f"then your project README at /projects/demo/README.md"
    )
    assert line == expected, f"Got:\n{line}\nExpected:\n{expected}"


def test_empty_project_readme_line_appends_nothing_extra(
    tmp_path: pathlib.Path,
) -> None:
    """The common (single-project) case: project_readme_line is empty, and
    the README.md entry is emitted with no trailing text spliced in."""
    root = _make_root(tmp_path)
    (root / "read-order" / "testrole.txt").write_text(
        "README.md\nroles/TESTROLE.md\n", encoding="utf-8"
    )
    log_file = tmp_path / "wake.log"

    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line testrole TESTROLE '{root}' ""
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]
    expected = f"{root}/README.md, and your role file at {root}/roles/TESTROLE.md"
    assert line == expected


# ---------------------------------------------------------------------------
# End-to-end against the real, checked-in coder.txt manifest.
# ---------------------------------------------------------------------------


def test_real_coder_manifest_produces_exact_documents_in_order(
    tmp_path: pathlib.Path,
) -> None:
    """Against the real tree, resolving role 'coder' emits exactly
    coder.txt's files, in manifest order -- the literal AC1 fixture."""
    expected_lines = [
        line.strip()
        for line in _REAL_CODER_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert expected_lines, "coder.txt fixture is empty -- test fixture is stale."
    expected_paths = [str(_REAL_TEAM_ROOT / rel) for rel in expected_lines]

    log_file = tmp_path / "wake.log"
    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line coder CODER '{_REAL_TEAM_ROOT}' ""
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]

    # Every manifest path must appear, in the manifest's own order.
    positions = [line.index(p) for p in expected_paths]
    assert positions == sorted(positions), (
        f"Manifest paths are not in manifest order in the assembled line.\n"
        f"Line: {line}\nExpected paths in order: {expected_paths}"
    )
    # The last manifest entry (the role file) carries the "your role file
    # at" phrasing, matching the pre-manifest hardcoded sentence's wording.
    assert f"your role file at {expected_paths[-1]}" in line


def test_real_coder_manifest_renamed_away_falls_back(tmp_path: pathlib.Path) -> None:
    """Simulates 'coder.txt renamed away' against a copy of the real tree
    layout: with no read-order/coder.txt, the dispatch emits the pre-
    release hardcoded list and logs the fallback."""
    # Build a fixture root that mirrors the real tree's flat runtime layout
    # (DIRECTIVES.md, OVERVIEW.md, SOP-core.md, README.md, roles/CODER.md)
    # but deliberately omits read-order/coder.txt.
    root = tmp_path / "renamed_away_root"
    (root / "roles").mkdir(parents=True)
    (root / "read-order").mkdir(parents=True)
    for name in ("DIRECTIVES.md", "OVERVIEW.md", "SOP-core.md", "README.md"):
        (root / name).write_text(f"{name} content\n", encoding="utf-8")
    (root / "roles" / "CODER.md").write_text("coder role file\n", encoding="utf-8")

    log_file = tmp_path / "wake.log"
    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line coder CODER '{root}' ""
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]

    expected = (
        f"{root}/DIRECTIVES.md, {root}/OVERVIEW.md, {root}/SOP-core.md, "
        f"{root}/README.md, and your role file at {root}/roles/CODER.md"
    )
    assert line == expected, f"Got:\n{line}\nExpected:\n{expected}"

    log_content = log_file.read_text(encoding="utf-8")
    assert "no manifest resolved for role 'coder'" in log_content
    assert "fell back" in log_content


# ---------------------------------------------------------------------------
# Structural gates: the composer actually wires _wd_build_governance_line
# into the heredoc, and every other prompt section the governance-line
# wiring leaves alone is still present, unchanged.
# ---------------------------------------------------------------------------


def test_prompt_builder_calls_governance_line_function() -> None:
    """_wd_build_task_prompt must call _wd_build_governance_line and embed
    its result in the heredoc -- not a residual hardcoded sentence."""
    content = pathlib.Path(_WAKE_DRIVER_INVOKE_LIB).read_text(encoding="utf-8")
    assert "_wd_build_governance_line" in content
    assert "${_wd_governance_line}" in content
    # The old hardcoded sentence must be gone from the heredoc -- the
    # manifest, not this literal string, drives what the prompt names.
    assert (
        "read the governance stack: ${TEAM_ROOT}/DIRECTIVES.md, ${TEAM_ROOT}/OVERVIEW.md"
        not in content
    )


def test_wake_driver_sources_read_order_lib() -> None:
    """wake_driver.sh must source read_order.sh so read_order_resolve_role
    is defined before wake_driver_invoke.sh's functions are called."""
    content = pathlib.Path("scripts/lib/wake_driver.sh").read_text(encoding="utf-8")
    assert "read_order.sh" in content


@pytest.mark.parametrize(
    "marker",
    [
        "IMPORTANT: The working directory for this task is the isolated git worktree",
        "Scratch/diagnostic output goes under ${_project_temp_subtree}",
        'You are running autonomously. No human is reviewing your output in real time.',
        "${role_reminder}",
    ],
    ids=[
        "working_dir_override",
        "scratch_line",
        "autonomy_paragraph",
        "role_reminder",
    ],
)
def test_non_governance_prompt_sections_unchanged(marker: str) -> None:
    """Regression guard: every non-governance prompt section is still
    present verbatim in the heredoc."""
    content = pathlib.Path(_WAKE_DRIVER_INVOKE_LIB).read_text(encoding="utf-8")
    assert marker in content, (
        f"Expected non-governance marker not found in {_WAKE_DRIVER_INVOKE_LIB}: "
        f"{marker!r}. Only the governance-stack sentence is manifest-driven; "
        "every other prompt section must stay in place."
    )
