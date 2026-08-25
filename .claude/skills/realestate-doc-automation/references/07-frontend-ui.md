# 07. 実行 UI と要確認レビュー画面

## 画面は 3 つで足りる

| 画面 | 役割 |
|---|---|
| ダッシュボード | 実行ボタン、直近のジョブの進捗、要確認件数 |
| 物件一覧 | 取り込んだ物件の一覧。要確認のものを上に |
| 物件詳細（レビュー） | 項目ごとに値・根拠・確信度を並べ、その場で直す。資料の再生成 |

一番重要なのは 3 つ目。ここの使い勝手がこのシステムの価値を決める。
実行ボタンだけ作って満足すると、結局スプレッドシートで手直しすることになり、
自動化した意味が半減する。

## 実行はジョブを作って即返す

```ts
// lib/api/jobs.ts
export async function startIngest(params: { label?: string; limit?: number }) {
  const res = await fetch(`${API_BASE}/jobs/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as { jobId: number };
}
```

進捗は SSE で流す。ポーリングでも動くが、1 通ごとに数十秒かかる処理を
2 秒間隔で叩き続けるのは無駄が多い。

```ts
export function subscribeJob(jobId: number, onEvent: (e: JobEvent) => void) {
  const source = new EventSource(`${API_BASE}/jobs/${jobId}/events`);
  source.onmessage = (event) => onEvent(JSON.parse(event.data) as JobEvent);
  return () => source.close();
}
```

サーバ側は `job_steps` の更新をそのまま流す。「3件目/12件 抽出中」まで
見えると、待っている人の体感がまったく違う。

## レビュー画面の作り

項目ごとに次を並べる。

```text
┌──────────────────────────────────────────────┐
│ 価格            [ 48,000,000 ]  円     ⚠要確認 │
│   根拠: 「販売価格 4,800万円」                  │
│   確信度: 0.62 / 検算: 記載利回りと年収が不一致  │
│   [原本を見る]                                 │
└──────────────────────────────────────────────┘
```

- **根拠（evidence）を必ず出す。** これが無いと、確認のたびに PDF を開いて
  全項目を目で追うことになる。根拠が出ていれば「見た文字列が合っているか」
  だけの判断で済み、確認が数倍速くなる。
- **原本をその場で開けるようにする。** 添付 PDF・画像をモーダルで表示する。
  画面を行き来させない。
- **要確認だけを絞り込むトグルを付ける。** 全 40 項目のうち直すのは
  2〜3 項目なのが普通。

型はバックエンドの Pydantic スキーマから生成する（`datamodel-code-generator` や
OpenAPI からの生成）。項目定義が `property_fields.json` にある以上、
TypeScript 側に手書きの型を置くと必ずずれる。

```ts
export type FieldEnvelope<T = string | number | null> = {
  value: T;
  confidence: number;
  evidence: string | null;
  needsReview: boolean;
  reviewReasons: string[];
};
```

## 修正の保存

修正は `property_revisions` に履歴として残し、`properties.fields` を更新する。
更新した項目は `needs_review = false`、`confidence = 1.0`、
`evidence = "手動修正"` にする。

履歴が残っていると、あとで「どの項目がよく直されているか」を集計できる。
それがそのままプロンプト改善の優先順位になる。

## 再生成

レビュー完了後に「資料を再生成」ボタン。スプレッドシートの該当行も
同時に更新する。生成物は `generated_documents` に追加し、過去版も残す。
上書きすると「前の資料を送ってしまった」ときに追跡できない。

## 権限

社内ツールでも、Google アカウントの OAuth ログイン（NextAuth など）は
最初から入れておく。誰が何を直したかが `property_revisions.edited_by` に
残らないと、履歴の価値が半減する。
