# TESTER report reader differential

Every report in the TESTER report corpus (`corpus/`, beside this file) on which the legacy report readers disagree with each other, field by field, and how each disagreement is resolved. The legacy readers are the test-only copies under `legacy_readers/`, frozen as the readers stood before they moved onto the one parser, `pgai_agent_kanban.reports.tester_report`. A reader migration may change a reader's behaviour only where a row below says so, and only to the row's resolved value. Wherever the legacy readers agree, the parser reads the report the way they do.

`team/tests/unit/test_tester_report_differential.py` keeps this file true. It runs every legacy reader over every corpus report and fails when:

- this file lists a disagreement the readers do not have, or misses one they do;
- a row's reader values are not what the readers read;
- a row has no resolution reason;
- `parse_report` or the report's golden file (`golden/`) holds anything other than a row's resolved value;
- `parse_report` reads a field differently from the legacy readers anywhere they agree.

## How to read a row

- **Readers.** `stop_bug_files` is the shared block split and field reader of `legacy_readers/stop_bug_files.py`. It is one column because the Known Issues stop list, the Bugs Skipped table (`skipped_bug_pairs`) and the ship-policy matrix's `dims` and `values` modes all read their finding fields through it, and the test checks every one of those consumers against it on every report. `resolution_matrix` is OVERWATCH's finding parser, which shares the block split but has its own Filed As and Regression patterns. The ship-policy `values` mode is the only reader of the report-level Recommendation and Systemic Risk, so those fields cannot disagree and have no column.
- **Finding** is the finding's 1-based position in the report's finding split. It is the same as its position in the parser's findings flattened in order, each finding followed by its nested sub-findings. **Heading** is that finding's heading, shortened, for orientation only.
- **Values** are given in the parser's vocabulary: a lowercase token, or the canonical `BUG-NNNN` / `PRIORITY-NNNN` id a Filed As value names. `(absent)` means the reader read no value. `legacy_readers/differential.py` states each reader's projection in full.
- **Resolved** is the value `parse_report` returns and the golden file holds. **Reason** says why in one line.

## Summary

147 corpus reports; 48 carry a disagreement, 123 rows in all:

- 117 `regression` rows. The value is `yes` or `no` followed by an annotation (two of them, shortened: `yes (functionality present in the last shipped version, ...)` and `no -- established: ...`). The shared field reader reads the leading token. resolution_matrix's pattern accepts a bare `yes` or `no` only, so it reads the line as no Regression. The token is the reading.
- 6 `filed_as` rows. The finding is filed as a `PRIORITY-NNNN` priority file. resolution_matrix counts the finding as filed. The shared field reader reads `BUG` ids only, so it sees no filing. The parser names the priority id.

The legacy readers never disagree on the finding split or on any other field. `parse_report` already reads every row the resolved way, so no golden file needed changing.

## Disagreements

