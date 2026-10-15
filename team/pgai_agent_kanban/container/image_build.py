"""
image_build.py — the image build command docker/build.sh issues.

``docker/build.sh <flavor>`` builds one of the repository's
``docker/<flavor>/Dockerfile`` images. This module holds everything that
script decides, so the script itself only parses flags, gates and runs:

* :func:`dockerfile_flavors` — the flavors that can be built: every
  ``docker/<flavor>/`` directory that holds a ``Dockerfile`` (today
  ``claude-abliterated``, ``debian``, ``rocky9``), sorted by name. The list is
  read from the tree, never from ``team/config/container-images.txt``: that
  manifest is the container tier's own build list, and a flavor it leaves out
  is still buildable here.
* :func:`build_arguments` — the engine argv after the engine name:
  ``build [--no-cache] -f <repo>/docker/<flavor>/Dockerfile
  --build-arg CLAUDE_CODE_VERSION=... --build-arg CODEX_VERSION=...
  -t pgai-kanban:<flavor> -t pgai-kanban:<tag> <repo>``. The build context
  is always the repository root, as an absolute path, so the command is the
  same whatever directory the build is started from.

The pinned CLI versions come from ``docker/cli-versions.env`` of the
repository being built, through :mod:`pgai_agent_kanban.container.cli_pins`,
the one reader of that file's format. The module depends only on its own
package and the standard library, so an installed copy answers for any
checkout named with ``--repo-root``; with no ``--repo-root`` it uses the
tree it sits in, which is a repository only when run from a checkout (the
only place docker/build.sh runs -- it needs the repository as its build
context).

Command line (docker/build.sh calls it through pp_run_ops; it also runs by
file path, ``python3 team/pgai_agent_kanban/container/image_build.py ...``)::

    python3 -m pgai_agent_kanban.container.image_build flavors [--repo-root DIR]
    python3 -m pgai_agent_kanban.container.image_build build-args FLAVOR
        [--repo-root DIR] [--tag TAG] [--no-cache]

``flavors`` prints one flavor per line. ``build-args`` prints the build argv,
one argument per line. Exit codes: 0 success; 1 the pinned versions cannot be
read (missing file or key; the reason is on stderr); 2 usage error; 3 the
flavor is not one of the buildable flavors (the list is on stderr).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Optional, Sequence

# Run by file path or loaded from a file outside its package, this module
# has no parent package and the interpreter puts only its own directory on
# sys.path, so the package import below would not resolve. Put this file's
# own package root -- the directory holding the pgai_agent_kanban/ this file
# sits in -- at the front of sys.path so it resolves from the same tree,
# never another. Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.container.cli_pins import cli_pins_build_args  # noqa: E402

# Repository root: four levels up from this file
# (container/ -> pgai_agent_kanban/ -> team/ -> repository root).
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# The image repository every build is tagged into, and the second tag a
# build carries unless --tag replaces it.
IMAGE_REPOSITORY = "pgai-kanban"
DEFAULT_EXTRA_TAG = "latest"

# A container image tag: letters, digits, '_', '.', '-', starting with a
# letter, digit or '_', at most 128 characters (the engines' own rule).
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")

EXIT_OK = 0
EXIT_PINS_UNREADABLE = 1
EXIT_USAGE = 2
EXIT_UNKNOWN_FLAVOR = 3


class InvalidTagError(ValueError):
    """The --tag value is not a well-formed image tag."""


class UnknownFlavorError(ValueError):
    """The requested flavor has no ``docker/<flavor>/Dockerfile``.

    ``flavors`` carries the buildable flavors so a caller can list them.
    """

    def __init__(self, flavor: str, flavors: Sequence[str]):
        self.flavor = flavor
        self.flavors = list(flavors)
        listed = ", ".join(self.flavors) if self.flavors else "(none found)"
        super().__init__(f"unknown flavor {flavor!r}; buildable flavors: {listed}")


def docker_dir(repo_root: Optional[pathlib.Path | str] = None) -> pathlib.Path:
    """Return the repository's ``docker/`` directory."""
    return pathlib.Path(repo_root if repo_root is not None else REPO_ROOT).resolve() / "docker"


def dockerfile_flavors(repo_root: Optional[pathlib.Path | str] = None) -> list[str]:
    """Return every buildable flavor, sorted by name.

    A flavor is a direct subdirectory of ``<repo_root>/docker/`` that holds a
    regular file named ``Dockerfile``. A missing ``docker/`` directory yields
    an empty list.
    """
    base = docker_dir(repo_root)
    if not base.is_dir():
        return []
    return sorted(
        entry.name
        for entry in base.iterdir()
        if entry.is_dir() and (entry / "Dockerfile").is_file()
    )


