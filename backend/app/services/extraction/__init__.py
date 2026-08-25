"""抽出サービス（②）のエントリポイント。"""

from __future__ import annotations

import logging
import re
from html import unescape
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import MailMessage
from app.services.extraction.claude import ExtractionError, extract
from app.services.extraction.normalize import (
    apply_review_flags, cross_check, normalize_fields, summarize,
)

logger = logging.getLogger(__name__)

__all__ = ["run_for_mail", "html_to_text", "ExtractionError", "cross_check"]

SCRIPT_RE = re.compile(r"(?is)<(script|style).*?</\1>")
CELL_END_RE = re.compile(r"(?i)</t[dh]>")
LINE_END_RE = re.compile(r"(?i)</(tr|p|div|li|h[1-6])>|<br\s*/?>")
TAG_RE = re.compile(r"<[^>]+>")


def run_for_mail(session: Session, mail_id: int) -> dict[str, Any]:
    """1 通のメールから物件情報を抽出する。

    戻り値は {"fields", "stations", "images", "meta"}。
    抽出できなかった項目は null のまま needs_review が立つ。
    """
    mail = session.get(MailMessage, mail_id)
    if mail is None:
        raise ExtractionError(f"メールが見つかりません: mail_id={mail_id}")

    # 署名画像・バナーは抽出対象から外す。会社ロゴを毎回 API に投げても
    # 情報は増えず、トークンとレイテンシだけ増える。
    attachments = [
        a for a in mail.attachments if not a.is_signature and Path(a.storage_path).exists()
    ]
    files = [Path(a.storage_path) for a in attachments]

    body = mail.body_text or html_to_text(mail.body_html)
    if not body and not files:
        raise ExtractionError(f"本文も添付もありません: mail_id={mail_id}")

    result = extract(body, files)
    logger.info(
        "抽出完了 mail_id=%s model=%s input_tokens=%s cache_read=%s",
        mail_id, result.model, result.usage.get("input_tokens"),
        result.usage.get("cache_read_input_tokens"),
    )

    raw = dict(result.raw)
    stations = raw.pop("stations", [])
    images = raw.pop("images", [])
    fields = apply_review_flags(normalize_fields(raw))

    # モデルが返すのはファイル名だけなので、ここで実体と対応付けないと
    # ⑤の資料生成で画像を開けない。
    by_name = {a.filename: a for a in attachments}
    resolved_images = [
        {**item, "storage_path": by_name[item["file"]].storage_path}
        for item in images
        if item.get("file") in by_name
    ]

    return {
        "fields": fields,
        "stations": [{**station, "source": "extracted"} for station in stations],
        "images": resolved_images,
        "meta": {
            **summarize(fields),
            "extraction_model": result.model,
            "prompt_version": get_settings().prompt_version,
            "usage": result.usage,
        },
    }


def html_to_text(html: str | None) -> str:
    """本文が HTML しか無いメール向けのテキスト化。

    物件概要が表で組まれていることが多いので、セルをタブ・行を改行に
    置き換えてから落とす。単純にタグを剥ぐと表構造が消えて、
    「価格」と「4,800万円」の対応が失われ、抽出精度が落ちる。
    """
    if not html:
        return ""

    text = SCRIPT_RE.sub(" ", html)
    text = CELL_END_RE.sub("\t", text)
    text = LINE_END_RE.sub("\n", text)
    text = TAG_RE.sub("", text)
    text = unescape(text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
