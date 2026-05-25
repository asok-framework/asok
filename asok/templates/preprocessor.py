from __future__ import annotations

import ast
import os
import re
from typing import Any, Optional

from .safestring import SafeString

# Pre-compiled regex patterns
_RE_EXTENDS = re.compile(r"{%-?\s*extends\s+[\'\"](.*?)[\'\"]\s*-?%}")
_RE_INCLUDE = re.compile(r"{%-?\s*include\s+(.*?)\s*-?%}")
_RE_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_RE_TOKENS = re.compile(r"(?s)({{.*?}}|{%.*?%})")
_RE_BLOCK_OPEN = re.compile(r"{%-?\s*block\s+(\w+)\s*-?%}")
_RE_BLOCK_CLOSE = re.compile(r"{%-?\s*endblock(?:\s+\w+)?\s*-?%}")
_RE_FROM_IMPORT = re.compile(r"{%-?\s*from\s+['\"](.+?)['\"]\s+import\s+(.+?)\s*-?%}")
_RE_IMPORT_AS = re.compile(r"{%-?\s*import\s+['\"](.+?)['\"]\s+as\s+(\w+)\s*-?%}")
_RE_FILTER_BLOCK = re.compile(
    r"{%-?\s*filter\s+(\w+(?:\([^)]*\))?)\s*-?%}(.*?){%-?\s*endfilter\s*-?%}",
    re.DOTALL,
)
_RE_AUTOESCAPE_BLOCK = re.compile(
    r"{%-?\s*autoescape\s+(true|false)\s*-?%}(.*?){%-?\s*endautoescape\s*-?%}",
    re.DOTALL,
)
_RE_MACRO = re.compile(
    r"{%-?\s*macro\s+(\w+)\s*\((.*?)\)\s*-?%}(.*?){%-?\s*endmacro\s*-?%}", re.DOTALL
)
_RE_RAW = re.compile(r"{%-?\s*raw\s*-?%}(.*?){%-?\s*endraw\s*-?%}", re.DOTALL)
_RE_COMPONENT = re.compile(
    r"{%-?\s*component\s+[\'\"](.*?)[\'\"]\s*(.*?)-?%}(.*?){%-?\s*endcomponent\s*-?%}",
    re.DOTALL,
)

_macro_cache: dict[str, str] = {}  # file_path -> file content
_macro_mtimes: dict[str, float] = {}  # file_path -> modification time


def _safe_resolve(base: str, requested: str) -> str:
    """Ensure requested path resolves within base directory."""
    base = os.path.abspath(base)
    full = os.path.abspath(os.path.join(base, requested))
    if not (full.startswith(base + os.sep) or full == base):
        raise ValueError(f"Path traversal blocked: {requested}")
    return full


def _get_all_macro_names(file_path: str) -> list[str]:
    """Get all macro names from a file without loading them.

    SECURITY: File size limits prevent DoS via extremely large macro files.
    """
    content = _macro_cache.get(file_path)
    if content is None:
        if os.path.exists(file_path):
            # SECURITY: Limit macro file size to prevent DoS (max 1MB)
            try:
                file_size = os.path.getsize(file_path)
                if file_size > 1_000_000:
                    return []
            except OSError:
                return []

            current_mtime = os.path.getmtime(file_path)
            _macro_mtimes[file_path] = current_mtime
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            _macro_cache[file_path] = content
        else:
            return []

    return [m.group(1) for m in _RE_MACRO.finditer(content)]


