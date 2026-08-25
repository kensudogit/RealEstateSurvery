"""環境変数から設定を読む。既定値は .env.example と揃えてある。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ の 1 つ上がリポジトリのルート。config/ と templates/ の既定値を
# ここから組み立てる。絶対パスを直書きすると、Docker・ローカル・Railway で
# 配置が変わったときに必ずどれかが壊れる。
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- 抽出（②）----
    anthropic_api_key: str = ""
    extraction_model: str = "claude-opus-5"
    extraction_effort: str = "high"
    extraction_confidence_threshold: float = 0.75
    prompt_version: str = "v1"

    # ---- 項目定義 ----
    property_fields_path: Path = REPO_ROOT / "config" / "property_fields.json"

    # ---- Google 共通 ----
    google_service_account_json: Path | None = None
    google_impersonate_subject: str | None = None
    google_oauth_client_secrets: Path | None = None
    google_oauth_token_path: Path | None = None

    # ---- Gmail（①）----
    gmail_label_target: str = "物件情報"
    gmail_label_done: str = "物件情報/処理済"
    gmail_label_error: str = "物件情報/エラー"
    gmail_max_results: int = 50
    attachment_storage_dir: Path = Path("/data/attachments")

    # ---- Sheets（③）----
    sheets_spreadsheet_id: str = ""
    sheets_worksheet_name: str = "物件一覧"
    sheets_header_row: int = 1
    sheets_column_map: Path = REPO_ROOT / "config" / "column_map.yaml"

    # ---- Maps（④）----
    google_maps_api_key: str = ""
    geo_walk_meters_per_minute: int = 80
    geo_max_stations: int = 3
    geo_search_radius_m: float = 2000.0
    geo_cache_ttl_days: int = 90

    # ---- PPTX（⑤）----
    pptx_template_mysoku: Path = REPO_ROOT / "templates" / "mysoku_a4.pptx"
    pptx_template_summary: Path = REPO_ROOT / "templates" / "summary_a4.pptx"
    pptx_output_dir: Path = Path("/data/documents")
    pptx_image_max_px: int = 1600
    pptx_image_jpeg_quality: int = 85

    # ---- インフラ ----
    database_url: str = "postgresql+psycopg://app:app@postgres:5432/realestate"
    redis_url: str = "redis://redis:6379/0"
    cors_origins: str = "http://localhost:3000"

    # REDIS_URL が空になる環境向けの部品。Railway の Redis は個別の変数も
    # 提供しており、参照変数の解決に失敗しても、こちらは埋まることがある。
    redishost: str = ""
    redisport: int = 6379
    redisuser: str = ""
    redispassword: str = ""

    @field_validator("database_url", mode="after")
    @classmethod
    def _use_psycopg3(cls, url: str) -> str:
        """接続 URL のドライバ指定を psycopg3 に揃える。

        Railway や Heroku が配る DATABASE_URL は driver 無しの
        `postgresql://` 形式で、SQLAlchemy はこれを psycopg2 と解釈する。
        本プロジェクトが入れているのは psycopg3 なので、そのままだと
        起動時に ModuleNotFoundError: psycopg2 で落ちる。
        `postgres://` 形式（Heroku の旧表記）も同時に吸収する。
        """
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def broker_url(self) -> str:
        """ジョブキューの接続先。

        REDIS_URL をそのまま使うのが基本。ただしマネージド環境の参照変数は
        空文字に解決されることがあり（Railway では内部ドメインが空になる
        事象が報告されている）、その場合は個別の変数から組み立てる。
        どちらも無ければ空文字を返し、呼び出し側で「未設定」として扱う。
        """
        if self.redis_url.strip():
            return self.redis_url.strip()
        if self.redishost.strip():
            credentials = ""
            if self.redispassword:
                credentials = f"{self.redisuser or 'default'}:{self.redispassword}@"
            return f"redis://{credentials}{self.redishost.strip()}:{self.redisport}/0"
        return ""

    @property
    def template_paths(self) -> dict[str, Path]:
        return {"mysoku": self.pptx_template_mysoku, "summary": self.pptx_template_summary}


@lru_cache
def get_settings() -> Settings:
    return Settings()
