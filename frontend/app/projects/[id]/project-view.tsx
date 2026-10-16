"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/contracts";
import { retryIn, Badge, Button, ErrorNote, Panel, StatusBadge, usePolled } from "@/components/ui";

const ACTIVE = new Set(["uploaded", "processing"]);

export function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const { data, error, restart } = usePolled<ProjectDetail>(
    () => api.project(id),
    (d) => d.documents.some((x) => ACTIVE.has(x.status)) || d.jobs.some((j) => j.status === "queued" || j.status === "running"),
  );
  const [uploadLog, setUploadLog] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function uploadFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      try {
        const r = await api.upload(id, file);
        setUploadLog((l) => [`${file.name}: ${r.duplicate ? "duplicate of existing document, not reprocessed" : "queued"}`, ...l]);
      } catch (e) {
        setUploadLog((l) => [`${file.name}: rejected (${e instanceof Error ? e.message : e})`, ...l]);
      }
    }
    restart(); // new work is in flight: resume polling
  }

  if (!data) return error ? <ErrorNote error={error} /> : <p className="text-sm text-stone-500">Loading…</p>;
  const { project, documents, issues, jobs } = data;
  const openIssues = issues.filter((i) => !i.resolved);
  const jobByDoc = new Map(jobs.map((j) => [j.document_id, j]));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-stone-500"><Link href="/" className="hover:underline">Projects</Link> /</p>
          <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
          <p className="text-stone-600">{project.company_name}</p>
        </div>
        <div className="flex items-center gap-3">
          {data.report_ready ? <Badge tone="green">report ready</Badge> : <Badge tone="amber">draft</Badge>}
          <a href={api.reportUrl(id)} target="_blank" rel="noreferrer">
            <Button variant={data.report_ready ? "primary" : "secondary"}>Open report</Button>
          </a>
        </div>
      </div>

      <ErrorNote error={error} />

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); uploadFiles(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        className={`cursor-pointer rounded-md border-2 border-dashed px-4 py-6 text-center text-sm transition-colors ${dragging ? "border-stone-900 bg-white" : "border-stone-300 text-stone-500 hover:border-stone-500"}`}
      >
        Drop financial statements, management accounts or supporting documents here, or click to choose.
        <span className="block text-xs text-stone-400">PDF · XLSX · TXT · CSV, up to 25 MB. Re-uploading the same file is a no-op.</span>
        <input ref={input} type="file" multiple hidden accept=".pdf,.xlsx,.txt,.md,.csv"
          onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
      </div>
      {uploadLog.length > 0 && (
        <ul className="space-y-0.5 text-xs text-stone-600">{uploadLog.slice(0, 6).map((l, i) => <li key={i}>{l}</li>)}</ul>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Panel title={`Documents (${documents.length})`}>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
              <tr><th className="px-4 py-2 font-medium">File</th><th className="px-2 font-medium">FY</th>
                <th className="px-2 font-medium">Status</th><th className="px-4 font-medium">Pipeline</th></tr>
            </thead>
            <tbody className="divide-y divide-stone-200">
              {documents.map((d) => {
                const job = jobByDoc.get(d.id);
                return (
                  <tr key={d.id} className="align-top">
                    <td className="px-4 py-2.5">
                      {ACTIVE.has(d.status) || d.status === "failed" ? (
                        <span className="font-medium">{d.file_name}</span>
                      ) : (
                        <Link href={`/documents/${d.id}`} className="font-medium underline-offset-2 hover:underline">{d.file_name}</Link>
                      )}
                      {d.error && <p className="mt-1 text-xs text-rose-700">{d.error}</p>}
                    </td>
                    <td className="num px-2 py-2.5">{d.fiscal_year ?? "—"}</td>
                    <td className="px-2 py-2.5"><StatusBadge status={d.status} /></td>
                    <td className="px-4 py-2.5 text-xs text-stone-600">
                      {job && (
                        <>
                          <StatusBadge status={job.status} />{" "}
                          <span className="num">attempt {job.attempts}/{job.max_attempts}</span>
                          {job.status === "queued" && job.attempts > 0 && (
                            <p className="mt-1">retry {retryIn(job.next_run_at)} · {job.last_error}</p>
                          )}
                          {job.status === "dead" && <p className="mt-1"><Link href="/ops" className="underline">in dead-letter queue</Link></p>}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
              {documents.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-6 text-stone-500">No documents yet.</td></tr>
              )}
            </tbody>
          </table>
        </Panel>

        <Panel title={`Validation issues (${openIssues.length} open)`}>
          <ul className="divide-y divide-stone-200">
            {issues.map((i) => {
              const doc = documents.find((d) => d.id === i.document_id);
              return (
                <li key={i.id} className={`px-4 py-3 text-sm ${i.resolved ? "opacity-60" : ""}`}>
                  <div className="mb-1 flex items-center gap-2">
                    <StatusBadge status={i.severity} />
                    <code className="text-xs text-stone-500">{i.rule}</code>
                    {i.resolved && <Badge tone="green">accepted</Badge>}
                  </div>
                  <p>{i.message}</p>
                  {doc && (
                    <Link href={`/documents/${doc.id}`} className="text-xs text-stone-500 underline">Review in {doc.file_name}</Link>
                  )}
                </li>
              );
            })}
            {issues.length === 0 && <li className="px-4 py-6 text-sm text-stone-500">No issues.</li>}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
