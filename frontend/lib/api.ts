import type {
  AppConfig, FieldSpec, Job, PropertyDetail, PropertySummary,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${detail}`);
  }
  return (await response.json()) as T;
}

export const api = {
  config: () => request<AppConfig>("/config"),

  fieldSpecs: () => request<FieldSpec[]>("/properties/field-specs"),

  listProperties: (params: { reviewOnly?: boolean; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.reviewOnly) query.set("review_only", "true");
    if (params.limit) query.set("limit", String(params.limit));
    return request<PropertySummary[]>(`/properties?${query}`);
  },

  getProperty: (id: number) => request<PropertyDetail>(`/properties/${id}`),

  /** レビュー画面からの修正。needs_review はサーバ側で下ろされる */
  editProperty: (id: number, edits: Record<string, unknown>, editedBy: string, reason?: string) =>
    request<PropertyDetail>(`/properties/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ edits, edited_by: editedBy, reason }),
    }),

  rerender: (id: number, markReview: boolean) =>
    request<{ job_id: number }>(`/properties/${id}/rerender`, {
      method: "POST",
      body: JSON.stringify({ mark_review: markReview }),
    }),

  /** 実処理はワーカーに投げられ、すぐ job_id が返る */
  startIngest: (body: { limit?: number; render?: boolean; sync_sheet?: boolean } = {}) =>
    request<{ job_id: number }>("/jobs/ingest", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listJobs: (limit = 10) => request<Job[]>(`/jobs?limit=${limit}`),

  getJob: (id: number) => request<Job>(`/jobs/${id}`),

  sourceFileUrl: (propertyId: number, index: number) =>
    `${API_BASE}/properties/${propertyId}/source/${index}`,

  documentsUrl: (propertyId: number) => `${API_BASE}/documents/by-property/${propertyId}`,
};

/**
 * ジョブ進捗の購読。
 *
 * 1 通あたり数十秒かかる処理を 2 秒間隔で叩き続けるのは無駄なので、
 * ポーリングではなく SSE で受ける。
 */
export function subscribeJob(jobId: number, onEvent: (job: Job) => void): () => void {
  const source = new EventSource(`${API_BASE}/jobs/${jobId}/events`);
  source.addEventListener("progress", (event) => {
    onEvent(JSON.parse((event as MessageEvent).data) as Job);
  });
  source.onerror = () => source.close();
  return () => source.close();
}
