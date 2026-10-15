# TESTER Verification Reference

The long-form reference behind several passages of `team/roles/TESTER.md`.
The role file carries the procedure a TESTER performs on a wake; this
document carries the reasoning, the edge cases, and the worked examples
a TESTER consults when a case is not obvious.

Each section below is named for the role-file passage it expands, so the
material is always one hop away. Nothing here is new law — every passage
expands a rule the role file already states in operative form.

This document is not on any role's read-order manifest, by design: no
role in the catalog has the governance byte headroom to absorb it, and a
named pointer keeps it reachable without spending that headroom.

## Contents

| Section | Named from `team/roles/TESTER.md` |
|---|---|
| Running the gated suites | Steps 5a and 5b |
| Deep-verify freshness reporting, in full | Step 5d |
| Report format the tooling reads | Step 9, "The finding heading and these `**Field:** value` lines" |
| Filing a Path C bug or priority | Step 10, "The document shapes" |
| Aggregating lint-class findings into one filing | Step 10, "One lint class is one related thing" |
| Anti-patterns to avoid | "Anti-Patterns to Avoid" |
| Autonomous criterion expectations | Step 6.12 and "Autonomous Criterion Expectations" |
| Reading CODER's `## Stale Literal Risks` section | "Reading CODER's `## Stale Literal Risks` section" |
| What TESTER does not verify | "What TESTER does not verify" |

---

## Running the gated suites

Named from Step 5a ("Run the gated suites") and Step 5b ("Record coverage") of `team/roles/TESTER.md`. The role file says the verdict is the two gated runners' exit codes. This section covers the mechanics behind them: the one command that runs both runners, the worker budget they share, the serial lane inside each runner, when `--coverage` actually collects, and the slowest-tests record each certification run leaves behind.

**Start both suites with one command.** `team/scripts/run-gated-suites.sh` runs `run-unit-tests.sh` and `run-integration-tests.sh` as two ordinary certification runs, inside one worker budget. Run it from the dev tree root:

```bash
bash team/scripts/run-gated-suites.sh --coverage --out-dir "$PGAI_AGENT_KANBAN_TEMP_DIR/tester"
UNIT_EXIT="$(cat "$PGAI_AGENT_KANBAN_TEMP_DIR/tester/unit.exit")"
INTEGRATION_EXIT="$(cat "$PGAI_AGENT_KANBAN_TEMP_DIR/tester/integration.exit")"
```

The run leaves three things per suite:

- **What the runner leaves anyway.** Each suite writes the same evidence it writes when run on its own: `lint/findings-<suite>.json` under the temp root, its green-ledger record, and its durations record.
- **`<suite>.log`** in the `--out-dir` directory: that runner's stdout and stderr.
- **`<suite>.exit`** in the `--out-dir` directory: one line, that runner's exit code. It is removed when the run starts, so a stale file is never read.

`--out-dir` defaults to the temp root itself.

**The verdict has not moved.** `unit.exit` and `integration.exit` *are* `UNIT_EXIT` and `INTEGRATION_EXIT`, because each holds its own runner's exit code. Steps 6.6 and 6.7 read them as before. Both suites always run to the end, even when one fails. The entry point's own exit status is 0 only when both are 0. Otherwise it is the first non-zero suite code, unit first. Read the two `.exit` files rather than that single status, because a single number cannot say which suite failed.

**When the entry point refuses before any suite runs.** An exit of 1 with neither suite run means the entry point itself refused. The causes are:

- an unrecognized argument
- an unaccepted `[testing] workers` value
- a missing runner
- an `--out-dir` it cannot write

Each refusal prints an error key, a message and a remedy. A missing runner is Step 5a's infrastructure failure.

**Running the two runners by hand is still valid.** Two separate runs produce identical evidence, and the role file's commands still work. The entry point is the faster way to make the same two certification runs, not a different kind of run. Targeted `--files` runs are not offered by the entry point; they belong to each runner.

### The worker budget

`[testing] workers` in `kanban.cfg` is the most pytest-xdist workers a gated run may have alive on this host. The default is 3. The key is kanban-wide: `project.cfg` has no `[testing]` section.

| `workers` | What `run-gated-suites.sh` does |
|---|---|
| 2 or more | Starts both suites at once: unit with `--workers <workers - 1>`, integration with `--workers 1`. The two shares add up to exactly the budget. |
| 0 or 1 | Runs unit, then integration, each single-process. Two suites cannot each have a worker inside a budget of 1, so they take turns. |

`--workers N` on either runner lowers that run's budget to the smaller of `N` and `[testing] workers`; it never raises it. Before pytest starts, every runner prints a `Parallel workers:` line stating how it will run. A runner given `--workers` also prints a `Worker budget:` line naming the share it settled on.

At the default of 3, the unit suite runs with two xdist workers. The integration suite gets a budget of 1, so it runs single-process.

Two outcomes that look odd are expected:

