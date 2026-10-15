"""
test_fixed_in_writeback.py
==========================
Unit coverage for the release engine's ## Fixed In writeback: the
``pgai_agent_kanban.cm.fixed_in_writeback`` module and the two
``release_finalize.sh`` helpers that drive it around bundled-item promotion.

What is pinned here:
  - Only bugs named as promoted by this release are stamped; a done bug from
    an earlier release with an empty ## Fixed In is left byte-for-byte alone.
  - An already-set ## Fixed In is never overwritten.
  - The bash helpers import the package cleanly from a working directory
    that holds a same-named, non-package ``pgai_agent_kanban/`` directory
    (the kanban root seen from its parent), and print the result line with
    no traceback.
  - The closing-summary outcome line reports a failure as a failure, never
    as a stamp that did not happen.
  - Step 16b itself, run as the finalizer carries it, stamps only this
    release's bugs when the caller's cwd is the parent of a kanban root
    laid out like the live install (``scripts/`` beside the real
    ``pgai_agent_kanban/`` package), with no traceback.
  - Step 16b names every done bug still lacking ## Fixed In, before the
    result line, without writing any bug file; when every done bug carries
    ## Fixed In its output is exactly the progress line and the result line.
"""

from __future__ import annotations

import pathlib

from pgai_agent_kanban.cm.fixed_in_writeback import (
    done_bugs_without_fixed_in,
    main,
    running_bug_names,
    stamp_fixed_in,
    write_fixed_in,
)
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PP_RUN_OPS_SH = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_RELEASE_FINALIZE_SH = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_finalize.sh"

_FIXED_IN_TEMPLATE = (
    "## Fixed In\n<!-- Written by the release engine at the fixing release. -->\n"
)


def _bug_text(name: str, status: str, fixed_in: str = "") -> str:
    """A minimal bug file in the ledger's shape, with a template Fixed In."""
    body = f"{fixed_in}\n" if fixed_in else ""
    return (
        f"# {name}\n\n"
        "## Status\n"
        f"{status}\n\n"
        "## Symptom\n"
        "Something misbehaves.\n\n"
        f"{_FIXED_IN_TEMPLATE}{body}\n"
        "## Public ID\n"
        "<!-- Assigned at first disclosure. -->\n"
    )


def _write_bug(bugs_dir: pathlib.Path, name: str, status: str, fixed_in: str = "") -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    path = bugs_dir / f"{name}.md"
    path.write_text(_bug_text(name, status, fixed_in), encoding="utf-8")
    return path


def _fixed_in_value(path: pathlib.Path) -> str:
    """The ## Fixed In body with template comments removed."""
    from pgai_agent_kanban.cm.changelog_writer import parse_bug_file

    return parse_bug_file(path).fixed_in


# ---------------------------------------------------------------------------
# Module logic
# ---------------------------------------------------------------------------


