"""
test_lint_python_duplicates.py — Unit tests for team/scripts/lint_python_duplicates.py.

Every test builds its own fixture tree under pytest's ``tmp_path``: the three
scan roots (``team/pgai_agent_kanban``, ``team/scripts``, ``team/pm-agent``)
holding fixture ``.py`` files, and a
``team/config/python-duplicates-allowlist.txt``. The lint is pointed at it
with ``--root``, so nothing here reads this dev tree's own modules or
allowlist.

Coverage:

  1. Duplicate bodies: two functions whose bodies differ only in names,
     parameter names, annotations, docstrings and whitespace are reported;
     bodies below the size floor are not.
  2. Deny-list: each deny-listed helper name, configparser instantiation
     and ``## State`` regex outside its owning module is reported; the same
     inside its owning module is not; plain text and docstrings are not.
  3. Ratchet: a tree matching its allowlist is clean; a new finding is
     reported; an entry that no longer matches, or matches less than
     recorded, asks to tighten the entry; a key above its count is reported.
  4. The lint exits 0 whatever it finds and writes its findings file.
  5. The exact wiring block both gated runners carry, run against a seeded
     fixture tree with the real ``gated_runner_lint.sh``, puts the seeded
     finding into the runner's findings output and returns 0.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_python_duplicates.py"
_GATED_RUNNER_LIB = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_RUNNERS = (
    _TEAM_DIR / "scripts" / "run-unit-tests.sh",
    _TEAM_DIR / "scripts" / "run-integration-tests.sh",
)
_PKG = "team/pgai_agent_kanban"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# A body comfortably above the default size floor.
COPY_A = textwrap.dedent(
    '''\
    def load_rows(path: str, limit: int = 10) -> list:
        """Read rows."""
        rows = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip() and not line.startswith("#"):
                    rows.append(line.split(","))
                if len(rows) >= limit:
                    break
        return rows
    '''
)

# Same body: different function name, parameter names, annotations,
# docstring, spacing and comments.
COPY_B = textwrap.dedent(
    """\
    import os


    def read_csv_lines(target, maximum=10):
        # no docstring here
        rows = [ ]
        with open( target , encoding = "utf-8" ) as fh:
            for line in fh:
                if line.strip()  and  not line.startswith( "#" ):
                    rows.append( line.split(",") )
                if len(rows) >= maximum:
                    break
        return rows
    """
)

ATOMIC_WRITE = textwrap.dedent(
    """\
    import os


    def {name}(target, content):
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    """
)


def _make_tree(tmp_path: Path, files: dict[str, str], allowlist: str = "") -> Path:
    """Build ``<root>/<rel>`` files, the three scan roots and the allowlist; return root."""
    root = tmp_path / "repo"
    for scan_root in ("team/pgai_agent_kanban", "team/scripts", "team/pm-agent"):
        (root / scan_root).mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    cfg = root / "team" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "python-duplicates-allowlist.txt").write_text(allowlist, encoding="utf-8")
    return root


def _run(root: Path, tmp_path: Path, *extra: str) -> tuple[int, str, list[dict]]:
    """Run the lint against *root*; return (exit code, stdout, findings)."""
    findings_file = tmp_path / "findings.json"
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--findings-file", str(findings_file), *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    return proc.returncode, proc.stdout, findings


def _keys(findings: list[dict]) -> list[tuple[str, str, str]]:
    return sorted((f["kind"], f["family"], f["file"]) for f in findings)


def _emit(root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--emit-allowlist"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# ---------------------------------------------------------------------------
# 1. Duplicate bodies
# ---------------------------------------------------------------------------


def test_identical_normalised_bodies_with_different_names_are_reported(tmp_path):
    root = _make_tree(
        tmp_path,
        {f"{_PKG}/a.py": COPY_A, "team/scripts/b.py": COPY_B},
    )
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _keys(findings) == [
        ("unlisted", "duplicate-body", f"{_PKG}/a.py"),
        ("unlisted", "duplicate-body", "team/scripts/b.py"),
    ]
    by_file = {f["file"]: f for f in findings}
    assert "team/scripts/b.py:read_csv_lines" in by_file[f"{_PKG}/a.py"]["summary"]
    assert f"{_PKG}/a.py:load_rows" in by_file["team/scripts/b.py"]["summary"]
    assert by_file[f"{_PKG}/a.py"]["line"] == 1
    assert all(f["lint"] == "python_duplicates" and f["severity"] == "quality" for f in findings)
    assert "duplicate-body=2" in out


def test_methods_are_named_by_qualified_name(tmp_path):
    method = "class Loader:\n" + textwrap.indent(COPY_A, "    ")
    root = _make_tree(tmp_path, {f"{_PKG}/a.py": method, "team/pm-agent/b.py": COPY_B})
    assert _emit(root) == (
        f"duplicate-body\t{_PKG}/a.py\tLoader.load_rows\t1\n"
        "duplicate-body\tteam/pm-agent/b.py\tread_csv_lines\t1\n"
    )


def test_different_bodies_are_not_reported(tmp_path):
    other = COPY_B.replace(">= maximum", "> maximum")
    root = _make_tree(tmp_path, {f"{_PKG}/a.py": COPY_A, "team/scripts/b.py": other})
    _rc, _out, findings = _run(root, tmp_path)
    assert findings == []


def test_bodies_below_the_size_floor_are_not_reported(tmp_path):
    tiny = "def a(x):\n    return x.value\n\n\ndef b(y):\n    return y.value\n"
    root = _make_tree(tmp_path, {f"{_PKG}/tiny.py": tiny})
    _rc, _out, findings = _run(root, tmp_path)
    assert findings == []
    _rc, _out, findings = _run(root, tmp_path, "--min-nodes", "1")
    assert _keys(findings) == [
        ("unlisted", "duplicate-body", f"{_PKG}/tiny.py"),
        ("unlisted", "duplicate-body", f"{_PKG}/tiny.py"),
    ]


# ---------------------------------------------------------------------------
# 2. Deny-list
# ---------------------------------------------------------------------------

_DENY_CASES = [
    ("atomic-write", "_atomic_write", f"{_PKG}/lib/atomic_write.py"),
    ("traversal", "_has_traversal", f"{_PKG}/api/validation.py"),
    ("version-parse", "_parse_version", f"{_PKG}/lib/semver.py"),
    ("iso-parse", "_parse_iso", f"{_PKG}/lib/timeutil.py"),
    ("read-section", "read_section", f"{_PKG}/task_status.py"),
]


@pytest.mark.parametrize("family,name,owner", _DENY_CASES, ids=[c[0] for c in _DENY_CASES])
def test_deny_listed_name_outside_its_owner_is_reported_and_inside_is_not(tmp_path, family, name, owner):
    body = f"def {name}(value):\n    return value\n"
    root = _make_tree(tmp_path, {"team/scripts/copy.py": body, owner: body})
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _keys(findings) == [("unlisted", family, "team/scripts/copy.py")]
    assert owner in findings[0]["summary"]
    assert owner in findings[0]["remedy"]


def test_deny_listed_name_matches_with_or_without_leading_underscores(tmp_path):
    body = (
        "def atomic_write(a, b):\n    pass\n\n\n"
        "class W:\n    def __atomic_write(self):\n        pass\n\n\n"
        "def _atomic_write_json(a, b):\n    pass\n"
    )
    root = _make_tree(tmp_path, {f"{_PKG}/w.py": body})
    assert _emit(root) == (
        f"atomic-write\t{_PKG}/w.py\tW.__atomic_write\t1\n"
        f"atomic-write\t{_PKG}/w.py\tatomic_write\t1\n"
    )


def test_deny_listed_copy_is_not_also_reported_as_a_duplicate_body(tmp_path):
    root = _make_tree(
        tmp_path,
        {
            f"{_PKG}/lib/atomic_write.py": ATOMIC_WRITE.format(name="atomic_write"),
            f"{_PKG}/ledger.py": ATOMIC_WRITE.format(name="_atomic_write"),
            "team/scripts/writer.py": ATOMIC_WRITE.format(name="write_file_safely"),
        },
    )
    assert _emit(root) == (
        f"atomic-write\t{_PKG}/ledger.py\t_atomic_write\t1\n"
        "duplicate-body\tteam/scripts/writer.py\twrite_file_safely\t1\n"
    )


def test_configparser_instantiation_outside_lib_ini_is_reported_under_any_alias(tmp_path):
    body = textwrap.dedent(
        """\
        import configparser
        import configparser as _cp
        from configparser import RawConfigParser as RCP


        def one():
            return configparser.ConfigParser()


        def two():
            p = _cp.RawConfigParser()
            q = RCP()
            return p, q


        p = configparser.ConfigParser(interpolation=None)
        """
    )
    root = _make_tree(
        tmp_path,
        {"team/pm-agent/cfg.py": body, f"{_PKG}/lib/ini.py": body},
    )
    assert _emit(root) == (
        "ini-instantiation\tteam/pm-agent/cfg.py\t<module>\t1\n"
        "ini-instantiation\tteam/pm-agent/cfg.py\tone\t1\n"
        "ini-instantiation\tteam/pm-agent/cfg.py\ttwo\t2\n"
    )


def test_state_regex_outside_task_status_is_reported(tmp_path):
    body = textwrap.dedent(
        '''\
        import re

        _STATE_RE = re.compile(r"^##\\s+State\\s*$", re.MULTILINE)


        def read(text):
            m = re.search(r"## State\\n(?:[ \\t]*\\n)*([^\\n]+)", text)
            return m


        def write(state):
            return re.sub("## State", state, "x")
        '''
    )
    root = _make_tree(
        tmp_path,
        {f"{_PKG}/reader.py": body, f"{_PKG}/task_status.py": body},
    )
    assert _emit(root) == (
        f"state-regex\t{_PKG}/reader.py\t<module>\t1\n"
        f"state-regex\t{_PKG}/reader.py\tread\t1\n"
        f"state-regex\t{_PKG}/reader.py\twrite\t1\n"
    )


def test_state_text_that_is_not_a_regex_is_not_reported(tmp_path):
    body = textwrap.dedent(
        '''\
        """Reads the ## State field; the pattern is r"^##\\s+State"."""

        TEMPLATE = """
        ## State
        BACKLOG

        - [ ] Human has set status.md ## State to DONE.
        """


        def render(state):
            """Write ``## State\\n`` followed by *state*."""
            return f"## State\\n{state}\\n" + "## State\\n"


        def is_heading(line):
            return line.strip() == "## State"
        '''
    )
    root = _make_tree(tmp_path, {f"{_PKG}/render.py": body})
    _rc, _out, findings = _run(root, tmp_path)
    assert findings == []


# ---------------------------------------------------------------------------
# 3. Ratchet
# ---------------------------------------------------------------------------


def test_tree_matching_its_allowlist_is_clean(tmp_path):
    files = {f"{_PKG}/a.py": COPY_A, "team/scripts/b.py": COPY_B, "team/scripts/c.py": "def _parse_iso(v):\n    return v\n"}
    root = _make_tree(tmp_path, files)
    (root / "team/config/python-duplicates-allowlist.txt").write_text("# header\n\n" + _emit(root), encoding="utf-8")
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []
    assert "0 ratchet finding(s)" in out
    assert "iso-parse=1" in out and "duplicate-body=2" in out


def test_new_finding_not_in_the_allowlist_is_reported(tmp_path):
    allow = "iso-parse\tteam/scripts/c.py\t_parse_iso\t1\n"
    root = _make_tree(
        tmp_path,
        {
            "team/scripts/c.py": "def _parse_iso(v):\n    return v\n",
            "team/scripts/d.py": "def _parse_iso(v):\n    return v\n",
        },
        allowlist=allow,
    )
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _keys(findings) == [("unlisted", "iso-parse", "team/scripts/d.py")]
    assert "[unlisted]" in out


def test_allowlist_entry_that_no_longer_matches_asks_to_tighten_the_entry(tmp_path):
    allow = "atomic-write\tteam/scripts/gone.py\t_atomic_write\t1\n"
    root = _make_tree(tmp_path, {}, allowlist=allow)
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _keys(findings) == [("below", "atomic-write", "team/scripts/gone.py")]
    assert "tighten the entry (remove it)" in findings[0]["summary"]


def test_entry_above_what_is_measured_asks_to_tighten_to_the_measured_count(tmp_path):
    body = "import configparser\n\n\ndef f():\n    return configparser.ConfigParser()\n"
    allow = "ini-instantiation\tteam/scripts/cfg.py\tf\t3\n"
    root = _make_tree(tmp_path, {"team/scripts/cfg.py": body}, allowlist=allow)
    _rc, _out, findings = _run(root, tmp_path)
    assert _keys(findings) == [("below", "ini-instantiation", "team/scripts/cfg.py")]
    assert "tighten the entry" in findings[0]["summary"]
    assert "ini-instantiation\tteam/scripts/cfg.py\tf\t1" in findings[0]["summary"]


def test_key_above_its_allowlist_count_is_reported(tmp_path):
    body = (
        "import configparser\n\n\ndef f():\n"
        "    a = configparser.ConfigParser()\n    b = configparser.ConfigParser()\n    return a, b\n"
    )
    allow = "ini-instantiation  team/scripts/cfg.py   f   1  \n"
    root = _make_tree(tmp_path, {"team/scripts/cfg.py": body}, allowlist=allow)
    _rc, _out, findings = _run(root, tmp_path)
    assert _keys(findings) == [("above", "ini-instantiation", "team/scripts/cfg.py")]
    assert "2 finding(s), above its allowlist entry of 1" in findings[0]["summary"]


def test_malformed_allowlist_line_is_reported_and_ignored(tmp_path):
    allow = "# comment\n\nnot-a-family\tteam/scripts/x.py\tf\t1\natomic-write only-two\n"
    root = _make_tree(tmp_path, {}, allowlist=allow)
    _rc, _out, findings = _run(root, tmp_path)
    assert [(f["kind"], f["line"]) for f in findings] == [("allowlist-malformed", 3), ("allowlist-malformed", 4)]


# ---------------------------------------------------------------------------
# 4. Scan scope and robustness
# ---------------------------------------------------------------------------


def test_files_under_test_directories_are_not_scanned(tmp_path):
    copy = "def _has_traversal(v):\n    return '..' in v\n"
    root = _make_tree(
        tmp_path,
        {
            "team/scripts/lib/checks/tests/test_x.py": copy,
            f"{_PKG}/test/helper.py": copy,
            "team/scripts/test_named_but_not_in_a_test_dir.py": copy,
        },
    )
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _keys(findings) == [("unlisted", "traversal", "team/scripts/test_named_but_not_in_a_test_dir.py")]
    assert "totals: 1 file(s) scanned" in out


def test_unparseable_file_is_reported_and_the_lint_still_exits_zero(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/broken.py": "def f(:\n", "team/scripts/ok.py": "x = 1\n"})
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert [(f["kind"], f["file"]) for f in findings] == [("parse-error", "team/scripts/broken.py")]


def test_missing_allowlist_reports_every_finding_as_unlisted(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/c.py": "def _parse_iso(v):\n    return v\n"})
    (root / "team/config/python-duplicates-allowlist.txt").unlink()
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert sorted(f["kind"] for f in findings) == ["allowlist-missing", "unlisted"]


# ---------------------------------------------------------------------------
# 5. Runner wiring
# ---------------------------------------------------------------------------

_BLOCK_START = 'echo "Running duplicated-Python ratchet lint..."'
_BLOCK_END_RE = re.compile(r"^unset _R[UI]_PYTHON_DUPLICATES_FINDINGS\s*$", re.M)


def _runner_block(runner: Path) -> str:
    text = runner.read_text(encoding="utf-8")
    start = text.find(_BLOCK_START)
    assert start != -1, f"{runner.name} does not run the duplicated-Python lint"
    end = _BLOCK_END_RE.search(text, start)
    assert end is not None, f"{runner.name}: duplicated-Python lint block has no closing unset"
    return text[start : end.end()]


@pytest.mark.parametrize("runner", _RUNNERS, ids=lambda p: p.name)
def test_runner_wiring_records_a_seeded_finding_and_returns_zero(tmp_path, runner):
    """Run the runner's own wiring block against a seeded fixture tree.

    The fixture's ``team/scripts`` carries a copy of the lint (it self-locates
    its root from its own path) plus one unlisted deny-listed helper; the
    block runs from the fixture's ``team`` directory, as the runner runs from
    its own.
    """
    root = _make_tree(tmp_path, {f"{_PKG}/seeded.py": "def _has_traversal(v):\n    return '..' in v\n"})
    shutil.copy2(_LINT_SCRIPT, root / "team" / "scripts" / "lint_python_duplicates.py")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out_file = tmp_path / "findings-out.json"
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -euo pipefail\n"
        f'source "{_GATED_RUNNER_LIB}"\n'
        + _runner_block(runner)
        + "\n"
        + f'gated_runner_write_findings_file "{out_file}" "{runner.name}"\n'
        + 'echo "block-exit=$?"\n',
        encoding="utf-8",
    )
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch)},
    )
    proc = subprocess.run(
        ["bash", str(driver)],
        cwd=root / "team",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "block-exit=0" in proc.stdout
    data = json.loads(out_file.read_text(encoding="utf-8"))
    ours = [f for f in data["findings"] if f.get("lint") == "python_duplicates"]
    assert [(f["kind"], f["family"], f["file"]) for f in ours] == [
        ("unlisted", "traversal", f"{_PKG}/seeded.py")
    ]
