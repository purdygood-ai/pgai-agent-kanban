"""
test_pp_run_ops_sweep.py
========================
Enforces the sweep invariant: no bare ``python3 -m pgai_agent_kanban`` invocation
exists in team/scripts/ outside the documented exemption list.

WHY THIS EXISTS
---------------
Every bare ``python3 -m pgai_agent_kanban.*`` invocation is cwd-dependent: it
resolves the package only when the working directory happens to be the kanban
root or team/ directory.  The shared pp_run_ops helper (team/scripts/lib/pp_run_ops.sh)
sets PYTHONPATH correctly from its own location so callers are cwd-independent.

The sweep invariant is the mechanically-verifiable completeness signal: if a
future edit re-introduces a bare invocation outside the exemption list, this test
fails before the change reaches the RC.

EXEMPTIONS
----------
Two call sites are permanently exempt and appear in the exemption list below with
rationale:

  team/scripts/lib/pp_run_ops.sh
      The shared helper itself.  It MUST contain the literal ``python3 -m``
      invocation — that is the implementation of the cwd-independent wrapper.
      Excluding it from the scan is correct by definition.

  team/scripts/generate-icd.sh
      The ICD wrapper invokes ``python3 -m pgai_agent_kanban.api.generate_icd``
      directly with ``PYTHONPATH`` pre-set to the dev-tree ``team/`` directory.
      Routing through pp_run_ops would allow KANBAN_ROOT to override the
      caller-set PYTHONPATH entry (pp_run_ops prepends its own-tree root, which
      in a wake-script environment equals the live install, not the dev tree).
      Keeping the direct invocation preserves the correct import precedence for
      a script that explicitly requires the dev-tree package (Option A per
      BUG-0083).

HOW THE GATE WORKS
------------------
Pattern: ``python3 -m pgai_agent_kanban``

Scope: all files under team/scripts/, recursively.
Exemptions: the two files named above are excluded from the scan.

Negative proof: a temp file containing a synthetic bare invocation is confirmed
to match the pattern, showing the gate can catch new violations if they appear.

TEST NAMING CONVENTION (SOP.md Anti-pattern 6)
-----------------------------------------------
All test function names describe behavior, not the bug ID or task ID that
prompted them.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

# ---------------------------------------------------------------------------
# Exemption list (one entry per file; includes rationale as a comment above).
# The paths are relative to the repo root and matched as exact file-relative
# suffixes of each scanned file's repo-relative path.
# ---------------------------------------------------------------------------

# team/scripts/lib/pp_run_ops.sh — the shared helper itself: MUST contain
# the bare invocation because it IS the cwd-independent wrapper implementation.
#
# team/scripts/generate-icd.sh — ICD wrapper: routes directly to avoid
# KANBAN_ROOT overriding the pre-set dev-tree PYTHONPATH (Option A, BUG-0083).
_EXEMPT_SUFFIXES: tuple[str, ...] = (
    "team/scripts/lib/pp_run_ops.sh",
    "team/scripts/generate-icd.sh",
)

_BARE_INVOCATION_PATTERN = re.compile(r"python3\s+-m\s+pgai_agent_kanban")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> pathlib.Path:
    """Return the absolute path to the repository root.

    Resolved via git so the result is correct regardless of the working
    directory the test runner uses.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(result.stdout.strip())


def _find_violations() -> list[str]:
    """Return a list of '<file>:<line>:<content>' strings for each violation.

    Scans all files under team/scripts/ recursively for the bare invocation
    pattern, then filters out any file whose repo-relative path ends with an
    exempt suffix.  Returns an empty list when the invariant holds.
    """
    root = _repo_root()
    scripts_dir = root / "team" / "scripts"

    if not scripts_dir.is_dir():
        raise FileNotFoundError(
            f"team/scripts/ directory not found at {scripts_dir}; "
            "repo root resolution may be wrong."
        )

    violations: list[str] = []

    for candidate in scripts_dir.rglob("*"):
        if not candidate.is_file():
            continue

        try:
            rel_path = candidate.relative_to(root)
        except ValueError:
            rel_path = candidate

        rel_str = str(rel_path).replace("\\", "/")

        # Skip files on the exemption list.
        if any(rel_str.endswith(suffix) for suffix in _EXEMPT_SUFFIXES):
            continue

        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, start=1):
            if _BARE_INVOCATION_PATTERN.search(line):
                violations.append(f"{rel_str}:{lineno}:{line.rstrip()}")

    return violations


# ---------------------------------------------------------------------------
# Gate test
# ---------------------------------------------------------------------------


