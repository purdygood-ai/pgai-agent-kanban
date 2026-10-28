"""
test_bug_filing.py
====================
Unit tests for pgai_agent_kanban.lib.bug_filing -- the shared bug-filing
primitives the autonomous filers (lint, deep-verify, ship-policy) import
(docs/coding-standards.md directive 1: BUG-NNNN claiming, the live-bug
bugs_dir scan, the record shape and the existing-bug lookup are not
reimplemented per filer).

Coverage:
  - read_status() reads the '## Status' section, defaulting to "open" when
    absent (a malformed bug file is treated as live, never silently
    excluded from dedup).
  - iter_open_or_running_bugs() yields only files matching the
    "BUG-NNNN-*.md" naming convention, in filename-sorted order, filtered
    to the eligible (open/running/needs-operator) statuses.
  - iter_open_or_running_bugs() on a missing bugs_dir yields nothing rather
    than raising.
  - import_bug_scanner() resolves the real claim_next_bug_id /
    release_bug_id_claim callables from team/pm-agent/lib/bug_scanner.py.
  - The claim path, and discovery's BUG-ID collision report, still work
    when an unrelated namespace 'lib' (shaped like team/scripts/lib/) is
    first on sys.path and already imported as the top-level 'lib', and
    neither call changes sys.path or the cached 'lib' module.
  - load_bug_scanner() on a directory without lib/bug_scanner.py raises
    ImportError naming the path it looked at.
  - render_bug_record() writes the BUG-TEMPLATE.md header and sections,
    then the dedup-key section (a string as is, a tuple one line each) and
    the extra sections, one blank line apart, ending in one newline.
  - find_existing_bug() matches a live bug whose key section records the
    key: a string against the section's value, a set against its lines in
    any order; a closed bug, another heading, an empty set or a missing
    bugs_dir match nothing.
  - file_or_cite_record() files the rendered record under a claimed slot,
    then cites it on a second call writing nothing; it cites a hand-filed
    live bug the same-defect rule matches, and a closed bug recording the
    key never suppresses a filing.

Fixture bug IDs are in the fictitious BUG-9xxx range.

Test names describe behaviour under test, never bug/task IDs (team/roles/
TESTER.md, Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import io
import pathlib
import sys

import pytest

from pgai_agent_kanban.discovery import items
from pgai_agent_kanban.lib import bug_filing
from pgai_agent_kanban.lib.bug_filing import (
    ELIGIBLE_DEDUP_STATUSES,
    BugRecord,
    file_or_cite_record,
    find_existing_bug,
    import_bug_scanner,
    iter_open_or_running_bugs,
    load_bug_scanner,
    own_pm_agent_dir,
    read_status,
    render_bug_record,
)
from tests.fixtures.hand_filed_bug import CLOSED_STATUSES, write_hand_filed_bug


def _write_bug(bugs_dir: pathlib.Path, name: str, *, status: "str | None") -> None:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    body = "" if status is None else f"## Status\n{status}\n\n"
    (bugs_dir / name).write_text(f"# fixture\n\n{body}", encoding="utf-8")


def test_read_status_reads_the_status_section() -> None:
    assert read_status("## Status\nopen\n") == "open"
    assert read_status("## Status\nRUNNING\n") == "running"


def test_read_status_defaults_to_open_when_section_absent() -> None:
    assert read_status("no status section here at all") == "open"


def test_eligible_dedup_statuses_are_the_active_intake_states() -> None:
    assert ELIGIBLE_DEDUP_STATUSES == frozenset({"open", "running", "needs-operator"})


def test_iter_open_or_running_bugs_yields_only_eligible_statuses(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0001-a.md", status="open")
    _write_bug(bugs_dir, "BUG-0002-b.md", status="running")
    _write_bug(bugs_dir, "BUG-0003-c.md", status="done")
    _write_bug(bugs_dir, "BUG-0004-d.md", status="wont-do")
    _write_bug(bugs_dir, "BUG-0005-e.md", status="needs-operator")
    _write_bug(bugs_dir, "BUG-0006-f.md", status="duplicate")
    (bugs_dir / "not-a-bug-file.md").write_text("ignored", encoding="utf-8")

    found = sorted(bug_id for bug_id, _text in iter_open_or_running_bugs(str(bugs_dir)))
    assert found == ["BUG-0001", "BUG-0002", "BUG-0005"]


def test_iter_open_or_running_bugs_is_filename_sorted(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0010-z.md", status="open")
    _write_bug(bugs_dir, "BUG-0002-a.md", status="open")

    found = [bug_id for bug_id, _text in iter_open_or_running_bugs(str(bugs_dir))]
    assert found == ["BUG-0002", "BUG-0010"]


def test_iter_open_or_running_bugs_on_missing_dir_yields_nothing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "bugs"
    assert list(iter_open_or_running_bugs(str(missing))) == []


def test_import_bug_scanner_resolves_the_real_claim_helpers() -> None:
    claim_next_bug_id, release_bug_id_claim = import_bug_scanner()
    assert callable(claim_next_bug_id)
    assert callable(release_bug_id_claim)


def test_import_bug_scanner_claim_round_trip(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    claim_next_bug_id, release_bug_id_claim = import_bug_scanner()

    bug_id, bug_path, lock_path = claim_next_bug_id(str(bugs_dir), "a-test-slug")
    try:
        assert bug_id.startswith("BUG-")
        assert str(bug_path).endswith("a-test-slug.md")
    finally:
        release_bug_id_claim(lock_path)


def _is_lib_module(name: str) -> bool:
    return name == "lib" or name.startswith("lib.")


@pytest.fixture
def shadowing_lib_namespace(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Make the top-level name 'lib' resolve to an empty namespace directory.

    Mirrors a process that has team/scripts/ (whose lib/ has no
    __init__.py) on sys.path: a directory holding an empty 'lib/' goes
    first on sys.path and 'lib' is imported from it. Other sys.path entries
    that carry a lib/ directory are left out for the duration, so the
    shadow wins whatever earlier tests put on sys.path. The loader cache in
    bug_filing starts empty, so the load really happens under the shadow.

    sys.path (via monkeypatch) and every 'lib' / 'lib.*' entry in
    sys.modules are restored afterwards.
    """
    shadow_root = tmp_path / "shadow"
    (shadow_root / "lib").mkdir(parents=True)
    saved_modules = {name: mod for name, mod in sys.modules.items() if _is_lib_module(name)}
    for name in saved_modules:
        del sys.modules[name]
    other_entries = [p for p in sys.path if not (pathlib.Path(p or ".") / "lib").is_dir()]
    monkeypatch.setattr(sys, "path", [str(shadow_root)] + other_entries)
    monkeypatch.setattr(bug_filing, "_BUG_SCANNER_CACHE", {})
    try:
        import lib  # type: ignore[import-not-found]

        assert list(lib.__path__) == [str(shadow_root / "lib")]
        yield lib
    finally:
        for name in [n for n in sys.modules if _is_lib_module(n)]:
            del sys.modules[name]
        sys.modules.update(saved_modules)


