from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("asok.core")


class RoutingMixin:
    def _resolve_route(
        self, parts: list[str], request: Optional[Any] = None
    ) -> tuple[Optional[str], dict[str, Any]]:
        """Resolve a list of URL segments to a page file and captured parameters."""
        debug = self.config.get("DEBUG", False)

        # Header-based API Versioning Rewrite:
        if (
            request
            and len(parts) >= 2
            and parts[0] == "api"
            and not re.match(r"^v\d+(?:\.\d+)?$", parts[1])
        ):
            from ..api.versioning import get_request_version

            version = get_request_version(request)
            if version:
                new_parts = [parts[0], version] + parts[1:]
                current_dir = os.path.join(self.root_dir, self.dirs["PAGES"])
                res = self._walk_route(new_parts, current_dir, {})
                if res[0]:
                    parts = new_parts

        if not debug:
            if not hasattr(self, "_route_cache"):
                self._route_cache = {}
            key = "/".join(parts)
            if key in self._route_cache:
                page_file, route_params = self._route_cache[key]
                return page_file, dict(route_params)

        search_dirs = getattr(self, "_pages_search_paths", [os.path.join(self.root_dir, self.dirs["PAGES"])])
        result = (None, {})
        for pages_dir in search_dirs:
            result = self._walk_route(parts, pages_dir, {})
            if result[0]:
                break

        if not debug:
            page_file, route_params = result
            self._route_cache[key] = (page_file, dict(route_params))

        return result

    def _convert_param(self, value: str, type_name: str) -> Optional[Any]:
        """Convert a URL segment to a typed parameter (int, float, uuid, slug).

        SECURITY: Limits parameter length to prevent DoS attacks.
        """
        # SECURITY: Reject overly long parameters (max 255 chars)
        MAX_PARAM_LENGTH = 255
        if len(value) > MAX_PARAM_LENGTH:
            if self.config.get("DEBUG"):
                logger.debug(
                    f"Routing: Parameter too long ({len(value)} chars, max {MAX_PARAM_LENGTH}): '{value[:50]}...'"
                )
            return None

        if type_name == "int":
            try:
                return int(value)
            except ValueError:
                if self.config.get("DEBUG"):
                    logger.debug(
                        f"Routing: Int validation failed for segment '{value}'"
                    )
                return None
        if type_name == "float":
            try:
                return float(value)
            except ValueError:
                if self.config.get("DEBUG"):
                    logger.debug(
                        f"Routing: Float validation failed for segment '{value}'"
                    )
                return None
        if type_name == "uuid":
            # Support standard (8-4-4-4-12) and compact (32 hex) formats, case-insensitive
            # Optional {} for full standard formats
            pattern = r"^({)?[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}(?(1)})$"
            if re.match(pattern, value, re.I):
                return value

            if self.config.get("DEBUG"):
                logger.debug(f"Routing: UUID validation failed for segment '{value}'")
            return None
        if type_name == "hex":
            # Support hex characters and optional hyphens (1-64 chars)
            if re.match(r"^[0-9a-f-]{1,64}$", value, re.I):
                return value

            if self.config.get("DEBUG"):
                logger.debug(f"Routing: Hex validation failed for segment '{value}'")
            return None
        if type_name == "slug":
            if re.match(r"^[a-z0-9-]+$", value):
                return value

            if self.config.get("DEBUG"):
                logger.debug(f"Routing: Slug validation failed for segment '{value}'")
            return None
        return value  # str or unknown type

    def _walk_route(
        self, segments: list[str], current_base: str, captured_params: dict[str, Any]
    ) -> tuple[Optional[str], dict[str, Any]]:
        """Recursively walk the pages directory to find a matching route file."""
        if not segments:
            for ext in (".py", ".pyc", ".html", ".asok"):
                p = os.path.join(current_base, self.config["INDEX"] + ext)
                if os.path.isfile(p):
                    return p, captured_params
            return None, captured_params

        seg = segments[0]
        remaining = segments[1:]

        # 0. Literal File match (e.g. /about -> about.py or about.html or about.asok)
        if not remaining:
            for ext in (".py", ".pyc", ".html", ".asok"):
                p = os.path.join(current_base, seg + ext)
                if os.path.isfile(p):
                    return p, captured_params

        # 1. Literal Directory match
        dir_candidate = os.path.join(current_base, seg)
        if os.path.isdir(dir_candidate):
            res, pars = self._walk_route(remaining, dir_candidate, captured_params)
            if res:
                return res, pars

        # 2. Dynamic Directory match [param] or [param:type]
        try:
            entries = os.listdir(current_base)
        except OSError:
            return None, captured_params

        candidates = []
        for entry in entries:
            if (
                entry[0] == "["
                and entry[-1] == "]"
                and os.path.isdir(os.path.join(current_base, entry))
            ):
                candidates.append(entry)

        # Priority: Typed matches ([id:int]) before Generic matches ([name])
        typed = sorted([c for c in candidates if ":" in c])
        generic = [c for c in candidates if ":" not in c]

        for entry in typed + generic:
            inner = entry[1:-1]
            if ":" in inner:
                param_name, type_name = inner.split(":", 1)
                converted = self._convert_param(seg, type_name)
                if converted is None:
                    if self.config.get("DEBUG"):
                        logger.debug(
                            f"Routing: Folder '{entry}' rejected segment '{seg}' due to type mismatch ({type_name})"
                        )
                    continue  # Type mismatch, try next folder
            else:
                param_name = inner
                converted = seg

            new_params = captured_params.copy()
            new_params[param_name] = converted
            res, pars = self._walk_route(
                remaining, os.path.join(current_base, entry), new_params
            )
            if res:
                return res, pars

        return None, captured_params
