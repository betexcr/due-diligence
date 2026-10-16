"use client";

import { Fragment, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Job, JobAttempt } from "@/lib/contracts";
import { ago, retryIn, Button, ErrorNote, FOCUS_RING, Panel, StatusBadge, TableScroll, usePolled } from "@/components/ui";

export default function OpsPage() {
  const { data: jobs, error, refresh } = usePolled<Job[]>(() => api.jobs(), () => true, 2000);
  const [open, setOpen] = useState<string | null>(null);
  const [attempts, setAttempts] = useState<JobAttempt[] | null>(null);
  const [requeueing, setRequeueing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function toggle(id: string) {
    if (open === id) return setOpen(null);
    setOpen(id);
    setAttempts(null);
    try {
      setAttempts(await api.attempts(id));
    } catch (e) {
      setActionError(errorMessage(e));
    }
  }

  async function requeue(id: string) {
    if (requeueing) return;
    setRequeueing(id);
    setActionError(null);
    try {
      await api.requeue(id);
      await refresh();
    } catch (e) {
      setActionError(errorMessage(e));
    } finally {
      setRequeueing(null);
    }
  }

  const dead = jobs?.filter((j) => j.status === "dead") ?? [];
  const counts = (jobs ?? []).reduce<Record<string, number>>((acc, j) => ({ ...acc, [j.status]: (acc[j.status] ?? 0) + 1 }), {});

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Pipeline operations</h1>
        <p className="text-sm text-stone-600">
          Jobs are leased, retried with capped exponential backoff and full jitter, killed on timeout, and
          moved to the dead-letter queue on permanent errors or when retries run out.
        </p>
      </div>
      <ErrorNote error={error} />
      <ErrorNote error={actionError} onDismiss={() => setActionError(null)} />

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(["queued", "running", "succeeded", "dead"] as const).map((s) => (
          <div key={s} className="rounded-md border border-stone-300 bg-white px-4 py-3">
            <dt className="text-xs uppercase tracking-wide text-stone-500">{s === "dead" ? "dead-lettered" : s}</dt>
            <dd className="num text-2xl">{jobs ? counts[s] ?? 0 : "…"}</dd>
          </div>
        ))}
      </dl>

      <Panel title={`Dead-letter queue (${dead.length})`}>
        <ul className="divide-y divide-stone-200 text-sm">
          {dead.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{j.file_name}</div>
                <div className="break-words text-xs text-rose-700">{j.error_kind} · {j.last_error}</div>
              </div>
              <Button onClick={() => requeue(j.id)} disabled={requeueing !== null}
                aria-label={`Requeue ${j.file_name}`}
                title="Retry with a fresh attempt budget, e.g. after fixing the root cause">
                {requeueing === j.id ? "Requeueing…" : "Requeue"}
              </Button>
            </li>
          ))}
          {dead.length === 0 && <li className="px-4 py-4 text-stone-500">Empty.</li>}
        </ul>
      </Panel>

      <Panel title="All jobs">
        {jobs && jobs.length === 0 ? (
          <p className="px-4 py-6 text-sm text-stone-500">No jobs yet. Uploading a document creates one.</p>
        ) : (
          <TableScroll label="All jobs">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">Document</th>
                  <th scope="col" className="px-2 font-medium">Status</th>
                  <th scope="col" className="px-2 font-medium">Attempts</th>
                  <th scope="col" className="px-2 font-medium">Updated</th>
                  <th scope="col" className="px-4 font-medium">Last error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-200">
                {jobs?.map((j) => (
                  <Fragment key={j.id}>
                    <tr className="align-top">
                      <th scope="row" className="px-4 py-2 text-left font-normal">
                        <button type="button" onClick={() => toggle(j.id)} aria-expanded={open === j.id}
                          aria-controls={`attempts-${j.id}`}
                          className={`rounded text-left font-medium hover:underline ${FOCUS_RING}`}>
                          {j.file_name}
                          <span className="sr-only">: {open === j.id ? "hide" : "show"} attempt history</span>
                        </button>
                      </th>
                      <td className="px-2 py-2.5"><StatusBadge status={j.status} /></td>
                      <td className="num px-2 py-2.5">{j.attempts}/{j.max_attempts}</td>
                      <td className="px-2 py-2.5 text-xs text-stone-500">
                        {j.status === "queued" && j.attempts > 0 ? `retry ${retryIn(j.next_run_at)}` : ago(j.updated_at)}
                      </td>
                      <td className="max-w-md truncate px-4 py-2.5 text-xs text-stone-600" title={j.last_error ?? undefined}>
                        {j.last_error ?? ""}
                      </td>
                    </tr>
                    {open === j.id && (
                      <tr id={`attempts-${j.id}`} className="bg-stone-50">
                        <td colSpan={5} className="px-4 pb-3">
                          {attempts === null ? (
                            <p className="py-1 text-xs text-stone-500" role="status">Loading attempts…</p>
                          ) : (
                            <table className="num w-full text-xs">
                              <caption className="sr-only">Attempt history for {j.file_name}</caption>
                              <thead className="text-left text-stone-500">
                                <tr>
                                  <th scope="col" className="py-1 font-medium">#</th>
                                  <th scope="col" className="font-medium">Started</th>
                                  <th scope="col" className="font-medium">Outcome</th>
                                  <th scope="col" className="font-medium">Backoff before next</th>
                                  <th scope="col" className="font-medium">Error</th>
                                </tr>
                              </thead>
                              <tbody>
                                {attempts.map((a) => (
                                  <tr key={a.id}>
                                    <td className="py-0.5">{a.attempt}</td>
                                    <td>{new Date(a.started_at).toLocaleTimeString()}</td>
                                    <td>{a.outcome ?? "running"}</td>
                                    <td>{a.delay_seconds !== null ? `${a.delay_seconds.toFixed(2)}s` : "—"}</td>
                                    <td className="font-sans text-stone-600">{a.error ?? ""}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Panel>
    </div>
  );
}
