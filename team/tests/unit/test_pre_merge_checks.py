"""
test_pre_merge_checks.py
========================
The pre-merge check run (``pgai_agent_kanban.testing.pre_merge``) runs the
by-path module test, the five ratchet lints in fail-on-regression mode and
the tracked-files lint, the always-run whole-tree guards and the affected
tests for
``git diff <base>...HEAD``. It prints one summary block and exits non-zero
when any check fails.

The scenario tests build a small fixture repository under tmp_path, run the
engine's command line on it by file path and assert on its exit code and
output. The fixture repository is a git repository whose ``team/`` holds:

* copies of the five real ratchet lints and the real tracked-files lint,
  the real env-bootstrap guard,
  the real by-path module test and the few package modules they import, so
  the checks that run are the tree's own checks;
* passing stand-ins for the other three always-run guards
  (``generate_operator_verb_docs.py``, ``dashboard/generate_window_docs.py``,
  ``check_docs_citations.py``), whose real scripts check documents and
  censuses a fixture tree this small does not carry;
* a small ``pgai_agent_kanban`` package: one guarded command-line module,
  one package-only module (the shape the by-path test's allow-list names)
  and a ``utils`` module holding ``add_tax``;
* a bash script carrying one inline-Python heredoc, recorded in the
  fixture's inline-Python allowlist at 1 block / 2 lines;
* ``tests/unit/test_pricing.py``, which asserts ``add_tax``'s behaviour.

Base commits go on ``ai_rc/v1.0.0``, and each scenario's change is
committed on ``ai_feature/fixture-change`` on top. Every engine run uses
``--workers 1``, so a scenario never starts pytest workers beyond the one
process the outer run already budgets for. The fast cases below the
scenarios call the module's helpers directly.

Nothing reads or writes the live install or the dev tree's branches: every
child process runs with an environment rebuilt from an explicit set, and the
kanban root and temp dir are sandboxes under tmp_path.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Sequence

import pytest

from pgai_agent_kanban.testing import pre_merge
from pgai_agent_kanban.testing.pre_merge import (
    InvocationError,
    combine_lane_exits,
    default_base,
    failed_test_files,
)
from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
_ENGINE = pathlib.Path(pre_merge.__file__).resolve()

_BASE_BRANCH = "ai_rc/v1.0.0"
_CHANGE_BRANCH = "ai_feature/fixture-change"

# Real files the fixture repository carries unchanged, relative to the repo
# root: the six lints, the by-path test, and what they import.
_COPIED_FROM_TREE = (
    "team/pyproject.toml",
    "team/pgai_agent_kanban/__init__.py",
    "team/pgai_agent_kanban/lib/__init__.py",
    "team/pgai_agent_kanban/lib/error_contract.py",
    "team/pgai_agent_kanban/lib/operator_verb_manifest.py",
    "team/scripts/lint_inline_python.py",
    "team/scripts/lint_python_duplicates.py",
    "team/scripts/lint_engine_size_dupe.py",
    "team/scripts/lint_api_route_impl.py",
    "team/scripts/lint_echo_pipe_grep_q.py",
    "team/scripts/lint_tracked_ignored.py",
    "team/config/echo-pipe-grep-q-allowlist.txt",
    "team/scripts/lint_env_bootstrap.py",
    "team/tests/unit/__init__.py",
    "team/tests/unit/test_package_modules_run_by_path.py",
    "team/tests/fixtures/__init__.py",
    "team/tests/fixtures/scrubbed_subprocess_env.py",
)

_SELF_LOCATION_GUARD = """\
    if not __package__:
        _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
        if _OWN_PACKAGE_ROOT not in sys.path:
            sys.path.insert(0, _OWN_PACKAGE_ROOT)
    """

_UTILS_BASE = """\
    \"\"\"Price helpers.\"\"\"


    def add_tax(amount):
        return round(amount * 1.10, 2)
    """

# A whole-tree guard the fixture tree cannot carry for real: it reports a
# clean tree, as each real one does on the kanban's own tree.
_GUARD_STAND_IN = """\
    \"\"\"Stand-in whole-tree guard: the fixture tree is clean.\"\"\"
    import sys

    sys.exit(0)
    """

# A guard that finds something: prints its finding and exits 1.
_GUARD_FINDING = """\
    \"\"\"Stand-in whole-tree guard that reports one finding.\"\"\"
    import sys

    print("DRIFT -- docs/DASHBOARD-PANES.md window=1 disagrees with the census")
    sys.exit(1)
    """

# Fixture files written fresh, relative to the repo root.
_AUTHORED = {
    "team/scripts/generate_operator_verb_docs.py": _GUARD_STAND_IN,
    "team/scripts/dashboard/generate_window_docs.py": _GUARD_STAND_IN,
    "team/scripts/check_docs_citations.py": _GUARD_STAND_IN,
    "team/pgai_agent_kanban/utils.py": _UTILS_BASE,
    "team/pgai_agent_kanban/greeting.py": (
        "\"\"\"Print a greeting.\"\"\"\n"
        "import argparse\nimport pathlib\nimport sys\n\n"
        + textwrap.dedent(_SELF_LOCATION_GUARD)
        + textwrap.dedent("""\

            from pgai_agent_kanban.utils import add_tax  # noqa: E402,F401


            def main(argv=None):
                parser = argparse.ArgumentParser(description="Print a greeting.")
                parser.add_argument("name", nargs="?", default="world")
                print("hello", parser.parse_args(argv).name)
                return 0


            if __name__ == "__main__":
                sys.exit(main())
            """)
    ),
    "team/pgai_agent_kanban/ops/__init__.py": "",
    # The by-path test's package-only allow-list names this module, so the
    # fixture package carries one of that shape.
    "team/pgai_agent_kanban/ops/write.py": """\
        \"\"\"Write verbs, served by the package entry point.\"\"\"
        from pgai_agent_kanban.utils import add_tax  # noqa: F401

        if __name__ == "__main__":
            pass
        """,
    "team/scripts/greet.sh": """\
        #!/usr/bin/env bash
        # Greets the name given, through one small inline Python block.
        python3 - "$1" <<'PY'
        import sys
        print("hello", sys.argv[1])
        PY
        """,
    "team/config/inline-python-allowlist.txt": (
        "# fixture allowlist\nteam/scripts/greet.sh\t1\t2\n"
    ),
    "team/config/operator_verbs.manifest": "# fixture manifest: no verbs\n",
    "team/config/api-route-impl-baseline.txt": "shell_rows\t0\n",
    "team/pm-agent/README.md": "fixture: the duplicates lint scans this directory\n",
    ".gitignore": "__pycache__/\n.pytest_cache/\n",
    "team/tests/unit/test_pricing.py": """\
        from pgai_agent_kanban.utils import add_tax


        def test_tax_is_ten_percent():
            assert add_tax(100) == 110.0
        """,
    "team/tests/unit/test_greeting_words.py": """\
        def test_words():
            assert "hello world".split() == ["hello", "world"]
        """,
}


# ---------------------------------------------------------------------------
# Fixture repository
# ---------------------------------------------------------------------------


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _write(root: pathlib.Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


class _FixtureRepo:
    """A throwaway repository shaped like the kanban's own, with the base
    committed on the RC branch and a feature branch checked out."""

    def __init__(self, tmp_path: pathlib.Path, extra_base_files: dict[str, str] | None = None) -> None:
        self.tmp_path = tmp_path
        self.path = tmp_path / "repo"
        self.path.mkdir()
        for relative in _COPIED_FROM_TREE:
            target = self.path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_REPO_ROOT / relative, target)
        _write(self.path, {**_AUTHORED, **(extra_base_files or {})})
        self._record_duplicates_allowlist()
        self.git("init", "-q", "-b", _BASE_BRANCH)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", _CHANGE_BRANCH)

    def _record_duplicates_allowlist(self) -> None:
        # The copied lints may share function bodies with one another; the
        # base records whatever the fixture tree carries, as the real
        # allowlist records the real tree, so the base starts clean.
        done = subprocess.run(
            [sys.executable, str(self.path / "team/scripts/lint_python_duplicates.py"),
             "--emit-allowlist"],
            cwd=str(self.path), capture_output=True, text=True, check=False,
            env=_env(self.tmp_path),
        )
        assert done.returncode == 0, _diagnostic(done)
        _write(self.path, {"team/config/python-duplicates-allowlist.txt": done.stdout})

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.path), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=False,
        )
        assert done.returncode == 0, f"git {' '.join(args)} failed\n{_diagnostic(done)}"
        return done.stdout

    def commit(self, files: dict[str, str], message: str) -> None:
        _write(self.path, files)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run_engine(self, *args: str) -> subprocess.CompletedProcess:
        workdir = self.tmp_path / "elsewhere"
        workdir.mkdir(exist_ok=True)
        return subprocess.run(
            [sys.executable, str(_ENGINE), "--repo", str(self.path), "--workers", "1", *args],
            cwd=str(workdir), env=_env(self.tmp_path), capture_output=True, text=True,
            timeout=300, check=False,
        )


def _env(tmp_path: pathlib.Path) -> dict[str, str]:
    """A child environment rebuilt from an explicit set, with a sandboxed
    kanban root and temp dir under *tmp_path*."""
    kanban_root = tmp_path / "kanban"
    temp_dir = tmp_path / "kanban-tmp"
    kanban_root.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(kanban_root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        },
    )


@pytest.fixture
def fixture_repo(tmp_path: pathlib.Path) -> _FixtureRepo:
    require_binary("git", label="pre-merge check engine")
    return _FixtureRepo(tmp_path)


def _summary_rows(output: str) -> dict[str, tuple[str, str]]:
    """``{check name: (PASS|FAIL, duration)}`` from the summary block."""
    rows = {}
    for line in output.splitlines():
        match = re.match(r"^(\S.*?)\s+(PASS|FAIL)\s+(\d+\.\d)s\b", line)
        if match:
            rows[match.group(1)] = (match.group(2), match.group(3))
    return rows


_GUARD_ROWS = [
    "guard: env-bootstrap", "guard: operator-verb-docs",
    "guard: dashboard-window-docs", "guard: docs-citations",
]

_UNIT_CHECKS = {
    "by-path", "lint: inline-python", "lint: python-duplicates",
    "lint: engine-size", "lint: api-route-impl", "lint: echo-pipe-grep-q", "lint: tracked-ignored", *_GUARD_ROWS,
    "affected tests",
}

_BUDGET_LINE_RE = re.compile(
    r"^guards:  4 always-run guard\(s\) added \d+\.\ds (of|over) the 5\.0s budget", re.M,
)


# ---------------------------------------------------------------------------
# The requirement's scenarios
# ---------------------------------------------------------------------------


def test_heredoc_over_its_inline_python_allowance_fails_naming_file_and_allowance(
    fixture_repo: _FixtureRepo,
) -> None:
    fixture_repo.commit({"team/scripts/greet.sh": """\
        #!/usr/bin/env bash
        # Greets the name given, through one small inline Python block.
        python3 - "$1" <<'PY'
        import sys
        name = sys.argv[1].strip()
        print("hello", name)
        PY
        """}, "grow the heredoc")

    result = fixture_repo.run_engine("--base", _BASE_BRANCH)

    assert result.returncode == 1, _diagnostic(result)
    rows = _summary_rows(result.stdout)
    assert rows["lint: inline-python"][0] == "FAIL", _diagnostic(result)
    assert "team/scripts/greet.sh" in result.stdout
    assert "above its allowlist entry of 1 / 2" in result.stdout, _diagnostic(result)
    # Only the lint that found something failed.
    assert {name for name, (verdict, _) in rows.items() if verdict == "FAIL"} == {
        "lint: inline-python"
    }, _diagnostic(result)
    assert "error:   pre_merge.checks_failed" in result.stderr
    # The remedy re-runs this same engine with the same base, as printed.
    assert f"remedy:  python3 {_ENGINE} --repo {fixture_repo.path}" in result.stderr, result.stderr
    assert f"--base {_BASE_BRANCH}" in result.stderr


def test_package_module_with_main_but_no_self_location_guard_fails_the_by_path_check(
    fixture_repo: _FixtureRepo,
) -> None:
    fixture_repo.commit({"team/pgai_agent_kanban/receipt.py": """\
        \"\"\"Print a receipt line.\"\"\"
        import sys

        from pgai_agent_kanban.utils import add_tax


        def main(argv=None):
            print(add_tax(float((argv or sys.argv[1:] or ["1"])[0])))
            return 0


        if __name__ == "__main__":
            sys.exit(main())
        """}, "add an unguarded command-line module")

    result = fixture_repo.run_engine("--base", _BASE_BRANCH)

    assert result.returncode == 1, _diagnostic(result)
    rows = _summary_rows(result.stdout)
    assert rows["by-path"][0] == "FAIL", _diagnostic(result)
    assert "pgai_agent_kanban.receipt" in result.stdout, _diagnostic(result)
    assert "self-location guard" in result.stdout


def test_untouched_test_asserting_the_old_behaviour_is_selected_and_fails_by_name(
    fixture_repo: _FixtureRepo,
) -> None:
    fixture_repo.commit({"team/pgai_agent_kanban/utils.py": _UTILS_BASE.replace("1.10", "1.20")},
                        "raise the tax rate")
    test_text = (fixture_repo.path / "team/tests/unit/test_pricing.py").read_text(encoding="utf-8")
    # The stale test cites the function by name. The module name it also
    # cites, utils, is one the finder drops as generic, so the function's
    # name alone is what selects it.
    assert "add_tax" in test_text and "utils" in test_text

    result = fixture_repo.run_engine("--base", _BASE_BRANCH)

    assert result.returncode == 1, _diagnostic(result)
    assert "pre-merge:   tests/unit/test_pricing.py" in result.stdout, _diagnostic(result)
    assert "tests/unit/test_greeting_words.py" not in result.stdout, (
        f"a test citing no changed name was selected\n{_diagnostic(result)}"
    )
    rows = _summary_rows(result.stdout)
    assert rows["affected tests"][0] == "FAIL", _diagnostic(result)
    assert "failing test file: tests/unit/test_pricing.py" in result.stdout, _diagnostic(result)


_CAP_FILES = {
    # Ranked by occurrences of the one changed name: rounding (3),
    # pricing (2), beyond-the-cap (1). The last fails if it ever runs.
    "team/tests/unit/test_rounding.py": """\
        from pgai_agent_kanban.utils import add_tax


        def test_rounds_to_cents():
            assert add_tax(1) == 1.1
            assert add_tax(0) == 0
        """,
    "team/tests/unit/test_zz_beyond_cap.py": """\
        def test_runs_only_without_a_cap():
            assert False, "add_tax: this file ranks below the cap and must not run"
        """,
}

_UTILS_COMMENTED = _UTILS_BASE.replace(
    "        return round", "        # Rounded to whole cents.\n        return round",
)


def test_clean_change_passes_every_check_and_the_cap_runs_only_the_most_referencing(
    tmp_path: pathlib.Path,
) -> None:
    require_binary("git", label="pre-merge check engine")
    repo = _FixtureRepo(tmp_path, extra_base_files=_CAP_FILES)
    repo.commit({"team/pgai_agent_kanban/utils.py": _UTILS_COMMENTED}, "comment add_tax")

    result = repo.run_engine("--base", _BASE_BRANCH, "--cap", "2")

    assert result.returncode == 0, _diagnostic(result)
    rows = _summary_rows(result.stdout)
    assert set(rows) == _UNIT_CHECKS, _diagnostic(result)
    assert all(verdict == "PASS" for verdict, _ in rows.values()), _diagnostic(result)
    assert "3 affected test files; cap 2 applied, running the 2 most-referencing" in result.stdout
    selected = re.findall(r"^pre-merge:   (tests/\S+)$", result.stdout, re.M)
    assert selected == ["tests/unit/test_rounding.py", "tests/unit/test_pricing.py"], (
        _diagnostic(result)
    )
    assert f"base:    {_BASE_BRANCH} (given with --base)" in result.stdout
    assert _BUDGET_LINE_RE.search(result.stdout), _diagnostic(result)
    assert "pre-merge: PASS -- all 12 check(s) passed" in result.stdout


def test_without_the_cap_the_beyond_cap_file_runs_and_fails(tmp_path: pathlib.Path) -> None:
    # The negative half of the cap test: the file the cap left out does
    # fail when it runs, so the passing run above proves it was not run.
    require_binary("git", label="pre-merge check engine")
    repo = _FixtureRepo(tmp_path, extra_base_files=_CAP_FILES)
    repo.commit({"team/pgai_agent_kanban/utils.py": _UTILS_COMMENTED}, "comment add_tax")

    result = repo.run_engine("--base", _BASE_BRANCH, "--cap", "0", "--suite", "unit")

    assert result.returncode == 1, _diagnostic(result)
    assert "3 affected test file(s)" in result.stdout
    assert "failing test file: tests/unit/test_zz_beyond_cap.py" in result.stdout, _diagnostic(result)


# ---------------------------------------------------------------------------
# The always-run whole-tree guards
# ---------------------------------------------------------------------------


def test_direct_kanban_root_read_in_a_package_module_fails_the_env_bootstrap_guard(
    fixture_repo: _FixtureRepo,
) -> None:
    # No test cites the new module, so affected selection picks nothing: the
    # guard runs because it always runs, not because the diff named it.
    fixture_repo.commit({"team/pgai_agent_kanban/rootpeek.py": """\
        \"\"\"Report where the kanban root is.\"\"\"
        import os


        def kanban_root():
            return os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
        """}, "read the kanban root directly")

    result = fixture_repo.run_engine("--base", _BASE_BRANCH)

    assert result.returncode == 1, _diagnostic(result)
    rows = _summary_rows(result.stdout)
    assert {name for name, (verdict, _) in rows.items() if verdict == "FAIL"} == {
        "guard: env-bootstrap"
    }, _diagnostic(result)
    assert rows["affected tests"][0] == "PASS", _diagnostic(result)
    # The guard's own finding names the module, and its fix follows it.
    assert "team/pgai_agent_kanban/rootpeek.py" in result.stdout, _diagnostic(result)
    assert "    fix: read the kanban root through resolve_kanban_root()" in result.stdout, (
        _diagnostic(result)
    )
    assert "pre-merge: FAIL -- 1 of 12 check(s) failed (guard: env-bootstrap)" in result.stdout, (
        _diagnostic(result)
    )
    assert "error:   pre_merge.checks_failed" in result.stderr
    assert "guard: env-bootstrap" in result.stderr, result.stderr


def test_a_guard_finding_fails_the_run_and_every_other_check_still_runs(
    fixture_repo: _FixtureRepo,
) -> None:
    fixture_repo.commit(
        {"team/scripts/dashboard/generate_window_docs.py": _GUARD_FINDING}, "a guard with a finding",
    )

    result = fixture_repo.run_engine("--base", _BASE_BRANCH)

    assert result.returncode == 1, _diagnostic(result)
    rows = _summary_rows(result.stdout)
    assert set(rows) == _UNIT_CHECKS, _diagnostic(result)
    assert {name for name, (verdict, _) in rows.items() if verdict == "FAIL"} == {
        "guard: dashboard-window-docs"
    }, _diagnostic(result)
    block = result.stdout[result.stdout.index("guard: dashboard-window-docs  FAIL"):]
    beneath = block.splitlines()[1:3]
    assert beneath == [
        "    DRIFT -- docs/DASHBOARD-PANES.md window=1 disagrees with the census",
        "    fix: regenerate, then commit team/scripts/dashboard/create.sh, docs/DASHBOARD-PANES.md "
        "and docs/dashboard.md: python3 team/scripts/dashboard/generate_window_docs.py",
    ], _diagnostic(result)
    # The budget line still reports the guards' time beside the failure.
    assert _BUDGET_LINE_RE.search(result.stdout), _diagnostic(result)


def test_a_guard_that_cannot_run_fails_the_run_naming_its_exit(fixture_repo: _FixtureRepo) -> None:
    fixture_repo.commit({"team/scripts/check_docs_citations.py": """\
        import sys

        print("check_docs_citations: docs-manifest.txt is missing")
        sys.exit(2)
        """}, "a guard that cannot run")

    result = fixture_repo.run_engine("--base", _BASE_BRANCH)

    assert result.returncode == 1, _diagnostic(result)
    assert re.search(
        r"^guard: docs-citations\s+FAIL\s+\d+\.\ds  the guard could not run \(exit 2\)$",
        result.stdout, re.M,
    ), _diagnostic(result)
    assert "    check_docs_citations: docs-manifest.txt is missing" in result.stdout


# ---------------------------------------------------------------------------
# The base, the integration suite and what a run leaves behind
# ---------------------------------------------------------------------------


def test_default_base_is_the_rc_branch_and_the_summary_names_it(fixture_repo: _FixtureRepo) -> None:
    fixture_repo.commit({"team/scripts/greet.sh": (
        fixture_repo.path / "team/scripts/greet.sh").read_text(encoding="utf-8") + "# trailing\n"},
        "touch a script")

    result = fixture_repo.run_engine("--suite", "integration")

    assert result.returncode == 0, _diagnostic(result)
    assert f"base:    {_BASE_BRANCH} (default: the only local RC branch)" in result.stdout
    # The integration suite runs the affected-test check alone.
    assert set(_summary_rows(result.stdout)) == {"affected integration tests"}, _diagnostic(result)


def test_unknown_base_is_an_invocation_error_in_the_dual_channel_shape(
    fixture_repo: _FixtureRepo,
) -> None:
    result = fixture_repo.run_engine("--base", "ai_rc/v9.9.9")

    assert result.returncode == 2, _diagnostic(result)
    assert "error:   affected.ref_unknown" in result.stderr
    assert "remedy:" in result.stderr
    assert "pre-merge summary" not in result.stdout, "no check may run against an unknown base"


def test_a_run_writes_nothing_outside_its_removed_scratch_directory(fixture_repo: _FixtureRepo) -> None:
    fixture_repo.commit({"team/pgai_agent_kanban/utils.py": _UTILS_COMMENTED}, "comment add_tax")
    temp_dir = fixture_repo.tmp_path / "kanban-tmp"
    kanban_root = fixture_repo.tmp_path / "kanban"

    result = fixture_repo.run_engine("--base", _BASE_BRANCH)

    assert result.returncode == 0, _diagnostic(result)
    left = sorted(p.relative_to(temp_dir).as_posix() for p in temp_dir.rglob("*"))
    # The copied by-path test is the fixture repo's, and the real conftest is
    # not part of the fixture, so nothing but the run's own (removed)
    # scratch directory is ever created there.
    assert not [p for p in left if p.startswith("pre-merge-")], left
    assert not [p for p in left if p.startswith("lint/") or p.endswith(".exit") or "ledger" in p], left
    assert list(kanban_root.iterdir()) == [], "a pre-merge run wrote into the kanban root"
    status = fixture_repo.git("status", "--porcelain", "--untracked-files=all")
    assert status == "", f"a pre-merge run left files in the repository:\n{status}"


# ---------------------------------------------------------------------------
# Helpers, called directly
# ---------------------------------------------------------------------------


class _Branches:
    """A bare-bones repository for the default-base rule."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.commit("root")

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.path), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=False,
        )
        assert done.returncode == 0, f"git {' '.join(args)} failed\n{_diagnostic(done)}"
        return done.stdout

    def commit(self, message: str) -> None:
        self.git("commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def branches(tmp_path: pathlib.Path) -> _Branches:
    require_binary("git", label="pre-merge check engine")
    return _Branches(tmp_path / "branches")


def test_default_base_prefers_the_rc_branch_head_is_nearest(branches: _Branches) -> None:
    branches.git("branch", "ai_main")
    branches.git("checkout", "-q", "-b", "ai_rc/v1.2.0")
    branches.commit("rc 1.2.0 work")
    branches.git("checkout", "-q", "-b", "ai_rc/v1.10.0")
    branches.commit("rc 1.10.0 work")
    branches.git("checkout", "-q", "-b", "ai_feature/task")
    branches.commit("the change")

    ref, reason = default_base(branches.path)

    assert ref == "ai_rc/v1.10.0"
    assert "nearest" in reason


def test_default_base_breaks_a_tie_by_semantic_version_not_text(branches: _Branches) -> None:
    for rc in ("ai_rc/v1.9.0", "ai_rc/v1.10.0"):
        branches.git("branch", rc)
    branches.git("checkout", "-q", "-b", "ai_feature/task")

    ref, _ = default_base(branches.path)

    assert ref == "ai_rc/v1.10.0", "v1.10.0 is the higher version; text order would pick v1.9.0"


def test_default_base_keeps_to_the_feature_branch_prefix(branches: _Branches) -> None:
    branches.git("branch", "rc/v5.0.0")
    branches.git("branch", "ai_rc/v1.0.0")
    branches.git("checkout", "-q", "-b", "ai_feature/task")

    assert default_base(branches.path)[0] == "ai_rc/v1.0.0"


def test_default_base_falls_back_to_the_prefixed_main_branch(branches: _Branches) -> None:
    branches.git("branch", "ai_main")
    branches.git("checkout", "-q", "-b", "ai_feature/task")

    ref, reason = default_base(branches.path)

    assert ref == "ai_main"
    assert "no local RC branch" in reason


def test_default_base_with_no_rc_or_main_branch_is_refused(tmp_path: pathlib.Path) -> None:
    require_binary("git", label="pre-merge check engine")
    repo = tmp_path / "lonely"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "work"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=F", "-c", "user.email=f@example.invalid",
         "commit", "-q", "--allow-empty", "-m", "root"], check=True,
    )

    with pytest.raises(InvocationError) as caught:
        default_base(repo)

    assert caught.value.error == "pre_merge.no_default_base"
    assert "--base HEAD~1" in caught.value.remedy
    assert "<" not in caught.value.remedy, "a remedy is runnable as printed"


