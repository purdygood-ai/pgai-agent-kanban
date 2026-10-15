# Read-Order Manifests and Governance Byte Budgets

**A document that is not named in a role's read-order manifest is not
read by that role. Full stop.**

That is the whole rule. There is no implicit stack, no "everything in
the kanban root," no convention by which a new governance file finds
its way into an agent's head. Every document an agent reads at wake
time is a line somebody typed into `read-order/<role>.txt`. If you
write a governance document and do not add that line, no agent will
ever see it.

This page is for the person maintaining the governance tree: what a
manifest is, how a line resolves to a file, what the wake composer does
with the result, what the byte budgets mean, and how to add or remove a
document from a role's wake.

Nothing here restates `team/SOP-core.md`. The reading order agents are
told to follow as a matter of law is in that file under "Required Read
Order For Assigned Work"; this page describes the machinery that
delivers it.

---

## Why the mechanism exists

A governance stack hardcoded inside the wake driver gives every role
the same documents, edited in bash whenever the stack changes. Every
agent re-reads the entire rulebook on every wake, and the rulebook
grows with no surface anywhere that shows it growing.

The manifests avoid that. Each role declares its own reading
list in a plain-text file, so the stack is data an operator can read
and edit rather than code a contributor has to find. The budgets
attached to it make the growth visible at the moment it happens instead
of in a token meter three RCs later.

Two consequences follow, and both are the point:

- **Adding a document to a wake is an explicit, reviewable edit** to a
  file whose entire purpose is to say what gets read.
- **A role's context can be trimmed without touching any other role.**
  TESTER's manifest and CODER's manifest are independent; a document
  that only TESTER needs costs CODER nothing.

---

## Where everything lives

| What | Dev tree | Live install |
|---|---|---|
| The manifests | `team/read-order/<role>.txt` | `$KANBAN_ROOT/read-order/<role>.txt` |
| The resolver | `team/scripts/lib/read_order.sh` | `$KANBAN_ROOT/scripts/lib/read_order.sh` |
| The composer | `team/scripts/lib/wake_governance_composer.sh` | `$KANBAN_ROOT/scripts/lib/wake_governance_composer.sh` |
| The budget lint | `team/scripts/lint_governance_byte_budgets.sh` | `$KANBAN_ROOT/scripts/lint_governance_byte_budgets.sh` |
| The budgets | `kanban.cfg_example` `[governance]` | `$KANBAN_ROOT/kanban.cfg` `[governance]` |

`install.sh` copies `team/read-order/` to `$KANBAN_ROOT/read-order/`
alongside `roles/`, `scripts/`, and the rest of the team tree. The
directory is deposited wholesale, so a manifest added in the dev tree
ships on the next install with no installer change.

There are seven manifests, one per role in the catalog: `po.txt`,
`pm.txt`, `coder.txt`, `writer.txt`, `tester.txt`, `cm.txt`, and
`overwatch.txt`. The filename is the lowercase agent name the wake
layer dispatches with — for the six pipeline agents, the same token
that appears in `wake-batch.sh --agent=<role>`. OVERWATCH sits outside
the RC pipeline and runs on its own schedule, but carries a manifest
and a budget like everyone else, for catalog completeness.

---

## The manifest format

Plain text. One path per line. That is the entire dialect — there are
no sections, no `key = value` pairs, no ordering directives, and no
include mechanism.

The rules, in full:

- **One path per line**, and the order of the lines is the order the
  documents are read in.
- **Paths are relative to the kanban root as the runtime layout
  resolves it.** `install.sh` flattens `team/` onto the kanban root, so
  a manifest line reads `roles/CODER.md`, never `team/roles/CODER.md`.
  This holds in the dev tree too, because the resolver anchors dev-tree
  lookups at `team/` — see "How a line resolves" below.
- **Blank lines are ignored.**
- **Lines whose first non-whitespace character is `#` are ignored.**
  Every shipped manifest opens with a comment header explaining what
  the role reads and why; that header is comment lines, not content.
- **Leading and trailing whitespace is stripped** from each line before
  it is interpreted, so indentation is harmless.

A manifest therefore looks like a comment block followed by a short
list of bare paths. Stripped of its header, the CODER manifest is:

```
DIRECTIVES.md
OVERVIEW.md
SOP-core.md
README.md
roles/CODER.md
```

All seven shipped manifests currently name that same five-document
shape, differing only in the role file on the last line. That is a
starting point, not a constraint — the mechanism exists precisely so
the lists can diverge.

### Two things a manifest must not contain