### pgai-agent-kanban--TESTER-20260905-018-verify-2-9-20.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: RC is branched from a stale ai_main and would ship a five-release ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: tree ships Dockerfiles but carries no team/scripts/deep-verify.sh | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: five unit tests read the operator's live bug ledger (gated unit ru... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260905-025-verify-2-9-21.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: CHANGELOG.md regenerated from the test fixture ledger — freshness ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: the unit suite silently edits a tracked file in the tree under test | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: shipped release notes carry an internal task identifier | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: three container images ship with no cold-build lane | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-011-verify-2-9-21.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: Cold-build failure detail repeats each failing pair once per log o... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: doc-lint feature-greps demands documentation for an image flavour ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-017-verify-2-9-22.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 8 | Finding 8: new lint's docstring cites an internal framework version | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 9 | Finding 9: deep_verify = off sits in the wrong INI section, so the switch is ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-023-verify-2-9-23.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 11 | Finding 11: internal ticket IDs left in the new test docstrings | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 12 | Finding 12: shipped v2.9.22 release notes carry a garbled duplicated change list | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-039-verify-2-10-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: TESTER and CM exceed their governance byte budgets; unit suite red | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: Role files disagree on the release-state.md schema, and the split ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: The helloworld behaviour-unchanged criterion was never exercised | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: The helloworld behaviour-unchanged criterion was never exercised | filed_as | (absent) | PRIORITY-0001 | PRIORITY-0001 | Filed as the priority PRIORITY-0001: resolution_matrix counted the finding filed and the parser names the id; stop_bug_files reads BUG ids only. A priority id names no bug file, so the Known Issues and Bugs Skipped lists still carry nothing for it. |
| 4 | Finding 4: Four of seven read-order manifests carry a stale header comment | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: A red unit suite leaves a stale lint findings file behind | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-048-verify-2-10-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 6 | Finding 6: relocated governance documents are not installed to a kanban root | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: the new regression test names the bug ticket that motivated it | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-057-verify-2-10-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: cm-release.sh Step 13c can never find promote_bundled_items.py | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: an explicit --team-root is silently overridden by ambient env, and... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: the shipped --help output cites the authoring ticket | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: a stray empty commit shipped in the RC's history | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-063-verify-2-10-3.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: The sanctioned upgrade path never delivers the documents, so the f... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: Both files added or changed by this RC cite the motivating ticket ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: Five runnable source lines in the installed role files load helper... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260907-075-verify-2-10-4.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 8 | Finding 8: lib/config.py docstring claims a precedence layer the code does no... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 9 | Finding 9: the provenance lint's printed re-run command cannot see repository... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 10 | Finding 10: a new module's docstring delegates its rationale to kanban task s... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260908-011-verify-2-11-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 2 | Finding 2: verify-rc-state.sh Check e ignores branch_prefix and always errors... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: A wake provider still takes the shared repo lock without provenanc... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: Generated queue-file marker legend omits [B], contradicting the vo... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260908-016-verify-2-11-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 5 | Finding 5: Check e no longer surfaces an unprefixed orphan RC branch on a pre... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 6 | Finding 6: Two RC-branch call sites elsewhere in the tree remain prefix-blind | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260908-029-verify-2-12-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: every role's assembled governance is over its configured byte budget | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: the new HALT-scope lint misses the local var="${KANBAN_ROOT}/HALT"... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: the operator HALT playbook still names the fleet-wide root HALT fo... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260908-038-verify-2-12-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 5 | Finding 5: BUG-0189 — one process-history pointer still ships | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 13 | Finding 13: BUG-0198 — two prefix-blind sites fixed; the tree-wide grep is no... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 14 | Finding 14: HALT-scope lint false-positives a literal project-scoped path | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 15 | Finding 15: mechanical provenance rewording left placeholder text in shipped ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260908-053-verify-2-13-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: unit gated runner exits 1 -- dual-channel lint misreads a command ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: piggyback carry ships without the specified task cap, and PM was n... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: shipped config templates and loader header state the new keys are ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: the refusal an operator actually meets does not name the accepted set | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260909-007-verify-2-12-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 6 | Finding 6: Runner-side branch_prefix fallback drops RC mode where the Python ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: Twenty-two shipped source files point the reader at a requirements... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260909-021-verify-2-13-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: Integration suite red — engine-behavior baseline golden not refres... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260909-029-verify-2-13-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 7 | Finding 7: census anchor markers that no Locator Index entry names | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260909-040-verify-2-14-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 2 | Finding 2: new wake-bootstrap comment cites the release-bundle slug | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: stray empty commit is the RC branch tip | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260909-045-verify-2-14-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 3 | Finding 3: PM governance byte budget exceeded — unit suite red | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260910-008-verify-2-14-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 5 | Finding 5: unit gated runner exits 1 -- provenance-lint tests scan the live r... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 6 | Finding 6: release-notes/v2.14.1.md:33 cites an internal task ID | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: two new comments cite team/SOP.md, a file removed from the tree at... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260910-017-verify-2-14-3.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 3 | Finding 3: BUG-0222 -- the task-ID leak is fixed in the file it named, but th... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: the new internal-ID scrub removes only the head of a task identifi... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260910-028-verify-2-15-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: installed docs cite docs/operator-api.md, which the installer does... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: why.sh --details discards the verdict exit code | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260910-037-verify-2-15-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 7 | Finding 7: this RC added ten new comment-provenance violations | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 8 | Finding 8: BUG-0224's structural half landed as an unwired helper | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260910-045-verify-2-15-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 4 | Finding 4: the render is unreachable on the notes path this project ships on | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260910-053-verify-2-15-3.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 5 | Finding 5: the new end-to-end test passes when the rendered table stops arriving | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 6 | Finding 6: one release-notes generation step is narrated under two step numbers | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: rendered Bugs-Skipped cells are not normalized for a markdown table | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260910-070-verify-2-16-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 6 | Finding 6: Malformed hygiene sidecar aborts both gated runners before pytest | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: Remedy commands persisted into auto-filed bugs name a deleted work... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260911-006-verify-2-16-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 3 | Finding 3: a hygiene-sidecar finding files a bug nobody can act on | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: lint_hygiene.py still comments an exit path this release deleted | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260911-030-verify-2-18-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1 — deep_verify true/false aliases are refused by the resolver the ne... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260911-053-verify-2-19-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: docs/OPERATIONS.md cites docs/dashboard.md, which the installer do... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: agent-metrics "Avg Wall" column is the RC's wall time, not the age... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260911-074-verify-2-20-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: Integration suite fails on a stale refusal-wording assertion | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: not-found remedy degenerates to an empty list and supersedes a mor... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: five verbs in the named set have no dual-channel refusals | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: read verbs do not resolve project keys by prefix; show-queues rend... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 5 | Finding 5: manifest lint's drift refusal names the default manifest, not the ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 6 | Finding 6: provenance litter in a new shipped config file, unseen by the hygi... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: placeholder sweep replaced a remedy with a canned reason the refus... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260912-007-verify-2-20-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: the global-halt "root does not exist" refusal creates the root, an... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: the verb fixture matrix records a REST-parity exemption reason tha... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: show-queues still renders a board of zeros for an unknown project ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260913-013-verify-2-20-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: the governance trim deleted census-lint finding documentation and ... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: two quality-class hygiene provenance findings survive, and both bu... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260913-027-verify-2-21-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: integration gated runner exits 1 on an environment-coupled golden | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: the new order-coupling checker does not control the order it compares | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: the blocking-site census rollup understates its own table | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260914-004-verify-2-21-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 4 | Finding 4: The golden-regeneration command the harness prints produces a gold... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 5 | Finding 5: No authoring-time detector for the environment-coupled-fixture class | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 5 | Finding 5: No authoring-time detector for the environment-coupled-fixture class | filed_as | (absent) | PRIORITY-0002 | PRIORITY-0002 | Filed as the priority PRIORITY-0002: resolution_matrix counted the finding filed and the parser names the id; stop_bug_files reads BUG ids only. A priority id names no bug file, so the Known Issues and Bugs Skipped lists still carry nothing for it. |