@pytest.mark.parametrize(
    "codes, expected",
    [
        pytest.param([0], 0, id="one_lane_passed"),
        pytest.param([0, 5], 0, id="serial_lane_collected_nothing"),
        pytest.param([5, 5], 5, id="nothing_collected_anywhere"),
        pytest.param([0, 1], 1, id="serial_lane_failed"),
        pytest.param([2, 1], 2, id="first_failure_wins"),
    ],
)
def test_lane_exits_combine_as_the_gated_runners_combine_them(codes: list[int], expected: int) -> None:
    assert combine_lane_exits(codes) == expected


def test_failed_test_files_reads_pytest_summary_lines() -> None:
    output = textwrap.dedent("""\
        tests/unit/test_a.py .F                                    [100%]
        =========================== short test summary info ============================
        FAILED tests/unit/test_a.py::test_one - assert 1 == 2
        FAILED tests/unit/test_a.py::test_two[x] - AssertionError
        ERROR tests/unit/test_b.py - ImportError: no module
        """)

    assert failed_test_files(output) == ["tests/unit/test_a.py", "tests/unit/test_b.py"]


def test_the_by_path_test_and_runner_ignored_files_are_never_selected_twice() -> None:
    assert pre_merge.BY_PATH_TEST == "tests/unit/test_package_modules_run_by_path.py"
    assert (_TEAM_DIR / pre_merge.BY_PATH_TEST).is_file()
    runner = (_TEAM_DIR / "scripts" / "run-unit-tests.sh").read_text(encoding="utf-8")
    for ignored in pre_merge.IGNORED_TEST_FILES["unit"]:
        assert f"--ignore={ignored}" in runner, (
            f"{ignored} is left out of affected selection because the unit runner "
            f"ignores it; the runner no longer does, so update IGNORED_TEST_FILES"
        )


