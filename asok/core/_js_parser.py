"""Tiny ad-hoc JS parser helpers used for directive validation.

Every helper goes through the shared JsScanner so quote/escape logic isn't
re-implemented in each function — see ``_js_scanner``.
"""

from __future__ import annotations

import re

from ._js_scanner import JsScanner, iter_structural_chars


def find_outside_char(s: str, target: str) -> int:
    sc = JsScanner(s)
    while sc.remaining():
        if not sc.advance():
            continue
        if _skip_question_pair(sc, target):
            continue
        if sc.char == target:
            return sc.i
        sc.step()
    return -1


def _skip_question_pair(sc: JsScanner, target: str) -> bool:
    if target != "?":
        return False
    if sc.s[sc.i : sc.i + 2] not in ("??", "?."):
        return False
    sc.step()
    sc.step()
    return True


def find_outside_arrow(s: str) -> int:
    sc = JsScanner(s)
    while sc.i < len(s) - 1:
        if not sc.advance():
            continue
        if sc.s[sc.i : sc.i + 2] == "=>":
            return sc.i
        sc.step()
    return -1


def find_matching_paren_forward(s: str, target_close_idx: int) -> int:
    stack: list[int] = []
    for i, char in iter_structural_chars(s):
        result = _process_paren_match(stack, i, char, target_close_idx)
        if result is not None:
            return result
    return -1


def _process_paren_match(
    stack: list[int], i: int, char: str, target: int
) -> int | None:
    if char == "(":
        stack.append(i)
        return None
    if char == ")" and stack:
        open_idx = stack.pop()
        if i == target:
            return open_idx
    return None


def find_matching_forward(s: str, start_idx: int, open_char: str, close_char: str) -> int:
    depth = 0
    for i, char in iter_structural_chars(s, start_idx):
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i + 1
    return len(s)


# ── Expression body scan ─────────────────────────────────────────────

_OPEN_BRACKETS = {"(": "paren", "[": "bracket", "{": "brace"}
_CLOSE_BRACKETS = {")": "paren", "]": "bracket", "}": "brace"}


def find_expression_body_end(s: str, start_idx: int) -> int:
    depths = {"paren": 0, "bracket": 0, "brace": 0}
    for i, char in iter_structural_chars(s, start_idx):
        if _is_terminator(char, depths):
            return i
        _update_depth(char, depths)
    return len(s)


def _is_terminator(char: str, depths: dict[str, int]) -> bool:
    kind = _CLOSE_BRACKETS.get(char)
    if kind is not None and depths[kind] == 0:
        return True
    return char in (",", ";") and all(d == 0 for d in depths.values())


def _update_depth(char: str, depths: dict[str, int]) -> None:
    open_kind = _OPEN_BRACKETS.get(char)
    if open_kind is not None:
        depths[open_kind] += 1
        return
    close_kind = _CLOSE_BRACKETS.get(char)
    if close_kind is not None and depths[close_kind] > 0:
        depths[close_kind] -= 1


# ── Arrow function extraction ────────────────────────────────────────


def extract_arrow_functions(s: str) -> tuple[str, list[str]]:
    idx = find_outside_arrow(s)
    if idx == -1:
        return s, []
    param_start = _arrow_param_start(s, idx)
    if param_start is None:
        return s.replace("=>", "lambda_dummy:"), []
    body_start = _arrow_body_start(s, idx)
    if body_start is None:
        return s.replace("=>", "lambda_dummy:"), []
    body_end, body_content = _arrow_body_extract(s, body_start)
    modified_expr = f"{s[:param_start]}None{s[body_end:]}"
    parsed_body, bodies_from_body = extract_arrow_functions(body_content)
    parsed_modified, bodies_from_modified = extract_arrow_functions(modified_expr)
    all_bodies = [parsed_body] + bodies_from_body + bodies_from_modified
    return parsed_modified, all_bodies


def _arrow_param_start(s: str, idx: int) -> int | None:
    i = _skip_whitespace_back(s, idx - 1)
    if i < 0:
        return None
    if s[i] == ")":
        match = find_matching_paren_forward(s, i)
        return match if match != -1 else i
    return _identifier_start(s, i)


def _skip_whitespace_back(s: str, start: int) -> int:
    i = start
    while i >= 0 and s[i].isspace():
        i -= 1
    return i


def _identifier_start(s: str, end_inclusive: int) -> int:
    i = end_inclusive
    while i >= 0 and (s[i].isalnum() or s[i] in "_$"):
        i -= 1
    return i + 1


def _arrow_body_start(s: str, idx: int) -> int | None:
    i = idx + 2
    while i < len(s) and s[i].isspace():
        i += 1
    return i if i < len(s) else None


def _arrow_body_extract(s: str, body_start: int) -> tuple[int, str]:
    if s[body_start] == "{":
        body_end = find_matching_forward(s, body_start, "{", "}")
        return body_end, s[body_start + 1 : body_end - 1]
    body_end = find_expression_body_end(s, body_start)
    return body_end, s[body_start:body_end]


# ── Statement splitter ───────────────────────────────────────────────


