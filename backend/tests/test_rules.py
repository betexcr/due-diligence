from app.rules import DocFacts, validate_document, validate_project

BASE = {
    "revenue": 12_500_000, "cost_of_goods_sold": 5_000_000, "gross_profit": 7_500_000,
    "operating_expenses": 4_000_000, "operating_income": 3_500_000, "net_income": 2_600_000,
    "total_assets": 20_000_000, "total_liabilities": 11_000_000, "total_equity": 9_000_000,
    "total_debt": 4_000_000, "cash": 2_500_000,
}


def facts(doc_id="d1", year=2024, currency="USD", company="Alpha Manufacturing Inc.", **overrides):
    # Figures are written in whole currency units for readability; rules work in minor units.
    values = {k: (v * 100 if v is not None else None) for k, v in {**BASE, **overrides}.items()}
    return DocFacts(doc_id, f"{doc_id}.pdf", company, year, currency, values)


def rules(issues):
    return sorted(i.rule for i in issues)


def test_consistent_statement_has_no_issues():
    assert validate_document(facts()) == []


def test_operating_income_mismatch_is_flagged_with_expected_value():
    [issue] = validate_document(facts(operating_income=4_500_000))
    assert issue.rule == "operating_income_identity"
    assert issue.expected == 3_500_000_00 and issue.actual == 4_500_000_00
    assert "operating_income" in issue.fields


def test_operating_income_without_gross_profit_uses_revenue_minus_costs():
    issues = validate_document(facts(gross_profit=None, cost_of_goods_sold=None, operating_expenses=9_000_000))
    assert issues == []  # 12.5M - 9M = 3.5M, the doc's example


def test_tolerance_absorbs_rounding():
    assert validate_document(facts(operating_income=3_510_000)) == []  # 0.29% off, within 0.5%


def test_balance_sheet_identity():
    assert rules(validate_document(facts(total_equity=8_000_000))) == ["balance_sheet_identity"]


def test_sanity_checks():
    assert "debt_exceeds_liabilities" in rules(validate_document(facts(total_debt=12_000_000)))
    assert "cash_exceeds_assets" in rules(validate_document(facts(cash=27_000_000, total_assets=26_000_000,
                                                                   total_liabilities=17_000_000)))


def test_required_fields_scoped_to_statements_present():
    pnl_only = facts(total_assets=None, total_liabilities=None, total_equity=None, total_debt=None, cash=None)
    assert validate_document(pnl_only) == []
    partial = facts(net_income=None)
    assert rules(validate_document(partial)) == ["required_field_missing"]


def test_low_confidence_flagged():
    d = facts()
    d.confidence = {"revenue": 0.4}
    assert rules(validate_document(d)) == ["low_confidence"]


def test_cross_document_conflict_same_year():
    issues = validate_project("Alpha Manufacturing", [facts("a"), facts("b", operating_income=4_500_000)])
    assert rules(issues) == ["cross_document_conflict"]


def test_currency_and_company_mismatch():
    issues = validate_project("Alpha Manufacturing", [facts("a"), facts("b", year=2023, currency="EUR",
                                                                         company="Beta Holdings Ltd")])
    assert rules(issues) == ["company_mismatch", "currency_mismatch"]


def test_revenue_swing_warning():
    issues = validate_project("Alpha", [facts("a", year=2023, revenue=5_000_000), facts("b")])
    assert "revenue_swing" in rules(issues)


def test_tolerance_floor_is_one_currency_unit():
    # A 1-unit rounding difference on small figures is not a finding; 2 units is.
    small = dict(revenue=100, cost_of_goods_sold=40, gross_profit=60, operating_expenses=20, net_income=30,
                 total_assets=None, total_liabilities=None, total_equity=None, total_debt=None, cash=None)
    assert validate_document(facts(**small, operating_income=41)) == []
    assert rules(validate_document(facts(**small, operating_income=42))) == ["operating_income_identity"]


def test_message_formats_minor_units_as_currency():
    [issue] = validate_document(facts(operating_income=4_500_000))
    assert "4,500,000" in issue.message and "3,500,000" in issue.message
