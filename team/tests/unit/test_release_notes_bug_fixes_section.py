"""
test_release_notes_bug_fixes_section.py
=======================================
A release that closes bugs lists every one of them under its release notes'
``## Bug Fixes`` section, whatever subsystem the bug's own ``## Category``
names, and a CHANGELOG regenerated from those notes lists the same bugs
under that version's ``### Fixed``.

Bug reports carry free-form subsystem categories (``release``, ``api``,
``wake``, ``docs``, ...) rather than the literal ``bugfix``; the release
notes generator therefore classifies a bundled item as a bug fix by its
filename (``BUG-NNNN-*.md``), not by its category. Priority and
requirements-document items keep the category-to-section mapping.

Each bug's bullet states the defect, built from the lead sentence of its
``## Symptom`` section; its ``## Fix`` section is a proposed remedy written
before the work and never reaches the notes or the CHANGELOG. The fixture
Symptoms take the shapes filed reports use -- a lead sentence ending in ':'
followed by a fenced excerpt or a bulleted list -- and none of the fenced or
listed text may surface in either artifact.

Fidelity approach: identical to test_release_notes_content_described.py --
the shipped generator, ``pgai_agent_kanban.cm.release_notes_generator``, is
called through its CLI entry point ``main()`` with the argv
cm_release_generate_notes passes, and the CHANGELOG half calls the real
``changelog_writer.regenerate`` on the notes that generator wrote. No
parallel implementation to drift.

All paths live under pytest's tmp_path (redirected to the framework temp
root by conftest.py). No live kanban root is touched.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from pgai_agent_kanban.cm.changelog_writer import regenerate
from pgai_agent_kanban.cm.release_notes_generator import main as release_notes_main

# Any internal ledger identifier; none may reach either rendered artifact.
_INTERNAL_BUG_ID_RE = re.compile(r"BUG-\d")

_VERSION = "v9.9.9"

# (filename, category, Symptom body, expected Symptom-derived bullet text,
# Fix text) for the bundled bug reports. None of the categories is "bugfix"
# -- that is the point of the fixture.
_BUGS = [
    ("BUG-0901-release-notes-section.md", "release",
     "Release notes omitted shipped bug fixes whose category was not bugfix:\n\n"
     "```\n## Bug Fixes\nNone\n```\n",
     "Fixed: Release notes omitted shipped bug fixes whose category was not bugfix.",
     "Classify a bundled item as a bug fix by its filename."),
    ("BUG-0902-api-port-resolution.md", "api",
     "The API server ignored the port in the project configuration "
     "(e.g. `api.port = 8100`) and bound the default:\n"
     "- `curl localhost:8100` refused the connection\n"
     "- `curl localhost:8000` answered\n",
     "Fixed: The API server ignored the port in the project configuration "
     "(e.g. `api.port = 8100`) and bound the default.",
     "Resolve the port from the project configuration first."),
    ("BUG-0903-wake-stale-lock.md", "wake",
     "A stale wake lock left by a dead process stalled the queue.\n",
     "Fixed: A stale wake lock left by a dead process stalled the queue.",
     "Options: (a) reap the lock by pid; (b) expire it after a timeout."),
    ("BUG-0904-docs-broken-link.md", "docs",
     "The operations guide linked to a configuration reference page that "
     "no longer exists.\n",
     "Fixed: The operations guide linked to a configuration reference page "
     "that no longer exists.",
     "Point the link at the current configuration reference."),
]

# Text that appears only inside a fixture Symptom's fence or list; it must
# never surface in a rendered artifact.
_SYMPTOM_DETAIL_ONLY = ["refused the connection", "answered", "```"]


def _section_body(text: str, heading_re: str) -> list[str]:
    """Return the non-blank lines under the first heading matching
    *heading_re*, up to the next heading of the same or higher level."""
    match = re.search(heading_re, text, re.MULTILINE)
    assert match, f"heading {heading_re!r} not found in:\n{text}"
    rest = text[match.end():]
    level = heading_re.count("#")
    stop = re.search(r"^#{1,%d}\s" % level, rest, re.MULTILINE)
    block = rest[: stop.start()] if stop else rest
    return [ln.strip() for ln in block.splitlines() if ln.strip()]


@pytest.fixture()
def release(tmp_path: pathlib.Path) -> dict:
    """Write bug and priority items plus a bundle, run the shipped generator,
    and return the paths and rendered notes text."""
    bugs_dir = tmp_path / "bugs"
    priority_dir = tmp_path / "priority"
    bugs_dir.mkdir()
    priority_dir.mkdir()

    item_paths: list[pathlib.Path] = []
    for filename, category, symptom, _, fix in _BUGS:
        path = bugs_dir / filename
        path.write_text(
            f"# {path.stem}\n\n## Status\n\nrunning\n\n## Category\n\n{category}\n\n"
            f"## Symptom\n\n{symptom}\n"
            f"## Fix\n\n{fix}\n",
            encoding="utf-8",
        )
        item_paths.append(path)

    feature = priority_dir / "PRIORITY-0901-dashboard-filter.md"
    feature.write_text(
        "# PRIORITY-0901-dashboard-filter\n\n## Category\n\nfeature\n\n"
        "## Title\n\nThe dashboard filters tasks by state.\n",
        encoding="utf-8",
    )
    misc = priority_dir / "PRIORITY-0902-wake-cadence.md"
    misc.write_text(
        "# PRIORITY-0902-wake-cadence\n\n## Category\n\nwake\n\n"
        "## Title\n\nWake cadence is configurable per agent.\n",
        encoding="utf-8",
    )
    item_paths += [feature, misc]

    bundle = tmp_path / f"{_VERSION}-bugfix-bundle.md"
    bundle.write_text(
        "# Bundle\n\n## Bundled Items\n\n"
        + "".join(f"- {p.name} (`{p}`)\n" for p in item_paths),
        encoding="utf-8",
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    rc = release_notes_main(
        [str(bundle), str(repo_root), _VERSION,
         "FUNCTIONAL", "2026-09-30", "test-project", "none", "rc/" + _VERSION]
    )
    assert rc == 0, f"release notes generator returned {rc}"
    notes_path = repo_root / "release-notes" / f"{_VERSION}.md"
    return {
        "repo_root": repo_root,
        "bugs_dir": bugs_dir,
        "notes": notes_path.read_text(encoding="utf-8"),
    }


class TestReleaseNotesBugFixes:
    def test_every_bug_is_listed_under_bug_fixes_whatever_its_category(
        self, release: dict
    ) -> None:
        bug_fixes = _section_body(release["notes"], r"^##\s+Bug Fixes\s*$")
        for _, category, _, expected, _ in _BUGS:
            assert f"- {expected}" in bug_fixes, (
                f"bug with category {category!r} missing from ## Bug Fixes; "
                f"section reads: {bug_fixes}"
            )
        assert len(bug_fixes) == len(_BUGS), bug_fixes

    def test_fix_and_symptom_detail_text_never_reach_the_notes(
        self, release: dict
    ) -> None:
        for _, _, _, _, fix in _BUGS:
            assert fix not in release["notes"]
        for fragment in _SYMPTOM_DETAIL_ONLY:
            assert fragment not in release["notes"]

    def test_bugs_do_not_also_appear_in_other_sections(self, release: dict) -> None:
        other = _section_body(release["notes"], r"^##\s+Other Changes\s*$")
        docs = _section_body(release["notes"], r"^##\s+Documentation\s*$")
        for _, _, _, expected, _ in _BUGS:
            assert f"- {expected}" not in other + docs

    def test_priority_items_keep_their_category_mapping(self, release: dict) -> None:
        features = _section_body(release["notes"], r"^##\s+Features\s*$")
        other = _section_body(release["notes"], r"^##\s+Other Changes\s*$")
        assert features == ["- The dashboard filters tasks by state."]
        assert other == ["- Wake cadence is configurable per agent."]

    def test_notes_carry_no_internal_bug_id(self, release: dict) -> None:
        leaked = _INTERNAL_BUG_ID_RE.findall(release["notes"])
        assert not leaked, f"internal ids leaked into release notes: {leaked}"


class TestRegeneratedChangelogFixed:
    def test_fixed_lists_every_bundled_bug(self, release: dict) -> None:
        content = regenerate(
            release["repo_root"], [_VERSION], [], release["bugs_dir"],
            published_changelog=None,
        )
        fixed = _section_body(content, r"^###\s+Fixed\s*$")
        assert fixed != ["None"], f"### Fixed is empty:\n{content}"
        for _, category, _, expected, fix in _BUGS:
            assert any(expected in line for line in fixed), (
                f"bug with category {category!r} missing from ### Fixed; "
                f"section reads: {fixed}"
            )
            assert fix not in content
        for fragment in _SYMPTOM_DETAIL_ONLY:
            assert fragment not in content
        assert not any("dashboard filters" in line for line in fixed)

    def test_changelog_carries_no_internal_bug_id(self, release: dict) -> None:
        content = regenerate(
            release["repo_root"], [_VERSION], [], release["bugs_dir"],
            published_changelog=None,
        )
        leaked = _INTERNAL_BUG_ID_RE.findall(content)
        assert not leaked, f"internal ids leaked into CHANGELOG: {leaked}"
