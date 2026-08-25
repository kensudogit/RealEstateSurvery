from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# 設定を読む前に環境変数を立てる。get_settings() は lru_cache なので、
# import 順を誤ると既定パス（/app/config/...）で固定されてしまう。
os.environ.setdefault("PROPERTY_FIELDS_PATH", str(REPO_ROOT / "config" / "property_fields.json"))
os.environ.setdefault("SHEETS_COLUMN_MAP", str(REPO_ROOT / "config" / "column_map.yaml"))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/realestate")


@pytest.fixture(scope="session")
def field_definitions() -> dict:
    from app.services.extraction import fields as field_defs

    return field_defs.load_definitions()


@pytest.fixture
def envelope():
    """テスト用の envelope を作るヘルパ。"""

    def _make(value, confidence=0.95, evidence="原文"):
        return {"value": value, "confidence": confidence, "evidence": evidence}

    return _make


def pytest_addoption(parser) -> None:
    """ゴールデンセットの再抽出フラグ。

    API 呼び出しは高いので既定ではキャッシュを使い、プロンプトを変えたときだけ
    実際に呼び直す。CI は常にキャッシュを使う。
    """
    parser.addoption(
        "--refresh-golden", action="store_true", default=False,
        help="キャッシュを無視して実際に Claude API を呼び、正解と突き合わせる",
    )
