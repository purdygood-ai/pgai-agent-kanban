<!--
GUIDE NOT GATE: This template is a guide, not a gate.

The structure here is a starting point. The TESTER fills in every section
when writing a priority requirements document during autonomous Path C
(autonomous follow-up filing — non-blocking; gap handling, Step 19 of the
verification methodology).

Agents and humans consuming this document must tolerate imperfect or
partial sections. The absence of a section is never a hard error. However,
the closer this document is to the full structure below, the better the PM
agent can decompose the bugfix release and the better the subsequent CODER
and TESTER agents can execute against it.

Contract for agents reading requirements produced from this template:
  - Required sections: Tracks, Goals, Bug Entries, Deliverables,
    Acceptance Criteria, Context Paths
  - Optional sections: Suggested Decomposition, Constraints, Notes
  - If a required section is missing or vague, flag it in status.md and
    proceed with the best available interpretation
  - Never block work solely because a template section is absent

Authorship note: this template is exclusively for TESTER-authored priority
documents. It differs from REQUIREMENTS-TEMPLATE.md in that it includes
Tracks, Bug Entries, and Suggested Decomposition — sections that make sense
only in the context of a verification-driven bugfix cycle.
-->

<!--
Authored By: TESTER — autonomous follow-up filing, non-blocking (Path C)
Source Task: <TESTER-YYYYMMDD-NNN-slug of the TESTER task that found these gaps>
Source Report: <absolute path to artifacts/report.md from that task>
-->

## Status
<!--
Required field. Discovery's Step 2 bundler filters priority/ files by
"## Status" with value "open". Files missing this field are invisible to
the bundler and will never be queued for PM. Always set the value to
"open" when writing a new priority requirements document.
-->
open

# Project: v<X.Y.Z+1> bugfix — <2-5 word human-readable description of the gaps>

<!--
Filename convention: v<X.Y.Z+1>-bugfix-<short-slug>.md
Where <X.Y.Z+1> increments the patch version of the RC that failed verification
and <short-slug> is a 2-4 word kebab-case description of the gap cluster.

Example: vX.Y.Z+1-bugfix-queue-path-gaps.md
-->

## Target Version
<!--
For TESTER autonomous Path C (autonomous follow-up filing — non-blocking) output: write `auto`. Do NOT embed a specific
vX.Y.Z literal. This file is framework-authored — TESTER cannot know which
version slot will be free when discovery/materializer pick the file up
(other priorities or bug bundles may interleave). The framework computes
the actual version at bundle/materialization time.

Operator-authored requirements files (e.g., vX.Y.0-foo.md) DO declare an
explicit version here, because the operator is expressing deliberate
intent. The auto sentinel is exclusively for framework-authored output
like this Path C (autonomous follow-up filing — non-blocking) template.

The `auto` sentinel lets the framework assign the next available patch version automatically.
-->
auto

## Git Repo
<!--
The same git repository URL as the RC that was verified.
Example: git@github.com:org/repo.git
-->
<git repo URL>

## Workflow Type
release

## Human Approval Required
<!--
Use "auto" for bugfix releases that restore a broken invariant.
Use "required" if the gaps touch release-lifecycle scripts or state files
and a human should review before CM-release fires.
-->
auto

## Tracks
<!--
List each gap or bug ID this document addresses.
Copy the gap IDs or short names from artifacts/gaps.md.
One entry per line.
-->
- Gap: <gap-id or short name from gaps.md>
- Gap: <gap-id or short name from gaps.md>

## Goals
<!--
One paragraph. Summarize what this bugfix release must accomplish.
Reference the gaps by name. State the invariant being restored.
Do not list implementation steps here — save those for Bug Entries.

Example: "This release fixes two gaps found during verification of the
prior RC: the queue-path bare-reference bug (a tester step false negative)
and the missing env_example update (documentation discipline gap). After
this release, queue-path constraint checks will correctly flag bare
references in subagent prompts, and env_example will match the production
env file."
-->
<One-paragraph summary>

## Bug Entries
<!--
One block per gap from gaps.md. Copy the short name from the gap entry.
Fill in all four fields for each bug.
-->

### Bug: <short name matching gap entry in gaps.md>

**Symptom:** <What the TESTER observed — the externally visible failure mode.
Be specific: which step failed, what output was wrong, what assertion did not
hold.>

**Root Cause:** <The underlying implementation defect that caused the symptom.
Point to the file, function, or logic path that is broken.>

**Fix:** <What the coder must change to resolve the defect. Be specific about
file paths, function signatures, and the nature of the change. If the fix is
ambiguous, describe two or three acceptable approaches and indicate a
preference.>

**Acceptance:** <One testable assertion that proves the fix is present and
correct. Phrased as a command the TESTER can run or an observable state the
TESTER can check. Mirrors the corresponding entry in ## Acceptance Criteria
below.>

<!--
Repeat the ### Bug block above for each gap.
One block per gap — do not merge multiple gaps into one bug entry.
-->

## Suggested Decomposition
<!--
A recommended task breakdown for the PM agent. Adjust row count to match the
number of fixes needed. The PM agent uses this as a starting point but may
reorder or merge tasks as needed.

Standard bookends for a release workflow:
  - Ticket 1: CM open-rc (always first)
  - Ticket N: TESTER verify (after all CODER fixes)
  - Ticket N+1: CM release (after TESTER passes)

Add one CODER row per distinct fix. If two fixes touch the same file but are
logically independent, keep them as separate CODER tasks.
-->

| # | Role   | Task Slug                      | Depends On |
|---|--------|-------------------------------|------------|
| 1 | CM     | open-rc                        | —          |
| 2 | CODER  | <slug for fix 1>               | 1          |
| 3 | CODER  | <slug for fix 2 — if needed>   | 1          |
| 4 | TESTER | verify-v<X.Y.Z+1>              | 2, 3       |
| 5 | CM     | release-v<X.Y.Z+1>             | 4          |

## Deliverables
<!--
Bulleted list of files to create or modify.
One bullet per file. Include the path relative to the repo root
and a brief description of what changes.
-->
- `<path/to/file1>` — <what changes and why>
- `<path/to/file2>` — <what changes and why>

## Constraints
<!--
Hard rules the fixing agents must follow.
Always include the first three bullets below. Add more as needed.
-->
- All previously-passing acceptance criteria from v<X.Y.Z> must remain passing.
- No new features. Bugfix scope only — each change must trace to a specific gap entry in gaps.md.
- Each fix must be traceable to a specific gap entry in ## Tracks.
- <any constraint specific to the file or component being fixed>

## Acceptance Criteria
<!--
One checkbox per bug entry above. Phrased as a testable command or assertion.
These should be runnable by the TESTER in the next verification cycle.
Mirror the Acceptance field of each Bug Entry above.
-->
- [ ] <testable assertion for bug 1 — mirrors Bug Entry 1 Acceptance field>
- [ ] <testable assertion for bug 2 — mirrors Bug Entry 2 Acceptance field>

## Context Paths
<!--
Files the PM and CODER agents should read for context.
Always include the gaps.md and report.md from the source TESTER task.
Add other relevant files as needed.
-->
- <absolute path to artifacts/gaps.md from the source TESTER task>
- <absolute path to artifacts/report.md from the source TESTER task>

## Notes
<!--
Optional. Anything else the PM, CODER, or future TESTER should know.
Edge cases, warnings, related bugs, historical context.
-->
