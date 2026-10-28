"""delta.py -- seeded fixture under a tests/ directory, which the scan
leaves out."""


def flatten_text(value: object) -> str:
    return " ".join(str(value).split())
