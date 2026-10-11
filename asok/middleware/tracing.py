from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from asok.core.tracing import (
    Tracer,
    default_tracer,
    format_traceparent,
    generate_trace_id,
    parse_traceparent,
)


class TracingMiddleware:
    """Middleware that extracts or initiates distributed traces per W3C TraceContext standards."""

    def __init__(self, tracer: Optional[Tracer] = None):
        self.tracer = tracer or default_tracer

    def _extract_traceparent(self, request: Any) -> Tuple[str, Optional[str], str]:
        headers = getattr(request, "headers", {})
        header_val = headers.get("traceparent") if hasattr(headers, "get") else None
        parsed = parse_traceparent(header_val)
        if parsed:
            return parsed[0], parsed[1], parsed[2]
        return generate_trace_id(), None, "01"

    def _populate_span_attributes(self, span: Any, request: Any) -> None:
        attrs = {
            "http.method": getattr(request, "method", "GET"),
            "http.target": getattr(request, "path", "/"),
            "http.client_ip": getattr(request, "ip", "127.0.0.1"),
            "http.host": getattr(request, "host", "localhost"),
        }
        user_agent = getattr(request, "user_agent", None)
        if user_agent:
            attrs["http.user_agent"] = user_agent
        span.set_attributes(attrs)

    def _inject_response_headers(
        self,
        response: Any,
        trace_id: str,
        span_id: str,
        flags: str,
        request: Any = None,
    ) -> None:
        tp = format_traceparent(trace_id, span_id, flags)
        headers = getattr(response, "headers", None)
        if headers is not None:
            headers["traceparent"] = tp
            headers["X-Trace-ID"] = trace_id
            headers["X-Span-ID"] = span_id

        if request is not None and hasattr(request, "response_headers"):
            request.response_headers.append(("traceparent", tp))
            request.response_headers.append(("X-Trace-ID", trace_id))
            request.response_headers.append(("X-Span-ID", span_id))

    def _record_response_status(self, span: Any, response: Any) -> None:
        status_code = getattr(response, "status_code", 200)
        span.set_attribute("http.status_code", status_code)
        if status_code >= 500:
            span.status = "error"
        else:
            span.status = "ok"

    def __call__(self, request: Any, next_handler: Callable) -> Any:
        trace_id, parent_span_id, flags = self._extract_traceparent(request)
        span = self.tracer.start_span(
            name="http.server",
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            trace_flags=flags,
        )
        self._populate_span_attributes(span, request)

        setattr(request, "trace_id", trace_id)
        setattr(request, "span_id", span.span_id)
        setattr(request, "tracer", self.tracer)
        setattr(request, "span", span)

        with span:
            response = next_handler(request)
            self._record_response_status(span, response)
            self._inject_response_headers(
                response, trace_id, span.span_id, flags, request=request
            )
            return response


def tracing_middleware(request: Any, next_handler: Callable) -> Any:
    """Default functional tracing middleware."""
    mw = TracingMiddleware()
    return mw(request, next_handler)
