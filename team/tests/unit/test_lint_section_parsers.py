"""
test_lint_section_parsers.py — Unit tests for team/scripts/lint_section_parsers.py.

Every test builds its own fixture tree under pytest's ``tmp_path``: Python
and bash files under ``team/pgai_agent_kanban``, ``team/pm-agent`` and
``team/scripts``, and a ``team/config/section-parser-allowlist.txt``. The
lint is pointed at it with ``--root``, so nothing here reads this dev tree's
own code or allowlist -- except the last test, which runs the lint on this
tree as it stands.

Coverage:

  1. A seeded hand-rolled parser is reported: a Python
     ``if line.startswith("## ")`` walk and an awk
     ``/^## State/{f=1;next} f&&/^## /{exit}`` reader, plus the other
     heading-detection shapes (``== "## "``, ``^\\s*##`` regexes,
     ``re.match("##...")``, bash ``[[ =~ ^## ]]`` and ``sed``/``grep``
     addresses, ``#{1,3}``-quantified hash runs, unanchored ``grep`` and
     ``awk`` matches, and a comparison with a variable holding a ``## ``
     heading).
  2. What is not a site: comment lines, Python docstrings, test files, the
     exempt reader and its bash library, and ``#``-only text that detects
     no level-2 heading.
  3. The allowlist: an allowlisted site is not reported; an entry naming a
     missing file, or a line that changed, is reported for removal; two
     identical lines need two entries; a malformed line, an unknown class
     and an empty reason are reported.
  4. The lint exits 0 in every case and always writes its findings file in
     the gated runner's per-instance shape; ``--help`` prints usage and
     exits 0.
  5. The wiring block both gated runners carry, run against a seeded
     fixture tree with the real ``gated_runner_lint.sh``, puts the seeded
     finding into the runner's findings output and returns 0.
"""

from __future__ import annotations

import importlib.util
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
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_section_parsers.py"
_GATED_RUNNER_LIB = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_RUNNERS = (
    _TEAM_DIR / "scripts" / "run-unit-tests.sh",
    _TEAM_DIR / "scripts" / "run-integration-tests.sh",
)

_spec = importlib.util.spec_from_file_location("lint_section_parsers", _LINT_SCRIPT)
assert _spec is not None and _spec.loader is not None
lint_section_parsers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_section_parsers)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

PY_PARSER = textwrap.dedent(
    '''\
    def state(text):
        found = False
        for line in text.splitlines():
            if line.startswith("## State"):
                found = True
                continue
            if found and line.startswith("## "):
                break
            if found and line.strip():
                return line.strip()
        return ""
    '''
)

AWK_PARSER = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    state="$(awk '/^## State/{f=1;next} f&&/^## /{exit} f&&NF{print;exit}' "$1")"
    echo "$state"
    """
)

READER_CALL = textwrap.dedent(
    """\
    from pgai_agent_kanban.task_status import read_section


    def state(text):
        return read_section(text, "State") or ""
    """
)


def _make_tree(tmp_path: Path, files: dict[str, str], allowlist: str | None = "") -> Path:
    """Build ``<root>/<rel>`` files under team/ plus the allowlist; return root.

    *files* keys are paths relative to the repository root. An allowlist of
    None leaves the file absent.
    """
    root = tmp_path / "repo"
    for rel in ("team/pgai_agent_kanban", "team/pm-agent", "team/scripts"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    cfg = root / "team" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    if allowlist is not None:
        (cfg / "section-parser-allowlist.txt").write_text(allowlist, encoding="utf-8")
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


def _kinds(findings: list[dict]) -> list[tuple[str, str, int]]:
    return sorted((f["kind"], f["file"], f["line"]) for f in findings)


def _entry(path: str, site_class: str, reason: str, code: str) -> str:
    return f"{path}\t{site_class}\t{reason}\t{code}\n"


# ---------------------------------------------------------------------------
# 1. Seeded parsers are reported
# ---------------------------------------------------------------------------


def test_seeded_python_startswith_parser_is_reported(tmp_path):
    root = _make_tree(tmp_path, {"team/pgai_agent_kanban/ops/new.py": PY_PARSER})
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    rel = "team/pgai_agent_kanban/ops/new.py"
    assert _kinds(findings) == [("unlisted", rel, 4), ("unlisted", rel, 7)]
    assert all(f["lint"] == "section_parsers" and f["severity"] == "quality" for f in findings)
    assert 'line.startswith("## ")' in findings[1]["summary"]
    assert "task_status.read_section" in findings[0]["remedy"]
    assert "[unlisted]" in out


def test_seeded_awk_parser_is_reported(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/lib/new.sh": AWK_PARSER})
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("unlisted", "team/scripts/lib/new.sh", 2)]
    assert "(anchored)" in findings[0]["summary"]
    assert "read_md_field" in findings[0]["remedy"]


_CLI_READS = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    model="$(pp_run_ops pgai_agent_kanban.task_status read "$f" \\
        --section=Model --raw 2>/dev/null)" || model=""
    labels="$(pp_run_ops pgai_agent_kanban.task_status read "$f" --section=Labels --raw)"
    # pp_run_ops pgai_agent_kanban.task_status read "$f" --section=Commented
    count="$(pp_run_ops pgai_agent_kanban.task_status read "$f" \\
        --section="Usage Limit Requeue" --field=count --default=0)"
    state="$(read_md_field --value "$f" State)"
    """
)


