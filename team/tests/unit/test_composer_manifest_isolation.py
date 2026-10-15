"""
test_composer_manifest_isolation.py
=====================================
Composer fixture: a doc NOT in a manifest never appears in that role's
wake.

Proves the manifest is the only door into a role's assembled governance
prompt: a document that genuinely exists in the checked-in tree, but is
absent from a role's read-order manifest, never appears in that role's
assembled wake prompt -- and adding the same document to the manifest (a
text edit, no code change) makes it appear.

Composer path under test: _wd_build_governance_line in
team/scripts/lib/wake_governance_composer.sh (self-sourced by
team/scripts/lib/wake_driver_invoke.sh, the file this test sources), which
resolves a role's manifest via
read_order_resolve_role (scripts/lib/read_order.sh) and joins the result
into the exact prose fragment _wd_build_task_prompt embeds in every wake
prompt after "read the governance stack: ". This is the same function
team/tests/unit/test_wake_composer_governance_manifest.py exercises for
the composer's manifest-order and fallback acceptance criteria; this
file is scoped narrowly to isolation -- absence and presence driven purely
by the manifest text.

Isolation document: team/TESTING.md. It genuinely exists in the checked-in
tree (satisfying the task constraint that the isolation fixture must use a
real document -- asserting the absence of a file that does not exist proves
nothing about the manifest) and is absent from read-order/coder.txt, which
lists only DIRECTIVES.md, OVERVIEW.md, SOP-core.md, README.md and
roles/CODER.md.

Fixture root construction: rather than fabricating synthetic file content,
_mirror_root() builds a tmp_path root that SYMLINKS every top-level entry of
the real team root (except read-order/, which this test controls) into the
fixture root. Every path the assembled prompt can reference -- including
TESTING.md -- therefore resolves to the genuine, checked-in file, not a
fixture stand-in. Only the read-order/coder.txt manifest itself is a
test-controlled copy, so the "add a document to the manifest" half of the
test can edit it without mutating the real, tracked read-order/coder.txt.
"""

from __future__ import annotations

import pathlib
import textwrap

from tests.fixtures.log_stub import make_log_stub_fragment
from tests.unit.shell_harness import run_bash

_READ_ORDER_LIB = "scripts/lib/read_order.sh"
_WAKE_DRIVER_INVOKE_LIB = "scripts/lib/wake_driver_invoke.sh"

_REAL_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REAL_CODER_MANIFEST = _REAL_TEAM_ROOT / "read-order" / "coder.txt"

# A document that genuinely exists in the checked-in tree, at
# team/TESTING.md, and is absent from read-order/coder.txt.
_ISOLATION_DOC_REL = "TESTING.md"


def _governance_script(call: str, log_file: pathlib.Path) -> str:
    """Bash script that sources the production libs (with a faithful log()
    stub injected first, as wake_driver_invoke.sh documents log() as a
    dependency it expects already sourced) and then runs *call*."""
    stub = make_log_stub_fragment(log_file)
    return (
        stub
        + "\n"
        + f"source {_READ_ORDER_LIB}\n"
        + f"source {_WAKE_DRIVER_INVOKE_LIB}\n"
        + textwrap.dedent(call)
    )


