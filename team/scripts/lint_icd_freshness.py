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
  4. When they differ, prints an actionable message naming the stale
     artifact and the command to refresh it, and records it as a finding.

A missing docs/api/icd.json is reported as stale, and a regeneration that
fails is reported as a finding too (freshness could not be checked).

CONVERT (quality class, team/SOP-core.md "Gates and Refusals"): a stale ICD
is documentation drift with zero runtime-failure risk.  Every finding is
printed and, with --findings-file, written as a JSON array (the shape
pgai_agent_kanban.lint.findings defines).  Both gated runners
(run-unit-tests.sh and run-integration-tests.sh) pass --findings-file and
merge the array per instance with gated_runner_merge_findings_file.

Usage:
    python3 team/scripts/lint_icd_freshness.py [--icd PATH] [--version-file PATH]
                                               [--findings-file PATH]

Options:
    --icd PATH            Path to the checked-in ICD artifact.
                          Default: docs/api/icd.json relative to the dev tree root.
    --version-file PATH   Path to ICD_VERSION.
                          Default: docs/api/ICD_VERSION relative to the dev tree root.
    --findings-file PATH  Write the findings as a JSON array to PATH (parent
                          directories created; written even when empty).
    --help, -h            Show this help and exit.

Exit codes:
    0   The check ran, whatever it found (a stale, missing or unregenerable
        artifact is a finding, not a failure).
    2   Usage error.

Environment:
    PGAI_AGENT_KANBAN_TEMP_DIR  Framework temp root override.  When unset the
                                 root comes from kanban.cfg [paths], resolved
                                 by pgai_agent_kanban.lib.temp_root.  Temp
                                 files are written under this root, never
                                 directly to /tmp.
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
_TEAM_DIR = _THIS_FILE.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent  # project_root/
_DEFAULT_ICD_PATH = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_DEFAULT_VERSION_FILE = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"

# The temp root resolver lives in the team package beside scripts/; this
# script is run directly as well as imported, so the package root is put on
# sys.path from this file's own location before the import.
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lib.temp_root import resolve_temp_root  # noqa: E402
from pgai_agent_kanban.lint.findings import (  # noqa: E402
    display_path,
    quality_finding,
    write_findings_file,
)

_LINT_NAME = "icd_freshness"
_REGENERATE_COMMAND = "bash team/scripts/generate-icd.sh"


def _record(findings: list | None, icd_path: pathlib.Path, cause: str) -> None:
    """Append one finding about *icd_path* to *findings* (a no-op when None).

    The remedy regenerates the artifact from the repository root and
    re-runs this check against the same paths.
    """
    if findings is None:
        return
    remedy = f"cd {_DEV_TREE_ROOT} && {_REGENERATE_COMMAND} && " f"python3 {_THIS_FILE} --icd {icd_path}"
    findings.append(quality_finding(_LINT_NAME, cause, remedy, file=display_path(icd_path, _DEV_TREE_ROOT)))


def _get_temp_root() -> pathlib.Path:
    """Return this lint's scratch directory under the framework temp root.

    The root comes from :func:`pgai_agent_kanban.lib.temp_root.resolve_temp_root`
    (``PGAI_AGENT_KANBAN_TEMP_DIR``, then kanban.cfg ``[paths]``, then the
    resolver's own fallback).  Never writes directly to bare /tmp.

    Returns:
        Path to ``<temp root>/icd_freshness`` (created if absent).
    """
    root = pathlib.Path(resolve_temp_root()) / "icd_freshness"
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
    fd, tmp_str = tempfile.mkstemp(prefix="icd_fresh_", suffix=".json", dir=str(temp_root))
    os.close(fd)
    tmp_path = pathlib.Path(tmp_str)

    try:
        generate_icd(output_path=tmp_path, icd_version_file=version_file)
    except SystemExit:
        # generate_icd calls sys.exit(1) on failure; it already printed the error.
        tmp_path.unlink(missing_ok=True)
        return None
    except Exception as exc:  # noqa: BLE001 - reported as "could not be checked"
        # Anything else the generator raises (an API dependency that will not
        # import, for one) leaves freshness unchecked; the caller records it.
        print(f"ERROR: ICD regeneration failed: {exc!r}", file=sys.stderr)
        tmp_path.unlink(missing_ok=True)
        return None

    return tmp_path


def check_freshness(
    icd_path: pathlib.Path = _DEFAULT_ICD_PATH,
    version_file: pathlib.Path = _DEFAULT_VERSION_FILE,
    findings: list | None = None,
) -> bool:
    """Assert that icd_path is byte-identical to a fresh regeneration.

    Args:
        icd_path:     Path to the checked-in ICD artifact to verify.
        version_file: Path to ICD_VERSION (passed to the generator).
        findings:     When given, each problem found is appended to it as one
                      quality finding (pgai_agent_kanban.lint.findings).

    Returns:
        True when the artifact is fresh; False when stale, missing, or the
        regeneration failed.
    """
    temp_root = _get_temp_root()
    regen_path = _regenerate_to_temp(temp_root, version_file)
    if regen_path is None:
        _record(
            findings,
            icd_path,
            f"{icd_path} could not be checked: regenerating the ICD from "
            f"{version_file} failed (the error is printed above)",
        )
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
        _record(findings, icd_path, f"{icd_path} does not exist; the ICD artifact is not committed")
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
        _record(
            findings,
            icd_path,
            f"{icd_path} is stale: it differs from a fresh regeneration from the " "current codebase",
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
            "regeneration from the current codebase.  Quality class: a stale, "
            "missing or unregenerable artifact is a finding, and the lint exits "
            "0 whatever it finds (2 on a usage error)."
        ),
    )
    parser.add_argument(
        "--icd",
        metavar="PATH",
        help=("Path to the checked-in ICD artifact.  Default: docs/api/icd.json " "relative to the dev tree root."),
    )
    parser.add_argument(
        "--version-file",
        metavar="PATH",
        help=("Path to ICD_VERSION.  Default: docs/api/ICD_VERSION relative to " "the dev tree root."),
    )
    parser.add_argument(
        "--findings-file",
        metavar="PATH",
        help=(
            "Write the findings as a JSON array to PATH (parent directories "
            "created; written even when empty).  Findings are printed either way."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI invocation.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        Exit code: 0 whatever was found (each finding is printed and written
        to --findings-file); 2 usage error (argparse).
    """
    args = _parse_args(argv)
    icd_path = pathlib.Path(args.icd) if args.icd else _DEFAULT_ICD_PATH
    version_file = pathlib.Path(args.version_file) if args.version_file else _DEFAULT_VERSION_FILE

    findings: list = []
    check_freshness(icd_path=icd_path, version_file=version_file, findings=findings)
    write_findings_file(args.findings_file, findings, lint="lint_icd_freshness")
    if findings:
        # CENSUS-ANCHOR: icd-freshness-01
        # CONVERT: a stale ICD is documentation drift with zero runtime-failure
        # risk -- narrated and recorded as findings; main returns 0 below.
        emit_error_contract(
            "lint.icd-freshness",
            f"{icd_path} does not match a fresh regeneration from the current " "codebase; the cause is printed above.",
            findings[0]["remedy"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
