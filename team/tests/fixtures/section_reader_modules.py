"""
section_reader_modules.py
=========================
Copy the section reader into a sandbox install root's package directory.

A sandbox that lays out only ``pgai_agent_kanban/cm/*.py`` under a throwaway
kanban root runs the CM field readers from there, as the CM scripts do on a
live install. ``cm/read_state_field.py`` reads every section through
``pgai_agent_kanban.task_status.read_section``, so such a sandbox also needs
``task_status.py`` and the modules it imports at load or when reading
(``lib/atomic_write.py``, ``lib/error_contract.py``,
``lib/markdown_fence.py``). A sandbox that copies or links the whole package
needs none of this.

Usage:

    from tests.fixtures.section_reader_modules import copy_section_reader_modules

    copy_section_reader_modules(root / "pgai_agent_kanban")

No ``__init__.py`` is added: a sandbox package directory stays exactly as
regular or namespace as its own layout made it.
"""

from __future__ import annotations

import pathlib
import shutil

_PACKAGE_DIR = pathlib.Path(__file__).resolve().parents[2] / "pgai_agent_kanban"

# Package-relative paths of the section reader and the modules it imports.
SECTION_READER_MODULES = (
    "task_status.py",
    "lib/atomic_write.py",
    "lib/error_contract.py",
    "lib/markdown_fence.py",
)


def copy_section_reader_modules(package_dir: pathlib.Path) -> None:
    """Copy :data:`SECTION_READER_MODULES` from this tree into *package_dir*.

    Args:
        package_dir: The sandbox's ``pgai_agent_kanban`` directory; created,
            with any ``lib/`` below it, when absent.
    """
    for rel in SECTION_READER_MODULES:
        dest = package_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_PACKAGE_DIR / rel, dest)
