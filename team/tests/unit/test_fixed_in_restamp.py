"""
test_fixed_in_restamp.py
========================
Unit coverage for the operator back-fill of ``## Fixed In``:
``team/scripts/restamp-fixed-in.sh`` and the
``pgai_agent_kanban.cm.fixed_in_restamp`` module behind it.

What is pinned here:
  - Against a synthetic ledger, each named done bug with an empty
    ``## Fixed In`` is stamped, each named bug with a value already set is
    reported as skipped and left byte-for-byte alone, and bugs that are not
    named are never written.
  - Run from a working directory holding a same-named, non-package
    ``pgai_agent_kanban/`` directory, with PYTHONPATH cleared, the script
    produces identical output and identical files.
  - Every bad invocation (no project, missing or malformed version, no bug
    named, a bug name matching no file or several) is refused dual-channel
    with exit 2 and writes nothing.
  - --help carries a worked back-fill example.
  - Correction mode (--replace-from): a named done bug whose value is
    exactly the named wrong version is rewritten to --version, an empty one
    is stamped, and every unnamed bug is byte-for-byte unchanged; a named bug
    carrying any other value refuses the whole run dual-channel with exit 2
    and nothing written; a malformed --replace-from is refused the same way.
    The in-place rewrite leaves every other byte of the file alone.
"""

from __future__ import annotations

import pathlib
import shlex

import pytest

from pgai_agent_kanban.cm.changelog_writer import parse_bug_file
from pgai_agent_kanban.cm.fixed_in_restamp import normalize_version, RestampRefused
from pgai_agent_kanban.cm.fixed_in_writeback import replace_fixed_in
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_DIR / "scripts" / "restamp-fixed-in.sh"
_PROJECT = "demo"


def _bug_text(name: str, status: str, fixed_in: str = "") -> str:
    """A minimal bug file in the ledger's shape, with a template Fixed In."""
    body = f"{fixed_in}\n" if fixed_in else ""
    return (
        f"# {name}\n\n"
        "## Status\n"
        f"{status}\n\n"
        "## Symptom\n"
        "Something misbehaves.\n\n"
        "## Fixed In\n"
        "<!-- Written by the release engine at the fixing release. -->\n"
        f"{body}\n"
        "## Public ID\n"
        "<!-- Assigned at first disclosure. -->\n"
    )


def _ledger(root: pathlib.Path) -> pathlib.Path:
    """A sandbox kanban root with one project whose ledger holds five bugs.

    Two are done with an empty Fixed In, one is done with a value already
    set, one is still running, and one is done and empty but is never named
    by the tests that stamp.
    """
    bugs = root / "projects" / _PROJECT / "bugs"
    bugs.mkdir(parents=True)
    (root / "tmp").mkdir()
    specs = [
        ("BUG-0001-first-fix", "done", ""),
        ("BUG-0002-second-fix", "done", ""),
        ("BUG-0003-already-recorded", "done", "v1.0.0"),
        ("BUG-0004-still-running", "running", ""),
        ("BUG-0005-not-named", "done", ""),
    ]
    for name, status, fixed_in in specs:
        (bugs / f"{name}.md").write_text(_bug_text(name, status, fixed_in), encoding="utf-8")
    return bugs


def _snapshot(bugs: pathlib.Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(bugs.glob("*.md"))}


def _shadowing_cwd(tmp_path: pathlib.Path) -> pathlib.Path:
    """A directory whose child ``pgai_agent_kanban/`` is not a Python package.

    The layout seen from the default kanban root's parent: the child carries
    the package's name, has no __init__.py, and holds unrelated files.
    """
    parent = tmp_path / "home"
    shadow = parent / "pgai_agent_kanban"
    (shadow / "projects").mkdir(parents=True)
    (shadow / "README.md").write_text("kanban root\n", encoding="utf-8")
    return parent


