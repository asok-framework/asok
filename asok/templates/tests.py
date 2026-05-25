from __future__ import annotations

TEMPLATE_TESTS = {
    "defined": lambda v: v is not None and v != "",
    "undefined": lambda v: v is None or v == "",
    "none": lambda v: v is None,
    "true": lambda v: v is True,
    "false": lambda v: v is False,
    "even": lambda v: isinstance(v, (int, float)) and int(v) % 2 == 0,
    "odd": lambda v: isinstance(v, (int, float)) and int(v) % 2 != 0,
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "sequence": lambda v: isinstance(v, (list, tuple)),
    "mapping": lambda v: isinstance(v, dict),
    "iterable": lambda v: hasattr(v, "__iter__") and not isinstance(v, (str, bytes)),
    "lower": lambda v: isinstance(v, str) and v.islower(),
    "upper": lambda v: isinstance(v, str) and v.isupper(),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, float),
}
