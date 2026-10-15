"""
test_bundle_family.py
======================
Unit tests for pgai_agent_kanban.cm.bundle_family — the retry-counter
family-identity and attempt-number computation.

Coverage:
  - Same sorted cargo set rebundled under a new version increments the
    attempt count (N -> N+1) via a matching CANCELLED history record.
  - A different cargo set produces a different family_key and starts fresh
    at attempt 1.
  - First-attempt happy path (no prior history for the family) records
    attempt 1.
  - A bundle with no `## Bundled Items` (or no bundle file at all) has no
    family to track and always starts at attempt 1.
  - family_key is order- and duplicate-independent (same SET -> same key).

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
from pathlib import Path

from pgai_agent_kanban.cm.bundle_family import (
    compute_bundle_family,
    compute_family_key,
    compute_next_attempt,
    find_bundle_file,
    parse_bundled_item_basenames,
)


# ---------------------------------------------------------------------------
# find_bundle_file
# ---------------------------------------------------------------------------


def test_find_bundle_file_returns_none_when_requirements_dir_missing(tmp_path: Path) -> None:
    """A requirements dir that does not exist on disk yields no bundle file."""
    result = find_bundle_file(str(tmp_path / "does-not-exist"), "v2.0.1")
    assert result is None


def test_find_bundle_file_returns_none_when_no_match(tmp_path: Path) -> None:
    """A requirements dir with unrelated files yields no bundle file for the version."""
    (tmp_path / "v1.9.0-bugfix-bundle-20260101.md").write_text("# unrelated\n")
    result = find_bundle_file(str(tmp_path), "v2.0.1")
    assert result is None


def test_find_bundle_file_matches_versioned_bundle(tmp_path: Path) -> None:
    """A bundle file named <version>-*.md is located for that version."""
    bundle = tmp_path / "v2.0.1-bugfix-bundle-20260727.md"
    bundle.write_text("# bundle\n")
    result = find_bundle_file(str(tmp_path), "v2.0.1")
    assert result == str(bundle)


def test_find_bundle_file_is_deterministic_when_multiple_match(tmp_path: Path) -> None:
    """When more than one file matches, the lexically-first is returned."""
    (tmp_path / "v2.0.1-zzz-bundle.md").write_text("# z\n")
    (tmp_path / "v2.0.1-aaa-bundle.md").write_text("# a\n")
    result = find_bundle_file(str(tmp_path), "v2.0.1")
    assert result == str(tmp_path / "v2.0.1-aaa-bundle.md")


# ---------------------------------------------------------------------------
# parse_bundled_item_basenames
# ---------------------------------------------------------------------------


def test_parse_bundled_item_basenames_extracts_backtick_paths(tmp_path: Path) -> None:
    """Items are extracted from the ## Bundled Items section's backtick-paren paths."""
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        "# Requirements: v2.0.1 — BUG bundle\n\n"
        "## Status\n\nopen\n\n"
        "## Bundled Items\n\n"
        "- BUG-0001-foo.md (`/abs/path/bugs/BUG-0001-foo.md`)\n"
        "- BUG-0002-bar.md (`/abs/path/bugs/BUG-0002-bar.md`)\n\n"
        "## Acceptance Criteria\n\n- [ ] done\n",
        encoding="utf-8",
    )
    result = parse_bundled_item_basenames(str(bundle))
    assert result == ["BUG-0001-foo.md", "BUG-0002-bar.md"]


def test_parse_bundled_item_basenames_stops_at_next_section(tmp_path: Path) -> None:
    """Parsing stops at the next '##' heading — items after it are not collected."""
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        "## Bundled Items\n\n"
        "- BUG-0001-foo.md (`/abs/BUG-0001-foo.md`)\n\n"
        "## Notes\n\n"
        "- BUG-9999-decoy.md (`/abs/BUG-9999-decoy.md`)\n",
        encoding="utf-8",
    )
    result = parse_bundled_item_basenames(str(bundle))
    assert result == ["BUG-0001-foo.md"]


def test_parse_bundled_item_basenames_empty_when_no_section(tmp_path: Path) -> None:
    """A bundle with no ## Bundled Items section yields an empty list."""
    bundle = tmp_path / "bundle.md"
    bundle.write_text("# Operator-authored requirements\n\nNo bundled items here.\n")
    result = parse_bundled_item_basenames(str(bundle))
    assert result == []


def test_parse_bundled_item_basenames_empty_when_file_missing(tmp_path: Path) -> None:
    """A nonexistent bundle path yields an empty list rather than raising."""
    result = parse_bundled_item_basenames(str(tmp_path / "missing.md"))
    assert result == []


# ---------------------------------------------------------------------------
# compute_family_key
# ---------------------------------------------------------------------------


def test_compute_family_key_is_none_for_empty_cargo() -> None:
    """An empty cargo set has no family to track."""
    assert compute_family_key([]) is None