- **pytest-xdist missing.** The runner says so in its `Parallel workers:` line and runs single-process. That is never a failure.
- **Cross-suite findings.** Running concurrently, each suite skips its own scratch sweep and the entry point sweeps once after both pass. A suite's post-run bare-`/tmp` or TCP-listener check can still report a file or listener the *other* suite was holding when the first one finished. Those checks are quality-class: they land in the findings file for Step 5c and never change an exit code.

### The serial lane

Inside each runner, a run with more than one worker and pytest-xdist available runs pytest twice, one lane after the other:

1. **Parallel lane.** Every test not marked `serial`, with `-n <workers> --dist loadfile`.
2. **Serial lane.** The `serial`-marked tests, in one process.

Each lane prints `Lane 1 of 2: parallel` or `Lane 2 of 2: serial`, then its own `Replay this exact run:` line. A closing `Lane exit codes: … -> combined exit code N` line combines the two into the runner's one exit code. The run still writes one findings file.

An empty serial lane is not a failure: pytest's "no tests ran" exit there combines as a pass. A single-process run (workers 0 or 1, no xdist, `--files`, `--quarantined`) has one lane and prints no lane lines.

When a runner fails, the lane lines tell you which lane the failure came from, and that lane's replay line reproduces exactly the run that failed. To bisect a failure in the parallel lane, start from its replay line. Direct pytest is still a debugging aid here, never a PASS source.

What earns a test the `serial` marker, and the one-line reason it carries, is in `docs/test-authoring-guidelines.md` ("The `serial` marker and the parallel lane"). A test that passes alone and fails beside parallel workers usually means a missing marker, or a resource the test should not be sharing. Either way it is a defect in the test, not a flake to re-run until green.

### Coverage is collected only when the operator turns it on

Step 5a passes `--coverage`, and the runners accept it. Whether coverage is actually collected is the runners' decision. It is honoured in exactly two cases:

- **`[testing] coverage = true` in `kanban.cfg`.** This is kanban-wide, with no per-project override. The default is `false`.
- **`PGAI_TESTING_COVERAGE=1`** set in the operator's own shell for a one-off run. This is an operator's switch and never an agent's. The wake bootstrap unsets it, and the runners ignore it while `PGAI_AGENT_WAKE_ACTIVE=1`. Inside a TESTER wake, only the config key can turn coverage on.

Otherwise each runner prints `coverage disabled by config ([testing] coverage)` and runs the suite without coverage. The exit code is unaffected.

That line is Step 5b's `not collected`. Write it in the report's `## Code Coverage` section and move on; it is not a finding.

A `--coverage` request the config turned down does not count as coverage for `--reuse-if-green` either. A green-ledger reuse still applies.

**Report coverage only from a run that collected it.** The runners write their coverage JSON under `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/`. A file already there may have been left by an earlier run, including the operator's weekly run below, against a different checkout. If this verification's runners printed the disabled line, the RC has no coverage figure. Do not lift one from a file you did not produce.

**The operator's weekly coverage run.** Every crontab template under `team/templates/install/` carries this line, commented out:

```cron
# 0 5 * * 0 PGAI_TESTING_COVERAGE=1 /path/to/dev-tree/team/scripts/run-unit-tests.sh --coverage >> __KANBAN_ROOT__/logs/cron-coverage.log 2>&1
```

It runs the unit suite with coverage on Sundays at 05:00. The operator enables it by replacing `/path/to/dev-tree` with their dev tree checkout and uncommenting it. It is not part of RC verification: TESTER never installs it, never runs it, and never sets the variable.

### Slowest tests

Every certification run records its slowest tests. A certification run here means any run without `--files` or `--quarantined`, whether started through the entry point or by hand. The record goes to `lint/durations-<suite>.json` under the temp root, beside the findings file. It holds:

- up to 25 tests, slowest first, each taking at least 1.0 s (setup, call and teardown together)
- the run's `total_seconds`
- its `test_count`

The record is informational only. It never gates a run, never files a bug, and never changes an exit code.

The report template's `## Slowest tests` section is where it goes: the top 10 entries and the total-time change since the previous RC. The next certification run overwrites the file. To give the next RC something to compare against, copy both files into the task's `artifacts/` directory beside `report.md`.

---

## Deep-verify freshness reporting, in full

This is Step 5d as the role file carried it before the governance byte
budget forced the split. The role file keeps the operative half — do not
run the lane, read the ledger through its one reader, write one of the
three forms, never imply a green lane that did not run. Everything below
is the same rule set with its reasoning, its worked example, and its
edge cases intact. When the condensed step leaves you unsure what to
write, this is the answer.

**Do not run the cold-build lane during RC verification.** TESTER runs no container build, starts no container engine, and calls none of the container-tier harnesses (`run-cold-build-tests.sh`, `run-three-host-matrix.sh`, `harness/strangers-path-check.sh`, `deep-verify.sh`, `cold-build-now.sh`). The tier is operator-only: each harness runs only with the operator's `PGAI_CONTAINER_TIER=1` or `--operator-approved`, and inside an agent wake it prints one `SKIPPED` line and exits 0 having run nothing and recorded nothing — never a pass. The lane lives in the DEEP-VERIFY tier — weekly, on demand, and as a named pre-release step; the operator runs it, and `roles/CM.md` owns its pre-release freshness check. A full sweep costs roughly thirteen minutes of wall clock; that is a DEEP-VERIFY cost now, not an RC-verification one.