class TestStampScope:
    """Which bug files a stamp run may touch."""

    def test_only_named_done_bug_is_stamped_and_earlier_done_bug_is_untouched(
        self, tmp_path: pathlib.Path
    ) -> None:
        bugs = tmp_path / "bugs"
        current = _write_bug(bugs, "BUG-0002-current-release", "done")
        earlier = _write_bug(bugs, "BUG-0001-earlier-release", "done")
        earlier_before = earlier.read_bytes()

        result = stamp_fixed_in(bugs, "v1.4.0", [current.name])

        assert _fixed_in_value(current) == "v1.4.0", current.read_text()
        assert earlier.read_bytes() == earlier_before, (
            "a done bug that this release did not promote must not be written, "
            f"even with an empty ## Fixed In; it now reads:\n{earlier.read_text()}"
        )
        assert (result.stamped, result.earlier_unstamped, result.failed) == (1, 1, 0), result

    def test_already_set_fixed_in_is_never_overwritten(self, tmp_path: pathlib.Path) -> None:
        bugs = tmp_path / "bugs"
        bug = _write_bug(bugs, "BUG-0003-already-stamped", "done", fixed_in="v1.3.9")
        before = bug.read_bytes()

        result = stamp_fixed_in(bugs, "v1.4.0", [bug.name])

        assert bug.read_bytes() == before
        assert (result.stamped, result.already_set) == (0, 1), result

    def test_named_bug_still_running_is_not_stamped(self, tmp_path: pathlib.Path) -> None:
        """A bug promotion carried (left at running) was not closed by this release."""
        bugs = tmp_path / "bugs"
        bug = _write_bug(bugs, "BUG-0004-carried", "running")
        before = bug.read_bytes()

        result = stamp_fixed_in(bugs, "v1.4.0", [bug.name])

        assert bug.read_bytes() == before
        assert (result.stamped, result.not_done) == (0, 1), result

    def test_name_with_directory_part_cannot_reach_outside_bugs_dir(
        self, tmp_path: pathlib.Path
    ) -> None:
        bugs = tmp_path / "bugs"
        outside = _write_bug(tmp_path / "elsewhere", "BUG-0005-outside", "done")
        before = outside.read_bytes()

        result = stamp_fixed_in(bugs, "v1.4.0", [f"../elsewhere/{outside.name}"])

        assert outside.read_bytes() == before
        assert result.stamped == 0 and result.failed == 1, result

    def test_running_lists_only_running_bugs(self, tmp_path: pathlib.Path) -> None:
        bugs = tmp_path / "bugs"
        _write_bug(bugs, "BUG-0010-running", "running")
        _write_bug(bugs, "BUG-0011-open", "open")
        _write_bug(bugs, "BUG-0012-done", "done")

        assert running_bug_names(bugs) == ["BUG-0010-running.md"]
        assert running_bug_names(tmp_path / "absent") == []


class TestWriteFixedIn:
    """Where the version lands inside the bug file."""

    def test_value_lands_after_the_template_comment(self, tmp_path: pathlib.Path) -> None:
        bug = _write_bug(tmp_path, "BUG-0020-template", "done")
        assert write_fixed_in(bug, "v2.0.1") is True
        text = bug.read_text()
        assert f"{_FIXED_IN_TEMPLATE}\nv2.0.1\n" in text or f"{_FIXED_IN_TEMPLATE}v2.0.1\n" in text, text
        assert "## Public ID" in text

    def test_absent_section_is_appended(self, tmp_path: pathlib.Path) -> None:
        bug = tmp_path / "BUG-0021-no-section.md"
        bug.write_text("# BUG-0021\n\n## Status\ndone\n", encoding="utf-8")
        assert write_fixed_in(bug, "v2.0.1") is True
        assert bug.read_text().endswith("\n\n## Fixed In\nv2.0.1\n")


# ---------------------------------------------------------------------------
# release_finalize.sh helpers, driven from a shadowing working directory
# ---------------------------------------------------------------------------


def _shadowing_cwd(tmp_path: pathlib.Path) -> pathlib.Path:
    """A directory whose child ``pgai_agent_kanban/`` is not a Python package.

    Mirrors the default layout seen from the kanban root's parent: the child
    carries the package's name, has no __init__.py, and holds unrelated files.
    """
    parent = tmp_path / "home"
    root = parent / "pgai_agent_kanban"
    (root / "projects").mkdir(parents=True)
    (root / "README.md").write_text("kanban root\n", encoding="utf-8")
    return parent


def _run_helpers(
    tmp_path: pathlib.Path, bugs_dir: pathlib.Path, promote: list[str], *, prelude: str = ""
):
    """Source the helpers, snapshot, promote *promote* to done, stamp, print outcome.

    Runs from a shadowing cwd with PYTHONPATH cleared, so nothing but the
    helpers' own path composition can make the package importable.
    """
    cwd = _shadowing_cwd(tmp_path)
    promote_cmds = "\n".join(
        f"sed -i 's/^running$/done/' {bugs_dir / name}" for name in promote
    )
    script = f"""
set -euo pipefail
unset PYTHONPATH
export KANBAN_ROOT={cwd / 'pgai_agent_kanban'}
source {_PP_RUN_OPS_SH}
source {_RELEASE_FINALIZE_SH}
{prelude}
_cm_release_snapshot_running_bugs {bugs_dir}
{promote_cmds}
_cm_release_stamp_fixed_in {bugs_dir} v1.4.0
echo "OUTCOME: ${{_CM_FIXED_IN_OUTCOME}}"
"""
    return run_bash(tmp_path, script, cwd=cwd)


