"use client";

import type { FieldEnvelope, FieldSpec } from "@/lib/types";

/**
 * レビュー画面の 1 項目。
 *
 * 根拠（evidence）を必ず出すのがこの画面の肝。これが無いと、確認のたびに
 * PDF を開いて全項目を目で追うことになり、自動化した意味が半減する。
 * 根拠が出ていれば「見た文字列が合っているか」だけの判断で済む。
 */
export function FieldRow({
  spec,
  envelope,
  draft,
  onChange,
}: {
  spec: FieldSpec;
  envelope: FieldEnvelope | undefined;
  draft: string;
  onChange: (raw: string) => void;
}) {
  const needsReview = envelope?.needs_review ?? false;

  return (
    <div className={needsReview ? "field review" : "field"}>
      <div className="head">
        <span className="label">
          {spec.label}
          {spec.required && <span style={{ color: "#b91c1c" }}> *</span>}
        </span>

        {spec.type === "enum" && spec.enum ? (
          <select value={draft} onChange={(event) => onChange(event.target.value)}>
            <option value="">（未設定）</option>
            {spec.enum.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        ) : (
          <input
            type={spec.type === "integer" || spec.type === "number" ? "number" : "text"}
            value={draft}
            placeholder={spec.type === "date_ym" ? "1993-03" : "（未取得）"}
            onChange={(event) => onChange(event.target.value)}
          />
        )}

        {spec.unit && <span className="muted">{spec.unit}</span>}
        {needsReview && <span className="badge review">要確認</span>}
      </div>

      {envelope?.evidence && (
        <div className="evidence">
          根拠:「{envelope.evidence}」
          {envelope.confidence > 0 && <> / 確信度 {envelope.confidence.toFixed(2)}</>}
        </div>
      )}
      {envelope?.review_reasons?.length ? (
        <div className="reasons">{envelope.review_reasons.join(" / ")}</div>
      ) : null}
    </div>
  );
}

/** 入力欄の文字列を、項目定義の型に合わせた値へ戻す */
export function parseDraft(spec: FieldSpec, raw: string): string | number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (spec.type === "integer") {
    const parsed = Number.parseInt(trimmed.replace(/,/g, ""), 10);
    return Number.isNaN(parsed) ? null : parsed;
  }
  if (spec.type === "number") {
    const parsed = Number.parseFloat(trimmed.replace(/,/g, ""));
    return Number.isNaN(parsed) ? null : parsed;
  }
  return trimmed;
}

export function toDraft(envelope: FieldEnvelope | undefined): string {
  const value = envelope?.value;
  return value === null || value === undefined ? "" : String(value);
}
