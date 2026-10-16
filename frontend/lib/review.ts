import type { Evidence, ExtractedField, ValidationIssue } from "./contracts";

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

/** True when approving the field accepts open issues, so the API requires a note.
 * `issues` are the field's unresolved issues in its own document. */
export function needsJustification(issues: ValidationIssue[]): boolean {
  return issues.some((i) => !i.resolved && i.severity !== "info");
}

const EVIDENCE_PROBLEM: Record<Exclude<Evidence, "verified">, string> = {
  no_source: "No source text was quoted for this value.",
  quote_not_found: "The quoted text was not found on the cited page. Check the original before approving.",
  value_not_in_quote: "The quoted text does not contain this figure with this sign. Check the original before approving.",
  ambiguous: "The quote does not show unambiguously which line item and figure this is (e.g. several columns). Check the original before approving.",
  not_checked: "This value was extracted before source checks existed, so its quote was never verified. Check the original before approving.",
};

/** Why the model's cited source for this value is unverified, or null when it is
 * verified or irrelevant (no value, or a value the analyst entered). Fails closed:
 * a value without a recorded check is reported as not checked. */
export function evidenceProblem(field: ExtractedField): string | null {
  if (field.value_minor === null || field.status === "corrected" || field.evidence === "verified") return null;
  return EVIDENCE_PROBLEM[field.evidence ?? "not_checked"];
}
