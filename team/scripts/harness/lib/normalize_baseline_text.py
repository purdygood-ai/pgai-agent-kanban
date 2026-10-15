#!/usr/bin/env python3
"""team/scripts/harness/lib/normalize_baseline_text.py

Scrub volatile fields from a captured operator-facing text surface, reading
the raw capture from stdin and writing the normalized text to stdout.

Used exclusively by team/scripts/harness/engine-behavior-baseline.sh — kept
as a standalone file (rather than an inline bash heredoc) because a heredoc
used as the script source for `python3 -` consumes stdin for the script
text itself, leaving nothing for the piped capture data to land on.

Usage:
    normalize_baseline_text.py <scratch-prefix> <temp-root> < raw.txt > normalized.txt

Only volatile fields are scrubbed — never whole lines of operator text:
  - ISO-8601 timestamps                          -> <TIMESTAMP>
  - durations following "in "/"took "/"elapsed "  -> <DURATION>
  - PIDs ("pid 12345", "PID=12345")               -> "pid <PID>"
  - absolute paths under the ephemeral scratch dir or the configured temp
    root                                          -> <TMPPATH>
  - git commit SHAs (7-40 lowercase hex chars containing at least one a-f
    letter, so plain numeric run/date/version literals are never touched)
                                                   -> <SHA>
"""

from __future__ import annotations

import re
import sys


def normalize(text: str, scratch_prefix: str, temp_root: str) -> str:
    """Return *text* with run-specific values replaced by stable placeholders.

    Temp paths (the run's *scratch_prefix*, anything under *temp_root*, then
    any ``/tmp/`` path) become ``<TMPPATH>``; ISO-8601 timestamps become
    ``<TIMESTAMP>``; durations become ``<DURATION>``; epoch seconds in
    ``*begin=`` / ``*end=`` fields become ``<EPOCH>``; pids become ``<PID>``;
    and hex git SHAs of 7-40 characters (containing a letter a-f) become
    ``<SHA>``.
    """
    # Absolute temp paths: the exact scratch dir used by this run, then a
    # generic fallback for the configured temp root (covers any additional
    # ephemeral paths the captured tools create on their own, e.g. metrics
    # scratch files), then a bare /tmp/ fallback as a last resort.
    if scratch_prefix:
        text = text.replace(scratch_prefix, "<TMPPATH>")
    if temp_root:
        text = re.sub(re.escape(temp_root) + r"\S*", "<TMPPATH>", text)
    text = re.sub(r"/tmp/\S+", "<TMPPATH>", text)

    # ISO-8601 timestamps (with or without a UTC/offset suffix).
    text = re.sub(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
        "<TIMESTAMP>",
        text,
    )

    # Durations reported after "in "/"took "/"elapsed ".
    for keyword in ("in ", "took ", "elapsed "):
        text = re.sub(
            r"(?<=" + re.escape(keyword) + r")\d+(\.\d+)?(ms|s|m|h)\b",
            "<DURATION>",
            text,
        )

    # Durations and epoch seconds reported in key=value log fields, e.g.
    # "elapsed=1s", "work_delta=3s", "work_begin=1788587959".
    text = re.sub(
        r"\b(elapsed|\w*delta)=\d+(\.\d+)?[a-zA-Z]*",
        lambda m: f"{m.group(1)}=<DURATION>",
        text,
    )
    text = re.sub(
        r"\b(\w*begin|\w*end)=\d{9,}\b",
        lambda m: f"{m.group(1)}=<EPOCH>",
        text,
    )

    # PIDs.
    text = re.sub(
        r"(?i)\bpid[=: ]\s*\d+",
        lambda m: re.sub(r"\d+$", "<PID>", m.group(0)),
        text,
    )

    # Git SHAs: 7-40 lowercase hex chars containing at least one a-f letter,
    # so plain numeric literals (dates, task-ID suffixes, version numbers)
    # are never mistaken for a SHA.
    def _sha_sub(m: "re.Match[str]") -> str:
        token = m.group(0)
        return "<SHA>" if any(c in "abcdef" for c in token) else token

    text = re.sub(r"\b[0-9a-f]{7,40}\b", _sha_sub, text)

    return text


def main() -> int:
    """Normalize stdin to stdout with :func:`normalize`.

    ``[SCRATCH_PREFIX [TEMP_ROOT]]`` (both optional, default empty). Always
    returns 0.
    """
    scratch_prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    temp_root = sys.argv[2] if len(sys.argv) > 2 else ""
    raw = sys.stdin.read()
    sys.stdout.write(normalize(raw, scratch_prefix, temp_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
