"""
test_dashboard_sys_path_live_only.py
=====================================
Acceptance tests for an earlier defect: dashboard python sys.path must use only the
live-install anchor, never the dev-tree path.

Three areas covered:

1. **Behavior-gate scan** — a grep-zero invariant: no file under team/scripts/
   may construct a live-runtime sys.path entry from PGAI_DEV_TREE_PATH.  A
   negative-proof test confirms the pattern catches a synthetic violation.

2. **Live-root import fixture (a)** — with PGAI_DEV_TREE_PATH set to a
   directory that lacks dashboard/status_priority_cap, importing via only
   PGAI_AGENT_KANBAN_ROOT_PATH in sys.path succeeds AND the module's __file__
   resolves under the kanban root, not the dev tree.

3. **Deployment-gap visibility fixture (b)** — with the live module renamed
   aside, an attempt to import it raises ModuleNotFoundError.  The error message
   names the import path (pgai_agent_kanban.dashboard.status_priority_cap) so
   the live deployment root is unambiguously blamed, not a silent dev-tree
   rescue.

TEST NAMING
-----------
All names describe behavior, not bug IDs (SOP.md Anti-pattern 6).
"""

from __future__ import annotations

import importlib
import pathlib
import re
import subprocess
import sys
import types


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _repo_root() -> pathlib.Path:
    """Return the repository root via git, regardless of working directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return pathlib.Path(result.stdout.strip())


# Pattern that catches sys.path construction expressions that include the
# PGAI_DEV_TREE_PATH environment variable.  The pattern matches:
#   - sys.path.insert / sys.path.append lines referencing _dev_tree or PGAI_DEV_TREE_PATH
#   - os.path.join(_dev_tree, ...) appearing inside a sys.path operation
#   - Any Python list literal in a sys.path block that names _dev_tree
#
# We use two separate sub-patterns and OR them so the single function captures
# both the direct variable reference and the indirect join form.
_SYS_PATH_DEV_TREE_PATTERN = re.compile(
    r"sys\.path\s*\.\s*(?:insert|append)\s*\(.*(?:_dev_tree|PGAI_DEV_TREE_PATH)"
    r"|"
    r"for\s+_candidate\s+in\s*\[.*(?:_dev_tree|PGAI_DEV_TREE_PATH)"
)


def _find_sys_path_dev_tree_violations() -> list[str]:
    """Return '<rel_path>:<lineno>:<content>' for each violation in team/scripts/.

    Scans all .sh files under team/scripts/, reads their content line by line,
    and reports any line matching the live-runtime sys.path + dev-tree pattern.
    Returns an empty list when the invariant holds.
    """
    root = _repo_root()
    scripts_dir = root / "team" / "scripts"

    if not scripts_dir.is_dir():
        raise FileNotFoundError(
            f"team/scripts/ not found at {scripts_dir}; "
            "repo root resolution may be wrong."
        )

    violations: list[str] = []

    for sh_file in sorted(scripts_dir.rglob("*.sh")):
        try:
            rel_path = sh_file.relative_to(root)
        except ValueError:
            rel_path = sh_file

        rel_str = str(rel_path).replace("\\", "/")

        try:
            lines = sh_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, start=1):
            if _SYS_PATH_DEV_TREE_PATTERN.search(line):
                violations.append(f"{rel_str}:{lineno}:{line.rstrip()}")

    return violations


# ---------------------------------------------------------------------------
# 1. Behavior-gate: no live-runtime sys.path from PGAI_DEV_TREE_PATH
# ---------------------------------------------------------------------------


def test_no_dev_tree_path_in_live_runtime_sys_path() -> None:
    """No shell script in team/scripts/ constructs a sys.path entry from the dev tree.

    The live dashboard runtime must use only $PGAI_AGENT_KANBAN_ROOT_PATH as the
    single sys.path candidate.  Any file that builds a sys.path entry from
    PGAI_DEV_TREE_PATH causes the live install to silently shadow its own package
    with dev-tree code — exactly the defect fixed in an earlier defect.

    This gate is the mechanically-verifiable completeness signal: if a future edit
    re-introduces a dev-tree sys.path entry, this test fails before the change
    reaches the RC.
    """
    violations = _find_sys_path_dev_tree_violations()
    assert not violations, (
        f"Live-runtime sys.path dev-tree invariant violated — "
        f"{len(violations)} violation(s) found in team/scripts/:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nDashboard python heredocs must use only $PGAI_AGENT_KANBAN_ROOT_PATH "
        "in sys.path.  The dev tree is DATA to the live runtime, never CODE.  "
        "Remove any PGAI_DEV_TREE_PATH / _dev_tree reference from sys.path "
        "construction blocks."
    )


def test_sys_path_dev_tree_pattern_detects_synthetic_violation(
    tmp_path: pathlib.Path,
) -> None:
    """The scan pattern matches a known-bad sys.path + dev-tree construction.

    Negative proof: confirms the scan can catch a real violation rather than
    always returning clean (a silent pass-through bug in the scanner).
    """
    bad_script = tmp_path / "bad_dashboard.sh"
    bad_script.write_text(
        "#!/usr/bin/env bash\n"
        "# Synthetic bad script — contains the prohibited pattern.\n"
        "python3 <<'PYEOF'\n"
        "import os, sys\n"
        "_kanban_root = os.environ.get('PGAI_AGENT_KANBAN_ROOT_PATH', '')\n"
        "_dev_tree    = os.environ.get('PGAI_DEV_TREE_PATH', '')\n"
        "for _candidate in [_kanban_root, _dev_tree]:\n"
        "    if _candidate and _candidate not in sys.path:\n"
        "        sys.path.insert(0, _candidate)\n"
        "PYEOF\n",
        encoding="utf-8",
    )

    # anti-pattern-allowlist: 1 (justification: test reads synthetic temp file it created, not a real scan)
    matching_lines = [
        line
        for line in bad_script.read_text(encoding="utf-8").splitlines()
        if _SYS_PATH_DEV_TREE_PATTERN.search(line)
    ]

    assert len(matching_lines) >= 1, (
        f"Expected at least 1 matching line in synthetic bad script; "
        f"got {len(matching_lines)}: {matching_lines!r}.  "
        "Check _SYS_PATH_DEV_TREE_PATTERN."
    )


def test_sys_path_dev_tree_pattern_does_not_match_live_only_construction(
    tmp_path: pathlib.Path,
) -> None:
    """The scan pattern does not flag a correct single-candidate sys.path block.

    A script that uses only _kanban_root in sys.path (no _dev_tree) must not
    trigger the gate — that is the correctly-fixed form.
    """
    good_script = tmp_path / "good_dashboard.sh"
    good_script.write_text(
        "#!/usr/bin/env bash\n"
        "# Correct form: single live-install candidate.\n"
        "python3 <<'PYEOF'\n"
        "import os, sys\n"
        "_kanban_root = os.environ.get('PGAI_AGENT_KANBAN_ROOT_PATH', '')\n"
        "if _kanban_root and _kanban_root not in sys.path:\n"
        "    sys.path.insert(0, _kanban_root)\n"
        "PYEOF\n",
        encoding="utf-8",
    )

    # anti-pattern-allowlist: 1 (justification: test reads synthetic temp file it created, not a real scan)
    matching_lines = [
        line
        for line in good_script.read_text(encoding="utf-8").splitlines()
        if _SYS_PATH_DEV_TREE_PATTERN.search(line)
    ]

    assert not matching_lines, (
        f"Pattern incorrectly flagged correct single-candidate form: "
        f"{matching_lines!r}.  Only constructions referencing _dev_tree / "
        "PGAI_DEV_TREE_PATH should match."
    )


# ---------------------------------------------------------------------------
# 2. Fixture (a): import resolves from live root when dev tree lacks the module
# ---------------------------------------------------------------------------


def test_module_imports_from_live_root_when_dev_tree_lacks_it(
    tmp_path: pathlib.Path,
    monkeypatch: "pytest.MonkeyPatch",  # noqa: F821
) -> None:
    """sys.path built from kanban root only imports status_priority_cap from live root.

    Simulates the environment a live-install operator sees when PGAI_DEV_TREE_PATH
    points at a checkout that lacks dashboard/status_priority_cap (e.g. the public
    main branch before that module was added).  The fixed sys.path construction
    in column-render.sh uses only $PGAI_AGENT_KANBAN_ROOT_PATH, so the module
    must resolve from the live root regardless of what the dev tree contains.

    Assertion: module.__file__ starts with the kanban root, not the dev-tree path.
    """
    # Build a fake dev tree that deliberately lacks the module.
    fake_dev_tree = tmp_path / "fake_dev_tree"
    (fake_dev_tree / "team" / "pgai_agent_kanban" / "dashboard").mkdir(
        parents=True, exist_ok=True
    )
    # No status_priority_cap.py written here — the module is intentionally absent.
    (fake_dev_tree / "team" / "pgai_agent_kanban" / "__init__.py").write_text("")
    (fake_dev_tree / "team" / "pgai_agent_kanban" / "dashboard" / "__init__.py").write_text("")

    # The live kanban root is the real dev-tree root (where the module IS present
    # under team/pgai_agent_kanban/dashboard/).  In a real install, this would be
    # $HOME/pgai_agent_kanban with pgai_agent_kanban/ at its root; in the dev tree,
    # the package is under team/, so we point at the team/ parent directory.
    team_dir = _repo_root() / "team"
    live_kanban_root = str(team_dir)

    # Set PGAI_DEV_TREE_PATH to the fake dev tree (which lacks the module).
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(fake_dev_tree))

    # Simulate the fixed sys.path construction from column-render.sh:
    # single candidate — the live kanban root only.
    test_sys_path = [live_kanban_root]

    # Isolate the import from the current process's sys.path and sys.modules.
    # We use importlib machinery to load from a controlled path.
    module_name = "pgai_agent_kanban.dashboard.status_priority_cap"

    # Remove any cached version from the current interpreter so we can re-import
    # with a controlled path.
    cached_keys = [k for k in sys.modules if k == module_name or k.startswith(module_name + ".")]
    saved_modules: dict[str, types.ModuleType] = {}
    for key in cached_keys:
        saved_modules[key] = sys.modules.pop(key)

    original_path = list(sys.path)
    try:
        # Use only the live root — no dev-tree entry.
        sys.path = test_sys_path + [p for p in original_path if p not in test_sys_path]

        # Ensure the fake dev tree's team/ is NOT on the path.
        fake_team = str(fake_dev_tree / "team")
        assert fake_team not in sys.path, (
            "fake dev-tree team/ must not be on sys.path in this fixture"
        )

        mod = importlib.import_module(module_name)

        assert mod.__file__ is not None, (
            "Imported module has no __file__ — cannot verify import source."
        )
        assert mod.__file__.startswith(live_kanban_root), (
            f"Module resolved from wrong location.\n"
            f"  Expected prefix: {live_kanban_root!r}\n"
            f"  Got __file__:    {mod.__file__!r}\n"
            "The module must resolve from the live kanban root, not the dev tree."
        )

        # Confirm the fake dev tree path does not appear in __file__.
        assert str(fake_dev_tree) not in mod.__file__, (
            f"Module resolved from fake dev tree: {mod.__file__!r}.  "
            "The single-candidate sys.path must prevent any dev-tree resolution."
        )
    finally:
        # Restore original sys.path and sys.modules so other tests are unaffected.
        sys.path = original_path
        for key in cached_keys:
            sys.modules.pop(key, None)
        sys.modules.update(saved_modules)


# ---------------------------------------------------------------------------
# 3. Fixture (b): missing live module raises loud error, not silent dev-tree rescue
# ---------------------------------------------------------------------------


def test_missing_live_module_raises_module_not_found_error(
    tmp_path: pathlib.Path,
) -> None:
    """Importing with only the live root in sys.path raises ModuleNotFoundError when module absent.

    Simulates a deployment gap: the live pgai_agent_kanban/dashboard/status_priority_cap
    module is absent (e.g. a partial deploy or corrupt install).  With the fixed
    single-candidate sys.path, the import raises ModuleNotFoundError immediately,
    naming the module path in the exception.  No silent fallback to the dev tree occurs.

    This test proves the deployment-gap visibility guarantee: when the live package
    is broken, the operator sees a clear error rather than the system silently
    executing code from the dev tree.
    """
    # Build a minimal "live root" that has the pgai_agent_kanban package structure
    # but deliberately omits status_priority_cap.py.
    fake_live_root = tmp_path / "fake_live_root"
    dashboard_dir = fake_live_root / "pgai_agent_kanban" / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    (fake_live_root / "pgai_agent_kanban" / "__init__.py").write_text("")
    (dashboard_dir / "__init__.py").write_text("")
    # status_priority_cap.py intentionally NOT written — simulates deployment gap.

    module_name = "pgai_agent_kanban.dashboard.status_priority_cap"

    # Remove any cached version so we get a fresh import attempt.
    cached_keys = [k for k in sys.modules if k == module_name or k.startswith(module_name + ".")]
    saved_modules: dict[str, types.ModuleType] = {}
    for key in cached_keys:
        saved_modules[key] = sys.modules.pop(key)

    original_path = list(sys.path)
    try:
        # Simulate the fixed column-render.sh: single-candidate path — fake live root only.
        # The fake live root has the package skeleton but NOT the target module.
        sys.path = [str(fake_live_root)] + [
            p for p in original_path if p not in (str(fake_live_root),)
        ]

        # Remove any already-imported pgai_agent_kanban entries that could satisfy
        # the import from the original dev path.
        parent_keys = [k for k in sys.modules if k in (
            "pgai_agent_kanban",
            "pgai_agent_kanban.dashboard",
        )]
        saved_parents: dict[str, types.ModuleType] = {}
        for key in parent_keys:
            saved_parents[key] = sys.modules.pop(key)

        try:
            import pytest  # noqa: PLC0415
            with pytest.raises(
                (ModuleNotFoundError, ImportError),
                match=r"status_priority_cap|pgai_agent_kanban",
            ):
                importlib.import_module(module_name)
        finally:
            sys.modules.update(saved_parents)
    finally:
        sys.path = original_path
        for key in cached_keys:
            sys.modules.pop(key, None)
        sys.modules.update(saved_modules)
