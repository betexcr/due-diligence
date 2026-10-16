"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { ReviewAction, type DocumentDetail, type ExtractedField, type ValidationIssue } from "@/lib/contracts";
import { Badge, Button, ErrorNote, FIELD_LABELS, money, Panel, StatusBadge, usePolled } from "@/components/ui";

export function ReviewView() {
  const { id } = useParams<{ id: string }>();
  const { data, error, refresh } = usePolled<DocumentDetail>(() => api.document(id), () => false);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!data) return error ? <ErrorNote error={error} /> : <p className="text-sm text-stone-500">Loading…</p>;
  const { document: doc, fields, issues, reviews, currency } = data;
  const docIssues = issues.filter((i) => i.document_id === doc.id);
  const projectIssues = issues.filter((i) => i.document_id === null);
  const issuesFor = (name: string) => docIssues.filter((i) => i.fields.includes(name) && !i.resolved);
  // Default to the field the first open issue is about (its first field is the one being checked).
  const focus = docIssues.find((i) => !i.resolved)?.fields[0];
  const current = fields.find((f) => f.id === selected) ?? fields.find((f) => f.name === focus) ?? fields[0];
  const pending = fields.filter((f) => f.status === "extracted").length;

  async function act(field: ExtractedField, action: ReviewAction) {
    setBusy(true);
    setActionError(null);
    try {
      await api.review(field.id, action);
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function approveClean() {
    setBusy(true);
    try {
      await api.approveAll(doc.id);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-stone-500">
            <Link href={`/projects/${doc.project_id}`} className="hover:underline">Project</Link> / review
          </p>
          <h1 className="text-xl font-semibold tracking-tight">{doc.file_name}</h1>
          <p className="text-sm text-stone-600">
            {data.company_name ?? "Unknown company"} · FY{doc.fiscal_year ?? "?"} · {currency ?? "currency not stated"}
            {data.reporting_period_end && ` · period end ${data.reporting_period_end}`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={doc.status} />
          <Button onClick={approveClean} disabled={busy || pending === 0}
            title="Approve every value that passed all checks. Flagged values still need individual review.">
            Approve {pending} clean value{pending === 1 ? "" : "s"}
          </Button>
        </div>
      </div>

      <ErrorNote error={actionError} />

      {docIssues.filter((i) => !i.resolved).map((i) => <IssueCard key={i.id} issue={i} currency={currency} />)}
      {projectIssues.filter((i) => !i.resolved).map((i) => <IssueCard key={i.id} issue={i} currency={currency} />)}

      <div className="grid gap-5 lg:grid-cols-[1.25fr_1fr]">
        <Panel title="Extracted values">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
              <tr>
                <th className="px-4 py-2 font-medium">Line item</th>
                <th className="px-2 text-right font-medium">Value</th>
                <th className="px-2 font-medium">Conf.</th>
                <th className="px-2 font-medium">Status</th>
                <th className="px-4 font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-200">
              {fields.map((f) => (
                <FieldRow key={f.id} field={f} currency={currency} active={current?.id === f.id}
                  issues={issuesFor(f.name)} busy={busy}
                  onSelect={() => setSelected(f.id)} onAct={(a) => act(f, a)} />
              ))}
            </tbody>
          </table>
        </Panel>

        <div className="space-y-5">
          <SourcePanel doc={doc} field={current} />
          <Panel title="Audit trail">
            <ul className="divide-y divide-stone-200 text-xs">
              {reviews.map((r) => {
                const f = fields.find((x) => x.id === r.field_id);
                return (
                  <li key={r.id} className="px-4 py-2">
                    <span className="text-stone-500">{new Date(r.created_at).toLocaleString()}</span>{" "}
                    <b>{r.reviewer}</b> {r.action === "correct" ? "corrected" : "approved"}{" "}
                    {FIELD_LABELS[f?.name ?? ""] ?? f?.name}
                    {r.action === "correct" && <span className="num"> {money(r.old_value)} → {money(r.new_value)}</span>}
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
  return (
    <div className={`rounded-md border-l-4 px-4 py-3 text-sm ${border}`}>
      <div className="flex items-center gap-2">
        <StatusBadge status={issue.severity} />
        <code className="text-xs text-stone-600">{issue.rule}</code>
      </div>
      <p className="mt-1">{issue.message}</p>
      {issue.expected !== null && issue.actual !== null && (
        <dl className="num mt-2 grid w-fit grid-cols-2 gap-x-6 text-xs">
          <dt className="text-stone-600">Extracted</dt><dd className="text-right">{money(issue.actual, currency)}</dd>
          <dt className="text-stone-600">Calculated / other source</dt><dd className="text-right">{money(issue.expected, currency)}</dd>
          <dt className="text-stone-600">Difference</dt><dd className="text-right">{money(issue.actual - issue.expected, currency)}</dd>
        </dl>
      )}
    </div>
  );
}

function FieldRow({ field, currency, active, issues, busy, onSelect, onAct }: {
  field: ExtractedField; currency: string | null; active: boolean; issues: ValidationIssue[]; busy: boolean;
  onSelect: () => void; onAct: (a: ReviewAction) => void;
}) {
  const [editing, setEditing] = useState(false);
  const suggested = issues.find((i) => i.expected !== null)?.expected;
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const reviewed = field.status === "approved" || field.status === "corrected";

  function startEdit() {
    setValue(String(suggested ?? field.value ?? ""));
    setNote("");
    setErr(null);
    setEditing(true);
    onSelect();
  }

  function submit() {
    const parsed = ReviewAction.safeParse({ action: "correct", value: Number(value.replace(/[,\s]/g, "")), note });
    if (!parsed.success) return setErr(parsed.error.issues[0].message);
    onAct(parsed.data);
    setEditing(false);
  }

  return (
    <>
      <tr onClick={onSelect} className={`cursor-pointer align-top ${active ? "bg-stone-100" : "hover:bg-stone-50"}`}>
        <td className="px-4 py-2.5">
          <div className="font-medium">{FIELD_LABELS[field.name] ?? field.name}</div>
          {field.status === "corrected" && (
            <div className="num text-xs text-stone-500">was {money(field.original_value)}</div>
          )}
        </td>
        <td className={`num px-2 py-2.5 text-right ${field.status === "flagged" ? "text-rose-700" : ""}`}>
          {money(field.value, currency)}
        </td>
        <td className="num px-2 py-2.5 text-xs text-stone-500">{field.value === null ? "—" : `${Math.round(field.confidence * 100)}%`}</td>
        <td className="px-2 py-2.5"><StatusBadge status={field.status} /></td>
        <td className="px-4 py-2 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
          {!reviewed && (
            <>
              <Button variant="ghost" disabled={busy} onClick={() => onAct({ action: "approve" })}
                title={field.value === null ? "Acknowledge that this item is not in the document" : "Value matches the source"}>
                {field.value === null ? "Ack" : "Approve"}
              </Button>
              <Button variant="ghost" disabled={busy} onClick={startEdit}>Correct</Button>
            </>
          )}
          {reviewed && <Button variant="ghost" disabled={busy} onClick={startEdit}>Edit</Button>}
        </td>
      </tr>
      {editing && (
        <tr className="bg-stone-100">
          <td colSpan={5} className="px-4 pb-3">
            <div className="flex flex-wrap items-start gap-2">
              <input autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="Value"
                className="num w-40 rounded border border-stone-300 bg-white px-2 py-1.5 text-right text-sm" />
              <input value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="Why? e.g. typo in statement, confirmed against p.2 subtotals"
                className="min-w-60 flex-1 rounded border border-stone-300 bg-white px-2 py-1.5 text-sm" />
              <Button variant="primary" disabled={busy} onClick={submit}>Save correction</Button>
              <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            </div>
            {suggested !== undefined && suggested !== null && (
              <p className="mt-1 text-xs text-stone-500">Pre-filled with the calculated value. Verify it against the source before saving.</p>
            )}
            {err && <p className="mt-1 text-xs text-rose-700">{err}</p>}
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
    <Panel title="Source evidence" actions={
      <a href={api.fileUrl(doc.id)} target="_blank" rel="noreferrer" className="text-xs underline">Open original</a>
    }>
      {field && (
        <div className="border-b border-stone-200 px-4 py-3 text-sm">
          <div className="flex items-center gap-2">
            <b>{FIELD_LABELS[field.name] ?? field.name}</b>
            {location && <Badge>{location}</Badge>}
          </div>
          {field.quote ? (
            <blockquote className="num mt-2 rounded bg-amber-50 px-3 py-2 text-xs ring-1 ring-amber-200">{field.quote}</blockquote>
          ) : (
            <p className="mt-2 text-xs text-stone-500">The extractor found no reliable evidence for this item, so it was left empty rather than guessed.</p>
          )}
        </div>
      )}
      {isPdf && (
        <iframe key={`${doc.id}-${field?.page ?? 1}`} title="Source document"
          src={`${api.fileUrl(doc.id)}#page=${field?.page ?? 1}&view=FitH`}
          className="h-[520px] w-full rounded-b-md" />
      )}
    </Panel>
  );
}
