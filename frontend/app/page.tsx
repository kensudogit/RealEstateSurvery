"use client";

import { useEffect, useState } from "react";

import { JobProgress } from "@/components/JobProgress";
import { api } from "@/lib/api";
import type { AppConfig, Job, PropertySummary } from "@/lib/types";

export default function Dashboard() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [needsReview, setNeedsReview] = useState<PropertySummary[]>([]);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.config(),
      api.listJobs(5),
      api.listProperties({ reviewOnly: true, limit: 100 }),
    ])
      .then(([cfg, jobList, review]) => {
        setConfig(cfg);
        setJobs(jobList);
        setNeedsReview(review);
        const running = jobList.find((job) => job.status === "running" || job.status === "queued");
        if (running) setActiveJobId(running.id);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  async function run() {
    setStarting(true);
    setError(null);
    try {
      const { job_id } = await api.startIngest({ limit: 50 });
      setActiveJobId(job_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <main>
      <section className="panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <h2 style={{ margin: "0 0 4px" }}>メールを取り込む</h2>
            <p className="muted" style={{ margin: 0 }}>
              ラベル「{config?.gmail_label ?? "…"}」の未処理メールを取り込み、
              抽出・転記・資料生成までを実行します。処理済みのメールは再取り込みされません。
            </p>
          </div>
          <button className="primary" onClick={run} disabled={starting}>
            {starting ? "起動中…" : "実行"}
          </button>
        </div>

        {error && <p className="step-failed">{error}</p>}
        {activeJobId && (
          <div style={{ marginTop: 16, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            <JobProgress jobId={activeJobId} />
          </div>
        )}
      </section>

      <section className="panel">
        <h2 style={{ marginTop: 0 }}>
          要確認 <span className="badge review">{needsReview.length} 件</span>
        </h2>
        <p className="muted">
          AI が読み取れなかった項目、または検算に引っかかった項目があります。
          確認せずに資料を客先へ出さないでください。
        </p>
        {needsReview.length === 0 ? (
          <p className="muted">確認待ちはありません。</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>物件名</th>
                <th>所在地</th>
                <th>取込日時</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {needsReview.slice(0, 10).map((item) => (
                <tr key={item.id}>
                  <td>{item.property_name ?? <span className="muted">―</span>}</td>
                  <td>{item.address ?? <span className="muted">―</span>}</td>
                  <td className="muted">{new Date(item.created_at).toLocaleString("ja-JP")}</td>
                  <td>
                    <a href={`/properties/${item.id}`}>確認する</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2 style={{ marginTop: 0 }}>最近のジョブ</h2>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>種類</th>
              <th>状態</th>
              <th>開始</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  <button onClick={() => setActiveJobId(job.id)}>#{job.id}</button>
                </td>
                <td>{job.kind}</td>
                <td>{job.status}</td>
                <td className="muted">
                  {job.started_at ? new Date(job.started_at).toLocaleString("ja-JP") : "―"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {config && (
        <p className="muted">
          抽出モデル: {config.extraction_model} / プロンプト {config.prompt_version} /
          確信度の閾値 {config.review_threshold}
        </p>
      )}
    </main>
  );
}
