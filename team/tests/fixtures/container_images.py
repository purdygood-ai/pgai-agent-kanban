"""
container_images.py
===================
Reader for team/config/container-images.txt, the declared list of supported
container image flavors.

Every container-tier test that needs "the images this project ships" reads
that list here instead of scanning docker/ for Dockerfiles, so a directory
under docker/ that the manifest does not name is never built.

The container-tier harnesses (team/scripts/lib/container_tier.sh) export
the operator's image selection as PGAI_CONTAINER_IMAGES; the tests that
build images parametrize over load_selected_container_images so a run
builds only what the operator chose.
"""

from __future__ import annotations

import os
import pathlib
import re

MANIFEST_RELATIVE_PATH = pathlib.Path("team") / "config" / "container-images.txt"
SELECTION_ENV_VAR = "PGAI_CONTAINER_IMAGES"


def load_container_images(repo_root: pathlib.Path) -> list[str]:
    """Return the flavor names declared in the manifest under *repo_root*, in file order.

    Blank lines and ``#`` comment lines are skipped and surrounding
    whitespace is stripped. A name listed twice is returned once.

    Raises FileNotFoundError naming the manifest path when it is absent,
    and ValueError naming the file and line when an entry is not a plain
    directory name (it contains a path separator or a glob character).
    """
    manifest = repo_root / MANIFEST_RELATIVE_PATH
    if not manifest.is_file():
        raise FileNotFoundError(
            f"container image manifest not found at {manifest}; it declares the "
            f"image flavors the container tier builds (one docker/<flavor> name "
            f"per line)"
        )
    names: list[str] = []
    for line_no, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        if any(ch in name for ch in "/\\*?[]") or name in {".", ".."}:
            raise ValueError(
                f"{manifest}:{line_no}: entry {name!r} is not a plain flavor name; "
                f"write the docker/<flavor> directory name only, e.g. 'debian'"
            )
        if name not in names:
            names.append(name)
    return names


def load_selected_container_images(repo_root: pathlib.Path) -> list[str]:
    """Return the manifest's flavors narrowed to the operator's selection.

    The selection is ``PGAI_CONTAINER_IMAGES`` (names separated by spaces
    or commas, or ``all``), exported by the container-tier harnesses after
    they resolve ``--image`` / ``kanban.cfg [release] container_images``.
    Unset or blank means every manifest flavor (a direct pytest run of the
    cold lane). Manifest order is kept. Raises ValueError naming the
    manifest's list when the selection names a flavor it does not declare.
    """
    declared = load_container_images(repo_root)
    raw = os.environ.get(SELECTION_ENV_VAR, "").strip()
    if not raw:
        return declared
    requested = [name for name in re.split(r"[\s,]+", raw) if name]
    if "all" in requested:
        return declared
    unknown = [name for name in requested if name not in declared]
    if unknown:
        raise ValueError(
            f"{SELECTION_ENV_VAR}={raw!r} names {unknown}, which "
            f"{repo_root / MANIFEST_RELATIVE_PATH} does not declare; declared: {declared}"
        )
    return [name for name in declared if name in requested]
