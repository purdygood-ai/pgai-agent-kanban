"""gamma.py -- seeded fixture: the same name with a different body, which
is not a copy."""


def flatten_text(value: object) -> str:
    return str(value).replace("\n", " ")