class TestReleaseFinalizeHelpers:
    def test_writeback_imports_cleanly_from_shadowing_cwd_and_stamps_this_release_only(
        self, tmp_path: pathlib.Path
    ) -> None:
        bugs = tmp_path / "ledger" / "bugs"
        current = _write_bug(bugs, "BUG-0002-current-release", "running")
        earlier = _write_bug(bugs, "BUG-0001-earlier-release", "done")
        earlier_before = earlier.read_bytes()

        result = _run_helpers(tmp_path, bugs, [current.name])
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "Traceback" not in output and "ImportError" not in output, output
        assert "[Step 16b] Fixed-In writeback: 1 stamped, 0 already set, 0 not done, 1 earlier" in result.stdout, output
        assert "OUTCOME: ## Fixed In: v1.4.0 — 1 stamped," in result.stdout, output
        assert _fixed_in_value(current) == "v1.4.0", current.read_text()
        assert earlier.read_bytes() == earlier_before, earlier.read_text()

    def test_failed_writeback_is_reported_as_failed_and_does_not_stop_the_release(
        self, tmp_path: pathlib.Path
    ) -> None:
        bugs = tmp_path / "ledger" / "bugs"
        current = _write_bug(bugs, "BUG-0002-current-release", "running")
        before = current.read_bytes()
        # The stamp invocation fails the way an unimportable package would.
        prelude = (
            "pp_run_ops() { if [[ \"$2\" == stamp ]]; then "
            "echo 'ImportError: simulated' >&2; return 1; fi; "
            "PYTHONPATH=\"$(pp_pythonpath)\" python3 -m \"$@\"; }"
        )

        result = _run_helpers(tmp_path, bugs, [current.name], prelude=prelude)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "OUTCOME: ## Fixed In: v1.4.0 writeback FAILED (exit 1)" in result.stdout, output
        assert "stamped on" not in result.stdout, output
        assert "[Step 16b] WARNING: Fixed-In writeback encountered an error" in result.stderr, output
        # Only the simulated promotion touched the file; nothing was stamped.
        assert current.read_bytes() == before.replace(b"\nrunning\n", b"\ndone\n")
        assert _fixed_in_value(current) == "", current.read_text()

    def test_failed_snapshot_is_reported_as_failed(self, tmp_path: pathlib.Path) -> None:
        bugs = tmp_path / "ledger" / "bugs"
        _write_bug(bugs, "BUG-0002-current-release", "running")
        prelude = "pp_run_ops() { return 1; }"

        result = _run_helpers(tmp_path, bugs, [], prelude=prelude)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "OUTCOME: ## Fixed In: v1.4.0 writeback FAILED (exit 3)" in result.stdout
        assert "running-bug snapshot taken before Step 13c failed" in result.stdout


# ---------------------------------------------------------------------------
# Step 16b as release_finalize.sh runs it, from the kanban root's parent
# ---------------------------------------------------------------------------


def _step_16b_block() -> str:
    """The Step 16b body exactly as the release finalizer carries it.

    Everything from the ``--- Step 16b`` marker comment up to the next
    step's marker (``--- Step 16c``, the archive), so the test runs whatever
    form the step currently takes rather than a copy of the helper call.
    """
    text = _RELEASE_FINALIZE_SH.read_text(encoding="utf-8")
    start = text.index("# --- Step 16b")
    end = text.index("# --- Step 16c", start)
    return text[start:end]


