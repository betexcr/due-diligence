"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { ProjectCreate, type Project } from "@/lib/contracts";
import { Badge, Button, ErrorNote, Panel, usePolled } from "@/components/ui";

export default function ProjectsPage() {
  const { data: projects, error, refresh } = usePolled<Project[]>(api.projects, () => false);
  const [name, setName] = useState("Acquisition Target Alpha");
  const [company, setCompany] = useState("Alpha Manufacturing Inc.");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const parsed = ProjectCreate.safeParse({ name, company_name: company });
    if (!parsed.success) return setFormError(parsed.error.issues[0].message);
    setBusy(true);
    try {
      await api.createProject(parsed.data);
      setFormError(null);
      await refresh();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <Panel title="Due diligence projects">
        <ErrorNote error={error} />
        {projects && projects.length === 0 && (
          <p className="px-4 py-8 text-sm text-stone-500">No projects yet. Create one to start uploading documents.</p>
        )}
        <ul className="divide-y divide-stone-200">
          {projects?.map((p) => (
            <li key={p.id}>
              <Link href={`/projects/${p.id}`} className="flex items-center gap-4 px-4 py-3 hover:bg-stone-50">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{p.name}</div>
                  <div className="text-sm text-stone-500">{p.company_name}</div>
                </div>
                <span className="num text-sm text-stone-500">{p.document_count} docs</span>
                {p.open_issue_count > 0 ? (
                  <Badge tone="amber">{p.open_issue_count} open issues</Badge>
                ) : (
                  <Badge tone="green">clean</Badge>
                )}
              </Link>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="New project">
        <form onSubmit={create} className="space-y-3 p-4">
          <label className="block text-sm">
            <span className="text-stone-600">Project</span>
            <input value={name} onChange={(e) => setName(e.target.value)}
              className="mt-1 w-full rounded border border-stone-300 px-2 py-1.5" />
          </label>
          <label className="block text-sm">
            <span className="text-stone-600">Target company</span>
            <input value={company} onChange={(e) => setCompany(e.target.value)}
              className="mt-1 w-full rounded border border-stone-300 px-2 py-1.5" />
          </label>
          <ErrorNote error={formError} />
          <Button variant="primary" disabled={busy} className="w-full">Create project</Button>
        </form>
      </Panel>
    </div>
  );
}
