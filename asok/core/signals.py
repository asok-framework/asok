from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("asok.signals")


class Signal:
    """A simple signal/event dispatcher for Asok lifecycle and request events."""

    def __init__(self) -> None:
        self._receivers: list[Callable] = []

    def connect(self, receiver: Callable) -> None:
        """Connect a receiver function to this signal."""
        if receiver not in self._receivers:
            self._receivers.append(receiver)

    def disconnect(self, receiver: Callable) -> None:
        """Disconnect a receiver function from this signal."""
        if receiver in self._receivers:
            self._receivers.remove(receiver)

    def send(self, sender: Any, **named: Any) -> None:
        """Send the signal to all connected receivers."""
        for receiver in list(self._receivers):
            try:
                receiver(sender, **named)
            except Exception as e:
                logger.error(
                    f"Error in signal receiver {receiver.__name__ if hasattr(receiver, '__name__') else receiver}: {e}",
                    exc_info=True,
                )


# Core application lifecycle signals
app_startup = Signal()
app_shutdown = Signal()

# Request lifecycle signals
request_started = Signal()
request_finished = Signal()
