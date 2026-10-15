"""
test_promote_bundled_items_carries_unfixed.py
================================================
Regression coverage for the ## Bundled Items enumeration path in
`team/scripts/cm/promote_bundled_items.py`: closing an item is a claim its
fix shipped, so the enumeration path must ask whether some DONE task's
README actually references the item before promoting it to 'done' -- never
promote on the strength of enumeration alone.

Fixture identifiers throughout this file use the codebase's established
fictitious-example numbering (the BUG-9001/PRIORITY-9001-style range other
suites use for synthetic fixtures, e.g. test_operation_contracts.py and
test_overwatch_fix_rewrite.py) so a reader never mistakes a fixture for a
citation of a real tracked item.

Mixed-bundle scenario (one fixed item, one withdrawn item, one release):
  - The first bundled bug has a task that reached DONE and whose README
    names it. Expected: promoted to 'done', unchanged from prior behaviour.
  - The second bundled bug has a task that ended WONT-DO with zero commits
    against it -- modeled here simply as "no DONE task references it,"
    since the check reads task state, never commits or diffs. Expected:
    left at 'running' (its prior status),
    named as carried in both the return value and the printed post-ship
    report, and NOT rewritten to any other state.
  - The bundle file's own ## Status still promotes to 'done' regardless of
    the carried count -- the release still ships.

A second scenario proves a PRIORITY-*.md item is gated by the same rule
(the primary path enumerates both BUG-*.md and PRIORITY-*.md per the
module's own docstring), and further scenarios prove the safe-by-default
direction when tasks_dir is not supplied at all (nothing can be verified,
so nothing in ## Bundled Items is promoted), and that a carried item stays
carried -- never flips state -- on a later idempotent re-run.

Further scenarios prove that a DONE README which names an item only to say
it is not ticketed, out of scope, deferred or skipped does not claim it --
on the enumeration path and the derivation path alike -- while the README
shape the task materializer writes (the item's file path under ## Inputs)
still claims every item it lists.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the real module under test directly from its file, the same way
# test_docs_citation_manifest_lint_wired.py imports check_docs_citations.py --
# team/scripts/cm/ is not a package on sys.path, and this file exercises the
# shipped script itself, never a copy of its logic.
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_PROMOTE_SCRIPT = _TEAM_DIR / "scripts" / "cm" / "promote_bundled_items.py"


def _import_promote_bundled_items_module():
    spec = importlib.util.spec_from_file_location("promote_bundled_items", _PROMOTE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_PROMOTE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_pbi = _import_promote_bundled_items_module()


def _status_of(p: Path) -> str:
    """Read the raw ## Status value out of a markdown fixture file."""
    value = _pbi.read_status_value(p)
    assert value is not None, f"{p} carries no ## Status field"
    return value.strip().lower()


def _write_task(tasks_dir: Path, name: str, *, state: str, readme_body: str, release_version: str) -> None:
    """A task folder of *release_version*: only a DONE task of the shipped
    version is evidence that an item's fix shipped."""
    task_dir = tasks_dir / name
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"{readme_body}\n## Release Version\n{release_version}\n", encoding="utf-8"
    )
    (task_dir / "status.md").write_text(f"## State\n{state}\n", encoding="utf-8")


def _write_item(items_dir: Path, filename: str) -> Path:
    """Write a synthetic BUG-*.md or PRIORITY-*.md fixture at 'running'."""
    p = items_dir / filename
    p.write_text(f"# {filename}\n\n## Status\nrunning\n\n## Category\ntest\n", encoding="utf-8")
    return p


def _write_bundle(req_dir: Path, name: str, item_paths: list[Path]) -> Path:
    lines = ["## Status", "running", "", "## Bundled Items", ""]
    for item_path in item_paths:
        lines.append(f"- {item_path.name} (`{item_path}`)")
    lines += ["", "## Acceptance Criteria", ""]
    p = req_dir / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Mixed bundle: one fixed item, one withdrawn item, one release.
# ---------------------------------------------------------------------------