### pgai-agent-kanban--TESTER-20260914-012-verify-2-21-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 6 | Finding 6: The RC required a manual intervention — a hand-authored commit on ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 6 | Finding 6: The RC required a manual intervention — a hand-authored commit on ... | filed_as | (absent) | PRIORITY-0003 | PRIORITY-0003 | Filed as the priority PRIORITY-0003: resolution_matrix counted the finding filed and the parser names the id; stop_bug_files reads BUG ids only. A priority id names no bug file, so the Known Issues and Bugs Skipped lists still carry nothing for it. |
| 7 | Finding 7: A quality-class lint instance with no bug tracking it | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260914-026-verify-2-22-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: Release Acceptance clause "Doc-truth lint green" is not met | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: Key-parity check reports genuinely-accepted configuration keys as ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: Installed reference documents cite scripts that were renamed and n... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: pseudocron.py --help starts the scheduler instead of printing usage | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 5 | Finding 5: Two installed documents describe the previous dashboard layout | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 6 | Finding 6: dashboard/create.sh --help dies of SIGPIPE intermittently | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: The doc-truth lint is invoked by nothing, and its default scan set... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260914-040-verify-2-22-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: This RC introduces 23 new provenance references while fixing 3 | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: PGAI_TEST_ORDER=shuffle:<seed> is still not reproducible with pyte... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: key-parity findings name the wrong registry file | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260915-008-verify-2-22-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 6 | Finding 6: cleanup.sh ignores the sandbox override the role files instruct ag... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: cleanup.sh --dry-run reports a log line where the trivial-log coun... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260915-024-verify-2-22-4.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 6 | Finding 6: The export refusals compare exactly but say they compare containment | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 7 | Finding 7: Nothing warns that copying a linked git worktree carries its .git ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260915-044-verify-2-23-0.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: Lane A never performs step (a), the Fix rewrite | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: the v2.23.0 migration is never invoked, and its README still says ... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 3 | Finding 3: the Lane B refusal's remedy describes a rule the check does not im... | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 4 | Finding 4: ten provenance hygiene findings introduced by this RC | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260915-054-verify-2-23-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 7 | Finding 7: Lane A's new decline behaviour is absent from OVERWATCH.md and ope... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260915-061-verify-2-23-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 8 | Finding 8: the shared walk's docstring describes a call contract the code doe... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 9 | Finding 9: three bug files entered the project's bug folder outside the sessi... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260916-009-verify-2-23-3.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 6 | Finding 6: BUG-0295 item 3 — the swim-lane boundary ships untested | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20260919-011-verify-2-24-2.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 3 | Finding 3: the event band's signal set is 96.7% one repeated line | filed_as | (absent) | PRIORITY-0004 | PRIORITY-0004 | Filed as the priority PRIORITY-0004: resolution_matrix counted the finding filed and the parser names the id; stop_bug_files reads BUG ids only. A priority id names no bug file, so the Known Issues and Bugs Skipped lists still carry nothing for it. |

