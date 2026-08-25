"""項目定義（config/property_fields.json）の読み込み。

抽出スキーマ・スプレッドシート列・PPTX プレースホルダ・レビュー画面のラベルは
すべてこの 1 ファイルから引く。定義をコードに書き写すと必ずどこかで食い違う。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.core.config import get_settings


@lru_cache
def load_definitions() -> dict[str, Any]:
    path = get_settings().property_fields_path
    if not path.exists():
        raise FileNotFoundError(
            f"項目定義が見つかりません: {path}。"
            "PROPERTY_FIELDS_PATH を確認してください"
        )
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache
def field_specs() -> dict[str, dict[str, Any]]:
    """key → 項目定義。"""
    return {f["key"]: f for f in load_definitions()["fields"]}


@lru_cache
def labels() -> dict[str, str]:
    """key → 日本語ラベル。レビュー画面とシートの見出しに使う。"""
    return {f["key"]: f["label"] for f in load_definitions()["fields"]}


@lru_cache
def required_keys() -> frozenset[str]:
    return frozenset(f["key"] for f in load_definitions()["fields"] if f.get("required"))


@lru_cache
def extractable_keys() -> tuple[str, ...]:
    """抽出対象。derived（緯度経度など後段で埋めるもの）は含まない。"""
    return tuple(f["key"] for f in load_definitions()["fields"] if not f.get("derived"))


def review_threshold() -> float:
    """確信度の閾値。環境変数が定義ファイルより優先される。"""
    settings = get_settings()
    return settings.extraction_confidence_threshold or float(
        load_definitions().get("review_confidence_threshold", 0.75)
    )


def station_pptx_keys() -> list[str]:
    return list(load_definitions()["repeated_fields"]["stations"]["pptx"])


def image_roles() -> list[str]:
    item_fields = load_definitions()["repeated_fields"]["images"]["item_fields"]
    return next(f["enum"] for f in item_fields if f["key"] == "role")


def max_stations() -> int:
    return int(load_definitions()["repeated_fields"]["stations"]["max_items"])


def max_images() -> int:
    return int(load_definitions()["repeated_fields"]["images"]["max_items"])
