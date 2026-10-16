"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useId, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { DocumentExclusion, type Document, type Job, type ProjectDetail } from "@/lib/contracts";
import {
  retryIn, Badge, Button, ErrorNote, FOCUS_RING, INPUT_CLASS, Panel, StatusBadge, TableScroll, usePolled,
} from "@/components/ui";

const ACTIVE = new Set(["uploaded", "processing"]);

type UploadOutcome = { name: string; ok: boolean; message: string };

export function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const { data, error, refresh, restart } = usePolled<ProjectDetail>(
    () => api.project(id),
    (d) => d.documents.some((x) => ACTIVE.has(x.status)) || d.jobs.some((j) => j.status === "queued" || j.status === "running"),
  );
  const [uploads, setUploads] = useState<UploadOutcome[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0 || uploading) return;
    setUploading(true);
    const outcomes: UploadOutcome[] = [];
    for (const file of list) {
      try {
        const r = await api.upload(id, file);
        outcomes.push({
          name: file.name, ok: true,
          message: r.duplicate ? "already uploaded; not reprocessed" : "queued for processing",
        });
      } catch (e) {
        outcomes.push({ name: file.name, ok: false, message: `rejected: ${errorMessage(e)}` });
      }
    }
    setUploads((prev) => [...outcomes, ...prev].slice(0, 8));
    setUploading(false);
    if (input.current) input.current.value = "";
    restart(); // new work is in flight: resume polling
  }

  if (!data) return error ? <ErrorNote error={error} /> : <p className="text-sm text-stone-500" role="status">Loading…</p>;
  const { project, documents, issues, jobs } = data;
  const openIssues = issues.filter((i) => !i.resolved);
  const jobByDoc = new Map(jobs.map((j) => [j.document_id, j]));
  const active = documents.filter((d) => !d.excluded);
  const excluded = documents.filter((d) => d.excluded);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm text-stone-500">
            <Link href="/" className={`rounded hover:underline ${FOCUS_RING}`}>Projects</Link> /
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
          <p className="text-stone-600">{project.company_name}</p>
        </div>
        <div className="flex items-center gap-3">
          {data.report_ready ? <Badge tone="green">report ready</Badge> : <Badge tone="amber">draft</Badge>}
          <a href={api.reportUrl(id)} target="_blank" rel="noreferrer"
            className={`rounded px-3 py-1.5 text-sm font-medium ${FOCUS_RING} ${data.report_ready
              ? "bg-stone-900 text-white hover:bg-stone-700"
              : "bg-white text-stone-800 ring-1 ring-inset ring-stone-300 hover:bg-stone-50"}`}>
            Open report<span className="sr-only"> (opens in a new tab)</span>
          </a>
        </div>
      </div>

      <ErrorNote error={error} />
      <ErrorNote error={actionError} onDismiss={() => setActionError(null)} />

      <div>
        <button type="button" disabled={uploading}
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); uploadFiles(e.dataTransfer.files); }}
          className={`w-full rounded-md border-2 border-dashed px-4 py-6 text-center text-sm transition-colors ${FOCUS_RING} ${dragging
            ? "border-stone-900 bg-white" : "border-stone-300 text-stone-600 hover:border-stone-500"}`}>
          {uploading ? "Uploading…" : "Upload documents: drop files here, or press to choose"}
          <span className="block text-xs text-stone-500">
            PDF · XLSX · TXT · CSV, up to 25 MB. Re-uploading the same file is a no-op.
          </span>
        </button>
        <input ref={input} type="file" multiple hidden accept=".pdf,.xlsx,.txt,.md,.csv"
          onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
        <ul aria-live="polite" className="mt-2 space-y-0.5 text-xs">
          {uploads.map((u, i) => (
            <li key={`${u.name}-${i}`} className={u.ok ? "text-stone-600" : "text-rose-700"}>
              <span className="font-medium">{u.name}</span>: {u.message}
            </li>
          ))}
        </ul>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Panel title={`Documents (${active.length})`}>
          {active.length === 0 ? (
            <p className="px-4 py-6 text-sm text-stone-500">No documents yet. Upload financial statements to begin.</p>
          ) : (
            <TableScroll label="Documents">
              <table className="w-full min-w-[540px] text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-medium">File</th>
                    <th scope="col" className="px-2 font-medium">FY</th>
                    <th scope="col" className="px-2 font-medium">Status</th>
                    <th scope="col" className="px-2 font-medium">Pipeline</th>
                    <th scope="col" className="px-4 font-medium"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-200">
                  {active.map((d) => (
                    <DocumentRow key={d.id} doc={d} job={jobByDoc.get(d.id)}
                      onExcluded={async () => { setActionError(null); await refresh(); }}
                      onError={setActionError} />
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </Panel>

        <Panel title={`Validation issues (${openIssues.length} open)`}>
          <ul className="divide-y divide-stone-200">
            {issues.map((i) => {
              const doc = documents.find((d) => d.id === i.document_id);
              return (
                <li key={i.id} className={`px-4 py-3 text-sm ${i.resolved ? "text-stone-500" : ""}`}>
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <StatusBadge status={i.severity} />
                    <code className="text-xs text-stone-500">{i.rule}</code>
                    {i.resolved && <Badge tone="green">accepted</Badge>}
                  </div>
                  <p>{i.message}</p>
                  {doc && (
                    <Link href={`/documents/${doc.id}`} className={`rounded text-xs text-stone-600 underline ${FOCUS_RING}`}>
                      Review in {doc.file_name}
                    </Link>
                  )}
                </li>
              );
            })}
            {issues.length === 0 && (
              <li className="px-4 py-6 text-sm text-stone-500">
                {active.some((d) => ACTIVE.has(d.status)) ? "Checks run as documents finish processing." : "No issues found."}
              </li>
            )}
          </ul>
        </Panel>
      </div>

      {excluded.length > 0 && (
        <Panel title={`Excluded documents (${excluded.length})`}>
          <ul className="divide-y divide-stone-200 text-sm">
            {excluded.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <Link href={`/documents/${d.id}`} className={`rounded font-medium hover:underline ${FOCUS_RING}`}>{d.file_name}</Link>
                  <p className="text-xs text-stone-600">Reason: {d.excluded_reason}</p>
                </div>
                <RestoreButton doc={d} onDone={refresh} onError={setActionError} />
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

function DocumentRow({ doc, job, onExcluded, onError }: {
  doc: Document; job: Job | undefined; onExcluded: () => Promise<void>; onError: (e: string) => void;
}) {
  const [excluding, setExcluding] = useState(false);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ids = { reason: useId(), err: useId() };
  const inFlight = ACTIVE.has(doc.status);
  const reviewable = !inFlight && doc.status !== "failed";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = DocumentExclusion.safeParse({ reason });
    if (!parsed.success) return setErr(parsed.error.issues[0].message);
    setBusy(true);
    try {
      await api.exclude(doc.id, parsed.data);
      setExcluding(false);
      await onExcluded();
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <tr className="align-top">
        <th scope="row" className="px-4 py-2.5 text-left font-normal">
          {reviewable ? (
            <Link href={`/documents/${doc.id}`} className={`rounded font-medium underline-offset-2 hover:underline ${FOCUS_RING}`}>
              {doc.file_name}
            </Link>
          ) : (
            <span className="font-medium">{doc.file_name}</span>
          )}
          {doc.error && <p className="mt-1 text-xs text-rose-700">{doc.error}</p>}
        </th>
        <td className="num px-2 py-2.5">{doc.fiscal_year ?? "—"}</td>
        <td className="px-2 py-2.5"><StatusBadge status={doc.status} /></td>
        <td className="px-2 py-2.5 text-xs text-stone-600">
          {job && (
            <>
              <StatusBadge status={job.status} /> <span className="num">attempt {job.attempts}/{job.max_attempts}</span>
              {job.status === "queued" && job.attempts > 0 && (
                <p className="mt-1">Retry {retryIn(job.next_run_at)}: {job.last_error}</p>
              )}
              {job.status === "dead" && (
                <p className="mt-1">
                  <Link href="/ops" className={`rounded underline ${FOCUS_RING}`}>In dead-letter queue</Link>
                </p>
              )}
            </>
          )}
        </td>
        <td className="px-4 py-2 text-right">
          {!inFlight && (
            <Button variant="ghost" onClick={() => { setExcluding(!excluding); setErr(null); }}
              aria-expanded={excluding} aria-label={`Exclude ${doc.file_name}`}>
              Exclude
            </Button>
          )}
        </td>
      </tr>
      {excluding && (
        <tr className="bg-stone-100">
          <td colSpan={5} className="px-4 pb-3 pt-2">
            <form onSubmit={submit} onKeyDown={(e) => e.key === "Escape" && setExcluding(false)}
              className="flex flex-wrap items-start gap-2" aria-label={`Exclude ${doc.file_name}`}>
              <label htmlFor={ids.reason} className="w-full text-xs text-stone-600">
                Why exclude this document? The reason appears in the report.
              </label>
              <input id={ids.reason} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500}
                placeholder="e.g. corrupt duplicate; client re-sent the statements"
                aria-invalid={!!err} aria-describedby={err ? ids.err : undefined}
                className={`min-w-56 flex-1 ${INPUT_CLASS}`} />
              <Button type="submit" variant="danger" disabled={busy}>{busy ? "Excluding…" : "Exclude"}</Button>
              <Button variant="ghost" onClick={() => setExcluding(false)}>Cancel</Button>
            </form>
            {err && <p id={ids.err} role="alert" className="mt-1 text-xs text-rose-700">{err}</p>}
          </td>
        </tr>
      )}
    </>
  );
}

function RestoreButton({ doc, onDone, onError }: {
  doc: Document; onDone: () => Promise<unknown>; onError: (e: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button disabled={busy} aria-label={`Restore ${doc.file_name}`} onClick={async () => {
      setBusy(true);
      try {
        await api.restore(doc.id);
        await onDone();
      } catch (e) {
        onError(errorMessage(e));
      } finally {
        setBusy(false);
      }
    }}>
      {busy ? "Restoring…" : "Restore"}
    </Button>
  );
}
