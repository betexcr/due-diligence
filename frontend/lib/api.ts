import { z } from "zod";
import * as C from "./contracts";

// Paths are same-origin: app/api/[...path]/route.ts forwards them to the backend and adds its token, so
// the token never reaches the browser and file/report links need no Authorization header.

export class ApiError extends Error {
  constructor(message: string, readonly status: number | null = null) {
    super(message);
  }

  /** 409: the resource changed since it was loaded, or the action isn't allowed in its current state. */
  get isConflict() {
    return this.status === 409;
  }
}

/** Human-readable message for any thrown value. */
export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// FastAPI sends a string for HTTPException and a list of {loc, msg} for 422 validation errors.
const ValidationDetail = z.array(z.object({ msg: z.string() }));

function describeError(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  const parsed = ValidationDetail.safeParse(detail);
  return parsed.success ? parsed.data.map((d) => d.msg.replace(/^Value error, /, "")).join("; ") : null;
}

async function request<T extends z.ZodType>(schema: T, path: string, init?: RequestInit): Promise<z.infer<T>> {
  let res: Response;
  try {
    res = await fetch(path, { cache: "no-store", ...init });
  } catch {
    throw new ApiError("Cannot reach the API. Check that the backend is running.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = describeError(body?.detail) ?? `HTTP ${res.status}`;
    throw new ApiError(detail, res.status);
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
  exclude: (documentId: string, body: z.infer<typeof C.DocumentExclusion>) =>
    request(C.Document, `/api/documents/${documentId}/exclude`, json(C.DocumentExclusion.parse(body))),
  restore: (documentId: string) =>
    request(C.Document, `/api/documents/${documentId}/restore`, { method: "POST" }),
  approveAll: (documentId: string) =>
    request(C.DocumentDetail, `/api/documents/${documentId}/approve-all`, { method: "POST" }),
  jobs: (status?: string) => request(z.array(C.Job), `/api/jobs${status ? `?status=${status}` : ""}`),
  attempts: (jobId: string) => request(z.array(C.JobAttempt), `/api/jobs/${jobId}/attempts`),
  requeue: (jobId: string) => request(C.Job, `/api/jobs/${jobId}/requeue`, { method: "POST" }),
  fileUrl: (documentId: string) => `/api/documents/${documentId}/file`,
  reportUrl: (projectId: string) => `/api/projects/${projectId}/report.html`,
};
