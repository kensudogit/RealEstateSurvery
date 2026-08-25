"use client";

import { use, useEffect, useMemo, useState } from "react";

import { FieldRow, parseDraft, toDraft } from "@/components/FieldRow";
import { JobProgress } from "@/components/JobProgress";
import { api } from "@/lib/api";
import type { FieldSpec, PropertyDetail } from "@/lib/types";

/**
 * 要確認レビュー画面。このシステムの価値を決める画面。
 *
 * 実行ボタンだけ作って満足すると、結局スプレッドシートで手直しすることになり
 * 自動化した意味が半減する。ここで「根拠を見て直して再生成」まで完結させる。
 */
export default function PropertyReview({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const propertyId = Number(id);

  const [property, setProperty] = useState<PropertyDetail | null>(null);
  const [specs, setSpecs] = useState<FieldSpec[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [reviewOnly, setReviewOnly] = useState(true);
  const [editedBy, setEditedBy] = useState("");
  const [saving, setSaving] = useState(false);
  const [jobId, setJobId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<number | null>(null);

  useEffect(() => {
    Promise.all([api.getProperty(propertyId), api.fieldSpecs()])
      .then(([detail, fieldSpecs]) => {
        setProperty(detail);
        setSpecs(fieldSpecs);
        setDrafts(
          Object.fromEntries(fieldSpecs.map((spec) => [spec.key, toDraft(detail.fields[spec.key])])),
        );
      })
      .catch((err: Error) => setError(err.message));
  }, [propertyId]);

  const changed = useMemo(() => {
    if (!property) return {} as Record<string, string | number | null>;
    const result: Record<string, string | number | null> = {};
    for (const spec of specs) {
      const next = parseDraft(spec, drafts[spec.key] ?? "");
      const current = property.fields[spec.key]?.value ?? null;
      if (next !== current) result[spec.key] = next;
    }
    return result;
  }, [drafts, property, specs]);

  const visibleSpecs = useMemo(() => {
    if (!property) return [];
    // 全 40 項目のうち直すのは 2〜3 項目なのが普通。既定で要確認だけに絞る。
    return reviewOnly
      ? specs.filter((spec) => property.fields[spec.key]?.needs_review)
      : specs;
  }, [property, specs, reviewOnly]);

  async function save() {
    if (!editedBy.trim()) {
      setError("編集者名を入力してください（履歴に残ります）");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await api.editProperty(propertyId, changed, editedBy.trim());
      setProperty(updated);
      setDrafts(Object.fromEntries(specs.map((s) => [s.key, toDraft(updated.fields[s.key])])));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function regenerate() {
    setError(null);
    try {
      const { job_id } = await api.rerender(propertyId, property?.review_status === "要確認");
      setJobId(job_id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (error && !property) return <p className="step-failed">{error}</p>;
  if (!property) return <p className="muted">読み込み中…</p>;

  const reviewCount = specs.filter((s) => property.fields[s.key]?.needs_review).length;

  return (
    <main>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>
          {property.property_name ?? `(物件名なし) #${property.id}`}{" "}
          <span className={property.review_status === "要確認" ? "badge review" : "badge"}>
            {property.review_status}
          </span>
        </h2>
        <a href="/properties">一覧へ戻る</a>
      </div>

      <p className="muted">
        {property.email_from} / {property.email_subject}
        {property.received_at && ` / ${new Date(property.received_at).toLocaleString("ja-JP")}`}
      </p>

      <section className="panel">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          <label className="row">
            <input
              type="checkbox"
              checked={reviewOnly}
              onChange={(event) => setReviewOnly(event.target.checked)}
            />
            要確認のみ表示（{reviewCount} 項目）
          </label>
          <div className="row">
            <input
              placeholder="編集者名"
              value={editedBy}
              onChange={(event) => setEditedBy(event.target.value)}
              style={{ padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 4 }}
            />
            <button
              className="primary"
              onClick={save}
              disabled={saving || Object.keys(changed).length === 0}
            >
              {saving ? "保存中…" : `修正を保存（${Object.keys(changed).length}）`}
            </button>
          </div>
        </div>

        {error && <p className="step-failed">{error}</p>}

        {visibleSpecs.length === 0 ? (
          <p className="muted">要確認の項目はありません。</p>
        ) : (
          visibleSpecs.map((spec) => (
            <FieldRow
              key={spec.key}
              spec={spec}
              envelope={property.fields[spec.key]}
              draft={drafts[spec.key] ?? ""}
              onChange={(raw) => setDrafts((prev) => ({ ...prev, [spec.key]: raw }))}
            />
          ))
        )}
      </section>

      <section className="panel">
        <h3 style={{ marginTop: 0 }}>交通</h3>
        {property.stations.length === 0 ? (
          <p className="muted">未取得です。</p>
        ) : (
          <ul>
            {property.stations.map((station, index) => (
              <li key={index}>
                {station.line ?? <span className="muted">（沿線未取得）</span>} {station.station}駅
                {station.walk_minutes != null && ` 徒歩${station.walk_minutes}分`}
                {station.distance_m != null && (
                  <span className="muted"> （道路距離 {station.distance_m}m）</span>
                )}
                {station.source === "geo" && <span className="badge">自動取得</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <h3 style={{ marginTop: 0 }}>原本</h3>
        <p className="muted">
          値が合っているか迷ったらここで原本を開いて確認できます。
        </p>
        <div className="row">
          {property.attachments.map((path, index) => (
            <button key={index} onClick={() => setPreview(index)}>
              {path.split(/[\\/]/).pop()}
            </button>
          ))}
          {property.attachments.length === 0 && <span className="muted">添付はありません。</span>}
        </div>
        {preview !== null && (
          <iframe
            title="原本"
            src={api.sourceFileUrl(propertyId, preview)}
            style={{ width: "100%", height: 640, marginTop: 12, border: "1px solid var(--border)" }}
          />
        )}
      </section>

      <section className="panel">
        <h3 style={{ marginTop: 0 }}>資料</h3>
        <p className="muted">
          修正を保存したあとに再生成してください。スプレッドシートも同時に更新されます。
          過去版は残るので、送ってしまった資料を後から追跡できます。
        </p>
        <button onClick={regenerate}>資料を再生成</button>
        {jobId && (
          <div style={{ marginTop: 12 }}>
            <JobProgress jobId={jobId} />
          </div>
        )}
      </section>

      <p className="muted">
        抽出モデル: {property.extraction_model ?? "―"} / プロンプト{" "}
        {property.prompt_version ?? "―"}
      </p>
    </main>
  );
}
