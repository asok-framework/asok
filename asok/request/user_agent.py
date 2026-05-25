from __future__ import annotations

from typing import Optional


class UserAgent:
    """Lightweight parser for identifying browser, OS, and mobile status."""

    def __init__(self, ua_string: Optional[str]):
        self.raw: str = ua_string or ""
        self._parsed: bool = False
        self._name: str = "Unknown"
        self._os: str = "Unknown"
        self._is_mobile: bool = False

    def _parse(self) -> None:
        if self._parsed:
            return
        ua = self.raw
        # Order matters: Edge/Opera/Chrome contain Safari; Edge/Chrome contain Chrome
        if "Edg/" in ua:
            self._name = "Edge"
        elif "OPR/" in ua or "Opera" in ua:
            self._name = "Opera"
        elif "MSIE" in ua or "Trident/" in ua:
            self._name = "Internet Explorer"
        elif "Firefox/" in ua:
            self._name = "Firefox"
        elif "Chrome/" in ua:
            self._name = "Chrome"
        elif "Safari/" in ua:
            self._name = "Safari"

        # OS detection (case-insensitive to handle variations)
        ua_lower = ua.lower()
        if "windows" in ua_lower:
            self._os = "Windows"
        elif "iphone" in ua_lower or "ipad" in ua_lower:
            self._os = "iOS"
        elif "android" in ua_lower:
            self._os = "Android"
        elif "mac os x" in ua_lower:
            self._os = "macOS"
        elif "linux" in ua_lower:
            self._os = "Linux"

        # Mobile detection
        self._is_mobile = any(
            x in ua.lower() for x in ["mobile", "android", "iphone", "ipad"]
        )
        self._parsed = True

    @property
    def name(self) -> str:
        """The identified browser name (e.g., 'Chrome', 'Firefox')."""
        self._parse()
        return self._name

    @property
    def os(self) -> str:
        """The identified operating system (e.g., 'Windows', 'iOS')."""
        self._parse()
        return self._os

    @property
    def is_mobile(self) -> bool:
        """True if the request originates from a mobile device."""
        self._parse()
        return self._is_mobile

    def __str__(self) -> str:
        return self.raw
