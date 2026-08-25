"""設定の解決テスト。

Railway / Heroku のようなマネージド環境が配る値をそのまま受けても
起動できることを確認する。ここが通らないとデプロイ先で必ず落ちる。
"""

from __future__ import annotations

import pytest

from app.core.config import REPO_ROOT, Settings


class TestDatabaseUrl:
    @pytest.mark.parametrize(
        "given,expected",
        [
            # Railway が配る形。driver 指定が無いと SQLAlchemy は psycopg2 を
            # 探しにいくが、入れているのは psycopg3 なので起動時に落ちる。
            ("postgresql://app:pw@host.railway.internal:5432/railway",
             "postgresql+psycopg://app:pw@host.railway.internal:5432/railway"),
            # Heroku の旧表記
            ("postgres://app:pw@host:5432/db",
             "postgresql+psycopg://app:pw@host:5432/db"),
            # 既に driver 指定があるものは触らない
            ("postgresql+psycopg://app:pw@postgres:5432/realestate",
             "postgresql+psycopg://app:pw@postgres:5432/realestate"),
            # 他の driver を明示している場合も尊重する
            ("postgresql+asyncpg://app:pw@host/db",
             "postgresql+asyncpg://app:pw@host/db"),
        ],
    )
    def test_driver_is_normalized(self, given, expected):
        assert Settings(database_url=given).database_url == expected


class TestResourcePaths:
    def test_defaults_resolve_to_repo(self):
        """絶対パスの直書きをやめ、リポジトリ基準で解決する。

        /app/config/... を直書きすると、ビルドのルートが backend/ になる
        デプロイ先（Railway など）で必ず見失う。
        """
        settings = Settings()
        assert settings.property_fields_path == REPO_ROOT / "config" / "property_fields.json"
        assert settings.property_fields_path.exists()
        assert settings.sheets_column_map.exists()

    def test_templates_are_bundled(self):
        settings = Settings()
        for key, path in settings.template_paths.items():
            assert path.exists(), f"{key} のテンプレートが見つかりません: {path}"

    def test_env_overrides_default(self, tmp_path):
        custom = tmp_path / "fields.json"
        custom.write_text("{}", encoding="utf-8")
        assert Settings(property_fields_path=custom).property_fields_path == custom