What this step produces is one mandatory report section, `## Deep-Verify Freshness`. **The lane's contribution is a fact TESTER reports, not a verdict TESTER produces** — its result never becomes a `## Findings` entry. It is mandatory rather than conditional because the risk rating is made against it: a byte-identical tree can stop building because something upstream moved, so an eleven-day-old green is a materially weaker claim about today's tree than one from this morning, and a number nobody states is a number nobody weighs. The line is written on **every** report for a tree that ships Dockerfiles, including trees never deep-verified and trees whose last result was red.

**Where the answer comes from.** The per-project ledger at `$PGAI_AGENT_KANBAN_ROOT_PATH/projects/<project-name>/deep-verify-ledger.md` — append-only kanban process state, a sibling of `release-state.md`, never committed into a dev tree. Read it through the one reader every consumer calls, `deep_verify_ledger_read_latest` in `team/scripts/lib/deep_verify_ledger.sh`; never parse the markdown yourself and never re-derive a verdict from raw lane output. On success it prints one `key=value` line per field — `found`, `timestamp`, `project`, `version`, `commit`, `verdict`, `detail`. A bare `found=false` is its own signal for "never deep-verified." **A non-zero return is not "never run"**: the ledger exists but is malformed, and it takes Form C.

**The verdict vocabulary is exactly three values**, used verbatim — never paraphrased, never translated into `PASS`/`FAIL`. `built` (the artefact built), `failed-to-build` (it did not — a defect in the tree), `could-not-attempt` (never attempted: no engine, unreachable registry, missing toolchain, so nothing about the tree was exercised). These map one-to-one onto the three build outcomes in `SOP-core.md` ("Gates and Refusals"), the canon for what each is worth.

**The line's exact shape.** Write it as the entire body of the section — one line, no bullet, no heading beneath it: the literal prefix `deep-verify: `, then `key=value` tokens in fixed order, then a space-em-dash-space separator, then one plain-language sentence. Three forms, selected by what the reader returned:

```
Form A — a record exists (found=true). Seven tokens, this order, no others:
deep-verify: found=true verdict=<verdict> version=<vX.Y.Z> commit=<sha> timestamp=<ISO 8601 UTC> age=<N>d freshness=<fresh|stale> — <one sentence>

Form B — never run (found=false). One token:
deep-verify: found=false — <one sentence>

Form C — the ledger is unreadable (reader returned non-zero). No tokens:
deep-verify: ledger unreadable — <one sentence naming the path>
```

`verdict`, `version`, `commit`, and `timestamp` are copied byte-for-byte. **`age=<N>d`** is whole days between the record's timestamp and now, floored — this morning is `age=0d`. **`freshness`** is the record's *currency*, a separate question from whether it was green: `fresh` requires **both** that `age` is within the window **and** that `version` equals the RC version under verification; otherwise `stale`, with the sentence naming which condition failed, or both. The window is `PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS`, default 7 days — the same policy the pre-release promotion gate reads. Currency and greenness are orthogonal on purpose: a `failed-to-build` record can be perfectly fresh and a `built` record badly stale, and collapsing them is how a reader believes an old pass is a current one. The sentence carries what the tokens cannot — for a red record, the bug that run already filed; for a `built` record whose `detail` names failing non-build cold tests, the bug that run filed for them; for `could-not-attempt`, the missing resource, plus the bug that run filed when its `detail` also names failing non-build cold tests; for Form B, that nothing is known about whether the images build; for Form C, the ledger path. A worked Form A, the common stale-green case:

```
deep-verify: found=true verdict=built version=v2.9.7 commit=8b41c02 timestamp=2026-08-23T04:00:09+00:00 age=11d freshness=stale — last built clean at v2.9.7; stale on both counts (11d exceeds the 7-day window, and v2.9.7 is not the v2.9.12 under verification), so the images have not been built cold against this tree.
```

**Never imply a green lane that did not run.** The absence of a failure is not a pass, and the rule has one named failure mode:

> **Omitting the `## Deep-Verify Freshness` line from a report for a tree that ships Dockerfiles is the forbidden failure — the silent skip this step exists to eliminate.**

Silence reads to every downstream human and to CM as "nothing to report," which they reasonably take as "fine." It is not fine; it is unknown. Three corollaries: **never-run is stated, not omitted**; **a red record is stated, not deferred** (waiting for the next green result is omission with extra steps); and **never write a line the ledger does not support** — do not soften `failed-to-build` into a caveat, do not round `age` down, do not write Form A from a lane run you observed by other means.

**Staleness is context the rating is made with, not a finding.** On its own a stale record — including `found=false`, staleness at its limit — does not raise `## Systemic Risk` and does not elevate `## Recommendation` off `PASS`.

