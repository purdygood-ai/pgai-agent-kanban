# Inherited-Environment Exposure Survey

**The number in this document is an exposure count, not a defect count.**
It counts the places in `team/tests/` where a subprocess environment is
built by inheriting the ambient one. Most of those places are correct as
written and must stay that way. Nothing here is a list of things to fix.

Read this before proposing work against the environment-coupling class.
The rule the survey supports is `docs/test-authoring-guidelines.md`,
anti-pattern 2 (environment-coupled fixtures); this document is the
measurement behind it, kept separately because it ages on a different
clock than the rule does.

## What was measured

Two text scans over the test tree, for the two idioms that build a child
environment out of the parent's:

```bash
# occurrences (one per matching line)
grep -rnE 'dict\(os\.environ\)|os\.environ\.copy\(\)' team/tests/ | wc -l

# distinct files carrying at least one
grep -rlE 'dict\(os\.environ\)|os\.environ\.copy\(\)' team/tests/ | wc -l
```

Run both from the repository root. They are deliberately crude: a text
scan, not an AST walk. A match inside a docstring or a comment counts,
and so does a match inside a fixture that exists precisely to prove the
lint fires. Treat the figure as an order of magnitude with a reproducible
definition, not as a census of live call sites.

## The counts, and the tree they were measured against

| Tree state | Occurrences | Files |
|---|---|---|
| `a84deb7` (current, after the shared helper, the two harness conversions, and the new check landed) | 160 | 109 |
| `7f7a37d` (the tip this line of work started from) | 157 | 107 |

Both rows were produced by running the two commands above; the earlier
row was re-derived from history with `git grep` at that commit rather
than quoted from memory.

The count went **up** by three occurrences across two more files, and the
movement is entirely accounted for:

- **Two occurrences left.** Both golden-comparison harnesses —
  `team/tests/integration/test_engine_behavior_baseline_harness.py` and
  `team/tests/integration/test_helloworld_flow_harness.py` — now build
  their subprocess environment explicitly, so both dropped out of the
  set entirely.
- **Five occurrences arrived, in four new files.** The shared helper
  `team/tests/fixtures/scrubbed_subprocess_env.py` names the inherited
  shape in its own docstring to explain what it exists to avoid; the
  check's two seeded fixtures and its negative control
  (`team/tests/fixtures/fixture_golden_comparison_inherited_env.py`,
  `team/tests/fixtures/fixture_inherited_env_without_golden_comparison.py`)
  contain the pattern on purpose, because a detector that cannot be
  shown to fire is not a detector; and the check's unit test
  (`team/tests/unit/test_lint_golden_env_coupling.py`) carries it once
  more as test data.

That is the whole story of the delta: two real sites converted, and five
matches that are prose or deliberate bait. A rise in this number is not
by itself bad news, which is the clearest possible demonstration of why
it is not a defect count.

## Why most of these sites are correct

A subprocess that runs `git`, `bash`, or `python3` needs a real `PATH`.
A subprocess that reads a config file needs a plausible `HOME`. Inheriting
the parent environment is the ordinary, cheap way to get both, and for a
test whose assertions are about return codes, emitted files, or log lines,
the inheritance costs nothing — no assertion in such a test can notice
what else the shell was carrying.

The exposure is latent, not present. Each of these sites is a place where
a *future* assertion — one that compares a captured surface byte-for-byte
against a committed artifact — could quietly acquire a dependency on the
verifying host. The verdict would then be a property of whoever ran the
suite rather than of the tree, and it would present as a suite that is
green everywhere except one machine.

## The sub-class that is actually dangerous

The pairing that turns latent exposure into a host-dependent verdict is
narrow: **a golden comparison plus an inherited environment, in the same
file.** That pairing, and only that pairing, is what
`team/scripts/lint_test_anti_patterns.py` reports under the category
`AP7`. Neither half alone is a finding — a golden comparison with an
explicitly built environment is correct and silent, and an inherited
environment with no golden comparison is one of the many ordinary sites
counted above and is also silent.

The check is quality-class: it records a finding and the run continues.
Both gated runners invoke it as part of the `test_anti_patterns` lint. It
reports zero findings against the tree as measured above.

The remedy the finding points at is
`tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env`, the shared
builder that assembles an environment from an explicit set of names
instead of inheriting one. `team/TESTING.md` says when to reach for it;
`docs/test-authoring-guidelines.md` anti-pattern 2 states the rule.

## What the intended follow-up is — and is not

**Converting all of these sites is not the follow-up, and a blanket
rewrite is the specific outcome this document exists to prevent.** Most
of the counted sites have no defect behind them. Rewriting them would be
churn measured in three figures, carrying a real regression risk in tests
that currently pass, in exchange for closing an exposure that in almost
every case cannot be reached. A reader who mistakes 160 for a backlog
will either raise an alarm that is not warranted or start a rewrite that
costs more than the class it addresses.

What is worth doing, in order:

1. **Author new subprocess tests with the shared builder.** This is where
   the class stops growing, and it costs nothing at authoring time.
2. **Convert a site when it acquires a golden comparison**, not before.
   The check will say so; that is what it is for.
3. **Re-measure before proposing anything against this class**, using the
   commands above and naming the tree state, so the next reader inherits
   a figure they can check rather than one they must trust.

## Re-measuring

Run both commands from the repository root and record the commit they
were run against, exactly as the table above does. A figure with no tree
state attached cannot be falsified and is worth less than no figure at
all. If the number has moved, account for the movement before drawing a
conclusion from it — as the delta section above shows, the direction of
travel on its own says nothing.
