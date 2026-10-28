"""alpha.py -- seeded fixture: the first copy of flatten_text."""


def flatten_text(value: object) -> str:
    """Every whitespace run becomes one space."""
    return " ".join(str(value).split())


def placeholder() -> None:
    pass


class Holder:
    def describe(self) -> str:
        return " ".join(str(self).split())