**A red record is cited, never re-filed.** The run that produced `failed-to-build` filed its own bug when it failed; cite it in the sentence, grepping `bugs/` for the open bug naming the failing engine/flavor pair if `detail` does not hand you the ID. **A `built` record can carry a bug the same way.** When every image built but a non-build cold test failed, `detail` begins `no image-build test failed; non-build cold test(s) failed:` and names the pytest node ids, and the run filed a bug naming each test's file and test id, or cited the open one already naming that set. Cite it the same way — grep `bugs/` for the open bug whose `## Deep-Verify Tests` section lists those node ids — and file nothing. The record is still `built`, so the token reads `verdict=built` and freshness is judged as for any green. **A `could-not-attempt` record can carry one too.** When no pair built and pairs were narrate-skipped, `detail` begins `no (engine, flavor) pair built; every pair the run reached was narrate-skipped:` and names the skip reasons; if an engine-free cold test also failed, it goes on to `non-build cold test(s) failed:` and the node ids, and that run filed or cited the same test bug. Cite it the same way and file nothing. The token reads `verdict=could-not-attempt`, and the record is not green. A duplicate splits the fix across two tickets and lies about how many defects exist. Because TESTER files nothing here, Step 8's "any bug filed" edge case is not triggered: a deep-verify finding belongs to the loop, not to whichever RC was open when the weekly run failed.

**Read the verdict; do not re-derive it.** The red-versus-environment-gap discrimination is made mechanically by the run and is already in the `verdict` field: **a `could-not-attempt` names a resource that was unavailable; a `failed-to-build` names a Dockerfile step, or an image-build test, that did not work with everything it needed available.** The consequence most often got wrong is that **`could-not-attempt` is not a weak red** — no image build was exercised, so no bug describing the build is owed, and "the deep-verify host had no podman" describes the host, which no agent in the loop can close. A failing engine-free test named in its `detail` is a separate finding the run already filed; cite it, as above.

**Two faults do earn a Step-7 `gap` finding**, both filed via Path C, neither `BLOCKED`: an **unreadable ledger** (kanban state an agent can repair, and one nobody can read silently disables the pre-release promotion gate too — write Form C, then file the gap), and a tree that **ships Dockerfiles but has no `team/scripts/deep-verify.sh`** (images shipping with no cold-build gate in front of them anywhere).

**Projects that ship no images.** Most projects here ship none, and for those trees this step is a **clean no-op** — not a skip, not a gap, not a finding. Do not read the ledger, do not write the section, do not record a shortfall. If `ls docker/*/Dockerfile` finds nothing, the entire content of this step is at most one neutral line under `## Caveats` reading `deep-verify: not applicable — tree ships no Dockerfiles`. **An absent subject is not a skipped step**, and the two rules do not contradict: the line is owed by every tree that ships Dockerfiles, and a tree that ships none has no lane to be fresh or stale about. Contrast Step 5a, where a missing `run-unit-tests.sh` blocks because the authoritative command for a verdict TESTER is *required* to produce has gone missing. Absent tooling for work that exists blocks; work that does not exist needs no tooling.

---

## Report format the tooling reads

Named from Step 9 of `team/roles/TESTER.md` ("The finding heading and these `**Field:** value` lines are the shape the release script parses"). One module, `team/pgai_agent_kanban/reports/tester_report.py`, parses `artifacts/report.md` for every consumer, and its module docstring is the whole contract. This section restates that contract. Where another description of the report shape differs from it, the module's rules are the ones the tooling applies.

**Reading is liberal, results are strict.** The parser tolerates the variation a writer introduces. Every enumerated field comes back as one lowercase token from a fixed vocabulary, or as `None` when it is absent or unrecognised. It never guesses a value.

**Check how a report will be read.** Run the parser's CLI on the report before you finish:

```bash
python3 -m pgai_agent_kanban.reports.tester_report <report> --json
```

`<report>` is a path, or `-` for standard input. `--json` prints the parsed report; without it a short plain-text summary prints. The exit code is 0 for any readable report, including one with no findings, 1 when the report cannot be read, and 2 on a usage error. A field that prints `null` was absent or unrecognised, and `None` is what every consumer reads for it.

### A minimal report

```markdown
# Verification Report: rc/vX.Y.Z

## Summary

One paragraph on what was verified.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

high

## Findings

### Finding 1: Short title

**Category:** gap
**Severity:** medium
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** high
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0042 (example id, Path C)

Detail, steps and evidence.
```

This example reads as recommendation `ship-with-concerns` and systemic risk `high`, with one finding: number `1`, title `Short title`, category `gap`, severity `medium`, fix effort `small`, systemic risk `high`, regression `no`, integrity `none`, filed as `BUG-0042`.

### Text the parser does not read

Fenced code blocks and HTML comments are not report text. A heading or field line inside a fenced block (three or more backticks or tildes) is never read. HTML comments are found the way markdown renders them: a `<!--` quoted in prose or inside an inline code span is text, and a comment opened mid-line counts only when it is closed in its own paragraph. An opener with no closer anywhere after it, whether a fence or a line-start `<!--`, hides nothing and reads as text.

