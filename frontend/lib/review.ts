import type { ExtractedField, ValidationIssue } from "./contracts";

// Rules whose expected_minor is a candidate value for the issue's first field:
// the arithmetic result of an identity, or the same line item in another document.
// Other rules carry a reference value that must never be offered as a correction
// (e.g. revenue_swing's expected is the prior year's revenue; debt_exceeds_liabilities'
// is total liabilities).
const SUGGESTING_RULES: Record<string, string> = {
  gross_profit_identity: "calculated",
  operating_income_identity: "calculated",
  balance_sheet_identity: "calculated",
  cross_document_conflict: "other_document",
};

export type Suggestion = { minor: number; source: "calculated" | "other_document" };

/** The value to pre-fill when correcting `field`, or null when no issue suggests one for it. */
export function suggestedCorrection(field: ExtractedField, issues: ValidationIssue[]): Suggestion | null {
  for (const i of issues) {
    const source = SUGGESTING_RULES[i.rule];
    // expected_minor describes fields[0] only: the other fields are inputs to the check.
    if (!source || i.resolved || i.expected_minor === null || i.fields[0] !== field.name) continue;
    if (i.document_id !== field.document_id) continue;
    return { minor: i.expected_minor, source: source as Suggestion["source"] };
  }
  return null;
}
