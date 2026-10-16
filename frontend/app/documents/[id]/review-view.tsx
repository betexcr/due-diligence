"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useId, useState } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import {
  AcceptFlagged, ReviewAction, type DocumentDetail, type Evidence, type ExtractedField, type ValidationIssue,
} from "@/lib/contracts";
import { formatMinor, minorToInput, parseMoneyToMinor } from "@/lib/money";
import { suggestedCorrection } from "@/lib/review";
import {
  Badge, Button, ErrorNote, FIELD_LABELS, FOCUS_RING, INPUT_CLASS, Panel, StatusBadge, TableScroll, usePolled,
} from "@/components/ui";

const SOURCE_PANEL_ID = "source-evidence";

const EVIDENCE_PROBLEM: Record<Exclude<Evidence, "verified">, string> = {
  no_source: "No source text was quoted for this value.",
  quote_not_found: "The quoted text was not found on the cited page. Check the original before approving.",
  value_not_in_quote: "The quoted text does not contain this figure. Check the original before approving.",
};

export function ReviewView() {
  const { id } = useParams<{ id: string }>();
  const { data, error, refresh } = usePolled<DocumentDetail>(() => api.document(id), () => false);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  if (!data) return error ? <ErrorNote error={error} /> : <p className="text-sm text-stone-500" role="status">Loading…</p>;
  const { document: doc, fields, issues, reviews, currency } = data;
  const readOnly = doc.excluded;
  const docIssues = issues.filter((i) => i.document_id === doc.id);
  const projectIssues = issues.filter((i) => i.document_id === null);
  const issuesFor = (name: string) => docIssues.filter((i) => i.fields.includes(name) && !i.resolved);
  // Default to the field the first open issue is about (its first field is the one being checked).
  const focus = docIssues.find((i) => !i.resolved)?.fields[0];
  const current = fields.find((f) => f.id === selected) ?? fields.find((f) => f.name === focus) ?? fields[0];
  const pending = fields.filter((f) => f.status === "extracted").length;

  /** Runs a mutation with one in-flight guard, conflict recovery and announced feedback. */
  async function run(action: () => Promise<unknown>, success: string): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setActionError(null);
    try {
      await action();
      setAnnouncement(success);
      await refresh();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.isConflict) {
        // Someone else changed the data (or reprocessing ran): show the latest state.
        await refresh();
        setActionError(`${e.message} The latest values are now shown.`);
      } else {
        setActionError(errorMessage(e));
      }
      return false;
    } finally {
      setBusy(false);
    }
  }

  const label = (f: ExtractedField) => FIELD_LABELS[f.name] ?? f.name;

  return (
    <div className="space-y-5">
      <p aria-live="polite" className="sr-only">{announcement}</p>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm text-stone-500">
            <Link href={`/projects/${doc.project_id}`} className={`rounded hover:underline ${FOCUS_RING}`}>Project</Link> / review
          </p>
          <h1 className="break-words text-xl font-semibold tracking-tight">{doc.file_name}</h1>
          <p className="text-sm text-stone-600">
            {data.company_name ?? "Unknown company"} · FY{doc.fiscal_year ?? "?"} · {currency ?? "currency not stated"}
            {data.reporting_period_end && ` · period end ${data.reporting_period_end}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {readOnly ? <Badge>excluded</Badge> : <StatusBadge status={doc.status} />}
          {!readOnly && (
            <Button onClick={() => run(() => api.approveAll(doc.id), `Approved ${pending} values.`)}
              disabled={busy || pending === 0}
              title="Approve every value that passed all checks, including the source check. Flagged values still need individual review.">
              {pending === 0 ? "No clean values to approve" : `Approve ${pending} clean value${pending === 1 ? "" : "s"}`}
            </Button>
          )}
        </div>
      </div>

      {readOnly && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-stone-300 bg-stone-100 px-4 py-3 text-sm">
          <p className="flex-1">
            Excluded from validation and the report: <q>{doc.excluded_reason}</q>. Values are read-only.
          </p>
          <Button onClick={() => run(() => api.restore(doc.id), "Document restored.")} disabled={busy}>
            Restore document
          </Button>
        </div>
      )}

      <ErrorNote error={actionError} onDismiss={() => setActionError(null)} />

      {!readOnly && docIssues.filter((i) => !i.resolved).map((i) => <IssueCard key={i.id} issue={i} currency={currency} />)}
      {!readOnly && projectIssues.filter((i) => !i.resolved).map((i) => <IssueCard key={i.id} issue={i} currency={currency} />)}

      <div className="grid gap-5 xl:grid-cols-[1.25fr_1fr]">
        <Panel title="Extracted values">
          {fields.length === 0 ? (
            <p className="px-4 py-6 text-sm text-stone-500">Nothing was extracted from this document.</p>
          ) : (
            <TableScroll label="Extracted values">
              <table className="w-full min-w-[560px] text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-medium">Line item</th>
                    <th scope="col" className="px-2 text-right font-medium">Value</th>
                    <th scope="col" className="px-2 font-medium">Conf.</th>
                    <th scope="col" className="px-2 font-medium">Status</th>
                    <th scope="col" className="px-4 font-medium"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-200">
                  {fields.map((f) => (
                    <FieldRow key={f.id} field={f} label={label(f)} currency={currency} active={current?.id === f.id}
                      issues={issuesFor(f.name)} busy={busy} readOnly={readOnly}
                      onSelect={() => setSelected(f.id)}
                      onAct={(a) => run(() => api.review(f.id, a),
                        `${label(f)} ${a.action === "correct" ? "corrected" : "approved"}.`)} />
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </Panel>

        <div className="min-w-0 space-y-5">
          <SourcePanel doc={doc} field={current} />
          <Panel title="Audit trail">
            <ul className="divide-y divide-stone-200 text-xs">
              {reviews.map((r) => {
                const f = fields.find((x) => x.id === r.field_id);
                return (
                  <li key={r.id} className="px-4 py-2">
                    <time dateTime={r.created_at} className="text-stone-500">{new Date(r.created_at).toLocaleString()}</time>{" "}
                    <b>{r.reviewer}</b> {r.action === "correct" ? "corrected" : "approved"} {f ? label(f) : "a value"}
                    {r.action === "correct" && (
                      <span className="num"> {formatMinor(r.old_value_minor)} → {formatMinor(r.new_value_minor)}</span>
                    )}
                    {r.note && <p className="mt-0.5 text-stone-600">“{r.note}”</p>}
                  </li>
                );
              })}
              {reviews.length === 0 && <li className="px-4 py-3 text-stone-500">No analyst actions yet.</li>}
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function IssueCard({ issue, currency }: { issue: ValidationIssue; currency: string | null }) {
  const border = issue.severity === "error" ? "border-rose-400 bg-rose-50" : "border-amber-400 bg-amber-50";
  const { expected_minor: expected, actual_minor: actual } = issue;
  return (
    <section aria-label={`${issue.severity}: ${issue.rule}`} className={`rounded-md border-l-4 px-4 py-3 text-sm ${border}`}>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={issue.severity} />
        <code className="text-xs text-stone-600">{issue.rule}</code>
      </div>
      <p className="mt-1">{issue.message}</p>
      {expected !== null && actual !== null && (
        <dl className="num mt-2 grid w-fit grid-cols-[auto_auto] gap-x-6 text-xs">
          <dt className="text-stone-600">Extracted</dt><dd className="text-right">{formatMinor(actual, currency)}</dd>
          <dt className="text-stone-600">Calculated / other source</dt><dd className="text-right">{formatMinor(expected, currency)}</dd>
          <dt className="text-stone-600">Difference</dt><dd className="text-right">{formatMinor(actual - expected, currency)}</dd>
        </dl>
      )}
    </section>
  );
}

function FieldRow({ field, label, currency, active, issues, busy, readOnly, onSelect, onAct }: {
  field: ExtractedField; label: string; currency: string | null; active: boolean; issues: ValidationIssue[];
  busy: boolean; readOnly: boolean; onSelect: () => void; onAct: (a: ReviewAction) => Promise<boolean>;
}) {
  // "correct" enters a new value; "accept" approves a flagged value as printed, which needs a note.
  const [mode, setMode] = useState<"correct" | "accept" | null>(null);
  const suggestion = suggestedCorrection(field, issues);
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const ids = { value: useId(), note: useId(), err: useId(), hint: useId() };
  const reviewed = field.status === "approved" || field.status === "corrected";
  const flagged = field.status === "flagged";

  function open(next: "correct" | "accept") {
    setValue(minorToInput(suggestion?.minor ?? field.value_minor));
    setNote("");
    setErr(null);
    setMode(next);
    onSelect();
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    let action: ReviewAction;
    if (mode === "accept") {
      const parsed = AcceptFlagged.safeParse({ action: "approve", note, expected_version: field.version });
      if (!parsed.success) return setErr(parsed.error.issues[0].message);
      action = parsed.data;
    } else {
      const amount = parseMoneyToMinor(value);
      if (!amount.ok) return setErr(amount.error);
      const parsed = ReviewAction.safeParse({
        action: "correct", value_minor: amount.minor, note, expected_version: field.version,
      });
      if (!parsed.success) return setErr(parsed.error.issues[0].message);
      action = parsed.data;
    }
    if (await onAct(action)) setMode(null);
  }

  const hint = mode === "accept"
    ? "Approving a flagged value accepts its validation issue. Your note appears in the report's audit trail."
    : suggestion?.source === "calculated"
      ? "Pre-filled with the calculated value. Verify it against the source before saving."
      : suggestion?.source === "other_document"
        ? "Pre-filled with the value in the other document. Verify which source is right before saving."
        : "Enter the amount as printed, e.g. 3,500,000 or 1200.50.";

  return (
    <>
      <tr className={`align-top ${active ? "bg-stone-100" : ""}`}>
        <th scope="row" className="px-4 py-2 text-left font-normal">
          <button type="button" onClick={onSelect} aria-pressed={active} aria-controls={SOURCE_PANEL_ID}
            className={`rounded text-left font-medium hover:underline ${FOCUS_RING}`}
            title="Show source evidence">
            {label}
          </button>
          {field.status === "corrected" && (
            <div className="num text-xs text-stone-500">was {formatMinor(field.original_value_minor)}</div>
          )}
          {field.evidence && field.evidence !== "verified" && !reviewed && (
            <div className="text-xs text-amber-800">source not verified</div>
          )}
        </th>
        <td className={`num px-2 py-2.5 text-right ${flagged ? "text-rose-700" : ""}`}>
          {formatMinor(field.value_minor, currency)}
        </td>
        <td className="num px-2 py-2.5 text-xs text-stone-500">
          {field.value_minor === null ? "—" : `${Math.round(field.confidence * 100)}%`}
        </td>
        <td className="px-2 py-2.5"><StatusBadge status={field.status} /></td>
        <td className="whitespace-nowrap px-4 py-2 text-right">
          {!readOnly && !reviewed && (
            <>
              {flagged ? (
                <Button variant="ghost" disabled={busy} onClick={() => open("accept")}
                  aria-label={field.value_minor === null ? `Acknowledge ${label} is missing` : `Accept ${label} as printed`}
                  aria-expanded={mode === "accept"}>{field.value_minor === null ? "Ack…" : "Accept…"}</Button>
              ) : (
                <Button variant="ghost" disabled={busy}
                  onClick={() => onAct({ action: "approve", expected_version: field.version })}
                  aria-label={field.value_minor === null ? `Acknowledge ${label} is not in the document` : `Approve ${label}`}>
                  {field.value_minor === null ? "Ack" : "Approve"}
                </Button>
              )}
              <Button variant="ghost" disabled={busy} onClick={() => open("correct")} aria-label={`Correct ${label}`}
                aria-expanded={mode === "correct"}>Correct</Button>
            </>
          )}
          {!readOnly && reviewed && (
            <Button variant="ghost" disabled={busy} onClick={() => open("correct")} aria-label={`Edit ${label}`}
              aria-expanded={mode === "correct"}>Edit</Button>
          )}
        </td>
      </tr>
      {mode && (
        <tr className="bg-stone-100">
          <td colSpan={5} className="px-4 pb-3">
            <form onSubmit={submit} onKeyDown={(e) => e.key === "Escape" && setMode(null)}
              className="flex flex-wrap items-start gap-2"
              aria-label={mode === "accept" ? `Accept ${label} as printed` : `Correct ${label}`}>
              {mode === "correct" && (
                <div>
                  <label htmlFor={ids.value} className="sr-only">Corrected {label}{currency ? ` in ${currency}` : ""}</label>
                  <input id={ids.value} autoFocus inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)}
                    aria-invalid={!!err} aria-describedby={`${err ? ids.err : ""} ${ids.hint}`.trim()}
                    className={`num w-40 text-right ${INPUT_CLASS}`} placeholder="Amount" />
                </div>
              )}
              <div className="min-w-56 flex-1">
                <label htmlFor={ids.note} className="sr-only">
                  {mode === "accept" ? "Why the value is right as printed" : "Reason for the correction"}
                </label>
                <input id={ids.note} autoFocus={mode === "accept"} value={note} onChange={(e) => setNote(e.target.value)}
                  maxLength={1000}
                  placeholder={mode === "accept"
                    ? "Why is it right? e.g. one-off restructuring charge, confirmed in note 7"
                    : "Why? e.g. typo in statement, confirmed against p.2 subtotals"}
                  aria-invalid={!!err} aria-describedby={`${err ? ids.err : ""} ${ids.hint}`.trim()}
                  className={`w-full ${INPUT_CLASS}`} />
              </div>
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? "Saving…" : mode === "accept" ? "Accept value" : "Save correction"}
              </Button>
              <Button variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
            </form>
            <p id={ids.hint} className="mt-1 text-xs text-stone-500">{hint}</p>
            {err && <p id={ids.err} role="alert" className="mt-1 text-xs text-rose-700">{err}</p>}
          </td>
        </tr>
      )}
    </>
  );
}

function SourcePanel({ doc, field }: { doc: DocumentDetail["document"]; field: ExtractedField | undefined }) {
  const isPdf = doc.content_type === "application/pdf";
  const location = field
    ? [field.page && `page ${field.page}`, field.sheet && `sheet ${field.sheet}`, field.cell && `cell ${field.cell}`]
        .filter(Boolean).join(", ")
    : "";
  return (
    <div id={SOURCE_PANEL_ID}>
      <Panel title="Source evidence" actions={
        <a href={api.fileUrl(doc.id)} target="_blank" rel="noreferrer" className={`rounded text-xs underline ${FOCUS_RING}`}>
          Open original<span className="sr-only"> (opens in a new tab)</span>
        </a>
      }>
        {field && (
          <div className="border-b border-stone-200 px-4 py-3 text-sm" aria-live="polite">
            <div className="flex flex-wrap items-center gap-2">
              <b>{FIELD_LABELS[field.name] ?? field.name}</b>
              {location && <Badge>{location}</Badge>}
            </div>
            {field.evidence && field.evidence !== "verified" && (
              <p className="mt-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-900 ring-1 ring-amber-300">
                {EVIDENCE_PROBLEM[field.evidence]}
              </p>
            )}
            {field.quote ? (
              <blockquote className="num mt-2 break-words rounded bg-amber-50 px-3 py-2 text-xs ring-1 ring-amber-200">{field.quote}</blockquote>
            ) : (
              <p className="mt-2 text-xs text-stone-500">The extractor found no reliable evidence for this item, so it was left empty rather than guessed.</p>
            )}
          </div>
        )}
        {isPdf && (
          <iframe key={`${doc.id}-${field?.page ?? 1}`} title={`${doc.file_name}, page ${field?.page ?? 1}`}
            src={`${api.fileUrl(doc.id)}#page=${field?.page ?? 1}&view=FitH`}
            className="h-[420px] w-full rounded-b-md lg:h-[520px]" />
        )}
      </Panel>
    </div>
  );
}
