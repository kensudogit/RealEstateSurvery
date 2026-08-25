"""DB への読み書き。サービス層から SQLAlchemy の詳細を隠す。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    GeneratedDocument, Job, JobStep, MailMessage, Property, PropertyRevision,
)
from app.services.extraction import fields as field_defs
from app.services.extraction.normalize import apply_review_flags, summarize

# properties テーブルに直接持つ列。一覧・検索・並べ替えに使う。
PROMOTED_KEYS = (
    "property_name", "property_type", "deal_type", "address",
    "price", "monthly_rent", "exclusive_area_sqm", "built_year_month",
    "latitude", "longitude",
)


def _value(fields: dict, key: str) -> Any:
    envelope = fields.get(key)
    return envelope.get("value") if isinstance(envelope, dict) else None


def upsert_property(session: Session, mail_id: int, result: dict[str, Any]) -> Property:
    """抽出結果を保存する。同じメールを再処理したら上書きする。"""
    meta = result.get("meta") or {}
    fields = result["fields"]

    property_ = session.scalar(select(Property).where(Property.mail_id == mail_id))
    if property_ is None:
        property_ = Property(mail_id=mail_id, fields={})
        session.add(property_)

    property_.fields = fields
    property_.stations = result.get("stations") or []
    property_.images = result.get("images") or []
    property_.review_status = meta.get("review_status", "要確認")
    property_.extraction_model = meta.get("extraction_model")
    property_.prompt_version = meta.get("prompt_version")

    for key in PROMOTED_KEYS:
        setattr(property_, key, _value(fields, key))

    session.flush()
    return property_


def to_payload(property_: Property, mail: MailMessage | None = None) -> dict[str, Any]:
    """③⑤へ渡す共通の形。DB のモデルではなく素の dict でやり取りする。"""
    mail = mail or property_.mail
    return {
        "fields": property_.fields,
        "stations": property_.stations,
        "images": property_.images,
        "meta": {
            "property_id": property_.id,
            "review_status": property_.review_status,
            "review_fields": summarize(property_.fields)["review_fields"],
            "gmail_message_id": mail.gmail_message_id if mail else None,
            "email_subject": mail.subject if mail else None,
            "email_from": mail.from_address if mail else None,
            "received_at": mail.received_at.isoformat() if mail else None,
            "source_files": "、".join(
                a.filename for a in mail.attachments if not a.is_signature
            ) if mail else "",
        },
    }


def apply_edits(session: Session, property_id: int, edits: dict[str, Any],
                edited_by: str, reason: str | None = None) -> Property:
    """レビュー画面からの修正を反映する。

    手で直した値は確定値なので needs_review を下ろし、確信度を 1.0 にする。
    履歴を残すのは、よく直される項目がそのままプロンプト改善の
    優先順位になるから。
    """
    property_ = session.get(Property, property_id)
    if property_ is None:
        raise LookupError(f"物件が見つかりません: id={property_id}")

    specs = field_defs.field_specs()
    fields = dict(property_.fields)

    for key, new_value in edits.items():
        if key not in specs:
            raise ValueError(f"未知の項目です: {key}")
        old = fields.get(key) or {}
        session.add(PropertyRevision(
            property_id=property_id,
            field_key=key,
            old_value=old or None,
            new_value={"value": new_value},
            edited_by=edited_by,
            reason=reason,
        ))
        fields[key] = {
            "value": new_value,
            "confidence": 1.0,
            "evidence": "手動修正",
            "needs_review": False,
            "review_reasons": [],
        }

    # 修正で検算が通るようになることがあるため、全体を再判定する。
    property_.fields = apply_review_flags(fields)
    property_.review_status = summarize(property_.fields)["review_status"]
    for key in PROMOTED_KEYS:
        setattr(property_, key, _value(property_.fields, key))

    session.flush()
    return property_


def list_properties(session: Session, *, review_only: bool = False,
                    limit: int = 50, offset: int = 0) -> list[Property]:
    statement = (
        select(Property)
        .options(selectinload(Property.mail))
        .order_by(Property.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if review_only:
        statement = statement.where(Property.review_status == "要確認")
    return list(session.scalars(statement))


def get_property(session: Session, property_id: int) -> Property | None:
    return session.scalar(
        select(Property)
        .options(selectinload(Property.mail), selectinload(Property.documents))
        .where(Property.id == property_id)
    )


def record_document(session: Session, property_id: int, rendered: dict[str, Any]) -> None:
    """生成物は上書きせず追加する。過去版が消えると
    「前の資料を送ってしまった」ときに追跡できない。"""
    session.add(GeneratedDocument(
        property_id=property_id,
        template_key=rendered["template_key"],
        storage_path=rendered["path"],
        data_hash=rendered["data_hash"],
    ))


# --------------------------------------------------------------------------
# ジョブ
# --------------------------------------------------------------------------

def create_job(session: Session, kind: str, triggered_by: str,
               params: dict[str, Any] | None = None) -> Job:
    job = Job(kind=kind, triggered_by=triggered_by, params=params or {})
    session.add(job)
    session.flush()
    return job


def start_job(session: Session, job_id: int) -> None:
    job = session.get(Job, job_id)
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    session.flush()


def finish_job(session: Session, job_id: int, error: str | None = None) -> None:
    job = session.get(Job, job_id)
    job.status = "failed" if error else "done"
    job.error = error
    job.finished_at = datetime.now(timezone.utc)
    session.flush()


def get_job(session: Session, job_id: int) -> Job | None:
    return session.scalar(
        select(Job).options(selectinload(Job.steps)).where(Job.id == job_id)
    )


def list_jobs(session: Session, limit: int = 20) -> list[Job]:
    return list(session.scalars(
        select(Job).options(selectinload(Job.steps))
        .order_by(Job.created_at.desc()).limit(limit)
    ))


def record_step(session: Session, job_id: int, mail_id: int | None, step: str,
                status: str, detail: dict | None = None,
                started_at: datetime | None = None) -> JobStep:
    record = JobStep(
        job_id=job_id, mail_id=mail_id, step=step, status=status, detail=detail,
        started_at=started_at, finished_at=datetime.now(timezone.utc),
    )
    session.add(record)
    session.flush()
    return record
