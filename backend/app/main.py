"""FastAPI アプリ。"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import documents, jobs, properties
from app.core.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = get_settings()

app = FastAPI(
    title="不動産物件情報 自動転記・資料生成 API",
    version="0.1.0",
    description=(
        "Gmail の指定ラベルから物件メールを取り込み、AI で抽出して"
        "スプレッドシート転記と PowerPoint 資料生成まで行う。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(properties.router)
app.include_router(documents.router)


@app.get("/", tags=["meta"])
def index() -> dict[str, object]:
    """ルートの案内。

    ここが 404 だと、デプロイ直後にブラウザで開いた人が
    「動いていない」と誤解する。ここは API サービスで UI は別サービスである、
    と分かる最小限の情報を返す。
    """
    return {
        "service": app.title,
        "version": app.version,
        "status": "ok",
        "note": "これは API です。操作画面は別サービスのフロントエンドから開いてください。",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "config": "/config",
            "properties": "/properties",
            "field_specs": "/properties/field-specs",
            "jobs": "/jobs",
        },
    }


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config", tags=["meta"])
def config() -> dict[str, object]:
    """UI が起動時に読む設定。認証情報は絶対に含めない。"""
    from app.services.extraction import fields as field_defs

    return {
        "gmail_label": settings.gmail_label_target,
        "extraction_model": settings.extraction_model,
        "prompt_version": settings.prompt_version,
        "review_threshold": field_defs.review_threshold(),
        "templates": sorted(settings.template_paths),
    }