def image_tags(flavor: str, tag: str = DEFAULT_EXTRA_TAG) -> list[str]:
    """Return the two image references a build of *flavor* is tagged with."""
    return [f"{IMAGE_REPOSITORY}:{flavor}", f"{IMAGE_REPOSITORY}:{tag}"]


def valid_tag(tag: str) -> bool:
    """True when *tag* is a well-formed image tag (no repository part)."""
    return bool(_TAG_RE.match(tag))


def pinned_build_args(repo_root: Optional[pathlib.Path | str] = None) -> list[str]:
    """Return ``--build-arg KEY=value`` pairs from ``docker/cli-versions.env``.

    Raises FileNotFoundError (file absent) or ValueError (a required key
    missing), each naming the path or key, from the shared pin reader.
    """
    return cli_pins_build_args(docker_dir(repo_root) / "cli-versions.env")


def build_arguments(
    flavor: str,
    *,
    repo_root: Optional[pathlib.Path | str] = None,
    tag: str = DEFAULT_EXTRA_TAG,
    no_cache: bool = False,
) -> list[str]:
    """Return the engine argv (after the engine name) that builds *flavor*.

    Raises UnknownFlavorError when *flavor* has no Dockerfile, InvalidTagError
    for a malformed *tag*, and the pin reader's FileNotFoundError / ValueError
    when ``docker/cli-versions.env`` cannot supply both pinned versions.
    """
    root = pathlib.Path(repo_root if repo_root is not None else REPO_ROOT).resolve()
    flavors = dockerfile_flavors(root)
    if flavor not in flavors:
        raise UnknownFlavorError(flavor, flavors)
    if not valid_tag(tag):
        raise InvalidTagError(
            f"--tag {tag!r} is not a valid image tag: use letters, digits, '_', '.' and '-' "
            "(starting with a letter, digit or '_'), at most 128 characters, e.g. --tag dev"
        )
    argv = ["build"]
    if no_cache:
        argv.append("--no-cache")
    argv += ["-f", str(docker_dir(root) / flavor / "Dockerfile")]
    argv += pinned_build_args(root)
    for ref in image_tags(flavor, tag):
        argv += ["-t", ref]
    argv.append(str(root))
    return argv


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.container.image_build",
        description=(
            "Answer docker/build.sh: which flavors can be built, and the engine argv that builds one. "
            "Exit 0 ok, 1 pinned versions unreadable, 2 usage error, 3 unknown flavor."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="VERB")

    p = sub.add_parser(
        "flavors", help="list the buildable flavors",
        description="Print every docker/<flavor>/ directory that holds a Dockerfile, one per line, sorted.",
    )
    p.add_argument("--repo-root", metavar="DIR", default=None, help="repository root (default: the tree this module is in)")

    p = sub.add_parser(
        "build-args", help="print the build argv for one flavor",
        description=(
            "Print the engine argv after the engine name, one argument per line: the Dockerfile, "
            "the pinned --build-arg pairs from docker/cli-versions.env, both tags and the repository root as context."
        ),
    )
    p.add_argument("flavor", help="a docker/<flavor>/ directory name, e.g. rocky9")
    p.add_argument("--repo-root", metavar="DIR", default=None, help="repository root (default: the tree this module is in)")
    p.add_argument("--tag", default=DEFAULT_EXTRA_TAG, help=f"second tag, replacing '{DEFAULT_EXTRA_TAG}' (default: {DEFAULT_EXTRA_TAG})")
    p.add_argument("--no-cache", action="store_true", help="add --no-cache to the build")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns the exit code (see the module docstring)."""
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command == "flavors":
        for name in dockerfile_flavors(args.repo_root):
            print(name)
        return EXIT_OK

    if args.command == "build-args":
        try:
            built = build_arguments(
                args.flavor, repo_root=args.repo_root, tag=args.tag, no_cache=args.no_cache
            )
        except UnknownFlavorError as exc:
            print(f"image_build: {exc}", file=sys.stderr)
            return EXIT_UNKNOWN_FLAVOR
        except InvalidTagError as exc:
            print(f"image_build: {exc}", file=sys.stderr)
            return EXIT_USAGE
        except (FileNotFoundError, ValueError) as exc:
            # The pin reader names the missing file or the missing key.
            print(f"image_build: cannot read the pinned CLI versions: {exc}", file=sys.stderr)
            return EXIT_PINS_UNREADABLE
        for item in built:
            print(item)
        return EXIT_OK

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
