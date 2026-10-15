"""
test_lint_echo_pipe_grep_q.py — Unit tests for team/scripts/lint_echo_pipe_grep_q.py.

Every command-line test builds its own fixture tree under pytest's
``tmp_path``: a ``team/`` directory holding fixture bash files, an optional
``install.sh`` beside it, and ``team/config/echo-pipe-grep-q-allowlist.txt``.
The lint is pointed at it with ``--root``, so nothing here reads this dev
tree's own scripts or allowlist.

Coverage:

  1. The race shape is reported: ``echo "$x" | grep -q y`` and a
     ``printf ... | grep -qE`` variant in an unlisted file each give one
     finding naming its line. A here-string, a ``[[ =~ ]]`` match, and the
     same race shape inside a ``tests/`` path or a ``test-*.sh`` /
     ``test_*.sh`` file are not reported.
  2. The scanner reads shell syntax: continued lines, a pipe ending a line,
     command substitutions (also inside double quotes), intermediate
     stages and every quiet spelling are found; a pipe in a string, ``||``,
     a comment, a heredoc body, a non-quiet grep and a ``q`` that is an
     option's argument are not.
  3. Which files are bash: ``*.sh``, ``install.sh`` at the root, and an
     extension-less file with a bash shebang; a Python file is not.
  4. The ratchet: a listed file above its count, below it ("tighten"), an
     entry for a file that is not scanned, a malformed line and a missing
     allowlist are each reported; a tree matching its allowlist is clean.
  5. The lint exits 0 whatever it finds and always writes its findings
     file; ``--emit-allowlist`` prints the measured counts.
  6. The wiring block both gated runners carry, run against a seeded tree
     with the real ``gated_runner_lint.sh``, records the seeded finding and
     returns 0; the pre-merge engine's ratchet check over the same seeded
     tree fails, naming the file and line, and passes on a clean tree.
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

from pgai_agent_kanban.testing import pre_merge
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_echo_pipe_grep_q.py"
_GATED_RUNNER_LIB = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_RUNNERS = (
    _TEAM_DIR / "scripts" / "run-unit-tests.sh",
    _TEAM_DIR / "scripts" / "run-integration-tests.sh",
)
_ALLOWLIST_REL = "team/config/echo-pipe-grep-q-allowlist.txt"

_spec = importlib.util.spec_from_file_location("lint_echo_pipe_grep_q", _LINT_SCRIPT)
assert _spec is not None and _spec.loader is not None
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

# Line 3 and line 4 are the two race sites; the rest test the same text
# without a pipe and must never be reported.
SEEDED = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    if echo "$x" | grep -q y; then :; fi
    printf '%s\\n' "$x" | grep -qE '^v[0-9]+'
    grep -q y <<< "$x"
    [[ $x =~ ^v[0-9]+ ]]
    """
)

