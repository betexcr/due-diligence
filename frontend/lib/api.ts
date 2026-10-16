import { z } from "zod";
import * as C from "./contracts";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {}

async function request<T extends z.ZodType>(schema: T, path: string, init?: RequestInit): Promise<z.infer<T>> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
    throw new ApiError(detail);
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    console.error("Contract violation", path, parsed.error);
    throw new ApiError(`API response for ${path} violated the contract: ${z.prettifyError(parsed.error)}`);
  }
  return parsed.data;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request(C.Health, "/api/health"),
  projects: () => request(z.array(C.Project), "/api/projects"),
  project: (id: string) => request(C.ProjectDetail, `/api/projects/${id}`),
  createProject: (body: z.infer<typeof C.ProjectCreate>) =>
    request(C.Project, "/api/projects", json(C.ProjectCreate.parse(body))),
  upload: (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request(C.UploadResult, `/api/projects/${projectId}/documents`, { method: "POST", body: form });
  },
  document: (id: string) => request(C.DocumentDetail, `/api/documents/${id}`),
  review: (fieldId: string, action: C.ReviewAction) =>
    request(C.ExtractedField, `/api/fields/${fieldId}/review`, json(C.ReviewAction.parse(action))),
  approveAll: (documentId: string) =>
    request(C.DocumentDetail, `/api/documents/${documentId}/approve-all`, { method: "POST" }),
  jobs: (status?: string) => request(z.array(C.Job), `/api/jobs${status ? `?status=${status}` : ""}`),
  attempts: (jobId: string) => request(z.array(C.JobAttempt), `/api/jobs/${jobId}/attempts`),
  requeue: (jobId: string) => request(C.Job, `/api/jobs/${jobId}/requeue`, { method: "POST" }),
  fileUrl: (documentId: string) => `${API_URL}/api/documents/${documentId}/file`,
  reportUrl: (projectId: string) => `${API_URL}/api/projects/${projectId}/report.html`,
};
