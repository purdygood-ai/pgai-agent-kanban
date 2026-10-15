# Coding Standards

This document is the authoritative list of coding standards for the
shop. Every code-producing role — human or AI agent — must follow it,
in the kanban itself and in every project the kanban builds. The
directives are written in imperative voice. Each one is followed by a
single-line rationale naming the failure mode it prevents.

If a specific requirement says otherwise, the requirement wins for
that ticket and the deviation is documented in the ticket. Silence
means these directives apply.

## Directives

1. **Split code by concern; one implementation per operation.**
   Duplicated implementations of the same operation drift out of sync
   and turn every bug into two bugs.

2. **Keep surfaces thin.** CLIs, dashboards, and API handlers are
   adapters over shared logic — they parse input, call into the core,
   and format output; they must not carry business rules of their own.
   Business rules embedded in a surface cannot be reused by the next
   surface without being copied.

3. **Put shared code in a `lib/` package and import it; never copy
   it.** Copy-paste sharing forks behavior the first time either copy
   changes and there is no compiler or test that will tell you.

4. **Write shared code generic enough that a future REST API (or
   another surface) can consume it unchanged.** Shared code shaped
   around one caller becomes private code the moment a second caller
   needs it, and the next surface pays the cost of extracting it.

5. **No hardcoded operational values.** Anything an operator might
   change lives in a config file with a commented `_example` companion;
   no hardcoded paths; missing required config fails loud and names the
   key. Hardcoded values hide the moving parts and make every
   deployment a code edit.

6. **Every script and CLI entry point ships a `--help` that describes
   every argument it accepts.** `--help` is the contract between the
   operator and the tool; a missing or stale `--help` is a silent lie
   to the next person who runs it.

7. **Scaffolding and documentation ship with the feature, in the same
   RC.** Config examples, README sections, and generators land in the
   same release as the code they describe. A feature that ships
   without its scaffolding and docs is a feature the next operator
   cannot adopt.

8. **Comments, docstrings, help text, log lines, and output strings
   describe behavior, never process history.** Do not cite bug IDs,
   ticket IDs, requirement versions, or internal framework versions
   in the code or its runtime output. Exceptions carry over from prior
   practice: format or usage examples that use bug- or version-shaped
   values, skip annotations that cite an OPEN follow-up bug, and
   references to EXTERNAL constraints such as upstream issues, CVEs,
   or RFCs. Anything else kept deliberately is suppressed in the
   allowlist sidecar with a justification, never with a marker comment
   written into the file — see "One hygiene lint enforces directive 8"
   below. Git history and task state hold the "why"; the code holds
   the meaning, and process residue in shipped code pollutes every
   downstream reader's context.

9. **Any REST API serves interactive contract docs (Swagger, OpenAPI,
   or equivalent) and a versioned contract file.** Without published,
   pinnable contract docs, every client integrates by reading server
   source, and every server change is a silent break.

10. **Default stack: Python 3.12+ for logic, bash for orchestration.**
    Deviate only when the requirement says otherwise and name the
    reason in the ticket. A consistent default stack is what lets one
    reader debug code written by another.

11. **Ship Docker and compose deployment artifacts where
    containerization makes operational sense, to the fail-loud
    entrypoint standard.** Entrypoints that swallow startup failures
    turn a broken deployment into a silently degraded one — fail loud
    at start so the operator sees the problem before traffic does.

12. **No inline Python in bash.** A bash script calls a Python module
    through `pp_run_ops` or `python3 -m`; it never carries the logic
    in a heredoc or a `python3 -c` string. Put the logic in a function
    in a Python module — in the kanban, under `pgai_agent_kanban/` —
    and have the script call it.
    `team/scripts/lint_inline_python.py` counts both shapes in every
    non-test script under `team/scripts` against
    `team/config/inline-python-allowlist.txt`; when you move a block
    out, lower that script's entry to its new counts in the same
    change, or delete the entry at zero. Python inside a heredoc cannot
    be imported, reused, or unit tested, so the next caller who needs
    it copies it.