def test_compute_family_key_is_order_independent() -> None:
    """The same set in a different order produces the identical family key."""
    key_a = compute_family_key(["BUG-0001-foo.md", "BUG-0002-bar.md"])
    key_b = compute_family_key(["BUG-0002-bar.md", "BUG-0001-foo.md"])
    assert key_a == key_b
    assert key_a is not None


def test_compute_family_key_is_duplicate_independent() -> None:
    """Duplicate entries in the source list do not change the family key."""
    key_a = compute_family_key(["BUG-0001-foo.md", "BUG-0002-bar.md"])
    key_b = compute_family_key(["BUG-0001-foo.md", "BUG-0001-foo.md", "BUG-0002-bar.md"])
    assert key_a == key_b


def test_compute_family_key_differs_for_different_cargo_sets() -> None:
    """A different cargo set (even by one bug) produces a different family key."""
    key_a = compute_family_key(["BUG-0001-foo.md", "BUG-0002-bar.md"])
    key_b = compute_family_key(["BUG-0001-foo.md", "BUG-0003-baz.md"])
    assert key_a != key_b


# ---------------------------------------------------------------------------
# compute_next_attempt
# ---------------------------------------------------------------------------


def test_compute_next_attempt_is_one_when_no_family_key(tmp_path: Path) -> None:
    """No family_key (empty cargo) always yields attempt 1 with no history."""
    attempt, history = compute_next_attempt(None, str(tmp_path))
    assert attempt == 1
    assert history == []


def test_compute_next_attempt_is_one_when_state_dir_empty(tmp_path: Path) -> None:
    """A family with no prior records anywhere starts at attempt 1 — the
    first-attempt happy path."""
    attempt, history = compute_next_attempt("family-abc123", str(tmp_path))
    assert attempt == 1
    assert history == []


def test_compute_next_attempt_increments_after_matching_cancelled_record(tmp_path: Path) -> None:
    """A cancelled record for the same family_key bumps the next attempt."""
    state_dir = tmp_path / "release-state"
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "v2.0.0-cancelled-2026-07-27T00-00-00Z.json").write_text(
        json.dumps({
            "rc": "v2.0.0", "outcome": "cancelled",
            "family_key": "family-abc123", "attempt": 1,
        }),
        encoding="utf-8",
    )

    attempt, history = compute_next_attempt("family-abc123", str(state_dir))
    assert attempt == 2
    assert len(history) == 1
    assert history[0]["attempt"] == 1


def test_compute_next_attempt_ignores_different_family(tmp_path: Path) -> None:
    """A cancelled record for a DIFFERENT family_key does not affect this family."""
    state_dir = tmp_path / "release-state"
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "v2.0.0-cancelled-2026-07-27T00-00-00Z.json").write_text(
        json.dumps({
            "rc": "v2.0.0", "outcome": "cancelled",
            "family_key": "family-different", "attempt": 1,
        }),
        encoding="utf-8",
    )

    attempt, history = compute_next_attempt("family-abc123", str(state_dir))
    assert attempt == 1
    assert history == []


def test_compute_next_attempt_does_not_advance_on_shipped_record(tmp_path: Path) -> None:
    """A SHIPPED record for the same family closes the story rather than
    continuing it — it appears in history but does not bump the count."""
    state_dir = tmp_path / "release-state"
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "v1.9.0-shipped-marker.json").write_text(
        json.dumps({
            "rc": "v1.9.0", "outcome": "shipped",
            "family_key": "family-abc123", "attempt": 1,
        }),
        encoding="utf-8",
    )

    attempt, history = compute_next_attempt("family-abc123", str(state_dir))
    assert attempt == 1, "A shipped record must not force a fresh cancel-retry count"
    assert len(history) == 1, "Shipped record is still surfaced in history for narration"


def test_compute_next_attempt_uses_max_across_multiple_cancellations(tmp_path: Path) -> None:
    """Two prior cancelled attempts for the same family yield attempt 3 next."""
    state_dir = tmp_path / "release-state"
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "v2.0.0-cancelled-a.json").write_text(
        json.dumps({"rc": "v2.0.0", "outcome": "cancelled", "family_key": "family-abc123", "attempt": 1}),
        encoding="utf-8",
    )
    (history_dir / "v2.0.1-cancelled-b.json").write_text(
        json.dumps({"rc": "v2.0.1", "outcome": "cancelled", "family_key": "family-abc123", "attempt": 2}),
        encoding="utf-8",
    )

    attempt, history = compute_next_attempt("family-abc123", str(state_dir))
    assert attempt == 3
    assert [h["attempt"] for h in history] == [1, 2]


def test_compute_next_attempt_excludes_given_path(tmp_path: Path) -> None:
    """A record at exclude_path never counts as its own prior attempt
    (guards a recovery re-run of the same version against double-counting)."""
    state_dir = tmp_path / "release-state"
    state_dir.mkdir(parents=True)
    self_path = state_dir / "v2.0.1.json"
    self_path.write_text(
        json.dumps({"rc": "v2.0.1", "outcome": "in_progress", "family_key": "family-abc123", "attempt": 5}),
        encoding="utf-8",
    )

    attempt, history = compute_next_attempt(
        "family-abc123", str(state_dir), exclude_path=str(self_path)
    )
    assert attempt == 1
    assert history == []


