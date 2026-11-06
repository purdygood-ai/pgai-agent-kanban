"""
test_ship_policy_bug_filer.py
==============================
Unit tests for pgai_agent_kanban.cm.ship_policy_bug_filer -- the CM
ship-policy stage's input-defect bug filer (release_ship_policy.sh Step 4d's
filed-bug path for an unparseable report value or a TESTER-task lookup field
that matched only after normalization).

Coverage:
  - An unparseable report value files exactly one bug that follows the
    BUG-TEMPLATE.md shape (Status open, Category cm, a severity from the
    canonical vocabulary, a self-documenting Filed By line) and quotes the
    raw field text and the report path.
  - A lookup-field mismatch files a bug quoting the raw field and the
    README path, under its own slug.
  - A second filing for the same (kind, field, source path) while the first
    bug is still open or running cites it and files no duplicate; a bug
    whose Status is done no longer suppresses; a different field or a
    different source path is a fresh filing.
  - A needs-operator bug for the same input is cited like an open one:
    the CLI prints its ID and nothing is filed.
  - A hand-filed open or running bug the shared same-defect rule matches
    (an ordinal-variant slug, or a report that cites it as its defect) is
    cited although the input key does not match it; closed bugs (done,
    wont-do, duplicate) matching by key or slug never suppress a filing.
  - An unknown kind is refused, never filed.
  - The CLI prints outcome/bug_id/bug_path lines and exits 0; it exits 1
    with a reason on stderr when nothing could be filed.

Every assertion is against the pure file_or_cite_input_bug() function or
the module's own main() operating on a tmp_path bugs/ directory -- no
release script, no subprocess. tmp_path is redirected under
$PGAI_AGENT_KANBAN_TEMP_DIR by team/tests/conftest.py's pytest_configure.

Test names describe behaviour under test, never bug/task IDs (team/roles/
TESTER.md, Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.cm.ship_policy_bug_filer import (
    KIND_LOOKUP_FIELD,
    KIND_REPORT_VALUE,
    FilingResult,
    file_or_cite_input_bug,
    input_key,
    main,
)
from pgai_agent_kanban.lib.bug_filing import find_existing_bug
from tests.fixtures.hand_filed_bug import CLOSED_STATUSES, write_hand_filed_bug


def _file(bugs_dir: pathlib.Path, **overrides) -> FilingResult:
    kwargs = dict(
        project="demo",
        version="v1.2.3",
        kind=KIND_REPORT_VALUE,
        field="Recommendation",
        raw_value="Recommendation: SHIPP-WITH-CONCERNZ",
        source_path="/kanban/projects/demo/tasks/TESTER-20260601-001-verify/artifacts/report.md",
        filed_at="2026-09-01",
    )
    kwargs.update(overrides)
    return file_or_cite_input_bug(str(bugs_dir), **kwargs)


def _set_status(bug_path: pathlib.Path, status: str) -> None:
    text = bug_path.read_text(encoding="utf-8")
    bug_path.write_text(text.replace("## Status\nopen", f"## Status\n{status}"), encoding="utf-8")


def test_unparseable_report_value_files_one_bug_quoting_raw_text_and_report_path(tmp_path):
    bugs_dir = tmp_path / "bugs"
    result = _file(bugs_dir)

    assert result.outcome == "filed"
    assert result.bug_id.startswith("BUG-")
    files = sorted(bugs_dir.glob("BUG-*.md"))
    assert len(files) == 1
    assert files[0].name.endswith("-unparseable-recommendation-value.md")
    content = files[0].read_text(encoding="utf-8")
    assert "Recommendation: SHIPP-WITH-CONCERNZ" in content
    assert "/kanban/projects/demo/tasks/TESTER-20260601-001-verify/artifacts/report.md" in content
    assert "## Status\nopen" in content
    assert "## Category\ncm" in content
    assert "**Filed By:** cm-release ship-policy (autonomous) --" in content
    assert any(f"**Severity:** {sev}" in content for sev in ("critical", "high", "medium", "low"))
    assert "SHIP-WITH-CONCERNS" in content
    assert "v1.2.3" in content
    assert (
        "## Ship-Policy Input\n"
        + input_key(
            KIND_REPORT_VALUE,
            "Recommendation",
            "/kanban/projects/demo/tasks/TESTER-20260601-001-verify/artifacts/report.md",
        )
        in content
    )


def test_lookup_field_mismatch_files_under_its_own_slug_quoting_the_readme(tmp_path):
    bugs_dir = tmp_path / "bugs"
    result = _file(
        bugs_dir,
        kind=KIND_LOOKUP_FIELD,
        field="Release Version",
        raw_value="`v1.2.3` (bugfix bundle)",
        source_path="/kanban/projects/demo/tasks/TESTER-20260601-001-verify/README.md",
    )
    assert result.outcome == "filed"
    filed = pathlib.Path(result.bug_path)
    assert filed.name.endswith("-tester-lookup-release-version.md")
    content = filed.read_text(encoding="utf-8")
    assert "`v1.2.3` (bugfix bundle)" in content
    assert "/kanban/projects/demo/tasks/TESTER-20260601-001-verify/README.md" in content
    assert "only after normalization" in content


def test_second_filing_for_the_same_input_cites_the_open_bug_and_files_no_duplicate(tmp_path):
    bugs_dir = tmp_path / "bugs"
    first = _file(bugs_dir)
    second = _file(bugs_dir)
    assert second.outcome == "duplicate"
    assert second.bug_id == first.bug_id
    assert second.bug_path is None
    assert len(list(bugs_dir.glob("BUG-*.md"))) == 1


def test_a_running_bug_for_the_same_input_still_suppresses(tmp_path):
    bugs_dir = tmp_path / "bugs"
    first = _file(bugs_dir)
    _set_status(pathlib.Path(first.bug_path), "running")
    assert _file(bugs_dir).outcome == "duplicate"


def test_a_done_bug_for_the_same_input_no_longer_suppresses(tmp_path):
    bugs_dir = tmp_path / "bugs"
    first = _file(bugs_dir)
    _set_status(pathlib.Path(first.bug_path), "done")
    second = _file(bugs_dir)
    assert second.outcome == "filed"
    assert second.bug_id != first.bug_id
    assert len(list(bugs_dir.glob("BUG-*.md"))) == 2


def test_a_different_field_or_source_path_is_a_fresh_filing(tmp_path):
    bugs_dir = tmp_path / "bugs"
    _file(bugs_dir)
    other_field = _file(bugs_dir, field="Systemic Risk", raw_value="hgih")
    other_path = _file(
        bugs_dir, source_path="/kanban/projects/demo/tasks/TESTER-20260602-002-verify/artifacts/report.md"
    )
    assert other_field.outcome == "filed"
    assert other_path.outcome == "filed"
    assert len({r.bug_id for r in (other_field, other_path)}) == 2


def test_input_lookup_returns_none_when_bugs_dir_absent(tmp_path):
    key = input_key(KIND_REPORT_VALUE, "Recommendation", "/nowhere/report.md")
    assert find_existing_bug(str(tmp_path / "missing"), "Ship-Policy Input", key) is None


def test_unknown_kind_is_refused_and_nothing_is_filed(tmp_path):
    bugs_dir = tmp_path / "bugs"
    with pytest.raises(ValueError):
        _file(bugs_dir, kind="banana")
    assert not list(bugs_dir.glob("BUG-*.md")) if bugs_dir.exists() else True


def test_cli_prints_outcome_lines_and_exits_zero(tmp_path, capsys):
    bugs_dir = tmp_path / "bugs"
    rc = main(
        [
            str(bugs_dir),
            "demo",
            "v1.2.3",
            KIND_REPORT_VALUE,
            "Systemic Risk",
            "hgih",
            "/kanban/report.md",
        ]
    )
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out[0] == "outcome=filed"
    assert out[1].startswith("bug_id=BUG-")
    assert out[2].startswith("bug_path=") and out[2].endswith("-unparseable-systemic-risk-value.md")

    rc = main(
        [
            str(bugs_dir),
            "demo",
            "v1.2.3",
            KIND_REPORT_VALUE,
            "Systemic Risk",
            "hgih",
            "/kanban/report.md",
        ]
    )
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert out[0] == "outcome=duplicate"
    assert out[2] == "bug_path="


def test_cli_exits_one_with_a_reason_when_nothing_can_be_filed(tmp_path, capsys):
    """A bugs_dir path that is an existing FILE cannot become a directory;
    the CLI reports why on stderr and exits 1 instead of raising."""
    not_a_dir = tmp_path / "bugs"
    not_a_dir.write_text("", encoding="utf-8")
    rc = main([str(not_a_dir), "demo", "v1.2.3", KIND_REPORT_VALUE, "Recommendation", "x", "/r.md"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "nothing filed" in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------------
# Live statuses and the shared same-defect rule
# ---------------------------------------------------------------------------


def test_needs_operator_bug_for_the_same_input_is_cited_and_nothing_is_filed(tmp_path, capsys):
    bugs_dir = tmp_path / "bugs"
    first = _file(bugs_dir)
    _set_status(pathlib.Path(first.bug_path), "needs-operator")

    rc = main(
        [
            str(bugs_dir),
            "demo",
            "v1.2.4",
            KIND_REPORT_VALUE,
            "Recommendation",
            "Recommendation: SHIPP-WITH-CONCERNZ",
            "/kanban/projects/demo/tasks/TESTER-20260601-001-verify/artifacts/report.md",
        ]
    )
    out = capsys.readouterr().out.splitlines()

    assert rc == 0
    assert out == ["outcome=duplicate", f"bug_id={first.bug_id}", "bug_path="]
    assert sorted(bugs_dir.glob("BUG-*.md")) == [pathlib.Path(first.bug_path)]


@pytest.mark.parametrize("status", ["open", "running"])
def test_live_bug_with_an_ordinal_variant_slug_is_cited_and_nothing_is_filed(tmp_path, status):
    bugs_dir = tmp_path / "bugs"
    hand_filed = write_hand_filed_bug(bugs_dir, "BUG-9201", "unparseable-recommendation-value-again", status=status)

    result = _file(bugs_dir)

    assert find_existing_bug(str(bugs_dir), "Ship-Policy Input", result.key) is None
    assert result.outcome == "duplicate"
    assert result.bug_id == "BUG-9201"
    assert result.bug_path is None
    assert sorted(bugs_dir.glob("BUG-*.md")) == [hand_filed]


def test_live_bug_the_drafted_report_cites_as_its_defect_is_cited(tmp_path):
    bugs_dir = tmp_path / "bugs"
    write_hand_filed_bug(bugs_dir, "BUG-9202", "release-step-misreads-report")

    result = _file(bugs_dir, raw_value="same defect as BUG-9202")

    assert result.outcome == "duplicate"
    assert result.bug_id == "BUG-9202"
    assert len(list(bugs_dir.glob("BUG-*.md"))) == 1


@pytest.mark.parametrize("status", CLOSED_STATUSES)
def test_closed_bugs_matching_by_key_or_slug_do_not_suppress_a_fresh_filing(tmp_path, status):
    bugs_dir = tmp_path / "bugs"
    same_key = _file(bugs_dir)
    _set_status(pathlib.Path(same_key.bug_path), status)
    write_hand_filed_bug(bugs_dir, "BUG-9203", "unparseable-recommendation-value-again", status=status)

    result = _file(bugs_dir)

    assert result.outcome == "filed"
    assert result.bug_id not in (same_key.bug_id, "BUG-9203")
    assert len(list(bugs_dir.glob("BUG-*.md"))) == 3
