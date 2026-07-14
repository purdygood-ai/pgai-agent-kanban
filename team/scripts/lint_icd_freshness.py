#!/usr/bin/env python3
"""
lint_icd_freshness.py
=====================
Pre-flight gate that asserts the checked-in docs/api/icd.json artifact is
byte-identical to a fresh regeneration from the current codebase.

How it works:
  1. Regenerates the ICD to a temporary file (via the framework temp resolver,
     never directly to /tmp).
  2. Reads the checked-in artifact from docs/api/icd.json.
  3. Byte-compares the two.
  4. Exits 0 when they are identical; exits 1 when they differ, printing an
     actionable error message naming the stale artifact and the command to
     refresh it.

An empty or missing docs/api/icd.json is treated as stale (exit 1) — the
artifact must be generated and committed before this gate passes.

Wired into both gated runners (run-unit-tests.sh and run-integration-tests.sh)
as a pre-flight check before pytest, following the lint_api_parity pattern.

Usage:
    python3 team/scripts/lint_icd_freshness.py [--icd PATH] [--version-file PATH]

Options:
    --icd PATH          Path to the checked-in ICD artifact.
                        Default: docs/api/icd.json relative to the dev tree root.
    --version-file PATH Path to ICD_VERSION.
                        Default: docs/api/ICD_VERSION relative to the dev tree root.
    --help, -h          Show this help and exit.

Exit codes:
    0   Artifact is fresh (byte-identical to regeneration).
    1   Artifact is stale, missing, or regeneration failed.
    2   Usage error or internal failure.

Environment:
    PGAI_AGENT_KANBAN_TEMP_DIR  Framework temp root (defaults to the resolver
                                 fallback when unset).  Temp files are written
                                 under this root, never directly to /tmp.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

# ---------------------------------------------------------------------------
# Dev-tree root resolution.
# This file lives at team/scripts/lint_icd_freshness.py.
# Two parent levels: scripts/ → team/ → project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent           # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent              # project_root/
_DEFAULT_ICD_PATH = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_DEFAULT_VERSION_FILE = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"


def _get_temp_root() -> pathlib.Path:
    """Return the framework temp root directory.

    Uses PGAI_AGENT_KANBAN_TEMP_DIR when set, otherwise falls back to the
    documented resolver default.  Never writes directly to bare /tmp.

    Returns:
        Path to the temp root directory (created if absent).
    """
    # anti-pattern-allowlist: 2 (justification: the literal is the resolver's
    # documented last-resort fallback, mirroring the behaviour of temp.sh's
    # pgai_temp_dir(); callers of this script should set PGAI_AGENT_KANBAN_TEMP_DIR)
    root = pathlib.Path(
        os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "") or "/tmp/pgai_kanban_tmp"
    ) / "icd_freshness"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _regenerate_to_temp(
    temp_root: pathlib.Path,
    version_file: pathlib.Path,
) -> pathlib.Path | None:
    """Regenerate the ICD to a temp file and return the path.

    Args:
        temp_root:    Directory under the framework temp root to use.
        version_file: Path to ICD_VERSION.

    Returns:
        Path to the regenerated temp file, or None if regeneration failed
        (error already printed to stderr).
    """
    # Import the generator module.  Adjust sys.path if needed so the team
    # package is importable when this script is run directly (not via pytest).
    team_str = str(_TEAM_DIR)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    dev_tree_str = str(_DEV_TREE_ROOT)
    if dev_tree_str not in sys.path:
        sys.path.insert(0, dev_tree_str)

    try:
        from pgai_agent_kanban.api.generate_icd import generate_icd, read_icd_version
    except ImportError as exc:
        print(
            f"ERROR: cannot import generate_icd module: {exc}\n"
            "Ensure team/ is on PYTHONPATH or run from the repository root.",
            file=sys.stderr,
        )
        return None

    # Write to a named temp file under the framework temp root.
    fd, tmp_str = tempfile.mkstemp(
        prefix="icd_fresh_", suffix=".json", dir=str(temp_root)
    )
    os.close(fd)
    tmp_path = pathlib.Path(tmp_str)

    try:
        generate_icd(output_path=tmp_path, icd_version_file=version_file)
    except SystemExit:
        # generate_icd calls sys.exit(1) on failure; it already printed the error.
        tmp_path.unlink(missing_ok=True)
        return None

    return tmp_path


def check_freshness(
    icd_path: pathlib.Path = _DEFAULT_ICD_PATH,
    version_file: pathlib.Path = _DEFAULT_VERSION_FILE,
) -> bool:
    """Assert that icd_path is byte-identical to a fresh regeneration.

    Args:
        icd_path:     Path to the checked-in ICD artifact to verify.
        version_file: Path to ICD_VERSION (passed to the generator).

    Returns:
        True when the artifact is fresh; False when stale or missing.
    """
    temp_root = _get_temp_root()
    regen_path = _regenerate_to_temp(temp_root, version_file)
    if regen_path is None:
        return False

    try:
        checked_in = icd_path.read_bytes() if icd_path.exists() else None
        regenerated = regen_path.read_bytes()
    finally:
        regen_path.unlink(missing_ok=True)

    if checked_in is None:
        print(
            f"lint_icd_freshness: FAIL — artifact not found at {icd_path}\n"
            f"Run 'bash team/scripts/generate-icd.sh' from the repository root "
            "and commit the result.",
            file=sys.stderr,
        )
        return False

    if checked_in != regenerated:
        print(
            f"lint_icd_freshness: FAIL — {icd_path} is stale.\n"
            "The checked-in artifact does not match a fresh regeneration from the "
            "current codebase.\n"
            "Run 'bash team/scripts/generate-icd.sh' from the repository root "
            "and commit the updated artifact.",
            file=sys.stderr,
        )
        return False

    print(f"lint_icd_freshness: ok — {icd_path} is fresh.")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_icd_freshness.py",
        description=(
            "Assert that docs/api/icd.json is byte-identical to a fresh "
            "regeneration from the current codebase.  Exits 0 when fresh, "
            "1 when stale or missing."
        ),
    )
    parser.add_argument(
        "--icd",
        metavar="PATH",
        help=(
            "Path to the checked-in ICD artifact.  Default: docs/api/icd.json "
            "relative to the dev tree root."
        ),
    )
    parser.add_argument(
        "--version-file",
        metavar="PATH",
        help=(
            "Path to ICD_VERSION.  Default: docs/api/ICD_VERSION relative to "
            "the dev tree root."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI invocation.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        Exit code (0 = fresh, 1 = stale/error, 2 = usage error).
    """
    args = _parse_args(argv)
    icd_path = pathlib.Path(args.icd) if args.icd else _DEFAULT_ICD_PATH
    version_file = pathlib.Path(args.version_file) if args.version_file else _DEFAULT_VERSION_FILE

    ok = check_freshness(icd_path=icd_path, version_file=version_file)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
