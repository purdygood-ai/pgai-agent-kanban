"""
test_cm_release_lane_b_intent_remedy_agreement.py
==================================================
team/scripts/cm/release.sh's Lane B override refuses an unmatched --reason
with error key cm-release.overwatch-lane-b.intent-not-matched (CENSUS-ANCHOR
overwatch-lane-b-intent-unmatched-01) and prints a remedy telling the
operator what to add to the project's INTENT.md to clear the refusal. The
check the refusal is printed from, _cm_release_lane_b_intent_verified(),
compares the INTENT.md entry's declaration text against --reason by exact
string equality. A remedy that tells the operator to write an entry
"whose text contains --reason verbatim" sends an operator who does exactly
that (declaration = reason plus surrounding words) into a second refusal,
because containment is not what the check implements.

This module proves the check and the remedy agree, and keeps them from
drifting apart: both the check function AND the remedy string are
extracted VERBATIM from the live release.sh source (never hand-copied), so
a future edit to either half alone — relaxing the check to containment
without updating the remedy's wording, or reverting the remedy's wording
to promise containment without relaxing the check — fails this suite. The
single combined test below exercises the remedy text and the check
function together, per the requirement that the two never be asserted in
isolation.
"""

from __future__ import annotations

import pathlib
import re

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_RELEASE_SH = _TEAM_DIR / "scripts" / "cm" / "release.sh"
_ERROR_CONTRACT_SH = _TEAM_DIR / "scripts" / "lib" / "error_contract.sh"
_PP_RUN_OPS_SH = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"

_FUNCTION_NAME = "_cm_release_lane_b_intent_verified"
_ERROR_KEY = "cm-release.overwatch-lane-b.intent-not-matched"


def _extract_function(source_path: pathlib.Path, function_name: str) -> str:
    """Extract a top-level bash function definition verbatim.

    Scans for a line exactly matching "<function_name>() {" and returns
    every line up to and including the next line that is exactly "}" (the
    closing brace of a function defined at column 0). Raises AssertionError
    if the function or its closing brace cannot be found, so a rename or
    reformat upstream fails this test loudly rather than silently testing
    nothing.
    """
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = f"{function_name}() {{"
    start = next((i for i, line in enumerate(lines) if line == header), None)
    assert start is not None, (
        f"Could not find '{header}' in {source_path}. "
        "The function may have been renamed or reformatted."
    )
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == "}"), None)
    assert end is not None, (
        f"Could not find the closing brace for {function_name}() in {source_path}."
    )
    return "\n".join(lines[start : end + 1])


def _extract_remedy_text(source_path: pathlib.Path, error_key: str) -> str:
    """Extract the remedy (third) argument of the pgai_emit_error_contract
    call whose error key is *error_key*, verbatim from the live source —
    never hand-copied — so a future edit to the printed remedy is picked up
    automatically the next time this test runs.
    """
    text = source_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'pgai_emit_error_contract\s*\\\s*\n'
        r'\s*"' + re.escape(error_key) + r'"\s*\\\s*\n'
        r'\s*"((?:[^"\\]|\\.)*)"\s*\\\s*\n'
        r'\s*"((?:[^"\\]|\\.)*)"',
    )
    match = pattern.search(text)
    assert match is not None, (
        f"Could not locate the pgai_emit_error_contract call for error key "
        f"{error_key!r} in {source_path}. The call site may have been "
        "reformatted (this test expects the standard 3-backslash-continued "
        "error/message/remedy shape)."
    )
    return match.group(2)


def _write_intent_md(kanban_root: pathlib.Path, project: str, declaration: str) -> None:
    proj_dir = kanban_root / "projects" / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "INTENT.md").write_text(
        f"# INTENT\n\n## Entries\n- 2026-09-10: {declaration}\n", encoding="utf-8"
    )


def _run_check(tmp_path: pathlib.Path, declaration: str, reason: str):
    """Source the real _cm_release_lane_b_intent_verified() — extracted
    verbatim from release.sh, not a hand copy — against a synthetic
    INTENT.md carrying *declaration*, and test whether *reason* is admitted.
    """
    fn_body = _extract_function(_RELEASE_SH, _FUNCTION_NAME)
    project = "remedy_agreement_proj"
    kanban_root = tmp_path / "kanban_root"
    _write_intent_md(kanban_root, project, declaration)

    script = f"""\
set -uo pipefail
source {str(_ERROR_CONTRACT_SH)!r}
source {str(_PP_RUN_OPS_SH)!r}

{fn_body}

_cm_release_lane_b_intent_verified {str(kanban_root)!r} {project!r} {reason!r}
"""
    return run_bash(tmp_path, script)


