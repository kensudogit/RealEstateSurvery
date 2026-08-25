"""Claude API による抽出（②）。

OCR エンジンは原則挟まない。Claude は PDF と画像をそのまま受け取り、
レイアウトを保ったまま読み取れる。「OCR でテキスト化 → LLM で構造化」の
2 段構えにすると、OCR が表の行列対応を壊した時点で情報が失われ、
後段では復元できない。販売図面のように表と図が混在した文書ほど、
画像のまま渡した方が精度が高い。
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from app.core.config import get_settings
from app.services.extraction.schema import SYSTEM_PROMPT, build_schema

logger = logging.getLogger(__name__)

IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp"}

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# API の上限。これを超えると 400 になるので、事前に弾いて人に知らせる。
MAX_REQUEST_BYTES = 30 * 1024 * 1024


class ExtractionError(RuntimeError):
    pass


@dataclass
class ExtractionResult:
    raw: dict[str, Any]
    model: str
    usage: dict[str, int] = field(default_factory=dict)


def _client() -> anthropic.Anthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ExtractionError("ANTHROPIC_API_KEY が設定されていません")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def build_content_blocks(body_text: str | None, files: list[Path]) -> list[dict]:
    """PDF・画像・本文を content blocks に変換する。

    document / image ブロックはテキストより前に置く。後ろに置くと
    「この文書について」という参照がモデル側で外れやすい。
    画像の直前にファイル名を入れておくと、どの画像がどれかをモデルが指せる
    ようになり、外観／間取り図／地図の分類が安定する。
    """
    blocks: list[dict] = []
    total_bytes = 0

    for path in files:
        payload = path.read_bytes()
        total_bytes += len(payload) * 4 // 3  # base64 で約 4/3 に膨らむ
        if total_bytes > MAX_REQUEST_BYTES:
            raise ExtractionError(
                f"添付の合計が API 上限を超えています（{path.name} まででおよそ "
                f"{total_bytes / 1024 / 1024:.1f}MB）。ファイルを分けて処理してください"
            )

        data = base64.standard_b64encode(payload).decode("ascii")
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            blocks.append({
                "type": "document",
                "title": path.name,
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            })
        elif suffix in IMAGE_MIME:
            blocks.append({"type": "text", "text": f"[次の画像のファイル名: {path.name}]"})
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": IMAGE_MIME[suffix], "data": data},
            })
        else:
            logger.warning("未対応の添付形式のため無視します: %s", path.name)

    instruction = "上記の資料から物件情報を抽出してください。"
    if body_text:
        instruction = (
            "以下はメール本文です。添付資料と合わせて物件情報を抽出してください。\n"
            "本文と添付で値が食い違う場合は、添付の販売図面・概要書を優先し、\n"
            "食い違ったこと自体を該当項目の evidence に書いてください。\n\n"
            "--- メール本文 ---\n" + body_text + "\n--- ここまで ---"
        )
    blocks.append({"type": "text", "text": instruction})
    return blocks


def extract(body_text: str | None, files: list[Path]) -> ExtractionResult:
    settings = get_settings()
    schema = build_schema([p.name for p in files])

    request: dict[str, Any] = dict(
        model=settings.extraction_model,
        max_tokens=32000,
        # システムプロンプトは毎回同じなのでキャッシュする。添付が大きいときほど
        # 効く。usage.cache_read_input_tokens が 0 のままなら効いていない。
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_content_blocks(body_text, files)}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": settings.extraction_effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    )

    client = _client()
    # 添付が大きいと入力が長くなるので streaming を使う（HTTP タイムアウト回避）。
    # 拒否された場合に備えてサーバサイドフォールバックを有効にしておく。
    with client.beta.messages.stream(
        betas=[FALLBACK_BETA], fallbacks="default", **request
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise ExtractionError(f"抽出が拒否されました: {getattr(message, 'stop_details', None)}")

    try:
        text = next(block.text for block in message.content if block.type == "text")
    except StopIteration as exc:
        raise ExtractionError("応答にテキストブロックがありません") from exc

    usage = message.usage
    return ExtractionResult(
        raw=json.loads(text),
        model=message.model,
        usage={
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        },
    )