# ---------------------------------------------------------------------------
# compute_bundle_family — end to end
# ---------------------------------------------------------------------------


def _write_bundle(path: Path, item_basenames) -> None:
    lines = ["# Requirements bundle\n", "\n", "## Bundled Items\n", "\n"]
    for name in item_basenames:
        lines.append(f"- {name} (`/abs/bugs/{name}`)\n")
    path.write_text("".join(lines), encoding="utf-8")


def test_compute_bundle_family_first_attempt_happy_path(tmp_path: Path) -> None:
    """A fresh family with a real bundle file on disk records attempt 1."""
    requirements_dir = tmp_path / "requirements"
    requirements_dir.mkdir()
    state_dir = tmp_path / "release-state"
    _write_bundle(
        requirements_dir / "v2.0.1-bugfix-bundle-20260727.md",
        ["BUG-0001-foo.md", "BUG-0002-bar.md"],
    )

    record = compute_bundle_family(str(requirements_dir), "v2.0.1", str(state_dir))

    assert record["attempt"] == 1
    assert record["cargo"] == ["BUG-0001-foo.md", "BUG-0002-bar.md"]
    assert record["family_key"] is not None
    assert record["history"] == []


def test_compute_bundle_family_same_cargo_new_version_increments(tmp_path: Path) -> None:
    """The same bugs rebundled under a new patch number is attempt N+1."""
    requirements_dir = tmp_path / "requirements"
    requirements_dir.mkdir()
    state_dir = tmp_path / "release-state"
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True)

    # v2.0.0 carried this cargo and was cancelled at attempt 1.
    _write_bundle(
        requirements_dir / "v2.0.0-bugfix-bundle-20260720.md",
        ["BUG-0001-foo.md", "BUG-0002-bar.md"],
    )
    first_record = compute_bundle_family(str(requirements_dir), "v2.0.0", str(state_dir))
    assert first_record["attempt"] == 1
    (history_dir / "v2.0.0-cancelled-2026-07-20T00-00-00Z.json").write_text(
        json.dumps({
            "rc": "v2.0.0",
            "outcome": "cancelled",
            "family_key": first_record["family_key"],
            "attempt": first_record["attempt"],
        }),
        encoding="utf-8",
    )

    # v2.0.1 rebundles the SAME cargo set (same two bugs, order does not matter).
    _write_bundle(
        requirements_dir / "v2.0.1-bugfix-bundle-20260727.md",
        ["BUG-0002-bar.md", "BUG-0001-foo.md"],
    )
    second_record = compute_bundle_family(str(requirements_dir), "v2.0.1", str(state_dir))

    assert second_record["family_key"] == first_record["family_key"]
    assert second_record["attempt"] == 2
    assert len(second_record["history"]) == 1


def test_compute_bundle_family_different_cargo_starts_fresh_family(tmp_path: Path) -> None:
    """A different cargo set produces a different family_key and attempt 1,
    even though a cancelled record already exists for a different family."""
    requirements_dir = tmp_path / "requirements"
    requirements_dir.mkdir()
    state_dir = tmp_path / "release-state"
    history_dir = state_dir / "history"
    history_dir.mkdir(parents=True)

    _write_bundle(
        requirements_dir / "v2.0.0-bugfix-bundle-20260720.md",
        ["BUG-0001-foo.md", "BUG-0002-bar.md"],
    )
    first_record = compute_bundle_family(str(requirements_dir), "v2.0.0", str(state_dir))
    (history_dir / "v2.0.0-cancelled-2026-07-20T00-00-00Z.json").write_text(
        json.dumps({
            "rc": "v2.0.0",
            "outcome": "cancelled",
            "family_key": first_record["family_key"],
            "attempt": first_record["attempt"],
        }),
        encoding="utf-8",
    )

    # v2.0.1 bundles an entirely different set of bugs.
    _write_bundle(
        requirements_dir / "v2.0.1-bugfix-bundle-20260727.md",
        ["BUG-0099-unrelated.md"],
    )
    second_record = compute_bundle_family(str(requirements_dir), "v2.0.1", str(state_dir))

    assert second_record["family_key"] != first_record["family_key"]
    assert second_record["attempt"] == 1
    assert second_record["history"] == []


def test_compute_bundle_family_no_bundle_file_always_attempt_one(tmp_path: Path) -> None:
    """An operator-authored requirements doc with no matching bundle file has
    no cargo to track and always records attempt 1."""
    requirements_dir = tmp_path / "requirements"
    requirements_dir.mkdir()
    state_dir = tmp_path / "release-state"

    record = compute_bundle_family(str(requirements_dir), "v3.0.0", str(state_dir))

    assert record["bundle_file"] is None
    assert record["family_key"] is None
    assert record["cargo"] == []
    assert record["attempt"] == 1
