# Bug Reports

Filed by TESTER or operators. Used by the discovery pipeline (Step 1) to
produce patch RCs.

Naming: `BUG-NNNN-YYYYMMDD-<slug>.md` — sequential numbering, never reused.

`## Status` field is authoritative: `open` | `running` | `done`. Cache is
in `tasks/queues/bug_backlog.md`.

`## Category` field classifies the change for release notes generation.
Valid values: `feature` | `bugfix` | `breaking` | `deprecation` | `removal` |
`docs` | `misc`. Default: `misc` (used when field is absent).
