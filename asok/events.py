import threading
from typing import Callable, Dict, List


class EventEmitter:
    """Simple, thread-safe event emitter for internal framework communication.

    SECURITY: Limits prevent DoS via excessive listener/event registration.
    """

    # SECURITY: Maximum limits to prevent DoS
    _MAX_EVENTS = 1000
    _MAX_LISTENERS_PER_EVENT = 100

    def __init__(self):
        self._listeners: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()

    def on(self, event: str, callback: Callable):
        """Register a listener for an event.

        SECURITY: Limits number of events and listeners per event to prevent DoS.
        """
        with self._lock:
            # SECURITY: Limit number of distinct events
            if event not in self._listeners:
                if len(self._listeners) >= self._MAX_EVENTS:
                    import logging

                    logging.getLogger("asok.events").warning(
                        "Max events limit reached (%d), ignoring event: %s",
                        self._MAX_EVENTS,
                        event,
                    )
                    return
                self._listeners[event] = []

            # SECURITY: Limit listeners per event
            if len(self._listeners[event]) >= self._MAX_LISTENERS_PER_EVENT:
                import logging

                logging.getLogger("asok.events").warning(
                    "Max listeners limit reached (%d) for event: %s",
                    self._MAX_LISTENERS_PER_EVENT,
                    event,
                )
                return

            self._listeners[event].append(callback)

    def off(self, event: str, callback: Callable):
        """Unregister a listener."""
        with self._lock:
            if event in self._listeners:
                try:
                    self._listeners[event].remove(callback)
                except ValueError:
                    pass

    def emit(self, event: str, *args, **kwargs):
        """Emit an event, calling all registered listeners."""
        with self._lock:
            listeners = self._listeners.get(event, []).copy()

        for listener in listeners:
            try:
                listener(*args, **kwargs)
            except Exception as e:
                import logging

                logging.getLogger("asok.events").error(
                    f"Error in listener for {event}: {e}"
                )


# Global event bus
events = EventEmitter()