def test_seeded_direct_cli_section_reads_are_reported_and_field_reads_are_not(tmp_path):
    """A bash call of the reader's CLI that read_md_field could make is a
    cli-read site (its first line, across continuations); a --field record
    read, a comment and a read_md_field call are not."""
    rel = "team/scripts/lib/new.sh"
    root = _make_tree(tmp_path, {rel: _CLI_READS})
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("unlisted", rel, 2), ("unlisted", rel, 4)]
    assert all("(cli-read)" in f["summary"] for f in findings)
    assert all("read_md_field" in f["remedy"] for f in findings)


def test_seeded_heading_variable_reader_is_reported_at_its_comparison(tmp_path):
    body = textwrap.dedent(
        '''\
        def field(text, heading):
            target = f"## {heading}"
            for line in text.splitlines():
                if line.strip() == target:
                    return line
            return None
        '''
    )
    root = _make_tree(tmp_path, {"team/pm-agent/new.py": body})
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("unlisted", "team/pm-agent/new.py", 4)]
    assert "(variable)" in findings[0]["summary"]


@pytest.mark.parametrize(
    "line, shape",
    [
        pytest.param('if stripped == "## Queue":', "equals", id="py_equals"),
        pytest.param('if "## State" == line.strip():', "equals", id="py_equals_reversed"),
        pytest.param("if stripped.startswith(('##', '# ')):", "startswith", id="py_startswith_tuple"),
        pytest.param('HEAD_RE = re.compile(r"^\\s*##\\s+Bundled Items\\s*$")', "anchored", id="py_indent_regex"),
        pytest.param('m = re.search(rf"(?m)^## {heading}", text)', "anchored", id="py_multiline_regex"),
        pytest.param('m = re.match(r"##\\s+Symptom", line)', "re-match", id="py_re_match"),
        pytest.param('if [[ "$line" =~ ^##[[:space:]]+State ]]; then', "anchored", id="bash_regex_test"),
        pytest.param('if [[ "$line" == "## "* ]]; then', "equals", id="bash_glob_test"),
        pytest.param("sed -n '/^## State/,/^## /p' \"$f\"", "anchored", id="sed_range"),
        pytest.param("grep -A1 '^## Status' \"$f\"", "anchored", id="grep_address"),
        pytest.param('target = f"## {heading}"\nif line.strip() == target:', "variable", id="py_equals_heading_variable"),
        pytest.param('head = "## " + name\nif line.startswith(head):', "variable", id="py_startswith_heading_variable"),
        pytest.param('target="## $heading"\nif [[ "$line" == "$target" ]]; then', "variable", id="bash_equals_heading_variable"),
        pytest.param('pattern = re.compile(r"^#{1,3}\\s+" + re.escape(field) + r"\\s*$", re.M)', "anchored", id="py_quantified_hashes"),
        pytest.param('_END_RE = re.compile(r"^ {0,3}(#{2,6})\\s")', "anchored", id="py_indented_group_quantified"),
        pytest.param('m = re.match(r"#+\\s+Symptom", line)', "re-match", id="py_re_match_hash_run"),
        pytest.param('x=$(grep -A1 "## State" "$1" | tail -1)', "unanchored", id="grep_unanchored"),
        pytest.param("y=$(awk '$0 ~ /## Goal/ {f=1}' \"$1\")", "unanchored", id="awk_unanchored"),
    ],
)
def test_each_heading_detection_shape_is_a_site(line, shape):
    # A multi-line case is the file's earlier lines, then the line under test.
    *earlier, line = line.split("\n")
    assert lint_section_parsers.line_shape(line, lint_section_parsers.heading_variables(earlier)) == shape


