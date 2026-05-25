from __future__ import annotations

from typing import Any, Iterable, Iterator


class _Loop:
    """Helper for tracking loop state (index, first, last, etc.) within template for-loops.

    SECURITY: Limits iterable size to prevent DoS with huge collections.
    """

    def __init__(self, iterable: Iterable[Any]):
        # Convert to list if needed
        if not hasattr(iterable, "__len__"):
            # SECURITY: Limit iterable size to prevent DoS (max 100,000 items)
            temp_list = []
            for i, item in enumerate(iterable):
                if i >= 100_000:
                    break
                temp_list.append(item)
            self._iterable = temp_list
        else:
            # SECURITY: Limit collection size to prevent DoS (max 100,000 items)
            if len(iterable) > 100_000:
                self._iterable = list(iterable)[:100_000]
            else:
                self._iterable = iterable

        self.length: int = len(self._iterable)
        self.index0: int = -1

    def __iter__(self) -> Iterator[Any]:
        for item in self._iterable:
            self.index0 += 1
            yield item

    @property
    def index(self) -> int:
        """The current 1-based index of the loop."""
        return self.index0 + 1

    @property
    def first(self) -> bool:
        """True if this is the first iteration of the loop."""
        return self.index0 == 0

    @property
    def last(self) -> bool:
        """True if this is the last iteration of the loop."""
        return self.index0 == self.length - 1
