from __future__ import annotations

from typing import Any, Callable

from ..templates import SafeString


class WidgetMixin:
    """Mixin for widgets registration and rendering on Asok Admin."""

    def add_widget(
        self,
        title: str,
        render: Callable[[Any], str | dict[str, str]],
        size: str = "medium",
        permission: str | None = None,
    ) -> None:
        """Register a custom dashboard widget.

        - title: display title
        - render: callable(request) -> str (HTML body) or dict
            {"html": str, "footer": str}
        - size: 'small' | 'medium' | 'large' (CSS hint)
        - permission: optional 'slug.verb' string; widget hidden if user lacks it
        """
        self._widgets.append(
            {
                "title": title,
                "render": render,
                "size": size,
                "permission": permission,
            }
        )

    def widget(
        self, title: str, size: str = "medium", permission: str | None = None
    ) -> Callable[[Callable], Callable]:
        """Decorator form: @admin.widget("Title")"""

        def deco(fn: Callable) -> Callable:
            self.add_widget(title, fn, size=size, permission=permission)
            return fn

        return deco

    def _render_widgets(self, request: Any) -> list[dict[str, Any]]:
        """Run each registered widget; skip ones the user can't see or that error."""
        out = []
        for w in self._widgets:
            if w["permission"]:
                try:
                    slug, verb = w["permission"].split(".", 1)
                except ValueError:
                    continue
                if not self._can(request, slug, verb):
                    continue
            try:
                result = w["render"](request)
            except Exception as e:
                result = f'<div class="muted">Widget error: {e}</div>'
            if isinstance(result, dict):
                html = result.get("html", "")
                footer = result.get("footer", "")
            else:
                html = str(result or "")
                footer = ""
            out.append(
                {
                    "title": w["title"],
                    "size": w["size"],
                    "html": SafeString(html),
                    "footer": SafeString(footer),
                }
            )
        return out
