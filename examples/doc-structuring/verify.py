"""Two independent ways to verify the rendered tree — both essential for trust.

1. Vocabulary check (fast): every element/attribute NAME used in the tree must exist in
   an authoritative schema vocabulary (e.g. the official XSDs). Catches OCR garbles whose
   spelling isn't a real name (a near-miss misspelling of a real one).

2. Source cross-check (definitive): a token MinerU emitted that is ABSENT from its
   section's own source PDF page is a real garble — even when it collides with a valid
   name elsewhere (a garble that happens to match a real name in another context). The PDF text layer
   is independent ground truth. Bounded: each token is checked only against its section's
   pages, deduped — no whole-document scan.

Both are generic; supply your own vocabulary and id->page map.
"""

from __future__ import annotations

import difflib
import re
import zipfile
from pathlib import Path
from typing import Callable


# --- 1. vocabulary ---------------------------------------------------------
class Vocabulary:
    """A set of valid element / attribute / type names + enumeration values."""

    def __init__(self, names: set[str]):
        self.names = names

    def __contains__(self, name: str) -> bool:
        return name in self.names

    def nearest(self, token: str, cutoff: float = 0.85) -> "str | None":
        m = difflib.get_close_matches(token, list(self.names), n=1, cutoff=cutoff)
        return m[0] if m and m[0] != token else None

    @classmethod
    def from_xsd(cls, sources: list[Path]) -> "Vocabulary":
        """Build from `.xsd` files and/or `.zip` archives of them."""
        names: set[str] = set()
        pat = re.compile(
            r'<xsd:(?:element|attribute|complexType|simpleType|group|attributeGroup)\s+[^>]*\bname="([^"]+)"')
        enum = re.compile(r'<xsd:enumeration\s+[^>]*\bvalue="([^"]+)"')

        def feed(text: str) -> None:
            names.update(pat.findall(text))
            names.update(enum.findall(text))

        for src in sources:
            src = Path(src)
            if src.suffix == ".zip":
                with zipfile.ZipFile(src) as z:
                    for n in z.namelist():
                        if n.endswith(".xsd"):
                            feed(z.read(n).decode("utf-8", "replace"))
            elif src.suffix == ".xsd":
                feed(src.read_text(encoding="utf-8", errors="replace"))
        return cls(names)


_PREFIXED = re.compile(r"\b[a-zA-Z][\w]*:([A-Za-z_][\w.\-]*)")
_TABLE_NAME = re.compile(r"^\|\s*([A-Za-z_][\w.\-]+)\s*\(", re.M)
_CODE_FENCE = re.compile(r"```[^\n]*\n(.*?)\n```", re.S)
# Authoritative schema-dump files are already canonical XML — they aren't prose to verify,
# and verifying their decl names against a section's PDF window produces false garbles.
SCHEMA_DIR_PARTS = {"complex-types", "simple-types", "elements", "groups",
                    "attribute-groups", "definitions"}


def check_names(tree_dir: Path, vocab: Vocabulary, skip_prefixes=("xsd", "xs", "m"),
                skip_dirs: "set[str] | None" = SCHEMA_DIR_PARTS) -> list[tuple]:
    """Return (token, count, suggestion, sample_file) for element/attr names used in
    examples + table-name cells that aren't in the vocabulary but closely resemble one
    (i.e. probable OCR garbles)."""
    skip_dirs = skip_dirs or set()
    used: dict[str, int] = {}
    where: dict[str, str] = {}
    for p in Path(tree_dir).rglob("*.md"):
        if skip_dirs & set(p.parts):
            continue
        txt = p.read_text(encoding="utf-8")
        toks = []
        for fence in _CODE_FENCE.findall(txt):
            for m in re.finditer(r"\b([a-zA-Z][\w]*):([A-Za-z_][\w.\-]*)", fence):
                if m.group(1).lower() not in skip_prefixes:
                    toks.append(m.group(2))
        toks += _TABLE_NAME.findall(txt)
        for tok in toks:
            used[tok] = used.get(tok, 0) + 1
            where.setdefault(tok, str(p))
    out = []
    for tok, n in used.items():
        if tok in vocab or len(tok) < 2:
            continue
        sug = vocab.nearest(tok)
        if sug:
            out.append((tok, n, sug, where[tok]))
    return sorted(out, key=lambda t: -t[1])


# --- 2. source cross-check -------------------------------------------------
def _present_as_name(tok: str, text: str) -> bool:
    e = re.escape(tok)
    return bool(re.search(rf"(?<![\w]){e}\s*\(", text) or f":{tok}" in text
                or f"<{tok}" in text or f"{tok}=" in text or f"{tok}>" in text)


def verify_against_pdf(
    tree_dir: Path,
    pdf_path: Path,
    section_of_file: Callable[[Path], "str | None"],
    page_of: dict[str, int],
    vocab: Vocabulary,
    next_page_of: dict[str, int] | None = None,
    span_cap: int = 40,
    benign: "set[tuple[str, str]] | None" = None,
    skip_dirs: "set[str] | None" = SCHEMA_DIR_PARTS,
) -> list[tuple]:
    """Return CONFIRMED garbles: (garble, correct, section_id, file). A garble is a name
    used in a file that is absent (in name-context) from its section's source pages, while
    a near-miss correct name IS present there. `section_of_file(path)->id` maps a file to
    its section; `page_of`/`next_page_of` give 0-based start/next pages.

    `benign` is a set of (garble, section_id) pairs you've reviewed and confirmed are
    genuine distinct names (the "correction" is a real sibling, e.g. useFirstPageNumber
    vs firstPageNumber both exist) — i.e. window-misses, not garbles. They're excluded
    from the result so re-runs stay actionable."""
    benign = benign or set()
    import fitz  # noqa: PLC0415  (pymupdf)

    doc = fitz.open(str(pdf_path))
    norm = [re.sub(r"\s+", " ", doc[i].get_text()) for i in range(doc.page_count)]
    N = len(norm)

    def window(sid: str) -> "str | None":
        p = page_of.get(sid)
        if p is None:
            return None
        end = min((next_page_of or {}).get(sid, p + 2) + 1, p + span_cap)
        return " ".join(norm[max(0, p - 1): min(N, end + 1)])

    skip_dirs = skip_dirs or set()
    confirmed, seen = [], set()
    for f in Path(tree_dir).rglob("*.md"):
        if skip_dirs & set(f.parts):
            continue
        sid = section_of_file(f)
        wt = window(sid) if sid else None
        if not wt:
            continue
        txt = f.read_text(encoding="utf-8")
        names = set(_TABLE_NAME.findall(txt))
        for fence in _CODE_FENCE.findall(txt):
            names.update(m for _p, m in re.findall(r"\b([a-zA-Z][\w]*):([A-Za-z_][\w.\-]*)", fence))
        for tok in names:
            if (tok, sid) in seen:
                continue
            seen.add((tok, sid))
            if _present_as_name(tok, wt):
                continue
            if (tok, sid) in benign:
                continue
            for c in difflib.get_close_matches(tok, list(vocab.names), n=5, cutoff=0.8):
                if c != tok and _present_as_name(c, wt):
                    confirmed.append((tok, c, sid, str(f)))
                    break
    return confirmed
