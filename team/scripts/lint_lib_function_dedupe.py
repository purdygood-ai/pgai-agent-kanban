#!/usr/bin/env python3
"""
lint_lib_function_dedupe.py
===========================
Pre-flight lint that asserts no function name is defined in more than one
file matching ``team/scripts/lib/*.sh`` (non-recursive glob).

WHY THIS EXISTS
---------------
BUG-0031 uncovered two functions (``overwatch_backup_file``,
``overwatch_log_action``) defined with divergent signatures in both
``overwatch_lib.sh`` and ``overwatch_protocol.sh``.  Both files were
sourced by ``overwatch-sweep.sh``; bash's silent last-definition-wins
semantics meant the load order was the only thing preventing silent
argument misrouting.

This lint converts the BUG-0031 grep-zero acceptance criterion (§4) into a
permanent, gated-suite check.  When future edits reintroduce a duplicate
function name across lib/*.sh files, this script exits 1 and the suite
catches it before it reaches production.

WHAT IS CHECKED
---------------
Every function name defined at the top level of any file matching
``team/scripts/lib/*.sh`` (non-recursive, top-level only).  A function
definition is any line matching the pattern::

    name()

where ``name`` consists of lowercase letters, digits, and underscores
(shell function naming convention for this codebase).  Lines whose first
non-whitespace character is ``#`` are skipped.

If the same function name appears in TWO OR MORE files, the lint reports
the name and the files, then exits 1.

ALLOW-LIST
----------
Some lib files intentionally define the same function names as a
provider-interface implementation.  For example, ``wake_claude_provider.sh``
and ``wake_codex_provider.sh`` each define ``provider_preflight``,
``provider_model_preflight``, and ``provider_invoke_agent`` — only one file
is ever sourced per invocation.  These pairs are explicitly allow-listed via
``--allow-file`` so the lint does not flag them as a violation.

Default allow-listed files (both files in each pair must be listed)::

    wake_claude_provider.sh
    wake_codex_provider.sh

When all files that define a given duplicated name are in the allow-list,
that duplicate is silently skipped.  If even one file defining the name is
NOT in the allow-list, the duplicate is reported as a violation.

SCOPE
-----
Only the flat glob ``lib/*.sh`` is scanned — NOT subdirectories such as
``lib/overwatch-checks/``.  Verifier-test helpers like ``_pass``/``_fail``
that live in non-lib scripts (or in subdirectory scripts) are not in scope
and do not need special exemption.

EXIT CODES
----------
  0   No duplicates found; lint is green.
  1   One or more duplicated function names found; see stdout for details.
  2   Usage error or the lib directory was not found.

USAGE
-----
  python3 scripts/lint_lib_function_dedupe.py [--lib-dir PATH]
                                               [--allow-file FILENAME]
                                               [--verbose]

  --lib-dir PATH         Directory to scan (default: team/scripts/lib/
                         resolved from this script's own location).
  --allow-file FILENAME  Filename (basename only) whose functions are exempt
                         from the duplicate check.  May be repeated.
                         Default allow-list: wake_claude_provider.sh,
                         wake_codex_provider.sh.  Passing ``--allow-file``
                         once REPLACES the defaults; pass all desired files
                         when using this flag.
  --verbose, -v          Print each scanned file and function extracted from it.

EXAMPLES
--------
  # From repo root:
  python3 team/scripts/lint_lib_function_dedupe.py

  # Explicit path:
  python3 team/scripts/lint_lib_function_dedupe.py --lib-dir /path/to/lib

  # Custom allow-list (replaces defaults):
  python3 team/scripts/lint_lib_function_dedupe.py \\
      --allow-file wake_claude_provider.sh \\
      --allow-file wake_codex_provider.sh \\
      --allow-file my_new_provider.sh
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Default allow-list
#
# Files in this set are provider-interface implementations that intentionally
# define the same function names.  A duplicate is only reported when at least
# one of the files defining the name is NOT in this set.
# ---------------------------------------------------------------------------
_DEFAULT_ALLOW_FILES: frozenset[str] = frozenset({
    "wake_claude_provider.sh",
    "wake_codex_provider.sh",
})

# ---------------------------------------------------------------------------
# Function-definition pattern
#
# Matches a shell function definition at the start of a line:
#   name()
# where name is [a-z0-9_]+.  The parentheses may be followed by optional
# whitespace and an open brace on the same line, but only the name and ()
# are required for detection.
#
# Case-sensitive: shell function names in this codebase are conventionally
# lowercase.  Upper-case names (like RETIRED_IDENTIFIERS-style constants)
# are not functions.
# ---------------------------------------------------------------------------
_FUNC_DEF_RE: re.Pattern[str] = re.compile(
    r"^([a-z][a-z0-9_]*)\(\)"
)


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------


def find_functions_in_file(path: Path) -> list[str]:
    """Return the list of function names defined in a single shell file.

    Lines whose first non-whitespace character is ``#`` are comment lines
    and are skipped.  Only lines matching ``name()`` at column 0 are
    extracted.

    Returns function names in definition order (duplicates within the same
    file are included as-is; cross-file deduplication is the caller's job).

    Raises FileNotFoundError if *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"lib script not found: {path}")

    names: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        m = _FUNC_DEF_RE.match(line)
        if m:
            names.append(m.group(1))

    return names


