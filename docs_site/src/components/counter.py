from asok import Component
from asok.component import exposed


class Counter(Component):
    """Reusable UI component for Counter."""
    count = 0

    @exposed
    def increment(self):
        self.count += 1

    @exposed
    def decrement(self):
        self.count -= 1

    @exposed
    def reset(self):
        self.count = 0

    def render(self):
        return self.html("counter.html")
