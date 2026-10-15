#!/usr/bin/env python3
"""
lint_dockerfile_static.py
==========================
Engine-free static Dockerfile lint for the fast per-RC suite -- the
fast-suite counterpart to the cold-build failure the DEEP-VERIFY lane
recorded when a malformed image definition reached it undetected.
Reads every ``Dockerfile`` under ``--docker-dir`` as plain text, with no
container engine on PATH and no image pulled, and refuses the two shapes
that previously broke the DEEP-VERIFY cold-build lane:

  1. A malformed ``FROM`` instruction -- wrong argument arity, including the
     duplicated-keyword form (``FROM FROM <image>``) that produced
     "FROM requires either one or three arguments" from both docker/buildkit
     and podman/buildah.
  2. A ``COPY``/``ADD`` ``--chmod`` flag carrying anything other than an
     octal mode -- docker/buildkit accepts the symbolic form
     (``--chmod=+x``) but podman/buildah rejects it, so a Dockerfile using
     it builds cleanly on one engine and fails cold on the other.

WHY THIS IS A FATAL (BLOCK-WORTHY) CHECK, NOT A QUALITY-CLASS LINT
--------------------------------------------------------------------
``team/SOP-core.md``'s "Gates and Refusals" -- "Unusable build blocks; an
unavailable build environment does not" doctrine draws the line: a tree whose own artefact fails to build
is an unusable build, and an unusable build is the one case that doctrine
reserves as BLOCK-WORTHY for a build-verification lane. A Dockerfile
carrying either shape above cannot be reduced to an image by any engine
this project ships -- docker/buildkit rejects the duplicated ``FROM``
outright, and podman/buildah rejects both shapes -- so there is no
successful build hiding behind the malformed text: this is doctrine's
verdict 2 ("the artefact failed to build"), never verdict 3 ("the build
could not be attempted"). Because that verdict is knowable from the text of
the Dockerfile alone, this check does not need to invoke an engine to earn
its BLOCK-WORTHY class -- it is a cheap, exact, engine-free proxy for a
failure class DEEP-VERIFY's expensive cold-build lane would otherwise be
the only place to catch. See ``run-unit-tests.sh``'s call site for the
fatal wiring, the classification comment, the dual-channel error contract,
and the census anchor this module's docstring is required to accompany.

WHAT IS CHECKED
----------------
Every file literally named ``Dockerfile`` found anywhere under
``--docker-dir`` (default: ``docker/`` resolved from this script's own
location) is scanned as plain text -- no docker/podman binary is invoked,
probed, or required on PATH. Two independent checks run per file:

  CHECK 1 -- FROM ARITY.  Every non-comment line whose first
  whitespace-separated token is ``FROM`` (case-insensitive, matching
  Dockerfile's own instruction-name case-insensitivity) is tokenized on
  whitespace. The tokens after the keyword must form one of Dockerfile's
  two valid ``FROM`` shapes: exactly one token (``FROM <image>``), or
  exactly three tokens whose second token is ``AS``/``as``
  (``FROM <image> AS <name>``). Any other token count -- including the
  duplicated-keyword form ``FROM FROM <image>``, which leaves a two-token
  remainder whose second token is not ``AS`` -- is a violation.
  Multi-stage Dockerfiles with more than one well-formed ``FROM`` line
  (e.g. ``docker/debian/Dockerfile``'s ``FROM node:20-bookworm AS nodejs``
  followed by ``FROM python:3.12-bookworm``) are unaffected: each ``FROM``
  line is validated independently.

  CHECK 2 -- CHMOD OCTAL MODE.  Every non-comment line whose first token is
  ``COPY`` or ``ADD`` (case-insensitive) is scanned for a
  ``--chmod=<value>`` flag token. When present, ``<value>`` must match an
  octal file-mode pattern (1-4 digits, each ``0``-``7``) -- ``755``,
  ``0755``, ``777`` are valid; ``+x``, ``u+x``, ``755x`` are not. A
  ``COPY``/``ADD`` line's unrelated flags (e.g. ``--from=<stage>``,
  ``--chown=``) are ignored; only the ``--chmod=`` flag's value is
  validated.

SCOPE (deliberately narrow)
-----------------------------
This is not a general-purpose Dockerfile linter. It checks exactly the two
shapes described above and nothing else -- no style opinions, no
best-practice nudges, no dependency on an external Dockerfile-parsing
library. Growing this into a hadolint-equivalent style checker would trade
a small, auditable regex scan for a dependency this codebase does not
otherwise carry, so it stays out of scope here.

EXIT CODES
----------
  0   No violations in either check.
  1   One or more violations found; see stdout for the file, line, and a
      runnable remedy.
  2   Usage error -- ``--docker-dir`` does not exist.

USAGE
-----
  python3 team/scripts/lint_dockerfile_static.py [--docker-dir PATH] [--verbose]

  --docker-dir PATH   Root directory to scan (recursively) for files named
                       "Dockerfile" (default: docker/ resolved from this
                       script's own location, i.e. team/scripts/ -> repo
                       root -> docker/). Tests point this at a fixture root
                       under team/tests/fixtures/dockerfile_static/.
  --verbose, -v        Print each Dockerfile as it is scanned.

EXAMPLES
--------
  # From the repository root, against the real docker/ tree:
  python3 team/scripts/lint_dockerfile_static.py

  # Against a fixture tree (e.g. from a unit test):
  python3 team/scripts/lint_dockerfile_static.py --docker-dir /path/to/fixture
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

_FROM_KEYWORD_RE = re.compile(r"^\s*FROM\b", re.IGNORECASE)
_COPY_ADD_KEYWORD_RE = re.compile(r"^\s*(COPY|ADD)\b", re.IGNORECASE)
_CHMOD_FLAG_RE = re.compile(r"^--chmod=(.*)$", re.IGNORECASE)
_OCTAL_MODE_RE = re.compile(r"^[0-7]{1,4}$")


@dataclass
class _Finding:
    """One static-check violation: a location, a concrete cause, and a
    ready-to-run remedy line specific to that finding."""

    kind: str  # "from-arity" | "chmod-mode"
    path: Path
    line: int
    cause: str
    remedy: str


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def find_dockerfiles(docker_dir: Path) -> list[Path]:
    """Return every file literally named ``Dockerfile`` under *docker_dir*,
    recursive, sorted for deterministic output."""
    return sorted(p for p in docker_dir.rglob("Dockerfile") if p.is_file())


# ---------------------------------------------------------------------------
# CHECK 1: FROM arity
# ---------------------------------------------------------------------------


def check_from_arity(path: Path, lines: list[str]) -> list[_Finding]:
    """Return one finding per malformed ``FROM`` instruction in *lines*."""
    findings: list[_Finding] = []
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _FROM_KEYWORD_RE.match(stripped):
            continue

        tokens = stripped.split()
        args = tokens[1:]

        valid = len(args) == 1 or (len(args) == 3 and args[1].upper() == "AS")
        if valid:
            continue

        line_no = i + 1
        findings.append(
            _Finding(
                kind="from-arity",
                path=path,
                line=line_no,
                cause=(
                    f"{path}:{line_no} -- malformed FROM instruction "
                    f"(found {len(args)} argument(s) after the keyword: "
                    f"{' '.join(args) or '(none)'}); Dockerfile's FROM "
                    "requires either one argument (FROM <image>) or three "
                    "(FROM <image> AS <name>)"
                ),
                remedy=(
                    f"Edit {path}:{line_no} -- replace it with a single "
                    "well-formed instruction, e.g. "
                    f"'FROM {args[-1] if args else '<image>'}' or "
                    f"'FROM {args[-1] if args else '<image>'} AS <stage-name>'"
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# CHECK 2: chmod octal mode
# ---------------------------------------------------------------------------


def check_chmod_octal(path: Path, lines: list[str]) -> list[_Finding]:
    """Return one finding per COPY/ADD line whose --chmod value is not a
    valid octal mode."""
    findings: list[_Finding] = []
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _COPY_ADD_KEYWORD_RE.match(stripped):
            continue

        tokens = stripped.split()
        instruction = tokens[0].upper()
        for token in tokens[1:]:
            chmod_match = _CHMOD_FLAG_RE.match(token)
            if chmod_match is None:
                continue
            value = chmod_match.group(1)
            if _OCTAL_MODE_RE.match(value):
                continue

            line_no = i + 1
            findings.append(
                _Finding(
                    kind="chmod-mode",
                    path=path,
                    line=line_no,
                    cause=(
                        f"{path}:{line_no} -- {instruction} --chmod={value} "
                        "is not an octal mode; podman/buildah rejects the "
                        "symbolic chmod form docker/buildkit accepts, so "
                        "this Dockerfile builds on one engine and fails "
                        "cold on the other"
                    ),
                    remedy=(
                        f"Edit {path}:{line_no} -- replace '--chmod={value}' "
                        "with an octal mode, e.g. '--chmod=755'"
                    ),
                )
            )
            # Only one --chmod flag is meaningful per instruction; stop
            # scanning this line's remaining tokens once it is found.
            break
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_findings(findings: list[_Finding], lint_script: Path) -> None:
    print(
        f"\nlint_dockerfile_static: FAIL -- {len(findings)} violation(s) "
        "found -- see docker/ (or the scanned directory) for the offending "
        "file(s).\n",
        flush=True,
    )
    print("Cause(s):", flush=True)
    for finding in findings:
        print(f"  - {finding.cause}", flush=True)
    print("", flush=True)
    print("To fix:", flush=True)
    for i, finding in enumerate(findings, start=1):
        print(f"  {i}. {finding.remedy}", flush=True)
    print(
        f"  {len(findings) + 1}. python3 \"{lint_script}\"   # re-run this "
        "lint to confirm",
        flush=True,
    )
    print("", flush=True)
    print(
        "Rule: every Dockerfile under the scanned directory must use a "
        "well-formed FROM instruction (one or three arguments) and, for "
        "COPY/ADD, an octal --chmod mode -- these are the two shapes that "
        "previously broke the DEEP-VERIFY cold-build lane undetected.",
        flush=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_dockerfile_static.py",
        description=(
            "Engine-free static lint over every 'Dockerfile' under "
            "--docker-dir: refuses a malformed FROM instruction (wrong "
            "argument arity, including the duplicated-keyword form) and a "
            "COPY/ADD --chmod flag that is not an octal mode. Requires no "
            "docker/podman binary on PATH."
        ),
    )
    parser.add_argument(
        "--docker-dir",
        metavar="PATH",
        help=(
            "Root directory to scan recursively for files named "
            "'Dockerfile' (default: docker/ resolved from this script's "
            "own location)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each Dockerfile as it is scanned.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 (clean), 1 (violation(s) found), or 2 (usage)."""
    args = _parse_args(argv)

    if args.docker_dir:
        docker_dir = Path(args.docker_dir).resolve()
    else:
        script_dir = Path(__file__).resolve().parent  # team/scripts/
        repo_root = script_dir.parent.parent           # repo root
        docker_dir = repo_root / "docker"

    if not docker_dir.is_dir():
        print(
            f"ERROR: docker directory not found: {docker_dir}\n"
            "Use --docker-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    dockerfiles = find_dockerfiles(docker_dir)

    print(
        f"lint_dockerfile_static: scanning {len(dockerfiles)} Dockerfile(s) "
        f"under {docker_dir}",
        flush=True,
    )

    findings: list[_Finding] = []
    for dockerfile in dockerfiles:
        if args.verbose:
            print(f"  scanning: {dockerfile}", flush=True)
        lines = dockerfile.read_text(encoding="utf-8", errors="replace").splitlines()
        findings.extend(check_from_arity(dockerfile, lines))
        findings.extend(check_chmod_octal(dockerfile, lines))

    findings.sort(key=lambda f: (str(f.path), f.line))

    if findings:
        _print_findings(findings, Path(__file__).resolve())
        return 1

    print(
        f"lint_dockerfile_static: OK -- {len(dockerfiles)} Dockerfile(s) "
        "checked, 0 violations.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
