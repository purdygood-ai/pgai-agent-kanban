r"""
container_images.py — the declared container-image manifest and the image
selection vocabulary the container tier accepts.

Python counterpart of ``container_tier_manifest_images`` and of the name
check inside ``container_tier_select_images`` in
``team/scripts/lib/container_tier.sh``: the same manifest file, the same
parse rule and the same request vocabulary, so a surface that checks an
image selection before handing it to a container-tier harness (the REST
deep-verify and cold-build-now routes) accepts and refuses exactly the
names the harness itself would.

"Exactly" includes the split of one ``--image`` entry into names, which
follows bash's ``IFS=$', \t' read -r -a`` field splitting rather than a
plain regex split: an empty slot between two commas, or before a leading
comma, is an empty name the harness refuses; a single trailing comma adds
nothing; a whitespace-only entry names nothing at all (the harness then
lists the images and builds nothing when no other entry names one); and
only the entry's first line is read. An entry that is the empty string is
one empty name, refused, as the harness's own ``-z`` check keeps it.

    declared = declared_container_images(manifest_path(root))
    unknown = undeclared_image_names(["debian", "all"], declared)

The manifest lives at ``config/container-images.txt`` relative to the
directory that also holds ``scripts/`` -- ``team/`` in a dev tree, the
kanban root in an installed copy -- which is the same file
``container_tier.sh`` resolves from its own location
(``scripts/lib/../../config``).
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterable, Sequence

__all__ = [
    "MANIFEST_RELATIVE_PATH",
    "SELECT_ALL",
    "declared_container_images",
    "manifest_path",
    "split_image_entry",
    "split_image_request",
    "undeclared_image_names",
]

#: The manifest's path relative to the directory holding ``scripts/``.
MANIFEST_RELATIVE_PATH = pathlib.PurePosixPath("config") / "container-images.txt"

#: The request word that selects every declared image.
SELECT_ALL = "all"

# The IFS container_tier.sh splits one request entry with (IFS=$', \t'):
# space and tab are IFS whitespace, the comma is the one non-whitespace
# separator. A field delimiter is either a comma together with any IFS
# whitespace around it, or a run of IFS whitespace alone.
_IFS_WHITESPACE = " \t"
_FIELD_DELIMITER = re.compile(r"[ \t]*,[ \t]*|[ \t]+")


def manifest_path(root: "pathlib.Path | str") -> pathlib.Path:
    """Return the manifest path under *root*, the directory holding ``scripts/``."""
    return pathlib.Path(root) / MANIFEST_RELATIVE_PATH


def declared_container_images(manifest: "pathlib.Path | str") -> list[str]:
    """Return the image names *manifest* declares, in file order.

    Surrounding whitespace is stripped; blank lines and lines starting with
    ``#`` are skipped; a name listed twice is returned once. A manifest that
    does not exist declares nothing and yields ``[]`` -- the harness then
    refuses every requested name, naming "(none declared)", and so does a
    caller of :func:`undeclared_image_names`.
    """
    path = pathlib.Path(manifest)
    if not path.is_file():
        return []
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        if name not in names:
            names.append(name)
    return names


def split_image_entry(entry: str) -> list[str]:
    r"""Return the names one ``--image`` value holds, split as the harness does.

    Mirrors ``container_tier_select_images``: the empty string is kept as one
    empty name (refused downstream as undeclared); any other entry is split
    the way ``IFS=$', \t' read -r -a parts <<< "$entry"`` splits it --

    * only the text before the first newline is read;
    * leading and trailing spaces and tabs are trimmed;
    * each comma ends a field, so ``"a,,b"`` is ``["a", "", "b"]`` and
      ``",a"`` is ``["", "a"]``;
    * spaces and tabs around a comma belong to that one delimiter, and a
      run of them alone also separates fields (``"a , b"`` and ``"a\tb"``
      are both ``["a", "b"]``);
    * a delimiter at the very end opens no extra field, so ``"a,"`` is
      ``["a"]`` while ``"a,,"`` is ``["a", ""]``;
    * an entry of only spaces and tabs is ``[]`` -- it names nothing.
    """
    if entry == "":
        return [""]
    line = entry.split("\n", 1)[0].strip(_IFS_WHITESPACE)
    if not line:
        return []
    fields = _FIELD_DELIMITER.split(line)
    # Leading/trailing whitespace is already trimmed, so an empty last field
    # can only follow a trailing comma, which read -a does not turn into a
    # field of its own.
    if fields and fields[-1] == "":
        fields.pop()
    return fields


def split_image_request(entries: Iterable[str]) -> list[str]:
    """Return the image names a list of request entries asks for, in order.

    Each entry is one ``--image`` value: a single name, a comma- or
    space-separated list of names, or ``all``; :func:`split_image_entry`
    splits it exactly as the harness does. An empty slot (``"a,,b"``,
    ``",a"``) and an empty entry (``""``) are kept as ``""`` so they are
    refused as undeclared names, as the harness refuses them; an entry of
    only spaces and tabs contributes no name, as in the harness.
    """
    names: list[str] = []
    for entry in entries:
        names.extend(split_image_entry(entry))
    return names


def undeclared_image_names(
    entries: Iterable[str], declared: Sequence[str]
) -> list[str]:
    """Return the requested names that are neither ``all`` nor in *declared*.

    *entries* are request entries as :func:`split_image_request` reads them.
    The result keeps request order; an empty list means every requested
    name is acceptable.
    """
    return [
        name
        for name in split_image_request(entries)
        if name != SELECT_ALL and name not in declared
    ]