@pytest.mark.parametrize(
    "line",
    [
        pytest.param('body = read_section(text, "State")', id="reader_call"),
        pytest.param('out.append(f"## {heading}\\n\\n{value}")', id="writer_literal"),
        pytest.param('if line.startswith("#"):', id="single_hash"),
        pytest.param('echo "## State"', id="echo_heading"),
        pytest.param('state="$(read_md_field --value "$f" State)"', id="read_md_field"),
        pytest.param('_RE = re.compile(r"^#{3}\\s")', id="quantified_level_three"),
        pytest.param('_RE = re.compile(r"^(#{3,6})\\s+Finding")', id="quantified_level_three_up"),
        pytest.param('target = f"## {heading}"', id="heading_variable_assignment"),
        pytest.param('if line.strip() == target:', id="comparison_with_unknown_variable"),
        pytest.param('n=$(grep -c "Status" "$f")', id="grep_without_hashes"),
    ],
)
def test_lines_that_detect_no_level_two_heading_are_not_sites(line):
    assert lint_section_parsers.line_shape(line) is None


# ---------------------------------------------------------------------------
# 2. What is not scanned
# ---------------------------------------------------------------------------


def test_comments_and_docstrings_are_not_scanned(tmp_path):
    body = textwrap.dedent(
        '''\
        """Module docstring: the old code did line.startswith("## ")."""


        def f(text):
            """Find '^## State' the way the reader does."""
            # a comment quoting startswith("## ")
            return text
        '''
    )
    sh = '#!/usr/bin/env bash\n# awk \'/^## State/{f=1}\' was the old reader\necho ok\n'
    root = _make_tree(tmp_path, {"team/pm-agent/doc.py": body, "team/scripts/doc.sh": sh})
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []


def test_test_files_and_the_exempt_reader_are_not_scanned(tmp_path):
    root = _make_tree(
        tmp_path,
        {
            "team/scripts/tests/helper.sh": AWK_PARSER,
            "team/pgai_agent_kanban/test_thing.py": PY_PARSER,
            "team/pgai_agent_kanban/conftest.py": PY_PARSER,
            "team/pgai_agent_kanban/task_status.py": PY_PARSER,
            "team/scripts/lib/md_field.sh": AWK_PARSER,
            "team/pgai_agent_kanban/ops/clean.py": READER_CALL,
        },
    )
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []
    assert "0 site(s)" in out


# ---------------------------------------------------------------------------
# 3. The allowlist
# ---------------------------------------------------------------------------


def test_an_allowlisted_site_is_not_reported(tmp_path):
    rel = "team/scripts/lib/new.sh"
    code = """state="$(awk '/^## State/{f=1;next} f&&/^## /{exit} f&&NF{print;exit}' "$1")\""""
    allow = "# header comment\n\n" + _entry(rel, "presence-check", "fixture site", code)
    root = _make_tree(tmp_path, {rel: AWK_PARSER}, allow)
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []
    assert "1 site(s), 1 allowlisted; 0 finding(s)" in out


def test_an_entry_matches_its_site_whatever_its_whitespace(tmp_path):
    rel = "team/pgai_agent_kanban/w.py"
    root = _make_tree(
        tmp_path,
        {rel: 'HEAD_RE   =  re.compile(r"^##\\s+Known Issues")\n'},
        # Fields split by runs of spaces rather than tabs, code re-spaced.
        f'{rel}  section-writer  writes it  HEAD_RE = re.compile(r"^##\\s+Known Issues")\n',
    )
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []


def test_an_entry_for_a_missing_file_is_reported_for_removal(tmp_path):
    allow = _entry("team/scripts/gone.sh", "presence-check", "was a presence check", "grep -q '^## Queue' \"$f\"")
    root = _make_tree(tmp_path, {}, allow)
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("stale", "team/scripts/gone.sh", 0)]
    assert "remove the entry" in findings[0]["summary"]
    assert "line 1" in findings[0]["summary"]


def test_an_entry_whose_line_changed_is_stale_and_the_new_line_unlisted(tmp_path):
    rel = "team/pgai_agent_kanban/ops/new.py"
    allow = _entry(rel, "section-writer", "fixture", 'if line.startswith("## Status"):')
    root = _make_tree(tmp_path, {rel: 'if line.startswith("## State"):\n    pass\n'}, allow)
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("stale", rel, 0), ("unlisted", rel, 1)]


def test_an_entry_for_an_exempt_file_is_stale(tmp_path):
    rel = "team/pgai_agent_kanban/task_status.py"
    allow = _entry(rel, "section-writer", "the reader", 'if line.startswith("## State"):')
    root = _make_tree(tmp_path, {rel: 'if line.startswith("## State"):\n    pass\n'}, allow)
    _rc, _out, findings = _run(root, tmp_path)
    assert _kinds(findings) == [("stale", rel, 0)]


def test_two_identical_lines_need_two_entries(tmp_path):
    rel = "team/scripts/two.py"
    line = 'if stripped.startswith("## "):'
    body = f"def a(s):\n    {line}\n        return 1\n\n\ndef b(s):\n    {line}\n        return 2\n"
    one = _entry(rel, "section-writer", "fixture", line)
    root = _make_tree(tmp_path, {rel: body}, one)
    _rc, _out, findings = _run(root, tmp_path)
    assert _kinds(findings) == [("unlisted", rel, 7)]

    (root / "team" / "config" / "section-parser-allowlist.txt").write_text(one + one, encoding="utf-8")
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []


