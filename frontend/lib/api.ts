import type {
  AppConfig, FieldSpec, Job, PropertyDetail, PropertySummary,
} from "./types";

/**
 * API の接続先。
 *
 * 既定は同一オリジンの /api/backend で、Next のルートハンドラが
 * サーバ側の API_BASE_URL へ中継する。NEXT_PUBLIC_ の変数はビルド時に
 * 焼き込まれるため、設定を忘れると localhost が埋まったまま公開されて
 * 「動いているのに全部 Failed to fetch」という壊れ方をする。同一オリジンに
 * しておけば、その事故が起きず CORS の設定も要らない。
 *
 * バックエンドを直接叩きたい場合だけ NEXT_PUBLIC_API_BASE_URL を設定する。
 * その場合はバックエンドの CORS_ORIGINS にこの画面の URL を追加すること。
 */
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api/backend";

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

  // 200 なのに本文が空、という状態が起こりうる（中継の途中で本文が
  // 落ちるなど）。そのまま JSON.parse すると
  // 「Unexpected end of JSON input」になり、どの通信が原因か分からない。
  // どこで何が起きたかを名指しする。
  const body = await response.text();
  if (body.trim() === "") {
    const via = response.headers.get("x-proxy-target");
    throw new Error(
      `${path} が空の応答を返しました（HTTP ${response.status}` +
        (via ? `, 中継先 ${via}` : ", 中継ヘッダなし") + "）",
    );
  }

  try {
    return JSON.parse(body) as T;
  } catch {
    throw new Error(`${path} の応答を解釈できません: ${body.slice(0, 120)}`);
  }
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

  /**
   * 原本のプレビュー URL。
   *
   * version を付けるのは、ブラウザが Content-Disposition ごと
   * レスポンスをキャッシュするため。ダウンロード扱いだった頃の
   * キャッシュが残っていると、サーバ側を直しても iframe が
   * 真っ白のままになる。画面を開くたびに新しい値を渡す。
   */
  sourceFileUrl: (propertyId: number, index: number, version?: number | string) =>
    `${API_BASE}/properties/${propertyId}/source/${index}` +
    (version === undefined ? "" : `?v=${encodeURIComponent(String(version))}`),

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
