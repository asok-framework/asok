from __future__ import annotations

import contextvars
import re
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_RE_TRACEPARENT = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)

_ZERO_TRACE_ID = "0" * 32
_ZERO_SPAN_ID = "0" * 16

_current_span_var: contextvars.ContextVar[Optional[Span]] = contextvars.ContextVar(
    "asok_current_span", default=None
)


def generate_trace_id() -> str:
    """Generate a random 16-byte (32 hex characters) W3C trace ID."""
    return secrets.token_hex(16)


def generate_span_id() -> str:
    """Generate a random 8-byte (16 hex characters) W3C span ID."""
    return secrets.token_hex(8)


def _validate_trace_parts(version: str, trace_id: str, parent_id: str) -> bool:
    if version != "00":
        return False
    return trace_id != _ZERO_TRACE_ID and parent_id != _ZERO_SPAN_ID


def parse_traceparent(header: Optional[str]) -> Optional[Tuple[str, str, str]]:
    """Parse and validate a W3C traceparent header string."""
    if not header:
        return None
    match = _RE_TRACEPARENT.match(header.strip().lower())
    if not match:
        return None

    version, trace_id, parent_id, flags = match.groups()
    if not _validate_trace_parts(version, trace_id, parent_id):
        return None

    return trace_id, parent_id, flags


def format_traceparent(trace_id: str, span_id: str, trace_flags: str = "01") -> str:
    """Format trace attributes into a compliant W3C traceparent header string."""
    return f"00-{trace_id}-{span_id}-{trace_flags}"


class Span:
    """Represents a single execution span within a distributed trace."""

    def __init__(
        self,
        name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: Optional[str] = None,
        trace_flags: str = "01",
        attributes: Optional[Dict[str, Any]] = None,
        tracer: Optional[Tracer] = None,
    ):
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.trace_flags = trace_flags
        self.attributes: Dict[str, Any] = dict(attributes or {})
        self.events: List[Dict[str, Any]] = []
        self.status = "unset"
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.duration_ms: Optional[float] = None
        self.tracer = tracer
        self._token: Optional[contextvars.Token] = None

    def set_attribute(self, key: str, value: Any) -> Span:
        """Set a single key/value attribute on this span."""
        self.attributes[key] = value
        return self

    def set_attributes(self, attrs: Dict[str, Any]) -> Span:
        """Bulk set multiple attributes on this span."""
        self.attributes.update(attrs)
        return self

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> Span:
        """Add a timed event annotation to this span."""
        self.events.append(
            {
                "name": name,
                "time": time.time(),
                "attributes": dict(attributes or {}),
            }
        )
        return self

    def record_exception(self, exception: BaseException) -> Span:
        """Record an exception occurrence as an event and set status to error."""
        self.status = "error"
        self.add_event(
            "exception",
            {
                "exception.type": type(exception).__name__,
                "exception.message": str(exception),
            },
        )
        return self

    def finish(self, status: Optional[str] = None) -> None:
        """Mark span as finished, compute duration, and invoke span processors."""
        if self.end_time is not None:
            return
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000.0, 3)
        if status:
            self.status = status
        elif self.status == "unset":
            self.status = "ok"

        if self.tracer is not None:
            self.tracer._on_span_finished(self)

    def to_dict(self) -> Dict[str, Any]:
        """Convert span data to an OpenTelemetry-compatible JSON dictionary."""
        return {
            "name": self.name,
            "context": {
                "trace_id": self.trace_id,
                "span_id": self.span_id,
                "trace_flags": self.trace_flags,
            },
            "parent_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }

    def __enter__(self) -> Span:
        self._token = _current_span_var.set(self)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_val is not None:
            self.record_exception(exc_val)
        self.finish()
        if self._token is not None:
            _current_span_var.reset(self._token)


class Tracer:
    """Manages span creation, contextual propagation, and exporters."""

    def __init__(self, max_finished_spans: int = 1000):
        self.max_finished_spans = max_finished_spans
        self._lock = threading.Lock()
        self._processors: List[Callable[[Span], None]] = []
        self._finished_spans: List[Span] = []

    def get_current_span(self) -> Optional[Span]:
        """Return the active contextual span or None."""
        return _current_span_var.get()

    def add_span_processor(self, processor: Callable[[Span], None]) -> None:
        """Register a callback invoked whenever a span finishes."""
        with self._lock:
            self._processors.append(processor)

    def _resolve_parent_ids(
        self, parent: Optional[Span]
    ) -> Tuple[str, Optional[str], str]:
        active_parent = parent or self.get_current_span()
        if active_parent:
            return (
                active_parent.trace_id,
                active_parent.span_id,
                active_parent.trace_flags,
            )
        return generate_trace_id(), None, "01"

    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        trace_flags: str = "01",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        """Start and return a new span."""
        if not trace_id:
            trace_id, parent_span_id, trace_flags = self._resolve_parent_ids(parent)

        span_id = generate_span_id()
        return Span(
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            trace_flags=trace_flags,
            attributes=attributes,
            tracer=self,
        )

    def _on_span_finished(self, span: Span) -> None:
        with self._lock:
            if len(self._finished_spans) >= self.max_finished_spans:
                self._finished_spans.pop(0)
            self._finished_spans.append(span)
            processors = list(self._processors)

        for processor in processors:
            try:
                processor(span)
            except Exception:
                pass

    def get_finished_spans(self) -> List[Span]:
        """Return list of recorded finished spans."""
        with self._lock:
            return list(self._finished_spans)

    def clear_spans(self) -> None:
        """Clear the in-memory finished spans buffer."""
        with self._lock:
            self._finished_spans.clear()


default_tracer = Tracer()
tracer = default_tracer


def trace(name: Optional[Any] = None) -> Any:
    """Decorator to trace execution of a function with an OpenTelemetry-compatible Span.

    Usage:
        @trace
        def load_catalog():
            ...

        @trace("database.custom_query")
        def query_db():
            ...
    """

    def decorator(fn: Callable) -> Callable:
        import functools

        span_name = name if isinstance(name, str) else f"{fn.__module__}.{fn.__name__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_span(span_name):
                return fn(*args, **kwargs)

        return wrapper

    if callable(name):
        actual_fn = name
        name = None
        return decorator(actual_fn)
    return decorator
