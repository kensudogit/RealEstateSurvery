"""①〜⑤のオーケストレーション。

1 件の失敗で全体を止めない。1 通ごとに独立して成否を job_steps に記録し、
最後にまとめて報告する。段ごとに記録しておくと、Sheets のレート制限で
落ちただけのジョブが Claude API を再度呼ぶ、という無駄を避けられる。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.services import gmail, repository
from app.services import geo as geo_service
from app.services import pptx as pptx_service
from app.services import sheets as sheets_service
from app.services import extraction as extraction_service

logger = logging.getLogger(__name__)

TEMPLATE_KEYS = ("mysoku", "summary")


class StepFailed(RuntimeError):
    """この段で失敗したので、このメールの以降の処理は打ち切る。"""

    def __init__(self, step: str, cause: Exception):
        super().__init__(f"{step}: {cause}")
        self.step = step
        self.cause = cause


@contextmanager
def step(session: Session, job_id: int, mail_id: int | None, name: str) -> Iterator[dict]:
    """1 段分の実行。成否を job_steps に記録する。"""
    started = datetime.now(timezone.utc)
    detail: dict[str, Any] = {}
    try:
        yield detail
    except Exception as exc:
        logger.exception("段の実行に失敗 job=%s mail=%s step=%s", job_id, mail_id, name)
        repository.record_step(
            session, job_id, mail_id, name, "failed",
            {**detail, "error": str(exc)}, started,
        )
        session.commit()
        raise StepFailed(name, exc) from exc
    else:
        repository.record_step(session, job_id, mail_id, name, "done", detail, started)
        session.commit()


def process_mail(session: Session, job_id: int, mail_id: int,
                 render: bool = True, sync_sheet: bool = True) -> int:
    """1 通を ②→④→保存→③→⑤ まで通す。物件 ID を返す。"""
    with step(session, job_id, mail_id, "extract") as detail:
        result = extraction_service.run_for_mail(session, mail_id)
        detail["review_count"] = result["meta"].get("review_count")
        detail["usage"] = result["meta"].get("usage")

    with step(session, job_id, mail_id, "geocode"):
        result = geo_service.enrich(session, result)

    with step(session, job_id, mail_id, "persist") as detail:
        property_ = repository.upsert_property(session, mail_id, result)
        detail["property_id"] = property_.id

    payload = repository.to_payload(property_)

    # ③転記と⑤資料生成は互いに独立している。シートの認証が切れているだけで
    # 資料が作られない、という止まり方をしないよう、片方の失敗で他方を
    # 打ち切らない。両方の結果を記録したうえで、失敗があればジョブ全体は
    # 失敗として報告する。
    failures: list[StepFailed] = []

    if sync_sheet:
        try:
            with step(session, job_id, mail_id, "sync") as detail:
                detail["row"] = sheets_service.sync(payload)
        except StepFailed as exc:
            failures.append(exc)

    if render:
        try:
            _render_documents(session, job_id, mail_id, property_.id, payload)
        except StepFailed as exc:
            failures.append(exc)

    if failures:
        raise failures[0]

    return property_.id


def _render_documents(session: Session, job_id: int, mail_id: int | None,
                      property_id: int, payload: dict, mark_review: bool = True) -> list[str]:
    with step(session, job_id, mail_id, "render") as detail:
        rendered: list[str] = []
        for template_key in TEMPLATE_KEYS:
            try:
                output = pptx_service.render(payload, template_key, mark_review=mark_review)
            except pptx_service.RenderError as exc:
                # テンプレート未配置は運用初期によくある。他のテンプレートは通す。
                logger.warning("資料生成をスキップ (%s): %s", template_key, exc)
                continue
            repository.record_document(session, property_id, output)
            rendered.append(output["path"])
        detail["documents"] = rendered
    return rendered


def run_ingest(session: Session, job_id: int, limit: int | None = None,
               render: bool = True, sync_sheet: bool = True) -> dict[str, Any]:
    """①から通しで実行する。"""
    repository.start_job(session, job_id)
    session.commit()

    summary: dict[str, Any] = {"fetched": 0, "succeeded": [], "failed": []}

    try:
        with step(session, job_id, None, "fetch") as detail:
            mail_ids = gmail.fetch_new_mails(session, limit)
            detail["count"] = len(mail_ids)
            summary["fetched"] = len(mail_ids)
    except StepFailed as exc:
        repository.finish_job(session, job_id, str(exc))
        session.commit()
        return summary

    service = gmail.build_service()

    for mail_id in mail_ids:
        try:
            property_id = process_mail(session, job_id, mail_id, render, sync_sheet)
        except StepFailed as exc:
            summary["failed"].append({"mail_id": mail_id, "step": exc.step, "error": str(exc.cause)})
            _mark(session, service, mail_id, failed=True)
            continue
        summary["succeeded"].append({"mail_id": mail_id, "property_id": property_id})
        # 処理済みラベルはパイプライン全体が成功してからにする。
        # 途中で落ちたのに付けると、そのメールは二度と拾われない。
        _mark(session, service, mail_id, failed=False)

    repository.finish_job(session, job_id)
    session.commit()
    return summary


def _mark(session: Session, service, mail_id: int, *, failed: bool) -> None:
    from app.models import MailMessage

    mail = session.get(MailMessage, mail_id)
    if mail is None:
        return
    try:
        gmail.mark_done(service, mail.gmail_message_id, failed=failed)
    except Exception:  # ラベル付けの失敗で処理結果を失わない
        logger.warning("ラベル付けに失敗: %s", mail.gmail_message_id, exc_info=True)


def rerender(session: Session, job_id: int, property_id: int,
             mark_review: bool = True) -> list[str]:
    """レビュー完了後の再生成。シートも同時に更新する。"""
    repository.start_job(session, job_id)
    session.commit()

    property_ = repository.get_property(session, property_id)
    if property_ is None:
        repository.finish_job(session, job_id, f"物件が見つかりません: {property_id}")
        session.commit()
        return []

    payload = repository.to_payload(property_)
    paths: list[str] = []
    failures: list[StepFailed] = []

    # 転記と資料生成は独立。シートが失敗しても資料は作る。
    try:
        with step(session, job_id, property_.mail_id, "sync"):
            sheets_service.sync(payload)
    except StepFailed as exc:
        failures.append(exc)

    try:
        paths = _render_documents(
            session, job_id, property_.mail_id, property_id, payload, mark_review
        )
    except StepFailed as exc:
        failures.append(exc)

    repository.finish_job(session, job_id, str(failures[0]) if failures else None)
    session.commit()
    return paths