def _installed_layout(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A kanban root shaped like the live install, and its parent directory.

    The root holds ``scripts/`` and the ``pgai_agent_kanban/`` package side by
    side (linked from this tree) and has no __init__.py of its own, so from
    the parent it is an importable-looking directory with the package's name
    that contains the real package one level down.
    """
    parent = tmp_path / "home"
    root = parent / "pgai_agent_kanban"
    (root / "projects").mkdir(parents=True)
    (root / "scripts").symlink_to(_TEAM_DIR / "scripts", target_is_directory=True)
    (root / "pgai_agent_kanban").symlink_to(
        _TEAM_DIR / "pgai_agent_kanban", target_is_directory=True
    )
    return parent, root


class TestStep16bFromKanbanRootParent:
    """Step 16b stamps this release's bugs when the caller's cwd shadows the package."""

    def test_step_16b_stamps_from_parent_of_kanban_root_without_traceback(
        self, tmp_path: pathlib.Path
    ) -> None:
        parent, root = _installed_layout(tmp_path)
        bugs = root / "projects" / "demo" / "bugs"
        current = _write_bug(bugs, "BUG-0002-current-release", "running")
        earlier = _write_bug(bugs, "BUG-0001-earlier-release", "done")
        earlier_before = earlier.read_bytes()

        script = f"""
set -euo pipefail
unset PYTHONPATH
export KANBAN_ROOT={root}
source {root / 'scripts' / 'lib' / 'pp_run_ops.sh'}
source {root / 'scripts' / 'lib' / 'cm' / 'release_finalize.sh'}
ACTIVE_RC=v1.4.0
_cm_release_bugs_dir={bugs}
_cm_release_snapshot_running_bugs "$_cm_release_bugs_dir"
sed -i 's/^running$/done/' {current}
{_step_16b_block()}
echo "OUTCOME: ${{_CM_FIXED_IN_OUTCOME:-unset}}"
"""
        result = run_bash(tmp_path, script, cwd=parent)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "Traceback" not in output and "ImportError" not in output, (
            "Step 16b must import the package from its own tree, not the "
            f"same-named directory in the caller's cwd:\n{output}"
        )
        assert (
            "[Step 16b] Fixed-In writeback: 1 stamped, 0 already set, 0 not done, 1 earlier"
            in result.stdout
        ), output
        assert "OUTCOME: ## Fixed In: v1.4.0 — 1 stamped," in result.stdout, output
        assert _fixed_in_value(current) == "v1.4.0", current.read_text()
        assert earlier.read_bytes() == earlier_before, (
            "Step 16b stamped a bug an earlier release closed:\n" + earlier.read_text()
        )


# ---------------------------------------------------------------------------
# Narration of done bugs that still lack ## Fixed In
# ---------------------------------------------------------------------------

_NARRATION_PREFIX = "Done bug without ## Fixed In: "


def _ledger_with_one_missing_stamp(bugs: pathlib.Path) -> dict[str, pathlib.Path]:
    """One done bug without ## Fixed In, one done bug with it, one running bug."""
    return {
        "missing": _write_bug(bugs, "BUG-0031-done-unstamped", "done"),
        "stamped": _write_bug(bugs, "BUG-0032-done-stamped", "done", fixed_in="v1.3.0"),
        "running": _write_bug(bugs, "BUG-0033-still-running", "running"),
    }


class TestMissingFixedInNarration:
    """The stamp run names done bugs lacking ## Fixed In and writes none of them."""

    def test_scan_names_only_the_done_bug_without_fixed_in(self, tmp_path: pathlib.Path) -> None:
        bugs = tmp_path / "bugs"
        ledger = _ledger_with_one_missing_stamp(bugs)

        assert done_bugs_without_fixed_in(bugs) == [ledger["missing"].name]
        assert done_bugs_without_fixed_in(tmp_path / "absent") == []

    def test_stamp_command_narrates_before_result_line_and_writes_nothing(
        self, tmp_path: pathlib.Path, capsys
    ) -> None:
        bugs = tmp_path / "bugs"
        ledger = _ledger_with_one_missing_stamp(bugs)
        before = {key: path.read_bytes() for key, path in ledger.items()}

        exit_code = main(["stamp", str(bugs), "v1.4.0", ledger["running"].name])
        lines = capsys.readouterr().out.splitlines()

        assert exit_code == 0, lines
        narration = [line for line in lines if line.startswith(_NARRATION_PREFIX)]
        assert len(narration) == 1, lines
        assert ledger["missing"].name in narration[0], lines
        assert lines[-1].startswith("Fixed-In writeback: 0 stamped, 0 already set, 1 not done, 1 earlier"), lines
        for key, path in ledger.items():
            assert path.read_bytes() == before[key], (
                f"the narration must not write bug files; {path.name} now reads:\n{path.read_text()}"
            )


def _run_step_16b(
    tmp_path: pathlib.Path, bugs: pathlib.Path, promote: list[pathlib.Path]
):
    """Run Step 16b as the finalizer carries it, promoting *promote* after the snapshot."""
    parent, root = _installed_layout(tmp_path)
    promote_cmds = "\n".join(f"sed -i 's/^running$/done/' {path}" for path in promote)
    script = f"""
set -euo pipefail
unset PYTHONPATH
export KANBAN_ROOT={root}
source {root / 'scripts' / 'lib' / 'pp_run_ops.sh'}
source {root / 'scripts' / 'lib' / 'cm' / 'release_finalize.sh'}
ACTIVE_RC=v1.4.0
_cm_release_bugs_dir={bugs}
_cm_release_snapshot_running_bugs "$_cm_release_bugs_dir"
{promote_cmds}
{_step_16b_block()}
echo "OUTCOME: ${{_CM_FIXED_IN_OUTCOME:-unset}}"
"""
    return run_bash(tmp_path, script, cwd=parent)


class TestStep16bMissingFixedInNarration:
    """Step 16b, as the finalizer runs it, surfaces a missed stamp read-only."""

    def test_step_16b_names_only_done_bug_without_fixed_in_and_changes_no_other_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        bugs = tmp_path / "ledger" / "bugs"
        ledger = _ledger_with_one_missing_stamp(bugs)
        closing = _write_bug(bugs, "BUG-0034-closed-by-this-release", "running")
        ledger_before = {key: path.read_bytes() for key, path in ledger.items()}
        closing_before = closing.read_bytes()

        # Only the closing bug is promoted; the other running bug stays carried.
        result = _run_step_16b(tmp_path, bugs, [closing])
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        narration = [
            line for line in result.stdout.splitlines()
            if line.startswith(f"[Step 16b] {_NARRATION_PREFIX}")
        ]
        assert len(narration) == 1, output
        assert ledger["missing"].name in narration[0], output
        assert result.stdout.splitlines().index(narration[0]) < result.stdout.splitlines().index(
            "[Step 16b] Fixed-In writeback: 1 stamped, 0 already set, 1 not done, "
            "1 earlier done bug(s) left unstamped."
        ), output
        assert "OUTCOME: ## Fixed In: v1.4.0 — 1 stamped," in result.stdout, output
        for key, path in ledger.items():
            assert path.read_bytes() == ledger_before[key], (
                f"Step 16b changed {path.name}:\n{path.read_text()}"
            )
        # The closing bug carries exactly the promotion plus Step 16b's own stamp.
        expected = tmp_path / "expected" / closing.name
        expected.parent.mkdir()
        expected.write_bytes(closing_before.replace(b"\nrunning\n", b"\ndone\n"))
        write_fixed_in(expected, "v1.4.0")
        assert closing.read_bytes() == expected.read_bytes(), closing.read_text()

    def test_step_16b_output_is_unchanged_when_every_done_bug_has_fixed_in(
        self, tmp_path: pathlib.Path
    ) -> None:
        bugs = tmp_path / "ledger" / "bugs"
        _write_bug(bugs, "BUG-0041-done-stamped", "done", fixed_in="v1.3.0")
        closing = _write_bug(bugs, "BUG-0042-closed-by-this-release", "running")

        result = _run_step_16b(tmp_path, bugs, [closing])
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        step_lines = [line for line in result.stdout.splitlines() if line.startswith("[Step 16b]")]
        assert step_lines == [
            "[Step 16b] Stamping ## Fixed In: v1.4.0 on bugs closed in this release...",
            "[Step 16b] Fixed-In writeback: 1 stamped, 0 already set, 0 not done, "
            "0 earlier done bug(s) left unstamped.",
        ], output
        assert (
            "OUTCOME: ## Fixed In: v1.4.0 — 1 stamped, 0 already set, 0 not done, "
            "0 earlier done bug(s) left unstamped. (Step 16b)"
        ) in result.stdout, output
