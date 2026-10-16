// Zod mirror of backend/app/contracts.py. Every API response is parsed through
// these schemas, so a backend contract change fails loudly at the boundary
// instead of rendering undefined values in the review UI.
import { z } from "zod";

export const DocumentStatus = z.enum(["uploaded", "processing", "needs_review", "validated", "failed"]);
export const JobStatus = z.enum(["queued", "running", "succeeded", "dead"]);
export const FieldStatus = z.enum(["extracted", "flagged", "missing", "approved", "corrected"]);
export const Severity = z.enum(["error", "warning", "info"]);
export const Evidence = z.enum(["verified", "no_source", "quote_not_found", "value_not_in_quote"]);

// Mirrors backend/app/money.py MAX_MINOR: the largest amount that is exact as a JS number.
export const MAX_MINOR = Number.MAX_SAFE_INTEGER;

export const Project = z.object({
  id: z.string(),
  name: z.string(),
  company_name: z.string(),
  created_at: z.string(),
  document_count: z.number().int(),
  open_issue_count: z.number().int(),
});

export const Document = z.object({
  id: z.string(),
  project_id: z.string(),
  file_name: z.string(),
  content_type: z.string(),
  size_bytes: z.number().int(),
  sha256: z.string(),
  status: DocumentStatus,
  fiscal_year: z.number().int().nullable(),
  error: z.string().nullable(),
  created_at: z.string(),
  excluded: z.boolean(),
  excluded_reason: z.string().nullable(),
});

export const Job = z.object({
  id: z.string(),
  document_id: z.string(),
  file_name: z.string().nullable(),
  kind: z.string(),
  status: JobStatus,
  attempts: z.number().int(),
  max_attempts: z.number().int(),
  next_run_at: z.string(),
  last_error: z.string().nullable(),
  error_kind: z.enum(["retryable", "permanent"]).nullable(),
  updated_at: z.string(),
});

export const JobAttempt = z.object({
  id: z.string(),
  job_id: z.string(),
  attempt: z.number().int(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  outcome: z.string().nullable(),
  error: z.string().nullable(),
  delay_seconds: z.number().nullable(),
});

export const ExtractedField = z.object({
  id: z.string(),
  document_id: z.string(),
  name: z.string(),
  // Money is integer minor units (hundredths of the currency unit), see lib/money.ts.
  value_minor: z.number().int().nullable(),
  original_value_minor: z.number().int().nullable(),
  page: z.number().int().nullable(),
  sheet: z.string().nullable(),
  cell: z.string().nullable(),
  quote: z.string().nullable(),
  confidence: z.number().min(0).max(1),
  status: FieldStatus,
  evidence: Evidence.nullable(),
  version: z.number().int().min(1),
});

export const ValidationIssue = z.object({
  id: z.string(),
  document_id: z.string().nullable(),
  project_id: z.string(),
  rule: z.string(),
  severity: Severity,
  message: z.string(),
  fields: z.array(z.string()),
  expected_minor: z.number().int().nullable(),
  actual_minor: z.number().int().nullable(),
  resolved: z.boolean(),
});

export const Review = z.object({
  id: z.string(),
  field_id: z.string(),
  action: z.string(),
  old_value_minor: z.number().int().nullable(),
  new_value_minor: z.number().int().nullable(),
  note: z.string().nullable(),
  reviewer: z.string(),
  created_at: z.string(),
});

export const UploadResult = z.object({
  document: Document,
  duplicate: z.boolean(),
  job_id: z.string().nullable(),
});

export const ProjectDetail = z.object({
  project: Project,
  documents: z.array(Document),
  issues: z.array(ValidationIssue),
  jobs: z.array(Job),
  report_ready: z.boolean(),
});

export const DocumentDetail = z.object({
  document: Document,
  company_name: z.string().nullable(),
  currency: z.string().nullable(),
  reporting_period_end: z.string().nullable(),
  fields: z.array(ExtractedField),
  issues: z.array(ValidationIssue),
  reviews: z.array(Review),
  jobs: z.array(Job),
});

export const Health = z.object({ ok: z.boolean(), extractor: z.string(), model: z.string() });

// Outgoing payloads are validated too, before they leave the browser.
export const ProjectCreate = z.object({
  name: z.string().trim().min(1, "Project name is required").max(200),
  company_name: z.string().trim().min(1, "Company name is required").max(200),
});

export const ReviewAction = z.discriminatedUnion("action", [
  z.object({
    action: z.literal("approve"),
    expected_version: z.number().int().min(1),
    note: z.string().max(1000).optional(),
  }),
  z.object({
    action: z.literal("correct"),
    expected_version: z.number().int().min(1),
    value_minor: z.number().int({ error: "Enter an amount" }).min(-MAX_MINOR).max(MAX_MINOR),
    note: z.string().trim().min(1, "Explain where the corrected value comes from").max(1000),
  }),
]);

// Approving a flagged value accepts its validation issue, so the API requires a note.
export const AcceptFlagged = z.object({
  action: z.literal("approve"),
  expected_version: z.number().int().min(1),
  note: z.string().trim().min(1, "Explain why the value is right as printed").max(1000),
});

export const DocumentExclusion = z.object({
  reason: z.string().trim().min(1, "Give a reason; it is shown in the report").max(500),
});

export type Project = z.infer<typeof Project>;
export type Document = z.infer<typeof Document>;
export type Job = z.infer<typeof Job>;
export type JobAttempt = z.infer<typeof JobAttempt>;
export type ExtractedField = z.infer<typeof ExtractedField>;
export type ValidationIssue = z.infer<typeof ValidationIssue>;
export type ProjectDetail = z.infer<typeof ProjectDetail>;
export type DocumentDetail = z.infer<typeof DocumentDetail>;
export type ReviewAction = z.infer<typeof ReviewAction>;
export type Evidence = z.infer<typeof Evidence>;