def _extract_macros(
    file_path: str, names: list[str], parent_ctx: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Parse a macro file and return callables for the requested macro names.

    All macros in the file are made available to each other (sibling calls),
    so a macro can reference another macro defined in the same file.

    In development mode (when file exists), checks file modification time
    and reloads if changed.

    SECURITY: File size limits prevent DoS via extremely large macro files.
    """
    # Check if file has been modified since last cache
    reload_needed = False
    if os.path.exists(file_path):
        current_mtime = os.path.getmtime(file_path)
        cached_mtime = _macro_mtimes.get(file_path)

        if cached_mtime is None or current_mtime > cached_mtime:
            reload_needed = True
            _macro_mtimes[file_path] = current_mtime

    content = _macro_cache.get(file_path)
    if content is None or reload_needed:
        # SECURITY: Limit macro file size to prevent DoS (max 1MB)
        try:
            file_size = os.path.getsize(file_path)
            if file_size > 1_000_000:
                return {}
        except OSError:
            return {}

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        _macro_cache[file_path] = content

    all_macros: dict[str, Any] = {}
    parsed = []
    for match in _RE_MACRO.finditer(content):
        macro_name = match.group(1)
        raw_params = match.group(2).strip()
        body = match.group(3)

        param_names = []
        param_defaults = {}
        varargs = None
        varkw = None

        if raw_params:
            for param in raw_params.split(","):
                param = param.strip()
                if not param:
                    continue

                if param.startswith("**"):
                    varkw = param[2:]
                elif param.startswith("*"):
                    varargs = param[1:]
                elif "=" in param:
                    pname, pdefault = param.split("=", 1)
                    pname = pname.strip()
                    param_names.append(pname)
                    param_defaults[pname] = pdefault.strip()
                else:
                    param_names.append(param)
        parsed.append((macro_name, body, param_names, param_defaults, varargs, varkw))

    def _make_macro(
        m_body: str,
        m_params: list[str],
        m_defaults: dict[str, str],
        m_varargs: Optional[str],
        m_varkw: Optional[str],
    ) -> Any:
        def macro_fn(*args: Any, **kwargs: Any) -> SafeString:
            local_ctx = dict(parent_ctx or {})
            local_ctx.update(all_macros)

            # 1. Map positional args to named params
            used_kwargs = set()
            for i, pname in enumerate(m_params):
                if i < len(args):
                    local_ctx[pname] = args[i]
                elif pname in kwargs:
                    local_ctx[pname] = kwargs[pname]
                    used_kwargs.add(pname)
                elif pname in m_defaults:
                    try:
                        local_ctx[pname] = ast.literal_eval(m_defaults[pname])
                    except (ValueError, SyntaxError):
                        local_ctx[pname] = m_defaults[pname]
                else:
                    local_ctx[pname] = ""

            # 2. Collect *varargs
            if m_varargs:
                local_ctx[m_varargs] = args[len(m_params) :]

            # 3. Collect **varkw
            if m_varkw:
                remaining = {k: v for k, v in kwargs.items() if k not in m_params}
                local_ctx[m_varkw] = remaining

            # 4. Always pass caller if provided (for {% call macro() %})
            if "caller" in kwargs and "caller" not in used_kwargs:
                local_ctx["caller"] = kwargs["caller"]

            from .engine import render_template_string

            return SafeString(render_template_string(m_body, local_ctx))

        return macro_fn

    for macro_name, body, param_names, param_defaults, varargs, varkw in parsed:
        all_macros[macro_name] = _make_macro(
            body, param_names, param_defaults, varargs, varkw
        )

    return {n: all_macros[n] for n in names if n in all_macros}


def _preprocess(
    template_string: str,
    context: Optional[dict[str, Any]] = None,
    root_dir: Optional[str] = None,
    strip_blocks: bool = True,
    inject_markers: bool = False,
) -> str:
    """Resolve inheritance, includes, macros, and strip comments.

    Args:
        inject_markers: If True, replaces block tags with HTML comment markers
                       for data-block targeting without IDs

    Returns the fully pre-processed template string (still contains
    {% block %} tags so callers can extract individual blocks).
    """

    # 1. Handle Inheritance (Extends & Block)
    def handle_inheritance(text: str, depth: int = 0) -> str:
        if depth > 5:
            return text

        extends_match = _RE_EXTENDS.search(text)
        if not extends_match:
            return text

        parent_path = extends_match.group(1)
        base = (
            root_dir
            if root_dir and os.path.isabs(root_dir)
            else os.path.join(os.getcwd(), root_dir or "")
        )
        try:
            full_parent_path = _safe_resolve(base, parent_path)
        except ValueError:
            return "<!-- Inheritance Error: path traversal blocked -->"

        if not os.path.exists(full_parent_path):
            # Try automatic extension resolution
            resolved = False
            # 1. Try appending
            for ext in (".html", ".asok"):
                if os.path.exists(full_parent_path + ext):
                    full_parent_path = full_parent_path + ext
                    resolved = True
                    break

            # 2. Try swapping
            if not resolved:
                base_path, current_ext = os.path.splitext(full_parent_path)
                if current_ext == ".html" and os.path.exists(base_path + ".asok"):
                    full_parent_path = base_path + ".asok"
                    resolved = True
                elif current_ext == ".asok" and os.path.exists(base_path + ".html"):
                    full_parent_path = base_path + ".html"
                    resolved = True

            if not resolved:
                return f"<!-- Inheritance Error: {parent_path} not found in {base} -->"

        # SECURITY: Limit template file size to prevent DoS (max 1MB)
        try:
            file_size = os.path.getsize(full_parent_path)
            if file_size > 1_000_000:
                return "<!-- Inheritance Error: template file too large -->"
        except OSError:
            return "<!-- Inheritance Error: cannot read template -->"

        with open(full_parent_path, "r", encoding="utf-8") as f:
            parent_text = f.read()

        # Nesting-aware extraction of functional tags outside blocks
        outside_text = ""
        block_ranges = []
        for open_match in _RE_BLOCK_OPEN.finditer(text):
            start = open_match.start()
            # If this block is already inside a previously found block, skip it
            if any(r[0] <= start < r[1] for r in block_ranges):
                continue

            # Find matching endblock
            depth_inner = 1
            pos = open_match.end()
            while depth_inner > 0:
                nxt_open = _RE_BLOCK_OPEN.search(text, pos)
                nxt_close = _RE_BLOCK_CLOSE.search(text, pos)
                if nxt_close is None:
                    break
                if nxt_open and nxt_open.start() < nxt_close.start():
                    depth_inner += 1
                    pos = nxt_open.end()
                else:
                    depth_inner -= 1
                    if depth_inner == 0:
                        block_ranges.append((start, nxt_close.end()))
                        break
                    pos = nxt_close.end()

        # Build outside_text by joining gaps between top-level blocks
        last_pos = 0
        for start, end in sorted(block_ranges):
            outside_text += text[last_pos:start]
            last_pos = end
        outside_text += text[last_pos:]

        child_orphans = []
        for m in _RE_TOKENS.finditer(outside_text):
            tag = m.group(0)
            if not any(
                tag.strip().startswith(p)
                for p in [
                    "{%- extends",
                    "{% extends",
                    "{%- block",
                    "{% block",
                    "{%- endblock",
                    "{% endblock",
                ]
            ):
                child_orphans.append(tag)

        child_logic = "\n".join(child_orphans)

        child_blocks = {}
        # Nesting-aware block extraction
        for open_match in _RE_BLOCK_OPEN.finditer(text):
            name = open_match.group(1)
            if name in child_blocks:
                continue  # already found
            start = open_match.end()
            depth_inner = 1
            pos = start
            while depth_inner > 0:
                nxt_open = _RE_BLOCK_OPEN.search(text, pos)
                nxt_close = _RE_BLOCK_CLOSE.search(text, pos)
                if nxt_close is None:
                    break
                if nxt_open and nxt_open.start() < nxt_close.start():
                    depth_inner += 1
                    pos = nxt_open.end()
                else:
                    depth_inner -= 1
                    if depth_inner == 0:
                        child_blocks[name] = text[start : nxt_close.start()]
                        break
                    pos = nxt_close.end()

        # Nesting-aware block replacement in parent
        blocks_to_replace = []
        for m in _RE_BLOCK_OPEN.finditer(parent_text):
            name = m.group(1)
            start = m.end()
            depth_inner = 1
            pos = start
            while depth_inner > 0:
                nxt_open = _RE_BLOCK_OPEN.search(parent_text, pos)
                nxt_close = _RE_BLOCK_CLOSE.search(parent_text, pos)
                if nxt_close is None:
                    break
                if nxt_open and nxt_open.start() < nxt_close.start():
                    depth_inner += 1
                    pos = nxt_open.end()
                else:
                    depth_inner -= 1
                    if depth_inner == 0:
                        blocks_to_replace.append(
                            (m.start(), nxt_close.end(), name, start, nxt_close.start())
                        )
                        break
                    pos = nxt_close.end()

        # Sort blocks by start position descending to handle nested blocks correctly
        # and keep string offsets valid during replacement.
        for full_start, full_end, name, content_start, content_end in sorted(
            blocks_to_replace, key=lambda x: x[0], reverse=True
        ):
            content = child_blocks.get(name, parent_text[content_start:content_end])
            replacement = f"{{% block {name} %}}{content}{{% endblock %}}"
            parent_text = (
                parent_text[:full_start] + replacement + parent_text[full_end:]
            )
        if child_logic:
            parent_text = child_logic + "\n" + parent_text
        return handle_inheritance(parent_text, depth + 1)

    template_string = handle_inheritance(template_string)

    # 1.5. Optional block tag stripping or marker injection
    if inject_markers:
        # Replace block tags with HTML comment markers for data-block targeting
        # Use nesting-aware replacement to match opening and closing tags
        replacements = []
        for open_match in _RE_BLOCK_OPEN.finditer(template_string):
            block_name = open_match.group(1)
            start_pos = open_match.start()
            end_pos = open_match.end()

            # Find the matching endblock (nesting-aware)
            depth = 1
            pos = end_pos
            close_start = None
            close_end = None
            while depth > 0:
                next_open = _RE_BLOCK_OPEN.search(template_string, pos)
                next_close = _RE_BLOCK_CLOSE.search(template_string, pos)
                if next_close is None:
                    break
                if next_open and next_open.start() < next_close.start():
                    depth += 1
                    pos = next_open.end()
                else:
                    depth -= 1
                    if depth == 0:
                        close_start = next_close.start()
                        close_end = next_close.end()
                    pos = next_close.end()

            if close_start is not None:
                # Inject markers BEFORE the opening tag and AFTER the closing tag
                # to keep the block tags intact for the Jinja-like renderer.
                # SECURITY: Skip markers for blocks inside tags where HTML comments are invalid

                # Check if block is inside <style>, <script>, <title>, or <meta name="description">
                def is_inside_no_comment_tag(text_before: str) -> bool:
                    """Check if we're inside a tag where HTML comments are invalid."""
                    # Look for unclosed <style>, <script>, <title> tags before this position
                    for tag in ["style", "script", "title"]:
                        # Find last opening tag
                        last_open = text_before.rfind(f"<{tag}")
                        if last_open == -1:
                            continue
                        # Check if it's been closed
                        last_close = text_before.rfind(f"</{tag}>", last_open)
                        if last_close == -1:
                            # Tag is open, we're inside it
                            return True

                    # Check for <meta name="description">
                    if "<meta" in text_before and 'name="description"' in text_before:
                        # Simplified check - if we see description meta recently, skip
                        last_meta = text_before.rfind("<meta")
                        if (
                            last_meta != -1
                            and 'name="description"' in text_before[last_meta:]
                        ):
                            return True

                    return False

                text_before = template_string[:start_pos]
                skip_markers = block_name in (
                    "title",
                    "description",
                    "styles",
                    "scripts",
                ) or is_inside_no_comment_tag(text_before)

                if not skip_markers:
                    replacements.append(
                        (start_pos, start_pos, f"<!-- block:{block_name}:start -->")
                    )
                    replacements.append(
                        (close_end, close_end, f"<!-- block:{block_name}:end -->")
                    )

        # Apply replacements in descending order of position to preserve offsets
        replacements.sort(key=lambda x: x[0], reverse=True)
        for start, end, replacement in replacements:
            # We use end:end for insertion if start==end, but here we want to keep the original content
            # so we use template_string[start:end] which is the original tag.
            template_string = (
                template_string[:start] + replacement + template_string[start:]
            )
    elif strip_blocks:
        template_string = _RE_BLOCK_OPEN.sub("", template_string)
        template_string = _RE_BLOCK_CLOSE.sub("", template_string)

    # 2. Pre-process includes recursively
    def handle_includes(text: str, depth: int = 0) -> str:
        if depth > 5:
            return text

        def replace_include(match: re.Match[str]) -> str:
            inc_path = match.group(1).strip("'\"")
            try:
                search_path = _safe_resolve(root_dir or os.getcwd(), inc_path)
            except ValueError:
                return "<!-- Include Error: path traversal blocked -->"
            if not os.path.exists(search_path):
                # Try automatic extension resolution
                # 1. Try appending
                for ext in (".html", ".asok"):
                    if os.path.exists(search_path + ext):
                        search_path = search_path + ext
                        break

                # 2. Try swapping
                if not os.path.exists(search_path):
                    base_path, current_ext = os.path.splitext(search_path)
                    if current_ext == ".html" and os.path.exists(base_path + ".asok"):
                        search_path = base_path + ".asok"
                    elif current_ext == ".asok" and os.path.exists(base_path + ".html"):
                        search_path = base_path + ".html"

            if os.path.exists(search_path):
                try:
                    # SECURITY: Limit include file size to prevent DoS (max 1MB)
                    file_size = os.path.getsize(search_path)
                    if file_size > 1_000_000:
                        return "<!-- Include Error: file too large -->"

                    with open(search_path, "r", encoding="utf-8") as f:
                        return handle_includes(f.read(), depth + 1)
                except OSError:
                    return f"<!-- Error reading {inc_path} -->"
            return f"<!-- Include Error: {inc_path} not found -->"

        return _RE_INCLUDE.sub(replace_include, text)

    template_string = handle_includes(template_string)

    # 3. Pre-process component blocks (Slots)
    def handle_components(text: str) -> str:
        def replace_comp(match: re.Match[str]) -> str:
            name = match.group(1).strip()
            args = match.group(2).strip()
            # Clean leading/trailing comma if any
            args = args.strip(",").strip()
            content = match.group(3)
            # Escape content for inclusion in a string literal
            safe_content = (
                content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            )
            comma = ", " if args else ""
            return f'{{{{ component("{name}"{comma}{args}, slot="{safe_content}") }}}}'

        while _RE_COMPONENT.search(text):
            text = _RE_COMPONENT.sub(replace_comp, text)
        return text

    template_string = handle_components(template_string)

    # 3. Handle macro imports: {% from "file" import name1, name2 %}
    if context is not None:
        for m in _RE_FROM_IMPORT.finditer(template_string):
            macro_file = m.group(1)
            names = [n.strip() for n in m.group(2).split(",")]
            try:
                full_path = _safe_resolve(root_dir or os.getcwd(), macro_file)
            except ValueError:
                continue
            if os.path.exists(full_path):
                imported = _extract_macros(full_path, names, parent_ctx=context)
                context.update(imported)
    template_string = _RE_FROM_IMPORT.sub("", template_string)

    # 3b. Handle full imports: {% import "file" as namespace %}
    if context is not None:
        for m in _RE_IMPORT_AS.finditer(template_string):
            macro_file = m.group(1)
            namespace_name = m.group(2)
            try:
                full_path = _safe_resolve(root_dir or os.getcwd(), macro_file)
            except ValueError:
                continue
            if os.path.exists(full_path):
                # Get all macros from the file
                all_macro_names = _get_all_macro_names(full_path)
                imported = _extract_macros(
                    full_path, all_macro_names, parent_ctx=context
                )
                # Create a namespace object
                context[namespace_name] = type("Namespace", (), imported)()
    template_string = _RE_IMPORT_AS.sub("", template_string)

    # 3c. Handle filter blocks: {% filter upper %}content{% endfilter %}
    def replace_filter_block(m: re.Match[str]) -> str:
        filter_chain = m.group(1)
        content = m.group(2)
        # Escape content for safe inclusion and apply filter
        safe_content = (
            content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        )
        return f'{{{{ "{safe_content}"|{filter_chain} }}}}'

    template_string = _RE_FILTER_BLOCK.sub(replace_filter_block, template_string)

    # 3d. Handle autoescape blocks: {% autoescape false %}...{% endautoescape %}
    def replace_autoescape_block(m: re.Match[str]) -> str:
        enabled = m.group(1) == "true"
        content = m.group(2)

        if not enabled:
            # When autoescape is false, mark all {{ }} as safe
            # Replace {{ expr }} with {{ expr|safe }}
            def add_safe_filter(var_match: re.Match[str]) -> str:
                expr = var_match.group(0)[2:-2].strip()
                # Don't add |safe if already has it
                if "|safe" in expr or expr.endswith("|safe"):
                    return var_match.group(0)
                return f"{{{{ {expr}|safe }}}}"

            content = re.sub(r"\{\{[^}]+\}\}", add_safe_filter, content)

        return content

    template_string = _RE_AUTOESCAPE_BLOCK.sub(
        replace_autoescape_block, template_string
    )

    # 4. Strip comments {# ... #}
    template_string = _RE_COMMENT.sub("", template_string)

    # 5. Protect {% raw %}...{% endraw %} content by escaping template syntax
    def _neutralize_raw(m: re.Match[str]) -> str:
        content = m.group(1)
        return content.replace("{{", "&#123;&#123;").replace("{%", "&#123;&#37;")

    template_string = _RE_RAW.sub(_neutralize_raw, template_string)

    # 6. Extract inline macros and add them to context
    if context is not None:
        for match in _RE_MACRO.finditer(template_string):
            macro_name = match.group(1)
            raw_params = match.group(2).strip()
            body = match.group(3)

            param_names = []
            param_defaults = {}
            varargs = None
            varkw = None

            if raw_params:
                for param in raw_params.split(","):
                    param = param.strip()
                    if not param:
                        continue
                    if param.startswith("**"):
                        varkw = param[2:]
                    elif param.startswith("*"):
                        varargs = param[1:]
                    elif "=" in param:
                        pname, pdefault = param.split("=", 1)
                        pname = pname.strip()
                        param_names.append(pname)
                        param_defaults[pname] = pdefault.strip()
                    else:
                        param_names.append(param)

            # Create the macro function
            def _make_inline_macro(
                m_body: str,
                m_params: list[str],
                m_defaults: dict[str, str],
                m_varargs: Optional[str],
                m_varkw: Optional[str],
                m_context: Optional[dict[str, Any]],
            ) -> Any:
                def macro_fn(*args: Any, **kwargs: Any) -> SafeString:
                    local_ctx = dict(m_context or {})

                    # Map positional args to named params
                    used_kwargs = set()
                    for i, pname in enumerate(m_params):
                        if i < len(args):
                            local_ctx[pname] = args[i]
                        elif pname in kwargs:
                            local_ctx[pname] = kwargs[pname]
                            used_kwargs.add(pname)
                        elif pname in m_defaults:
                            try:
                                local_ctx[pname] = ast.literal_eval(m_defaults[pname])
                            except (ValueError, SyntaxError):
                                local_ctx[pname] = m_defaults[pname]
                        else:
                            local_ctx[pname] = ""

                    # Collect *varargs
                    if m_varargs:
                        local_ctx[m_varargs] = args[len(m_params) :]

                    # Collect **varkw
                    if m_varkw:
                        remaining = {
                            k: v for k, v in kwargs.items() if k not in m_params
                        }
                        local_ctx[m_varkw] = remaining

                    # Always pass caller if provided (for {% call macro() %})
                    if "caller" in kwargs and "caller" not in used_kwargs:
                        local_ctx["caller"] = kwargs["caller"]

                    from .engine import render_template_string

                    return SafeString(render_template_string(m_body, local_ctx))

                return macro_fn

            context[macro_name] = _make_inline_macro(
                body, param_names, param_defaults, varargs, varkw, context
            )

        # Remove macro definitions from template
        template_string = _RE_MACRO.sub("", template_string)

    return template_string
