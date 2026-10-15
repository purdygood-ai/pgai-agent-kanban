"""
test_lint_inline_python.py — Unit tests for team/scripts/lint_inline_python.py.

Every test builds its own fixture tree under pytest's ``tmp_path``: a
``team/scripts`` directory holding fixture ``.sh`` files and a
``team/config/inline-python-allowlist.txt``. The lint is pointed at it with
``--root``, so nothing here reads this dev tree's own scripts or allowlist.

Coverage:

  1. Each finding kind the ratchet relies on is reported: an unlisted file
     with one new heredoc, a listed file above its counts, a listed file
     below its counts ("tighten the entry"), and an unquoted ``<<PY``
     delimiter reported even when the file is within its counts.
  2. Counting: ``python3 - \\`` backslash-continued heredocs and
     ``python3 -c`` programs (single-line and multi-line) are counted;
     non-Python heredocs are not, and their bodies are skipped; files under
     a ``tests`` directory are not scanned.
  3. The lint exits 0 whatever it finds and writes its findings file.
  4. The exact wiring block both gated runners carry, run against a seeded
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
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_inline_python.py"
_GATED_RUNNER_LIB = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_RUNNERS = (
    _TEAM_DIR / "scripts" / "run-unit-tests.sh",
    _TEAM_DIR / "scripts" / "run-integration-tests.sh",
)

_spec = importlib.util.spec_from_file_location("lint_inline_python", _LINT_SCRIPT)
assert _spec is not None and _spec.loader is not None
lint_inline_python = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_inline_python)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

ONE_HEREDOC = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    python3 - "$1" <<'PY'
    import sys
    print(sys.argv[1])
    PY
    """
)

TWO_HEREDOCS = ONE_HEREDOC + textwrap.dedent(
    """\
    python3 - <<'PY'
    print("second")
    PY
    """
)


def _make_tree(tmp_path: Path, scripts: dict[str, str], allowlist: str = "") -> Path:
    """Build ``<root>/team/scripts/<name>`` files and the allowlist; return root."""
    root = tmp_path / "repo"
    (root / "team" / "scripts").mkdir(parents=True, exist_ok=True)
    for rel, body in scripts.items():
        p = root / "team" / "scripts" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    cfg = root / "team" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "inline-python-allowlist.txt").write_text(allowlist, encoding="utf-8")
    return root


def _run(root: Path, tmp_path: Path) -> tuple[int, str, list[dict]]:
    """Run the lint against *root*; return (exit code, stdout, findings)."""
    findings_file = tmp_path / "findings.json"
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--findings-file", str(findings_file)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    return proc.returncode, proc.stdout, findings


def _kinds(findings: list[dict]) -> list[tuple[str, str]]:
    return sorted((f["kind"], f["file"]) for f in findings)


# ---------------------------------------------------------------------------
# 1. Finding kinds
# ---------------------------------------------------------------------------


def test_unlisted_file_with_one_new_heredoc_is_reported(tmp_path):
    root = _make_tree(tmp_path, {"new.sh": ONE_HEREDOC})
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("unlisted", "team/scripts/new.sh")]
    assert findings[0]["line"] == 2
    assert findings[0]["lint"] == "inline_python"
    assert findings[0]["severity"] == "quality"
    assert "[unlisted]" in out


def test_listed_file_above_its_counts_is_reported(tmp_path):
    root = _make_tree(
        tmp_path,
        {"grown.sh": TWO_HEREDOCS},
        allowlist="team/scripts/grown.sh\t1\t2\n",
    )
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("above", "team/scripts/grown.sh")]
    assert "2 block(s) / 3 python line(s), above its allowlist entry of 1 / 2" in findings[0]["summary"]


def test_listed_file_below_its_counts_asks_to_tighten_the_entry(tmp_path):
    root = _make_tree(
        tmp_path,
        {"shrunk.sh": ONE_HEREDOC},
        allowlist="team/scripts/shrunk.sh\t2\t3\n",
    )
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("below", "team/scripts/shrunk.sh")]
    assert "tighten the entry" in findings[0]["summary"]
    assert "team/scripts/shrunk.sh\t1\t2" in findings[0]["summary"]


def test_allowlist_entry_for_a_missing_file_asks_to_remove_it(tmp_path):
    root = _make_tree(tmp_path, {}, allowlist="team/scripts/gone.sh\t1\t5\n")
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("below", "team/scripts/gone.sh")]
    assert "tighten the entry (remove it)" in findings[0]["summary"]


def test_unquoted_delimiter_is_reported_even_within_counts(tmp_path):
    body = "#!/usr/bin/env bash\npython3 - <<PY\nprint('$HOME')\nPY\n"
    root = _make_tree(tmp_path, {"unq.sh": body}, allowlist="team/scripts/unq.sh\t1\t1\n")
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("unquoted-delimiter", "team/scripts/unq.sh")]
    assert findings[0]["line"] == 2
    assert "<<PY" in findings[0]["summary"]


def test_quoted_and_escaped_delimiters_are_not_reported(tmp_path):
    body = (
        "python3 - <<'PY'\nx = 1\nPY\n"
        'python3 - <<"PY"\nx = 2\nPY\n'
        "python3 - <<\\PY\nx = 3\nPY\n"
    )
    root = _make_tree(tmp_path, {"q.sh": body}, allowlist="team/scripts/q.sh\t3\t3\n")
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []


def test_tree_matching_its_allowlist_is_clean(tmp_path):
    root = _make_tree(tmp_path, {"same.sh": ONE_HEREDOC}, allowlist="team/scripts/same.sh\t1\t2\n")
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert findings == []
    assert "totals: 1 file(s) scanned, 1 block(s) (1 heredoc, 0 python3 -c), 2 python line(s); 0 finding(s)" in out


