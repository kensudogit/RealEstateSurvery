"""ジョブの起動と監視。

実処理は Celery に投げて即座に job_id を返す。1 通あたり数十秒かかるので、
HTTP リクエストの中で回すとタイムアウトする。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.api.deps import dispatch
from app.core.db import SessionLocal, get_db
from app.schemas import IngestRequest, JobAccepted, JobOut
from app.services import repository
from app.workers import tasks

router = APIRouter(prefix="/jobs", tags=["jobs"])

# 進捗は SSE で流す。1 通ごとに数十秒かかる処理を 2 秒間隔で
# ポーリングし続けるのは無駄が多い。
POLL_INTERVAL_SECONDS = 2.0


@router.post("/ingest", response_model=JobAccepted, status_code=202)
def start_ingest(request: IngestRequest, session: Session = Depends(get_db)) -> JobAccepted:
    job = repository.create_job(
        session, kind="ingest", triggered_by="ui", params=request.model_dump()
    )
    session.commit()
    dispatch(session, job, tasks.ingest,
             job.id, request.limit, request.render, request.sync_sheet)
    return JobAccepted(job_id=job.id)


@router.post("/reprocess/{mail_id}", response_model=JobAccepted, status_code=202)
def start_reprocess(mail_id: int, session: Session = Depends(get_db)) -> JobAccepted:
    """抽出からやり直す。プロンプトを変えた後の再評価に使う。"""
    job = repository.create_job(
        session, kind="reprocess", triggered_by="ui", params={"mail_id": mail_id}
    )
    session.commit()
    dispatch(session, job, tasks.reprocess, job.id, mail_id)
    return JobAccepted(job_id=job.id)


@router.get("", response_model=list[JobOut])
def list_jobs(limit: int = 20, session: Session = Depends(get_db)) -> list[JobOut]:
    return [JobOut.model_validate(job) for job in repository.list_jobs(session, limit)]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, session: Session = Depends(get_db)) -> JobOut:
    job = repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return JobOut.model_validate(job)


@router.get("/{job_id}/events")
async def job_events(job_id: int) -> EventSourceResponse:
    """job_steps の更新をそのまま流す。

    「3件目/12件 抽出中」まで見えると、待っている人の体感がまったく違う。
    """

    async def stream() -> AsyncIterator[dict]:
        seen_steps = 0
        while True:
            with SessionLocal() as session:
                job = repository.get_job(session, job_id)
                if job is None:
                    yield {"event": "error", "data": json.dumps({"detail": "not found"})}
                    return

                payload = JobOut.model_validate(job)
                if len(payload.steps) != seen_steps or payload.status in ("done", "failed"):
                    seen_steps = len(payload.steps)
                    yield {"event": "progress", "data": payload.model_dump_json()}

                if payload.status in ("done", "failed"):
                    return

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return EventSourceResponse(stream())
