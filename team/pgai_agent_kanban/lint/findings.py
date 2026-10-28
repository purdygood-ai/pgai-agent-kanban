"""
pgai_agent_kanban.lint.findings -- per-instance quality findings and the
``--findings-file`` writer shared by the quality-class lints.

A quality-class lint reports each thing it found as one finding object and
exits 0 whatever it found. With ``--findings-file PATH`` it writes those
objects as a JSON array, and the gated runners merge that array into their
aggregate findings file with ``gated_runner_merge_findings_file``
(``team/scripts/lib/gated_runner_lint.sh``), so each finding arrives there on
its own rather than as one coarse pass/fail entry.

A finding carries the keys ``lint_dual_channel_pair.py`` writes -- ``lint``,
``severity`` (always ``"quality"``), ``file``, ``line``, ``cause`` and
``remedy`` -- plus ``summary``, the one-line description the gated-runner
findings schema requires (equal to ``cause``). ``file`` and ``line`` are
omitted when a finding has no location, and a lint may add keys of its own.

Public API:
    quality_finding(lint, cause, remedy, *, file=None, line=None, **extra) -> dict
    write_findings_file(path, findings, *, lint) -> bool
    display_path(path, root) -> str
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional, Union

__all__ = ["display_path", "quality_finding", "write_findings_file"]


def display_path(path: Union[str, Path], root: Union[str, Path]) -> str:
    """*path* relative to *root* when it lies inside it, else *path* as given.

    Findings name files the way a reader at the repository root would type
    them; a path outside *root* (a test fixture, an explicit override) is
    kept whole rather than guessed at.
    """
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return str(candidate)


def quality_finding(
    lint: str,
    cause: str,
    remedy: str,
    *,
    file: Optional[str] = None,
    line: Optional[int] = None,
    **extra: Any,
) -> dict:
    """One per-instance quality finding.

    Args:
        lint:   The check's name, as the gated runners record it
                (``icd_freshness``, ``orphan_tests``, ...).
        cause:  What was found and where, in one line.
        remedy: A command or edit the reader can act on as printed.
        file:   The file the finding is about, repo-relative where possible.
        line:   The 1-indexed line in *file*, when the finding has one.
        extra:  Lint-specific keys, added as given.

    Returns:
        The finding as a JSON-serialisable dict.
    """
    finding: dict = {
        "lint": lint,
        "severity": "quality",
        "summary": cause,
        "cause": cause,
        "remedy": remedy,
    }
    if file is not None:
        finding["file"] = file
    if line is not None:
        finding["line"] = line
    finding.update(extra)
    return finding


def write_findings_file(
    path: Optional[Union[str, Path]], findings: list, *, lint: str
) -> bool:
    """Write *findings* to *path* as a JSON array, creating parent directories.

    The file is written even when *findings* is empty, so a reader can tell
    "ran and found nothing" from "did not run". A *path* of ``None`` or ``""``
    writes nothing. A write failure is narrated on stderr, naming the path,
    and never raised: the findings were already printed, and a quality-class
    lint does not fail its caller over its own report.

    Returns:
        True when the file was written (or none was asked for); False when
        the write failed.
    """
    if not path:
        return True
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"{lint}: could not write findings file {target}: {exc}", file=sys.stderr)
        return False
    return True
