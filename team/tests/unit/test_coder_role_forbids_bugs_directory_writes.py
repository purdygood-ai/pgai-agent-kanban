"""
test_coder_role_forbids_bugs_directory_writes.py
==================================================
Governance-content guard: pins the swim-lane rule that stops the
code-writing role from filing bug reports into a project's bugs/
directory. The rule lives as prose in team/roles/CODER.md and nothing
else in the shipped tree references it, so nothing catches a future
edit -- a byte-budget trim of the role file being the likeliest
candidate, since that assembly keeps growing -- that deletes or guts
the paragraph while both gated runners keep exiting 0.

Anchor choice
-------------
The assertion matches on the bolded lead sentence of the paragraph,
"Never write into a project's `bugs/` directory.", rather than the
whole paragraph or a line number. That sentence is the rule itself --
the explanatory sentences around it (who may file, what to do with a
defect noticed mid-task, the one ledger line exempted) can be reworded
freely without moving the anchor, so an ordinary edit to the
surrounding prose does not fail this test. Deleting or gutting the
lead sentence -- the only place in the paragraph that actually states
the prohibition -- is what the negative-direction test below proves
this assertion catches.

Three cases, one shared assertion function:
  - test_shipped_role_file_states_the_boundary: the assertion passes
    against the real, installed team/roles/CODER.md.
  - test_assertion_fails_once_the_boundary_sentence_is_removed: the
    assertion fails against a temporary copy of the same file with
    only the lead sentence deleted -- proving the check is a real
    gate, not a tautology that would pass against anything.
  - test_assertion_is_position_independent: the assertion still
    passes against a temporary copy with the boundary line moved to a
    different position in the file, proving the check locates the
    rule by content rather than by line number.

The temporary copies never touch the shipped role file -- team/roles/
is WRITER's lane, not CODER's, and this module pins what is already
there rather than edits it. Both copies are written under pytest's
tmp_path fixture, which the parent conftest redirects under
$PGAI_AGENT_KANBAN_TEMP_DIR (see team/tests/conftest.py, "Temp-dir
redirection for tmp_path"), so no bare /tmp path is ever touched.
"""

from __future__ import annotations

import pathlib

import pytest

_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CODER_ROLE_FILE = _TEAM_ROOT / "roles" / "CODER.md"

# The rule statement this test pins. Loose enough to tolerate a reword of
# the paragraph's explanatory sentences (nothing else in the file needs to
# match this exact wording); specific enough that deleting or gutting the
# prohibition itself is the only ordinary way to make it disappear.
_BOUNDARY_PHRASE = "Never write into a project's `bugs/` directory."


def _assert_forbids_bugs_directory_writes(role_file_text: str) -> None:
    """Assert role_file_text still states that the code-writing lane may
    not write into a project's bugs/ directory. Raises AssertionError
    with a remedy when the phrase is absent."""
    assert _BOUNDARY_PHRASE in role_file_text, (
        "team/roles/CODER.md no longer states the boundary phrase "
        f"{_BOUNDARY_PHRASE!r} -- the code-writing lane's prohibition on "
        "writing into a project's bugs/ directory has been removed or "
        "reworded away from its stated form. Restore a sentence stating "
        "the code-writing lane never writes into a project's bugs/ "
        "directory."
    )


def test_shipped_role_file_states_the_boundary() -> None:
    text = _CODER_ROLE_FILE.read_text(encoding="utf-8")
    _assert_forbids_bugs_directory_writes(text)


def test_assertion_fails_once_the_boundary_sentence_is_removed(
    tmp_path: pathlib.Path,
) -> None:
    original = _CODER_ROLE_FILE.read_text(encoding="utf-8")
    assert _BOUNDARY_PHRASE in original  # sanity: fixture assumes it is present

    gutted = original.replace(_BOUNDARY_PHRASE, "")
    assert gutted != original

    gutted_copy = tmp_path / "CODER.md"
    gutted_copy.write_text(gutted, encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_forbids_bugs_directory_writes(
            gutted_copy.read_text(encoding="utf-8")
        )


def test_assertion_is_position_independent(tmp_path: pathlib.Path) -> None:
    """Moving the boundary line elsewhere in the file must not change the
    result -- the assertion locates the rule by content, not by line
    number."""
    original = _CODER_ROLE_FILE.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    boundary_line_indexes = [
        i for i, line in enumerate(lines) if _BOUNDARY_PHRASE in line
    ]
    assert len(boundary_line_indexes) == 1, (
        "expected exactly one line carrying the boundary phrase in "
        "team/roles/CODER.md"
    )
    boundary_line = lines.pop(boundary_line_indexes[0])

    # Move the boundary line to the very top of the file, far from wherever
    # it shipped, and confirm the assertion still finds it.
    reordered_text = "".join([boundary_line] + lines)

    moved_copy = tmp_path / "CODER.md"
    moved_copy.write_text(reordered_text, encoding="utf-8")

    _assert_forbids_bugs_directory_writes(
        moved_copy.read_text(encoding="utf-8")
    )