### What starts a finding

A finding starts at a Markdown ATX heading of level 2 to 6 whose text begins with the whole word `Finding`, singular, in any case. The heading is 0 to 3 spaces, then 2 to 6 `#`, then the text. The space after the hashes may be missing for a finding heading only: `###Finding 1:` opens a finding, but `###Step 7` is not a heading at all.

| Opens a finding | Does not |
|---|---|
| `### Finding 1: title` | `## Findings` (plural) |
| `#### Finding 2 -- title` | `### Findings summary` (plural) |
| `## Finding 3:` | `# Finding 1` (level 1 is the report title) |
| `### Finding: title` | `####### Finding` (not a heading) |
| `### finding 4` | |
| `###Finding 1:` | |

Emphasis markers around the heading text are ignored, and trailing closing `#` are dropped.

After the word `Finding` an optional label may follow: digits (`1`, `12`, `3.2`, `4b`), or a single capital letter followed by a separator (`A:`). The finding's `number` is the label's leading digits as an integer, or `None` for no label or a letter label; `label` keeps the label as written. `title` is the rest of the heading after the label and one run of separators (`:`, `.`, `)`, `-`, `--`, an en dash or an em dash), or `""`.

### Where a finding ends

A finding ends at the next heading whose level is no deeper than its own, or at the end of the report. After `### Finding 1`, both `## Lint Findings` and `### Step 7` end it, and so does a sibling `### Finding 2`.

A deeper heading stays inside the finding, and that includes a deeper finding heading. `#### Finding 2` inside `### Finding 1` opens a sub-block of finding 1, not a finding of its own:

- The sub-block is split from the outer finding's lines by these same two rules: it ends at the next heading no deeper than its own.
- It comes back parsed in the outer finding's `sub_findings`.
- Its lines stay in the outer finding's `body`, but its field lines are its own. The outer finding's fields are read only from the outer finding's own lines, so a sub-block never drops, overrides or fills in an outer field.

Setext headings (text underlined with `===` or `---`) are not headings. A `---` line is a thematic break and ends nothing.

### How a field line is recognised

A field line is one line of the form `Label: value`, read after removing any leading indentation and one list marker (`-`, `*`, `+`, `1.`, `1)`). The label may be wrapped in emphasis (`**`, `__`, `*`, `_`) with the colon inside or outside it. The whole line may be wrapped (`**Label: value**`). The label and the value may each be wrapped in quotes or backticks. These five lines are the same field line:

```markdown
**Systemic Risk:** high
**Systemic Risk**: high
- **Systemic Risk:** "high"
Systemic Risk: high
**Systemic Risk: high**
```

A label starts with a letter and runs at most 60 characters of letters, digits, spaces and `_ / & ( ) ' . , + -`. Quoted (`>`) lines and table rows are not field lines.

- **The eight named fields** — `Filed As`, `Filed By`, `Severity`, `Category`, `Regression`, `Integrity`, `Fix Effort`, `Systemic Risk` — are read in plain or bold form.
- **Any other label** is read only in bold form, and lands in the finding's `extra`. A plain `Note: ...` sentence is prose, not a field.
- **`Integrity Damage` is its own label**, not `Integrity`, so it lands in `extra`.
- **The first line wins.** In each finding the first field line carrying a non-empty value decides a field, and later lines for the same label are ignored. A label line with nothing after its colon is passed over.

### Case and whitespace

- **Labels** match case-insensitively. Any run of spaces, tabs, underscores or dashes inside a label reads as one space, so `fix_effort`, `Fix-Effort` and `FIX  EFFORT` are all `Fix Effort`.
- **Values** are trimmed.
- **Enumerated values** match case-insensitively, and a token's own dashes may be written as spaces or underscores: `pass with caveat` reads `pass-with-caveat`.
- **The token must lead the value and stand alone.** Whatever follows it after a space or punctuation is an annotation and is ignored: `medium (could recur)` reads `medium`, and `High.` reads `high`. A token running on into more letters or dashes is not that token: `small-to-medium` and `pre-existing-bugs` read `None`. The longest token that fits is the one read, so `pre-existing-bug` reads as its own category, never as `bug`.
- **A placeholder reads `None`.** A value listing two or more of the field's tokens separated by `|` or `/`, such as `low / medium / high`, is the report template's placeholder.
- **Headings** follow the same rule: the word `Finding` in any case, and any spaces or tabs after the hashes.

### Values do not span lines

A value is the text after the colon on the field's own line, and the line break ends it. The next line is body text, even when no blank line separates the two. A label with nothing after its colon has no value.

### The fields

Each enumerated field reads as one of the tokens below, or `None` when absent or unrecognised.