### pgai-agent-kanban--TESTER-20260919-027-verify-2-24-4.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 9 | Finding 9: A non-CM agent ran a remote git operation during the build | filed_as | (absent) | PRIORITY-0005 | PRIORITY-0005 | Filed as the priority PRIORITY-0005: resolution_matrix counted the finding filed and the parser names the id; stop_bug_files reads BUG ids only. A priority id names no bug file, so the Known Issues and Bugs Skipped lists still carry nothing for it. |

### pgai-agent-kanban--TESTER-20260920-008-verify-2-24-7.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 1 | Finding 1: the origin-boundary guard is bypassed by leading git global options | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 2 | Finding 2: this RC's wake wiring puts wake_driver_invoke.sh over the engine c... | regression | yes | (absent) | yes | The value is `yes` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |
| 6 | Finding 6: the integration runner exits 1 on a fully green suite (pre-existing) | regression | no | (absent) | no | The value is `no` followed by an annotation; the leading token is the reading (parser Rule 4, as stop_bug_files reads it), and resolution_matrix's whole-value yes/no pattern read the annotated line as no Regression. |

### pgai-agent-kanban--TESTER-20261004-007-verify-2-28-1.md

| Finding | Heading | Field | stop_bug_files | resolution_matrix | Resolved | Reason |
|---|---|---|---|---|---|---|
| 4 | Finding 4: REST deep-verify / cold-build-now cannot select container images | filed_as | (absent) | PRIORITY-0007 | PRIORITY-0007 | Filed as the priority PRIORITY-0007: resolution_matrix counted the finding filed and the parser names the id; stop_bug_files reads BUG ids only. A priority id names no bug file, so the Known Issues and Bugs Skipped lists still carry nothing for it. |
