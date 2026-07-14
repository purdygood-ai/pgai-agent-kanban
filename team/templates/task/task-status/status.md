# Status

## Task
<TASK-ID>

## Participant
CLAUDE | HUMAN

## Role
CODER | WRITER | TESTER | CM | PM | PO

## Model
<!-- Wake-stamped at spawn time from the resolved model string. Agents must NOT
     write this field — it is an execution record written by the wake script. -->
<wake-stamped>

## State
<!-- One of: BACKLOG WAITING WORKING BLOCKED DONE WONT-DO -->
BACKLOG

## Summary
Describe what was completed or what is currently happening.

## Artifacts
List files created, modified, or relevant to review. If none, write `none`.

none

## Blockers
State exact blockers. If none, write `none`.

none

## Blocked By Agent
<!-- OPTIONAL. When state is BLOCKED, record which agent or system is blocking
progress. Accepts: CODER, WRITER, PM, TESTER, CM, BUG, HUMAN, or a service name.
Leave as "none" when not blocked. -->
none

## Blocked Reason
<!-- OPTIONAL. When state is BLOCKED, provide a concise human-readable explanation
of exactly why the task cannot proceed. Leave as "none" when not blocked. -->
none

## Needs Human
no

## Next Recommended Step
What should happen next.

## Instruction Conflicts
Describe any instruction conflicts discovered. If none, write `none`.

none
