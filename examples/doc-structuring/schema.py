"""Replace OCR'd schema-dump fragments with AUTHORITATIVE ones (§4F).

When your document embeds machine-generated schema listings (e.g. an XSD/RELAX-NG annex)
and you *also* have the real schema files, don't trust the OCR — swap each parsed
declaration for the authoritative one. This module is the generic core:

  * `load_authoritative_decls(sources)` — index every declaration in real `.xsd`/`.rnc`
    files (inside `.zip`s or a directory), keyed by schema-file basename;
  * `pick_home_schema(index, names)` — which schema file a given dump came from
    (highest declaration-name overlap);
  * `match_decl(home_map, name, …)` — resolve one OCR'd name to its authoritative
    (kind, canonical-name, well-formed fragment): exact → case-insensitive → fuzzy.

It knows standard XSD (`xsd:`/`xs:`) and RELAX-NG-compact syntax — not your spec. The
caller (see `example_pipeline.py`) plugs `match_decl` into a `tree.TreeConfig.split_leaf`
to emit one authoritative file per declaration, so even mis-named/dangling OCR fragments
self-correct (the authoritative kind drives the group dir + filename).
"""

from __future__ import annotations

import difflib
import re
import zipfile
from pathlib import Path
from typing import Callable, Iterable

_XSD_DECL = re.compile(
    r'<(?:xsd|xs):(complexType|simpleType|element|group|attributeGroup)\b[^>]*?\bname="([^"]+)"')
_RNC_DECL = re.compile(r"^([A-Za-z_][\w.]*)\s*=", re.M)
_RNC_KEYWORDS = {"namespace", "default", "datatypes", "include", "div", "grammar"}


def xsd_decl_fragment(text: str, start: int) -> str:
    """The balanced XSD element at/after `start` (self-closing + nested same-tag aware),
    so each authoritative fragment is well-formed on its own. `start` may point at leading
    whitespace, so advance to the opening `<` first. Works for `xsd:`/`xs:` prefixes."""
    lt = text.find("<", start)
    if lt == -1:
        return text[start:].rstrip()
    start = lt
    m = re.compile(r"<(xsd|xs):(\w+)\b").match(text, start)
    if not m:
        return text[start:].rstrip()
    pfx, tag = m.group(1), m.group(2)
    gt = text.find(">", start)
    if gt == -1:
        return text[start:].rstrip()
    if text[gt - 1] == "/":                       # self-closing
        return text[start:gt + 1]
    open_pat = re.compile(rf"<{pfx}:{tag}\b")
    close = f"</{pfx}:{tag}>"
    depth, pos = 1, gt + 1
    while depth > 0:
        no = open_pat.search(text, pos)
        nc = text.find(close, pos)
        if nc == -1:
            return text[start:].rstrip()          # unbalanced source — keep all
        if no and no.start() < nc:
            oe = text.find(">", no.start())
            if oe != -1 and text[oe - 1] == "/":   # nested self-closing — not a real open
                pos = oe + 1
            else:
                depth, pos = depth + 1, oe + 1
        else:
            depth, pos = depth - 1, nc + len(close)
    return text[start:pos]


def load_authoritative_decls(sources: Iterable[Path], prefer: tuple = ("Transitional",),
                             deprioritize: tuple = ("Strict",)) -> dict:
    """Index declarations from real `.xsd`/`.rnc` files (in `.zip`s or directories).

    Returns {"xsd": {file: {name: (kind, fragment)}}, "rnc": {file: {name: fragment}}}.
    On a duplicate basename across editions, `prefer`-tagged files win and
    `deprioritize`-tagged ones lose (e.g. Transitional over Strict)."""
    xsd: dict[str, dict] = {}
    rnc: dict[str, dict] = {}

    def rank(name: str) -> int:
        if any(k in name for k in prefer):
            return 0
        if any(k in name for k in deprioritize):
            return 2
        return 1

    members: list[tuple[int, str, Callable[[], bytes]]] = []
    for src in sources:
        src = Path(src)
        if src.suffix == ".zip":
            try:
                zf = zipfile.ZipFile(src)
            except zipfile.BadZipFile:
                continue
            for n in zf.namelist():
                if n.endswith((".xsd", ".rnc")):
                    members.append((rank(src.name), n.split("/")[-1], (lambda z=zf, m=n: z.read(m))))
        elif src.is_dir():
            for f in list(src.rglob("*.xsd")) + list(src.rglob("*.rnc")):
                members.append((rank(f.name), f.name, (lambda f=f: f.read_bytes())))
    members.sort(key=lambda t: t[0])              # preferred editions first -> setdefault wins

    for _r, base, read in members:
        text = read().decode("utf-8", "replace")
        if base.endswith(".xsd"):
            d = xsd.setdefault(base, {})
            for mm in _XSD_DECL.finditer(text):
                d.setdefault(mm.group(2), (mm.group(1), xsd_decl_fragment(text, mm.start())))
        else:
            decls = [(mm.start(), mm.group(1)) for mm in _RNC_DECL.finditer(text)
                     if mm.group(1) not in _RNC_KEYWORDS]
            d = rnc.setdefault(base, {})
            for i, (st, nm) in enumerate(decls):
                end = decls[i + 1][0] if i + 1 < len(decls) else len(text)
                d.setdefault(nm, text[st:end].rstrip())
    return {"xsd": xsd, "rnc": rnc}


def pick_home_schema(index: dict, names: list[str],
                     name_variants: Callable[[str], set] | None = None) -> "str | None":
    """The schema file whose declaration set best overlaps this dump's decl names.
    `index` is auth["xsd"] or auth["rnc"]; `name_variants(name)->set` lets you try OCR
    variants (space→underscore, a known correction) before counting overlap."""
    nv = name_variants or (lambda n: {n})
    cands = [c for n in names for c in nv(n)]
    best, best_c = None, 0
    for fn, d in index.items():
        c = sum(1 for c0 in cands if c0 in d)
        if c > best_c:
            best, best_c = fn, c
    return best


def match_decl(home_map: dict, name: str, is_rnc: bool,
               name_variants: Callable[[str], set] | None = None, cutoff: float = 0.85):
    """Resolve an OCR'd decl name to (kind, authoritative_name, fragment) within the home
    file: exact → case-insensitive → fuzzy. None if no confident match."""
    nv = name_variants or (lambda n: {n})

    def pack(key: str):
        if is_rnc:
            return ("define", key, home_map[key])
        kind, frag = home_map[key]
        return (kind, key, frag)

    for c in nv(name):
        if c in home_map:
            return pack(c)
    low = {k.lower(): k for k in home_map}
    for c in nv(name):
        if c.lower() in low:
            return pack(low[c.lower()])
    cm = difflib.get_close_matches(name, list(home_map), n=1, cutoff=cutoff)
    return pack(cm[0]) if cm else None
