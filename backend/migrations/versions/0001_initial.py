"""初期スキーマ

Revision ID: 0001
Revises:
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        # 冪等性の要。同じメールを二度取り込ませない。
        sa.Column("gmail_message_id", sa.Text(), nullable=False, unique=True),
        sa.Column("gmail_thread_id", sa.Text()),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text()),
        sa.Column("from_address", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_text", sa.Text()),
        sa.Column("body_html", sa.Text()),
        sa.Column("raw_headers", postgresql.JSONB()),
        sa.Column("ingested_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "attachments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("mail_id", sa.BigInteger(),
                  sa.ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        # 転送メールでは同じ図面が何度も添付される。ハッシュで重複抽出を止める。
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text()),
        sa.Column("is_signature", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("mail_id", "sha256", name="uq_attachment_hash"),
    )

    op.create_table(
        "properties",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("mail_id", sa.BigInteger(),
                  sa.ForeignKey("mail_messages.id"), nullable=False),
        # 一覧・検索・並べ替えに使う確定値
        sa.Column("property_name", sa.Text()),
        sa.Column("property_type", sa.Text()),
        sa.Column("deal_type", sa.Text()),
        sa.Column("address", sa.Text()),
        sa.Column("price", sa.BigInteger()),
        sa.Column("monthly_rent", sa.Integer()),
        sa.Column("exclusive_area_sqm", sa.Numeric(10, 2)),
        sa.Column("built_year_month", sa.CHAR(7)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        # 確信度・根拠・要確認フラグを含む全項目
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("stations", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("images", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="要確認"),
        sa.Column("extraction_model", sa.Text()),
        sa.Column("prompt_version", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("mail_id", name="uq_property_mail"),
    )
    op.create_index("ix_properties_review", "properties", ["review_status", "created_at"])
    op.create_index(
        "ix_properties_fields", "properties", ["fields"],
        postgresql_using="gin", postgresql_ops={"fields": "jsonb_path_ops"},
    )

    op.create_table(
        "property_revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("property_id", sa.BigInteger(),
                  sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_key", sa.Text(), nullable=False),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB()),
        sa.Column("edited_by", sa.Text(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("reason", sa.Text()),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "job_steps",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("job_id", sa.BigInteger(),
                  sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mail_id", sa.BigInteger(), sa.ForeignKey("mail_messages.id")),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "generated_documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("property_id", sa.BigInteger(),
                  sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        # データが変わっていないのに再生成した、を検出するため
        sa.Column("data_hash", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "geo_cache",
        sa.Column("address_key", sa.Text(), primary_key=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("formatted", sa.Text(), nullable=False),
        sa.Column("accuracy", sa.Text()),
        sa.Column("address_display", sa.Text()),
        sa.Column("stations", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "signature_images",
        sa.Column("sha256", sa.Text(), primary_key=True),
        sa.Column("from_domain", sa.Text(), primary_key=True),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("signature_images")
    op.drop_table("geo_cache")
    op.drop_table("generated_documents")
    op.drop_table("job_steps")
    op.drop_table("jobs")
    op.drop_table("property_revisions")
    op.drop_index("ix_properties_fields", table_name="properties")
    op.drop_index("ix_properties_review", table_name="properties")
    op.drop_table("properties")
    op.drop_table("attachments")
    op.drop_table("mail_messages")