**The per-project README is not a manifest line.** In multi-project
mode each project may carry its own `README.md`, and whether it exists
is a runtime fact no static file can state. The composer resolves it at
dispatch time and splices it in immediately after whichever manifest
entry resolved to the kanban root's own `README.md`. Do not add a
per-project README path to a manifest; it would be wrong in every other
project.

**Task-specific documents are not manifest lines.** The task
`README.md`, `status.md`, and any context paths the ticket names are
assembled from the task folder, not from the manifest. A manifest
describes the standing governance a role reads on every wake, not what
one ticket happens to need.

---

## How a line resolves

`team/scripts/lib/read_order.sh` is the only code in the tree that
parses a manifest. Nothing else reads `read-order/*.txt` directly, and
nothing else should.

It exposes two functions:

- `read_order_resolve_role <role> [root_dir]` — prints the ordered,
  absolute paths of the existing files the manifest names, one per
  line.
- `read_order_role_byte_total <role> [root_dir]` — prints the summed
  on-disk byte size of exactly those files.

**Root resolution self-locates.** Rather than trusting an environment
variable, the resolver derives the root from its own location: two
directories up from `.../scripts/lib/read_order.sh`. In the dev tree
that lands on `team/`, which is where `read-order/` sits. In a live
install it lands on `$KANBAN_ROOT`, which is also where `read-order/`
sits. One rule, both layouts, no configuration. Callers that need a
different root — the test suite, an alternate checkout — pass it as the
optional second argument.

**Resolution never fails hard.** Two degradations are possible and both
are narrated to stderr rather than raised:

| Situation | What happens |
|---|---|
| A manifest line names a file that does not exist | That line is skipped; the rest of the manifest resolves normally |
| The role has no manifest file at all | Nothing is printed; the function returns success and the caller falls back |

This is deliberate. Manifest resolution is prompt-content assembly, not
a gate: a typo in a manifest must degrade the reading list, not wedge
the chain. The narration is how an operator finds the typo afterwards.

---

## The composer contract

The wake driver builds an agent's prompt in
`_wd_build_governance_line` (`team/scripts/lib/wake_governance_composer.sh`).
Its contract is short:

1. **Resolve the dispatched role's manifest** through
   `read_order_resolve_role`, using the role name the wake layer
   dispatched with.
2. **If the manifest resolved zero files** — missing manifest, or every
   line naming a file that does not exist — **fall back to the
   pre-manifest hardcoded five-document list** (`DIRECTIVES.md`,
   `OVERVIEW.md`, `SOP-core.md`, `README.md`, `roles/<ROLE>.md`) and
   write a `read_order:` line to the wake log recording that the
   degradation happened. The fallback exists so a broken manifest
   cannot leave an agent with no governance at all; the log line exists
   so nobody mistakes the fallback for the manifest working.
3. **Splice the per-project README line** in after whichever entry
   resolved to the kanban root's `README.md`, when the active project
   has a README of its own that is not the same file.
4. **Join the result, in manifest order,** into the sentence the wake
   prompt embeds after "read the governance stack: ". The last entry is
   phrased "your role file at ..." when it is in fact the role file,
   which every shipped manifest puts last.

The consequences for a maintainer are the ones that matter:

- **Order is preserved exactly.** The agent is told to read the
  documents in the order the manifest lists them.
- **Adding a document to a role's wake is a manifest edit, not a code
  change.** Nothing in `wake_governance_composer.sh` needs to be touched.
- **The composer never refuses.** There is no manifest error that stops
  a wake. The worst case is the fallback list plus a log line.

---

## The byte budgets

Each role has a budget for the total on-disk size of its assembled
governance stack. Budgets live in `kanban.cfg` under the `[governance]`
section, one key per role:

```
[governance]
budget_bytes_coder = 61440
```

The shipped defaults and the reasoning behind each number are
documented inline in `kanban.cfg_example`, which is the schema
reference for every key. A live install reads its own `kanban.cfg`.

**Units are bytes, always.** Never KB, never a mixed value. The
comments in `kanban.cfg_example` state a rough KB equivalent for human
readers, but the key's value is an exact byte count —
`budget_bytes_coder = 61440` is 60KB expressed the only way the lint
will read it.

**A missing key is not an error.** A role with no `budget_bytes_<role>`
key is reported `unbudgeted` and the lint carries on. A key whose value
is not a whole number is reported `invalid-budget` and treated the same
way, rather than crashing the comparison.

### Reading the lint