def test_mixed_bundle_promotes_fixed_and_carries_withdrawn(tmp_path, capsys):
    tasks_dir = tmp_path / "tasks"
    bugs_dir = tmp_path / "bugs"
    req_dir = tmp_path / "requirements"
    tasks_dir.mkdir()
    bugs_dir.mkdir()
    req_dir.mkdir()

    fixed_bug = _write_item(bugs_dir, "BUG-9101-fixed-thing.md")
    withdrawn_bug = _write_item(bugs_dir, "BUG-9102-withdrawn-thing.md")

    # The fixed item's ticket landed a real commit and reached DONE, and its
    # README names the bug it closed.
    _write_task(
        tasks_dir,
        "CODER-20260926-101-fix-thing",
        release_version="v9.9.9",
        state="DONE",
        readme_body="# Task\nFixes BUG-9101-fixed-thing.\n",
    )
    # The withdrawn item's ticket ended WONT-DO with zero commits against
    # the defect -- modeled as simply having no DONE task reference it; the
    # check reads task state, never commits or diffs.
    _write_task(
        tasks_dir,
        "CODER-20260926-102-withdrawn-thing",
        release_version="v9.9.9",
        state="WONT-DO",
        readme_body="# Task\nWas going to fix BUG-9102-withdrawn-thing.\n",
    )

    bundle_file = _write_bundle(
        req_dir,
        "v9.9.9-bugfix-bundle-test.md",
        [fixed_bug, withdrawn_bug],
    )

    result = _pbi.promote_bundle(
        bundle_file,
        "v9.9.9",
        tasks_dir=tasks_dir,
        bugs_dir=bugs_dir,
    )

    # The fixed item promotes to done -- unchanged from prior behaviour.
    assert _status_of(fixed_bug) == "done"
    assert result["promoted"] == 1

    # The withdrawn item is left at its PRIOR status, never rewritten to any
    # other state, and reported as carried rather than closed.
    assert _status_of(withdrawn_bug) == "running"
    assert result["carried"] == 1
    assert result["carried_items"] == ["BUG-9102-withdrawn-thing.md"]

    # The bundle file's own status promotion still happens regardless of the
    # carried count -- the release still ships.
    assert _status_of(bundle_file) == "done"
    assert result["bundle_file_promoted"] is True

    # The post-ship report names the carried item and states the count.
    out = capsys.readouterr().out
    assert "Carried (not closed): 1 item(s)" in out
    assert "BUG-9102-withdrawn-thing.md" in out
    assert "carried" in out.lower()


def test_fixed_item_unchanged_from_prior_behaviour(tmp_path):
    """An item whose fix ticket reached DONE promotes exactly as before --
    the new gate does not regress the happy path."""
    tasks_dir = tmp_path / "tasks"
    bugs_dir = tmp_path / "bugs"
    req_dir = tmp_path / "requirements"
    tasks_dir.mkdir()
    bugs_dir.mkdir()
    req_dir.mkdir()

    fixed_bug = _write_item(bugs_dir, "BUG-9103-single-fixed-thing.md")
    _write_task(
        tasks_dir,
        "CODER-20260926-103-fix-thing",
        release_version="v9.9.11",
        state="DONE",
        readme_body="# Task\nFixes BUG-9103-single-fixed-thing.\n",
    )
    bundle_file = _write_bundle(req_dir, "v9.9.11-bugfix-bundle-test.md", [fixed_bug])

    result = _pbi.promote_bundle(bundle_file, "v9.9.11", tasks_dir=tasks_dir, bugs_dir=bugs_dir)

    assert _status_of(fixed_bug) == "done"
    assert result["promoted"] == 1
    assert result["carried"] == 0
    assert result["carried_items"] == []