def test_each_ratchet_lint_names_a_script_in_the_tree() -> None:
    assert [lint.name for lint in pre_merge.RATCHET_LINTS] == [
        "inline-python", "python-duplicates", "engine-size", "api-route-impl", "echo-pipe-grep-q",
        "tracked-ignored",
    ]
    for lint in pre_merge.RATCHET_LINTS:
        assert (_TEAM_DIR / lint.script).is_file(), lint.script


def test_each_whole_tree_guard_names_a_script_in_the_tree() -> None:
    assert [(guard.name, guard.argv) for guard in pre_merge.WHOLE_TREE_GUARDS] == [
        ("env-bootstrap", ("scripts/lint_env_bootstrap.py",)),
        ("operator-verb-docs", ("scripts/generate_operator_verb_docs.py", "--check")),
        ("dashboard-window-docs", ("scripts/dashboard/generate_window_docs.py", "--check")),
        ("docs-citations", ("scripts/check_docs_citations.py",)),
    ]
    assert [f"guard: {guard.name}" for guard in pre_merge.WHOLE_TREE_GUARDS] == _GUARD_ROWS
    for guard in pre_merge.WHOLE_TREE_GUARDS:
        assert (_TEAM_DIR / guard.script).is_file(), guard.script
        assert "<" not in guard.fix, "a guard's fix is runnable as printed"
    assert pre_merge.GUARD_BUDGET_SECONDS == 5.0


