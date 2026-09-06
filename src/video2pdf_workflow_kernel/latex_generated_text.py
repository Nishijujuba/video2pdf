from __future__ import annotations

from dataclasses import dataclass
import re


_TCOLORBOX_DECLARATION = re.compile(
    r"\s*\\newtcolorbox\{([^{}]+)\}"
    r"(?:\[[^\[\]]*\]){0,2}"
    r"\{((?:[^{}]|\{[^{}]*\})*)\}\s*"
)
_TCOLORBOX_TITLE = re.compile(
    r"(?:^|,)\s*title=(\{[^{}]*\}|[^,{}]+)(?:,|$)"
)
_TCOLORBOX_BOUNDARY = re.compile(
    r"\s*\\(begin|end)\{([^{}]+)\}"
    r"(?:\[([^\[\]]*)\])?\s*(?:%.*)?"
)


@dataclass(frozen=True)
class TcolorboxInvocation:
    environment: str
    begin_line: int
    end_line: int
    title_override: str | None


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


def extract_tcolorbox_invocations(
    source_text: str,
    environments: set[str],
) -> list[TcolorboxInvocation]:
    """Pair supported tcolorbox begins and ends in source order.

    A supported begin is a physical line containing only ``\\begin{name}``,
    one simple optional argument group, and an optional comment.  A literal
    ``title`` option overrides the style default.  Target-environment
    boundaries that do not fit this grammar, contain a non-literal title, or
    cannot be paired are rejected.
    """

    open_invocations: list[tuple[str, int, str | None]] = []
    completed: list[TcolorboxInvocation] = []
    for line_number, source_line in enumerate(source_text.splitlines(), 1):
        mentions_target = any(
            f"\\begin{{{environment}}}" in source_line
            or f"\\end{{{environment}}}" in source_line
            for environment in environments
        )
        boundary = _TCOLORBOX_BOUNDARY.fullmatch(source_line)
        if boundary is None:
            if mentions_target:
                raise ValueError(
                    f"unsupported tcolorbox invocation boundary at line {line_number}"
                )
            continue
        action, environment, options = boundary.groups()
        if environment not in environments:
            continue
        if action == "end":
            if options is not None:
                raise ValueError(
                    f"unsupported tcolorbox end boundary at line {line_number}"
                )
            if not open_invocations or open_invocations[-1][0] != environment:
                raise ValueError(
                    f"unmatched tcolorbox end boundary at line {line_number}"
                )
            _, begin_line, title_override = open_invocations.pop()
            completed.append(
                TcolorboxInvocation(
                    environment=environment,
                    begin_line=begin_line,
                    end_line=line_number,
                    title_override=title_override,
                )
            )
            continue
        title_override = None
        if options is not None:
            title_matches = list(_TCOLORBOX_TITLE.finditer(options))
            if "title=" in options and len(title_matches) != 1:
                raise ValueError(
                    f"unsupported tcolorbox title override at line {line_number}"
                )
            if title_matches:
                value = title_matches[0].group(1).strip()
                if value.startswith("{") and value.endswith("}"):
                    value = value[1:-1]
                if not value or "\\" in value:
                    raise ValueError(
                        f"unsupported tcolorbox title override at line {line_number}"
                    )
                title_override = value
        open_invocations.append((environment, line_number, title_override))
    if open_invocations:
        raise ValueError(
            "unmatched tcolorbox begin boundary at line "
            + str(open_invocations[-1][1])
        )
    return sorted(completed, key=lambda value: value.begin_line)
