"use client";

import { useEffect, useState } from "react";

import { api, subscribeJob } from "@/lib/api";
import { STEP_LABELS, type Job } from "@/lib/types";

/**
 * ジョブの進捗表示。
 *
 * 「3件目/12件 抽出中」まで見えると、待っている人の体感がまったく違う。
 * 段ごとに job_steps が積まれるので、それをそのまま並べる。
 */
export function JobProgress({ jobId }: { jobId: number }) {
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getJob(jobId).then((initial) => {
      if (!cancelled) setJob(initial);
    });
    const unsubscribe = subscribeJob(jobId, setJob);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [jobId]);

  if (!job) return <p className="muted">ジョブ #{jobId} を読み込み中…</p>;

  const failed = job.steps.filter((step) => step.status === "failed");

  return (
    <div>
      <div className="row">
        <strong>ジョブ #{job.id}</strong>
        <span className="badge">{statusLabel(job.status)}</span>
        {failed.length > 0 && (
          <span className="badge review">{failed.length} 件が失敗</span>
        )}
      </div>

      <div className="steps" style={{ marginTop: 8 }}>
        {job.steps.map((step) => (
          <div key={step.id} className={step.status === "failed" ? "step-failed" : "step-ok"}>
            {step.status === "failed" ? "×" : "✓"} {STEP_LABELS[step.step] ?? step.step}
            {step.mail_id != null && <span className="muted"> (mail #{step.mail_id})</span>}
            {step.detail?.error != null && <span> — {String(step.detail.error)}</span>}
            {step.detail?.review_count != null && (
              <span className="muted"> 要確認 {String(step.detail.review_count)} 項目</span>
            )}
          </div>
        ))}
        {job.steps.length === 0 && <span className="muted">開始待ち…</span>}
      </div>

      {job.error && <p className="step-failed">{job.error}</p>}
    </div>
  );
}

function statusLabel(status: Job["status"]): string {
  return { queued: "待機中", running: "実行中", done: "完了", failed: "失敗" }[status];
}
