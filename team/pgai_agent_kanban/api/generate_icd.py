"""
generate_icd.py — Deterministic ICD artifact generator for the operator API.

Builds the FastAPI app in-process via the existing factory (no running server,
no network calls), calls ``app.openapi()`` to obtain the OpenAPI document,
stamps ``info.version`` from ``docs/api/ICD_VERSION``, and writes deterministic
JSON to ``docs/api/icd.json``.

Also syncs the package-data copy of ICD_VERSION at
``team/pgai_agent_kanban/api/ICD_VERSION`` so it remains byte-identical to the
operator-edited ``docs/api/ICD_VERSION``.  The parity gate in the test suite
verifies this equality; invoking this generator is the one-step mechanism for
bumping the contract version across both copies at once.

Also re-freezes the current-version baseline at
``docs/api/baselines/icd-v<ICD_VERSION>.json`` so it stays byte-identical to
the freshly written ``docs/api/icd.json``.  This runs on every regeneration
that targets the canonical output path (not on regenerations that target a
custom ``--output``, e.g. the freshness gate's scratch-file comparison), so a
docstring-only or description-only ICD edit at an unchanged version can never
leave the frozen baseline stale.  When the version was bumped, this also
creates that new version's baseline file; adding the new version to
``docs/api/baselines/SUPPORTED`` remains a deliberate, separate operator
decision — freezing a baseline does not imply the compatibility gate protects
it.

Determinism is achieved by:
  - ``json.dumps`` with ``sort_keys=True`` and ``indent=2`` (fixed, reproducible layout)
  - A single trailing newline (no additional whitespace)

Two runs on the same tree produce byte-identical output.

Usage (as a module):
    python3 -m pgai_agent_kanban.api.generate_icd [--output PATH]

Usage (as a script via the bash wrapper):
    bash team/scripts/generate-icd.sh [--output PATH]

Command-line options:
    --output PATH   Write ICD JSON to PATH instead of the default
                    docs/api/icd.json (relative to the dev tree root).
    --help, -h      Show this help and exit.

Exit codes:
    0   ICD artifact written successfully; package-data copy synced.
    1   Error (ICD_VERSION file missing, output path unwritable, etc.).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# ---------------------------------------------------------------------------
# Dev-tree root resolution.
# This file lives at team/pgai_agent_kanban/api/generate_icd.py.
# Four parent levels: api/ → pgai_agent_kanban/ → team/ → project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_DEV_TREE_ROOT = _THIS_FILE.parent.parent.parent.parent
_ICD_VERSION_FILE = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"
_DEFAULT_OUTPUT = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_DEFAULT_BASELINES_DIR = _DEV_TREE_ROOT / "docs" / "api" / "baselines"

# Package-data copy — kept in byte-sync with _ICD_VERSION_FILE by generate_icd().
# This is the file that travels with the installed package on live deployments.
_PACKAGE_DATA_VERSION_FILE = _THIS_FILE.parent / "ICD_VERSION"


def read_icd_version(icd_version_file: pathlib.Path = _ICD_VERSION_FILE) -> str:
    """Return the API contract version from the ICD_VERSION file.

    Args:
        icd_version_file: Path to the ICD_VERSION file.  Defaults to
                          ``docs/api/ICD_VERSION`` relative to the dev tree root.

    Returns:
        The version string (e.g. ``"1.0.0"``), stripped of surrounding whitespace.

    Raises:
        SystemExit: When the file is absent or unreadable, with a descriptive
                    error message naming the expected file path.
    """
    try:
        content = icd_version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(
            f"ERROR: cannot read ICD_VERSION file at {icd_version_file}: {exc}\n"
            "Ensure docs/api/ICD_VERSION exists and contains the contract version "
            "(e.g. '1.0.0').  Run 'bash team/scripts/generate-icd.sh' from the "
            "repository root after creating the file.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not content:
        print(
            f"ERROR: ICD_VERSION file at {icd_version_file} is empty.\n"
            "The file must contain a non-empty version string (e.g. '1.0.0').",
            file=sys.stderr,
        )
        sys.exit(1)
    return content


def generate_icd(
    output_path: pathlib.Path = _DEFAULT_OUTPUT,
    icd_version_file: pathlib.Path = _ICD_VERSION_FILE,
    package_version_file: pathlib.Path = _PACKAGE_DATA_VERSION_FILE,
    baselines_dir: pathlib.Path = _DEFAULT_BASELINES_DIR,
) -> None:
    """Generate the deterministic ICD artifact and write it to output_path.

    Builds the FastAPI app in-process via the existing factory, calls
    ``app.openapi()`` to obtain the full OpenAPI document, overwrites
    ``info.version`` with the value from ICD_VERSION, and writes the
    document to output_path as sorted, indented JSON with a trailing newline.

    Also updates the package-data copy of ICD_VERSION so that the installed
    package and the dev-tree canonical copy remain in byte-exact sync.  The
    parity gate in the test suite asserts this equality; calling ``generate_icd``
    (or its ``generate-icd.sh`` wrapper) is the one-step mechanism for bumping
    the contract version everywhere at once.

    When ``output_path`` resolves to the canonical artifact location
    (``docs/api/icd.json`` under the dev tree root — i.e. a real regeneration,
    not a scratch-file comparison such as the freshness gate's), also
    re-freezes the current-version baseline at
    ``<baselines_dir>/icd-v<ICD_VERSION>.json`` to the same bytes.  A
    regeneration that changes ``icd.json`` without bumping ``ICD_VERSION``
    (e.g. a docstring or description sync) can therefore never leave the
    frozen current-version baseline stale — freezing is unconditional on
    every canonical regeneration.  Bumping a version and re-freezing does not
    add the new version to ``docs/api/baselines/SUPPORTED`` — that remains a
    deliberate, separate operator decision (see docs/operator-api.md "ICD —
    the published API contract").

    No running server is required; the build is entirely in-process.
    Two calls with the same tree produce byte-identical output.

    Args:
        output_path:          Destination path for the ICD JSON artifact.
                              Parent directory must exist.
        icd_version_file:     Path to the dev-tree ICD_VERSION file (the
                              operator-edited source of truth).  Defaults to
                              ``docs/api/ICD_VERSION`` relative to the dev
                              tree root.
        package_version_file: Path to the package-data ICD_VERSION copy.
                              Defaults to ``team/pgai_agent_kanban/api/ICD_VERSION``.
                              Receives the same bytes as ``icd_version_file``
                              so the installed package always reflects the
                              operator-chosen version.
        baselines_dir:        Directory holding the frozen per-version baseline
                              files.  Defaults to ``docs/api/baselines`` relative
                              to the dev tree root.  Only consulted when
                              ``output_path`` is the canonical artifact path.

    Raises:
        SystemExit: On ICD_VERSION read failure, output write failure, or
                    any other unrecoverable error.  Error messages name the
                    specific file and action needed to fix the problem.
    """
    # Read the contract version first — fail fast if the source of truth is missing.
    version = read_icd_version(icd_version_file)

    # Sync the package-data copy so it matches the dev-tree canonical file.
    # This keeps the installed package current without requiring a manual copy step.
    try:
        pkg_bytes = icd_version_file.read_bytes()
        package_version_file.write_bytes(pkg_bytes)
    except OSError as exc:
        print(
            f"ERROR: cannot sync package-data ICD_VERSION to {package_version_file}: {exc}\n"
            f"Source: {icd_version_file}\n"
            "Ensure the api/ package directory is writable.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build the app in-process. Import here (not at module level) so the module
    # can be imported without triggering the full app import chain.
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    # Use a minimal config: kanban_root defaults to current directory, which is
    # acceptable for in-process schema generation (no endpoint is invoked).
    cfg = ApiConfig()
    app = create_app(cfg=cfg)

    # Obtain the OpenAPI schema dict.  FastAPI caches it internally after the
    # first call, but we call it once so cache state does not affect determinism.
    schema: dict = app.openapi()

    # Stamp info.version from the single source of truth.
    # FastAPI may have already set it from create_app(); overwrite unconditionally
    # so the artifact always reflects ICD_VERSION even if create_app() used a
    # fallback (e.g. during tests where _ICD_VERSION_FILE may be relocated).
    schema.setdefault("info", {})["version"] = version

    # Serialize deterministically: sorted keys, fixed indent, trailing newline.
    # Two runs on the same tree produce byte-identical bytes.
    json_bytes = (json.dumps(schema, sort_keys=True, indent=2) + "\n").encode("utf-8")

    # Write atomically: write to a temp file alongside the destination, then
    # rename.  Avoids leaving a partial file if the process is interrupted.
    output_path = pathlib.Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")
    try:
        tmp_path.write_bytes(json_bytes)
        tmp_path.rename(output_path)
    except OSError as exc:
        print(
            f"ERROR: cannot write ICD artifact to {output_path}: {exc}\n"
            "Ensure the directory exists and is writable.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"ICD artifact written to {output_path}  (info.version={version!r})")
    print(f"Package-data ICD_VERSION synced to {package_version_file}")

    # Re-freeze the current-version baseline, but only for a real regeneration
    # of the canonical artifact — not for a scratch-file comparison such as the
    # freshness gate's (which passes a temp-file output_path).  Comparing
    # resolved paths means a caller invoking with a relative
    # "docs/api/icd.json" from the repo root still matches.
    if output_path.resolve() == _DEFAULT_OUTPUT:
        baselines_dir = pathlib.Path(baselines_dir)
        baselines_dir.mkdir(parents=True, exist_ok=True)
        baseline_path = baselines_dir / f"icd-v{version}.json"
        try:
            baseline_path.write_bytes(json_bytes)
        except OSError as exc:
            print(
                f"ERROR: cannot re-freeze baseline at {baseline_path}: {exc}\n"
                "The ICD artifact was written successfully, but the "
                "current-version baseline could not be refreshed. Copy "
                f"{output_path} to {baseline_path} manually before committing.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Baseline re-frozen at {baseline_path}  (matches info.version={version!r})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate_icd",
        description=(
            "Generate the deterministic OpenAPI ICD artifact for the "
            "pgai-agent-kanban operator API.  Builds the FastAPI app in-process "
            "and writes sorted, indented JSON with a trailing newline to "
            "docs/api/icd.json (or --output PATH)."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help=(
            "Write the ICD JSON to PATH.  Default: docs/api/icd.json relative "
            "to the repository root."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI invocation.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        Exit code (0 = success, 1 = failure).
    """
    args = _parse_args(argv)
    output = pathlib.Path(args.output) if args.output else _DEFAULT_OUTPUT
    generate_icd(output_path=output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
