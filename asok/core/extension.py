from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .asok import Asok


class AsokExtension:
    """Base class for all third-party Asok extensions."""

    def __init__(self, app: Optional[Asok] = None) -> None:
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Asok) -> None:
        """Initialize the extension and register it with the Asok application."""
        self.app = app
        app.extensions[self.__class__.__name__] = self
        app.register_extension(self)

    def get_pages_path(self) -> Optional[str]:
        """Return the absolute path to the extension's pages/controllers directory."""
        return None

    def get_templates_path(self) -> Optional[str]:
        """Return the absolute path to the extension's templates (partials/components) directory."""
        return None

    def get_static_path(self) -> Optional[str]:
        """Return the absolute path to the extension's static assets directory."""
        return None
