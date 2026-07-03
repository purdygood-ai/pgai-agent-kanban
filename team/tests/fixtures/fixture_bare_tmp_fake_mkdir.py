"""fixture_bare_tmp_fake_mkdir.py
==================================
Deliberate bad-example fixture: a Python file that uses os.makedirs with a
bare /tmp path whose name prefix was previously exempted by the lint (the
"fake*" pattern).  Under the zero-exemption contract (PRIORITY-0099), this
name prefix is no longer allowed — /tmp/fake_xyz is flagged exactly like
/tmp/real_dir.

This fixture is excluded from the normal CI lint scan by the fixtures/
directory guard.  It is scanned DIRECTLY by the unit tests in
test_lint_bare_tmp_fake_mkdir.py to prove the linter now flags it.

DO NOT add an opt-out marker here — the whole point is that the linter
must flag this file.
"""

import os

# Previously this line was exempt because the name starts with "fake".
# Under the zero-exemption contract it must be flagged.
os.makedirs("/tmp/fake_xyz", exist_ok=True)
