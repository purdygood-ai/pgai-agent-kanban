"""
test_docs_citation_scan_walk_contract.py
==========================================
Pins the real call contract between ``walk_docs_citation_closure`` and its
``resolved_target`` callback, directly against the shared module rather
than through either caller's own adapter.

The contract: ``resolved_target`` is consulted only for a citation
``is_resolved`` has already accepted. An unresolved citation is recorded
as a finding and ``resolved_target`` is never called for it. This is the
docstring paragraph that once claimed the opposite (``resolved_target``
"called for every citation regardless of is_resolved's answer") while the
walk loop only ever called it inside the ``is_resolved(...) is True``
branch -- see this module's own citation-walk module docstring for the
corrected paragraph. Both shipped callers were unaffected by the mismatch
only because each independently re-checks resolution inside its own
``resolved_target``, which made the discrepancy invisible from either
caller's own test suite. A test that drives the shared walk directly, with
a spy that would notice an unexpected call, is what keeps the documented
contract and the real call sequence from silently diverging again.
"""

from __future__ import annotations

import pathlib

from pgai_agent_kanban.lib.docs_citation_scan import walk_docs_citation_closure


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_resolved_target_is_never_called_when_every_citation_is_unresolved(
    tmp_path: pathlib.Path,
) -> None:
    """With an is_resolved that always returns False, the walk records one
    finding per citation and never calls resolved_target -- the real call
    contract, pinned with a spy so a future change to the walk loop that
    starts calling resolved_target on the unresolved branch fails this
    test instead of silently drifting from the documented contract."""
    seed = tmp_path / "seed.md"
    _write(
        seed,
        "See docs/one.md and docs/two.md for more, and docs/three.md too.\n",
    )

    resolved_target_calls: list[str] = []

    def resolved_target(cited: str) -> pathlib.Path | None:
        resolved_target_calls.append(cited)
        return None

    report = walk_docs_citation_closure(
        [seed],
        display_name=lambda path: path.name,
        is_resolved=lambda cited: False,
        resolved_target=resolved_target,
    )

    assert resolved_target_calls == [], (
        "resolved_target must not be called for any citation when "
        f"is_resolved rejects all of them; recorded calls: {resolved_target_calls}"
    )
    assert len(report.findings) == 3, (
        "Expected one finding per citation (docs/one.md, docs/two.md, "
        f"docs/three.md); got {len(report.findings)}: {report.findings}"
    )
    assert {finding.cited for finding in report.findings} == {
        "docs/one.md",
        "docs/two.md",
        "docs/three.md",
    }


def test_resolved_target_is_called_once_per_resolved_citation(
    tmp_path: pathlib.Path,
) -> None:
    """With an is_resolved that always returns True, resolved_target is
    consulted for every citation and no finding is recorded -- the
    complementary half of the contract, so this suite pins both branches
    rather than only the one the docstring got wrong."""
    seed = tmp_path / "seed.md"
    _write(seed, "See docs/one.md and docs/two.md for more.\n")

    resolved_target_calls: list[str] = []

    def resolved_target(cited: str) -> pathlib.Path | None:
        resolved_target_calls.append(cited)
        return None

    report = walk_docs_citation_closure(
        [seed],
        display_name=lambda path: path.name,
        is_resolved=lambda cited: True,
        resolved_target=resolved_target,
    )

    assert sorted(resolved_target_calls) == ["docs/one.md", "docs/two.md"], (
        "resolved_target must be called exactly once for every citation "
        f"is_resolved accepts; recorded calls: {resolved_target_calls}"
    )
    assert report.findings == []