def test_docs_citations_fix_agrees_with_the_checker_on_a_recorded_exclusion(
    tmp_path: pathlib.Path,
) -> None:
    """A governance file citing a recorded exclusion with the docs/ prefix
    fails the docs-citations guard. The checker's own To-fix block says not
    to add that page to the manifest, so the fix printed beneath the row
    must name rewording or removing the citation, not a manifest line as
    the only remedy -- and it still ends on the runnable re-check."""
    team = tmp_path / "repo" / "team"
    for rel in (
        "pgai_agent_kanban/__init__.py",
        "pgai_agent_kanban/lib/__init__.py",
        "pgai_agent_kanban/lib/docs_citation_scan.py",
        "scripts/check_docs_citations.py",
    ):
        (team / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_TEAM_DIR / rel, team / rel)
    (team / "config").mkdir()
    (team / "config" / "docs-manifest.txt").write_text(
        "# Fixture docs manifest.\n#\n"
        "# EXCLUDED-CITATION-TARGETS: PANES-REFERENCE.md\n\nguide.md\n",
        encoding="utf-8",
    )
    (team / "README.md").write_text(
        "# Fixture governance root\n\n"
        "Start with docs/guide.md.\n"
        "Per-pane detail is in docs/PANES-REFERENCE.md.\n",
        encoding="utf-8",
    )
    docs = tmp_path / "repo" / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (docs / "PANES-REFERENCE.md").write_text(
        "# Panes\n\nRepository checkout only.\n", encoding="utf-8"
    )
    guard = next(g for g in pre_merge.WHOLE_TREE_GUARDS if g.name == "docs-citations")

    result = pre_merge.check_whole_tree_guard(team, guard)

    assert not result.passed and result.note == "finding(s)", result.detail
    checker_output = "\n".join(result.detail[:-1])
    assert "Do not add them to the manifest" in checker_output, checker_output
    fix_line = result.detail[-1]
    assert fix_line == f"fix: {guard.fix}"
    assert "add the manifest line each finding" not in guard.fix, (
        "the fix prescribes a manifest line unconditionally, which the checker "
        f"forbids for a recorded exclusion: {guard.fix}"
    )
    for remedy in ("team/config/docs-manifest.txt", "EXCLUDED-CITATION-TARGETS",
                   "drop the docs/ prefix", "remove the citation"):
        assert remedy in guard.fix, f"the fix no longer names {remedy!r}: {guard.fix}"
    assert guard.fix.endswith("python3 team/scripts/check_docs_citations.py")


