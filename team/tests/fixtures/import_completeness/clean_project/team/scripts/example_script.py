"""
Fixture script for the clean_project — imports only stdlib and covered packages.
Used by test_lint_python_import_completeness.py to verify the lint exits 0 on
a tree with no uncovered third-party imports.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import fastapi  # covered by requirements.txt
import pydantic  # covered by requirements.txt
