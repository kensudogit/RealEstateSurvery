"""SQLAlchemy モデル。DDL の正は migrations/ 側だが、定義はここに揃える。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, CHAR, DateTime, Float, ForeignKey, Index, Integer,
    Numeric, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MailMessage(Base):
    __tablename__ = "mail_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 冪等性の要。同じメールを二度取り込ませないための一意キー。
    gmail_message_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    gmail_thread_id: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    raw_headers: Mapped[dict | None] = mapped_column(JSONB)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="mail", cascade="all, delete-orphan"
    )
    property: Mapped["Property | None"] = relationship(back_populates="mail", uselist=False)


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (UniqueConstraint("mail_id", "sha256", name="uq_attachment_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mail_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 同じ図面が転送で何度も添付される。ハッシュで重複抽出を止める。
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_id: Mapped[str | None] = mapped_column(Text)
    is_signature: Mapped[bool] = mapped_column(default=False, nullable=False)

    mail: Mapped[MailMessage] = relationship(back_populates="attachments")


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("mail_id", name="uq_property_mail"),
        Index("ix_properties_review", "review_status", "created_at"),
        # fields JSONB を条件に絞る検索（例: 特定項目が要確認の物件）向け。
        Index("ix_properties_fields", "fields",
              postgresql_using="gin", postgresql_ops={"fields": "jsonb_path_ops"}),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mail_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mail_messages.id"), nullable=False
    )

    # 一覧・検索・並べ替えに使う確定値。JSONB だけにすると一覧画面が作れない。
    property_name: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    deal_type: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    price: Mapped[int | None] = mapped_column(BigInteger)
    monthly_rent: Mapped[int | None] = mapped_column(Integer)
    exclusive_area_sqm: Mapped[float | None] = mapped_column(Numeric(10, 2))
    built_year_month: Mapped[str | None] = mapped_column(CHAR(7))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # 確信度・根拠・要確認フラグを含む全項目。カラムだけにすると根拠が消える。
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    images: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="要確認")
    extraction_model: Mapped[str | None] = mapped_column(Text)
    # 「先週より精度が落ちた」を追えるように必ず残す。
    prompt_version: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    mail: Mapped[MailMessage] = relationship(back_populates="property")
    revisions: Mapped[list["PropertyRevision"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    documents: Mapped[list["GeneratedDocument"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )


class PropertyRevision(Base):
    """人が直した履歴。よく直される項目が、そのままプロンプト改善の優先順位になる。"""

    __tablename__ = "property_revisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    field_key: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    edited_by: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)

    property: Mapped[Property] = relationship(back_populates="revisions")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)          # ingest/reprocess/render
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)  # ui/schedule/api
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    steps: Mapped[list["JobStep"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStep.id"
    )


class JobStep(Base):
    """段ごとの成否。失敗した段から再開できるようにするための記録。"""

    __tablename__ = "job_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    mail_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("mail_messages.id"))
    step: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="steps")


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    # データが変わっていないのに再生成した、を検出するため。
    data_hash: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped[Property] = relationship(back_populates="documents")


class GeoCache(Base):
    """住所は繰り返し同じものが来る。API 課金を抑えるためのキャッシュ。"""

    __tablename__ = "geo_cache"

    address_key: Mapped[str] = mapped_column(Text, primary_key=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    formatted: Mapped[str] = mapped_column(Text, nullable=False)
    accuracy: Mapped[str | None] = mapped_column(Text)
    address_display: Mapped[str | None] = mapped_column(Text)
    stations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SignatureImage(Base):
    """差出人ごとに繰り返し添付される署名画像・バナーのハッシュ。

    出現回数が閾値を超えたものを署名と見なして抽出対象から外す。
    これが無いと会社ロゴを毎回 Claude API に投げることになる。
    """

    __tablename__ = "signature_images"

    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    from_domain: Mapped[str] = mapped_column(Text, primary_key=True)
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Base", "MailMessage", "Attachment", "Property", "PropertyRevision",
    "Job", "JobStep", "GeneratedDocument", "GeoCache", "SignatureImage",
]