def test_malformed_allowlist_line_is_reported_and_ignored(tmp_path):
    root = _make_tree(
        tmp_path,
        {"a.sh": ONE_HEREDOC},
        allowlist="# comment\n\nteam/scripts/a.sh   1   2  \nnot a valid line\n",
    )
    rc, _out, findings = _run(root, tmp_path)
    assert rc == 0
    assert [f["kind"] for f in findings] == ["allowlist-malformed"]
    assert findings[0]["line"] == 4


# ---------------------------------------------------------------------------
# 2. Counting
# ---------------------------------------------------------------------------


def test_backslash_continued_python_heredoc_is_counted():
    text = textwrap.dedent(
        """\
        python3 - \\
            "$ARG_ONE" \\
            "$ARG_TWO" \\
        <<'PY_EOF'
        import sys
        print(sys.argv)
        PY_EOF
        echo after
        """
    )
    blocks = lint_inline_python.scan_text(text)
    assert [(b.kind, b.line, b.python_lines) for b in blocks] == [("heredoc", 4, 2)]


def test_dev_stdin_heredoc_is_counted():
    text = "python3 /dev/stdin \"$a\" \\\n    <<'PY_X'\nprint(1)\nPY_X\n"
    blocks = lint_inline_python.scan_text(text)
    assert [(b.kind, b.python_lines) for b in blocks] == [("heredoc", 1)]


def test_python_dash_c_one_liners_and_multi_line_programs_are_counted():
    text = textwrap.dedent(
        """\
        a="$(python3 -c 'import sys; print(1)')"
        if python3 -c "import pytest_cov" 2>/dev/null; then :; fi
        b="$(python3 -c '
        import json
        print(json.dumps({}))
        ' "$x")"
        PYTHONPATH=lib python3 -c "${PROGRAM}"
        """
    )
    blocks = lint_inline_python.scan_text(text)
    assert [(b.kind, b.line, b.python_lines) for b in blocks] == [
        ("dash-c", 1, 1),
        ("dash-c", 2, 1),
        ("dash-c", 3, 2),
        ("dash-c", 7, 1),
    ]


def test_non_python_heredocs_are_not_counted_and_their_bodies_are_skipped():
    text = textwrap.dedent(
        """\
        cat > hello.py <<'PY'
        print("hello")
        PY
        cat <<EOF
        python3 -c 'print(1)'
        python3 - <<'PY'
        EOF
        python3 "$SCRIPT" <<EOF
        {"data": 1}
        EOF
        # python3 - <<'PY' in a comment
        x=$(( 1 << 3 ))
        cat <<<"python3 -"
        """
    )
    assert lint_inline_python.scan_text(text) == []


def test_python_heredoc_body_containing_dash_c_text_counts_once():
    text = "python3 - <<'PY'\ncmd = \"python3 -c 'x'\"\nPY\n"
    blocks = lint_inline_python.scan_text(text)
    assert [(b.kind, b.python_lines) for b in blocks] == [("heredoc", 1)]


def test_files_under_test_directories_are_not_scanned(tmp_path):
    root = _make_tree(
        tmp_path,
        {
            "lib/checks/tests/test-thing.sh": ONE_HEREDOC,
            "test/helper.sh": ONE_HEREDOC,
            "test-named-but-not-in-a-test-dir.sh": ONE_HEREDOC,
        },
    )
    rc, out, findings = _run(root, tmp_path)
    assert rc == 0
    assert _kinds(findings) == [("unlisted", "team/scripts/test-named-but-not-in-a-test-dir.sh")]
    assert "totals: 1 file(s) scanned" in out


def test_emit_allowlist_prints_measured_counts(tmp_path):
    root = _make_tree(tmp_path, {"b.sh": TWO_HEREDOCS, "clean.sh": "echo hi\n"})
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--emit-allowlist"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert proc.stdout == "team/scripts/b.sh\t2\t3\n"


# ---------------------------------------------------------------------------
# 4. Runner wiring
# ---------------------------------------------------------------------------

_BLOCK_START = 'echo "Running inline-Python ratchet lint..."'
_BLOCK_END_RE = re.compile(r"^unset _R[UI]_INLINE_PYTHON_FINDINGS\s*$", re.M)


def _runner_block(runner: Path) -> str:
    text = runner.read_text(encoding="utf-8")
    start = text.find(_BLOCK_START)
    assert start != -1, f"{runner.name} does not run the inline-Python lint"
    end = _BLOCK_END_RE.search(text, start)
    assert end is not None, f"{runner.name}: inline-Python lint block has no closing unset"
    return text[start : end.end()]


@pytest.mark.parametrize("runner", _RUNNERS, ids=lambda p: p.name)
def test_runner_wiring_records_a_seeded_finding_and_returns_zero(tmp_path, runner):
    """Run the runner's own wiring block against a seeded fixture tree.

    The fixture's ``team/scripts`` carries a copy of the lint (it self-locates
    its root from its own path) plus one unlisted heredoc; the block runs from
    the fixture's ``team`` directory, as the runner runs from its own.
    """
    root = _make_tree(tmp_path, {"seeded.sh": ONE_HEREDOC})
    shutil.copy2(_LINT_SCRIPT, root / "team" / "scripts" / "lint_inline_python.py")
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
    ours = [f for f in data["findings"] if f.get("lint") == "inline_python"]
    assert [(f["kind"], f["file"]) for f in ours] == [("unlisted", "team/scripts/seeded.sh")]