def find_duplicate_functions(
    lib_dir: Path,
    allow_files: frozenset[str] | None = None,
    verbose: bool = False,
) -> dict[str, list[str]]:
    """Scan ``lib_dir/*.sh`` for function names defined in more than one file.

    Parameters
    ----------
    lib_dir:
        Directory containing the lib ``*.sh`` files to scan.  Non-recursive:
        only files directly inside ``lib_dir`` are examined.
    allow_files:
        Set of basenames (e.g. ``{"wake_claude_provider.sh"}``) whose
        functions are exempt from duplicate detection.  A duplicate is only
        reported when at least one defining file is NOT in this set.
        Defaults to ``_DEFAULT_ALLOW_FILES`` when ``None``.
    verbose:
        When True, prints each file and the functions extracted from it.

    Returns a dict mapping each duplicated function name to the sorted list
    of filenames (basenames) where it is defined.  Functions defined in only
    one file are not included.  Functions whose all defining files are in
    ``allow_files`` are not included.

    Returns an empty dict when no violations are found.

    Raises FileNotFoundError if ``lib_dir`` does not exist.
    """
    if allow_files is None:
        allow_files = _DEFAULT_ALLOW_FILES

    if not lib_dir.is_dir():
        raise FileNotFoundError(f"lib directory not found: {lib_dir}")

    # Map: function_name → list of filenames (basenames) that define it.
    func_to_files: dict[str, list[str]] = defaultdict(list)

    sh_files = sorted(lib_dir.glob("*.sh"))
    for sh_file in sh_files:
        if verbose:
            print(f"  scanning: {sh_file}", flush=True)
        try:
            funcs = find_functions_in_file(sh_file)
        except OSError as exc:
            print(f"WARNING: could not read {sh_file}: {exc}", file=sys.stderr)
            continue
        if verbose and funcs:
            print(f"    functions: {', '.join(funcs)}", flush=True)
        for func_name in funcs:
            func_to_files[func_name].append(sh_file.name)

    # Collect violations: function names defined in more than one file,
    # where at least one defining file is NOT in the allow-list.
    violations: dict[str, list[str]] = {}
    for func_name, filenames in func_to_files.items():
        if len(filenames) <= 1:
            continue
        # All files in the allow-list → skip this duplicate.
        if allow_files and all(f in allow_files for f in filenames):
            if verbose:
                print(
                    f"  allow-list: skipping duplicate '{func_name}' "
                    f"(all defining files are in allow-list: "
                    f"{', '.join(sorted(filenames))})",
                    flush=True,
                )
            continue
        violations[func_name] = sorted(filenames)

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_lib_function_dedupe.py",
        description=(
            "Pre-flight lint: assert that no function name is defined in more "
            "than one file matching team/scripts/lib/*.sh.  "
            "Exits 1 when any duplicated name is found outside the allow-list."
        ),
    )
    parser.add_argument(
        "--lib-dir",
        metavar="PATH",
        help=(
            "Directory to scan (default: team/scripts/lib/ resolved from "
            "this script's own location)."
        ),
    )
    parser.add_argument(
        "--allow-file",
        metavar="FILENAME",
        action="append",
        dest="allow_files",
        help=(
            "Basename of a lib/*.sh file whose functions are exempt from the "
            "duplicate check (provider-interface implementations).  May be "
            "repeated.  Passing this flag once REPLACES the defaults; list "
            "all desired files when using this flag.  "
            "Default: wake_claude_provider.sh, wake_codex_provider.sh."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each scanned file and the functions extracted from it.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the lib-function-dedupe lint.

    Returns 0 (clean), 1 (violations found), or 2 (usage / path error).
    """
    args = _parse_args(argv)

    # Resolve the lib directory.
    if args.lib_dir:
        lib_dir = Path(args.lib_dir).resolve()
    else:
        script_dir = Path(__file__).resolve().parent  # team/scripts/
        lib_dir = script_dir / "lib"

    if not lib_dir.is_dir():
        print(
            f"ERROR: lib directory not found: {lib_dir}\n"
            "Use --lib-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    # Resolve the allow-list.
    if args.allow_files is not None:
        allow_files: frozenset[str] = frozenset(args.allow_files)
    else:
        allow_files = _DEFAULT_ALLOW_FILES

    if args.verbose:
        print(
            f"lint_lib_function_dedupe: scanning {lib_dir}",
            flush=True,
        )
        if allow_files:
            print(
                f"  allow-listed files: {', '.join(sorted(allow_files))}",
                flush=True,
            )

    try:
        violations = find_duplicate_functions(
            lib_dir,
            allow_files=allow_files,
            verbose=args.verbose,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    sh_count = len(list(lib_dir.glob("*.sh")))

    if violations:
        print(
            f"\nlint_lib_function_dedupe: FAIL — "
            f"{len(violations)} function name(s) defined in more than one "
            f"team/scripts/lib/*.sh file:\n",
            flush=True,
        )
        for func_name, filenames in sorted(violations.items()):
            files_str = ", ".join(filenames)
            print(
                f"  {func_name}() — defined in: {files_str}",
                flush=True,
            )
        print(
            "\nEach function name must appear in exactly one lib/*.sh file.\n"
            "Fix: remove or rename the duplicate definition, or add the "
            "defining files to the allow-list (--allow-file) if this is an "
            "intentional provider-interface implementation.",
            flush=True,
        )
        return 1

    print(
        f"lint_lib_function_dedupe: ok — "
        f"{sh_count} lib/*.sh file(s) scanned, no duplicate function names.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
