"""
test_shipped_bundle_items.py
============================
pgai_agent_kanban.cm.shipped_bundle_items.promote_shipped_bundle_items, the
per-requirements-directory work of team/scripts/migrate/bug-status-done.sh:
promote each ``running`` item a shipped bundle names to ``done``.

Coverage:
  - An item of a bundle whose Target Version is shipped is flipped to
    ``done``, every other byte of the file kept.
  - An item of a bundle whose Target Version is not shipped is left alone.
  - ``dry_run`` writes nothing and reports what it would promote.
  - An empty shipped-tag set promotes nothing and says so; only ``None``
    (the CLI's ``--treat-all-shipped``) treats every bundle as shipped.
  - Items that are not ``running``, missing items, non-bundle files and a
    bundle with no Target Version are reported and counted, never written.
  - The module's command line, run through pp_run_ops as the script runs it:
    tags, no tags, ``--treat-all-shipped``, and that flag refused with tags.

The script's own exit status and --help are covered in
test_bug_status_done.py and test_operator_script_help_text.py.
"""

from __future__ import annotations

import io
import pathlib

from pgai_agent_kanban.cm.shipped_bundle_items import PromotionCounts, promote_shipped_bundle_items
from tests.unit.shell_harness import run_bash


def _item(dir_: pathlib.Path, name: str, status: str) -> pathlib.Path:
    path = dir_ / name
    path.write_text(f"# {name}\n\n## Status\n{status}\n\n## Severity\nlow\n", encoding="utf-8")
    return path


def _bundle(req: pathlib.Path, name: str, version: str, *items: pathlib.Path) -> pathlib.Path:
    lines = "".join(f"- {item.stem} (`{item}`)\n" for item in items)
    path = req / name
    path.write_text(f"# {name}\n\n## Target Version\n{version}\n\n## Bundled Items\n{lines}", encoding="utf-8")
    return path


def _dirs(tmp_path: pathlib.Path):
    req = tmp_path / "requirements"
    bugs = tmp_path / "bugs"
    req.mkdir()
    bugs.mkdir()
    return req, bugs


def _promote(req: pathlib.Path, tags, dry_run: bool = False):
    out = io.StringIO()
    counts = promote_shipped_bundle_items(req, tags, dry_run, out=out)
    return counts, out.getvalue().splitlines()


def test_item_of_a_shipped_bundle_is_flipped_to_done(tmp_path):
    req, bugs = _dirs(tmp_path)
    item = _item(bugs, "BUG-0001.md", "running")
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", item)
    counts, lines = _promote(req, {"v1.0.0", "v0.9.0"})
    assert item.read_text(encoding="utf-8") == "# BUG-0001.md\n\n## Status\ndone\n\n## Severity\nlow\n"
    assert "  bundle: v1.0.0-bundle.md (version=v1.0.0, SHIPPED)" in lines
    assert "    promoted (running -> done): BUG-0001.md" in lines
    assert counts == PromotionCounts(bundles_processed=1, bundles_shipped=1, promoted=1)


def test_item_of_an_unshipped_bundle_is_left_running(tmp_path):
    req, bugs = _dirs(tmp_path)
    item = _item(bugs, "BUG-0002.md", "running")
    before = item.read_bytes()
    _bundle(req, "v2.0.0-bundle.md", "v2.0.0", item)
    counts, lines = _promote(req, {"v1.0.0"})
    assert item.read_bytes() == before
    assert not any("bundle:" in line for line in lines)
    assert counts == PromotionCounts(bundles_processed=1)


def test_dry_run_writes_nothing_and_reports_what_it_would_promote(tmp_path):
    req, bugs = _dirs(tmp_path)
    item = _item(bugs, "BUG-0003.md", "running")
    before = item.read_bytes()
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", item)
    counts, lines = _promote(req, {"v1.0.0"}, dry_run=True)
    assert item.read_bytes() == before
    assert "    [DRY RUN] would promote (running -> done): BUG-0003.md" in lines
    assert counts.promoted == 1


_NOTHING_SHIPPED = "  no shipped release tag given: no bundle is treated as shipped, nothing is promoted"


def test_no_shipped_tag_set_treats_every_bundle_as_shipped(tmp_path):
    req, bugs = _dirs(tmp_path)
    first = _item(bugs, "BUG-0004.md", "running")
    second = _item(bugs, "BUG-0005.md", "Running")
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", first)
    _bundle(req, "v7.0.0-bundle.md", "v7.0.0", second)
    counts, lines = _promote(req, None)
    assert "## Status\ndone\n" in first.read_text(encoding="utf-8")
    assert "## Status\ndone\n" in second.read_text(encoding="utf-8")
    assert _NOTHING_SHIPPED not in lines
    assert counts == PromotionCounts(bundles_processed=2, bundles_shipped=2, promoted=2)


