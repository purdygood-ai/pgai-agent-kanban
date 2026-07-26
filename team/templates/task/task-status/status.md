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
of exactly why the task cannot proceed. Leave as "none" when not blocked. This is
narrative only — it is reset to "none" on every BACKLOG promotion. It is never
the source of truth for retry counting; see ## Transient Retry below for that. -->
none

## Transient Retry
<!-- OPTIONAL/system-managed. The ONE durable, machine-readable retry record for
transient provider errors. Written exclusively by wake_common.sh's
handle_working_after_exit; read by recheck_blocked_tasks and OVERWATCH's
check-transient-api-error.sh. Survives BACKLOG promotion (unlike Blocked Reason
above) so the count never resets while a task is still within the retry
ceiling. Agents should not hand-edit this section. Format when populated:
  reason_code: transient_api_error
  retry_count: <n>
  retry_limit: <n>
  provider: claude|codex
  last_error: <short signature fragment>
Leave as "none" when no transient retry has ever occurred. -->
none

## Needs Human
no

## Next Recommended Step
What should happen next.

## Instruction Conflicts
Describe any instruction conflicts discovered. If none, write `none`.

none
