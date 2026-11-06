"""
test_bug_same_defect.py
=========================
Unit tests for the one same-defect rule, pgai_agent_kanban.lib.bug_filing's
find_same_defect, in both directions.

A new bug draft reports the same defect as a live bug -- status open,
running or needs-operator -- when its body cites that bug as the defect it
reports, or when its slug differs from that bug's slug only by re-file
markers at its start or end: a re-file word, an ordinal of two or more, or
a trailing counter.

Proven here, on throwaway bugs/ directories under tmp_path:

  Matches (and names the live bug's ID):
    - a body citing a fixture bug at open, running or needs-operator, in
      each citation shape the rule documents;
    - a slug that differs from a live bug's slug only by a re-file marker
      at its start or end (again, redux, second, 2nd, a trailing -2, ...).

  Does not match:
    - a body that mentions a live bug's ID in passing ("as BUG-NNNN before
      it", "see also", "fixed for"), remarks on its status ("is still
      open"), or names it in a hypothetical ("when BUG-NNNN recurs");
    - a body citing a bug that is done, wont-do or duplicate, or absent;
    - a negated citation ("not the same defect as");
    - a citation inside an HTML comment or a fenced code block;
    - an identical slug, a slug differing by a real word, a number or word
      in the middle of a slug (intake-exits-1 vs intake-exits-2), still,
      once, another or first anywhere, a slug of re-file markers only.

Fixture bug IDs are in the fictitious BUG-9xxx range.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.lib.bug_filing import (
    ELIGIBLE_DEDUP_STATUSES,
    REFILE_ORDINALS,
    REFILE_WORDS,
    bug_slug,
    cited_defect_ids,
    find_same_defect,
    slugs_differ_only_by_count_words,
)

_LIVE_STATUSES = ["open", "running", "needs-operator"]
_CLOSED_STATUSES = ["done", "wont-do", "duplicate"]


def _bug(bugs_dir: pathlib.Path, name: str, status: str) -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    stem = name[:-3]
    path = bugs_dir / name
    path.write_text(
        f"# {stem}\n\n**Bug ID:** {stem}\n**Severity:** medium\n\n"
        f"## Status\n{status}\n\n## Symptom\nThe lock file is left behind.\n",
        encoding="utf-8",
    )
    return path


def _draft_body(symptom: str) -> str:
    return (
        "# BUG-9500-new-draft\n\n**Bug ID:** BUG-9500-new-draft\n**Severity:** medium\n\n"
        f"## Status\nopen\n\n## Symptom\n{symptom}\n"
    )


# ---------------------------------------------------------------------------
# The live-status set
# ---------------------------------------------------------------------------


def test_live_statuses_are_open_running_and_needs_operator() -> None:
    assert ELIGIBLE_DEDUP_STATUSES == frozenset(_LIVE_STATUSES)


# ---------------------------------------------------------------------------
# Citation: matches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", _LIVE_STATUSES)
def test_a_body_citing_a_live_bug_as_its_defect_matches_and_names_it(tmp_path: pathlib.Path, status: str) -> None:
    bugs = tmp_path / "bugs"
    existing = _bug(bugs, "BUG-9172-stale-lock-file.md", status)
    body = _draft_body("Same defect as BUG-9172: the lock file is left behind after a crash.")

    match = find_same_defect(str(bugs), "BUG-9500-lock-not-removed.md", body)

    assert match is not None
    assert match.bug.bug_id == "BUG-9172"
    assert match.bug.status == status
    assert match.bug.path == str(existing)
    assert match.rule == "cites"
    assert "BUG-9172" in match.why


@pytest.mark.parametrize(
    "symptom",
    [
        "This is the same defect as BUG-9172.",
        "**Duplicate Of:** BUG-9172",
        "## Re-file of\n\nBUG-9172",
        "Refile of `BUG-9172`, which no ticket picked up.",
        "A recurrence of BUG-9172 on the second host.",
        "Reopens BUG-9172.",
        "Same bug as the open BUG-9172.",
        "BUG-9172 still reproduces on the release branch.",
        "BUG-9172 is still broken and the lock is still left behind.",
        "BUG-9172 still recurs after every restart.",
        "BUG-9172 has recurred again since the restart.",
    ],
)
def test_each_documented_citation_shape_matches(tmp_path: pathlib.Path, symptom: str) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", "open")

    match = find_same_defect(str(bugs), "BUG-9500-unrelated-title.md", _draft_body(symptom))

    assert match is not None, symptom
    assert match.bug.bug_id == "BUG-9172"
    assert match.rule == "cites"


def test_a_citation_wins_over_a_slug_match(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9100-stale-lock-file.md", "open")
    _bug(bugs, "BUG-9172-other-defect.md", "running")
    body = _draft_body("Duplicate of BUG-9172.")

    match = find_same_defect(str(bugs), "BUG-9500-stale-lock-file-again.md", body)

    assert match is not None
    assert (match.bug.bug_id, match.rule) == ("BUG-9172", "cites")


# ---------------------------------------------------------------------------
# Citation: does not match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symptom",
    [
        "The wake fails as BUG-9172 before it, but on the queue file, not the lock.",
        "See also BUG-9172 for the lock side.",
        "The test added for BUG-9172 covers a different path.",
        "Found while reading the fix for BUG-9172.",
        "This is not the same defect as BUG-9172: the queue file, not the lock.",
        "Not a duplicate of BUG-9172.",
        "<!-- duplicate of BUG-9172 -->\nThe queue file is truncated.",
        "Log:\n\n```\nclosed as duplicate of BUG-9172\n```\n\nThe queue file is truncated.",
        "Related: BUG-9172 is still open, but this is a different defect.",
        "BUG-9172 is still open and the queue file is truncated too.",
        "BUG-9172 is still present on the dashboard; this is the queue file.",
        "When BUG-9172 recurs the check reads clean.",
        "If BUG-9172 reappears, the queue file is truncated as well.",
    ],
)
def test_a_live_bug_mentioned_in_passing_does_not_match(tmp_path: pathlib.Path, symptom: str) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", "open")

    assert find_same_defect(str(bugs), "BUG-9500-queue-file-truncated.md", _draft_body(symptom)) is None


@pytest.mark.parametrize("status", _CLOSED_STATUSES)
def test_a_body_citing_a_closed_bug_does_not_match(tmp_path: pathlib.Path, status: str) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", status)
    body = _draft_body("Same defect as BUG-9172: the lock file is left behind again.")

    assert find_same_defect(str(bugs), "BUG-9500-lock-not-removed.md", body) is None


def test_a_body_citing_a_bug_that_does_not_exist_does_not_match(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", "open")

    body = _draft_body("Duplicate of BUG-9999.")
    assert find_same_defect(str(bugs), "BUG-9500-lock-not-removed.md", body) is None


def test_a_missing_bugs_directory_matches_nothing(tmp_path: pathlib.Path) -> None:
    body = _draft_body("Duplicate of BUG-9172.")
    assert find_same_defect(str(tmp_path / "absent"), "BUG-9500-x.md", body) is None


def test_a_status_remark_about_a_live_bug_is_not_the_same_defect(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", "open")
    body = "Related: BUG-9172 is still open, but this is a different defect."

    assert find_same_defect(str(bugs), "BUG-9500-queue-file-truncated.md", body) is None


def test_a_recurrence_verb_cites_only_with_still_or_again() -> None:
    assert cited_defect_ids("When BUG-9172 recurs the check reads clean.") == []
    assert cited_defect_ids("BUG-9172 still reproduces") == ["BUG-9172"]
    assert cited_defect_ids("BUG-9172 recurs again") == ["BUG-9172"]


def test_cited_defect_ids_are_canonical_and_listed_once_in_order() -> None:
    text = "Duplicate of bug-9172. Same defect as BUG-9001. BUG-9172 still reproduces."
    assert cited_defect_ids(text) == ["BUG-9172", "BUG-9001"]


# ---------------------------------------------------------------------------
# Slug: matches and non-matches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "draft_name",
    [
        "BUG-9500-stale-lock-file-again.md",
        "BUG-9500-stale-lock-file-redux.md",
        "BUG-9500-refiled-stale-lock-file.md",
        "BUG-9500-second-stale-lock-file.md",
        "BUG-9500-stale-lock-file-2nd.md",
        "BUG-9500-stale-lock-file-2.md",
        "BUG-9500-20261025-stale-lock-file-third.md",
    ],
)
@pytest.mark.parametrize("status", _LIVE_STATUSES)
def test_a_slug_differing_only_by_an_ordinal_or_count_word_matches(
    tmp_path: pathlib.Path, draft_name: str, status: str
) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", status)

    match = find_same_defect(str(bugs), draft_name, _draft_body("The lock file is left behind."))

    assert match is not None, draft_name
    assert (match.bug.bug_id, match.rule) == ("BUG-9172", "slug")
    assert "stale-lock-file" in match.why


def test_an_existing_slug_carrying_the_count_word_matches_too(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file-again.md", "needs-operator")

    match = find_same_defect(str(bugs), "BUG-9500-stale-lock-file.md", _draft_body("x"))

    assert match is not None and match.bug.bug_id == "BUG-9172"


@pytest.mark.parametrize("status", _CLOSED_STATUSES)
def test_a_count_word_slug_of_a_closed_bug_does_not_match(tmp_path: pathlib.Path, status: str) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", status)

    assert find_same_defect(str(bugs), "BUG-9500-stale-lock-file-again.md", _draft_body("x")) is None


@pytest.mark.parametrize(
    ("slug_a", "slug_b", "expected"),
    [
        ("stale-lock-file", "stale-lock-file-again", True),
        ("stale-lock-file-2", "stale-lock-file-3rd", True),
        ("stale-lock-file", "stale-lock-file", False),
        ("stale-lock-file", "stale-lock-files", False),
        ("stale-lock-file", "stale-queue-file", False),
        ("two-projects-share-lock", "projects-share-lock", False),
        ("again", "second", False),
        ("depositor-a", "depositor-b", False),
        ("lock-leak", "lock-leak-again", True),
        ("second-lock-leak", "lock-leak-3", True),
        ("lock-leak", "lock-leak-repeated", True),
        ("lock-leak", "2nd-lock-leak", True),
        ("intake-exits-1-on-bad-name", "intake-exits-2-on-bad-name", False),
        ("docs-still-halt-on-tester-problems", "docs-halt-on-tester-problems", False),
        ("release-runs-once-per-wake", "release-runs-per-wake", False),
        ("first-wake-after-reboot-skips-guard", "wake-after-reboot-skips-guard", False),
        ("lock-leak", "lock-leak-still", False),
        ("lock-leak", "another-lock-leak", False),
        ("lock-leak", "lock-leak-1st", False),
        ("wake-runs-again-after-halt", "wake-runs-after-halt", False),
        ("lock-leak", "3-lock-leak", False),
    ],
)
def test_slugs_differ_only_by_count_words(slug_a: str, slug_b: str, expected: bool) -> None:
    assert slugs_differ_only_by_count_words(slug_a, slug_b) is expected
    assert slugs_differ_only_by_count_words(slug_b, slug_a) is expected


def test_a_number_in_the_middle_of_a_slug_names_a_distinct_defect(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-intake-exits-1-on-bad-name.md", "open")

    assert find_same_defect(str(bugs), "BUG-9500-intake-exits-2-on-bad-name.md", _draft_body("x")) is None


def test_an_identical_slug_alone_is_not_a_match(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, "BUG-9172-stale-lock-file.md", "open")

    assert find_same_defect(str(bugs), "BUG-9172-stale-lock-file.md", _draft_body("x")) is None


def test_bug_slug_drops_the_number_and_the_date_field() -> None:
    assert bug_slug("BUG-9172-stale-lock-file.md") == "stale-lock-file"
    assert bug_slug("BUG-9172-20261025-stale-lock-file.md") == "stale-lock-file"
    assert bug_slug("PRIORITY-9172-x.md") is None


def test_the_refile_vocabulary_holds_refile_words_and_ordinals_of_two_or_more() -> None:
    assert {"again", "redux", "recurring", "repeat", "repeated", "refile", "refiled"} <= REFILE_WORDS
    assert {"second", "third", "tenth"} <= REFILE_ORDINALS
    assert not {"first", "still", "once", "another", "one", "two", "three"} & (REFILE_WORDS | REFILE_ORDINALS)
