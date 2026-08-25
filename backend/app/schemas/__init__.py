"""API の入出力スキーマ。フロントエンドの型はここから生成する。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldEnvelope(BaseModel):
    """1 項目分の抽出結果。

    value だけでなく確信度と根拠を持つのがこのシステムの中核。
    evidence があるとレビュー画面で「モデルはここを見てこう判断した」を
    そのまま出せる。
    """

    value: Any = None
    confidence: float = 0.0
    evidence: str | None = None
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class Station(BaseModel):
    line: str | None = None
    station: str | None = None
    walk_minutes: int | None = None
    distance_m: int | None = None
    bus_minutes: int | None = None
    source: Literal["extracted", "geo"] = "extracted"


class PropertyImage(BaseModel):
    file: str
    role: str
    caption: str | None = None
    storage_path: str | None = None


class FieldSpec(BaseModel):
    """項目定義。レビュー画面のラベル・型・選択肢はここから引く。"""

    key: str
    label: str
    type: str
    unit: str | None = None
    required: bool = False
    enum: list[str] | None = None
    note: str | None = None


class PropertySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    property_name: str | None
    property_type: str | None
    deal_type: str | None
    address: str | None
    price: int | None
    monthly_rent: int | None
    review_status: str
    created_at: datetime


class PropertyDetail(PropertySummary):
    fields: dict[str, FieldEnvelope]
    stations: list[Station]
    images: list[PropertyImage]
    extraction_model: str | None = None
    prompt_version: str | None = None
    email_subject: str | None = None
    email_from: str | None = None
    received_at: datetime | None = None
    attachments: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)


class PropertyEditRequest(BaseModel):
    edits: dict[str, Any] = Field(
        ..., description="項目key → 修正後の値。needs_review は自動で下ろす"
    )
    edited_by: str
    reason: str | None = None


class JobStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mail_id: int | None
    step: str
    status: str
    detail: dict | None
    started_at: datetime | None
    finished_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    status: str
    triggered_by: str
    params: dict
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[JobStepOut] = Field(default_factory=list)


class IngestRequest(BaseModel):
    limit: int | None = Field(None, ge=1, le=200, description="取り込む最大件数")
    render: bool = True
    sync_sheet: bool = True


class RerenderRequest(BaseModel):
    mark_review: bool = Field(
        True, description="要確認項目に ※要確認 を付ける。確認完了後は false"
    )


class JobAccepted(BaseModel):
    job_id: int