13. **A helper lives in exactly one module, and every caller imports
    it.** Before you write an atomic file writer, a `status.md`
    section reader, a version or timestamp parser, an INI reader, or a
    path-traversal check, import the owning one — the table under
    "Three ratchet lints enforce directives 12–14" names each owner. If
    no owner exists yet, the first copy becomes the owner, in a
    `lib/` module (directive 3). `team/scripts/lint_python_duplicates.py`
    reports a deny-listed helper defined outside its owner and any
    function whose body matches another's, against
    `team/config/python-duplicates-allowlist.txt`; when you replace a
    copy with an import, lower or remove its entry in the same change.
    A private copy is invisible to whoever fixes the original, so the
    bug lives on in the copy.

14. **An API route and its CLI script call the same Python function.**
    The route imports the function and calls it; the bash script
    reaches the same function through `pp_run_ops`; neither shells out
    to the other or carries its own copy. Declare the function in the
    verb's `impl` column in `team/config/operator_verbs.manifest` as
    `module:<dotted.module.function>` — `shell` marks a verb not yet
    converted. `team/scripts/lint_api_route_impl.py` checks every
    `module:` row and holds the number of `shell` rows to
    `team/config/api-route-impl-baseline.txt`; when you flip a row to
    `module:`, lower the baseline in the same change. A route and a
    CLI that reach one operation by two paths give two answers the
    first time either path changes.

## A help path prints completely and exits 0

Directive 6 says every entry point ships a `--help`. This is how that
help path must behave: it MUST print its complete help text and then
exit 0, on every invocation, including under `set -euo pipefail`.
Determinism is part of the requirement, not a refinement of it. A help
path that exits 0 almost every time is a failing help path, because a
documentation check that runs the command and reads its exit status
will reach different verdicts on an unchanged tree.

**The obvious shape does not guarantee it.** A script that builds its
help text out of its own leading comment block invites a pipeline
ending in a reader that stops early:

```bash
# Do not write this.
grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40
```

`head -40` closes its read end the moment it has forty lines. When the
header is longer than that, the upstream stages are still writing into
a pipe with no reader, so they are killed by SIGPIPE and exit 141.
`pipefail` promotes that 141 to the status of the whole pipeline and
`set -e` terminates the script on it — before the `exit 0` on the next
line ever runs. The help text has already been printed, completely and
correctly; only the status is wrong. Whether it happens at all is a
scheduling race between the reader exiting and the writer's next
`write()`, so the same command exits 0 nearly every time and non-zero
once in a while. A single successful run proves nothing about this
shape.

**Print a header with the shared helper.** A script whose help is its
leading comment block calls `header_help_print` from
`team/scripts/lib/header_help.sh`:

```bash
    -h|--help)
      # shellcheck source=lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
```

A script one directory deeper, such as one under `team/scripts/dashboard/`,
reaches the same `team/scripts/lib/header_help.sh` one level up:

```bash
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
```

Inside the helper, one `awk` reads the script file and writes straight to
stdout, so no pipe stage sits between extraction and output. It prints from after the shebang to
the first non-comment line, strips the `# ` prefix and leaves out
`# shellcheck` directives, so there is no line count to keep in step
with the header.

**Any other extraction drains, then slices.** Help text built some other
way captures the extraction to end of input first, then narrows it
locally. `mapfile` reading a process substitution always consumes its
producer to EOF, so nothing downstream can close the pipe early, and an
array slice cannot signal anything. Narrow by an offset found in the
captured lines, such as the `Usage:` line, never by a hand-typed line
count, which goes stale the moment the header changes. What the rule
fixes is the order: capture all of it, then cut.

**The rule is checkable by reading the script.** Look at the help
branch and ask one question: can anything downstream of the extraction
stop before its input ends? If the text reaches stdout through a pipe
whose final stage is `head`, an `awk` or `sed` that quits at a line
count, or a `read` loop that breaks early, the help path does not
comply — regardless of the status it returned the last time anyone ran
it. A compliant help path calls `header_help_print`, or holds the whole
text in an array or variable between extraction and output; whatever
narrows it works on that captured copy rather than on a live pipe, and
the branch ends in an explicit `exit 0`.

