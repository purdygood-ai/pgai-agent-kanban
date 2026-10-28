"""
hand_filed_bug.py
=================
Writes a hand-filed bug report into a bugs/ directory for the autonomous
bug filers' dedup tests (lint, deep-verify and ship-policy).

A hand-filed report follows the BUG-TEMPLATE.md header and section order
but carries none of a filer's own recorded sections (``## Hygiene Lint
Class``, ``## Deep-Verify Pairs``, ``## Ship-Policy Input``), so a filer's
own dedup key never matches it. Only the shared same-defect rule
(pgai_agent_kanban.lib.bug_filing.find_same_defect) can: by its slug, or
by a body that cites another bug as its defect.
"""

from __future__ import annotations

import pathlib

#: Statuses that keep a bug live for dedup, and the closed ones that never do.
LIVE_STATUSES = ("open", "running", "needs-operator")
CLOSED_STATUSES = ("done", "wont-do", "duplicate")


def write_hand_filed_bug(
    bugs_dir: pathlib.Path,
    bug_id: str,
    slug: str,
    *,
    status: str = "open",
    body: str = "Fixture report filed by hand.",
) -> pathlib.Path:
    """Write ``<bug_id>-<slug>.md`` into *bugs_dir* (created when absent)
    with ``## Status`` *status* and *body* as its symptom; return its path."""
    bugs_dir.mkdir(parents=True, exist_ok=True)
    text = (
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        "**Filed By:** TESTER -- fixture report filed by hand.\n"
        "**Date:** 2026-09-01\n"
        "**Severity:** medium\n\n"
        f"## Status\n{status}\n\n"
        "## Category\nfixture\n\n"
        "---\n\n"
        f"## Symptom\n{body}\n"
    )
    path = bugs_dir / f"{bug_id}-{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path
