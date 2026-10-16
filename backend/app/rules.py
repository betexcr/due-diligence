"""Deterministic business validation. The LLM extracts; this code checks.

Pure functions over plain data so every rule is unit-testable without a
database or a model. Rules encode accounting identities and sanity checks;
which ones are hard errors vs warnings is a client decision, not a universal
truth (e.g. IFRS vs US GAAP presentation differences), so severities live here
in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .config import settings
from .contracts import Severity
from .money import MINOR_PER_UNIT, format_minor

REQUIRED_BY_STATEMENT = {
    "income_statement": ["revenue", "operating_income", "net_income"],
    "balance_sheet": ["total_assets", "total_liabilities", "total_equity"],
}


@dataclass
class DocFacts:
    document_id: str
    file_name: str
    company_name: str | None
    fiscal_year: int | None
    currency: str | None
    values: dict[str, int | None]  # integer minor units
    confidence: dict[str, float] = field(default_factory=dict)


@dataclass
class Issue:
    rule: str
    severity: Severity
    message: str
    fields: list[str]
    document_id: str | None = None
    expected: int | None = None  # minor units
    actual: int | None = None


def _close(expected: int, actual: int, tolerance_pct: float | None = None) -> bool:
    """Within tolerance_pct of the larger magnitude, and never stricter than one
    whole currency unit (statements round to units)."""
    tol = Decimal(str(settings.tolerance_pct if tolerance_pct is None else tolerance_pct))
    allowed = max(Decimal(MINOR_PER_UNIT), abs(tol) / 100 * max(abs(expected), abs(actual)))
    return abs(expected - actual) <= allowed


def _fmt(x: int) -> str:
    return format_minor(x)


def validate_document(d: DocFacts) -> list[Issue]:
    v = d.values
    out: list[Issue] = []

    def issue(rule, severity, message, fields, expected=None, actual=None):
        out.append(Issue(rule, severity, message, fields, d.document_id, expected, actual))

    # Only demand the statements a document actually contains: a P&L-only
    # spreadsheet or a bank letter is not "missing" its balance sheet.
    required: list[str] = []
    for group in REQUIRED_BY_STATEMENT.values():
        if any(v.get(f) is not None for f in group):
            required += group
    for f in required:
        if v.get(f) is None:
            issue("required_field_missing", Severity.warning,
                  f"No reliable evidence for {f.replace('_', ' ')} in {d.file_name}.", [f])

    for f, c in d.confidence.items():
        if v.get(f) is not None and c < settings.min_confidence:
            issue("low_confidence", Severity.warning,
                  f"Low extraction confidence ({c:.0%}) for {f.replace('_', ' ')}.", [f])

    rev, cogs, gp = v.get("revenue"), v.get("cost_of_goods_sold"), v.get("gross_profit")
    opex, opinc, ni = v.get("operating_expenses"), v.get("operating_income"), v.get("net_income")
    assets, liab, eq = v.get("total_assets"), v.get("total_liabilities"), v.get("total_equity")
    debt, cash = v.get("total_debt"), v.get("cash")

    if rev is not None and cogs is not None and gp is not None:
        exp = rev - cogs
        if not _close(exp, gp):
            issue("gross_profit_identity", Severity.error,
                  f"Gross profit {_fmt(gp)} does not equal revenue minus cost of goods sold ({_fmt(exp)}).",
                  ["gross_profit", "revenue", "cost_of_goods_sold"], exp, gp)

    if opinc is not None and opex is not None:
        if gp is not None:
            base, base_fields, label = gp, ["gross_profit"], "gross profit"
        elif rev is not None:
            base = rev - (cogs or 0)
            base_fields = ["revenue"] + (["cost_of_goods_sold"] if cogs is not None else [])
            label = "revenue" if cogs is None else "revenue minus cost of goods sold"
        else:
            base = None
        if base is not None:
            exp = base - opex
            if not _close(exp, opinc):
                issue("operating_income_identity", Severity.error,
                      f"Operating income {_fmt(opinc)} does not equal {label} minus operating expenses ({_fmt(exp)}).",
                      ["operating_income", "operating_expenses", *base_fields], exp, opinc)

    if assets is not None and liab is not None and eq is not None:
        exp = liab + eq
        if not _close(exp, assets):
            issue("balance_sheet_identity", Severity.error,
                  f"Total assets {_fmt(assets)} do not equal liabilities plus equity ({_fmt(exp)}).",
                  ["total_assets", "total_liabilities", "total_equity"], exp, assets)

    if rev is not None and rev < 0:
        issue("negative_revenue", Severity.error, "Revenue is negative.", ["revenue"], None, rev)
    if rev is not None and ni is not None and rev > 0 and ni > rev:
        issue("net_income_exceeds_revenue", Severity.warning,
              f"Net income {_fmt(ni)} exceeds revenue {_fmt(rev)}; check for one-off gains or a mis-read line.",
              ["net_income", "revenue"], None, ni)
    if debt is not None and liab is not None and debt > liab:
        issue("debt_exceeds_liabilities", Severity.error,
              f"Total debt {_fmt(debt)} exceeds total liabilities {_fmt(liab)}.",
              ["total_debt", "total_liabilities"], liab, debt)
    if cash is not None and assets is not None and cash > assets:
        issue("cash_exceeds_assets", Severity.error,
              f"Cash {_fmt(cash)} exceeds total assets {_fmt(assets)}.", ["cash", "total_assets"], assets, cash)

    return out


def _same_company(a: str, b: str) -> bool:
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
    na, nb = norm(a), norm(b)
    return bool(na) and bool(nb) and (na in nb or nb in na)


def validate_project(project_company: str, docs: list[DocFacts]) -> list[Issue]:
    """Cross-document checks: identity, currency, conflicting periods, trends."""
    out: list[Issue] = []

    for d in docs:
        if d.company_name and not _same_company(project_company, d.company_name):
            out.append(Issue("company_mismatch", Severity.warning,
                             f"{d.file_name} names '{d.company_name}', expected '{project_company}'.",
                             [], d.document_id))

    currencies = {d.currency for d in docs if d.currency}
    if len(currencies) > 1:
        out.append(Issue("currency_mismatch", Severity.error,
                         f"Documents use different currencies: {', '.join(sorted(currencies))}. "
                         "Figures are not comparable without conversion.", []))

    by_year: dict[int, list[DocFacts]] = {}
    for d in docs:
        if d.fiscal_year:
            by_year.setdefault(d.fiscal_year, []).append(d)

    for year, group in by_year.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                for f in ("revenue", "operating_income", "net_income", "total_assets"):
                    va, vb = a.values.get(f), b.values.get(f)
                    if va is not None and vb is not None and not _close(va, vb):
                        out.append(Issue(
                            "cross_document_conflict", Severity.error,
                            f"FY{year} {f.replace('_', ' ')} differs: {_fmt(va)} in {a.file_name} "
                            f"vs {_fmt(vb)} in {b.file_name}.",
                            [f], b.document_id, va, vb))

    years = sorted(by_year)
    for prev, cur in zip(years, years[1:]):
        rp = by_year[prev][0].values.get("revenue")
        rc = by_year[cur][0].values.get("revenue")
        if rp and rc and rp > 0:
            change = (rc - rp) / rp
            if abs(change) > 0.5:
                out.append(Issue("revenue_swing", Severity.warning,
                                 f"Revenue moved {change:+.0%} from FY{prev} to FY{cur}; confirm "
                                 "acquisitions, disposals or restatements.",
                                 ["revenue"], by_year[cur][0].document_id, rp, rc))
    return out