`team/scripts/lint_governance_byte_budgets.sh` resolves every role's
manifest through `read_order.sh` — it never re-parses the manifests
itself — sums the bytes, and prints one row per role. Run it on demand:

```
team/scripts/lint_governance_byte_budgets.sh [--kanban-cfg PATH] [--verbose]
```

It is not wired into the gated test runners. It is a maintainer's verb,
run when you want to know where the rulebook stands.

Example output (illustrative; the figures depend on the tree you run it
against):

```
ROLE             ASSEMBLED         BUDGET   VERDICT
po                   55337          57344   under
pm                   65145          65536   under
coder                60631          61440   under
writer               60381          61440   under
tester              112209          92160   OVER
cm                   82158          71680   OVER
overwatch            66164          66560   under
```

The verdict column has four values: `under`, `OVER`, `unbudgeted`, and
`invalid-budget`. `--verbose` additionally narrates which config file
the budgets were read from, surfaces the resolver's own stderr so a
skipped manifest line shows up in the same run that reports the totals,
and prints a closing reminder when any role is over that the result is
narrated, not a gate.

Config resolution, highest priority first: an explicit `--kanban-cfg`
path, then `$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg`, then a
self-located live-install `kanban.cfg`, then the dev tree's
`kanban.cfg_example`. A missing config file is not an error either —
every role simply reports `unbudgeted`.

### Why the lint narrates instead of blocking

Because an over-budget role is a quality finding, and quality findings
never stop The Loop.

The distinction The Loop draws is between work that moves the system
backwards and work that is merely imperfect. A tree that cannot build
its own artefact is backward motion — shipping it publishes something
that does not work, so it blocks. A role file that grew 3KB past its
budget is not backward motion. The chain still runs. Every agent still
reads its stack. Every task still completes. The only cost is tokens,
and the only repair is an editing pass somebody will do on a later
iteration.

Blocking on it would trade a real, immediate stop for a cost that is
diffuse and recoverable — exactly the trade The Loop is built to
refuse. So the lint always exits 0, whatever the verdicts read. It
carries no census classification, no census anchor, and no dual-channel
refusal, because it has no refusal to speak. It is not a gate, and the
new-gate rule in `team/SOP-core.md` "Gates and Refusals" does not apply
to it.

What the budgets buy is not enforcement. It is a number, printed on
demand, that makes rulebook growth visible at the moment it happens.
The remedy for `OVER` is the ordinary one: file it, and let the next
iteration trim the role file or move shared law into `SOP-core.md`.

---

## Adding a document to a role's wake

The whole procedure:

1. **Write the document** and place it where the runtime layout will
   find it — under `team/` in the dev tree, which `install.sh` flattens
   onto the kanban root.
2. **Add one line** to `team/read-order/<role>.txt` for each role that
   should read it, at the position in the list where you want it read.
   Write the path as the runtime layout resolves it: `roles/CODER.md`,
   not `team/roles/CODER.md`. Repeat per role — there is no way to add
   a document to every role at once, and that is intentional.
3. **Run the budget lint** and look at the affected roles. If a role
   crosses its budget, that is information, not a stop: decide whether
   to trim something, raise the budget in `kanban.cfg`, or accept the
   overrun and note it.
4. **Verify the resolution.** Source `team/scripts/lib/read_order.sh`
   in a shell and call `read_order_resolve_role <role>`. The list it
   prints is exactly what the composer will assemble. A path you
   expected and do not see was skipped because the file is not there;
   the resolver says so on stderr, and `--verbose` on the lint surfaces
   the same narration.

Removing a document is the same in reverse: delete the line from every
manifest that names it, then run the lint to confirm the byte total
dropped. Deleting the file without removing its manifest lines is not
fatal — the resolver skips the missing path and narrates it — but it
leaves dead lines in the manifests, so remove both.

**Adding a new role** means adding both halves: a
`team/read-order/<role>.txt` manifest and a `budget_bytes_<role>` key
under `[governance]`. A role with neither still wakes — the composer
falls back and the lint reports it `unbudgeted` — but it wakes with the
hardcoded list rather than a declared one, which is the state this
mechanism exists to leave behind.

---

## Where to read more

- `team/SOP-core.md`, "Required Read Order For Assigned Work" — the
  reading order as law, and the single canonical statement of it.
- `kanban.cfg_example`, `[governance]` — the schema for every budget
  key, with the reasoning behind each shipped default.
- `team/scripts/lib/read_order.sh` — the resolver's own header, if you
  need behavior this page does not cover.
- `ARCHITECTURE.md`, "Governance Stack" — where this mechanism sits in
  the system.
