"""Assign a flat MinerU block stream to sections, in document reading order.

The core is a single forward walk: each block belongs to the section whose heading most
recently appeared. Because boundaries are only ever set by a real heading, a section can
never steal a neighbour's content (the failure mode of page/position heuristics).

You inject `heading_id(block) -> section_id | None`. That callback is where your
domain lives: match a numbered heading, a styled `text_level` line, a code-caption that
holds a heading, an annex title, etc. Keep it precise — false headings fragment content.
"""

from __future__ import annotations

from typing import Callable

from model import Section


def attach_pages(blocks: list[dict], start_page: int) -> list[dict]:
    """Stamp each block with an absolute source page. MinerU `page_idx` is relative to
    the parsed slice, so add the slice's start page. Returns the same list."""
    for b in blocks:
        pi = b.get("page_idx")
        b["_abs_page"] = (start_page + pi) if isinstance(pi, int) else start_page
    return blocks


def segment_stream(
    blocks: list[dict],
    sections_by_id: dict[str, Section],
    heading_id: Callable[[dict], "str | None"],
    is_noise: Callable[[dict], bool] | None = None,
) -> set[str]:
    """Fill `section.blocks` for every section that appears in `blocks`.

    Returns the set of section ids whose heading was matched (so you can report
    coverage / find unmatched sections).
    """
    current: Section | None = None
    matched: set[str] = set()
    for b in blocks:
        sid = heading_id(b)
        if sid is not None and sid in sections_by_id:
            current = sections_by_id[sid]
            matched.add(sid)
            continue          # the heading itself isn't body content (the tree emits its own H1)
        if current is None or (is_noise and is_noise(b)):
            continue
        current.blocks.append(b)
    return matched
