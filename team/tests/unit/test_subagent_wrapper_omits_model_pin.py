"""
test_subagent_wrapper_omits_model_pin.py
==========================================
Governance-content guard: pins the rule that a shipped subagent wrapper
under subagents/ never carries its own frontmatter ``model:`` key. An
explicit model in a subagent definition outranks the session model the
wake layer passes with ``--model``, so a wrapper pin silently wins every
dispatch and makes both the ``kanban.cfg [models.<provider>]``
configuration and a task-level ``## Model Override`` unable to take
effect. Model selection belongs to the wake layer's resolution chain,
never to a wrapper.

Two cases, one shared scanning function:
  - test_real_subagent_wrappers_carry_no_frontmatter_model_key: the scan
    finds nothing when pointed at the repository's own subagents/
    directory.
  - test_scan_fails_naming_the_file_when_a_wrapper_pins_a_model: the scan
    finds and names a synthetic fixture wrapper carrying a frontmatter
    ``model:`` line, proving the check is a real gate rather than a
    tautology that would pass against anything.

The fixture directory in the second case is written under pytest's
tmp_path fixture, which the parent conftest redirects under
$PGAI_AGENT_KANBAN_TEMP_DIR (see team/tests/conftest.py, "Temp-dir
redirection for tmp_path"), so no bare /tmp path is ever touched, and the
real subagents/ directory is never written to.
"""

from __future__ import annotations

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_REAL_SUBAGENTS_DIR = _REPO_ROOT / "subagents"


def _wrappers_with_frontmatter_model_key(subagents_dir: pathlib.Path) -> list[pathlib.Path]:
    """Return every *.md file under subagents_dir whose YAML frontmatter
    (the block between the first two '---' lines) carries a 'model:' key.

    Liberal in what it reads: tolerates leading/trailing whitespace around
    the colon and around the value, per the parser convention in
    roles/CODER.md ("Parsers are liberal in what they accept").
    """
    offenders: list[pathlib.Path] = []
    for md_file in sorted(subagents_dir.glob("*.md")):
        lines = md_file.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0].strip() != "---":
            continue
        in_frontmatter = True
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if not in_frontmatter:
                continue
            key = line.split(":", 1)[0].strip()
            if key == "model":
                offenders.append(md_file)
                break
    return offenders


def test_real_subagent_wrappers_carry_no_frontmatter_model_key() -> None:
    """Every wrapper shipped in the repository's own subagents/ directory
    must inherit the session model the wake layer resolves; none may pin
    its own model in frontmatter.
    """
    assert _REAL_SUBAGENTS_DIR.is_dir(), f"subagents/ directory not found at {_REAL_SUBAGENTS_DIR}"

    wrapper_files = sorted(_REAL_SUBAGENTS_DIR.glob("*.md"))
    assert wrapper_files, f"no *.md wrapper files found under {_REAL_SUBAGENTS_DIR}"

    offenders = _wrappers_with_frontmatter_model_key(_REAL_SUBAGENTS_DIR)
    assert not offenders, (
        "the following subagent wrapper(s) carry a frontmatter 'model:' key, "
        "which overrides the model the wake layer resolves from "
        "kanban.cfg [models.<provider>] and any task-level ## Model Override: "
        f"{[str(p) for p in offenders]}"
    )


def test_scan_fails_naming_the_file_when_a_wrapper_pins_a_model(
    tmp_path: pathlib.Path,
) -> None:
    """A synthetic fixture directory containing one clean wrapper and one
    wrapper that pins a model must fail the scan, naming only the offender.
    """
    fixture_dir = tmp_path / "subagents"
    fixture_dir.mkdir()

    clean_wrapper = fixture_dir / "clean.md"
    clean_wrapper.write_text(
        "---\n"
        "name: clean\n"
        "description: \"A wrapper with no model pin.\"\n"
        "tools: Read, Write\n"
        "---\n"
        "\n"
        "You are working as CLEAN.\n",
        encoding="utf-8",
    )

    pinned_wrapper = fixture_dir / "pinned.md"
    pinned_wrapper.write_text(
        "---\n"
        "name: pinned\n"
        "description: \"A wrapper that pins a model.\"\n"
        "tools: Read, Write\n"
        "model: sonnet\n"
        "---\n"
        "\n"
        "You are working as PINNED.\n",
        encoding="utf-8",
    )

    offenders = _wrappers_with_frontmatter_model_key(fixture_dir)

    assert offenders == [pinned_wrapper], (
        f"expected only {pinned_wrapper} to be named as an offender, got {offenders}"
    )