| Field | Label | Reads as |
|---|---|---|
| `category` | `Category` | `pass`, `pass-with-caveat`, `gap`, `bug`, `pre-existing-bug`, `stale-assertion` |
| `severity` | `Severity` | `low`, `medium`, `high`, `critical` |
| `fix_effort` | `Fix Effort` | `small`, `medium`, `large` |
| `systemic_risk` | `Systemic Risk` | `low`, `medium`, `high` |
| `regression` | `Regression` | `yes`, `no` |
| `integrity` | `Integrity` | `none`, `breakage`, `destructive` |
| `filed_as` | `Filed As` | the first `BUG-N` or `PRIORITY-N` id anywhere in the value, in any case, returned upper-case with its number zero-padded to four digits; `None` when the value names no id (`none`, `not filed`) |
| `filed_by` | `Filed By` | the value as written |

A finding also carries three collections:

- **`extra`** — `{key: value}` for every other bold label. The key is the label lowercased, with each run of other characters turned into `_` (`Integrity Damage` becomes `integrity_damage`).
- **`raw`** — `{field: value}` for each named field present, the value as written after field-line unwrapping, so a caller can quote what the report said.
- **`sub_findings`** — the nested finding sub-blocks, each parsed as a finding.

### Report-level fields

- **`recommendation`** (`pass`, `ship-with-concerns`, `ship-with-serious-concerns`) and **`systemic_risk`** (`low`, `medium`, `high`) are read from the first non-blank line of their own `## Recommendation` or `## Systemic Risk` section. The section heading is level 1 or 2, with text starting with that name. Thematic breaks and deeper headings are skipped, and a value after a colon on the heading line itself comes first. The line is read whole first, then as a field line, so `SHIP-WITH-CONCERNS`, `**Decision: PASS**` and `Recommendation: pass` all read.
- **`summary`** is the text of the `## Summary` or `## Executive Summary` section, with comments removed and surrounding blank lines dropped, or `None`.

Nothing at report level is derived from the findings.

---

## Filing a Path C bug or priority

Named from Step 10 of `team/roles/TESTER.md`, which carries the filing
decision — which path, which filename, how the ID is claimed. This
section carries the two document shapes: the header block every filing
opens with, and the section list each type must contain.

**Required headers, in this order, at the top of the file.** A bug carries the block below; a priority carries the same plus `## Target Version` reading `auto` — **mandatory, never a `vX.Y.Z` literal**, because Path C output is framework-authored and the ship version is computed by discovery at bundle time.

```
**Filed By:** TESTER (autonomous Path C) — <one plain sentence of what was wrong>
**Source Task:** <this task ID>
**Source Report:** <relative path to artifacts/report.md from project root>

## Status

open
```

**The `Filed By` line is a fixed format.** The literal token `(autonomous Path C)` is preserved verbatim — tooling and the trace-classification heuristic key off it — and the ` — ` (space, em dash, space) plus a plain-language sentence are mandatory. That sentence names what was wrong in human terms: no bare acronyms, no using "Path C" as the whole explanation, no restating the filename. A reader who has never seen the bug must understand the defect at a glance from it. A worked example, which names the defect, explains the cause in one clause, and leans on neither jargon nor the filename:

```
**Filed By:** TESTER (autonomous Path C) — the per-project debug-log fix never actually ran because the provider wake scripts override it with the old global path.
```

Apply the same shape to every `Filed By` line you write — bugs, priorities, and any `Filed By` line inside `report.md`.

**Required sections, after the headers, in this order.** A **bug**: `## Symptom` (what was observed), `## Expected` (what should have happened), `## Reproduction` (numbered steps including environment), `## Fix` or `## Files Involved` (the concrete files and, if known, the change — speculation is welcome, this is hand-off context for CODER), `## Acceptance` (testable assertions proving the fix), `## Severity` (exactly one of `critical`, `high`, `medium`, `low`). A **priority**: `## Summary`, `## Goals`, `## Scope` (in and out), `## Acceptance Criteria` (a checklist matching the gap entries), `## Risk Assessment` (level and rationale). Copy the structure of `bugs/templates/BUG-TEMPLATE.md` or `priority/templates/PRIORITY-TEMPLATE.md`; those sections are the minimum.

**Where the current template lives.** The current bug template is the installed `$KANBAN_ROOT/templates/project/<workflow_type>/BUG-TEMPLATE.md` (`release/` for a release project), with the priority and requirements templates beside it. The project's `bugs/templates/BUG-TEMPLATE.md` is a copy that install and upgrade refresh from that set, together with the project's `priority/templates/PRIORITY-TEMPLATE.md` and `requirements/templates/REQUIREMENTS-TEMPLATE.md`, so filing from the project copy gives you the current structure. A project copy that differed from the installed template, whether an older release's template or an operator's edit, is kept beside it as `<file>.<UTC timestamp>.bak` and then overwritten. Copy from the `.md` file, never from a `.bak`: the backup is the previous copy, not the current template. `docs/OPERATIONS.md` ("Seeded intake templates on install and upgrade") covers the refresh in full.

---

## Aggregating lint-class findings into one filing