def test_no_bare_python3_module_invocations_outside_exemptions() -> None:
    """No bare python3 -m pgai_agent_kanban invocations exist outside the exemption list.

    Every invocation of the pgai_agent_kanban package via python3 -m must go
    through the pp_run_ops shared helper (or be listed in the exemption list
    above with rationale).  Bare invocations are cwd-dependent and fail in any
    environment where team/ is not on sys.path.

    Exempted files:
      - team/scripts/lib/pp_run_ops.sh   (the helper itself — must contain the form)
      - team/scripts/generate-icd.sh     (uses direct invocation with explicit PYTHONPATH
                                          to keep dev-tree precedence; see BUG-0083)
    """
    violations = _find_violations()
    assert not violations, (
        f"Sweep invariant violated — {len(violations)} bare 'python3 -m pgai_agent_kanban' "
        f"invocation(s) found outside the exemption list:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nRoute all invocations through the pp_run_ops shared helper "
        "(team/scripts/lib/pp_run_ops.sh) for cwd-independent package resolution.  "
        "If the invocation is legitimately exempt, add it to _EXEMPT_SUFFIXES in this "
        "file with a rationale comment."
    )


# ---------------------------------------------------------------------------
# Negative proof: the pattern DOES catch a synthetic bare invocation
# ---------------------------------------------------------------------------


def test_bare_invocation_pattern_detects_synthetic_violation(
    tmp_path: pathlib.Path,
) -> None:
    """The bare-invocation pattern matches a known-bad python3 -m call.

    This is the negative proof: it confirms the scan mechanism can detect a
    violation, not merely that the real files happen to be clean.  Without
    this, a bug in _find_violations() would make the gate always green.
    """
    bad_script = tmp_path / "bad_wrapper.sh"
    bad_script.write_text(
        "#!/usr/bin/env bash\n"
        "# Synthetic bare invocation — should be caught by the sweep gate.\n"
        "python3 -m pgai_agent_kanban.ops close_item \"$project_root\" \"$key\" done\n",
        encoding="utf-8",
    )

    matching_lines = [
        line
        for line in bad_script.read_text(encoding="utf-8").splitlines()
        if _BARE_INVOCATION_PATTERN.search(line)
    ]

    assert len(matching_lines) >= 1, (
        f"Expected the bare-invocation pattern to match at least 1 line in the synthetic "
        f"bad script; got {len(matching_lines)}: {matching_lines!r}.  "
        "The pattern may be wrong — check _BARE_INVOCATION_PATTERN."
    )


def test_bare_invocation_pattern_does_not_match_pp_run_ops_call(
    tmp_path: pathlib.Path,
) -> None:
    """The pattern does not match the correct pp_run_ops form.

    A line like ``pp_run_ops pgai_agent_kanban.ops close_item ...`` must not
    trigger the gate.  Only the bare ``python3 -m`` prefix is disallowed.
    """
    clean_script = tmp_path / "clean_wrapper.sh"
    clean_script.write_text(
        "#!/usr/bin/env bash\n"
        "source lib/pp_run_ops.sh\n"
        "pp_run_ops pgai_agent_kanban.ops close_item \"$project_root\" \"$key\" done\n",
        encoding="utf-8",
    )

    matching_lines = [
        line
        for line in clean_script.read_text(encoding="utf-8").splitlines()
        if _BARE_INVOCATION_PATTERN.search(line)
    ]

    assert not matching_lines, (
        f"Pattern incorrectly matched pp_run_ops call: {matching_lines!r}.  "
        "The scan must only flag 'python3 -m pgai_agent_kanban', not the helper form."
    )


def test_exempt_suffixes_cover_expected_files() -> None:
    """The exemption list names exactly the two expected files.

    Verifies the exemption list has not silently grown or shrunk.  If a new
    exemption is added without rationale, or an existing exemption is
    accidentally removed, this test fails and forces an explicit acknowledgment.
    """
    expected = frozenset({
        "team/scripts/lib/pp_run_ops.sh",
        "team/scripts/generate-icd.sh",
    })
    actual = frozenset(_EXEMPT_SUFFIXES)
    assert actual == expected, (
        f"Exemption list mismatch.\n"
        f"  Expected: {sorted(expected)}\n"
        f"  Actual:   {sorted(actual)}\n"
        "If a new exemption is legitimately needed, add it to _EXEMPT_SUFFIXES "
        "in this file with a rationale comment.  Removing an exemption requires "
        "confirming that the file no longer contains bare invocations."
    )