def _guarded_run(guard_seconds: Sequence[float]) -> pre_merge.PreMergeRun:
    run = pre_merge.PreMergeRun(
        repo=pathlib.Path("/repo"), suite="unit", base="ai_rc/v1.0.0",
        base_reason="given with --base", cap=60, workers=1,
    )
    run.checks.append(pre_merge.CheckResult("by-path", passed=True, duration=30.0))
    run.checks.extend(
        pre_merge.CheckResult(f"guard: g{i}", passed=True, duration=seconds, guard=True)
        for i, seconds in enumerate(guard_seconds)
    )
    return run


def test_guards_within_the_budget_report_their_added_time_against_it() -> None:
    summary = pre_merge.format_summary(_guarded_run([1.0, 0.5]))

    # The by-path check's 30s is not guard time.
    assert "guards:  2 always-run guard(s) added 1.5s of the 5.0s budget" in summary.splitlines()
    assert "pre-merge: PASS -- all 3 check(s) passed" in summary


def test_guards_over_the_budget_narrate_one_line_and_never_fail_the_run() -> None:
    run = _guarded_run([4.0, 2.5])

    summary = pre_merge.format_summary(run)

    over = [line for line in summary.splitlines() if "budget" in line]
    assert over == [
        "guards:  2 always-run guard(s) added 6.5s, over the 5.0s budget "
        "(a soft estimate; it never fails the run)"
    ], summary
    assert run.passed
    assert "pre-merge: PASS -- all 3 check(s) passed" in summary


def test_a_run_with_no_guards_prints_no_budget_line() -> None:
    summary = pre_merge.format_summary(_guarded_run([]))

    assert "budget" not in summary


def test_a_long_guard_output_keeps_its_first_lines_and_names_the_rerun() -> None:
    text = "\n".join(f"finding {i}" for i in range(25)) + "\n\n"

    kept = pre_merge._head(text, 20, "python3 team/scripts/check_docs_citations.py")

    assert kept[:20] == [f"finding {i}" for i in range(20)]
    assert kept[20] == (
        "... 5 more line(s); to see them all: python3 team/scripts/check_docs_citations.py"
    )
    assert pre_merge._head("one\n\ntwo\n", 20, "x") == ["one", "two"]
