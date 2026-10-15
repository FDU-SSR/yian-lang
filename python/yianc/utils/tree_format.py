"""Shared layout for textual compiler tree dumps."""

from __future__ import annotations

from collections.abc import Callable, Sequence


class TreeFormatter:
    """Render labeled tree lines while callers choose the visible children."""

    def render_line(self, guides: list[bool], is_last: bool, label: str) -> str:
        prefix = "".join("│   " if guide else "    " for guide in guides)
        return prefix + ("└── " if is_last else "├── ") + label + "\n"

    def render_items[ItemType](
        self,
        items: Sequence[ItemType],
        guides: list[bool],
        render_item: Callable[[ItemType, list[bool], bool], str],
    ) -> str:
        return "".join(
            render_item(item, guides, index == len(items) - 1)
            for index, item in enumerate(items)
        )

    def render_child[ItemType](
        self,
        label: str,
        child: ItemType,
        guides: list[bool],
        parent_is_last: bool,
        is_last: bool,
        render_item: Callable[[ItemType, list[bool], bool], str],
    ) -> str:
        child_guides = guides + [not parent_is_last]
        return (
            self.render_line(child_guides, is_last, f"{label}:")
            + render_item(child, child_guides + [not is_last], True)
        )