def _mirror_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a fixture root that symlinks every real top-level team-root
    entry except read-order/ (which this test supplies its own copy of),
    so every document the assembled prompt can name resolves to genuine,
    checked-in tree content."""
    root = tmp_path / "mirror_root"
    root.mkdir()
    for entry in _REAL_TEAM_ROOT.iterdir():
        if entry.name == "read-order":
            continue
        (root / entry.name).symlink_to(entry)
    (root / "read-order").mkdir()
    return root


def _real_manifest_lines() -> list[str]:
    """The real coder.txt manifest's non-comment, non-blank lines, in order."""
    return [
        line.strip()
        for line in _REAL_CODER_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _assemble_coder_governance_line(root: pathlib.Path, tmp_path: pathlib.Path) -> str:
    """Runs _wd_build_governance_line for role 'coder' against *root* and
    returns the assembled governance-stack fragment -- the text
    _wd_build_task_prompt embeds verbatim in every CODER wake prompt after
    "read the governance stack: "."""
    log_file = tmp_path / "wake.log"
    call = f"""
        _wd_governance_line=""
        _wd_build_governance_line coder CODER '{root}' ""
        echo "$_wd_governance_line"
    """
    result = run_bash(tmp_path, _governance_script(call, log_file))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


# ---------------------------------------------------------------------------
# Sanity: the chosen isolation document is genuine and genuinely absent.
# ---------------------------------------------------------------------------


def test_isolation_document_exists_in_tree_and_is_absent_from_coder_manifest() -> None:
    """The document this fixture proves isolation with must be a real,
    checked-in tree file -- not a fabricated name -- and must genuinely be
    absent from read-order/coder.txt today. If this fails, the fixture
    itself is stale and needs a new isolation document picked."""
    assert (_REAL_TEAM_ROOT / _ISOLATION_DOC_REL).is_file(), (
        f"{_ISOLATION_DOC_REL} does not exist under {_REAL_TEAM_ROOT} -- "
        "pick a document that genuinely exists in the tree."
    )
    assert _ISOLATION_DOC_REL not in _real_manifest_lines(), (
        f"{_ISOLATION_DOC_REL} is already listed in {_REAL_CODER_MANIFEST} -- "
        "pick a different isolation document; this fixture needs one that is "
        "currently absent from the manifest."
    )


# ---------------------------------------------------------------------------
# Absence: manifest-absent document never appears in the assembled prompt.
# ---------------------------------------------------------------------------


def test_tree_present_manifest_absent_document_excluded_from_assembled_prompt(
    tmp_path: pathlib.Path,
) -> None:
    """With the real, unmodified coder.txt manifest, TESTING.md exists on
    disk (via the mirror root's symlink) but must never appear in the
    assembled CODER governance line -- the manifest is the only door."""
    root = _mirror_root(tmp_path)
    (root / "read-order" / "coder.txt").write_text(
        _REAL_CODER_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert (root / _ISOLATION_DOC_REL).is_file(), (
        "Mirror root did not symlink TESTING.md; fixture construction is broken."
    )

    line = _assemble_coder_governance_line(root, tmp_path)
    assert _ISOLATION_DOC_REL not in line, (
        f"{_ISOLATION_DOC_REL} appeared in the assembled CODER prompt's "
        f"governance line despite being absent from coder.txt:\n{line}"
    )


# ---------------------------------------------------------------------------
# Presence: adding the document to the manifest makes it appear.
# ---------------------------------------------------------------------------


def test_adding_document_to_manifest_makes_it_appear_in_assembled_prompt(
    tmp_path: pathlib.Path,
) -> None:
    """A manifest edit alone -- no code change -- adding TESTING.md before
    the role-file entry makes it appear in the assembled governance line.
    This proves the fixture tracks the manifest, not a hardcoded list: the
    same document that was absent above now appears purely because the
    manifest text changed."""
    root = _mirror_root(tmp_path)
    lines = _real_manifest_lines()
    assert lines, "coder.txt fixture is empty -- test fixture is stale."
    # Insert before the last entry (the role file) so the role file stays
    # last, preserving the "your role file at" phrasing _wd_build_governance_line
    # applies to the final manifest entry.
    modified_lines = lines[:-1] + [_ISOLATION_DOC_REL] + lines[-1:]
    (root / "read-order" / "coder.txt").write_text(
        "\n".join(modified_lines) + "\n", encoding="utf-8"
    )

    line = _assemble_coder_governance_line(root, tmp_path)
    assert _ISOLATION_DOC_REL in line, (
        f"{_ISOLATION_DOC_REL} did not appear in the assembled CODER prompt's "
        f"governance line after being added to coder.txt:\n{line}"
    )
