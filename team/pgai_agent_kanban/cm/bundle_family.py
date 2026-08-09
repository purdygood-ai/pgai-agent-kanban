#!/usr/bin/env python3
"""
bundle_family.py — Compute an RC's bundle-family identity and retry-attempt
number from the sorted set of items ("cargo") its requirements bundle
carries.

Computes `attempt: N` in release-state/<version>.json, carried across
cancel -> rebundle by BUNDLE FAMILY (keyed on the sorted cargo set -- the
same bugs rebundling under a new patch number is attempt N+1, not a fresh
attempt 1; a different cargo set starts a fresh family at attempt 1).

Family identity is the sorted, de-duplicated set of bundled-item basenames
(e.g. "BUG-0042-foo.md") read from a requirements bundle's
`## Bundled Items` section -- the same section and backtick-paren path
convention CM.md documents for the bundled-item promotion in
cm-release.sh Step 16b and the release-notes generator (team/scripts/cm/
release.sh). Two bundles carrying the identical cargo set (same bugs, any
order, any patch number) produce the identical family_key.

A bundle with no `## Bundled Items` matches (operator-authored requirements,
or no bundle file located at all for the version) has no family to track --
it always starts at attempt 1, per the "happy path never exceeds 1" AC.

Usage (CLI):
    python3 bundle_family.py compute <requirements_dir> <version> <state_dir>
        Prints the compute_bundle_family() result as JSON to stdout.

Usage (import):
    from pgai_agent_kanban.cm.bundle_family import compute_bundle_family
    record = compute_bundle_family(requirements_dir, "v2.0.1", state_dir)
    # record == {"family_key": ..., "attempt": N, "cargo": [...],
    #            "bundle_file": ..., "history": [...]}
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
from typing import Optional

# Matches CM.md's documented "## Bundled Items" section header and the
# backtick-paren absolute-path convention already parsed identically in
# team/scripts/cm/release.sh (Step 16b promotion + release-notes generation).
_BUNDLED_ITEMS_HEADER_RE = re.compile(r"^\s*##\s+Bundled Items\s*$", re.IGNORECASE)
_SECTION_HEADER_RE = re.compile(r"^\s*##")
_ITEM_PATH_RE = re.compile(r"\(`([^`]+)`\)")


def find_bundle_file(requirements_dir: str, version: str) -> Optional[str]:
    """Locate the requirements bundle file for *version*.

    Mirrors the glob convention CM.md documents ("The RC's bundle file...
    located by glob: <active-rc>-*bundle*.md") and that cm/release.sh's
    Step 16b and release-notes generation already use: "<version>-*.md"
    directly under requirements_dir, non-recursive. When more than one file
    matches (should not normally happen), the lexically-first match is
    returned for determinism.

    Returns None when requirements_dir does not exist or nothing matches --
    the caller treats this as "no bundle, no family to track".
    """
    if not requirements_dir or not os.path.isdir(requirements_dir):
        return None
    pattern = os.path.join(requirements_dir, f"{version}-*.md")
    matches = sorted(glob.glob(pattern))
    return matches[0] if matches else None


def parse_bundled_item_basenames(bundle_path: str) -> list:
    """Return the basenames of every item referenced in *bundle_path*'s
    `## Bundled Items` section, in file order (not sorted or de-duplicated).

    Uses the same section-detection and backtick-paren path extraction as
    cm/release.sh's release-notes generator. Returns an empty list when the
    file is missing, unreadable, or carries no `## Bundled Items` section --
    the "operator-authored requirements, no cargo to track" case.
    """
    try:
        text = open(bundle_path, "r", encoding="utf-8").read()
    except OSError:
        return []

    basenames: list = []
    in_section = False
    for line in text.splitlines():
        if _BUNDLED_ITEMS_HEADER_RE.match(line):
            in_section = True
            continue
        if in_section:
            if _SECTION_HEADER_RE.match(line):
                break
            match = _ITEM_PATH_RE.search(line)
            if match:
                basenames.append(os.path.basename(match.group(1).strip()))
    return basenames


def compute_family_key(basenames: list) -> Optional[str]:
    """Compute a deterministic family key from a cargo set.

    The key is "family-" + the first 16 hex characters of the sha256 digest
    of the sorted, de-duplicated basenames joined by newlines -- item order
    and duplicates in the source list never affect the key, only the SET of
    bugs bundled does. Returns None for an empty cargo set: there is no
    family to track (operator-authored requirements with no bundled items,
    or no bundle file located at all).
    """
    cargo = sorted(set(basenames))
    if not cargo:
        return None
    digest = hashlib.sha256("\n".join(cargo).encode("utf-8")).hexdigest()
    return f"family-{digest[:16]}"


def _iter_state_records(state_dir: str, exclude_path: Optional[str] = None):
    """Yield (path, record) for every readable JSON file directly under
    state_dir and state_dir/history, skipping exclude_path.

    exclude_path is normally the JSON path for the version currently being
    opened, so a record never counts as its own prior attempt (relevant on
    recovery re-runs where the active slot may already hold a stale
    in_progress record for the same version).
    """
    if not state_dir or not os.path.isdir(state_dir):
        return
    exclude_norm = os.path.normpath(exclude_path) if exclude_path else None
    for directory in (state_dir, os.path.join(state_dir, "history")):
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            if exclude_norm and os.path.normpath(path) == exclude_norm:
                continue
            try:
                record = json.loads(open(path, "r", encoding="utf-8").read())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                yield path, record


def compute_next_attempt(
    family_key: Optional[str],
    state_dir: str,
    *,
    exclude_path: Optional[str] = None,
):
    """Compute the next attempt number and prior-attempt history for a family.

    Scans every JSON record directly under state_dir (active per-RC slots)
    and state_dir/history (archived cancellations, written by
    write_rc_state.archive_cancelled) for records whose 'family_key' matches
    *family_key*. Only records with outcome == 'cancelled' advance the
    attempt count -- "the same bugs rebundling under a new patch number"
    means a prior attempt was CANCELLED and this cargo set came back; a
    'shipped' record closes that family's story rather than continuing it,
    so it is reported in history but does not bump the count.

    Returns (next_attempt, history):
      - next_attempt is 1 when family_key is None (no cargo -- e.g.
        operator-authored requirements) or when no matching cancelled
        record exists (first attempt for this family).
      - history is the list of matching records (any outcome), sorted by
        'attempt' ascending, for narrating the retry history (e.g. "attempt
        3/3; prior attempts cancelled for: v2.0.0, v2.0.1").
    """
    if not family_key:
        return 1, []

    matches = []
    for _path, record in _iter_state_records(state_dir, exclude_path=exclude_path):
        if record.get("family_key") == family_key:
            matches.append(record)

    cancelled_attempts = [
        int(record["attempt"])
        for record in matches
        if record.get("outcome") == "cancelled" and isinstance(record.get("attempt"), int)
    ]
    next_attempt = (max(cancelled_attempts) + 1) if cancelled_attempts else 1

    history = sorted(matches, key=lambda record: record.get("attempt", 0))
    return next_attempt, history


def compute_bundle_family(requirements_dir: str, version: str, state_dir: str) -> dict:
    """Compute the complete family record for opening *version*.

    Locates the bundle file for *version*, derives its cargo set and family
    key, and looks up the next attempt number against prior records in
    *state_dir*. This is the one call open-rc.sh needs to make; the result
    feeds directly into write_rc_state.write_open_with_attempt.

    Returns a dict:
        {
          "family_key": str | None,
          "attempt": int,
          "cargo": list[str],       # sorted, de-duplicated bundled basenames
          "bundle_file": str | None,
          "history": list[dict],
        }
    """
    bundle_file = find_bundle_file(requirements_dir, version)
    basenames = parse_bundled_item_basenames(bundle_file) if bundle_file else []
    family_key = compute_family_key(basenames)
    exclude_path = os.path.join(state_dir, f"{version}.json") if state_dir else None
    attempt, history = compute_next_attempt(family_key, state_dir, exclude_path=exclude_path)
    return {
        "family_key": family_key,
        "attempt": attempt,
        "cargo": sorted(set(basenames)),
        "bundle_file": bundle_file,
        "history": history,
    }


def main() -> None:
    """CLI entry point: dispatch the 'compute' subcommand.

    Usage: bundle_family.py compute <requirements_dir> <version> <state_dir>
    Prints compute_bundle_family()'s result as JSON to stdout.
    """
    if len(sys.argv) != 5 or sys.argv[1] != "compute":
        print(
            "Usage: bundle_family.py compute <requirements_dir> <version> <state_dir>",
            file=sys.stderr,
        )
        sys.exit(2)
    _, _, requirements_dir, version, state_dir = sys.argv
    result = compute_bundle_family(requirements_dir, version, state_dir)
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