def test_claim_path_succeeds_while_a_namespace_lib_shadows_the_top_level_name(
    shadowing_lib_namespace, tmp_path: pathlib.Path
) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    path_before = list(sys.path)

    claim_next_bug_id, release_bug_id_claim = import_bug_scanner()
    bug_id, bug_path, lock_path = claim_next_bug_id(str(bugs_dir), "shadowed-lib-slug")
    try:
        assert bug_id == "BUG-0001"
        assert str(bug_path).endswith("shadowed-lib-slug.md")
        assert pathlib.Path(lock_path).exists()
    finally:
        release_bug_id_claim(lock_path)

    assert not pathlib.Path(lock_path).exists()
    assert sys.path == path_before
    assert sys.modules["lib"] is shadowing_lib_namespace


def test_collision_report_works_while_a_namespace_lib_shadows_the_top_level_name(
    shadowing_lib_namespace, tmp_path: pathlib.Path
) -> None:
    bugs_dir = tmp_path / "bugs"
    _write_bug(bugs_dir, "BUG-0007-first.md", status="open")
    _write_bug(bugs_dir, "BUG-0007-second.md", status="open")
    path_before = list(sys.path)
    err = io.StringIO()

    items.report_bug_id_collisions(str(bugs_dir), str(own_pm_agent_dir()), err=err)

    report = err.getvalue()
    assert "could not import bug_scanner" not in report
    assert "numeric prefix 0007" in report
    assert sys.path == path_before
    assert sys.modules["lib"] is shadowing_lib_namespace