Apply this whether or not the script sets `pipefail` today. Written
the compliant way, a help path cannot be broken by someone adding
`set -o pipefail` later, or by a header that grows past a limit
nobody remembers choosing; written the other way it is latent, and
becomes live on the day a line is added to the comment block above it.

## One hygiene lint enforces directive 8

`team/scripts/lint_hygiene.py` is the shop's single hygiene-lint entry
point. It is a registry of named classes; each finding names its class,
its `file`, its line, and a runnable remedy. Four classes ship today:

| Class | What it flags |
|---|---|
| `provenance` | A bug, priority, task or requirement ID, or a version cited as history (shapes such as `since vN.N`, `(vN.N.N)`, `added in vN` and `vN.N.N+`), in the code surface, the test tree or shipped prose; wording is not checked. |
| `version-named-test` | A test module or test function named for the release that produced it rather than for the behavior it exercises. Test tree only. |
| `comment-drift` | A comment or single-line docstring stating an argument count the adjacent single-line `def` signature contradicts. |
| `operator-literal` | A literal that only resolves on one operator's machine: a `/home/` path naming a real account (label `home-path`), or an scp-style `git@<host>:` remote whose host is a private `~/.ssh/config` alias (label `ssh-host-alias`). Scanned across every git-tracked file — see below. |

`operator-literal` reads its own surface, not the directory surfaces the
other three read: every file `git ls-files` lists at `--repo-root`, of
any type, except `release-notes/`, the root `CHANGELOG.md`, and
`team/tests/` — whose `artifacts/` subtree stays in scope, because those
archived snapshots ship as they are. A `--repo-root` that is not itself
the top of a git work tree scans nothing. Neutral forms are never
findings: `$HOME/...` and `~/...`; an angle-bracket or variable segment
(`/home/<user>/`, `/home/$USER/`); the placeholder account names `you`,
`user`, `youruser`, `username` and `op`, compared ignoring case and
`-`/`_`; `git@github.com:`; and the reserved example domains. Write
shipped examples in those forms — `$HOME/pgai_agent_kanban`,
`~/develop/<project>`, `git@github.com:<org>/<repo>.git` — and use
`/home/<user>/` only where the format expands no variable, such as a
JSON value or a systemd unit.

The lint's version-shaped matchers are anchored against mid-identifier
embedding: a version-shaped token sitting immediately after a `.` is
part of a longer dotted name, not a citation, and is not a candidate.

**It never blocks, without exception.** Both gated runners merge its
per-instance findings into the quality-class findings file and carry on to
pytest, then file at most one aggregated bug per class per run for the next
loop to heal. A malformed allowlist sidecar is one of those findings, not a
case apart — see "The allowlist sidecar" below.

### The allowlist sidecar

Suppression lives in a checked-in, line-level sidecar at
`team/scripts/hygiene-lint-allowlist.json` (override with `--sidecar`).
The lint reads no allowlist directive out of a scanned file, so a marker
comment pasted into the code or the prose suppresses nothing and is
itself the litter directive 8 forbids. A whole-file exemption cannot be
expressed — every entry suppresses exactly one line.

Each entry carries five fields: the hygiene `class` it suppresses, the
repo-relative `file`, a `content_hash` fingerprint of the flagged line's
own text, a `line_hint` for a human reader, and a non-empty
`justification`. The fingerprint is the match key, so an edit elsewhere
in the file never invalidates an entry; editing the suppressed line's
own text does, and the finding reappears on purpose — a changed line is
a line nobody has re-reviewed. A blank or missing `justification`
refuses the whole sidecar load rather than being honoured quietly — as
does an unreadable file, invalid JSON, or an entry of the wrong shape. A
refused load is reported as its own `SIDECAR-MALFORMED` finding naming the
sidecar, the reason it was refused, and the entry at fault where one entry
is at fault; the file then supplies no suppression for that run, so nothing
it would have hidden stays hidden. The lint still exits 0.

