# docs/ — Operator Documentation Index

Every operator-facing page lives here. Nothing important is reachable
only by directory listing. Start at the row that matches the task in
front of you.

## Routing table

| Page | When to read it |
|---|---|
| [OPERATIONS.md](OPERATIONS.md) | The operator walkthrough — HALT commands, dashboard/metrics tours, RC recovery, provider switch, project bootstrap. Companion to `team/SOP.md`; NOT part of the agent read order. |
| [operator-api.md](operator-api.md) | Start or query the localhost HTTP API; wire up the UI or a `curl` client. **Experimental** — full automated coverage; contract published (ICD 1.1.0); operator field-testing in progress; surfaces may be refined. |
| [operator-api.md § 8 ICD](operator-api.md#8-icd--the-published-api-contract) | The published API contract (`docs/api/icd.json`): version semantics, pinning guidance, regeneration command, additive-only promise, and the supported-version table. |
| [operator-commands.md](operator-commands.md) | Every operator command, every flag, worked examples. |
| [../team/workflows/README.md](../team/workflows/README.md) | The workflow-type plugin surface — manifest, hooks, optional `pipeline.yaml`. |
| [creating-a-workflow.md](creating-a-workflow.md) | Author a new workflow-type plugin end-to-end (live-operator and contributor paths). |
| [pseudocron.md](pseudocron.md) | Run the chain on a host where cron is unavailable or unwanted. |
| [codex-known-issues.md](codex-known-issues.md) | Rough edges in the experimental Codex/OpenAI lane. |
| [po-briefs.md](po-briefs.md) | The PO agent's operator guide — briefs, draft mode, batch workflow. |
| [overwatch.md](overwatch.md) | OVERWATCH condensed: two tiers, on-BLOCK trigger, whitelist, `HALT_OVERWATCH`. |
| [operator-troubleshooting.md](operator-troubleshooting.md) | Five common symptoms (slow chain, RC stuck, work vanished, provider warning). |
| [quarantine-recovery.md](quarantine-recovery.md) | Recover files quarantined by intake. |
| [disk-hygiene.md](disk-hygiene.md) | Log rotation, artifact cleanup, worktree pruning. |
| [dashboard.md](dashboard.md), [DASHBOARD-PANES.md](DASHBOARD-PANES.md) | The tmux dashboard: metrics window and the pane inventory. |
| [projects-cfg.md](projects-cfg.md) | The project registry format. |
| [public-contract.md](public-contract.md) | Stable-surface guarantees across the 1.x line. |
| [coding-standards.md](coding-standards.md) | The authoritative coding-standards directives — every code-producing role in the shop, and every project the kanban builds, follows this list. |
| [hybrid-shop-setup.md](hybrid-shop-setup.md) | Configure a shop where humans and agents share the same repo. |
| [operator-checkpoints/](operator-checkpoints/) | Operator-run verification checkpoints filed against past releases. |
| [../release-notes/](../release-notes/) | One file per shipped version; `## Status` stamped `FUNCTIONAL`, `KNOWN-BUGS`, or `NON-FUNCTIONAL` at release time. |

## Upgrades

`upgrade.sh` runs two phases: a stable Phase-1 bootstrap in the installed
tree, then Phase-2 in the dev tree. Both the top-level
[README.md](../README.md) (Feature census) and
[pseudocron.md](pseudocron.md) cover the crontab prompt and the
`--wake-tier` flag. If an upgrade wedges the install, see
[operator-troubleshooting.md](operator-troubleshooting.md) first.

## Release notes

The `../release-notes/` directory carries one file per shipped version.
`## Status` is stamped `FUNCTIONAL`, `KNOWN-BUGS`, or `NON-FUNCTIONAL` at
release time — see the top-level [CHANGELOG.md](../CHANGELOG.md) for the
canonical release history.

## Not in this index

The install-and-daily-operation walkthrough lives at
[../HOW_TO.md](../HOW_TO.md); architecture lives at
[../ARCHITECTURE.md](../ARCHITECTURE.md). Both are top-level, not under
`docs/`, because they are read before you dig into the docs tree.
