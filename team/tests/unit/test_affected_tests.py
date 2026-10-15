"""
test_affected_tests.py
======================
The affected-test finder (``pgai_agent_kanban.testing.affected``) selects
the test files a change should run: test files under a tests root that cite
a changed script, module, function or command name, or that list the
directory a changed file sits in, ranked by how strongly they cite them and
cut to a cap.

Interface-layer tests call :func:`affected_tests` and
:func:`names_from_diff` directly and assert on the returned paths and
names. Diff-driven cases build a throwaway git repository under tmp_path,
commit a base, change it on top, and read ``base...HEAD`` from it -- the
same local-only read the pre-merge runner makes. Two adapter tests run the
command line by file path from an unrelated directory with a rebuilt
environment.

Every fixture tree lives under pytest's tmp_path; nothing reads or writes
the live install or the dev tree's own tests.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.testing import affected
from pgai_agent_kanban.testing.affected import (
    AffectedTests,
    DiffReadError,
    affected_tests,
    ancestor_directories,
    names_from_diff,
    split_generic_names,
)
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_MODULE_PATH = pathlib.Path(affected.__file__).resolve()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write(root: pathlib.Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def _tests_root(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    root = tmp_path / "tests"
    root.mkdir()
    _write(root, files)
    return root


def _relative(selection: list[pathlib.Path], root: pathlib.Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in selection]


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


class _Repo:
    """A throwaway git repository with a committed ``base`` branch."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "base")

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.path), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
             *args],
            capture_output=True, text=True, check=False,
        )
        assert done.returncode == 0, f"git {' '.join(args)} failed\n{_diagnostic(done)}"
        return done.stdout

    def commit(self, files: dict[str, str], message: str, *, delete: tuple[str, ...] = ()) -> None:
        _write(self.path, files)
        for relative in delete:
            (self.path / relative).unlink()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def start_change(self) -> None:
        self.git("checkout", "-q", "-b", "change")


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> _Repo:
    require_binary("git", label="affected-test-finder")
    return _Repo(tmp_path / "repo")


_WIDGETS_BASE = """\
    def compute_widget_total(items):
        return sum(items)


    def format_widget_label(name):
        return name.title()
    """

_WIDGETS_CHANGED = """\
    def compute_widget_total(items):
        return sum(item for item in items if item > 0)


    def format_widget_label(name):
        return name.title()
    """


# ---------------------------------------------------------------------------
# Changed function names select the tests that cite them
# ---------------------------------------------------------------------------