Named from Step 10 of `team/roles/TESTER.md`, which carries the rule:
quality-class lint findings file as one bug per class per run, that bug
lists every instance, and its severity is the worst instance's. This
section carries the worked example and the cases the rule's three
sentences do not settle on their own.

**Why the exception exists.** A pattern lint fires once per site, and a
single mechanical habit spread across a tree fires in dozens of places at
once. Filed one per instance, forty hits become forty bug files, forty
lines in the backlog cache, and forty items in the next bundle — a queue
no reader can scan and a decomposition PM cannot size. The defect
underneath is one defect, fixed once. The filing should say so.

**What "related" means here.** The rule is not a contradiction of
one-filing-per-gap; it is what *related* means for this one kind of
finding. Two hits share a filing when they share a `lint` value in the
same run's findings files — nothing weaker. Not "both are formatting."
Not "both are in the same file." Not "both look like the same mistake."
The `lint` key is already on every finding the gated runners emit, so the
grouping is read from the data, never judged.

**A worked example.** A run's `findings-unit.json` carries thirty-one
entries with `lint: provenance` and two with `lint: orphan-test`. The
provenance entries are `severity: low` except one at `medium`; both
orphan-test entries are `low`. That is **two** bugs, not thirty-three:
one provenance bug at `## Severity: medium` — the worst instance's rating,
carried up even though thirty of the thirty-one were `low` — listing all
thirty-one instances with file, line, and message; and one orphan-test bug
at `low` listing both. Neither bug elevates the recommendation: the suites
were green and no Step-7 defect was found, so the report stays `PASS` and
CM ships `FUNCTIONAL`.

**One class across both suites is still one class.** The unit and
integration findings files are two outputs of one verification run, so a
`lint` value appearing in both produces one bug, not two. List the
instances from both files inside it and name which suite each came from;
the suite is context on the instance, never a second grouping key.

**Severity is selected, never computed.** Take the maximum severity across
the instances under core's `Rating a Finding` ordering and write that
value. Do not average, do not take the first entry read, and do not round
a lone `medium` down because thirty siblings are `low` — the reason the
worst instance governs is that a filing's severity has to describe the
worst thing inside it, or a triager reading the header is misled about
what they are picking up.

**What does not aggregate.** Everything else. A finding that reached
Step 7 as `bug`, `gap`, or `stale-assertion` is a real defect and files on
its own, one filing per gap, even when two such findings look alike or
share a root cause — that rule is unchanged and this exception does not
reach it. If a lint finding turns out to describe a real defect, it stops
being a lint-class finding: promote it to a Step-7 finding on the normal
tree and file it alone. Laundering a defect into an aggregated filing to
keep the recommendation at `PASS` is the failure mode Step 5c names.

---

## Anti-patterns to avoid

Named from the summary list of the same name in `team/roles/TESTER.md`,
which states each mistake in one line. This is the full text, with the
reasoning behind each. **This passage is a protected surface:** a
requirements document that modifies it must name it explicitly and carry
regression language.

These are mistakes that ship bad RCs:

1. **Trusting structural checks for behavioral logic.** "The fix is in the code" is not the same as "the fix works." Always exercise behavior for new preventive logic. (Principle 4.)

2. **Skipping close reading of new subagent prompts and role files.** Errors in documentation-layer files don't trigger executable tests but cause runtime failures the first time the agent uses them. Read those files fully.

3. **Assuming synthetic input will match production input.** Materializer plans without a `sequence` field; wake-script invocations against an empty queue; cleanup scripts run on near-empty filesystems. Production-shape inputs hide bugs that synthetic inputs reveal. When practical, exercise the production shape.

4. **Letting "scope creep" exclude valuable judgment-based findings.** Spot-checks outside the spec consistently surface real issues — state drift, untested mechanisms, regressions. Step 6.11 is the explicit place for these. The principle is "trace back to requirements OR Step-10 categories" — not "trace back to requirements only."

5. **Ambiguous recommendations.** "Mostly passes, mostly ships" is not a decision. PASS, SHIP-WITH-CONCERNS, or SHIP-WITH-SERIOUS-CONCERNS. There is no BLOCK recommendation. Bugs go in the Path C (autonomous follow-up filing — non-blocking) filings you write; release notes are CM's responsibility.

6. **Verifying only the new normal case after a bug fix.** Always synthesize the original failure mode AND the new normal case, then verify both.

7. **Reporting suite PASS from direct pytest output.** `python3 -m pytest tests/unit/` and `python3 -m pytest tests/integration/` bypass the anti-pattern lint pre-flight that `run-unit-tests.sh` and `run-integration-tests.sh` enforce before pytest. A green pytest line does not establish a suite PASS — the lint can be red while pytest is green, and the gated runner (the command an operator or CM would actually run) would still exit non-zero. PASS evidence must be `bash team/scripts/run-unit-tests.sh` exit 0 AND `bash team/scripts/run-integration-tests.sh` exit 0. Reporting a PASS from direct pytest while the gated runner is failing the lint ships an RC that does not actually pass.

---

