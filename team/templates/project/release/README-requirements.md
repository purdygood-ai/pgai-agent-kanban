# Requirements Documents

Three sources land docs here:
1. Operator-authored (minor/major releases)
2. Bug bundles (Step 1 of pipeline)
3. Priority bundles (Step 2 of pipeline)

PM picks up the lowest target_version > Last Released. See templates/.

`## Category` field classifies the change for release notes generation.
Valid values: `feature` | `bugfix` | `breaking` | `deprecation` | `removal` |
`docs` | `misc`. Default: `misc` (used when field is absent).
