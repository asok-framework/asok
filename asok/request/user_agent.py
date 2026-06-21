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

    def _is_opera(self, ua: str) -> bool:
        return "OPR/" in ua or "Opera" in ua

    def _is_ie(self, ua: str) -> bool:
        return "MSIE" in ua or "Trident/" in ua

    def _detect_other_browsers(self, ua: str) -> str:
        for key, name in (("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")):
            if key in ua:
                return name
        return "Unknown"

    def _detect_browser(self, ua: str) -> str:
        """Detect browser name from user-agent string."""
        if "Edg/" in ua:
            return "Edge"
        if self._is_opera(ua):
            return "Opera"
        if self._is_ie(ua):
            return "Internet Explorer"
        return self._detect_other_browsers(ua)

    def _detect_other_os(self, ua_lower: str) -> str:
        for key, name in (("android", "Android"), ("mac os x", "macOS"), ("linux", "Linux")):
            if key in ua_lower:
                return name
        return "Unknown"

    def _detect_os(self, ua_lower: str) -> str:
        """Detect OS name from lowercased user-agent string."""
        if "windows" in ua_lower:
            return "Windows"
        if "iphone" in ua_lower or "ipad" in ua_lower:
            return "iOS"
        return self._detect_other_os(ua_lower)

    def _parse(self) -> None:
        if self._parsed:
            return
        ua = self.raw
        ua_lower = ua.lower()
        self._name = self._detect_browser(ua)
        self._os = self._detect_os(ua_lower)
        self._is_mobile = any(x in ua_lower for x in ["mobile", "android", "iphone", "ipad"])
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
