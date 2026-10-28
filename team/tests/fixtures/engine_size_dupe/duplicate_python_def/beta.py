"""beta.py -- seeded fixture: a copy of flatten_text that differs only in
its parameter name, annotation and docstring, so it is still the same body."""


def flatten_text(text: str) -> str:
    # The same body as alpha.py's, under another parameter name.
    return " ".join(str(text).split())


def placeholder() -> None:
    pass


class Other:
    def describe(self) -> str:
        return " ".join(str(self).split())
