"""Celery タスク。実処理は pipeline に委譲し、ここではセッション管理だけ行う。"""

from __future__ import annotations

import logging
from typing import Any

from app.core.db import session_scope
from app.services import pipeline
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="jobs.ingest")
def ingest(job_id: int, limit: int | None = None,
           render: bool = True, sync_sheet: bool = True) -> dict[str, Any]:
    with session_scope() as session:
        return pipeline.run_ingest(session, job_id, limit, render, sync_sheet)


@celery_app.task(name="jobs.reprocess")
def reprocess(job_id: int, mail_id: int) -> dict[str, Any]:
    """抽出からやり直す。プロンプトを変えた後の再評価に使う。"""
    from app.services import repository

    with session_scope() as session:
        repository.start_job(session, job_id)
        session.commit()
        try:
            property_id = pipeline.process_mail(session, job_id, mail_id)
        except pipeline.StepFailed as exc:
            repository.finish_job(session, job_id, str(exc))
            session.commit()
            return {"mail_id": mail_id, "error": str(exc), "step": exc.step}
        repository.finish_job(session, job_id)
        session.commit()
        return {"mail_id": mail_id, "property_id": property_id}


@celery_app.task(name="jobs.rerender")
def rerender(job_id: int, property_id: int, mark_review: bool = True) -> dict[str, Any]:
    with session_scope() as session:
        paths = pipeline.rerender(session, job_id, property_id, mark_review)
        return {"property_id": property_id, "documents": paths}
