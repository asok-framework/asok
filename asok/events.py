import threading
from typing import Callable, Dict, List


class EventEmitter:
    """Simple, thread-safe event emitter for internal framework communication."""

    def __init__(self):
        self._listeners: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()

    def on(self, event: str, callback: Callable):
        """Register a listener for an event."""
        with self._lock:
            if event not in self._listeners:
                self._listeners[event] = []
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
