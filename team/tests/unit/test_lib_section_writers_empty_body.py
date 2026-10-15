"""
test_lib_section_writers_empty_body.py
======================================
Every section writer under scripts/lib must rewrite only the section it
targets. An empty target body is filled in place -- it never runs into the
next section, so every other heading and body survives byte-for-byte. A
populated target is rewritten with the same content as before.

Writers covered here:
  scripts/lib/discovery_state.sh
      _disc_set_status_field               (## Status, case-insensitive)
  scripts/lib/wake_driver_finalize.sh
      _wd_append_summary_finding           (## Summary, the pollution
                                            sweep's finding append)
  scripts/lib/overwatch-checks/check-readme-bundled.sh
      _crb_do_remediate                    (State, Summary, Needs Human,
                                            Blocked Reason after Blockers)

Every test runs shipped code on a fixture file. discovery_state.sh,
wake_driver_finalize.sh and the OVERWATCH check are sourced whole (the
check's collaborators overwatch_backup_file / overwatch_log_action are
stubbed, as the OVERWATCH check tests do). Every fixture lives under
pytest's tmp_path; no live task folder or kanban install is touched.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_DISCOVERY_STATE = _LIB / "discovery_state.sh"
_FINALIZE = _LIB / "wake_driver_finalize.sh"
_CHECK_README_BUNDLED = _LIB / "overwatch-checks" / "check-readme-bundled.sh"
_HARNESS = _TEAM_DIR / "scripts" / "wake" / "harness.sh"

# The pre-boundary splice shape: "\s*\n)(.*?)" under re.S lets \s* swallow an
# empty body's blank line so the lazy body starts at the next heading.
_OLD_WRITER_SHAPE = re.compile(r"\\s\*\\n\)\(\.\*\?\)")

_OW_STUBS = "overwatch_backup_file() { echo none; }\noverwatch_log_action() { :; }\n"


def _explain(result) -> str:
    return f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"


def _sections(text: str) -> dict[str, str]:
    """Map each '## ' heading to its full chunk: heading line through the
    byte before the next heading (blank separator lines included)."""
    starts = [m.start() for m in re.finditer(r"^## ", text, flags=re.M)]
    chunks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    return {chunk.split("\n", 1)[0].rstrip(): chunk for chunk in chunks}


def _body(text: str, heading: str) -> str:
    return _sections(text)[heading].split("\n", 1)[1].strip()


def _assert_untouched(before: str, after: str, owned: set[str]) -> None:
    """Every heading of *before* survives in order, and every section the
    writer does not own is byte-for-byte unchanged."""
    old, new = _sections(before), _sections(after)
    kept = [h for h in new if h in old]
    assert kept == list(old), f"section removed or reordered: {list(old)} -> {list(new)}\n{after!r}"
    for heading, chunk in old.items():
        if heading not in owned:
            assert new[heading] == chunk, f"{heading!r} altered:\n{after!r}"


# --- pattern level ------------------------------------------------------------


def test_no_section_writer_of_the_old_shape_remains_under_scripts_lib() -> None:
    # Intentional pattern-scan: every current and future splice under
    # scripts/lib (and the harness provider) must use the section-body
    # boundary; each file is read in full.
    paths = sorted(p for p in _LIB.rglob("*") if p.is_file()) + [_HARNESS]
    hits = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [
            f"{path.relative_to(_TEAM_DIR)}:{n}: {line.strip()}"
            for n, line in enumerate(text.splitlines(), 1)
            if _OLD_WRITER_SHAPE.search(line)
        ]
    assert hits == [], "section writers must stop the body at the next '##' line:\n" + "\n".join(hits)


# --- discovery_state.sh _disc_set_status_field --------------------------------


def _set_status_field(tmp_path: pathlib.Path, text: str, value: str = "done") -> str:
    item = tmp_path / "BUG-0001-example.md"
    item.write_text(text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_DISCOVERY_STATE}'\n_disc_set_status_field '{item}' '{value}'",
    )
    assert result.returncode == 0, _explain(result)
    return item.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "before",
    [
        "# BUG-0001\n\n## Status\n\n## Category\nwake\n\n## Symptom\ns\n",
        "# BUG-0001\n\n## Status\n## Category\nwake\n",
        "# BUG-0001\n\n## Status  \n\n\n\n## Category\nwake\n",
        "# BUG-0001\n\n## status\n\n## Category\nwake\n",
    ],
    ids=["blank-line-then-next", "next-heading-directly", "trailing-space-and-blank-lines", "lowercase-heading"],
)
def test_status_field_writer_fills_empty_non_last_status_in_place(tmp_path: pathlib.Path, before: str) -> None:
    after = _set_status_field(tmp_path, before)
    target = next(h for h in _sections(before) if h.lower().startswith("## status"))
    _assert_untouched(before, after, {target})
    assert _body(after, target) == "done", after


def test_status_field_writer_rewrites_populated_status_with_the_same_value(tmp_path: pathlib.Path) -> None:
    before = "# BUG-0001\n\n## Status\nopen\n\n## Category\nwake\n"
    after = _set_status_field(tmp_path, before, "running")
    assert after == "# BUG-0001\n\n## Status\nrunning\n\n## Category\nwake\n"
    # A second write does not grow the separator.
    assert _set_status_field(tmp_path, after, "running") == after


def test_status_field_writer_rewrites_a_last_status_section(tmp_path: pathlib.Path) -> None:
    assert _set_status_field(tmp_path, "# BUG-0001\n\n## Status\nopen\n") == "# BUG-0001\n\n## Status\ndone\n"
    assert _set_status_field(tmp_path, "# BUG-0001\n\n## Status\n") == "# BUG-0001\n\n## Status\ndone\n"


def test_status_field_writer_still_inserts_an_absent_status(tmp_path: pathlib.Path) -> None:
    after = _set_status_field(tmp_path, "# BUG-0001\n\n## Category\nwake\n")
    assert after == "# BUG-0001\n\n## Status\ndone\n\n## Category\nwake\n"


# --- wake_driver_finalize.sh pollution-sweep Summary append ------------------


def _append_finding(tmp_path: pathlib.Path, text: str, finding: str = "POLLUTION SWEEP WARNING: x") -> str:
    status = tmp_path / "status.md"
    status.write_text(text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_FINALIZE}'\n_wd_append_summary_finding '{status}' '{finding}'",
    )
    assert result.returncode == 0, _explain(result)
    return status.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "before",
    [
        "## State\nDONE\n\n## Summary\n\n## Needs Human\nno\n\n## Blockers\nnone\n",
        "## State\nDONE\n\n## Summary\n## Needs Human\nno\n",
        "## Summary\n\n\n## Needs Human\nno\n",
    ],
    ids=["blank-line-then-next", "next-heading-directly", "first-section"],
)
def test_summary_append_fills_empty_non_last_summary_in_place(tmp_path: pathlib.Path, before: str) -> None:
    after = _append_finding(tmp_path, before, "FINDING")
    _assert_untouched(before, after, {"## Summary"})
    assert _body(after, "## Summary") == "FINDING", after


def test_summary_append_keeps_existing_summary_then_adds_the_finding(tmp_path: pathlib.Path) -> None:
    before = "## State\nDONE\n\n## Summary\nold summary\n\n## Needs Human\nno\n"
    after = _append_finding(tmp_path, before, "FINDING")
    assert after == "## State\nDONE\n\n## Summary\nold summary\n\nFINDING\n\n## Needs Human\nno\n"


def test_summary_append_on_a_last_summary_section(tmp_path: pathlib.Path) -> None:
    assert _append_finding(tmp_path, "## State\nDONE\n\n## Summary\nold\n", "F") == (
        "## State\nDONE\n\n## Summary\nold\n\nF\n"
    )
    assert _append_finding(tmp_path, "## State\nDONE\n\n## Summary\n", "F") == "## State\nDONE\n\n## Summary\nF\n"


def test_summary_append_leaves_a_file_without_summary_unchanged(tmp_path: pathlib.Path) -> None:
    before = "## State\nDONE\n\n## Needs Human\nno\n"
    assert _append_finding(tmp_path, before) == before


# --- check-readme-bundled.sh _crb_do_remediate --------------------------------

_BAD_INPUT = "requirements/README.md"


def _remediate(tmp_path: pathlib.Path, text: str) -> str:
    task_dir = tmp_path / "CLAUDE-PM-20260101-001-example"
    bugs_dir = tmp_path / "bugs"
    task_dir.mkdir()
    bugs_dir.mkdir()
    (task_dir / "status.md").write_text(text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\n{_OW_STUBS}source '{_CHECK_README_BUNDLED}'\n"
        f"_CRB_TASK_ID='{task_dir.name}' _CRB_TASK_DIR='{task_dir}' _CRB_BAD_INPUT='{_BAD_INPUT}' "
        f"_CRB_BUGS_DIR='{bugs_dir}' _CRB_BUG_NUM=0001 _crb_do_remediate",
    )
    assert result.returncode == 0, _explain(result)
    return (task_dir / "status.md").read_text(encoding="utf-8")


def _pm_status(empty: str, with_reason: bool = False) -> str:
    bodies = [
        ("Task", "CLAUDE-PM-20260101-001-example"),
        ("State", "WORKING"),
        ("Summary", "old summary"),
        ("Artifacts", "keep me"),
        ("Blockers", "none"),
        ("Needs Human", "no"),
    ]
    if with_reason:
        bodies.append(("Blocked Reason", "old reason"))
    bodies.append(("Next Recommended Step", "keep me too"))
    return "# Status\n" + "".join(
        f"\n## {h}\n" + ("" if h == empty else f"{b}\n") for h, b in bodies
    )


def _assert_remediated(after: str) -> None:
    assert _body(after, "## State") == "WONT-DO", after
    assert _body(after, "## Needs Human") == "no", after
    assert _body(after, "## Summary").startswith("PM task marked WONT-DO by OVERWATCH check-readme-bundled"), after
    assert _body(after, "## Blocked Reason").startswith(f"Input path points at a README.md or templates/ file: {_BAD_INPUT}."), after


@pytest.mark.parametrize("empty", ["State", "Summary", "Needs Human"])
def test_readme_input_remediation_fills_an_empty_non_last_section_without_dropping_others(
    tmp_path: pathlib.Path, empty: str
) -> None:
    before = _pm_status(empty)
    after = _remediate(tmp_path, before)
    _assert_untouched(before, after, {"## State", "## Summary", "## Needs Human"})
    _assert_remediated(after)


def test_readme_input_remediation_fills_an_empty_non_last_blocked_reason(tmp_path: pathlib.Path) -> None:
    before = _pm_status("Blocked Reason", with_reason=True)
    after = _remediate(tmp_path, before)
    _assert_untouched(before, after, {"## State", "## Summary", "## Needs Human", "## Blocked Reason"})
    _assert_remediated(after)


def test_readme_input_remediation_inserts_blocked_reason_directly_after_an_empty_blockers(
    tmp_path: pathlib.Path,
) -> None:
    before = _pm_status("Blockers")
    after = _remediate(tmp_path, before)
    _assert_untouched(before, after, {"## State", "## Summary", "## Needs Human"})
    headings = list(_sections(after))
    assert headings[headings.index("## Blockers") + 1] == "## Blocked Reason", headings
    assert _body(after, "## Blockers") == "", after
    _assert_remediated(after)


def test_readme_input_remediation_rewrites_populated_sections_exactly_as_before(tmp_path: pathlib.Path) -> None:
    after = _remediate(tmp_path, _pm_status(empty=""))
    summary = _body(after, "## Summary")
    reason = _body(after, "## Blocked Reason")
    assert after == (
        "# Status\n\n## Task\nCLAUDE-PM-20260101-001-example\n\n## State\nWONT-DO\n\n"
        f"## Summary\n{summary}\n\n## Artifacts\nkeep me\n\n## Blockers\nnone\n\n"
        f"## Blocked Reason\n{reason}\n\n## Needs Human\nno\n\n## Next Recommended Step\nkeep me too\n"
    )
