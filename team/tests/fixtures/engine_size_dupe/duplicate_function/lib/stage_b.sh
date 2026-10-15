#!/usr/bin/env bash
# Seeded fixture: also defines seeded_shared_helper -- the planted duplicate.
# See driver.sh's docstring for why this is a genuine same-process collision
# rather than a false positive.
seeded_shared_helper() {
    echo "stage_b version"
}