def test_priority_item_gated_by_the_same_done_reference_rule(tmp_path):
    """The primary path enumerates PRIORITY-*.md items too (per the
    module's own docstring); the DONE-reference gate applies identically."""
    tasks_dir = tmp_path / "tasks"
    priority_dir = tmp_path / "priority"
    req_dir = tmp_path / "requirements"
    tasks_dir.mkdir()
    priority_dir.mkdir()
    req_dir.mkdir()

    fixed_priority = _write_item(priority_dir, "PRIORITY-9104-fixed-feature.md")
    unfixed_priority = _write_item(priority_dir, "PRIORITY-9105-unfixed-feature.md")

    _write_task(
        tasks_dir,
        "CODER-20260926-104-fixed-feature",
        release_version="v9.9.12",
        state="DONE",
        readme_body="# Task\nImplements PRIORITY-9104-fixed-feature.\n",
    )
    # No task references the second priority item at all -- never
    # decomposed, or its ticket never reached DONE.

    bundle_file = _write_bundle(
        req_dir,
        "v9.9.12-priority-bundle-test.md",
        [fixed_priority, unfixed_priority],
    )

    result = _pbi.promote_bundle(
        bundle_file, "v9.9.12", tasks_dir=tasks_dir, bugs_dir=priority_dir
    )

    assert _status_of(fixed_priority) == "done"
    assert _status_of(unfixed_priority) == "running"
    assert result["carried_items"] == ["PRIORITY-9105-unfixed-feature.md"]


def test_missing_tasks_dir_carries_everything_rather_than_promoting_blind(tmp_path):
    """Without tasks_dir the gate has nothing to consult -- it must fail
    toward "leave it," never toward "close it": every enumerated item at
    'running' is carried, not promoted."""
    bugs_dir = tmp_path / "bugs"
    req_dir = tmp_path / "requirements"
    bugs_dir.mkdir()
    req_dir.mkdir()

    bug = _write_item(bugs_dir, "BUG-9106-unverifiable-thing.md")
    bundle_file = _write_bundle(req_dir, "v9.9.13-bugfix-bundle-test.md", [bug])

    result = _pbi.promote_bundle(bundle_file, "v9.9.13")  # tasks_dir/bugs_dir omitted

    assert _status_of(bug) == "running"
    assert result["promoted"] == 0
    assert result["carried"] == 1
    assert result["carried_items"] == ["BUG-9106-unverifiable-thing.md"]
    # The bundle file's own promotion is unconditional.
    assert _status_of(bundle_file) == "done"


def test_rerun_after_carry_stays_idempotent(tmp_path):
    """A carried item re-checked on a later run (bundle file already
    'done', item still 'running') stays carried rather than flipping state
    on a second pass -- this gate has to hold on every future release, not
    just the one that first surfaces an unfixed item."""
    tasks_dir = tmp_path / "tasks"
    bugs_dir = tmp_path / "bugs"
    req_dir = tmp_path / "requirements"
    tasks_dir.mkdir()
    bugs_dir.mkdir()
    req_dir.mkdir()

    bug = _write_item(bugs_dir, "BUG-9107-still-withdrawn-thing.md")
    bundle_file = _write_bundle(req_dir, "v9.9.14-bugfix-bundle-test.md", [bug])

    first = _pbi.promote_bundle(bundle_file, "v9.9.14", tasks_dir=tasks_dir, bugs_dir=bugs_dir)
    assert first["carried_items"] == ["BUG-9107-still-withdrawn-thing.md"]
    assert _status_of(bundle_file) == "done"
    assert _status_of(bug) == "running"

    second = _pbi.promote_bundle(bundle_file, "v9.9.14", tasks_dir=tasks_dir, bugs_dir=bugs_dir)
    assert second["carried_items"] == ["BUG-9107-still-withdrawn-thing.md"]
    assert second["bundle_file_promoted"] is False  # already 'done' -- idempotent skip
    assert _status_of(bug) == "running"