Do not hand-author an entry. The lint writes it, and the same command is
the remedy it prints beside every finding:

```bash
python3 team/scripts/lint_hygiene.py \
    --add-suppression <file> <line> <class> "<justification>"
```

The printed form spells `--sidecar` and `--repo-root` out in full; the
defaults resolve to the same two values when you run the lint from the
dev tree. Re-run the lint afterwards to prove the finding cleared — that
re-check is step 2 of the remedy block the lint prints.

## Three ratchet lints enforce directives 12–14

Each of directives 12, 13 and 14 has one lint. None of them demands
zero: each compares the tree against a checked-in record of what it
holds, and the record may only fall.

| Directive | Lint | Record it ratchets against |
|---|---|---|
| 12 | `team/scripts/lint_inline_python.py` | `team/config/inline-python-allowlist.txt` — one `<path> <blocks> <python_lines>` line per script |
| 13 | `team/scripts/lint_python_duplicates.py` | `team/config/python-duplicates-allowlist.txt` — one `<family> <path> <symbol> <count>` line per finding key |
| 14 | `team/scripts/lint_api_route_impl.py` | `team/config/api-route-impl-baseline.txt` — one `shell_rows <count>` line |

**All three are quality class.** Each narrates one line per finding,
writes its findings to the gated runners' quality findings file, and
exits 0 whatever it found. None ever blocks a runner or a release;
TESTER files the findings like any other quality finding, and the next
loop heals them.

### The ratchet, in both directions

The same three outcomes apply to every lint:

- **Something new** — a script with inline Python that has no entry, a
  copy of a helper with no entry, a `module:` row the route or the
  script does not honour — is a finding. Fix the code; never add an
  entry for it.
- **Something above its record** — a script with more blocks or lines
  than its entry, a key counted more often than its entry, more `shell`
  rows than the baseline — is a finding. Fix the code; never raise the
  entry.
- **Something below its record** is a finding too, so the record is
  tightened in the same change that improved the file. Lower the entry
  to the measured value, or remove it when the measured value is zero.
  An entry left loose lets the next change regress back up to it
  unreported.

`lint_inline_python.py` also reports a Python heredoc opened with an
unquoted delimiter (`<<PY` rather than `<<'PY'`) even when its script is
within its counts: bash expands `$` and backticks in that body before
Python sees it.

### Tightening an entry

Each lint prints its measured values in its own record's format, so a
tightened entry is copied from the lint, never computed by hand. The
two allowlists are edited line by line — the emit modes print the
measured lines without the file's header comment, so never redirect them
over the allowlist; filter for the file you changed instead. For
example, for the script `team/scripts/cm/finalize.sh` and the duplicated
function `_make_envelope`:

```bash
python3 team/scripts/lint_inline_python.py --emit-allowlist | grep -F 'team/scripts/cm/finalize.sh'
python3 team/scripts/lint_python_duplicates.py --emit-allowlist | grep -F '_make_envelope'
```

Replace the matching line in the allowlist with the printed one, or
delete it when nothing is printed. The baseline's emit mode prints the
whole file, header included, so it may be redirected:

```bash
python3 team/scripts/lint_api_route_impl.py --emit-baseline > team/config/api-route-impl-baseline.txt
```

Re-run the lint afterwards; it reports nothing for the file you
improved. An emit mode prints what the tree measures, not what it
should measure — it is never a licence to raise or add an entry.

### Owning modules

Directive 13's deny-list names the helpers that may be defined only in
their owner, named here as the module you import from (the package
lives under `team/` in the repository). A definition anywhere else,
under any number of leading underscores, is a finding; import from the
owner instead.

| Helper | Owning module |
|---|---|
| `atomic_write` | `pgai_agent_kanban.lib.atomic_write` |
| `has_traversal` | `pgai_agent_kanban.api.validation` |
| `parse_version` | `pgai_agent_kanban.lib.semver` |
| `parse_iso` | `pgai_agent_kanban.lib.timeutil` |
| `read_section`, and any regex matching a `## State` heading | `pgai_agent_kanban.task_status` |
| `configparser` parser instantiation (`ConfigParser(`, `RawConfigParser(`, ...) | `pgai_agent_kanban.lib.ini` |

