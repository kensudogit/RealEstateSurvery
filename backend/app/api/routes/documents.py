"""生成した資料のダウンロード。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import GeneratedDocument

router = APIRouter(prefix="/documents", tags=["documents"])

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@router.get("/{document_id}")
def download(document_id: int, session: Session = Depends(get_db)) -> FileResponse:
    document = session.get(GeneratedDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="資料が見つかりません")

    path = Path(document.storage_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="ファイルが失われています")
    return FileResponse(path, media_type=PPTX_MIME, filename=path.name)


@router.get("/by-property/{property_id}", response_model=list[dict])
def list_for_property(property_id: int, session: Session = Depends(get_db)) -> list[dict]:
    """過去版も含めて返す。上書きしないので履歴が残る。"""
    documents = session.scalars(
        select(GeneratedDocument)
        .where(GeneratedDocument.property_id == property_id)
        .order_by(GeneratedDocument.generated_at.desc())
    )
    return [
        {
            "id": d.id,
            "template_key": d.template_key,
            "filename": Path(d.storage_path).name,
            "generated_at": d.generated_at.isoformat(),
        }
        for d in documents
    ]