## Autonomous criterion expectations

Named from Step 6.12 of `team/roles/TESTER.md`, which asks the five
YES/NO questions; this section defines what the answers mean.

A build cycle is autonomous when it runs from PM materialization through CM-release without manual intervention on the task pipeline. This is not a preference — it is a verified success criterion TESTER checks explicitly in Step 6.12.

**What counts as autonomous.** Every task state transition was made by the wake script or by an agent acting on task-defined instructions. No human edited a `status.md` to force a state change the automation should have made. No queue backlog file was hand-edited to add, remove, or reorder entries. The project's `release-state.md` was updated only by `team/scripts/cm/open-rc.sh`, `team/scripts/cm/release.sh`, and `team/scripts/cm/cancel-rc.sh`. No script under `team/scripts/` or `subagents/` was patched while an RC build was in flight — after PM materialization, before CM-release.

**What does not disqualify it.** Not every human touch is an intervention. Using a HALT flag to pause and resume is permitted. So is manually completing a task that BLOCKED because the automation genuinely could not proceed — but only when the cause was a pre-existing external dependency outside the pipeline's control, and the action is recorded in `status.md`. So is opening or cancelling an RC by running `team/scripts/cm/open-rc.sh` or `team/scripts/cm/cancel-rc.sh` as the task plan directs.

**The remote-operation question.** Question (5) asks whether any agent other than CM invoked `fetch`, `pull`, or `push`. It is answered from a file, not from inference: the wake guard appends one record per attempt to `git-boundary-guard.jsonl` in the `logs/` directory of the task that made it, each record naming the role, the task, and the command's arguments. A YES means at least one such record exists somewhere in the cycle's task folders; no record in any of them is the NO, and that is the expected state of a healthy build. Read the records themselves rather than their count — the role and the command are what the finding reports.

**What a YES does not mean.** It does not by itself disqualify a build. The guard refuses the command for non-CM roles, so a recorded attempt is usually one that never reached the remote and a boundary that held. CM's own remote operations are not recorded and are never a YES — that role is exactly what the boundary lets through. Rate the record by what the attempt did to a running system, under core's "Rating a Finding": a refused attempt, or a read-only one that moved no ref and changed no state, is `low`. Reserve a higher rating for an attempt that actually changed ref state — a completed push, or a pull that moved a branch — because those publish or import work the pipeline never reviewed. A record written under role `unknown` means the guard could not determine the invoking role and let the command through; treat it as the same question with an unnamed actor and say so in the finding.

**A YES the file never shows.** The verifier's own probes are the one source the evidence file cannot see. A command run with `PGAI_AGENT_ROLE` set to `cm` is exactly the condition the guard lets through unrecorded, and one run with it unset fails open with at best a record under role `unknown`; a remote verb run either way from the verification session is a real crossing that no record in any task folder attributes to the verifier. Self-report it: answer YES in Step 6.12, name the command, and file it like any other question-(5) YES — the report is the only record that crossing will ever have. `team/roles/TESTER.md` ("Live-install safety") names role impersonation as forbidden in a probe and the network stub that exercises a CM-only path without it.

**What TESTER does with the answer.** Any manual intervention found in Step 6.12 is recorded as a finding and filed via Path C. If it indicates a defect that would recur, elevate the recommendation to at least `SHIP-WITH-CONCERNS`, and set `Systemic Risk: high` when the autonomous loop itself is unhealthy. A question (5) YES is filed the same way, naming the role, the task, and the command the record carries; a `low` boundary finding leaves the recommendation where the rest of the report puts it, and the case for elevating is a record showing ref state actually changed, or the same lane crossing the boundary across several tasks.

Core states what an agent does when autonomy fails mid-task — BLOCK, name the obstacle, stop, never simulate autonomy with undocumented state changes. That rule is core's, not restated here.

---

## Reading CODER's `## Stale Literal Risks` section

CODER's `status.md` may carry a `## Stale Literal Risks` section — the output of a pre-flight scan for literals in the production diff that also appear in test assertions, each line naming a test file, a line number, and the literal at risk. **It is advisory, not blocking.** Its presence changes no terminal state and requires nothing of TESTER beyond noting the flagged tests during verification; read it as a forward-looking hint that these tests are likely to fail in a future RC if the production value drifts further. **Empty or absent sections are acceptable** — `(none)` means the scan ran and found nothing, an absent section means the diff held no literals worth checking, and neither warrants a bug against CODER. The section is distinct from CODER's `## Possible Stale Assertions`, which records what CODER's own judgment surfaced; both may appear in the same status file.

---

## What TESTER does not verify

The scope is intentionally bounded. **Performance and load testing** are out — the kanban runs one task per five to ten minutes. **Security audits** are out — there is no untrusted input boundary; the human is the only input source. **Cross-platform compatibility** is out — Linux plus bash 4 is the only target. **Requirements quality** is out — whether the requirements doc was wise is a PO or human question. The exception is uniform: when a requirements doc explicitly states a performance or security criterion, verify that specific criterion.
