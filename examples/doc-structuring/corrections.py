"""Apply a vetted token-correction map to fix OCR garbles of element/attribute names.

Each correction should be verified beforehand (target is a real name in your schema
vocabulary, garble is not — see verify.py). A token that collides with a real name
(valid as something else) goes in `attr_only` so it's only corrected in attribute /
table-name context, never as an element tag.

Example: `oldName -> newName`. A token that is also valid as something else (e.g. a real
element name) goes in `attr_only` so it's only corrected as an attribute / table name.
"""

from __future__ import annotations

import re


def apply_corrections(text: str, corrections: dict[str, str], attr_only: set[str] | None = None) -> str:
    """Replace garbles with corrections, scoped to XML name contexts.

    - tokens in `attr_only`: only `name=` (attribute) and `| name (` (table-name cell);
    - all others: whole-word replace anywhere (safe — they aren't valid names).
    """
    attr_only = attr_only or set()
    for g, t in corrections.items():
        eg = re.escape(g)
        if g in attr_only:
            text = re.sub(rf"(?<![\w]){eg}(?=\s*=)", t, text)        # attribute: name=
            text = re.sub(rf"(?<=\|\s){eg}(?=\s*\()", t, text)        # table cell: | name (
        else:
            text = re.sub(rf"(?<![\w]){eg}(?![\w])", t, text)         # whole-word
    return text


def apply_overlay(text: str, patches, on_miss=None) -> str:
    """Per-section overlay: a list of one-off, hand/PDF-verified edits that can't be
    generalized into the corrections map (a `\\@` switch garble, a dropped `)`, glued
    attribute names…). Each patch is {"find", "replace", "regex"?}.

    Apply this LAST — after cross-link rendering — so each `find` matches the on-disk text
    verbatim (that's what a reviewer copies from the rendered file). A `find` that no
    longer matches (source drift after a re-parse) is reported via `on_miss(find)` and
    skipped, never silently lost. Keep the patch DATA next to your document (e.g. a JSON
    file); this function is the generic applier.
    """
    for p in patches or []:
        find, repl = p["find"], p.get("replace", "")
        if p.get("regex"):
            new = re.sub(find, repl, text)
            if new == text and on_miss:
                on_miss(find)
            text = new
        elif find in text:
            text = text.replace(find, repl)
        elif on_miss:
            on_miss(find)
    return text
