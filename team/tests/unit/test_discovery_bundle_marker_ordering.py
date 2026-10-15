"""
test_discovery_bundle_marker_ordering.py
=========================================
Unit tests for _disc_order_bundle_items in team/scripts/lib/discovery_state.sh:
the stable partition that puts every item carrying a non-empty
"## Known Issue Carry Forward" value ahead of every item that does not,
preserving relative order within each group.

_disc_order_bundle_items is a pure function over a newline-separated list of
absolute file paths -- it never mutates its inputs. These tests invoke it
directly via the bash harness (no workflow plugin, no requirements
directory, no discovery pipeline needed), which keeps them fast and
isolates the ordering logic from _disc_write_bundle's file-writing and
plugin-loading concerns. End-to-end coverage of the rendered bundle document
(the ## Bundled Items section itself, produced by both discovery_step_bugs
and discovery_step_priority) lives in
team/tests/integration/test_discovery_pipeline.py.
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/discovery_state.sh"


def _write(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _order(tmp_path: pathlib.Path, files: list[pathlib.Path]) -> list[str]:
    file_list = "\n".join(str(f) for f in files)
    result = run_bash(
        tmp_path,
        f"source {_LIB} && _disc_order_bundle_items '{file_list}'",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    return [line for line in result.stdout.splitlines() if line.strip()]


def _plain_item(tmp_path: pathlib.Path, item_id: str) -> pathlib.Path:
    return _write(
        tmp_path,
        f"{item_id}.md",
        f"# {item_id}\n\n## Status\nopen\n",
    )


def _marked_item(tmp_path: pathlib.Path, item_id: str, version: str = "v1.2.3") -> pathlib.Path:
    return _write(
        tmp_path,
        f"{item_id}.md",
        f"# {item_id}\n\n## Status\nopen\n\n## Known Issue Carry Forward\n{version}\n",
    )


class TestSingleMarkedItem:
    """A bundle where exactly one item carries the marker."""

    def test_marked_item_moves_to_the_front(self, tmp_path):
        a = _plain_item(tmp_path, "BUG-0001-alpha")
        b = _marked_item(tmp_path, "BUG-0002-bravo")
        c = _plain_item(tmp_path, "BUG-0003-charlie")

        ordered = _order(tmp_path, [a, b, c])

        assert ordered == [str(b), str(a), str(c)], (
            f"Expected the marked item first with the others following in "
            f"their original order; got {ordered}"
        )


class TestSeveralMarkedItemsPreserveRelativeOrder:
    """A bundle where several of many items carry the marker."""

    def test_three_of_five_marked_items_lead_in_original_relative_order(self, tmp_path):
        # Marked: b, d, e (in that relative order). Unmarked: a, c.
        a = _plain_item(tmp_path, "BUG-0001-alpha")
        b = _marked_item(tmp_path, "BUG-0002-bravo")
        c = _plain_item(tmp_path, "BUG-0003-charlie")
        d = _marked_item(tmp_path, "BUG-0004-delta")
        e = _marked_item(tmp_path, "BUG-0005-echo")

        ordered = _order(tmp_path, [a, b, c, d, e])

        assert ordered == [str(b), str(d), str(e), str(a), str(c)], (
            f"Expected marked items (b, d, e) first in their original "
            f"relative order, then unmarked items (a, c) in theirs; "
            f"got {ordered}"
        )


class TestNoMarkedItemsIsUnchanged:
    """A bundle with no marked items must be a pure pass-through."""

    def test_all_unmarked_items_keep_their_original_order(self, tmp_path):
        a = _plain_item(tmp_path, "BUG-0001-alpha")
        b = _plain_item(tmp_path, "BUG-0002-bravo")
        c = _plain_item(tmp_path, "BUG-0003-charlie")

        ordered = _order(tmp_path, [a, b, c])

        assert ordered == [str(a), str(b), str(c)], (
            "Ordinary bundling (no markers present) must be provably "
            f"unchanged: got {ordered}"
        )


class TestUnparseableMarkerIsTreatedAsUnmarked:
    """A marker header with no readable value is neither promoted nor dropped."""

    def test_marker_header_with_empty_value_stays_in_place(self, tmp_path):
        # The marker header is present, but the very next non-blank line is
        # another heading -- there is no value between them.  Per the
        # documented contract in _disc_order_bundle_items's header, this
        # counts as UNMARKED: the item is not promoted to the front, and it
        # is not dropped from the output either.
        a = _plain_item(tmp_path, "BUG-0001-alpha")
        malformed = _write(
            tmp_path,
            "BUG-0002-bravo.md",
            "# BUG-0002-bravo\n\n## Status\nopen\n\n"
            "## Known Issue Carry Forward\n\n## Other Field\nx\n",
        )
        c = _plain_item(tmp_path, "BUG-0003-charlie")

        ordered = _order(tmp_path, [a, malformed, c])

        assert ordered == [str(a), str(malformed), str(c)], (
            "An unparseable (empty-value) marker must not promote its item "
            f"to the front, and must not drop it from the list; got {ordered}"
        )

    def test_marker_header_at_end_of_file_with_no_value_stays_in_place(self, tmp_path):
        # The marker header is the very last line of the file -- no value,
        # no following heading, just EOF.  Same UNMARKED disposition.
        a = _plain_item(tmp_path, "BUG-0001-alpha")
        malformed = _write(
            tmp_path,
            "BUG-0002-bravo.md",
            "# BUG-0002-bravo\n\n## Status\nopen\n\n## Known Issue Carry Forward\n",
        )

        ordered = _order(tmp_path, [a, malformed])

        assert ordered == [str(a), str(malformed)], (
            f"A marker header truncated at EOF must be treated as unmarked; got {ordered}"
        )

    def test_unreadable_path_stays_in_place_and_is_not_dropped(self, tmp_path):
        # A path in the list that does not exist on disk (e.g. removed
        # between scan and render) must be treated as unmarked -- and must
        # still appear in the output, in its original position.
        a = _plain_item(tmp_path, "BUG-0001-alpha")
        missing = tmp_path / "BUG-0002-bravo.md"  # never written
        b = _marked_item(tmp_path, "BUG-0003-charlie")

        ordered = _order(tmp_path, [a, missing, b])

        assert ordered == [str(b), str(a), str(missing)], (
            f"An unreadable path must be treated as unmarked (not dropped); got {ordered}"
        )


class TestMarkerRecognitionIsPresenceOnly:
    """Ordering cares only whether a marker was stamped, not its exact value."""

    def test_any_non_blank_value_counts_as_marked_regardless_of_content(self, tmp_path):
        a = _plain_item(tmp_path, "BUG-0001-alpha")
        oddly_valued = _write(
            tmp_path,
            "BUG-0002-bravo.md",
            "# BUG-0002-bravo\n\n## Status\nopen\n\n"
            "## Known Issue Carry Forward\nnot-a-version-string\n",
        )

        ordered = _order(tmp_path, [a, oddly_valued])

        assert ordered == [str(oddly_valued), str(a)], (
            "Ordering must key on marker presence, not on the value looking "
            f"like a version string; got {ordered}"
        )