# ---------------------------------------------------------------------------
# A DONE README that names an item only to put it outside the task does not
# claim it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "negative_sentence",
    [
        "Bundle item BUG-9112-left-out-thing is not ticketed in this plan.",
        "Bundle item 3, BUG-9112, is not ticketed in this plan (see plan summary).",
        "BUG-9112 is out of scope for this ticket.",
        "BUG-9112 is deferred to the next release.",
        "Leave BUG-9112 unfixed here; PM skips it.",
    ],
    ids=["not-ticketed", "not-ticketed-aside", "out-of-scope", "deferred", "skipped"],
)
def test_done_readme_naming_an_item_only_negatively_carries_it(tmp_path, capsys, negative_sentence):
    tasks_dir = tmp_path / "tasks"
    bugs_dir = tmp_path / "bugs"
    req_dir = tmp_path / "requirements"
    tasks_dir.mkdir()
    bugs_dir.mkdir()
    req_dir.mkdir()

    fixed_bug = _write_item(bugs_dir, "BUG-9111-fixed-thing.md")
    left_out_bug = _write_item(bugs_dir, "BUG-9112-left-out-thing.md")
    _write_task(
        tasks_dir,
        "CODER-20260926-111-fix-thing",
        release_version="v9.9.15",
        state="DONE",
        readme_body=f"# Task\n\n## Goal\nFix BUG-9111. {negative_sentence}\n",
    )
    bundle_file = _write_bundle(req_dir, "v9.9.15-bugfix-bundle-test.md", [fixed_bug, left_out_bug])

    result = _pbi.promote_bundle(bundle_file, "v9.9.15", tasks_dir=tasks_dir, bugs_dir=bugs_dir)

    assert _status_of(fixed_bug) == "done"
    assert _status_of(left_out_bug) == "running"
    assert result["carried_items"] == ["BUG-9112-left-out-thing.md"]
    assert result["derived_promoted"] == 0
    out = capsys.readouterr().out
    assert "carried (no DONE task claims it): BUG-9112-left-out-thing.md" in out
    assert "promoted (running -> done): BUG-9112-left-out-thing.md" not in out


def test_negative_mention_does_not_close_an_unbundled_running_bug(tmp_path):
    """The derivation path reads the same claim rule: a running bug no bundle
    enumerates is not promoted by a README that only says it is out of scope."""
    tasks_dir = tmp_path / "tasks"
    bugs_dir = tmp_path / "bugs"
    req_dir = tmp_path / "requirements"
    tasks_dir.mkdir()
    bugs_dir.mkdir()
    req_dir.mkdir()

    stray_bug = _write_item(bugs_dir, "BUG-9113-stray-thing.md")
    _write_task(
        tasks_dir,
        "CODER-20260926-113-other-thing",
        release_version="v9.9.16",
        state="DONE",
        readme_body="# Task\nBUG-9113 is out of scope here.\n",
    )
    bundle_file = _write_bundle(req_dir, "v9.9.16-bugfix-bundle-test.md", [])

    result = _pbi.promote_bundle(bundle_file, "v9.9.16", tasks_dir=tasks_dir, bugs_dir=bugs_dir)

    assert _status_of(stray_bug) == "running"
    assert result["derived_promoted"] == 0
    assert result["cannot_auto_promote"] == ["BUG-9113-stray-thing.md"]


def test_materialized_fix_ticket_still_claims_its_items(tmp_path):
    """The README shape the task materializer writes -- the bug file path
    under ## Inputs and the ID in ## Goal -- claims each item, even when the
    Goal's own prose describes negative cases, and PRIORITY items behave the
    same way."""
    tasks_dir = tmp_path / "tasks"
    bugs_dir = tmp_path / "bugs"
    priority_dir = tmp_path / "priority"
    req_dir = tmp_path / "requirements"
    for d in (tasks_dir, bugs_dir, priority_dir, req_dir):
        d.mkdir()

    bug = _write_item(bugs_dir, "BUG-9114-deferred-items-skipped.md")
    priority = _write_item(priority_dir, "PRIORITY-9115-out-of-scope-wording.md")
    _write_task(
        tasks_dir,
        "CODER-20260926-114-fix-close-rule",
        release_version="v9.9.17",
        state="DONE",
        readme_body=(
            "# Task: tighten the close rule\n\n## Goal\n"
            "BUG-9114, a README that says an item is not ticketed or deferred must not close it;"
            " PRIORITY-9115, the same for out of scope.\n\n"
            f"## Inputs\n- {bug}\n- {priority}\n"
        ),
    )
    bundle_file = _write_bundle(req_dir, "v9.9.17-bugfix-bundle-test.md", [bug, priority])

    result = _pbi.promote_bundle(bundle_file, "v9.9.17", tasks_dir=tasks_dir, bugs_dir=bugs_dir)

    assert _status_of(bug) == "done"
    assert _status_of(priority) == "done"
    assert result["carried_items"] == []
