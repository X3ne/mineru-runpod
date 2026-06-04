"""The one shared data type: a Section node in the document hierarchy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    """A node in the document tree.

    id        stable section id, e.g. "1.2.3" or "A.4.1" (yours to define).
    title     human title, e.g. "tbl (Table)".
    page      0-based source-page index where the section starts (for PDF verify).
    children  child Sections, in document order.
    blocks    MinerU content_list blocks that are this section's OWN content
              (everything before its first child) — filled in by segment_stream.

    A Section with children renders as a folder + barrel; a leaf renders as one file.
    """

    id: str
    title: str
    page: int = 0
    children: list["Section"] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)

    @property
    def has_children(self) -> bool:
        return bool(self.children)

    def walk(self):
        """Depth-first iterator over this section and all descendants."""
        yield self
        for c in self.children:
            yield from c.walk()
