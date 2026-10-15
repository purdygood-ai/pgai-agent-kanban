# Project: <project-name>

Layer 3 of the instruction stack: the rules that apply only when a task belongs to **this** project. Agents read it after their role file and before the task README (`OVERVIEW.md`, "How to read the kanban's instruction stack"). Install it as `projects/<project-name>/README.md`. Keep it to this project's facts: generic procedure belongs in `SOP-core.md` and the role files, never here.

## Identity

| Setting | Value |
|---|---|
| Dev tree | `<path>` (tasks run in isolated worktrees of it) |
| Branch prefix | `<prefix or none>` — main branch, RC branch and tag names |
| Remote | `<origin and push_to_remote>` |
| Workflow | `<release / feature / document>` |
| Orientation | `<the repo's own README / architecture docs>` |

## Tests

- **Targeted run (CODER/WRITER):** `<command that runs only named test files>`
- **Certification (TESTER):** `<the gated runner command(s) whose exit codes are the verdict>`
- **Never:** `<anything that costs money, needs a container engine, or touches shared state>`

## Coding standard and checks

- **Coding standard:** `<file, or "match the existing code">`
- **Pre-merge checks:** `<lints or generators to run before merging, and whether they block>`
- **Contracts:** `<API/ICD versions, generated files, schema rules — who updates what>`

## Release notes

`<where release notes live and who writes them>`
