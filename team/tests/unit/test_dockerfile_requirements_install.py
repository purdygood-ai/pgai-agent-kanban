"""
test_dockerfile_requirements_install.py
=======================================
Engine-free checks that both shipped images take their Python packages from
the repository's requirements files rather than a list of their own:

  - Each Dockerfile COPYs requirements.txt and requirements-test.txt from
    the build context (the repo root) into one directory and installs with
    ``python3 -m pip install --no-cache-dir -r requirements-test.txt`` run
    from that directory, so the nested ``-r requirements.txt`` resolves.
  - No RUN instruction names a pip package directly, and none carries an
    unquoted ``>`` — in a shell RUN line that is a redirection, so a
    version limit written that way never reaches pip.
  - requirements-test.txt includes requirements.txt and declares
    pytest-xdist at 3.8 or later.

The container tier is operator-only, so nothing here builds an image. The
Dockerfiles are parsed into logical instructions (continuation lines
joined, comments dropped) and checked as text.
"""

from __future__ import annotations

import pathlib
import re
import shlex

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
_DOCKERFILES = sorted((_REPO_ROOT / "docker").glob("*/Dockerfile"))
_REQUIREMENTS_TEST = _REPO_ROOT / "requirements-test.txt"
_INSTALL_COMMAND = "python3 -m pip install --no-cache-dir -r requirements-test.txt"


def _instructions(dockerfile: pathlib.Path) -> list[tuple[str, str]]:
    """(KEYWORD, arguments) per logical instruction, continuations joined."""
    out: list[tuple[str, str]] = []
    pending = ""
    for raw in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if pending and line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        logical = (pending + line).strip()
        pending = ""
        keyword, _, args = logical.partition(" ")
        out.append((keyword.upper(), args.strip()))
    assert not pending, f"{dockerfile} ends inside a continuation line"
    return out


def _unquoted(text: str) -> str:
    """*text* with single- and double-quoted spans removed."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", "", text)


def test_both_flavors_are_present() -> None:
    names = {p.parent.name for p in _DOCKERFILES}
    assert {"debian", "rocky9"} <= names, names


@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_dockerfile_installs_from_the_requirements_files(dockerfile: pathlib.Path) -> None:
    instructions = _instructions(dockerfile)
    workdir = None
    copied_into: dict[str, str] = {}
    install_dir = None
    for keyword, args in instructions:
        if keyword == "WORKDIR":
            workdir = args
        elif keyword == "COPY" and not args.startswith("--from"):
            parts = [p for p in shlex.split(args) if not p.startswith("--")]
            for src in parts[:-1]:
                copied_into[src] = parts[-1] if parts[-1] not in ("./", ".") else (workdir or "")
        elif keyword == "RUN" and _INSTALL_COMMAND in args:
            install_dir = workdir
    for name in ("requirements.txt", "requirements-test.txt"):
        assert name in copied_into, f"{dockerfile} does not COPY {name} from the build context"
        assert (_REPO_ROOT / name).is_file(), f"build context is missing {name}"
    assert install_dir is not None, f"{dockerfile} has no RUN '{_INSTALL_COMMAND}'"
    assert copied_into["requirements.txt"] == copied_into["requirements-test.txt"] == install_dir, (
        f"{dockerfile}: both requirements files must land in the directory the install runs from "
        f"(copied to {copied_into}, install runs in {install_dir!r})"
    )


@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_no_run_line_names_a_pip_package_or_redirects(dockerfile: pathlib.Path) -> None:
    for keyword, args in _instructions(dockerfile):
        if keyword != "RUN":
            continue
        assert ">" not in _unquoted(args), f"{dockerfile}: unquoted '>' in RUN {args!r}"
        if "pip install" in args:
            words = shlex.split(args.split("pip install", 1)[1])
            named = [w for i, w in enumerate(words) if not w.startswith("-") and (i == 0 or words[i - 1] != "-r")]
            assert not named, f"{dockerfile}: RUN pip install names packages directly: {named}"


def test_requirements_test_includes_runtime_and_xdist() -> None:
    lines = [l.split("#", 1)[0].strip() for l in _REQUIREMENTS_TEST.read_text(encoding="utf-8").splitlines()]
    lines = [l for l in lines if l]
    assert "-r requirements.txt" in lines
    assert lines.count("pytest-xdist>=3.8") == 1, lines
