import assert from "node:assert/strict";
import { test } from "node:test";
import type { ExtractedField, ValidationIssue } from "./contracts.ts";
import { evidenceProblem, needsJustification, suggestedCorrection } from "./review.ts";

const field = (name: string): ExtractedField => ({
  id: `f-${name}`, document_id: "d1", name, value_minor: 1, original_value_minor: 1, page: 2, sheet: null,
  cell: null, quote: "q", confidence: 0.95, status: "flagged", evidence: "verified", version: 1,
});

const issue = (rule: string, fields: string[], expected: number | null, over: Partial<ValidationIssue> = {}): ValidationIssue => ({
  id: rule, document_id: "d1", project_id: "p1", rule, severity: "error", message: "m", fields,
  expected_minor: expected, actual_minor: 0, resolved: false, ...over,
});

const opIncome = issue("operating_income_identity", ["operating_income", "operating_expenses", "gross_profit"], 350_000_000);

test("an identity's expected value is suggested only for the field it checks", () => {
  assert.deepEqual(suggestedCorrection(field("operating_income"), [opIncome]), { minor: 350_000_000, source: "calculated" });
  assert.equal(suggestedCorrection(field("gross_profit"), [opIncome]), null);
  assert.equal(suggestedCorrection(field("operating_expenses"), [opIncome]), null);
});

test("reference values are never offered as corrections", () => {
  const swing = issue("revenue_swing", ["revenue"], 900_000_000, { severity: "warning" });
  const debt = issue("debt_exceeds_liabilities", ["total_debt", "total_liabilities"], 500);
  assert.equal(suggestedCorrection(field("revenue"), [swing]), null);
  assert.equal(suggestedCorrection(field("total_debt"), [debt]), null);
});

test("a conflicting figure in another document is suggested as such", () => {
  const conflict = issue("cross_document_conflict", ["operating_income"], 350_000_000);
  assert.deepEqual(suggestedCorrection(field("operating_income"), [conflict]),
    { minor: 350_000_000, source: "other_document" });
});

test("resolved issues and other documents' issues suggest nothing", () => {
  assert.equal(suggestedCorrection(field("operating_income"), [{ ...opIncome, resolved: true }]), null);
  assert.equal(suggestedCorrection(field("operating_income"), [{ ...opIncome, document_id: "d2" }]), null);
});

test("approving needs a note while any open error or warning involves the field", () => {
  assert.equal(needsJustification([opIncome]), true);
  assert.equal(needsJustification([issue("x", ["revenue"], null, { severity: "warning" })]), true);
  assert.equal(needsJustification([{ ...opIncome, resolved: true }]), false);
  assert.equal(needsJustification([issue("x", ["revenue"], null, { severity: "info" })]), false);
  assert.equal(needsJustification([]), false);
});

test("evidence problems fail closed and ignore analyst-entered values", () => {
  assert.equal(evidenceProblem(field("revenue")), null);
  assert.match(evidenceProblem({ ...field("revenue"), evidence: "ambiguous" }) ?? "", /unambiguously/);
  assert.match(evidenceProblem({ ...field("revenue"), evidence: null }) ?? "", /never verified/);
  assert.equal(evidenceProblem({ ...field("revenue"), evidence: "quote_not_found", status: "corrected" }), null);
  assert.equal(evidenceProblem({ ...field("revenue"), evidence: null, value_minor: null }), null);
});
