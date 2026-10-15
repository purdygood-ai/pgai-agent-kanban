#!/usr/bin/env python3
"""team/scripts/harness/lib/doc_step_extract.py

Line-number-aware fenced-command extractor for one documented step.

Reused by team/scripts/harness/strangers-path-check.sh so a stage's
commands are pulled straight out of a Markdown file rather than pasted
into the fixture by hand — the same "extract, don't transcribe" contract
team/scripts/harness/doc-lint.sh's cmd suite already keeps, applied here to
a single named step instead of the whole audited surface. Unlike doc-lint's
extractor, this one keeps the source line number of every command (so a
caller can name "document and line" in a per-stage report) and does NOT
honor "<!-- doc-lint: skip -->" annotations — doc-lint's skip markers exist
to keep the *lint* from executing commands that need a live daemon or an
interactive terminal; this tool backs a fixture whose entire purpose is to
attempt exactly those commands for real.

Usage
-----
  doc_step_extract.py <file> <heading-substring> [--block-index N] [--tsv]

  <file>               Markdown file to scan.
  <heading-substring>  A substring matched against heading lines (any
                        '#'-prefixed line), case-sensitive. The FIRST
                        heading line containing this substring is used.
  --block-index N       Which fenced bash/sh block AFTER that heading to
                        return (0-based, default 0). Only shell blocks
                        are counted (see "Which fences count" below). The
                        search stops at the next heading of the same or
                        shallower level, so a block under a later heading
                        is never picked up by accident.
  --tsv                 Print tab-separated lines instead of one JSON
                        object, for a bash caller to `read` directly
                        without a second python invocation per field:
                          HEADING<TAB><heading text>
                          HEADING_LINE<TAB><line>
                          CMD<TAB><line><TAB><command text>   (one per command)

Output (stdout, one JSON object — default; see --tsv above for the
bash-friendly alternative)
---------------------------------
  {
    "file": "<file>",
    "heading": "<the matched heading line, stripped of leading '#'s>",
    "heading_line": <1-based line number of the heading>,
    "block_fence_line": <1-based line number of the opening fence>,
    "commands": [
      {"cmd": "<logical command text>", "line": <1-based source line>},
      ...
    ]
  }

Which fences count
------------------
Fence recognition follows CommonMark, through the helper shared with
team/scripts/lint_doc_command_truth.py
(pgai_agent_kanban/lib/markdown_fence.py): ANY run of three or more
backticks or tildes opens a fenced block whatever its info string
(```text, ```ini, ~~~python, ```bash title=x, a bare fence), and the next
line made only of the same character, at least as long, closes it. Lines
are matched with surrounding whitespace stripped, so an indented fence is
recognised too. Every block is paired with its own closer this way, so a
non-shell block never shifts or swallows the shell block after it, and a
'#' line inside any fenced block never ends the step's section early.

Only a block whose info string's first word is `bash` or `sh` (any case) is
a shell block: it alone counts toward --block-index and can be returned.
A BARE fence (no info string) is NOT a shell block — documents use bare
fences for expected output and file contents, and this tool's consumer runs
what it returns for real. Tag a block ```bash to make it extractable.

Backslash-continuation lines are joined into one logical command, whose
reported line number is the line the command STARTS on. Blank lines and
comment-only lines ('#'-prefixed) inside the fence are dropped, matching
doc-lint.sh's own join_continuations()/filter behavior.

Exit codes
----------
  0  block found and printed (commands list may be empty for a block with
     comment-only content — still success, the caller decides what an
     empty stage means)
  1  file not found, heading not found, or no bash/sh fenced block found
     after the matched heading before the next same-or-shallower heading
  2  usage error
"""
import json
import pathlib
import re
import sys

# The pgai_agent_kanban package sits three levels above this file in both
# the dev tree (team/scripts/harness/lib/ -> team/) and the live install
# (scripts/harness/lib/ -> install root), so it is located from this file's
# own path and never from the environment.
_OWN_ROOT = str(pathlib.Path(__file__).resolve().parents[3])
if _OWN_ROOT not in sys.path:
    sys.path.insert(0, _OWN_ROOT)

from pgai_agent_kanban.lib.markdown_fence import (  # noqa: E402
    SHELL_FENCE_LANGS,
    find_fence_close,
    match_fence_opener,
)


def _fail(msg: str) -> None:
    print(f"doc_step_extract.py: {msg}", file=sys.stderr)