def test_load_bug_scanner_without_the_file_raises_import_error_naming_the_path(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(ImportError) as excinfo:
        load_bug_scanner(tmp_path)
    assert str((tmp_path / "lib" / "bug_scanner.py").resolve()) in str(excinfo.value)


# ---------------------------------------------------------------------------
# Bug records: render_bug_record, find_existing_bug, file_or_cite_record
# ---------------------------------------------------------------------------


def _record(**overrides) -> BugRecord:
    fields = dict(
        slug="widget-drops-input",
        filed_by="example-filer (autonomous) -- the widget dropped 2 inputs.",
        filed_at="2026-10-25",
        severity="low",
        category="example",
        symptom="The widget drops input.",
        expected="No input is dropped.",
        actual="- input 1\n- input 2",
        reproduction="1. Type into the widget.",
        files_involved="- widget.py",
        hypothesis="Speculative.",
        key_heading="Example Key",
        key="widget|input",
        extra_sections=(("Example Run", "- Host: a"), ("Notes", "none")),
    )
    fields.update(overrides)
    return BugRecord(**fields)


def test_render_bug_record_writes_the_template_shape_then_key_and_extra_sections() -> None:
    assert render_bug_record("BUG-9301", _record()) == (
        "# BUG-9301-widget-drops-input\n\n"
        "**Bug ID:** BUG-9301-widget-drops-input\n"
        "**Filed By:** example-filer (autonomous) -- the widget dropped 2 inputs.\n"
        "**Date:** 2026-10-25\n"
        "**Severity:** low\n\n"
        "## Status\nopen\n\n"
        "## Category\nexample\n\n"
        "---\n\n"
        "## Symptom\nThe widget drops input.\n\n"
        "## Expected\nNo input is dropped.\n\n"
        "## Actual\n- input 1\n- input 2\n\n"
        "## Reproduction\n1. Type into the widget.\n\n"
        "## Files Involved\n- widget.py\n\n"
        "## Hypothesis (optional)\nSpeculative.\n\n"
        "---\n\n"
        "## Example Key\nwidget|input\n\n"
        "## Example Run\n- Host: a\n\n"
        "## Notes\nnone\n"
    )


def test_render_bug_record_writes_a_tuple_key_one_line_each_and_an_empty_one_empty() -> None:
    text = render_bug_record("BUG-9301", _record(key=("b/x", "a/y"), extra_sections=()))
    assert text.endswith("---\n\n## Example Key\nb/x\na/y\n")

    empty = render_bug_record("BUG-9301", _record(key=(), extra_sections=()))
    assert empty.endswith("---\n\n## Example Key\n\n")


def test_find_existing_bug_matches_a_string_key_against_the_section_value(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    (bugs_dir / "BUG-9301-widget-drops-input.md").write_text(render_bug_record("BUG-9301", _record()))

    assert find_existing_bug(str(bugs_dir), "Example Key", "widget|input") == "BUG-9301"
    assert find_existing_bug(str(bugs_dir), "Example Key", "widget|output") is None
    assert find_existing_bug(str(bugs_dir), "Other Key", "widget|input") is None


def test_find_existing_bug_matches_a_set_key_in_any_order(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    (bugs_dir / "BUG-9301-widget-drops-input.md").write_text(
        render_bug_record("BUG-9301", _record(key=("b/x", "a/y")))
    )

    assert find_existing_bug(str(bugs_dir), "Example Key", {"a/y", "b/x"}) == "BUG-9301"
    assert find_existing_bug(str(bugs_dir), "Example Key", ("a/y",)) is None
    assert find_existing_bug(str(bugs_dir), "Example Key", frozenset()) is None


@pytest.mark.parametrize("status", CLOSED_STATUSES)
def test_find_existing_bug_ignores_a_closed_bug(tmp_path: pathlib.Path, status: str) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    text = render_bug_record("BUG-9301", _record()).replace("## Status\nopen\n", f"## Status\n{status}\n")
    (bugs_dir / "BUG-9301-widget-drops-input.md").write_text(text)

    assert find_existing_bug(str(bugs_dir), "Example Key", "widget|input") is None


def test_find_existing_bug_on_a_missing_bugs_dir_matches_nothing(tmp_path: pathlib.Path) -> None:
    assert find_existing_bug(str(tmp_path / "absent"), "Example Key", "widget|input") is None


def test_file_or_cite_record_files_the_rendered_record_then_cites_it(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    record = _record()

    filed = file_or_cite_record(str(bugs_dir), record, claimed_by="example-filer")

    assert filed.outcome == "filed"
    path = pathlib.Path(filed.bug_path)
    assert path.name == f"{filed.bug_id}-widget-drops-input.md"
    assert path.read_text(encoding="utf-8") == render_bug_record(filed.bug_id, record)

    before = sorted(p.name for p in bugs_dir.iterdir())
    again = file_or_cite_record(str(bugs_dir), record, claimed_by="example-filer")

    assert (again.outcome, again.bug_id, again.bug_path) == ("duplicate", filed.bug_id, None)
    assert sorted(p.name for p in bugs_dir.iterdir()) == before


def test_file_or_cite_record_cites_a_live_bug_the_same_defect_rule_matches(tmp_path: pathlib.Path) -> None:
    bugs_dir = tmp_path / "bugs"
    write_hand_filed_bug(bugs_dir, "BUG-9302", "widget-drops-input-again")

    cited = file_or_cite_record(str(bugs_dir), _record(), claimed_by="example-filer")

    assert (cited.outcome, cited.bug_id, cited.bug_path) == ("duplicate", "BUG-9302", None)
    assert [p.name for p in bugs_dir.iterdir()] == ["BUG-9302-widget-drops-input-again.md"]


@pytest.mark.parametrize("status", CLOSED_STATUSES)
def test_file_or_cite_record_files_past_a_closed_bug_recording_the_key(tmp_path: pathlib.Path, status: str) -> None:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    text = render_bug_record("BUG-9301", _record()).replace("## Status\nopen\n", f"## Status\n{status}\n")
    (bugs_dir / "BUG-9301-widget-drops-input.md").write_text(text)

    filed = file_or_cite_record(str(bugs_dir), _record(), claimed_by="example-filer")

    assert filed.outcome == "filed"
    assert filed.bug_id != "BUG-9301"
