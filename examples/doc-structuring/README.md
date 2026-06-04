# doc-structuring — from MinerU output to a clean, cross-linked doc tree

A small, **document- and standard-agnostic** toolkit for the *post-processing* half of a
large-document pipeline. The MinerU worker turns a PDF into `content_list.json` blocks;
these modules turn those blocks into a folder/file tree of small, faithful, cross-linked
Markdown files that a coding agent can navigate one file at a time.

Distilled from a real run of a **5,000-page numbered technical standard** (tens of
thousands of files). Nothing here is tied to a particular document, standard, or
repository — see `example_pipeline.py` for end-to-end wiring you drive entirely by CLI
flags.

## Why a library, not a script

Nothing here knows about a specific spec, numbering scheme, or disk layout. You supply
the data and a few small callbacks; the modules do the generic work:

| Module | Stage | What it does |
|---|---|---|
| `render.py` | render | MinerU blocks → clean Markdown (the bulk of the hard-won cleanup) |
| `segment.py` | segment | block stream → sections (single matched-heading forward walk) |
| `tree.py` | structure | sections → folder/file tree with barrels + the "golden rule" (`split_leaf` hook for special sections) |
| `crosslink.py` | link | normalize references; resolvable ones → **relative** links |
| `corrections.py` | correct | a vetted OCR-garble map **plus `apply_overlay`** — per-section one-off find/replace patches (verified, miss-tracked) |
| `schema.py` | reconcile | **authoritative schema replacement**: swap OCR'd schema-dump declarations for the real ones from your `.xsd`/`.rnc` sources |
| `verify.py` | verify | names vs a schema **vocabulary**; tokens vs the **source PDF** (with a reviewed-`benign` allowlist) |
| `model.py` | — | the one shared type, `Section` |

## Data model

One type: `Section(id, title, page, children, blocks)`. Build the hierarchy from
wherever you have it (PDF bookmarks, a TOC, headings). `blocks` are the MinerU
`content_list` entries that are a section's *own* content (filled by `segment_stream`).

## The cleanup `render.py` handles (each from a real artifact)

- code lives in `code_body`/`code_caption` (not `text`); `[Example: … end example]`
  markers bracket the code; page-split code halves are merged; mislabelled fences
  (```txt holding XML) relabelled to ```xml.
- tables → Markdown; inline XML examples wrapped as `$<ns:…>$` recovered; empty
  illustration columns dropped.
- lists: no doubled bullets (`- - foo`); ordered items keep their numbers.
- a long / sentence-like `text_level` block is treated as prose, not a heading.
- `$§1.2.3$`-wrapped section refs and `\-` escaped-dash bullets normalized.

## Verification (do both)

1. **Vocabulary** (`Vocabulary.from_xsd([...])`) — catches OCR garbles whose spelling
   isn't a real schema name.
2. **Source cross-check** (`verify_against_pdf`) — a token absent from its section's own
   PDF page (while a near-miss correct name is present) is a confirmed garble. Catches
   even garbles that collide with a valid name elsewhere (a misread that happens to spell
   another real name). Bounded: per-section pages only, deduped.

Feed confirmed garbles back into the `corrections` map and re-run; the verifier should
then report zero. Pairs you've reviewed as genuine distinct names (window-misses) go in
the `benign` allowlist so re-runs stay actionable.

## Two more last-mile mechanisms

- **Authoritative schema replacement** (`schema.py`) — if your document embeds
  machine-generated schema listings *and* you have the real `.xsd`/`.rnc` files, don't
  trust the OCR: `load_authoritative_decls()` indexes them, `pick_home_schema()` finds
  which file a dump came from, and `match_decl()` resolves each OCR'd name (exact →
  case-insensitive → fuzzy) to its authoritative kind + well-formed fragment. Plug it into
  `TreeConfig.split_leaf` so mis-named/dangling fragments self-correct on rebuild.
- **Per-section overlay patches** (`apply_overlay`) — for the long tail of one-off OCR
  defects that can't be generalized (a `\@` switch garble, a dropped `)`), keep a small
  JSON of `{section-id: [{find, replace, regex?}]}` next to your document and apply it
  **last** (after cross-link rendering) so each `find` matches the on-disk text verbatim.
  A `find` that stops matching after a re-parse is reported, never silently dropped.

## Run the worked example

All inputs are CLI flags — no paths are hard-coded.

```bash
pip install pymupdf           # only needed for the --pdf cross-check
cd examples/doc-structuring
python example_pipeline.py \
    --outline sections.json --batches ./batches --out ./tree \
    [--plan batch_order.json] [--pdf source.pdf] [--xsd ./schemas] \
    [--corrections corrections.json] [--patches patches.json] [--benign benign.json]
```

`--outline` is a JSON list `[{"id","title","page"}, …]` (id like `1.2.3`/`A.4.1`; page is
0-based). `--batches` is a directory of MinerU `*_content_list.json`. The XSD/corrections/
patches/PDF flags are optional — they enable schema reconciliation, OCR-garble fixes,
overlay patches, and source verification respectively. See the module docstring for the
full flag reference.

## Using it on your own document (sketch)

```python
from model import Section
from render import RenderConfig, render_blocks
from segment import attach_pages, segment_stream
from crosslink import SectionIndex, make_linkifier
from tree import TreeConfig, write_tree

by_id, roots = build_my_section_hierarchy()        # your outline -> Sections
segment_stream(my_blocks, by_id, my_heading_id)    # your heading detector
index = SectionIndex(roots, folder_name, file_name, barrel_name)
write_tree(roots, out_dir, TreeConfig(render=my_render, folder_name=..., ...))
```

Relative links mean the tree is portable: it works identically wherever you mount it.
