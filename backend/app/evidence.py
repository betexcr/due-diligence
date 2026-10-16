"""Deterministic check of the evidence the model cites for each value.

The model's quote and page are claims, not facts. Before a value can count as
"passed all checks" we confirm, in code, that:

- the quote really appears in the page or sheet it cites;
- the quote names a line item (a bare number says nothing about what it is);
- the quote contains the reported number, with its sign: a loss printed as
  (1,200) reported as +1,200 is not verified;
- the quote pins down which figure it is: a line with several figures (e.g. two
  fiscal-year columns) is ambiguous unless the cited cell identifies the figure.

A value that fails any check is flagged for individual review.

The comparison is deliberately conservative: anything we cannot match (e.g.
"12.500.000,00" European formatting) is reported as unverified rather than
guessed at, which costs the analyst a look, never a wrong approval.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .contracts import DocumentContent, Evidence, ExtractedValue

# Cost lines are printed as positive numbers in some statements and in
# parentheses in others; the model reports them as positive costs either way,
# so for these only the magnitude is compared.
SIGN_BY_CONVENTION = frozenset({"cost_of_goods_sold", "operating_expenses"})

# A printed number: optional parentheses/sign/currency symbol, thousands commas,
# optional decimals. The lookbehind skips digits glued to letters, so the "5" in
# a cell reference like "B5" is not read as a figure.
_NUMBER = re.compile(r"(?<![A-Za-z\d.,])(\()?([-−])?\s?[$€£]?\s?(\d[\d,]*(?:\.\d+)?)(\))?")
_CELL_PREFIX = re.compile(r"\b[A-Z]{1,3}\d+=")
_LABEL = re.compile(r"[^\W\d_]{2,}")
_YEAR = re.compile(r"(?:19|20)\d\d")


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _figures(text: str, keep: Decimal) -> list[Decimal]:
    """Signed numbers printed in text. Bare years (e.g. "2024") are skipped, since
    "Revenue for 2024: 12,500,000" holds one figure, unless the year is the value."""
    out = []
    for m in _NUMBER.finditer(text):
        raw = m.group(3)
        try:
            n = Decimal(raw.rstrip(",").replace(",", ""))
        except InvalidOperation:
            continue
        negative = bool(m.group(2)) or bool(m.group(1) and m.group(4))
        if _YEAR.fullmatch(raw) and not negative and n != abs(keep):
            continue
        out.append(-n if negative else n)
    return out


def _cited_text(content: DocumentContent, page: int | None, sheet: str | None) -> str:
    if sheet is not None:
        parts = [p.text for p in content.pages if p.sheet == sheet]
    elif page is not None:
        parts = [p.text for p in content.pages if p.page == page]
    else:
        parts = [p.text for p in content.pages]
    return "\n".join(parts)


def _cell_text(quote: str, cell: str | None) -> str | None:
    """The text of one cell in a spreadsheet line like "A5=Revenue | B5=12500000"."""
    if not cell:
        return None
    m = re.search(rf"(?<![A-Za-z]){re.escape(cell.strip().upper())}=([^|]*)", quote)
    return m.group(1) if m else None


def check_evidence(value: ExtractedValue, content: DocumentContent, field: str | None = None) -> Evidence | None:
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
    if not _LABEL.search(_CELL_PREFIX.sub(" ", src.quote)):
        return Evidence.ambiguous

    printed = Decimal(str(value.value))
    signed = field not in SIGN_BY_CONVENTION
    cell = _cell_text(src.quote, src.cell)
    candidates = _figures(cell if cell is not None else src.quote, printed)
    same = (lambda c: c == printed) if signed else (lambda c: abs(c) == abs(printed))
    if not any(same(c) for c in candidates):
        return Evidence.value_not_in_quote
    if len({abs(c) for c in candidates}) > 1:
        return Evidence.ambiguous
    return Evidence.verified
