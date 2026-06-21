from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("asok.core")


class RoutingMixin:
    def _should_rewrite_version(self, parts: list[str], request: Optional[Any]) -> bool:
        if not request or len(parts) < 2:
            return False
        if parts[0] != "api" or re.match(r"^v\d+(?:\.\d+)?$", parts[1]):
            return False
        return True

    def _rewrite_api_version(self, parts: list[str], request: Optional[Any]) -> list[str]:
        """Header-based API Versioning Rewrite."""
        if not self._should_rewrite_version(parts, request):
            return parts

        from ..api.versioning import get_request_version
        version = get_request_version(request)
        if not version:
            return parts

        new_parts = [parts[0], version] + parts[1:]
        current_dir = os.path.join(self.root_dir, self.dirs["PAGES"])
        res = self._walk_route(new_parts, current_dir, {})
        if res[0]:
            return new_parts
        return parts

    def _cache_route(
        self, parts: list[str], result: tuple[Optional[str], dict[str, Any]]
    ) -> None:
        """Cache the resolved route result."""
        page_file, route_params = result
        key = "/".join(parts)
        if not hasattr(self, "_route_cache"):
            self._route_cache = {}
        self._route_cache[key] = (page_file, dict(route_params))

    def _resolve_route(
        self, parts: list[str], request: Optional[Any] = None
    ) -> tuple[Optional[str], dict[str, Any]]:
        """Resolve a list of URL segments to a page file and captured parameters."""
        debug = self.config.get("DEBUG", False)

        parts = self._rewrite_api_version(parts, request)

        if not debug:
            cached = self._lookup_route_cache(parts)
            if cached is not None:
                return cached

        result = self._search_pages_dirs(parts)

        if not debug:
            self._cache_route(parts, result)

        return result

    def _lookup_route_cache(
        self, parts: list[str]
    ) -> Optional[tuple[Optional[str], dict[str, Any]]]:
        """Return cached route result or None if not cached."""
        if not hasattr(self, "_route_cache"):
            self._route_cache = {}
        key = "/".join(parts)
        if key in self._route_cache:
            page_file, route_params = self._route_cache[key]
            return page_file, dict(route_params)
        return None

    def _search_pages_dirs(
        self, parts: list[str]
    ) -> tuple[Optional[str], dict[str, Any]]:
        """Search all configured pages directories for a matching route."""
        search_dirs = getattr(
            self,
            "_pages_search_paths",
            [os.path.join(self.root_dir, self.dirs["PAGES"])],
        )
        result: tuple[Optional[str], dict[str, Any]] = (None, {})
        for pages_dir in search_dirs:
            result = self._walk_route(parts, pages_dir, {})
            if result[0]:
                break
        return result

    def _convert_int_param(self, value: str) -> Optional[int]:
        """Convert a URL segment to int."""
        try:
            return int(value)
        except ValueError:
            if self.config.get("DEBUG"):
                logger.debug(f"Routing: Int validation failed for segment '{value}'")
            return None

    def _convert_float_param(self, value: str) -> Optional[float]:
        """Convert a URL segment to float."""
        try:
            return float(value)
        except ValueError:
            if self.config.get("DEBUG"):
                logger.debug(f"Routing: Float validation failed for segment '{value}'")
            return None

    def _convert_uuid_param(self, value: str) -> Optional[str]:
        """Validate and return a UUID segment."""
        pattern = r"^({)?[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}(?(1)})$"
        if re.match(pattern, value, re.I):
            return value
        if self.config.get("DEBUG"):
            logger.debug(f"Routing: UUID validation failed for segment '{value}'")
        return None

    def _convert_hex_param(self, value: str) -> Optional[str]:
        """Validate and return a hex segment."""
        if re.match(r"^[0-9a-f-]{1,64}$", value, re.I):
            return value
        if self.config.get("DEBUG"):
            logger.debug(f"Routing: Hex validation failed for segment '{value}'")
        return None

    def _convert_slug_param(self, value: str) -> Optional[str]:
        """Validate and return a slug segment."""
        if re.match(r"^[a-z0-9-]+$", value):
            return value
        if self.config.get("DEBUG"):
            logger.debug(f"Routing: Slug validation failed for segment '{value}'")
        return None

    def _convert_param(self, value: str, type_name: str) -> Optional[Any]:
        """Convert a URL segment to a typed parameter (int, float, uuid, slug).

        SECURITY: Limits parameter length to prevent DoS attacks.
        """
        MAX_PARAM_LENGTH = 255
        if len(value) > MAX_PARAM_LENGTH:
            if self.config.get("DEBUG"):
                logger.debug(
                    f"Routing: Parameter too long ({len(value)} chars, max {MAX_PARAM_LENGTH}): '{value[:50]}...'"
                )
            return None

        dispatch = {
            "int": self._convert_int_param,
            "float": self._convert_float_param,
            "uuid": self._convert_uuid_param,
            "hex": self._convert_hex_param,
            "slug": self._convert_slug_param,
        }
        handler = dispatch.get(type_name)
        if handler:
            return handler(value)
        return value  # str or unknown type

    def _walk_route_terminal(self, current_base: str, captured_params: dict[str, Any]) -> tuple[Optional[str], dict[str, Any]]:
        for ext in (".py", ".pyc", ".html", ".asok"):
            p = os.path.join(current_base, self.config["INDEX"] + ext)
            if os.path.isfile(p):
                return p, captured_params
        return None, captured_params

    def _walk_literal_file(
        self, segments: list[str], current_base: str, captured_params: dict[str, Any]
    ) -> tuple[Optional[str], dict[str, Any]]:
        if len(segments) != 1:
            return None, captured_params

        seg = segments[0]
        for ext in (".py", ".pyc", ".html", ".asok"):
            p = os.path.join(current_base, seg + ext)
            if os.path.isfile(p):
                return p, captured_params
        return None, captured_params

    def _walk_literal_dir(
        self, segments: list[str], current_base: str, captured_params: dict[str, Any]
    ) -> tuple[Optional[str], dict[str, Any]]:
        seg = segments[0]
        dir_candidate = os.path.join(current_base, seg)
        if os.path.isdir(dir_candidate):
            return self._walk_route(segments[1:], dir_candidate, captured_params)
        return None, captured_params

    def _find_dynamic_candidates(self, current_base: str, entries: list[str]) -> list[str]:
        candidates = []
        for entry in entries:
            if entry.startswith("[") and entry.endswith("]"):
                if os.path.isdir(os.path.join(current_base, entry)):
                    candidates.append(entry)
        return candidates

    def _try_dynamic_entry(
        self,
        seg: str,
        remaining: list[str],
        current_base: str,
        entry: str,
        captured_params: dict[str, Any]
    ) -> tuple[Optional[str], dict[str, Any]]:
        inner = entry[1:-1]
        if ":" in inner:
            param_name, type_name = inner.split(":", 1)
            converted = self._convert_param(seg, type_name)
            if converted is None:
                if self.config.get("DEBUG"):
                    logger.debug(
                        f"Routing: Folder '{entry}' rejected segment '{seg}' due to type mismatch ({type_name})"
                    )
                return None, captured_params
        else:
            param_name = inner
            converted = seg

        new_params = captured_params.copy()
        new_params[param_name] = converted
        return self._walk_route(
            remaining, os.path.join(current_base, entry), new_params
        )

    def _split_candidates_by_type(self, candidates: list[str]) -> tuple[list[str], list[str]]:
        typed = []
        generic = []
        for c in candidates:
            if ":" in c:
                typed.append(c)
            else:
                generic.append(c)
        return sorted(typed), generic

    def _walk_dynamic_dir(
        self, segments: list[str], current_base: str, captured_params: dict[str, Any]
    ) -> tuple[Optional[str], dict[str, Any]]:
        try:
            entries = os.listdir(current_base)
        except OSError:
            return None, captured_params

        candidates = self._find_dynamic_candidates(current_base, entries)
        typed, generic = self._split_candidates_by_type(candidates)

        seg = segments[0]
        remaining = segments[1:]

        for entry in typed + generic:
            res, pars = self._try_dynamic_entry(seg, remaining, current_base, entry, captured_params)
            if res:
                return res, pars

        return None, captured_params

    def _walk_route(
        self, segments: list[str], current_base: str, captured_params: dict[str, Any]
    ) -> tuple[Optional[str], dict[str, Any]]:
        """Recursively walk the pages directory to find a matching route file."""
        if not segments:
            return self._walk_route_terminal(current_base, captured_params)

        res, pars = self._walk_literal_file(segments, current_base, captured_params)
        if res:
            return res, pars

        res, pars = self._walk_literal_dir(segments, current_base, captured_params)
        if res:
            return res, pars

        return self._walk_dynamic_dir(segments, current_base, captured_params)