def _run(root: pathlib.Path, args: list[str], *, cwd: pathlib.Path | None = None, tmp_path: pathlib.Path):
    """Run the script against the sandbox *root*, every live-install name repointed or cleared."""
    argv = " ".join(shlex.quote(a) for a in args)
    script = (
        "env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PGAI_PROJECT_NAME -u PYTHONPATH "
        f"KANBAN_ROOT={shlex.quote(str(root))} "
        f"PGAI_AGENT_KANBAN_ROOT_PATH={shlex.quote(str(root))} "
        f"PGAI_AGENT_KANBAN_TEMP_DIR={shlex.quote(str(root / 'tmp'))} "
        f"{shlex.quote(str(_SCRIPT))} {argv}"
    )
    return run_bash(tmp_path, script, cwd=cwd)


def _fixed_in(path: pathlib.Path) -> str:
    return parse_bug_file(path).fixed_in


# ---------------------------------------------------------------------------
# Stamping behaviour
# ---------------------------------------------------------------------------


class TestRestamp:
    def test_stamps_empty_skips_set_and_leaves_unnamed_untouched(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        bugs = _ledger(root)
        before = _snapshot(bugs)

        result = _run(
            root,
            ["--project", _PROJECT, "--version", "v2.26.1",
             "BUG-0001", "BUG-0002-second-fix.md", "BUG-0003-already-recorded", "BUG-0004"],
            tmp_path=tmp_path,
        )
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "Traceback" not in output, output
        # Stamped: the two named done bugs with an empty value.
        assert _fixed_in(bugs / "BUG-0001-first-fix.md") == "v2.26.1"
        assert _fixed_in(bugs / "BUG-0002-second-fix.md") == "v2.26.1"
        assert "stamped: BUG-0001-first-fix.md" in result.stdout, output
        assert "stamped: BUG-0002-second-fix.md" in result.stdout, output
        # Skipped: the already-set value is reported and never overwritten.
        assert "skipped (already set: v1.0.0): BUG-0003-already-recorded.md" in result.stdout, output
        assert (bugs / "BUG-0003-already-recorded.md").read_bytes() == before["BUG-0003-already-recorded.md"]
        # Skipped: a named bug that has not shipped is not written.
        assert "skipped (status is running, not done): BUG-0004-still-running.md" in result.stdout, output
        assert (bugs / "BUG-0004-still-running.md").read_bytes() == before["BUG-0004-still-running.md"]
        # Untouched: the unnamed done bug keeps its empty value, byte for byte.
        assert (bugs / "BUG-0005-not-named.md").read_bytes() == before["BUG-0005-not-named.md"]
        assert "BUG-0005" not in result.stdout, output
        assert result.stdout.strip().splitlines()[-1] == "Fixed-In restamp v2.26.1: 2 stamped, 2 skipped."

    def test_second_run_skips_what_the_first_stamped(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        bugs = _ledger(root)
        args = ["--project", _PROJECT, "--version", "v2.26.1", "BUG-0001"]
        assert _run(root, args, tmp_path=tmp_path).returncode == 0
        after_first = _snapshot(bugs)

        again = _run(root, args, tmp_path=tmp_path)

        assert again.returncode == 0, again.stdout + again.stderr
        assert "skipped (already set: v2.26.1): BUG-0001-first-fix.md" in again.stdout
        assert _snapshot(bugs) == after_first

    def test_accepts_path_form_and_version_without_leading_v(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        bugs = _ledger(root)

        result = _run(
            root,
            ["--project", _PROJECT, "--version", "2.26.3", str(bugs / "BUG-0001-first-fix.md")],
            tmp_path=tmp_path,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _fixed_in(bugs / "BUG-0001-first-fix.md") == "v2.26.3"

    def test_shadowing_cwd_behaves_identically(self, tmp_path: pathlib.Path) -> None:
        args = ["--project", _PROJECT, "--version", "v2.26.1", "BUG-0001", "BUG-0003"]
        plain_root = tmp_path / "plain" / "root"
        shadow_root = tmp_path / "shadowed" / "root"
        plain_bugs = _ledger(plain_root)
        shadow_bugs = _ledger(shadow_root)
        shadow_cwd = _shadowing_cwd(tmp_path)

        plain = _run(plain_root, args, tmp_path=tmp_path)
        shadowed = _run(shadow_root, args, cwd=shadow_cwd, tmp_path=tmp_path)

        assert shadowed.returncode == plain.returncode == 0, shadowed.stdout + shadowed.stderr
        assert "Traceback" not in shadowed.stderr and "ImportError" not in shadowed.stderr, shadowed.stderr
        assert shadowed.stdout == plain.stdout
        assert _snapshot(shadow_bugs) == _snapshot(plain_bugs)
        assert _fixed_in(shadow_bugs / "BUG-0001-first-fix.md") == "v2.26.1"


# ---------------------------------------------------------------------------
# Refusals: dual-channel, exit 2, nothing written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "error_key"),
    [
        (["--project", _PROJECT, "BUG-0001"], "fixed_in_restamp.version_missing"),
        (["--project", _PROJECT, "--version", "2.26", "BUG-0001"], "fixed_in_restamp.version_malformed"),
        (["--project", _PROJECT, "--version", "latest", "BUG-0001"], "fixed_in_restamp.version_malformed"),
        (["--project", _PROJECT, "--version", "v2.26.1", "BUG-0001", "BUG-9999"], "fixed_in_restamp.bug_not_found"),
        (["--project", _PROJECT, "--version", "v2.26.1", "BUG-0001-no-such-slug.md"], "fixed_in_restamp.bug_not_found"),
        (["--project", _PROJECT, "--version", "v2.26.1"], "fixed_in_restamp.no_bugs_named"),
        (["--version", "v2.26.1", "BUG-0001"], "fixed_in_restamp.project_required"),
    ],
    ids=[
        "missing-version", "short-version", "word-version", "nonexistent-bug-among-valid",
        "nonexistent-file-name", "no-bugs-named", "no-project",
    ],
)
def test_bad_invocation_is_refused_and_writes_nothing(
    tmp_path: pathlib.Path, args: list[str], error_key: str
) -> None:
    root = tmp_path / "root"
    bugs = _ledger(root)
    before = _snapshot(bugs)

    result = _run(root, args, tmp_path=tmp_path)

    assert result.returncode == 2, result.stdout + result.stderr
    assert f"error:   {error_key}" in result.stderr, result.stderr
    assert "\nmessage: " in result.stderr and "\nremedy:  " in result.stderr, result.stderr
    assert "stamped" not in result.stdout, result.stdout
    assert _snapshot(bugs) == before


def test_ambiguous_bug_id_is_refused_and_writes_nothing(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    bugs = _ledger(root)
    (bugs / "BUG-0001-a-second-file.md").write_text(
        _bug_text("BUG-0001-a-second-file", "done"), encoding="utf-8"
    )
    before = _snapshot(bugs)

    result = _run(root, ["--project", _PROJECT, "--version", "v2.26.1", "BUG-0001"], tmp_path=tmp_path)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "error:   fixed_in_restamp.bug_ambiguous" in result.stderr, result.stderr
    assert "BUG-0001-a-second-file.md" in result.stderr and "BUG-0001-first-fix.md" in result.stderr
    assert _snapshot(bugs) == before


def test_missing_bugs_directory_is_refused(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    (root / "tmp").mkdir()

    result = _run(root, ["--project", _PROJECT, "--version", "v2.26.1", "BUG-0001"], tmp_path=tmp_path)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "error:   fixed_in_restamp.bugs_dir_missing" in result.stderr, result.stderr


def test_remedy_names_the_project_in_hand(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    _ledger(root)

    result = _run(root, ["--project", _PROJECT, "--version", "bad", "BUG-0001"], tmp_path=tmp_path)

    assert f"remedy:  team/scripts/restamp-fixed-in.sh --project {_PROJECT} --version v1.4.0" in result.stderr


# ---------------------------------------------------------------------------
# Help and version normalisation
# ---------------------------------------------------------------------------


def test_help_documents_a_worked_backfill_example(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    _ledger(root)

    result = _run(root, ["--help"], tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "--project NAME" in result.stdout and "--version vX.Y.Z" in result.stdout
    assert "restamp-fixed-in.sh --project pgai-agent-kanban --version v1.4.0" in result.stdout
    assert "never overwritten" in result.stdout.replace("\n", " ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("v2.26.1", "v2.26.1"), ("2.26.1", "v2.26.1"), (" V10.0.3 ", "v10.0.3")],
)
def test_version_is_normalised_to_canonical_form(raw: str, expected: str) -> None:
    assert normalize_version(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "v2.26", "v2.26.1-rc1", "vX.Y.Z"])
def test_malformed_or_absent_version_raises(raw: str | None) -> None:
    with pytest.raises(RestampRefused):
        normalize_version(raw)


# ---------------------------------------------------------------------------
# Correction mode: --replace-from
# ---------------------------------------------------------------------------

_WRONG = "v2.26.4"
_RIGHT = "v2.26.1"
_MIS_STAMPED = [f"BUG-001{n}" for n in range(1, 8)]


def _mis_stamped_ledger(root: pathlib.Path) -> pathlib.Path:
    """A ledger where seven bugs read the wrong release, beside bugs not named.

    The unnamed set deliberately includes a bug that also reads the wrong
    value, one with another value, an empty done bug and a running bug, so a
    correction that reached past its list would show up in the snapshot.
    """
    bugs = _ledger(root)
    for bug_id in _MIS_STAMPED:
        name = f"{bug_id}-shipped-earlier"
        (bugs / f"{name}.md").write_text(_bug_text(name, "done", _WRONG), encoding="utf-8")
    name = "BUG-0018-really-shipped-in-wrong-release"
    (bugs / f"{name}.md").write_text(_bug_text(name, "done", _WRONG), encoding="utf-8")
    return bugs


class TestCorrectionMode:
    def test_rewrites_exactly_the_named_wrong_stamps(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        bugs = _mis_stamped_ledger(root)
        before = _snapshot(bugs)

        result = _run(
            root,
            ["--project", _PROJECT, "--version", _RIGHT, "--replace-from", _WRONG, *_MIS_STAMPED],
            tmp_path=tmp_path,
        )
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        named = {f"{b}-shipped-earlier.md" for b in _MIS_STAMPED}
        for file_name in sorted(named):
            assert _fixed_in(bugs / file_name) == _RIGHT, file_name
            assert f"replaced (was {_WRONG}): {file_name}" in result.stdout, output
        after = _snapshot(bugs)
        for file_name, content in before.items():
            if file_name not in named:
                assert after[file_name] == content, f"unnamed {file_name} was written"
        assert result.stdout.strip().splitlines()[-1] == (
            f"Fixed-In restamp {_RIGHT}: 0 stamped, 7 replaced from {_WRONG}, 0 skipped."
        )

    def test_empty_is_stamped_and_not_done_is_skipped(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        bugs = _mis_stamped_ledger(root)
        before = _snapshot(bugs)

        result = _run(
            root,
            ["--project", _PROJECT, "--version", _RIGHT, "--replace-from", _WRONG,
             "BUG-0011", "BUG-0001", "BUG-0004"],
            tmp_path=tmp_path,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _fixed_in(bugs / "BUG-0011-shipped-earlier.md") == _RIGHT
        assert _fixed_in(bugs / "BUG-0001-first-fix.md") == _RIGHT
        assert "stamped: BUG-0001-first-fix.md" in result.stdout
        assert "skipped (status is running, not done): BUG-0004-still-running.md" in result.stdout
        assert (bugs / "BUG-0004-still-running.md").read_bytes() == before["BUG-0004-still-running.md"]

    def test_other_set_value_refuses_whole_run_and_writes_nothing(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        bugs = _mis_stamped_ledger(root)
        before = _snapshot(bugs)

        # The first mis-stamped bug alone would be corrected; the already-recorded
        # bug carries v1.0.0, which is neither empty nor the named wrong version.
        result = _run(
            root,
            ["--project", _PROJECT, "--version", _RIGHT, "--replace-from", _WRONG,
             "BUG-0011", "BUG-0003"],
            tmp_path=tmp_path,
        )

        assert result.returncode == 2, result.stdout + result.stderr
        assert "error:   fixed_in_restamp.replace_from_mismatch" in result.stderr, result.stderr
        assert "BUG-0003-already-recorded.md (has v1.0.0)" in result.stderr, result.stderr
        assert "\nmessage: " in result.stderr and "\nremedy:  " in result.stderr, result.stderr
        # The remedy re-runs the correction on the bugs that do qualify.
        assert (
            f"remedy:  team/scripts/restamp-fixed-in.sh --project {_PROJECT} --version {_RIGHT} "
            f"--replace-from {_WRONG} BUG-0011-shipped-earlier"
        ) in result.stderr, result.stderr
        assert "replaced" not in result.stdout and "stamped" not in result.stdout, result.stdout
        assert _snapshot(bugs) == before

    def test_second_correction_run_is_refused_and_writes_nothing(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        bugs = _mis_stamped_ledger(root)
        args = ["--project", _PROJECT, "--version", _RIGHT, "--replace-from", _WRONG, "BUG-0011"]
        assert _run(root, args, tmp_path=tmp_path).returncode == 0
        after_first = _snapshot(bugs)

        again = _run(root, args, tmp_path=tmp_path)

        assert again.returncode == 2, again.stdout + again.stderr
        assert "error:   fixed_in_restamp.replace_from_mismatch" in again.stderr, again.stderr
        assert _snapshot(bugs) == after_first

    @pytest.mark.parametrize("bad", ["2.26", "latest", ""])
    def test_malformed_replace_from_is_refused(self, tmp_path: pathlib.Path, bad: str) -> None:
        root = tmp_path / "root"
        bugs = _mis_stamped_ledger(root)
        before = _snapshot(bugs)

        result = _run(
            root,
            ["--project", _PROJECT, "--version", _RIGHT, "--replace-from", bad, "BUG-0011"],
            tmp_path=tmp_path,
        )

        assert result.returncode == 2, result.stdout + result.stderr
        assert "error:   fixed_in_restamp.replace_from_malformed" in result.stderr, result.stderr
        assert "\nremedy:  " in result.stderr, result.stderr
        assert _snapshot(bugs) == before

    def test_help_documents_the_correction_flag(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        _ledger(root)

        result = _run(root, ["--help"], tmp_path=tmp_path)

        assert result.returncode == 0, result.stderr
        assert "--replace-from vA.B.C" in result.stdout
        assert "--version v1.4.0 --replace-from v1.5.0" in result.stdout
        assert "fixed_in_restamp.replace_from_mismatch" in result.stdout


def test_replace_fixed_in_swaps_only_the_value(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "BUG-0001-x.md"
    # The wrong version also appears inside a comment and elsewhere in the
    # file; only the section's value may change.
    original = (
        "# BUG-0001-x\n\n## Status\ndone\n\n## Symptom\nSeen in v2.26.4.\n\n"
        "## Fixed In\n<!-- e.g. v2.26.4 -->\nv2.26.4  \n\n## Public ID\n"
    )
    path.write_text(original, encoding="utf-8")

    assert replace_fixed_in(path, "v2.26.4", "v2.26.1") is True
    assert path.read_text(encoding="utf-8") == original.replace(
        "\nv2.26.4  \n", "\nv2.26.1  \n"
    )


@pytest.mark.parametrize("fixed_in", ["", "v2.26.2", "v2.26.40"])
def test_replace_fixed_in_leaves_other_values_alone(tmp_path: pathlib.Path, fixed_in: str) -> None:
    path = tmp_path / "BUG-0001-x.md"
    path.write_text(_bug_text("BUG-0001-x", "done", fixed_in), encoding="utf-8")
    before = path.read_bytes()

    assert replace_fixed_in(path, "v2.26.4", "v2.26.1") is False
    assert path.read_bytes() == before