def test_remedy_text_and_check_agree_on_exact_equality(tmp_path: pathlib.Path) -> None:
    """The remedy and the check must describe the SAME rule, and this test
    exercises both together rather than asserting each in isolation:

    1. The remedy text (extracted verbatim from the live emitter call) must
       state the real rule the check implements — byte-for-byte equality —
       and must not promise the containment rule the check has never
       implemented.
    2. An INTENT.md entry that satisfies containment but NOT equality (the
       shape a containment-promising remedy tells an operator to write:
       --reason plus surrounding words) must still be refused by the real check function.
       If a future edit relaxes the check to containment without touching
       the remedy wording, this assertion fails.
    3. An INTENT.md entry that satisfies the remedy's actual instruction —
       a declaration byte-for-byte identical to --reason — must be admitted
       by the real check function. If a future edit reverts the remedy's
       wording to promise containment without relaxing the check, assertion
       1 above fails; if the check itself regresses to something stricter
       than exact-equality, this assertion fails.
    """
    remedy = _extract_remedy_text(_RELEASE_SH, _ERROR_KEY)

    # (1) The remedy states the real rule, not a containment promise the
    # check does not implement.
    assert "byte-for-byte" in remedy or "exactly" in remedy, (
        f"Remedy no longer states an exact-equality rule; a stranger reading "
        f"it would not know a substring match is insufficient:\n{remedy}"
    )
    assert "whose text contains --reason verbatim" not in remedy, (
        f"Remedy promises a containment match an operator who follows it "
        f"literally would still be refused for, since the check compares "
        f"by exact equality:\n{remedy}"
    )

    reason = "ai_main rebased to v2.4.0 for the winter migration experiment"

    # (2) Containment-only match (what a containment-promising remedy tells
    # an operator to write) must still be refused by the real check.
    containment_only_declaration = f"{reason} — plus a trailing operator note"
    containment_result = _run_check(tmp_path, containment_only_declaration, reason)
    assert containment_result.returncode != 0, (
        "A declaration that merely CONTAINS --reason (the containment "
        "promise the remedy must not make) was admitted — the check has drifted to containment "
        "semantics without the remedy documenting a fallback for exact "
        "callers, or the remedy's exact-equality claim above is now false.\n"
        f"stdout: {containment_result.stdout}\nstderr: {containment_result.stderr}"
    )

    # (3) An entry that follows the remedy's actual instruction — exact,
    # byte-for-byte equality — is admitted.
    exact_result = _run_check(tmp_path, reason, reason)
    assert exact_result.returncode == 0, (
        "Following the (now-corrected) remedy literally — writing a "
        "declaration byte-for-byte identical to --reason — did not clear "
        "the refusal. The dual-channel contract's promise that the remedy "
        "is runnable is broken.\n"
        f"stdout: {exact_result.stdout}\nstderr: {exact_result.stderr}"
    )


def test_check_refuses_when_no_entry_matches(tmp_path: pathlib.Path) -> None:
    """An INTENT.md with no authorising entry at all is refused (fail-closed
    — no entries, no false admission)."""
    project = "remedy_agreement_no_entry_proj"
    kanban_root = tmp_path / "kanban_root"
    (kanban_root / "projects" / project).mkdir(parents=True, exist_ok=True)
    (kanban_root / "projects" / project / "INTENT.md").write_text(
        "# INTENT\n\n## Entries\n\n<!-- no live entries -->\n", encoding="utf-8"
    )

    fn_body = _extract_function(_RELEASE_SH, _FUNCTION_NAME)
    script = f"""\
set -uo pipefail
source {str(_ERROR_CONTRACT_SH)!r}
source {str(_PP_RUN_OPS_SH)!r}

{fn_body}

_cm_release_lane_b_intent_verified {str(kanban_root)!r} {project!r} 'any reason at all'
"""
    result = run_bash(tmp_path, script)
    assert result.returncode != 0, (
        f"An INTENT.md with zero live entries must never admit a Lane B "
        f"override.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
