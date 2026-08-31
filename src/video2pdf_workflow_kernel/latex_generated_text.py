from __future__ import annotations

import re


_TCOLORBOX_DECLARATION = re.compile(
    r"\s*\\newtcolorbox\{([^{}]+)\}"
    r"(?:\[[^\[\]]*\]){0,2}"
    r"\{((?:[^{}]|\{[^{}]*\})*)\}\s*"
)
_TCOLORBOX_TITLE = re.compile(
    r"(?:^|,)\s*title=(\{[^{}]*\}|[^,{}]+)(?:,|$)"
)


def extract_tcolorbox_titles(style_text: str) -> dict[str, str]:
    """Return deterministic environment-to-title declarations in source order.

    The supported boundary is one physical ``\\newtcolorbox`` declaration per
    line, up to two simple optional argument groups, and at most one grouping
    pair around a literal title. Unsupported macro-generated or nested title
    forms remain absent so their callers can fail closed.
    """

    titles: dict[str, str] = {}
    for source_line in style_text.splitlines():
        declaration = _TCOLORBOX_DECLARATION.fullmatch(source_line)
        if declaration is None:
            continue
        title = _TCOLORBOX_TITLE.search(declaration.group(2))
        if title is None:
            continue
        value = title.group(1).strip()
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1]
        if value and "\\" not in value:
            titles[declaration.group(1)] = value
    return titles