The lint's own module docstring is the authoritative copy of this list
and of the body-normalisation rule behind its `duplicate-body` family.

## Gates prescribe, not just diagnose

A deterministic gate that only reports what is wrong forces the reader to
rediscover the fix that was already knowable at the moment of failure.
Every deterministic gate — every lint, freshness check, pre-flight abort,
release-script guard, and pre-tag hook — MUST, on failure, prescribe its own
remedy, not merely diagnose. The test is concrete: a stranger reading only
the gate's failure output, with no other guidance, must be able to clear the
gate.

A "deterministic gate" is any check whose passing condition and whose fix are
both computable from the state the gate inspects. The hygiene lint
(`team/scripts/lint_hygiene.py`) is the seed pattern: on a finding
it prints the offending `file:line`, the exact token that tripped it, the
one-line rule it enforces, and the precise way to clear it. Generalize that
shape to every gate.

### The remedy block: three required parts

On failure a deterministic gate MUST print, in this order:

1. **Cause(s), each with its concrete location.** Name the exact path, line
   number, config key, or field that tripped the check. "Something is stale"
   is a diagnosis of nothing; "`CHANGELOG.md` is missing the `## v1.27.2`
   section" names the file and the field. List every cause when there is more
   than one — the reader fixes what they can see.

2. **A numbered fix block of verbatim-runnable commands.** The commands must
   be copy-paste runnable exactly as printed, in the order printed, with no
   editing required to run them. Number them so the reader follows a sequence,
   not a paragraph. End the block with the re-check command that proves the
   fix worked (typically re-running the gate).

3. **The one-line rule the gate enforces.** State the single standing
   invariant the gate exists to protect, in one sentence, so the reader learns
   *why* the gate fired and not only *how* to silence it. This is what stops
   the reader from defeating the gate instead of satisfying it.

### The house placeholder law

The fix commands carry no angle-bracket placeholders. Never print `<path>`,
`<version>`, `<repo-root>`, or any other bracketed stand-in for a value the
gate could resolve. Where a value is site-specific, the gate derives it at
detection time and prints the actual literal: the gate already knows the
offending path, the current version, the resolved repo root at the moment it
fails, so it emits them. A placeholder the gate could have resolved is a
defect in the gate, not a convenience for the reader.

The only slot that may remain textual is genuinely free-form input that only
a human can author — a justification string, a chosen name — which the gate
cannot know. Even then, print a concrete, filled-in example line the reader
can adapt, never a bare `<slot>`.

### Remedy text lives beside the check

The remedy text is maintained in the same file as the check it describes.
When the check moves, its message moves with it; when a reviewer changes the
check, the now-stale remedy is in the same diff and is caught in review.
Remedy text stored apart from its check drifts silently until the day a reader
runs the printed commands and they no longer clear the gate.

### The non-deterministic exemption

Failures whose fix is not knowable at detection time are exempt from the
numbered command block. Real test-assertion failures, provider or API errors,
and network faults have no verbatim command that clears them — prescribing one
would be a lie.

They are NOT exempt from direction. Each such failure MUST still
name the next diagnostic step: point the reader at the specific evidence to
read next and what to look for in it — for example, "the failing test is
`test_release_gate.py::test_fidelity`; read the assertion tail printed above."
Exempt from prescription, never exempt from telling the reader where to look
next.

### The shape, generically

Gate-specific command text belongs beside each gate, not here. The generic
shape a compliant remedy block fills is:

```
FAIL: <one-line statement of what failed>

Cause(s):
  - <derived path/line/field 1> — <what is wrong with it>
  - <derived path/line/field 2> — <what is wrong with it>

To fix:
  1. <verbatim command, site-specific values already substituted>
  2. <verbatim command>
  3. <re-run the gate to confirm>

Rule: <the one-line invariant this gate enforces>
```

The angle brackets above mark the slots a gate author fills; a compliant gate
never prints them at runtime — by the placeholder law it prints the derived
literals in their place.
