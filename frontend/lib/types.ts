/**
 * バックエンドの Pydantic スキーマに対応する型。
 *
 * 項目そのもの（物件名・価格…）はここに列挙しない。config/property_fields.json が
 * 唯一の定義で、UI は /properties/field-specs から引く。TypeScript 側に項目を
 * 書き写すと、項目を足したときに必ずずれる。
 */

export type FieldEnvelope = {
  /** 読み取れなかった項目は null。0 や空文字では埋めない */
  value: string | number | boolean | null;
  confidence: number;
  /** 原文の該当文字列。人が検算するための材料 */
  evidence: string | null;
  needs_review: boolean;
  review_reasons: string[];
};

export type Station = {
  line: string | null;
  station: string | null;
  walk_minutes: number | null;
  distance_m: number | null;
  bus_minutes: number | null;
  source: "extracted" | "geo";
};

export type PropertyImage = {
  file: string;
  role: string;
  caption: string | null;
  storage_path: string | null;
};

export type FieldSpec = {
  key: string;
  label: string;
  type: "string" | "integer" | "number" | "boolean" | "enum" | "date_ym";
  unit: string | null;
  required: boolean;
  enum: string[] | null;
  note: string | null;
};

export type PropertySummary = {
  id: number;
  property_name: string | null;
  property_type: string | null;
  deal_type: string | null;
  address: string | null;
  price: number | null;
  monthly_rent: number | null;
  review_status: string;
  created_at: string;
};

export type PropertyDetail = PropertySummary & {
  fields: Record<string, FieldEnvelope>;
  stations: Station[];
  images: PropertyImage[];
  extraction_model: string | null;
  prompt_version: string | null;
  email_subject: string | null;
  email_from: string | null;
  received_at: string | null;
  attachments: string[];
  documents: string[];
};

export type JobStep = {
  id: number;
  mail_id: number | null;
  step: string;
  status: string;
  detail: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
};

export type Job = {
  id: number;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  triggered_by: string;
  params: Record<string, unknown>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  steps: JobStep[];
};

export type AppConfig = {
  gmail_label: string;
  extraction_model: string;
  prompt_version: string;
  review_threshold: number;
  templates: string[];
};

/** 段の日本語名。ジョブ進捗の表示に使う */
export const STEP_LABELS: Record<string, string> = {
  fetch: "メール取得",
  extract: "情報抽出",
  geocode: "最寄駅の補完",
  persist: "保存",
  sync: "シート転記",
  render: "資料生成",
};
