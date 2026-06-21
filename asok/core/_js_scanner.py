from __future__ import annotations

"""Shared JS-aware character scanner.

Quote-awareness and escape handling is the same boilerplate in every parser
helper below; pull it into one class so each helper stays at A complexity.
"""


class JsScanner:
    """Stateful pointer over a JS source string that skips quoted regions."""

    __slots__ = ("s", "i", "in_quote", "escape")

    def __init__(self, s: str, start: int = 0) -> None:
        self.s = s
        self.i = start
        self.in_quote: str | None = None
        self.escape = False

    @property
    def char(self) -> str:
        return self.s[self.i]

    def remaining(self) -> bool:
        return self.i < len(self.s)

    def step(self) -> None:
        self.i += 1

    def advance(self) -> bool:
        """Consume escape/quote chars. Returns True if char was structural and
        the caller may still inspect it; False if it was already consumed."""
        if self._consume_escape():
            return False
        if self._consume_quote():
            return False
        return True

    def _consume_escape(self) -> bool:
        if self.escape:
            self.escape = False
            self.step()
            return True
        if self.char == "\\":
            self.escape = True
            self.step()
            return True
        return False

    def _consume_quote(self) -> bool:
        if self.in_quote:
            if self.char == self.in_quote:
                self.in_quote = None
            self.step()
            return True
        if self.char in ("'", '"', "`"):
            self.in_quote = self.char
            self.step()
            return True
        return False


def iter_structural_chars(s: str, start: int = 0):
    """Yield (index, char) for every char outside quotes/escapes."""
    sc = JsScanner(s, start)
    while sc.remaining():
        if sc.advance():
            yield sc.i, sc.char
            sc.step()
