"""物件の一覧・詳細・修正。レビュー画面の裏側。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy.orm import Session

from app.api.deps import dispatch
from app.core.db import get_db
from app.models import Property
from app.schemas import (
    FieldSpec, JobAccepted, PropertyDetail, PropertyEditRequest,
    PropertySummary, RerenderRequest,
)
from app.services import repository
from app.services.extraction import fields as field_defs
from app.workers import tasks

router = APIRouter(prefix="/properties", tags=["properties"])


def _detail(property_: Property) -> PropertyDetail:
    mail = property_.mail
    return PropertyDetail(
        id=property_.id,
        property_name=property_.property_name,
        property_type=property_.property_type,
        deal_type=property_.deal_type,
        address=property_.address,
        price=property_.price,
        monthly_rent=property_.monthly_rent,
        review_status=property_.review_status,
        created_at=property_.created_at,
        fields=property_.fields,
        stations=property_.stations,
        images=property_.images,
        extraction_model=property_.extraction_model,
        prompt_version=property_.prompt_version,
        email_subject=mail.subject if mail else None,
        email_from=mail.from_address if mail else None,
        received_at=mail.received_at if mail else None,
        attachments=[a.storage_path for a in mail.attachments if not a.is_signature]
        if mail else [],
        documents=[d.storage_path for d in property_.documents],
    )


@router.get("/field-specs", response_model=list[FieldSpec])
def get_field_specs() -> list[FieldSpec]:
    """項目定義。レビュー画面のラベル・型・選択肢はこれを使う。

    TypeScript 側に項目を書き写すと必ずずれるので、UI は必ずここから引く。
    """
    return [FieldSpec.model_validate(spec) for spec in field_defs.load_definitions()["fields"]]


@router.get("", response_model=list[PropertySummary])
def list_properties(review_only: bool = False, limit: int = 50, offset: int = 0,
                    session: Session = Depends(get_db)) -> list[PropertySummary]:
    return [
        PropertySummary.model_validate(item)
        for item in repository.list_properties(
            session, review_only=review_only, limit=limit, offset=offset
        )
    ]


@router.get("/{property_id}", response_model=PropertyDetail)
def get_property(property_id: int, session: Session = Depends(get_db)) -> PropertyDetail:
    property_ = repository.get_property(session, property_id)
    if property_ is None:
        raise HTTPException(status_code=404, detail="物件が見つかりません")
    return _detail(property_)


@router.patch("/{property_id}", response_model=PropertyDetail)
def edit_property(property_id: int, request: PropertyEditRequest,
                  session: Session = Depends(get_db)) -> PropertyDetail:
    try:
        property_ = repository.apply_edits(
            session, property_id, request.edits, request.edited_by, request.reason
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _detail(repository.get_property(session, property_id))


@router.post("/{property_id}/rerender", response_model=JobAccepted, status_code=202)
def rerender(property_id: int, request: RerenderRequest,
             session: Session = Depends(get_db)) -> JobAccepted:
    """レビュー完了後の再生成。シートも同時に更新される。"""
    if repository.get_property(session, property_id) is None:
        raise HTTPException(status_code=404, detail="物件が見つかりません")

    job = repository.create_job(
        session, kind="render", triggered_by="ui", params={"property_id": property_id}
    )
    session.commit()
    dispatch(session, job, tasks.rerender, job.id, property_id, request.mark_review)
    return JobAccepted(job_id=job.id)


@router.get("/{property_id}/source/{index}")
def get_source_file(property_id: int, index: int,
                    session: Session = Depends(get_db)) -> FileResponse:
    """原本の添付を返す。

    レビュー画面から原本をその場で開けないと、確認のたびに画面を
    行き来することになって手が止まる。
    """
    property_ = repository.get_property(session, property_id)
    if property_ is None or property_.mail is None:
        raise HTTPException(status_code=404, detail="物件が見つかりません")

    attachments = [a for a in property_.mail.attachments if not a.is_signature]
    if not 0 <= index < len(attachments):
        raise HTTPException(status_code=404, detail="添付が見つかりません")

    path = Path(attachments[index].storage_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="ファイルが失われています")
    return FileResponse(path, media_type=attachments[index].mime_type, filename=path.name)
