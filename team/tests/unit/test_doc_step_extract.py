"""
test_doc_step_extract.py
========================
Tagged-fence handling in team/scripts/harness/lib/doc_step_extract.py, the
extractor strangers-path-check.sh uses to pull a documented step's commands
out of a Markdown file and run them for real.

The specification pinned here: every fenced block is paired with its own
closer per CommonMark whatever its info string, so a ```text / ```ini /
~~~python block before a shell block never shifts block_fence_line, never
leaks a literal fence line into the commands, and never changes which block
--block-index selects. Only blocks tagged bash or sh are shell blocks; a bare
fence is not.

Every case drives the shipped script as a real subprocess from a working
directory outside the repository (the scratch directory holding the fixture
document), under an environment built from an explicit set, and asserts the
JSON output contract the consumer reads: block_fence_line, the commands with
their line numbers, and which block --block-index returns.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# Self-located, never derived from an environment variable or from git.
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_EXTRACTOR = _TEAM_DIR / "scripts" / "harness" / "lib" / "doc_step_extract.py"


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    """Render a subprocess result's full stdout+stderr for an assertion message."""
    return f"--- exit {result.returncode} ---\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _write_doc(tmp_path: pathlib.Path, lines: list[str]) -> pathlib.Path:
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir(exist_ok=True)
    doc = doc_dir / "guide.md"
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return doc


def _line_of(lines: list[str], text: str) -> int:
    """1-based line number of the single fixture line equal to *text*."""
    hits = [i + 1 for i, line in enumerate(lines) if line == text]
    assert len(hits) == 1, f"fixture line {text!r} must be unique; found at {hits}"
    return hits[0]