def split_js_statements(s: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    sc = JsScanner(s)
    stack: list[str] = []
    while sc.remaining():
        _split_step(sc, stack, current, parts)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _split_step(
    sc: JsScanner, stack: list[str], current: list[str], parts: list[str]
) -> None:
    if not sc.advance():
        current.append(sc.s[sc.i - 1])
        return
    _consume_split_char(sc, stack, current, parts)


def _consume_split_char(
    sc: JsScanner, stack: list[str], current: list[str], parts: list[str]
) -> None:
    char = sc.char
    if _is_statement_break(char, stack):
        parts.append("".join(current).strip())
        current.clear()
        sc.step()
        return
    _adjust_split_stack(char, stack)
    current.append(char)
    sc.step()


def _is_statement_break(char: str, stack: list[str]) -> bool:
    return char == ";" and not stack


def _adjust_split_stack(char: str, stack: list[str]) -> None:
    if char in ("(", "[", "{"):
        stack.append(char)
    elif char in (")", "]", "}") and stack:
        stack.pop()


def parse_js_if_statement(s: str) -> tuple[str, str] | None:
    match = re.match(r"^if\s*\(", s)
    if not match:
        return None
    start_paren = match.end() - 1
    end_paren = find_matching_forward(s, start_paren, "(", ")")
    if end_paren == -1 or end_paren > len(s):
        return None
    cond = s[start_paren + 1 : end_paren - 1].strip()
    return cond, _unwrap_block_body(s[end_paren:].strip())


def _unwrap_block_body(body: str) -> str:
    if body.startswith("{") and body.endswith("}"):
        return body[1:-1].strip()
    return body


# ── Ternary → Python if/else ─────────────────────────────────────────


def convert_ternary(s: str) -> str:
    q_idx = find_outside_char(s, "?")
    if q_idx == -1:
        return s
    c_idx = _find_ternary_colon(s, q_idx)
    if c_idx == -1:
        masked = s[:q_idx] + "\x00" + s[q_idx + 1 :]
        return convert_ternary(masked).replace("\x00", "?")
    stack, delimiters = _scan_ternary_left(s, q_idx)
    cond_start = _ternary_boundary(stack, delimiters) + 1
    expr2_end = _find_ternary_expr2_end(s, c_idx, stack)
    return _assemble_ternary(s, cond_start, q_idx, c_idx, expr2_end)


def _find_ternary_colon(s: str, q_idx: int) -> int:
    depth = 1
    sc = JsScanner(s, q_idx + 1)
    while sc.remaining():
        if not sc.advance():
            continue
        depth = _advance_ternary_depth(sc, depth)
        if depth == 0:
            return sc.i
        sc.step()
    return -1


def _advance_ternary_depth(sc: JsScanner, depth: int) -> int:
    if sc.char == "?":
        if not _is_skippable_question(sc.s, sc.i):
            depth += 1
        return depth
    if sc.char == ":":
        return depth - 1
    return depth


def _is_skippable_question(s: str, i: int) -> bool:
    if s[i : i + 2] == "??":
        return True
    if i > 0 and s[i - 1] == "?":
        return True
    return s[i : i + 2] == "?."


def _is_single_equal(s: str, i: int) -> bool:
    if i > 0 and s[i - 1] in ("=", "!", "<", ">"):
        return False
    return not (i + 1 < len(s) and s[i + 1] == "=")


def _scan_ternary_left(s: str, q_idx: int) -> tuple[list[tuple[str, int]], dict[int, list[int]]]:
    stack: list[tuple[str, int]] = []
    delimiters: dict[int, list[int]] = {}
    for i, char in iter_structural_chars(s):
        if i >= q_idx:
            break
        _scan_left_char(stack, delimiters, s, i, char)
    return stack, delimiters


def _scan_left_char(
    stack: list[tuple[str, int]],
    delimiters: dict[int, list[int]],
    s: str, i: int, char: str,
) -> None:
    if char in ("(", "[", "{"):
        stack.append((char, i))
        return
    if char in (")", "]", "}"):
        if stack:
            stack.pop()
        return
    if _is_left_delimiter(s, i, char):
        delimiters.setdefault(len(stack), []).append(i)


def _is_left_delimiter(s: str, i: int, char: str) -> bool:
    if char in (",", ";"):
        return True
    return char == "=" and _is_single_equal(s, i)


def _ternary_boundary(
    stack: list[tuple[str, int]], delimiters: dict[int, list[int]]
) -> int:
    boundary = -1
    if stack:
        boundary = stack[-1][1]
    L = len(stack)
    if L in delimiters and delimiters[L]:
        boundary = max(boundary, delimiters[L][-1])
    return boundary


def _find_ternary_expr2_end(s: str, c_idx: int, stack: list[tuple[str, int]]) -> int:
    L = len(stack)
    for i, char in iter_structural_chars(s, c_idx + 1):
        result = _ternary_expr2_step(s, i, char, stack, L)
        if result is not None:
            return result
    return len(s)


def _ternary_expr2_step(
    s: str, i: int, char: str, stack: list[tuple[str, int]], L: int
) -> int | None:
    if char in ("(", "[", "{"):
        stack.append((char, i))
        return None
    if char in (")", "]", "}"):
        return _ternary_close_step(stack, L, i)
    if _is_ternary_break(s, i, char) and len(stack) == L:
        return i
    return None


def _ternary_close_step(stack: list[tuple[str, int]], L: int, i: int) -> int | None:
    if len(stack) == L:
        return i
    if stack:
        stack.pop()
    return None


def _is_ternary_break(s: str, i: int, char: str) -> bool:
    if char in (",", ";"):
        return True
    return char == "=" and _is_single_equal(s, i)


def _assemble_ternary(s: str, cond_start: int, q_idx: int, c_idx: int, expr2_end: int) -> str:
    cond = s[cond_start:q_idx].strip()
    expr1 = s[q_idx + 1 : c_idx].strip()
    expr2 = s[c_idx + 1 : expr2_end].strip()
    reconstructed = (
        f"{s[:cond_start]}(({convert_ternary(expr1)}) "
        f"if ({convert_ternary(cond)}) else ({convert_ternary(expr2)})){s[expr2_end:]}"
    )
    return convert_ternary(reconstructed)