CLEAN = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    grep -qE '^v[0-9]+' <<< "$x"
    [[ $x =~ y ]] && echo yes
    """
)

ONE_SITE = '#!/usr/bin/env bash\necho "$x" | grep -q y\n'


def _make_tree(
    tmp_path: Path, files: dict[str, str], allowlist: str | None = "# fixture allowlist\n",
) -> Path:
    """Build ``<root>/<rel>`` for each of *files* and the allowlist (omitted
    when *allowlist* is None); return the root."""
    root = tmp_path / "repo"
    (root / "team" / "config").mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    if allowlist is not None:
        (root / _ALLOWLIST_REL).write_text(allowlist, encoding="utf-8")
    return root


def _run(root: Path, tmp_path: Path, *extra: str) -> tuple[int, str, list[dict]]:
    """Run the lint against *root*; return (exit code, stdout, findings)."""
    findings_file = tmp_path / "findings.json"
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root),
         "--findings-file", str(findings_file), *extra],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    return proc.returncode, proc.stdout, findings


def _sites(text: str) -> list[int]:
    return [site.line for site in lint.scan_text(text)]


# ---------------------------------------------------------------------------
# 1. The race shape, and what is not it
# ---------------------------------------------------------------------------


def test_echo_and_printf_into_quiet_grep_are_both_reported(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/seeded.sh": SEEDED})

    _rc, out, findings = _run(root, tmp_path)

    assert [(f["kind"], f["file"], f["line"]) for f in findings] == [
        ("unlisted", "team/scripts/seeded.sh", 3),
        ("unlisted", "team/scripts/seeded.sh", 4),
    ], out
    assert "pipes echo into a quiet grep" in findings[0]["summary"]
    assert "pipes printf into a quiet grep" in findings[1]["summary"]
    assert all("<<<" in f["remedy"] for f in findings)
    assert all(f["lint"] == "echo_pipe_grep_q" and f["severity"] == "quality" for f in findings)


def test_here_string_and_bash_regex_forms_are_not_reported():
    assert _sites(CLEAN) == []
    assert _sites('grep -qiE "a|b" <<< "$(printf \'%s\\n\' "$x")"\n') == []


def test_sites_in_test_paths_are_not_scanned(tmp_path):
    root = _make_tree(tmp_path, {
        "team/scripts/lib/checks/tests/check-thing.sh": ONE_SITE,
        "team/tests/unit/helper.sh": ONE_SITE,
        "team/scripts/test-sweep-runner.sh": ONE_SITE,
        "team/scripts/test_helper.sh": ONE_SITE,
        "team/scripts/contest-entry.sh": ONE_SITE,
    })

    _rc, out, findings = _run(root, tmp_path)

    assert [f["file"] for f in findings] == ["team/scripts/contest-entry.sh"], out
    assert "totals: 1 file(s) scanned" in out


# ---------------------------------------------------------------------------
# 2. Shell syntax
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    'echo "$x" \\\n  | grep -q y\n',
    'echo "$x" |\n  grep -q y\n',
    'r=$(echo "$x" | grep -q y && echo ok)\n',
    'z="$(printf %s "$x" | /usr/bin/grep -sq y)"\n',
    'v=`echo "$x" | grep -q y`\n',
    'echo "$x" | tr a b | grep --quiet y\n',
    'echo "$x" | grep --silent y\n',
    'echo "$x" | egrep -iq y\n',
    '! echo "$x" | LC_ALL=C command grep -F -q y\n',
    'echo "$x" | grep y -q\n',
    'while read -r l; do echo "$l" | grep -q y; done < f\n',
    'case "$x" in a) echo "$x" | grep -q y ;; esac\n',
    'echo "${x//|/,}" | grep -q y\n',
    'diff <(echo a | grep -q b) /dev/null\n',
], ids=lambda t: t.strip().replace("\n", " ")[:40])
def test_race_shapes_written_any_way_are_reported(text):
    assert _sites(text) == [1], text


@pytest.mark.parametrize("text", [
    'echo "a | grep -q y"\n',
    "echo 'x' || grep -q y file\n",
    '# echo "$x" | grep -q y\n',
    'cat <<EOF\necho "$x" | grep -q y\nEOF\n',
    "cat <<-'EOF'\n\techo \"$x\" | grep -q y\n\tEOF\n",
    'echo "$x" | grep -c y\n',
    'echo "$x" | grep y > /dev/null\n',
    'echo "$x" | grep -e q y\n',
    'echo "$x" | grep -eq\n',
    'echo "$x" | grep y -- -q\n',
    '(( n = 1 << 2 ))\necho done\n',
    'echo "$x"; grep -q y file\n',
    '[[ $x =~ ^(echo|grep)$ ]]\n',
], ids=lambda t: t.strip().replace("\n", " ")[:40])
def test_text_that_is_not_a_pipeline_into_quiet_grep_is_not_reported(text):
    assert _sites(text) == [], text


def test_a_heredoc_body_does_not_hide_the_lines_after_it():
    text = 'cat <<EOF\nbody | grep -q x\nEOF\necho "$x" | grep -q y\n'
    assert _sites(text) == [4]


def test_grep_is_quiet_reads_option_clusters():
    assert lint.grep_is_quiet(["-qE", "pat"])
    assert lint.grep_is_quiet(["-Eiq", "pat"])
    assert lint.grep_is_quiet(["--quiet", "pat"])
    assert not lint.grep_is_quiet(["-E", "pat"])
    assert not lint.grep_is_quiet(["-eq"])
    assert not lint.grep_is_quiet(["-e", "-q"])
    assert not lint.grep_is_quiet(["--", "-q"])


# ---------------------------------------------------------------------------
# 3. Which files are bash
# ---------------------------------------------------------------------------


def test_install_sh_and_shebang_scripts_are_scanned_and_python_is_not(tmp_path):
    root = _make_tree(tmp_path, {
        "install.sh": ONE_SITE,
        "team/scripts/wake/guard/git": ONE_SITE,
        "team/scripts/tool.py": '#!/usr/bin/env bash\necho "$x" | grep -q y\n',
        "team/scripts/notes.txt": ONE_SITE,
    })

    _rc, out, findings = _run(root, tmp_path)

    assert sorted(f["file"] for f in findings) == ["install.sh", "team/scripts/wake/guard/git"], out


# ---------------------------------------------------------------------------
# 4. The ratchet
# ---------------------------------------------------------------------------


def test_listed_file_above_its_count_reports_every_site(tmp_path):
    root = _make_tree(
        tmp_path, {"team/scripts/seeded.sh": SEEDED},
        allowlist="team/scripts/seeded.sh\t1\n",
    )

    _rc, _out, findings = _run(root, tmp_path)

    assert [(f["kind"], f["line"]) for f in findings] == [("above", 3), ("above", 4)]
    assert "above its allowlist entry of 1" in findings[0]["summary"]


def test_tree_matching_its_allowlist_is_clean(tmp_path):
    root = _make_tree(
        tmp_path, {"team/scripts/seeded.sh": SEEDED},
        allowlist="# header\n\nteam/scripts/seeded.sh   2\n",
    )

    _rc, out, findings = _run(root, tmp_path)

    assert findings == []
    assert "totals: 1 file(s) scanned, 2 site(s); 0 finding(s)" in out


def test_listed_file_below_its_count_asks_to_tighten_the_entry(tmp_path):
    root = _make_tree(
        tmp_path, {"team/scripts/seeded.sh": SEEDED, "team/scripts/clean.sh": CLEAN},
        allowlist="team/scripts/seeded.sh\t3\nteam/scripts/clean.sh\t1\n",
    )

    _rc, _out, findings = _run(root, tmp_path)

    by_file = {f["file"]: f for f in findings}
    assert sorted(by_file) == ["team/scripts/clean.sh", "team/scripts/seeded.sh"]
    assert all(f["kind"] == "below" for f in findings)
    assert "team/scripts/seeded.sh\t2" in by_file["team/scripts/seeded.sh"]["summary"]
    assert "remove it" in by_file["team/scripts/clean.sh"]["summary"]


def test_entry_for_a_file_that_is_not_scanned_asks_to_remove_it(tmp_path):
    root = _make_tree(tmp_path, {}, allowlist="team/scripts/gone.sh\t1\n")

    _rc, _out, findings = _run(root, tmp_path)

    assert [(f["kind"], f["file"]) for f in findings] == [("below", "team/scripts/gone.sh")]
    assert "not scanned" in findings[0]["summary"]


def test_malformed_allowlist_line_is_reported_and_ignored(tmp_path):
    root = _make_tree(
        tmp_path, {"team/scripts/clean.sh": CLEAN},
        allowlist="team/scripts/clean.sh\tmany\n",
    )

    _rc, _out, findings = _run(root, tmp_path)

    assert [(f["kind"], f["file"], f["line"]) for f in findings] == [
        ("allowlist-malformed", _ALLOWLIST_REL, 1),
    ]


def test_missing_allowlist_is_reported_and_every_site_is_unlisted(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/one.sh": ONE_SITE}, allowlist=None)

    _rc, _out, findings = _run(root, tmp_path)

    assert [f["kind"] for f in findings] == ["allowlist-missing", "unlisted"]


# ---------------------------------------------------------------------------
# 5. Exit code, findings file, --emit-allowlist
# ---------------------------------------------------------------------------


def test_lint_exits_zero_and_writes_an_empty_findings_file_when_clean(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/clean.sh": CLEAN})

    rc, out, findings = _run(root, tmp_path)

    assert rc == 0 and findings == []
    assert "0 finding(s)" in out


def test_emit_allowlist_prints_measured_counts(tmp_path):
    root = _make_tree(tmp_path, {"team/scripts/seeded.sh": SEEDED, "team/scripts/clean.sh": CLEAN})
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--emit-allowlist"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "team/scripts/seeded.sh\t2\n"


def test_help_exits_zero_and_names_the_race_free_forms():
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True, text=True, timeout=60, cwd=str(_TEAM_DIR),
    )
    assert proc.returncode == 0, proc.stderr
    assert "<<<" in proc.stdout and "=~" in proc.stdout


# ---------------------------------------------------------------------------
# 6. Runner wiring and the pre-merge ratchet check
# ---------------------------------------------------------------------------

_BLOCK_START = 'echo "Running echo/printf-into-quiet-grep ratchet lint..."'
_BLOCK_END_RE = re.compile(r"^unset _R[UI]_ECHO_PIPE_GREP_Q_FINDINGS\s*$", re.M)


def _runner_block(runner: Path) -> str:
    text = runner.read_text(encoding="utf-8")
    start = text.find(_BLOCK_START)
    assert start != -1, f"{runner.name} does not run the echo/printf-into-quiet-grep lint"
    end = _BLOCK_END_RE.search(text, start)
    assert end is not None, f"{runner.name}: the lint's block has no closing unset"
    return text[start:end.end()]


def _seeded_tree_with_lint(tmp_path: Path, files: dict[str, str]) -> Path:
    """A fixture tree carrying a copy of the lint, which self-locates its
    root from its own path, as it does in the real tree."""
    root = _make_tree(tmp_path, files)
    shutil.copy2(_LINT_SCRIPT, root / "team" / "scripts" / "lint_echo_pipe_grep_q.py")
    return root


@pytest.mark.parametrize("runner", _RUNNERS, ids=lambda p: p.name)
def test_runner_wiring_records_a_seeded_finding_and_returns_zero(tmp_path, runner):
    root = _seeded_tree_with_lint(tmp_path, {"team/scripts/seeded.sh": ONE_SITE})
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
        ["bash", str(driver)], cwd=root / "team", env=env,
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "block-exit=0" in proc.stdout
    data = json.loads(out_file.read_text(encoding="utf-8"))
    ours = [f for f in data["findings"] if f.get("lint") == "echo_pipe_grep_q"]
    assert [(f["kind"], f["file"], f["line"]) for f in ours] == [
        ("unlisted", "team/scripts/seeded.sh", 2),
    ], proc.stdout + proc.stderr


def _pre_merge_lint() -> pre_merge.RatchetLint:
    return next(r for r in pre_merge.RATCHET_LINTS if r.script == "scripts/lint_echo_pipe_grep_q.py")


def test_pre_merge_ratchet_check_fails_on_a_new_site_naming_file_and_line(tmp_path):
    root = _seeded_tree_with_lint(tmp_path, {"team/scripts/seeded.sh": ONE_SITE})
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    result = pre_merge.check_ratchet_lint(root / "team", _pre_merge_lint(), scratch=scratch)

    assert not result.passed, result.detail
    assert result.note == "1 finding(s)", result.detail
    assert len(result.detail) == 1 and result.detail[0].startswith(
        "[unlisted] team/scripts/seeded.sh:2 pipes echo into a quiet grep"
    ), result.detail


def test_pre_merge_ratchet_check_passes_on_a_clean_tree(tmp_path):
    root = _seeded_tree_with_lint(tmp_path, {"team/scripts/clean.sh": CLEAN})
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    result = pre_merge.check_ratchet_lint(root / "team", _pre_merge_lint(), scratch=scratch)

    assert result.passed, result.detail