def join_continuations(raw_lines):
    """Join backslash-continuation lines; report (text, start_line) pairs.

    start_line is 1-based and refers to raw_lines' own indexing, offset by
    the caller (the caller passes lines already paired with their absolute
    file line number via enumerate, so this works directly on (line, num)
    tuples instead of bare strings).
    """
    joined = []
    buf = ""
    start = None
    for line, num in raw_lines:
        if start is None:
            start = num
        if line.endswith("\\"):
            buf += line[:-1] + " "
        else:
            buf += line
            text = buf.strip()
            if text:
                joined.append((text, start))
            buf = ""
            start = None
    if buf.strip():
        joined.append((buf.strip(), start))
    return joined


def main(argv):
    """Print the commands of one fenced bash block under a document heading.

    ``FILE HEADING_SUBSTRING [--block-index N]``. Exit codes: 0 the block was
    found and printed (possibly with no commands); 1 the file, the heading or a
    bash/sh fenced block under it was not found; 2 usage error.
    """
    if len(argv) < 3:
        _fail("requires <file> <heading-substring> [--block-index N]")
        return 2

    filepath = argv[1]
    heading_substring = argv[2]
    block_index = 0
    tsv = False
    rest = argv[3:]
    i = 0
    while i < len(rest):
        if rest[i] == "--block-index":
            if i + 1 >= len(rest):
                _fail("--block-index requires a value")
                return 2
            try:
                block_index = int(rest[i + 1])
            except ValueError:
                _fail(f"--block-index value must be an integer, got '{rest[i + 1]}'")
                return 2
            i += 2
        elif rest[i] == "--tsv":
            tsv = True
            i += 1
        else:
            _fail(f"unrecognized argument: {rest[i]}")
            return 2

    try:
        text = open(filepath, encoding="utf-8").read()
    except OSError as e:
        _fail(f"cannot read {filepath}: {e}")
        return 1

    lines = text.splitlines()

    heading_re = re.compile(r"^(#+)\s+(.*)$")

    heading_line_no = None
    heading_text = None
    heading_level = None
    for idx, line in enumerate(lines):
        m = heading_re.match(line)
        if m and heading_substring in m.group(2):
            heading_line_no = idx + 1  # 1-based
            heading_text = m.group(2)
            heading_level = len(m.group(1))
            break

    if heading_line_no is None:
        _fail(f"no heading containing '{heading_substring}' found in {filepath}")
        return 1

    # Scan forward from just after the heading, pairing every fenced block
    # with its own closer and keeping the shell ones, until a heading of the
    # same-or-shallower level is hit.
    blocks = []  # list of (fence_line_no, [(raw_line, line_no), ...])
    j = heading_line_no  # 0-based index of the line AFTER the heading (heading_line_no is 1-based == index of next line in 0-based)
    while j < len(lines):
        line = lines[j]
        m = heading_re.match(line)
        if m and len(m.group(1)) <= heading_level:
            break  # left this step's section

        fence_open = match_fence_opener(line.strip())
        if fence_open is not None:
            fence_token, lang = fence_open
            fence_line_no = j + 1  # 1-based
            close_idx = find_fence_close(lines, j + 1, fence_token)
            if lang in SHELL_FENCE_LANGS:
                raw = [(lines[k], k + 1) for k in range(j + 1, close_idx)]  # 1-based line numbers
                blocks.append((fence_line_no, raw))
            j = close_idx + 1  # skip the closing fence
            continue
        j += 1

    if block_index >= len(blocks):
        _fail(
            f"only {len(blocks)} bash/sh fenced block(s) found under heading "
            f"'{heading_text}' ({filepath}:{heading_line_no}); "
            f"requested block-index {block_index}"
        )
        return 1

    fence_line_no, raw_lines = blocks[block_index]

    commands = []
    for raw_line, num in raw_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # keep as (line-without-leading/trailing-ws, num) for continuation join
        pass
    # Re-walk raw_lines for continuation-joining, filtering comment/blank
    # lines up front (same as doc-lint.sh's extractor).
    filtered = [(rl.strip(), num) for rl, num in raw_lines if rl.strip() and not rl.strip().startswith("#")]
    commands = [
        {"cmd": text, "line": num} for text, num in join_continuations(filtered)
    ]

    if tsv:
        print(f"HEADING\t{heading_text}")
        print(f"HEADING_LINE\t{heading_line_no}")
        for c in commands:
            print(f"CMD\t{c['line']}\t{c['cmd']}")
        return 0

    print(
        json.dumps(
            {
                "file": filepath,
                "heading": heading_text,
                "heading_line": heading_line_no,
                "block_fence_line": fence_line_no,
                "commands": commands,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
