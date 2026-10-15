# TEST-ONLY -- imported by no production code.
"""Legacy TESTER-report readers, kept as test-only copies for this release.

Each module here is a frozen copy of a TESTER-report reader as it read
before the readers moved onto the one parser,
pgai_agent_kanban.reports.tester_report, and differential.py runs them over
the report corpus. team/tests/unit/test_tester_report_differential.py is
their only user; the package is deleted together with it once the readers
are migrated. Production code never imports from here.
"""
