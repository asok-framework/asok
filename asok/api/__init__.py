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


def handle_docs_request(app: Asok, request: Request) -> Optional[Any]:
    """Handle requests to documentation endpoints (/docs and /openapi.json)."""
    from .openapi import OpenAPIGenerator

    path = request.path
    # Support custom doc paths via config
    docs_path = app.config.get("DOCS_PATH", "/docs")
    spec_path = app.config.get("OPENAPI_PATH", "/openapi.json")
    css_path = "/asok-api/docs.css"

    if path == spec_path or path == docs_path:
        gen = OpenAPIGenerator(app)
        spec = gen.generate()

        # If no API paths are found, hide the docs (return None to 404)
        if not spec.get("paths"):
            return None

        if path == spec_path:
            return request.json(spec)

        # Handle docs_path
        current_dir = os.path.dirname(__file__)
        template_path = os.path.join(current_dir, "templates", "docs.html")

        if not os.path.exists(template_path):
            # Fallback for when templates aren't in the expected spot
            return None

        with open(template_path) as f:
            content = f.read()

        # Render using the app's engine
        from ..templates import render_template_string

        return render_template_string(
            content,
            {
                "spec": spec,
                "csrf_token": request.csrf_token_value,
                "api_title": app.config.get(
                    "API_TITLE", app.config.get("PROJECT_NAME", spec["info"]["title"])
                ),
                "api_logo": app.config.get("API_LOGO", app.config.get("SITE_LOGO")),
            },
        )

    if path == css_path or path == "/asok-api/logo.svg":
        current_dir = os.path.dirname(__file__)
        filename = "docs.css" if path == css_path else "logo.svg"
        file_path = os.path.join(current_dir, "static", filename)
        if not os.path.exists(file_path):
            return None
        with open(file_path, "rb") as f:
            request.content_type = "text/css" if path == css_path else "image/svg+xml"
            return f.read()

    return None