@pytest.mark.parametrize(
    "line, why",
    [
        pytest.param("team/scripts/a.sh\tpresence-check\tno code field\n", "with every field filled", id="three_fields"),
        pytest.param("team/scripts/a.sh\tvalue-reader\treads State\tgrep '^## State' f\n", "not one of", id="unknown_class"),
        pytest.param("team/scripts/a.sh\tpresence-check\t\t\tgrep '^## State' f\n", "with every field filled", id="empty_reason"),
    ],
)
def test_malformed_allowlist_lines_are_reported_and_ignored(tmp_path, line, why):
    root = _make_tree(tmp_path, {}, "# header\n" + line)
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert [(f["kind"], f["line"]) for f in findings] == [("allowlist-malformed", 2)]
    assert why in findings[0]["summary"]


def test_a_missing_allowlist_is_reported_and_every_site_is_unlisted(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/lib/new.sh": AWK_PARSER}, allowlist=None)
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert sorted(f["kind"] for f in findings) == ["allowlist-missing", "unlisted"]


def test_list_sites_prints_every_site_and_whether_it_is_listed(tmp_path):
    rel = "team/scripts/lib/new.sh"
    code = """state="$(awk '/^## State/{f=1;next} f&&/^## /{exit} f&&NF{print;exit}' "$1")\""""
    root = _make_tree(
        tmp_path,
        {rel: AWK_PARSER, "team/pm-agent/p.py": PY_PARSER},
        _entry(rel, "presence-check", "fixture", code),
    )
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--list-sites"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    rows = [line.split("\t") for line in proc.stdout.splitlines()]
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("team/pm-agent/p.py:4", "startswith", "UNLISTED"),
        ("team/pm-agent/p.py:7", "startswith", "UNLISTED"),
        ("team/scripts/lib/new.sh:2", "anchored", "listed"),
    ]


# ---------------------------------------------------------------------------
# 4. Exit code, findings file, --help
# ---------------------------------------------------------------------------


def test_a_clean_tree_writes_an_empty_findings_file_and_exits_zero(tmp_path):
    root = _make_tree(tmp_path, {"team/pgai_agent_kanban/ops/clean.py": READER_CALL})
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []
    assert "0 finding(s)" in out


def test_a_missing_scan_directory_is_reported_and_exits_zero(tmp_path):
    root = _make_tree(tmp_path, {})
    rc, _out, findings = _run(root, tmp_path, "--python-dir", "team/nowhere")
    assert rc == 0
    assert [f["kind"] for f in findings] == ["scan-dir-missing"]


def test_help_prints_usage_and_exits_zero():
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0
    assert proc.stdout.startswith("usage: lint_section_parsers")
    assert "--findings-file" in proc.stdout and "--list-sites" in proc.stdout


def test_the_tree_as_it_stands_is_clean(tmp_path):
    findings_file = tmp_path / "tree.json"
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--findings-file", str(findings_file)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0
    assert json.loads(findings_file.read_text(encoding="utf-8")) == [], proc.stdout


# ---------------------------------------------------------------------------
# 5. Runner wiring
# ---------------------------------------------------------------------------

_BLOCK_START = 'echo "Running section-parser ratchet lint..."'
_BLOCK_END_RE = re.compile(r"^unset _R[UI]_SECTION_PARSERS_FINDINGS\s*$", re.M)


def _runner_block(runner: Path) -> str:
    text = runner.read_text(encoding="utf-8")
    start = text.find(_BLOCK_START)
    assert start != -1, f"{runner.name} does not run the section-parser lint"
    end = _BLOCK_END_RE.search(text, start)
    assert end is not None, f"{runner.name}: section-parser lint block has no closing unset"
    return text[start : end.end()]


@pytest.mark.parametrize("runner", _RUNNERS, ids=lambda p: p.name)
def test_runner_wiring_records_a_seeded_finding_and_returns_zero(tmp_path, runner):
    """Run the runner's own wiring block against a seeded fixture tree.

    The fixture's ``team/scripts`` carries a copy of the lint (it self-locates
    its root from its own path) plus one seeded awk parser; the block runs
    from the fixture's ``team`` directory, as the runner runs from its own.
    """
    root = _make_tree(tmp_path, {"team/scripts/seeded.sh": AWK_PARSER})
    shutil.copy2(_LINT_SCRIPT, root / "team" / "scripts" / "lint_section_parsers.py")
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
    ours = [f for f in data["findings"] if f.get("lint") == "section_parsers"]
    assert [(f["kind"], f["file"]) for f in ours] == [("unlisted", "team/scripts/seeded.sh")]