def test_function_body_change_selects_a_test_citing_the_function_by_name(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({"pkg/widgets.py": _WIDGETS_BASE}, "base")
    repo.start_change()
    repo.commit({"pkg/widgets.py": _WIDGETS_CHANGED}, "change one function body")
    tests = _tests_root(tmp_path, {
        "unit/test_totals.py": """\
            from pkg.inventory import compute_widget_total

            def test_negative_items_are_ignored():
                assert compute_widget_total([1, -2]) == 1
            """,
        "unit/test_unrelated.py": """\
            def test_something_else():
                assert format_order_number(3) == "003"
            """,
    })
    assert "widgets" not in (tests / "unit/test_totals.py").read_text(encoding="utf-8")

    names = names_from_diff(repo.path, "base")
    selection = affected_tests(names, tests, 60)

    assert "compute_widget_total" in names
    assert "format_widget_label" not in names, (
        f"an untouched function was reported as changed: {names}"
    )
    assert _relative(selection, tests) == ["unit/test_totals.py"]
    assert not selection.cap_applied


def test_changed_shell_script_and_module_are_matched_by_file_and_module_name(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({
        "scripts/deploy-widgets.sh": "#!/usr/bin/env bash\necho deploy\n",
        "pkg/lib/ledger_store.py": "LIMIT = 1\n",
    }, "base")
    repo.start_change()
    repo.commit({
        "scripts/deploy-widgets.sh": "#!/usr/bin/env bash\necho deploying\n",
        "pkg/lib/ledger_store.py": "LIMIT = 2\n",
    }, "change a script and a module")
    tests = _tests_root(tmp_path, {
        "unit/test_deploy_command.py": """\
            def test_deploy_prints(run):
                assert run(["scripts/deploy-widgets.sh"]).returncode == 0
            """,
        "unit/test_ledger.py": """\
            from pkg.lib import ledger_store

            def test_limit():
                assert ledger_store.LIMIT > 0
            """,
        "unit/test_lookalikes.py": """\
            def test_other_names():
                assert run(["scripts/predeploy-widgets.sh", "ledger_store_v2"])
            """,
    })

    names = names_from_diff(repo.path, "base")
    selection = affected_tests(names, tests, 60)

    assert {"deploy-widgets.sh", "ledger_store"} <= set(names), names
    assert sorted(_relative(selection, tests)) == [
        "unit/test_deploy_command.py", "unit/test_ledger.py",
    ]


def test_removed_function_is_still_a_changed_name(repo: _Repo) -> None:
    repo.commit({"pkg/widgets.py": _WIDGETS_BASE}, "base")
    repo.start_change()
    repo.commit({"pkg/widgets.py": "def compute_widget_total(items):\n    return sum(items)\n"},
                "remove a function")

    names = names_from_diff(repo.path, "base")

    assert "format_widget_label" in names
    assert "compute_widget_total" not in names, (
        f"a function the change did not touch was reported: {names}"
    )


def test_removing_the_last_statement_of_a_function_reports_that_function(
    repo: _Repo,
) -> None:
    base = textwrap.dedent("""\
        def compute_widget_total(items):
            checked = [i for i in items if i]
            return sum(checked)


        def format_widget_label(name):
            return name.title()
        """)
    repo.commit({"pkg/widgets.py": base}, "base")
    repo.start_change()
    changed = base.replace("    return sum(checked)\n", "")
    assert changed != base
    repo.commit({"pkg/widgets.py": changed}, "remove a function's last statement")

    names = names_from_diff(repo.path, "base")

    assert "compute_widget_total" in names, names
    assert "format_widget_label" not in names, names


def test_removing_a_method_reports_its_class_not_the_method_above(repo: _Repo) -> None:
    base = textwrap.dedent("""\
        class WidgetLedger:
            def opening_balance(self):
                return 0

            def closing_balance(self):
                return 1
        """)
    repo.commit({"pkg/ledger.py": base}, "base")
    repo.start_change()
    changed = base.split("\n\n    def closing_balance")[0] + "\n"
    assert "closing_balance" not in changed
    repo.commit({"pkg/ledger.py": changed}, "remove a method")

    names = names_from_diff(repo.path, "base")

    assert {"WidgetLedger", "closing_balance"} <= set(names), names
    assert "opening_balance" not in names, names


def test_changed_method_reports_the_method_and_its_class(repo: _Repo) -> None:
    base = """\
        class WidgetLedger:
            def opening_balance(self):
                return 0

            def closing_balance(self):
                return 1
        """
    repo.commit({"pkg/ledger.py": base}, "base")
    repo.start_change()
    repo.commit({"pkg/ledger.py": base.replace("return 1", "return 2")}, "change a method")

    names = names_from_diff(repo.path, "base")

    assert {"WidgetLedger", "closing_balance"} <= set(names), names
    assert "opening_balance" not in names


def test_shell_function_body_change_reports_only_that_function(repo: _Repo) -> None:
    base = textwrap.dedent("""\
        #!/usr/bin/env bash
        stage_widgets() {
          echo staging
        }

        publish_widgets() {
          echo publishing
        }

        echo top-level
        """)
    repo.commit({"scripts/widgets.sh": base}, "base")
    repo.start_change()
    repo.commit({"scripts/widgets.sh": base.replace("echo staging", "echo staging now")
                 .replace("echo top-level", "echo top-level now")}, "change a function")

    names = names_from_diff(repo.path, "base")

    assert "stage_widgets" in names
    assert "publish_widgets" not in names, (
        f"a top-level change after a function was attributed to it: {names}"
    )
    assert "widgets.sh" in names


def test_deleted_script_contributes_its_file_name(repo: _Repo) -> None:
    repo.commit({"scripts/retire-widgets.sh": "echo retire\n", "keep.txt": "x\n"}, "base")
    repo.start_change()
    repo.commit({}, "delete a script", delete=("scripts/retire-widgets.sh",))

    assert "retire-widgets.sh" in names_from_diff(repo.path, "base")


def test_diff_read_from_a_subdirectory_of_the_repository(repo: _Repo) -> None:
    repo.commit({"pkg/widgets.py": _WIDGETS_BASE}, "base")
    repo.start_change()
    repo.commit({"pkg/widgets.py": _WIDGETS_CHANGED}, "change")

    assert "compute_widget_total" in names_from_diff(repo.path / "pkg", "base")


def test_unknown_base_ref_raises_with_a_machine_key(repo: _Repo) -> None:
    repo.commit({"a.txt": "a\n"}, "base")

    with pytest.raises(DiffReadError) as caught:
        names_from_diff(repo.path, "no-such-branch")

    assert caught.value.error == "affected.ref_unknown"
    assert "no-such-branch" in caught.value.message
    assert str(repo.path) in caught.value.remedy


# ---------------------------------------------------------------------------
# A changed test file contributes its own name, not the names it defines
# ---------------------------------------------------------------------------

# A test file defining the private constant and repository-fixture method
# that most of a real test tree also defines for itself.
_HELPER_TEST_FILE = """\
    import pathlib
    import subprocess

    _TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


    class _WidgetRepo:
        def git(self, *args):
            return subprocess.run(["git", *args], check=True)


    def test_team_dir_exists():
        assert _TEAM_DIR.is_dir()
    """

# An unrelated test that uses both names for its own definitions.
_UNRELATED_HELPER_USER = """\
    import pathlib

    _TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


    def test_ledger_repo(ledger_repo):
        ledger_repo.git("status")
        assert (_TEAM_DIR / "ledger").exists()
    """


def test_new_test_file_selects_itself_but_not_tests_sharing_its_helper_names(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({"pkg/widgets.py": _WIDGETS_BASE}, "base")
    repo.start_change()
    repo.commit({"tests/unit/test_widget_team_dir.py": _HELPER_TEST_FILE}, "add a test file")
    tests = _tests_root(tmp_path, {
        "unit/test_widget_team_dir.py": _HELPER_TEST_FILE,
        "unit/test_ledger_repo.py": _UNRELATED_HELPER_USER,
    })

    names = names_from_diff(repo.path, "base")
    selection = affected_tests(names, tests, 60)

    # names_from_diff keeps generic names, so their absence here is the
    # test-file rule at work, not the generic-name filter.
    assert "_TEAM_DIR" not in names, names
    assert "git" not in names, names
    assert names == ["test_widget_team_dir"], names
    assert _relative(selection, tests) == ["unit/test_widget_team_dir.py"], (
        "a name defined only inside the changed test file selected another test"
    )
    assert selection.matched_count == 1


def test_editing_a_test_file_fixture_method_contributes_only_the_file_name(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({"tests/unit/test_widget_team_dir.py": _HELPER_TEST_FILE}, "base")
    repo.start_change()
    edited = (_HELPER_TEST_FILE
              .replace('["git", *args], check=True', '["git", "-C", ".", *args], check=True')
              .replace("parents[2]", "parents[1]"))
    assert edited != _HELPER_TEST_FILE
    repo.commit({"tests/unit/test_widget_team_dir.py": edited}, "edit the fixture method")
    tests = _tests_root(tmp_path, {
        "unit/test_widget_team_dir.py": edited,
        "unit/test_ledger_repo.py": _UNRELATED_HELPER_USER,
    })

    names = names_from_diff(repo.path, "base")
    selection = affected_tests(names, tests, 60)

    assert names == ["test_widget_team_dir"], (
        f"the edited method, its class or the edited constant was reported: {names}"
    )
    assert _relative(selection, tests) == ["unit/test_widget_team_dir.py"]


def test_shared_test_helpers_still_contribute_their_changed_definitions(
    repo: _Repo,
) -> None:
    base = {
        "tests/conftest.py": "def widget_ledger_fixture():\n    return []\n",
        "tests/fixtures/widget_repo.py": "def build_widget_repo(path):\n    return path\n",
        "tests/unit/fixtures/test_seeded_widget.py": "SEEDED_WIDGET_COUNT = 1\n",
    }
    repo.commit(base, "base")
    repo.start_change()
    repo.commit({
        "tests/conftest.py": "def widget_ledger_fixture():\n    return [1]\n",
        "tests/fixtures/widget_repo.py": "def build_widget_repo(path):\n    return str(path)\n",
        "tests/unit/fixtures/test_seeded_widget.py": "SEEDED_WIDGET_COUNT = 2\n",
    }, "change shared helpers")

    names = names_from_diff(repo.path, "base")

    # conftest.py, helper modules and test_*.py fixture data under fixtures/
    # are not test files: a change to them selects the tests that call them.
    assert {"widget_ledger_fixture", "build_widget_repo", "SEEDED_WIDGET_COUNT"} <= set(names), (
        names
    )


def test_production_change_beside_a_test_file_change_keeps_its_names(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({"pkg/widgets.py": _WIDGETS_BASE}, "base")
    repo.start_change()
    repo.commit({
        "pkg/widgets.py": _WIDGETS_CHANGED,
        "tests/unit/test_widget_team_dir.py": _HELPER_TEST_FILE,
    }, "change a function and add a test file")
    tests = _tests_root(tmp_path, {
        "unit/test_widget_team_dir.py": _HELPER_TEST_FILE,
        "unit/test_ledger_repo.py": _UNRELATED_HELPER_USER,
        "unit/test_totals.py": "def test_total():\n    assert compute_widget_total([1]) == 1\n",
    })

    names = names_from_diff(repo.path, "base")
    selection = affected_tests(names, tests, 60)

    assert {"compute_widget_total", "widgets", "test_widget_team_dir"} <= set(names), names
    assert sorted(_relative(selection, tests)) == [
        "unit/test_totals.py", "unit/test_widget_team_dir.py",
    ]


# ---------------------------------------------------------------------------
# Docstring and comment lines contribute no definition names
# ---------------------------------------------------------------------------

_DOCUMENTED_MODULE = '''\
    """Widget ledger helpers.

    The ledger keeps one row per widget.
    """


    def compute_widget_total(items):
        return sum(items)
    '''


def test_docstring_line_starting_with_class_contributes_no_name(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({"pkg/widget_ledger.py": _DOCUMENTED_MODULE}, "base")
    repo.start_change()
    repo.commit({"pkg/widget_ledger.py": _DOCUMENTED_MODULE.replace(
        "one row per widget.\n",
        "one row per widget.\n    class whose members the ledger reads are listed below.\n",
    )}, "reword the module docstring")
    tests = _tests_root(tmp_path, {
        "unit/test_widget_ledger.py": "from pkg import widget_ledger\n",
        "unit/test_prose.py": "# whose turn is it\nwhose = 1\n",
    })

    names = names_from_diff(repo.path, "base")
    searched, _dropped = split_generic_names(names)
    selection = affected_tests(names, tests, 60)

    assert "whose" not in names, f"a docstring line was read as a class definition: {names}"
    assert searched == ["widget_ledger"], searched
    assert _relative(selection, tests) == ["unit/test_widget_ledger.py"]


def test_definition_shaped_lines_inside_a_docstring_contribute_no_names(
    repo: _Repo,
) -> None:
    repo.commit({"pkg/widget_ledger.py": _DOCUMENTED_MODULE}, "base")
    repo.start_change()
    example = (
        "one row per widget.\n"
        "class LedgerExample(Base):\n"
        "    def example_rollup(self, rows):\n"
        "example_limit = 3\n"
    )
    repo.commit({"pkg/widget_ledger.py": _DOCUMENTED_MODULE.replace(
        "one row per widget.\n", textwrap.indent(example, "    ")[4:],
    )}, "add a code example to the docstring")

    names = names_from_diff(repo.path, "base")

    assert not {"LedgerExample", "example_rollup", "example_limit"} & set(names), (
        f"a line inside a docstring contributed a definition name: {names}"
    )
    assert names == ["widget_ledger"], names


def test_removed_docstring_line_contributes_no_name(repo: _Repo) -> None:
    with_example = _DOCUMENTED_MODULE.replace(
        "one row per widget.\n", "one row per widget.\n    class RetiredExample:\n",
    )
    repo.commit({"pkg/widget_ledger.py": with_example}, "base")
    repo.start_change()
    repo.commit({"pkg/widget_ledger.py": _DOCUMENTED_MODULE}, "drop the docstring example")

    names = names_from_diff(repo.path, "base")

    assert "RetiredExample" not in names, (
        f"a removed docstring line was read as a class definition: {names}"
    )
    assert names == ["widget_ledger"], names


def test_comment_lines_starting_with_class_or_def_contribute_no_names(
    repo: _Repo,
) -> None:
    repo.commit({"pkg/widget_ledger.py": _DOCUMENTED_MODULE}, "base")
    repo.start_change()
    repo.commit({"pkg/widget_ledger.py": _DOCUMENTED_MODULE + (
        "\n\n    # class whose members the ledger reads are listed below.\n"
        "    # def retired_rollup(rows):\n"
    )}, "add comments")

    names = names_from_diff(repo.path, "base")

    assert not {"whose", "retired_rollup"} & set(names), (
        f"a comment line was read as a definition: {names}"
    )
    assert names == ["widget_ledger"], names


def test_unparseable_module_still_needs_definition_shape(repo: _Repo) -> None:
    # A file that does not parse cannot be masked by string position, so the
    # definition shape alone separates "class whose ..." from a real class.
    broken = "def half_written(:\n"
    repo.commit({"pkg/widget_ledger.py": broken}, "base")
    repo.start_change()
    repo.commit({"pkg/widget_ledger.py": broken + (
        "class whose members are listed below.\n"
        "class LedgerRollup(Base):\n"
        "def ledger_rollup_total(rows):\n"
    )}, "add lines to an unparseable module")

    names = names_from_diff(repo.path, "base")

    assert "whose" not in names, names
    assert {"LedgerRollup", "ledger_rollup_total"} <= set(names), names


# ---------------------------------------------------------------------------
# Common-word function names select nothing on their own
# ---------------------------------------------------------------------------


def test_change_inside_a_function_named_exists_selects_only_the_module_tests(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    base = textwrap.dedent("""\
        def plan_widget_pane(root, name):
            def exists(name):
                return bool(name) and (root / name).is_dir()

            return exists(name)
        """)
    repo.commit({"pkg/widget_pane.py": base}, "base")
    repo.start_change()
    repo.commit({"pkg/widget_pane.py": base.replace(
        "(root / name).is_dir()", "(root / name).is_dir()  # project directory",
    )}, "change a line inside exists")
    tests = _tests_root(tmp_path, {
        "unit/test_widget_pane.py": "from pkg import widget_pane\n",
        "unit/test_paths.py": "def test_tmp(tmp_path):\n    assert tmp_path.exists()\n",
        "unit/test_ledger_files.py": "assert (root / 'ledger').exists()\n",
    })

    names = names_from_diff(repo.path, "base")
    searched, dropped = split_generic_names(names)
    selection = affected_tests(names, tests, 60)

    # names_from_diff keeps the name; the selection drops it as generic.
    assert "exists" in names and "exists" in dropped, (names, dropped)
    assert searched == ["plan_widget_pane", "widget_pane"], searched
    assert _relative(selection, tests) == ["unit/test_widget_pane.py"], (
        "a change inside a function named exists selected tests on that word alone"
    )


@pytest.mark.parametrize("word", ["exists", "line", "lines", "has", "module", "function"])
def test_common_word_function_names_are_generic_and_select_nothing(
    word: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_common_words.py": f"""\
            def test_word(item):
                assert item.{word} is not None
                {word} = 1
            """,
    })

    searched, dropped = split_generic_names([word, "ledger_store"])
    selection = affected_tests([word], tests, 60)

    assert dropped == [word] and searched == ["ledger_store"]
    assert list(selection) == [] and selection.matched_count == 0, (
        f"the common word {word!r} selected a test on its own"
    )


# ---------------------------------------------------------------------------
# Selection rule on a fixed tree
# ---------------------------------------------------------------------------


def test_changed_test_file_is_selected_by_its_own_name(tmp_path: pathlib.Path) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_widget_rounding.py": "def test_rounds():\n    assert round(1.5) == 2\n",
        "unit/test_other.py": "def test_other():\n    pass\n",
    })

    selection = affected_tests(["test_widget_rounding"], tests, 60)

    assert _relative(selection, tests) == ["unit/test_widget_rounding.py"]


def test_name_match_respects_word_and_hyphen_boundaries(tmp_path: pathlib.Path) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_dotted.py": "from pkg.lib import semver\nsemver.py\n",
        "unit/test_longer.py": "semver_lt(a, b)\npysemver\nsemver-tool\n",
    })

    selection = affected_tests(["semver"], tests, 60)

    assert _relative(selection, tests) == ["unit/test_dotted.py"]


def test_generic_names_are_dropped_and_select_nothing(tmp_path: pathlib.Path) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_entry.py": "def main():\n    run()\n# __init__ ab\n",
    })

    searched, dropped = split_generic_names(["main", "run", "__init__", "ab", " ", "ledger_store"])
    selection = affected_tests(["main", "run", "__init__", "ab"], tests, 60)

    assert searched == ["ledger_store"]
    assert dropped == ["__init__", "ab", "main", "run"]
    assert list(selection) == [] and selection.matched_count == 0


@pytest.mark.parametrize("member", ["failed", "passed", "skipped", "say", "git", "commit"])
def test_ubiquitous_member_names_are_generic_and_select_nothing(
    member: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_report_counts.py": f"""\
            def test_counts(report, repo):
                assert report.{member} is not None
                repo.{member}("x")
                {member} = 1
            """,
    })

    searched, dropped = split_generic_names([member, "ledger_store"])
    selection = affected_tests([member], tests, 60)

    assert dropped == [member] and searched == ["ledger_store"]
    assert list(selection) == [] and selection.matched_count == 0, (
        f"the ubiquitous member name {member!r} selected a test on its own"
    )


# ---------------------------------------------------------------------------
# Tests that list a changed file's directory
# ---------------------------------------------------------------------------

# A help test in the shape of one that finds its subjects by listing the
# dashboard directory: it never names a script it checks.
_LISTS_DASHBOARD_DIR = """\
    import pathlib

    _TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
    _DASHBOARD_DIR = _TEAM_DIR / "scripts" / "dashboard"


    def _discover():
        return sorted(p for p in _DASHBOARD_DIR.glob("*.sh") if p.is_file())


    def test_every_script_answers_help():
        assert all(p.suffix == ".sh" for p in _discover())
    """


def test_script_change_selects_a_test_listing_its_directory_but_not_another_directory(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({
        "team/scripts/dashboard/attention.sh": "#!/usr/bin/env bash\necho attention\n",
        "team/scripts/ledger/rollup.sh": "#!/usr/bin/env bash\necho rollup\n",
    }, "base")
    tests = _tests_root(tmp_path, {"unit/test_dashboard_help.py": _LISTS_DASHBOARD_DIR})
    assert "attention" not in (tests / "unit" / "test_dashboard_help.py").read_text()

    repo.start_change()
    repo.commit({
        "team/scripts/dashboard/attention.sh": "#!/usr/bin/env bash\necho attention pane\n",
    }, "change a dashboard script")
    dashboard_names = names_from_diff(repo.path, "base")
    dashboard_selection = affected_tests(dashboard_names, tests, 60)

    repo.git("checkout", "-q", "-b", "ledger-change", "base")
    repo.commit({
        "team/scripts/ledger/rollup.sh": "#!/usr/bin/env bash\necho rolling up\n",
    }, "change a script in another directory")
    ledger_names = names_from_diff(repo.path, "base")
    ledger_selection = affected_tests(ledger_names, tests, 60)

    assert dashboard_names == ["attention.sh", "team/scripts/dashboard/"], dashboard_names
    assert _relative(dashboard_selection, tests) == ["unit/test_dashboard_help.py"], (
        "a change to a script in a directory the test lists did not select it"
    )
    assert ledger_names == ["rollup.sh", "team/scripts/ledger/"], ledger_names
    assert list(ledger_selection) == [] and ledger_selection.matched_count == 0, (
        f"a change in an unrelated directory selected the dashboard test: {ledger_selection}"
    )


def test_top_level_files_and_test_files_contribute_no_directory(repo: _Repo) -> None:
    repo.commit({
        "install.sh": "echo install\n",
        "docs/widget-guide.md": "# Widgets\n",
        "team/tests/unit/test_widget_rounding.py": "def test_rounds():\n    pass\n",
    }, "base")
    repo.start_change()
    repo.commit({
        "install.sh": "echo installing\n",
        "docs/widget-guide.md": "# Widget guide\n",
        "team/tests/unit/test_widget_rounding.py": "def test_rounds():\n    assert True\n",
    }, "change a root file, a top-level doc and a test file")

    names = names_from_diff(repo.path, "base")

    assert names == ["install.sh", "test_widget_rounding", "widget-guide.md"], names


@pytest.mark.parametrize("listing", [
    pytest.param(
        '_DASHBOARD_DIR = _TEAM_DIR / "scripts" / "dashboard"\n'
        'SCRIPTS = sorted(_DASHBOARD_DIR.glob("*.sh"))\n',
        id="pathlib-join-assigned-then-glob",
    ),
    pytest.param(
        'SCRIPTS = sorted((REPO / "team/scripts/dashboard").iterdir())\n',
        id="one-string-listed-on-the-same-line",
    ),
    pytest.param(
        'DASH = os.path.join(ROOT, "scripts", "dashboard")\n'
        'SCRIPTS = os.listdir(DASH)\n',
        id="os-path-join-assigned-then-listdir",
    ),
    pytest.param(
        '_PANES = _SCRIPTS_DIR / "dashboard"\n'
        'SCRIPTS = [p for p in _PANES.rglob("*.sh")]\n',
        id="parent-held-in-a-variable",
    ),
    pytest.param(
        'SCRIPTS = glob.glob("team/scripts/dashboard/*.sh")\n',
        id="glob-pattern-inside-the-directory",
    ),
])
def test_each_way_of_listing_the_directory_selects_the_test(
    listing: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {"unit/test_panes.py": listing})

    selection = affected_tests(["team/scripts/dashboard/"], tests, 60)

    assert _relative(selection, tests) == ["unit/test_panes.py"], (
        f"a test listing the directory this way was not selected:\n{listing}"
    )


@pytest.mark.parametrize("citation", [
    pytest.param(
        'sys.path.insert(0, str(_TEAM_DIR / "scripts" / "dashboard"))\n'
        'LOGS = sorted(tmp_path.glob("*.log"))\n',
        id="cites-the-directory-but-lists-another",
    ),
    pytest.param(
        '_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"\n'
        'LOGS = sorted(_KILL_SH.parent.parent.glob("*.log"))\n',
        id="names-a-file-inside-the-directory",
    ),
    pytest.param(
        '_LIB = _TEAM_DIR / "scripts" / "dashboard" / "lib"\n'
        'LIBS = sorted(_LIB.glob("*.sh"))\n',
        id="lists-a-subdirectory",
    ),
    pytest.param(
        '_OLD = _TEAM_DIR / "scripts" / "dashboard_old"\n'
        'OLD = sorted(_OLD.glob("*.sh"))\n',
        id="lists-a-longer-named-sibling",
    ),
])
def test_citing_the_directory_without_listing_it_selects_nothing(
    citation: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {"unit/test_panes.py": citation})

    selection = affected_tests(["team/scripts/dashboard/"], tests, 60)

    assert list(selection) == [] and selection.matched_count == 0, (
        f"a test that does not list team/scripts/dashboard/ was selected:\n{citation}"
    )


# ---------------------------------------------------------------------------
# Tests that list a changed directory through a recursive walk of an
# ancestor, or assign it across several lines
# ---------------------------------------------------------------------------

# A census in the shape of one that walks every script below team/scripts:
# it covers each dashboard script without citing the dashboard directory.
_WALKS_SCRIPTS_RECURSIVELY = """\
    import pathlib

    ROOT = pathlib.Path(__file__).resolve().parents[2]
    _SCRIPTS = ROOT / "scripts"


    def test_every_script_has_a_shebang():
        scripts = sorted(_SCRIPTS.rglob("*.sh"))
        assert all(p.read_text().startswith("#!") for p in scripts)
    """

# The same census over team/scripts' own entries only: it never sees a
# script in a subdirectory.
_GLOBS_SCRIPTS_TOP_LEVEL = """\
    import pathlib

    ROOT = pathlib.Path(__file__).resolve().parents[2]
    _SCRIPTS = ROOT / "scripts"


    def test_every_top_level_script_has_a_shebang():
        scripts = sorted(_SCRIPTS.glob("*.sh"))
        assert all(p.read_text().startswith("#!") for p in scripts)
    """


def test_subdirectory_change_selects_a_recursive_walk_of_an_ancestor_but_not_a_flat_glob(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({
        "team/scripts/dashboard/attention.sh": "#!/usr/bin/env bash\necho attention\n",
        "team/scripts/deploy.sh": "#!/usr/bin/env bash\necho deploy\n",
    }, "base")
    tests = _tests_root(tmp_path, {
        "unit/test_script_census.py": _WALKS_SCRIPTS_RECURSIVELY,
        "unit/test_top_level_scripts.py": _GLOBS_SCRIPTS_TOP_LEVEL,
    })

    repo.start_change()
    repo.commit({
        "team/scripts/dashboard/attention.sh": "#!/usr/bin/env bash\necho attention pane\n",
    }, "change a dashboard script")
    dashboard_names = names_from_diff(repo.path, "base")
    dashboard_selection = affected_tests(dashboard_names, tests, 60)

    repo.git("checkout", "-q", "-b", "top-level-change", "base")
    repo.commit({
        "team/scripts/deploy.sh": "#!/usr/bin/env bash\necho deploying\n",
    }, "change a script directly in team/scripts")
    top_level_names = names_from_diff(repo.path, "base")
    top_level_selection = affected_tests(top_level_names, tests, 60)

    assert dashboard_names == ["attention.sh", "team/scripts/dashboard/"], dashboard_names
    assert _relative(dashboard_selection, tests) == ["unit/test_script_census.py"], (
        "a dashboard change must select the test walking team/scripts recursively "
        "and not the one globbing only team/scripts' own entries"
    )
    # Both tests do list team/scripts, so the flat glob is left out above
    # for not reaching the subdirectory, not for failing to cite the tree.
    assert top_level_names == ["deploy.sh", "team/scripts/"], top_level_names
    assert _relative(top_level_selection, tests) == [
        "unit/test_script_census.py", "unit/test_top_level_scripts.py",
    ]


@pytest.mark.parametrize("listing", [
    pytest.param(
        '_SCRIPTS = _TEAM_DIR / "scripts"\n'
        'FOUND = sorted(_SCRIPTS.rglob("*.sh"))\n',
        id="parent-held-in-a-variable-then-rglob",
    ),
    pytest.param(
        'scripts_dir = root / "team" / "scripts"\n'
        'for sh_file in scripts_dir.rglob("*.sh"):\n'
        '    pass\n',
        id="repository-anchored-join-then-rglob",
    ),
    pytest.param(
        'for top, _dirs, files in os.walk(os.path.join(ROOT, "team", "scripts")):\n'
        '    pass\n',
        id="os-walk-on-the-citing-line",
    ),
    pytest.param(
        '_SCRIPTS_DIR = _TEAM_DIR / "scripts"\n'
        'FOUND = sorted(_SCRIPTS_DIR.glob("**/*.sh"))\n',
        id="glob-with-a-double-star-pattern",
    ),
    pytest.param(
        'FOUND = glob.glob("team/scripts/**/*.sh", recursive=True)\n',
        id="recursive-glob-pattern-inside-the-ancestor",
    ),
    pytest.param(
        'SCRIPTS = os.path.join(REPO, "team", "scripts")\n'
        'FOUND = [f for _top, _dirs, f in os.walk(SCRIPTS)]\n',
        id="os-path-join-assigned-then-os-walk",
    ),
])
def test_each_recursive_listing_of_an_ancestor_selects_the_test(
    listing: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {"unit/test_walk.py": listing})

    selection = affected_tests(["team/scripts/dashboard/"], tests, 60)

    assert _relative(selection, tests) == ["unit/test_walk.py"], (
        f"a test walking team/scripts recursively this way was not selected:\n{listing}"
    )


@pytest.mark.parametrize("listing", [
    pytest.param(
        '_SCRIPTS = _TEAM_DIR / "scripts"\n'
        'FOUND = sorted(_SCRIPTS.glob("*.sh"))\n',
        id="flat-glob-of-the-ancestor",
    ),
    pytest.param(
        'FOUND = sorted((REPO / "team/scripts").iterdir())\n',
        id="iterdir-of-the-ancestor",
    ),
    pytest.param(
        'SCRIPTS = os.path.join(ROOT, "team", "scripts")\n'
        'FOUND = os.listdir(SCRIPTS) + list(os.scandir(SCRIPTS))\n',
        id="listdir-and-scandir-of-the-ancestor",
    ),
    pytest.param(
        '_SCRIPTS = _TEAM_DIR / "scripts"\n'
        'LOGS = sorted(tmp_path.rglob("*.log"))\n',
        id="cites-the-ancestor-but-walks-another-tree",
    ),
    pytest.param(
        '_OLD = _TEAM_DIR / "scripts_old"\n'
        'FOUND = sorted(_OLD.rglob("*.sh"))\n',
        id="walks-a-longer-named-sibling-of-the-ancestor",
    ),
    pytest.param(
        '_LIB = _TEAM_DIR / "scripts" / "dashboard" / "lib"\n'
        'FOUND = sorted(_LIB.rglob("*.sh"))\n',
        id="walks-a-subdirectory-of-the-changed-directory",
    ),
    pytest.param(
        'FOUND = sorted((ROOT / "team").rglob("*.sh"))\n',
        id="walks-a-whole-top-level-directory",
    ),
])
def test_an_ancestor_listing_that_cannot_reach_the_changed_directory_selects_nothing(
    listing: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {"unit/test_walk.py": listing})

    selection = affected_tests(["team/scripts/dashboard/"], tests, 60)

    assert list(selection) == [] and selection.matched_count == 0, (
        f"a test that does not list team/scripts/dashboard/ was selected:\n{listing}"
    )


def test_every_ancestor_down_to_two_components_is_walked_for_a_nested_change(
    tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_walks_dashboard.py": (
            '_PANES = _TEAM_DIR / "scripts" / "dashboard"\n'
            'FOUND = sorted(_PANES.rglob("*.sh"))\n'
        ),
        "unit/test_walks_scripts.py": (
            'FOUND = sorted((_TEAM_DIR / "scripts").rglob("*.sh"))\n'
        ),
    })

    selection = affected_tests(["team/scripts/dashboard/lib/"], tests, 60)

    assert ancestor_directories("team/scripts/dashboard/lib/") == [
        "team/scripts/dashboard/", "team/scripts/",
    ]
    assert ancestor_directories("team/scripts/") == []
    assert _relative(selection, tests) == [
        "unit/test_walks_dashboard.py", "unit/test_walks_scripts.py",
    ]


@pytest.mark.parametrize("listing", [
    pytest.param(
        '_DIR = (\n'
        '    ROOT / "scripts" / "dashboard"\n'
        ')\n'
        'SCRIPTS = sorted(_DIR.glob("*.sh"))\n',
        id="parenthesized-assignment-then-glob",
    ),
    pytest.param(
        '_DIR: pathlib.Path = (\n'
        '    pathlib.Path(__file__).parents[2] / "scripts" / "dashboard"\n'
        ')\n'
        'SCRIPTS = list(_DIR.glob("*.sh"))\n',
        id="annotated-parenthesized-assignment",
    ),
    pytest.param(
        '_DIR = \\\n'
        '    ROOT / "scripts" / "dashboard"\n'
        'SCRIPTS = sorted(_DIR.glob("*.sh"))\n',
        id="backslash-continued-assignment",
    ),
    pytest.param(
        '_SCRIPTS = (\n'
        '    _TEAM_DIR / "scripts"\n'
        ')\n'
        'FOUND = sorted(_SCRIPTS.rglob("*.sh"))\n',
        id="parenthesized-ancestor-then-rglob",
    ),
])
def test_directory_assigned_across_several_lines_then_listed_selects_the_test(
    listing: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {"unit/test_panes.py": listing})

    selection = affected_tests(["team/scripts/dashboard/"], tests, 60)

    assert _relative(selection, tests) == ["unit/test_panes.py"], (
        f"a directory assigned across several lines and listed was not selected:\n{listing}"
    )


@pytest.mark.parametrize("citation", [
    pytest.param(
        '_DIR = (\n'
        '    ROOT / "scripts" / "dashboard"\n'
        ')\n'
        'LOGS = sorted(tmp_path.glob("*.log"))\n',
        id="assigned-across-lines-but-never-listed",
    ),
    pytest.param(
        'sys.path.insert(\n'
        '    0, str(ROOT / "scripts" / "dashboard")\n'
        ')\n'
        'LOGS = sorted(tmp_path.glob("*.log"))\n',
        id="multi-line-call-that-assigns-nothing",
    ),
])
def test_multi_line_citation_that_assigns_no_listed_name_selects_nothing(
    citation: str, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {"unit/test_panes.py": citation})

    selection = affected_tests(["team/scripts/dashboard/"], tests, 60)

    assert list(selection) == [] and selection.matched_count == 0, (
        f"a test that does not list team/scripts/dashboard/ was selected:\n{citation}"
    )


def test_a_file_that_does_not_tokenize_still_lists_by_single_line_assignment(
    tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_broken.py": (
            '_D = _TEAM_DIR / "scripts" / "dashboard"\n'
            'print(_TEAM_DIR / "scripts" / "dashboard")\n'
            'FOUND = sorted(_D.glob("*.sh"))\n'
            'unterminated = (\n'
        ),
    })

    selection = affected_tests(["team/scripts/dashboard/"], tests, 60)

    assert _relative(selection, tests) == ["unit/test_broken.py"]


def test_an_ancestor_walk_ranks_as_a_directory_listing_below_name_matches(
    tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_alpha_walks_scripts.py": (
            '_SCRIPTS = _TEAM_DIR / "scripts"\n'
            'A = sorted(_SCRIPTS.rglob("*.sh"))\n'
            'B = [f for _t, _d, f in os.walk(_SCRIPTS)]\n'
        ),
        "unit/test_zulu_names_the_script.py": 'run(["attention.sh", "--help"])\n',
    })

    names = ["attention.sh", "team/scripts/dashboard/"]
    full = affected_tests(names, tests, None)
    capped = affected_tests(names, tests, 1)

    assert _relative(full, tests) == [
        "unit/test_zulu_names_the_script.py", "unit/test_alpha_walks_scripts.py",
    ]
    assert _relative(capped, tests) == ["unit/test_zulu_names_the_script.py"]
    assert capped.matched_count == 2 and capped.cap_applied


def test_broad_directories_are_generic_and_select_nothing(tmp_path: pathlib.Path) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_tree_census.py": (
            'ROOT = pathlib.Path(".")\n'
            'EVERYTHING = sorted(ROOT.rglob("*"))\n'
            'TEAM = sorted((ROOT / "team").rglob("*.sh"))\n'
        ),
    })

    searched, dropped = split_generic_names(
        ["/", "team/", "./docs/", "./team//scripts/dashboard/"],
    )
    selection = affected_tests(["/", "team/", "./docs/"], tests, 60)

    assert searched == ["team/scripts/dashboard/"], searched
    assert dropped == ["./docs/", "/", "team/"], dropped
    assert list(selection) == [] and selection.matched_count == 0


def test_name_citations_rank_above_directory_listings_and_the_cap_drops_listings_first(
    tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        # Lists the directory twice and names nothing: still ranks last.
        "unit/test_alpha_lists_twice.py": (
            '_D = _TEAM_DIR / "scripts" / "dashboard"\n'
            'A = sorted(_D.glob("*.sh"))\n'
            'B = sorted((_TEAM_DIR / "scripts" / "dashboard").iterdir())\n'
        ),
        "unit/test_bravo_lists_once.py": (
            'B = sorted((_TEAM_DIR / "scripts" / "dashboard").iterdir())\n'
        ),
        "unit/test_zulu_names_the_script.py": 'run(["attention.sh", "--help"])\n',
    })

    names = ["attention.sh", "team/scripts/dashboard/"]
    full = affected_tests(names, tests, None)
    capped = affected_tests(names, tests, 2)

    assert _relative(full, tests) == [
        "unit/test_zulu_names_the_script.py",
        "unit/test_alpha_lists_twice.py",
        "unit/test_bravo_lists_once.py",
    ]
    assert _relative(capped, tests) == [
        "unit/test_zulu_names_the_script.py", "unit/test_alpha_lists_twice.py",
    ]
    assert capped.matched_count == 3 and capped.cap_applied


def test_cap_keeps_the_most_referencing_files_in_a_deterministic_order(
    tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        # Two distinct names cited: ranks above every single-name file.
        "unit/test_zeta_both.py": "alpha_widget()\nbeta_widget()\n",
        # One name, three occurrences.
        "unit/test_yankee_three.py": "alpha_widget()\nalpha_widget()\nalpha_widget()\n",
        # One name, one occurrence each: a three-way tie broken by path.
        "unit/test_charlie_one.py": "beta_widget()\n",
        "integration/test_alpha_one.py": "alpha_widget()\n",
        "unit/test_bravo_one.py": "alpha_widget()\n",
    })

    first = affected_tests(["alpha_widget", "beta_widget"], tests, 3)
    second = affected_tests(["beta_widget", "alpha_widget"], tests, 3)

    assert isinstance(first, AffectedTests) and isinstance(first, list)
    assert _relative(first, tests) == [
        "unit/test_zeta_both.py",
        "unit/test_yankee_three.py",
        "integration/test_alpha_one.py",
    ]
    assert list(first) == list(second), "selection order depended on input order"
    assert first.matched_count == 5
    assert first.cap == 3
    assert first.cap_applied


def test_selection_under_the_cap_reports_the_cap_unused(tmp_path: pathlib.Path) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_one.py": "alpha_widget()\n",
        "unit/test_two.py": "alpha_widget()\n",
    })

    exact = affected_tests(["alpha_widget"], tests, 2)
    uncapped = affected_tests(["alpha_widget"], tests, None)

    assert len(exact) == 2 and not exact.cap_applied
    assert len(uncapped) == 2 and not uncapped.cap_applied and uncapped.cap is None


def test_non_test_files_under_the_tests_root_are_never_returned(
    tmp_path: pathlib.Path,
) -> None:
    citing = "alpha_widget()\n"
    tests = _tests_root(tmp_path, {
        "conftest.py": citing,
        "unit/conftest.py": citing,
        "unit/helpers.py": citing,
        "unit/fixture_alpha.py": citing,
        "fixtures/test_seeded_case.py": citing,
        "fixtures/nested/deeper/test_seeded_case.py": citing,
        "unit/fixtures/test_seeded_case.py": citing,
        "artifacts/test_captured.py": citing,
        ".cache/test_cached.py": citing,
        "unit/test_notes.md": citing,
        "unit/test_real.py": citing,
        "unit/widget_test.py": citing,
    })

    selection = affected_tests(["alpha_widget"], tests, 60)

    assert sorted(_relative(selection, tests)) == ["unit/test_real.py", "unit/widget_test.py"]


def test_returned_paths_are_joined_to_the_tests_root_as_given(tmp_path: pathlib.Path) -> None:
    tests = _tests_root(tmp_path, {"unit/test_one.py": "alpha_widget()\n"})

    selection = affected_tests(["alpha_widget"], str(tests), 60)

    assert selection == [tests / "unit" / "test_one.py"]


@pytest.mark.parametrize("cap", [0, -1, 2.5, True, "60"])
def test_cap_that_is_not_a_positive_whole_number_is_refused(
    cap: object, tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {"unit/test_one.py": "alpha_widget()\n"})

    with pytest.raises(ValueError, match="cap must be"):
        affected_tests(["alpha_widget"], tests, cap)  # type: ignore[arg-type]


def test_single_string_instead_of_names_is_refused(tmp_path: pathlib.Path) -> None:
    tests = _tests_root(tmp_path, {"unit/test_one.py": "alpha_widget()\n"})

    with pytest.raises(TypeError, match="wrap it in a list"):
        affected_tests("alpha_widget", tests, 60)


def test_missing_tests_root_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(NotADirectoryError, match="tests root"):
        affected_tests(["alpha_widget"], tmp_path / "absent", 60)


# ---------------------------------------------------------------------------
# Command line, run by file path
# ---------------------------------------------------------------------------


def _run_cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "kanban"),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
    })
    assert "PYTHONPATH" not in env
    return subprocess.run(
        [sys.executable, str(_MODULE_PATH), *args], cwd=str(elsewhere), env=env,
        capture_output=True, text=True, timeout=60, check=False,
    )


def test_cli_json_reports_the_capped_selection_in_runner_file_form(
    tmp_path: pathlib.Path,
) -> None:
    tests = _tests_root(tmp_path, {
        "unit/test_one.py": "alpha_widget()\nalpha_widget()\n",
        "unit/test_two.py": "alpha_widget()\n",
    })

    result = _run_cli(tmp_path, "--names", "alpha_widget", "main",
                      "--tests-root", str(tests), "--cap", "1", "--json")

    assert result.returncode == 0, _diagnostic(result)
    report = json.loads(result.stdout)
    assert report["tests"] == ["tests/unit/test_one.py"]
    assert report["matched"] == 2 and report["cap"] == 1 and report["cap_applied"] is True
    assert report["names"] == ["alpha_widget"] and report["generic_names"] == ["main"]


def test_cli_unknown_base_exits_2_with_the_error_contract(
    repo: _Repo, tmp_path: pathlib.Path,
) -> None:
    repo.commit({"a.txt": "a\n"}, "base")

    result = _run_cli(tmp_path, "--base", "no-such-branch", "--repo", str(repo.path))

    assert result.returncode == 2, _diagnostic(result)
    assert "error:   affected.ref_unknown" in result.stderr, _diagnostic(result)
    assert "remedy:" in result.stderr, _diagnostic(result)
    assert "Traceback" not in result.stderr, _diagnostic(result)
