"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Job, JobAttempt } from "@/lib/contracts";
import { ago, retryIn, Button, ErrorNote, Panel, StatusBadge, usePolled } from "@/components/ui";

export default function OpsPage() {
  const { data: jobs, error, refresh } = usePolled<Job[]>(() => api.jobs(), () => true, 2000);
  const [open, setOpen] = useState<string | null>(null);
  const [attempts, setAttempts] = useState<JobAttempt[]>([]);

  async function toggle(id: string) {
    if (open === id) return setOpen(null);
    setOpen(id);
    setAttempts(await api.attempts(id));
  }

  async function requeue(id: string) {
    await api.requeue(id);
    await refresh();
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

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(["queued", "running", "succeeded", "dead"] as const).map((s) => (
          <div key={s} className="rounded-md border border-stone-300 bg-white px-4 py-3">
            <div className="text-xs uppercase tracking-wide text-stone-500">{s === "dead" ? "dead-lettered" : s}</div>
            <div className="num text-2xl">{counts[s] ?? 0}</div>
          </div>
        ))}
      </div>

      <Panel title={`Dead-letter queue (${dead.length})`}>
        <ul className="divide-y divide-stone-200 text-sm">
          {dead.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{j.file_name}</div>
                <div className="text-xs text-rose-700">{j.error_kind} · {j.last_error}</div>
              </div>
              <Button onClick={() => requeue(j.id)} title="Retry with a fresh attempt budget, e.g. after fixing the root cause">Requeue</Button>
            </li>
          ))}
          {dead.length === 0 && <li className="px-4 py-4 text-stone-500">Empty.</li>}
        </ul>
      </Panel>

      <Panel title="All jobs">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
            <tr><th className="px-4 py-2 font-medium">Document</th><th className="px-2 font-medium">Status</th>
              <th className="px-2 font-medium">Attempts</th><th className="px-2 font-medium">Updated</th>
              <th className="px-4 font-medium">Last error</th></tr>
          </thead>
          <tbody className="divide-y divide-stone-200">
            {jobs?.map((j) => (
              <JobRow key={j.id} job={j} open={open === j.id} attempts={attempts} onToggle={() => toggle(j.id)} />
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function JobRow({ job, open, attempts, onToggle }: { job: Job; open: boolean; attempts: JobAttempt[]; onToggle: () => void }) {
  return (
    <>
      <tr onClick={onToggle} className="cursor-pointer align-top hover:bg-stone-50">
        <td className="px-4 py-2.5 font-medium">{job.file_name}</td>
        <td className="px-2 py-2.5"><StatusBadge status={job.status} /></td>
        <td className="num px-2 py-2.5">{job.attempts}/{job.max_attempts}</td>
        <td className="px-2 py-2.5 text-xs text-stone-500">
          {job.status === "queued" && job.attempts > 0 ? `retry ${retryIn(job.next_run_at)}` : ago(job.updated_at)}
        </td>
        <td className="max-w-md truncate px-4 py-2.5 text-xs text-stone-600">{job.last_error ?? ""}</td>
      </tr>
      {open && (
        <tr className="bg-stone-50">
          <td colSpan={5} className="px-4 pb-3">
            <table className="num w-full text-xs">
              <thead className="text-left text-stone-500">
                <tr><th className="py-1 font-medium">#</th><th className="font-medium">Started</th><th className="font-medium">Outcome</th>
                  <th className="font-medium">Backoff before next</th><th className="font-medium">Error</th></tr>
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
          </td>
        </tr>
      )}
    </>
  );
}
