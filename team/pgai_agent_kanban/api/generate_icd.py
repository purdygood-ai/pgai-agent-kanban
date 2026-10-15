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

Baseline freezing — a published baseline is immutable
------------------------------------------------------
On every regeneration that targets the canonical output path (not on
regenerations that target a custom ``--output``, e.g. the freshness gate's
scratch-file comparison), the generator decides what to do about the
per-version baseline at ``docs/api/baselines/icd-v<ICD_VERSION>.json``.
Freezing is NOT unconditional: only the version the current bump introduces
is ever frozen, and a baseline that has been published to clients is never
rewritten by regeneration.

  - ``icd-v<ICD_VERSION>.json`` absent, and ICD_VERSION newer (semver) than
    every existing baseline: this regeneration is the bump.  The new baseline
    is created from the freshly written ``icd.json``.  Every older baseline is
    left byte-identical.
  - ``icd-v<ICD_VERSION>.json`` absent, but ICD_VERSION is NOT newer than
    every existing baseline: no baseline is created (a warning names the
    newer baseline); ``icd.json`` is still written.
  - ``icd-v<ICD_VERSION>.json`` present and byte-identical to the new
    artifact: nothing to do.
  - ``icd-v<ICD_VERSION>.json`` present, differs, and ICD_VERSION is listed
    in ``docs/api/baselines/SUPPORTED``: the baseline is PUBLISHED.  The
    generator refuses dual-channel (error / message / remedy), leaves the
    baseline byte-untouched, and exits 1.  ``icd.json`` and the package-data
    ICD_VERSION copy have already been written by then, so the mid-release
    flow (add a route now, bump ICD_VERSION in the release's one bump step)
    still gets a fresh artifact; only the published baseline is protected.
    The remedy is the bump step: write the next version to
    ``docs/api/ICD_VERSION`` and regenerate.
  - ``icd-v<ICD_VERSION>.json`` present, differs, and ICD_VERSION is NOT in
    ``SUPPORTED``: the version was frozen by a bump but has not been published
    yet, so the in-flight baseline is refreshed to the new bytes.

``--refreeze`` is the explicit, documented override for the refusal case: it
rewrites the published ``icd-v<ICD_VERSION>.json`` to the new artifact and
says so loudly.  It never touches any other version's baseline.  The default
path never re-freezes a published baseline.

Adding a new version to ``docs/api/baselines/SUPPORTED`` remains a deliberate,
separate operator decision — freezing a baseline does not imply the
compatibility gate protects it.

Determinism is achieved by:
  - ``json.dumps`` with ``sort_keys=True`` and ``indent=2`` (fixed, reproducible layout)
  - A single trailing newline (no additional whitespace)

Two runs on the same tree produce byte-identical output.

Usage (as a module):
    python3 -m pgai_agent_kanban.api.generate_icd [--output PATH] [--refreeze]

Usage (as a script via the bash wrapper):
    bash team/scripts/generate-icd.sh [--output PATH] [--refreeze]

Command-line options:
    --output PATH   Write ICD JSON to PATH instead of the default
                    docs/api/icd.json (relative to the dev tree root).  A
                    non-canonical PATH never touches any baseline file.
    --refreeze      Rewrite the PUBLISHED baseline icd-v<ICD_VERSION>.json
                    (a version listed in SUPPORTED) to the new artifact
                    instead of refusing.  Explicit override only; the
                    default never re-freezes a published baseline.
    --help, -h      Show this help and exit.

Exit codes:
    0   ICD artifact written successfully; package-data copy synced; baseline
        policy applied (frozen, refreshed, unchanged, or skipped).
    1   Error (ICD_VERSION file missing, output path unwritable, etc.), or
        the published-baseline refusal: icd-v<ICD_VERSION>.json is listed in
        SUPPORTED and differs from the new artifact, and --refreeze was not
        given.  The refusal prints error / message / remedy on stderr.
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

# Manifest of published (client-visible) contract versions, one per line,
# alongside the frozen baseline files.
_SUPPORTED_MANIFEST_NAME = "SUPPORTED"

# Stable machine key for the published-baseline refusal (dual-channel error
# contract).  Tools grep this literal; keep it in step with the census
# Locator Index entry in docs/blocking-site-census.md.
PUBLISHED_BASELINE_ERROR_KEY = "generate_icd.published_baseline_immutable"


def _import_semver():
    """Return the framework's semver helper module.

    Version ordering always goes through ``pgai_agent_kanban.lib.semver``
    (``parse`` / ``lt`` / ``gt``), never string comparison — ``1.10.0`` sorts
    below ``1.9.0`` lexicographically and above it under semver.

    Returns:
        The ``pgai_agent_kanban.lib.semver`` module.
    """
    from pgai_agent_kanban.lib import semver

    return semver


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


def read_supported_versions(baselines_dir: pathlib.Path) -> set[str]:
    """Return the set of published contract versions listed in SUPPORTED.

    A missing manifest reads as "nothing published" — this generator never
    creates or edits SUPPORTED, and an absent file must not make it refuse.
    (The compatibility gate is the check that treats an absent or empty
    manifest as an error.)

    Args:
        baselines_dir: Directory holding SUPPORTED and the frozen baselines.

    Returns:
        Set of version strings, whitespace-stripped, blank lines dropped.
    """
    manifest = pathlib.Path(baselines_dir) / _SUPPORTED_MANIFEST_NAME
    if not manifest.is_file():
        return set()
    return {
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def existing_baseline_versions(baselines_dir: pathlib.Path) -> list[str]:
    """Return the versions of every ``icd-v<X.Y.Z>.json`` in *baselines_dir*.

    Files whose name does not carry a parseable ``vX.Y.Z`` token are ignored
    rather than fatal — a stray file in the directory is not this
    generator's concern.

    Args:
        baselines_dir: Directory holding the frozen baseline files.

    Returns:
        List of version strings (no ``v`` prefix), in directory order.
    """
    semver = _import_semver()
    versions: list[str] = []
    for path in sorted(pathlib.Path(baselines_dir).glob("icd-v*.json")):
        token = semver.from_filename(path.name)
        if not token:
            continue
        versions.append(token.lstrip("vV"))
    return versions


def _next_minor(version: str) -> str:
    """Return the next additive (minor) version after *version*.

    Used only to make the refusal's remedy runnable as printed — the
    operator may of course choose a different bump.

    Args:
        version: Current contract version, ``X.Y.Z``.

    Returns:
        ``X.(Y+1).0``.
    """
    semver = _import_semver()
    major, minor, _patch = semver.parse(version)
    return f"{major}.{minor + 1}.0"


def freeze_baseline(
    json_bytes: bytes,
    version: str,
    baselines_dir: pathlib.Path = _DEFAULT_BASELINES_DIR,
    *,
    refreeze: bool = False,
    icd_version_file: pathlib.Path = _ICD_VERSION_FILE,
) -> pathlib.Path | None:
    """Apply the baseline-freeze policy for a canonical regeneration.

    See the module docstring ("Baseline freezing") for the full decision
    table.  In short: create the baseline only when *version* is the bump
    (newer than every existing baseline); refresh an existing baseline only
    when it is not yet published (not listed in SUPPORTED); refuse to rewrite
    a published baseline unless *refreeze* is given.

    Args:
        json_bytes:       The freshly serialised ICD artifact — the bytes a
                          frozen baseline must equal.
        version:          The contract version (``ICD_VERSION`` contents).
        baselines_dir:    Directory holding SUPPORTED and the frozen baselines.
                          Created if absent.
        refreeze:         Explicit override: rewrite a published baseline that
                          differs instead of refusing.
        icd_version_file: Path to the ICD_VERSION file, named in the refusal's
                          remedy so the printed command edits the right file.

    Returns:
        The baseline path that was written (created, refreshed, or
        re-frozen), or ``None`` when nothing was written (already identical,
        or no baseline created because *version* is not the newest).

    Raises:
        SystemExit: Exit 1 on the published-baseline refusal (after printing
                    the dual-channel error contract on stderr) or on a write
                    failure.
    """
    semver = _import_semver()
    baselines_dir = pathlib.Path(baselines_dir)
    baselines_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baselines_dir / f"icd-v{version}.json"

    if not baseline_path.exists():
        # No baseline for this version yet.  It is created only when this
        # version is the bump — newer than every baseline already frozen.
        newer_existing = [
            v for v in existing_baseline_versions(baselines_dir)
            if not semver.lt(v, version)
        ]
        if newer_existing:
            print(
                f"WARNING: no baseline frozen for ICD_VERSION {version!r}: "
                f"{baselines_dir / ('icd-v' + max(newer_existing, key=semver.parse) + '.json')} "
                "already exists at a version that is not below it.  A new baseline "
                "is created only when ICD_VERSION is newer than every existing "
                "baseline; check docs/api/ICD_VERSION.",
                file=sys.stderr,
            )
            return None
        _write_baseline(baseline_path, json_bytes)
        print(f"New baseline frozen at {baseline_path}  (info.version={version!r})")
        return baseline_path

    if baseline_path.read_bytes() == json_bytes:
        print(f"Baseline at {baseline_path} already matches the artifact; left untouched.")
        return None

    if version not in read_supported_versions(baselines_dir):
        # Frozen by a bump but not yet published: still in flight, so the
        # baseline follows the artifact until the version is added to SUPPORTED.
        _write_baseline(baseline_path, json_bytes)
        print(
            f"Unpublished baseline refreshed at {baseline_path}  "
            f"(version {version!r} is not listed in SUPPORTED)"
        )
        return baseline_path

    if refreeze:
        _write_baseline(baseline_path, json_bytes)
        print(
            f"PUBLISHED baseline RE-FROZEN at {baseline_path} under --refreeze: "
            f"version {version!r} is listed in SUPPORTED and its frozen bytes have "
            "changed.  Clients pinned on this version now see a different contract.",
            file=sys.stderr,
        )
        return baseline_path

    # The version is published (listed in SUPPORTED) and the frozen bytes
    # differ from the new artifact: rewriting would mutate a contract clients
    # have already pinned on.  Refuse; the artifact itself was written above.
    from pgai_agent_kanban.lib.error_contract import emit_error_contract

    manifest = baselines_dir / _SUPPORTED_MANIFEST_NAME
    next_version = _next_minor(version)
    # BLOCK-WORTHY: overwriting a published baseline silently rewrites a
    # contract already delivered to clients — destructive, not a style note.
    emit_error_contract(
        PUBLISHED_BASELINE_ERROR_KEY,
        (
            f"refusing to overwrite the published baseline {baseline_path}: "
            f"version {version} is listed in {manifest} and the frozen file differs "
            f"from the newly generated artifact, so the ICD changed without an "
            f"ICD_VERSION bump. The baseline was left untouched; {version} stays as "
            f"published. Bump ICD_VERSION (the release's one bump step) so the change "
            f"freezes as a new version, or pass --refreeze to deliberately rewrite "
            f"the published baseline."
        ),
        (
            f"printf '{next_version}\\n' > {icd_version_file} && "
            "bash team/scripts/generate-icd.sh"
        ),
    )
    # CENSUS-ANCHOR: generate-icd-published-baseline-01
    sys.exit(1)


def _write_baseline(baseline_path: pathlib.Path, json_bytes: bytes) -> None:
    """Write *json_bytes* to *baseline_path*, exiting 1 with a named path on failure.

    Args:
        baseline_path: Destination baseline file.
        json_bytes:    Bytes to write (the ICD artifact).

    Raises:
        SystemExit: Exit 1 when the write fails; the message names the file
                    and the manual copy that recovers.
    """
    try:
        baseline_path.write_bytes(json_bytes)
    except OSError as exc:
        print(
            f"ERROR: cannot write baseline at {baseline_path}: {exc}\n"
            "The ICD artifact was written successfully, but the baseline could "
            f"not be written. Copy docs/api/icd.json to {baseline_path} manually "
            "before committing.",
            file=sys.stderr,
        )
        sys.exit(1)


def generate_icd(
    output_path: pathlib.Path = _DEFAULT_OUTPUT,
    icd_version_file: pathlib.Path = _ICD_VERSION_FILE,
    package_version_file: pathlib.Path = _PACKAGE_DATA_VERSION_FILE,
    baselines_dir: pathlib.Path = _DEFAULT_BASELINES_DIR,
    *,
    refreeze: bool = False,
    canonical_output_path: pathlib.Path = _DEFAULT_OUTPUT,
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
    (``canonical_output_path``, by default ``docs/api/icd.json`` under the dev
    tree root — i.e. a real regeneration, not a scratch-file comparison such as
    the freshness gate's), also applies the baseline-freeze policy via
    :func:`freeze_baseline`: the baseline at
    ``<baselines_dir>/icd-v<ICD_VERSION>.json`` is created only when
    ICD_VERSION is newer than every existing baseline, refreshed only while
    unpublished, and never rewritten once listed in
    ``docs/api/baselines/SUPPORTED`` unless ``refreeze`` is given — a
    regeneration that changes ``icd.json`` without bumping ``ICD_VERSION``
    refuses (exit 1, dual-channel) rather than mutating a published contract.
    Bumping a version and freezing does not add the new version to
    ``docs/api/baselines/SUPPORTED`` — that remains a deliberate, separate
    operator decision (see docs/operator-api.md "ICD — the published API
    contract").

    No running server is required; the build is entirely in-process.
    Two calls with the same tree produce byte-identical output.

    Args:
        output_path:           Destination path for the ICD JSON artifact.
                               Parent directory must exist.
        icd_version_file:      Path to the dev-tree ICD_VERSION file (the
                               operator-edited source of truth).  Defaults to
                               ``docs/api/ICD_VERSION`` relative to the dev
                               tree root.
        package_version_file:  Path to the package-data ICD_VERSION copy.
                               Defaults to ``team/pgai_agent_kanban/api/ICD_VERSION``.
                               Receives the same bytes as ``icd_version_file``
                               so the installed package always reflects the
                               operator-chosen version.
        baselines_dir:         Directory holding SUPPORTED and the frozen
                               per-version baseline files.  Defaults to
                               ``docs/api/baselines`` relative to the dev tree
                               root.  Only consulted when ``output_path`` is
                               the canonical artifact path.
        refreeze:              Explicit override: rewrite a published baseline
                               that differs instead of refusing.  Default False.
        canonical_output_path: The path that counts as "the canonical
                               artifact" for the purpose of the freeze policy.
                               Defaults to the dev tree's ``docs/api/icd.json``;
                               a caller building a scratch tree points it at
                               that tree's ``icd.json`` so the freeze path runs
                               there instead.

    Raises:
        SystemExit: On ICD_VERSION read failure, output write failure, the
                    published-baseline refusal, or any other unrecoverable
                    error.  Error messages name the specific file and action
                    needed to fix the problem.
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

    # Apply the baseline policy, but only for a real regeneration of the
    # canonical artifact — not for a scratch-file comparison such as the
    # freshness gate's (which passes a temp-file output_path).  Comparing
    # resolved paths means a caller invoking with a relative
    # "docs/api/icd.json" from the repo root still matches.
    if output_path.resolve() == pathlib.Path(canonical_output_path).resolve():
        freeze_baseline(
            json_bytes,
            version,
            baselines_dir,
            refreeze=refreeze,
            icd_version_file=icd_version_file,
        )


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
            "docs/api/icd.json (or --output PATH).  On a canonical regeneration "
            "the per-version baseline docs/api/baselines/icd-v<ICD_VERSION>.json "
            "is created only when ICD_VERSION is newer than every existing "
            "baseline; a baseline listed in SUPPORTED is never rewritten (exit 1, "
            "error/message/remedy) unless --refreeze is given."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help=(
            "Write the ICD JSON to PATH.  Default: docs/api/icd.json relative "
            "to the repository root.  A non-default PATH never touches any "
            "baseline file."
        ),
    )
    parser.add_argument(
        "--refreeze",
        action="store_true",
        help=(
            "Rewrite the PUBLISHED baseline icd-v<ICD_VERSION>.json (a version "
            "listed in docs/api/baselines/SUPPORTED) to the new artifact instead "
            "of refusing.  Explicit override only: the default never re-freezes "
            "a published baseline, and no other version's baseline is ever touched."
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
    generate_icd(output_path=output, refreeze=args.refreeze)
    return 0


if __name__ == "__main__":
    sys.exit(main())