def _extract(
    tmp_path: pathlib.Path, doc: pathlib.Path, heading: str, *extra: str
) -> subprocess.CompletedProcess:
    """Run the shipped extractor from a foreign cwd under a scrubbed environment."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(home_dir=home, extra_path_dirs=(pathlib.Path(sys.executable).parent,))
    return subprocess.run(
        [sys.executable, str(_EXTRACTOR), doc.name, heading, *extra],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(doc.parent),
        env=env,
    )


def _extract_json(tmp_path: pathlib.Path, doc: pathlib.Path, heading: str, block_index: int) -> dict:
    result = _extract(tmp_path, doc, heading, "--block-index", str(block_index))
    assert result.returncode == 0, _diagnostic(result)
    return json.loads(result.stdout)


# A step whose shell blocks come after three non-shell blocks of different
# fence styles, one of them holding a '#' line that reads like a level-1
# heading, followed by a later step whose block must never be picked up.
_TAGGED_BEFORE_SHELL = [
    "# Guide",
    "",
    "## Step 1",
    "",
    "```text",
    "example output",
    "```",
    "",
    "```ini",
    "[section]",
    "key = value",
    "```",
    "",
    "~~~python",
    "# a comment that reads like a level-1 heading",
    'print("not a command")',
    "~~~",
    "",
    "```bash",
    "echo first-shell",
    "```",
    "",
    "```sh",
    "echo second-shell",
    "```",
    "",
    "## Step 2",
    "",
    "~~~bash",
    "echo next-step",
    "~~~",
]


class TestTaggedFencesBeforeAShellBlock:
    def test_text_block_before_bash_block_returns_the_bash_block(self, tmp_path: pathlib.Path) -> None:
        lines = [
            "# Guide",
            "",
            "## Step 1",
            "",
            "```text",
            "example output",
            "```",
            "",
            "```bash",
            "echo real-command",
            "```",
        ]
        doc = _write_doc(tmp_path, lines)
        out = _extract_json(tmp_path, doc, "Step 1", 0)
        assert out["block_fence_line"] == 9, out
        assert out["commands"] == [{"cmd": "echo real-command", "line": 10}], out

    def test_block_index_zero_skips_text_ini_and_tilde_python_blocks(self, tmp_path: pathlib.Path) -> None:
        doc = _write_doc(tmp_path, _TAGGED_BEFORE_SHELL)
        out = _extract_json(tmp_path, doc, "Step 1", 0)
        assert out["block_fence_line"] == _line_of(_TAGGED_BEFORE_SHELL, "```bash"), out
        assert out["commands"] == [
            {"cmd": "echo first-shell", "line": _line_of(_TAGGED_BEFORE_SHELL, "echo first-shell")}
        ], out

    def test_block_index_one_selects_the_second_shell_block(self, tmp_path: pathlib.Path) -> None:
        doc = _write_doc(tmp_path, _TAGGED_BEFORE_SHELL)
        out = _extract_json(tmp_path, doc, "Step 1", 1)
        assert out["block_fence_line"] == _line_of(_TAGGED_BEFORE_SHELL, "```sh"), out
        assert out["commands"] == [
            {"cmd": "echo second-shell", "line": _line_of(_TAGGED_BEFORE_SHELL, "echo second-shell")}
        ], out

    def test_non_shell_blocks_are_not_counted_toward_block_index(self, tmp_path: pathlib.Path) -> None:
        doc = _write_doc(tmp_path, _TAGGED_BEFORE_SHELL)
        result = _extract(tmp_path, doc, "Step 1", "--block-index", "2")
        assert result.returncode == 1, _diagnostic(result)
        assert "only 2 bash/sh fenced block(s)" in result.stderr, _diagnostic(result)

    def test_tsv_output_names_the_same_commands_and_lines(self, tmp_path: pathlib.Path) -> None:
        doc = _write_doc(tmp_path, _TAGGED_BEFORE_SHELL)
        result = _extract(tmp_path, doc, "Step 1", "--block-index", "0", "--tsv")
        assert result.returncode == 0, _diagnostic(result)
        cmd_rows = [row for row in result.stdout.splitlines() if row.startswith("CMD\t")]
        assert cmd_rows == [f"CMD\t{_line_of(_TAGGED_BEFORE_SHELL, 'echo first-shell')}\techo first-shell"], (
            _diagnostic(result)
        )


class TestFenceNesting:
    def test_four_backtick_fence_hides_the_three_backtick_bash_block_it_wraps(
        self, tmp_path: pathlib.Path
    ) -> None:
        lines = [
            "## Step 1",
            "",
            "````markdown",
            "```bash",
            "echo inside-the-example",
            "```",
            "````",
            "",
            "```bash",
            "echo real-outer",
            "```",
        ]
        doc = _write_doc(tmp_path, lines)
        out = _extract_json(tmp_path, doc, "Step 1", 0)
        assert out["block_fence_line"] == 9, out
        assert out["commands"] == [{"cmd": "echo real-outer", "line": 10}], out

        result = _extract(tmp_path, doc, "Step 1", "--block-index", "1")
        assert result.returncode == 1, _diagnostic(result)
        assert "only 1 bash/sh fenced block(s)" in result.stderr, _diagnostic(result)

    def test_four_backtick_bash_fence_closes_only_on_four_or_more_backticks(
        self, tmp_path: pathlib.Path
    ) -> None:
        lines = [
            "## Step 1",
            "",
            "````bash",
            "echo before",
            "```",
            "echo after",
            "`````",
            "",
            "```bash",
            "echo second",
            "```",
        ]
        doc = _write_doc(tmp_path, lines)
        out = _extract_json(tmp_path, doc, "Step 1", 0)
        assert out["block_fence_line"] == 3, out
        assert out["commands"] == [
            {"cmd": "echo before", "line": 4},
            {"cmd": "```", "line": 5},
            {"cmd": "echo after", "line": 6},
        ], out
        assert _extract_json(tmp_path, doc, "Step 1", 1)["block_fence_line"] == 9

    def test_backtick_line_inside_a_tilde_block_does_not_close_it(self, tmp_path: pathlib.Path) -> None:
        lines = [
            "## Step 1",
            "",
            "~~~text",
            "```",
            "not a command",
            "~~~",
            "",
            "```bash",
            "echo real",
            "```",
        ]
        doc = _write_doc(tmp_path, lines)
        out = _extract_json(tmp_path, doc, "Step 1", 0)
        assert out["block_fence_line"] == 8, out
        assert out["commands"] == [{"cmd": "echo real", "line": 9}], out


class TestWhichFencesAreShellBlocks:
    def test_bare_fence_is_not_a_shell_block(self, tmp_path: pathlib.Path) -> None:
        lines = [
            "## Step 1",
            "",
            "```",
            "echo expected-output-not-a-command",
            "```",
            "",
            "```bash",
            "echo real",
            "```",
        ]
        doc = _write_doc(tmp_path, lines)
        out = _extract_json(tmp_path, doc, "Step 1", 0)
        assert out["block_fence_line"] == 7, out
        assert out["commands"] == [{"cmd": "echo real", "line": 8}], out

        result = _extract(tmp_path, doc, "Step 1", "--block-index", "1")
        assert result.returncode == 1, _diagnostic(result)

    def test_shell_tag_is_read_from_the_info_strings_first_word_in_any_case(
        self, tmp_path: pathlib.Path
    ) -> None:
        lines = [
            "## Step 1",
            "",
            "```bash title=install.sh",
            "echo with-attributes",
            "```",
            "",
            "~~~sh",
            "echo tilde-sh",
            "~~~",
            "",
            "```BASH",
            "echo upper-case",
            "```",
        ]
        doc = _write_doc(tmp_path, lines)
        picked = [_extract_json(tmp_path, doc, "Step 1", i) for i in range(3)]
        assert [p["block_fence_line"] for p in picked] == [3, 7, 11], picked
        assert [p["commands"][0]["cmd"] for p in picked] == [
            "echo with-attributes",
            "echo tilde-sh",
            "echo upper-case",
        ], picked
