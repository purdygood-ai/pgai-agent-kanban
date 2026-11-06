"""
lint_findings.py
================
Run a quality-class lint's ``main()`` in process and read back the findings
it wrote with ``--findings-file``.

A quality-class lint exits 0 whatever it finds, so a test can no longer tell
"flagged" from "clean" by the return code. It reads the findings file
instead: an empty array is a clean run, and each entry is one finding in the
shape ``pgai_agent_kanban.lint.findings`` defines.

Public API:
    run_main(main, argv, findings_path) -> tuple[int, list[dict]]
"""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable, Sequence


def run_main(main: Callable[[list[str]], int], argv: Sequence[str], findings_path: Path) -> tuple[int, list[dict]]:
    """Call ``main(argv + ["--findings-file", findings_path])``.

    Returns:
        ``(return code, findings)``. The findings file must exist afterwards
        (the lints write it even when empty); a missing file fails the
        calling test with the path in the message.
    """
    rc = main([*argv, "--findings-file", str(findings_path)])
    assert findings_path.is_file(), f"the lint wrote no findings file at {findings_path}"
    return rc, json.loads(findings_path.read_text(encoding="utf-8"))
