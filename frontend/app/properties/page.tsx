"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { PropertySummary } from "@/lib/types";

export default function PropertyList() {
  const [items, setItems] = useState<PropertySummary[]>([]);
  const [reviewOnly, setReviewOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listProperties({ reviewOnly, limit: 100 })
      .then(setItems)
      .catch((err: Error) => setError(err.message));
  }, [reviewOnly]);

  return (
    <main>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>物件一覧</h2>
        <label className="row">
          <input
            type="checkbox"
            checked={reviewOnly}
            onChange={(event) => setReviewOnly(event.target.checked)}
          />
          要確認のみ表示
        </label>
      </div>

      {error && <p className="step-failed">{error}</p>}

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>状態</th>
              <th>物件名</th>
              <th>種別</th>
              <th>所在地</th>
              <th>価格 / 賃料</th>
              <th>取込日時</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>
                  <span className={item.review_status === "要確認" ? "badge review" : "badge"}>
                    {item.review_status}
                  </span>
                </td>
                <td>
                  <a href={`/properties/${item.id}`}>
                    {item.property_name ?? `(物件名なし) #${item.id}`}
                  </a>
                </td>
                <td>{item.property_type ?? <span className="muted">―</span>}</td>
                <td>{item.address ?? <span className="muted">―</span>}</td>
                <td>{formatPrice(item)}</td>
                <td className="muted">{new Date(item.created_at).toLocaleString("ja-JP")}</td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  該当する物件はありません。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}

function formatPrice(item: PropertySummary) {
  if (item.price != null) return formatYen(item.price);
  if (item.monthly_rent != null) return `${item.monthly_rent.toLocaleString("ja-JP")}円/月`;
  return <span className="muted">―</span>;
}

/**
 * 1億円を「10,000万円」と書く資料は無いので、億が立つときは億で表す。
 * 万で割り切れない端数がある物件は丸めずに円で出す（資料に載る数字なので
 * 見やすさより正確さを優先する）。backend/app/services/pptx の format_yen と対。
 */
function formatYen(value: number): string {
  const jp = (n: number) => n.toLocaleString("ja-JP");
  if (value % 10_000 !== 0 || value < 10_000_000) return `${jp(value)}円`;
  if (value >= 100_000_000) {
    const oku = Math.floor(value / 100_000_000);
    const man = Math.floor((value % 100_000_000) / 10_000);
    return man ? `${jp(oku)}億${jp(man)}万円` : `${jp(oku)}億円`;
  }
  return `${jp(value / 10_000)}万円`;
}
