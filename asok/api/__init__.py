from __future__ import annotations

import os
from typing import Any, Callable, Optional, ParamSpec, TypeVar

from ..core import Asok
from ..request import Request

P = ParamSpec("P")
R = TypeVar("R")


class APIMetadata:
    """Stores metadata for an API endpoint used for OpenAPI generation."""

    def __init__(
        self,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
        input: Optional[Any] = None,
        output: Optional[Any] = None,
        name: Optional[str] = None,
    ):
        self.summary = name or summary
        self.description = description
        self.tags = tags or []
        self.input = input
        self.output = output


def api(
    summary: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
    input: Optional[Any] = None,
    output: Optional[Any] = None,
    name: Optional[str] = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to document an API endpoint for OpenAPI generation.

    Args:
        summary: A short summary of what the endpoint does.
        name: Alias for summary.
        description: A verbose explanation of the endpoint behavior.
        tags: A list of tags for API grouping.
        input: A schema class or dict describing the request body.
        output: A schema class or dict describing the response body.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        fn._asok_api = APIMetadata(
            summary=summary,
            description=description or fn.__doc__,
            tags=tags,
            input=input,
            output=output,
            name=name,
        )
        return fn

    return decorator


def register_api_docs(app):
    """Register internal documentation handlers."""
    # This will be used by Asok core to check for documentation requests
    pass


def _should_serve_docs(app: Asok, path: str, spec_path: str, docs_path: str) -> Optional[dict]:
    if path != spec_path and path != docs_path:
        return None
    from .openapi import OpenAPIGenerator
    gen = OpenAPIGenerator(app)
    spec = gen.generate()
    if not spec.get("paths"):
        return None
    return spec


def _render_docs_html(app: Asok, request: Request, spec: dict) -> Optional[str]:
    current_dir = os.path.dirname(__file__)
    template_path = os.path.join(current_dir, "templates", "docs.html")

    if not os.path.exists(template_path):
        return None

    try:
        file_size = os.path.getsize(template_path)
        if file_size > 1_000_000:
            return None
    except OSError:
        return None

    with open(template_path) as f:
        content = f.read()

    from ..templates import render_template_string
    css_url = "/asok-api/docs.min.css"
    js_url = "/asok-api/docs.min.js"

    return render_template_string(
        content,
        {
            "spec": spec,
            "csrf_token": request.csrf_token_value,
            "api_title": app.config.get(
                "API_TITLE", app.config.get("PROJECT_NAME", spec["info"]["title"])
            ),
            "api_logo": app.config.get("API_LOGO", app.config.get("SITE_LOGO")),
            "css_url": css_url,
            "js_url": js_url,
            "graphql_enabled": bool(app.config.get("GRAPHQL_ENABLED", False)),
            "graphql_path": app.config.get("GRAPHQL_PATH", "/graphql"),
        },
    )


def _serve_static_asset(request: Request, path: str) -> Optional[bytes]:
    static_paths = {
        "/asok-api/docs.css": ("docs.css", "text/css"),
        "/asok-api/docs.min.css": ("docs.min.css", "text/css"),
        "/asok-api/docs.js": ("docs.js", "application/javascript"),
        "/asok-api/docs.min.js": ("docs.min.js", "application/javascript"),
        "/asok-api/logo.svg": ("logo.svg", "image/svg+xml"),
        "/asok-api/fonts/inter-400.woff2": ("fonts/inter-400.woff2", "font/woff2"),
        "/asok-api/fonts/inter-500.woff2": ("fonts/inter-500.woff2", "font/woff2"),
        "/asok-api/fonts/inter-600.woff2": ("fonts/inter-600.woff2", "font/woff2"),
        "/asok-api/fonts/inter-700.woff2": ("fonts/inter-700.woff2", "font/woff2"),
        "/asok-api/fonts/outfit-500.woff2": ("fonts/outfit-500.woff2", "font/woff2"),
        "/asok-api/fonts/outfit-600.woff2": ("fonts/outfit-600.woff2", "font/woff2"),
        "/asok-api/fonts/outfit-700.woff2": ("fonts/outfit-700.woff2", "font/woff2"),
        "/asok-api/fonts/outfit-800.woff2": ("fonts/outfit-800.woff2", "font/woff2"),
        "/asok-graphql/react.min.js":     ("graphiql/react.min.js", "application/javascript"),
        "/asok-graphql/react-dom.min.js": ("graphiql/react-dom.min.js", "application/javascript"),
        "/asok-graphql/graphiql.min.js":  ("graphiql/graphiql.min.js", "application/javascript"),
        "/asok-graphql/graphiql.min.css": ("graphiql/graphiql.min.css", "text/css"),
    }

    if path not in static_paths:
        return None

    filename, content_type = static_paths[path]
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, "static", filename)

    if not os.path.exists(file_path):
        return None

    try:
        file_size = os.path.getsize(file_path)
        if file_size > 5_000_000:
            return None
    except OSError:
        return None

    with open(file_path, "rb") as f:
        request.content_type = content_type
        return f.read()


def handle_docs_request(app: Asok, request: Request) -> Optional[Any]:
    """Handle requests to documentation endpoints (/docs and /openapi.json)."""
    path = request.path
    docs_path = app.config.get("DOCS_PATH", "/docs")
    spec_path = app.config.get("OPENAPI_PATH", "/openapi.json")

    spec = _should_serve_docs(app, path, spec_path, docs_path)
    if spec is not None:
        if path == spec_path:
            auth_hook = app.config.get("OPENAPI_AUTHORIZE")
            if auth_hook is not None and not auth_hook(request):
                request.status = "403 Forbidden"
                return request.json({"error": "Unauthorized"})
            return request.json(spec)
        return _render_docs_html(app, request, spec)

    return _serve_static_asset(request, path)
