"""Deterministic check of the evidence the model cites for each value.

The model's quote and page are claims, not facts. Before a value can count as
"passed all checks" we confirm, in code, that the quote really appears in the
page or sheet it cites, and that the quote really contains the number reported.
A value that fails either check is flagged for individual review.

The comparison is deliberately conservative: anything we cannot match (e.g.
"12.500.000,00" European formatting) is reported as unverified rather than
guessed at, which costs the analyst a look, never a wrong approval.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .contracts import DocumentContent, Evidence, ExtractedValue

# A printed number: optional parentheses/sign/currency symbol, thousands commas,
# optional decimals. The lookbehind skips digits glued to letters, so the "5" in
# a cell reference like "B5" is not read as a figure.
_NUMBER = re.compile(r"(?<![A-Za-z\d.,])\(?[-−]?[$€£]?\s?(\d[\d,]*(?:\.\d+)?)\)?")


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _numbers(text: str) -> set[Decimal]:
    out = set()
    for m in _NUMBER.finditer(text):
        try:
            out.add(abs(Decimal(m.group(1).replace(",", ""))))
        except InvalidOperation:
            continue
    return out


def _cited_text(content: DocumentContent, page: int | None, sheet: str | None) -> str:
    if sheet is not None:
        parts = [p.text for p in content.pages if p.sheet == sheet]
    elif page is not None:
        parts = [p.text for p in content.pages if p.page == page]
    else:
        parts = [p.text for p in content.pages]
    return "\n".join(parts)


def check_evidence(value: ExtractedValue, content: DocumentContent) -> Evidence | None:
    """None when there is no value to check."""
    if value.value is None:
        return None
    if value.source is None:
        return Evidence.no_source
    src = value.source
    quote = _normalise(src.quote)
    if not quote:
        return Evidence.no_source
    if quote not in _normalise(_cited_text(content, src.page, src.sheet)):
        return Evidence.quote_not_found
    printed = abs(Decimal(str(value.value)))
    if printed not in _numbers(src.quote):
        return Evidence.value_not_in_quote
    return Evidence.verified
