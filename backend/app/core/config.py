"""環境変数から設定を読む。既定値は .env.example と揃えてある。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- 抽出（②）----
    anthropic_api_key: str = ""
    extraction_model: str = "claude-opus-5"
    extraction_effort: str = "high"
    extraction_confidence_threshold: float = 0.75
    prompt_version: str = "v1"

    # ---- 項目定義 ----
    property_fields_path: Path = Path("/app/config/property_fields.json")

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
    sheets_column_map: Path = Path("/app/config/column_map.yaml")

    # ---- Maps（④）----
    google_maps_api_key: str = ""
    geo_walk_meters_per_minute: int = 80
    geo_max_stations: int = 3
    geo_search_radius_m: float = 2000.0
    geo_cache_ttl_days: int = 90

    # ---- PPTX（⑤）----
    pptx_template_mysoku: Path = Path("/app/templates/mysoku_a4.pptx")
    pptx_template_summary: Path = Path("/app/templates/summary_a4.pptx")
    pptx_output_dir: Path = Path("/data/documents")
    pptx_image_max_px: int = 1600
    pptx_image_jpeg_quality: int = 85

    # ---- インフラ ----
    database_url: str = "postgresql+psycopg://app:app@postgres:5432/realestate"
    redis_url: str = "redis://redis:6379/0"
    cors_origins: str = "http://localhost:3000"

    @property
    def template_paths(self) -> dict[str, Path]:
        return {"mysoku": self.pptx_template_mysoku, "summary": self.pptx_template_summary}


@lru_cache
def get_settings() -> Settings:
    return Settings()