def test_empty_shipped_tag_set_promotes_nothing_and_says_so(tmp_path):
    req, bugs = _dirs(tmp_path)
    first = _item(bugs, "BUG-0011.md", "running")
    second = _item(bugs, "BUG-0012.md", "running")
    before = {p: p.read_bytes() for p in (first, second)}
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", first)
    _bundle(req, "v7.0.0-bundle.md", "v7.0.0", second)
    counts, lines = _promote(req, set())
    assert {p: p.read_bytes() for p in before} == before
    assert lines[0] == _NOTHING_SHIPPED
    assert not any("SHIPPED" in line for line in lines)
    assert counts == PromotionCounts(bundles_processed=2)


def test_items_that_are_not_running_or_missing_are_counted_and_untouched(tmp_path):
    req, bugs = _dirs(tmp_path)
    open_item = _item(bugs, "BUG-0006.md", "open")
    no_status = bugs / "BUG-0007.md"
    no_status.write_text("# BUG-0007\nno status\n", encoding="utf-8")
    missing = bugs / "BUG-0008.md"
    before = {p: p.read_bytes() for p in (open_item, no_status)}
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", open_item, no_status, missing)
    counts, lines = _promote(req, {"v1.0.0"})
    assert {p: p.read_bytes() for p in before} == before
    assert "    skip (status=open): BUG-0006.md" in lines
    assert "    skip (status=no-status-field): BUG-0007.md" in lines
    assert f"    WARN: item not found (skipped): {missing}" in lines
    assert counts == PromotionCounts(bundles_processed=1, bundles_shipped=1, skipped_not_running=2, skipped_missing=1)


def test_non_bundle_files_and_a_bundle_without_target_version_are_skipped(tmp_path):
    req, bugs = _dirs(tmp_path)
    item = _item(bugs, "BUG-0009.md", "running")
    before = item.read_bytes()
    _bundle(req, "v1.0.0-requirement.md", "v1.0.0", item)
    (req / "x-bundle.md").write_text(f"## Target Version\nTBD\n\n## Bundled Items\n- (`{item}`)\n", encoding="utf-8")
    counts, lines = _promote(req, {"v1.0.0"})
    assert item.read_bytes() == before
    assert "  skip (no ## Target Version): x-bundle.md" in lines
    assert counts == PromotionCounts(bundles_processed=1)
    assert lines[-5:] == [
        "  Bundles processed: 1",
        "  Bundles shipped:   0",
        "  Items promoted:    0",
        "  Items skipped (not running): 0",
        "  Items missing:     0",
    ]


def test_command_line_promotes_through_pp_run_ops_and_honours_dry_run(tmp_path):
    req, bugs = _dirs(tmp_path)
    item = _item(bugs, "BUG-0010.md", "running")
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", item)
    run = "source scripts/lib/pp_run_ops.sh && pp_run_ops pgai_agent_kanban.cm.shipped_bundle_items"

    dry = run_bash(tmp_path, f"{run} --dry-run -- '{req}' v1.0.0")
    assert dry.returncode == 0, dry.stderr
    assert "    [DRY RUN] would promote (running -> done): BUG-0010.md" in dry.stdout.splitlines()
    assert "## Status\nrunning\n" in item.read_text(encoding="utf-8")

    real = run_bash(tmp_path, f"{run} -- '{req}' v1.0.0")
    assert real.returncode == 0, real.stderr
    assert "    promoted (running -> done): BUG-0010.md" in real.stdout.splitlines()
    assert "## Status\ndone\n" in item.read_text(encoding="utf-8")


def test_command_line_without_tags_promotes_nothing_and_treat_all_shipped_promotes_every_bundle(tmp_path):
    req, bugs = _dirs(tmp_path)
    first = _item(bugs, "BUG-0013.md", "running")
    second = _item(bugs, "BUG-0014.md", "running")
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", first)
    _bundle(req, "v2.0.0-bundle.md", "v2.0.0", second)
    run = "source scripts/lib/pp_run_ops.sh && pp_run_ops pgai_agent_kanban.cm.shipped_bundle_items"

    none_given = run_bash(tmp_path, f"{run} -- '{req}'")
    assert none_given.returncode == 0, none_given.stderr
    assert _NOTHING_SHIPPED in none_given.stdout.splitlines()
    assert "## Status\nrunning\n" in first.read_text(encoding="utf-8")
    assert "## Status\nrunning\n" in second.read_text(encoding="utf-8")

    every = run_bash(tmp_path, f"{run} --treat-all-shipped -- '{req}'")
    assert every.returncode == 0, every.stderr
    assert "## Status\ndone\n" in first.read_text(encoding="utf-8")
    assert "## Status\ndone\n" in second.read_text(encoding="utf-8")


def test_command_line_refuses_treat_all_shipped_with_tags(tmp_path):
    req, bugs = _dirs(tmp_path)
    item = _item(bugs, "BUG-0015.md", "running")
    before = item.read_bytes()
    _bundle(req, "v1.0.0-bundle.md", "v1.0.0", item)
    run = "source scripts/lib/pp_run_ops.sh && pp_run_ops pgai_agent_kanban.cm.shipped_bundle_items"
    result = run_bash(tmp_path, f"{run} --treat-all-shipped -- '{req}' v1.0.0")
    assert result.returncode == 2
    assert "--treat-all-shipped takes no SHIPPED_TAG" in result.stderr
    assert item.read_bytes() == before
