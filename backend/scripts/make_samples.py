"""Generate sample due-diligence documents for Alpha Manufacturing Inc.

Each one exercises a different path through the pipeline:
  alpha_fs_2023.pdf              clean, internally consistent
  alpha_fs_2024.pdf              operating income printed as 4,500,000 (should be 3,500,000)
  alpha_mgmt_accounts_2024.xlsx  says operating income 3,500,000 -> cross-document conflict
  alpha_bank_confirmation.txt    fails twice with a transient error, then succeeds (retry + jitter)
  corrupt_upload.pdf             not a real PDF -> permanent error -> dead-letter queue

Run:  uv run python scripts/make_samples.py
"""

from pathlib import Path

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent.parent / "samples"


def statement_pdf(path: Path, year: int, income: list[tuple[str, int]], balance: list[tuple[str, int]]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, h = A4

    def page(title: str, lines: list[str]) -> None:
        y = h - 72
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, y, title)
        y -= 28
        c.setFont("Courier", 10)
        for line in lines:
            c.drawString(72, y, line)
            y -= 16
        c.showPage()

    row = lambda label, v: f"{label:<36}{v:>16,}"
    page("Alpha Manufacturing Inc.", [
        "Company: Alpha Manufacturing Inc.",
        f"Audited Financial Statements for the fiscal year ended December 31, {year}",
        f"Period end: {year}-12-31",
        "All amounts in USD",
        "",
        "Contents",
        "  Page 2  Income statement",
        "  Page 3  Balance sheet",
    ])
    page(f"Income Statement - FY{year}", [row(k, v) for k, v in income])
    page(f"Balance Sheet - as at {year}-12-31", [row(k, v) for k, v in balance])
    c.save()


def main() -> None:
    OUT.mkdir(exist_ok=True)

    statement_pdf(OUT / "alpha_fs_2023.pdf", 2023, [
        ("Revenue", 10_200_000),
        ("Cost of goods sold", 4_300_000),
        ("Gross profit", 5_900_000),
        ("Operating expenses", 3_600_000),
        ("Operating income", 2_300_000),
        ("Net income", 1_650_000),
    ], [
        ("Cash and cash equivalents", 1_900_000),
        ("Total assets", 17_500_000),
        ("Total debt", 4_500_000),
        ("Total liabilities", 10_100_000),
        ("Total equity", 7_400_000),
    ])

    # Deliberate inconsistency: 7,500,000 - 4,000,000 = 3,500,000, but 4,500,000 is printed.
    statement_pdf(OUT / "alpha_fs_2024.pdf", 2024, [
        ("Revenue", 12_500_000),
        ("Cost of goods sold", 5_000_000),
        ("Gross profit", 7_500_000),
        ("Operating expenses", 4_000_000),
        ("Operating income", 4_500_000),
        ("Net income", 2_600_000),
    ], [
        ("Cash and cash equivalents", 2_500_000),
        ("Total assets", 20_000_000),
        ("Total debt", 4_000_000),
        ("Total liabilities", 11_000_000),
        ("Total equity", 9_000_000),
    ])

    wb = Workbook()
    ws = wb.active
    ws.title = "P&L FY2024"
    rows = [
        ("Company: Alpha Manufacturing Inc.", None),
        ("Management accounts, fiscal year 2024 (USD)", None),
        (None, None),
        ("Revenue", 12_500_000),
        ("Cost of goods sold", 5_000_000),
        ("Gross profit", 7_500_000),
        ("Operating expenses", 4_000_000),
        ("Operating income", 3_500_000),
        ("Net income", 2_600_000),
    ]
    for r in rows:
        ws.append(list(r))
    wb.save(OUT / "alpha_mgmt_accounts_2024.xlsx")

    (OUT / "alpha_bank_confirmation.txt").write_text(
        "Company: Alpha Manufacturing Inc.\n"
        "Bank confirmation letter, fiscal year 2024, USD\n"
        "Period end: 2024-12-31\n\n"
        "Cash and cash equivalents: 2,500,000\n"
        "Total debt: 4,000,000\n\n"
        "SIMULATE_FAILURE: transient x2\n",
        encoding="utf-8",
    )

    (OUT / "corrupt_upload.pdf").write_bytes(b"%PDF-1.4\nthis is not really a pdf\n")

    for p in sorted(OUT.iterdir()):
        print(f"{p.name:36} {p.stat().st_size:>8} bytes")


if __name__ == "__main__":
    main()
