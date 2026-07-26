"""
Fixture script for the stray_import_project — contains a planted stray import.
Used by test_lint_python_import_completeness.py to verify the lint exits nonzero
and names the missing module and this source file.

The import 'httpx' is intentionally NOT listed in the fixture requirements files.
"""

from __future__ import annotations

import os
import sys

import fastapi  # covered
import httpx    # NOT covered — this is the planted stray import
