"""Gmail からの取得（①）。

同期は重複と再送が普通に起きる。gmail_message_id を一意キーにして、
何度流しても同じ結果になるようにしてある。処理済みラベルはパイプライン
全体が成功してから付ける（途中で落ちたのに付けると二度と拾われない）。
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.models import Attachment, MailMessage, SignatureImage

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

# 署名画像・バナーを落とす閾値。実務では会社ロゴが毎回添付されてくる。
MIN_IMAGE_BYTES = 20 * 1024
SIGNATURE_SEEN_THRESHOLD = 3

IMAGE_MIME_PREFIX = "image/"
SUPPORTED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"}


class GmailError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 認証
# --------------------------------------------------------------------------

def build_service():
    """サービスアカウント（ドメイン委任）か OAuth トークンのどちらかで作る。"""
    settings = get_settings()

    if settings.google_service_account_json and settings.google_service_account_json.exists():
        credentials = service_account.Credentials.from_service_account_file(
            str(settings.google_service_account_json), scopes=SCOPES
        )
        if settings.google_impersonate_subject:
            credentials = credentials.with_subject(settings.google_impersonate_subject)
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    token_path = settings.google_oauth_token_path
    if token_path and token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_path.write_text(credentials.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    raise GmailError(
        "Gmail の認証情報がありません。GOOGLE_SERVICE_ACCOUNT_JSON か "
        "GOOGLE_OAUTH_TOKEN_PATH を設定してください"
    )


# --------------------------------------------------------------------------
# 取得
# --------------------------------------------------------------------------

_retry = retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)


def target_query() -> str:
    """対象ラベルが付いていて、処理済みでもエラーでもないもの。

    historyId による差分同期もあるが、ラベルを外せば再取り込みできる
    この形の方が運用中の手戻りに強い。
    """
    settings = get_settings()
    return (
        f'label:"{settings.gmail_label_target}" '
        f'-label:"{settings.gmail_label_done}" '
        f'-label:"{settings.gmail_label_error}"'
    )


@_retry
def list_message_ids(service, query: str, max_results: int) -> list[str]:
    """ページングを忘れると 100 通で止まる。"""
    ids: list[str] = []
    page_token = None
    while True:
        response = service.users().messages().list(
            userId="me", q=query, maxResults=min(max_results, 500), pageToken=page_token,
        ).execute()
        ids.extend(item["id"] for item in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token or len(ids) >= max_results:
            break
    return ids[:max_results]


@_retry
def get_message(service, message_id: str) -> dict[str, Any]:
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


@_retry
def download_attachment(service, message_id: str, attachment_id: str) -> bytes:
    response = service.users().messages().attachments().get(
        userId="me", messageId=message_id, id=attachment_id
    ).execute()
    return base64.urlsafe_b64decode(response["data"])


# --------------------------------------------------------------------------
# MIME 解析
# --------------------------------------------------------------------------

def walk_parts(part: dict) -> Iterator[dict]:
    yield part
    for child in part.get("parts") or []:
        yield from walk_parts(child)


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def parse_payload(message: dict) -> tuple[str, str, list[dict]]:
    """本文（text / html）と添付メタを取り出す。"""
    body_text, body_html = "", ""
    attachments: list[dict] = []

    for part in walk_parts(message["payload"]):
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        filename = part.get("filename") or ""
        headers = {h["name"].lower(): h["value"] for h in part.get("headers", []) or []}

        if mime == "text/plain" and not filename and body.get("data"):
            body_text += _decode(body["data"])
        elif mime == "text/html" and not filename and body.get("data"):
            body_html += _decode(body["data"])
        elif body.get("attachmentId"):
            # 本文中の inline 画像は filename が空のことがある。
            # Content-ID から名前を作らないと、後で画像を参照できない。
            content_id = (headers.get("content-id") or "").strip("<>")
            if not filename:
                extension = mime.split("/")[-1].replace("jpeg", "jpg")
                stem = content_id or body["attachmentId"][:8]
                filename = f"inline_{stem}.{extension}"
            attachments.append({
                "filename": filename,
                "mime_type": mime,
                "attachment_id": body["attachmentId"],
                "content_id": content_id or None,
                "size_bytes": body.get("size", 0),
            })

    return body_text, body_html, attachments


def header_value(message: dict, name: str) -> str | None:
    for header in message["payload"].get("headers", []) or []:
        if header["name"].lower() == name.lower():
            return header["value"]
    return None


# --------------------------------------------------------------------------
# 保存
# --------------------------------------------------------------------------

def _is_probably_signature(session: Session, sha256: str, domain: str,
                           mime: str, size: int) -> bool:
    if not mime.startswith(IMAGE_MIME_PREFIX):
        return False
    if size < MIN_IMAGE_BYTES:
        return True
    record = session.get(SignatureImage, (sha256, domain))
    return bool(record and record.seen_count >= SIGNATURE_SEEN_THRESHOLD)


def _remember_image(session: Session, sha256: str, domain: str) -> None:
    record = session.get(SignatureImage, (sha256, domain))
    if record is None:
        session.add(SignatureImage(sha256=sha256, from_domain=domain, seen_count=1))
    else:
        record.seen_count += 1
        record.last_seen_at = datetime.now(timezone.utc)


def ingest_one(session: Session, service, message_id: str) -> int | None:
    """1 通取り込む。既に取り込み済みなら None を返して何もしない。"""
    existing = session.scalar(
        select(MailMessage.id).where(MailMessage.gmail_message_id == message_id)
    )
    if existing is not None:
        logger.debug("取り込み済みのためスキップ: %s", message_id)
        return None

    settings = get_settings()
    message = get_message(service, message_id)
    body_text, body_html, attachment_metas = parse_payload(message)

    from_address = header_value(message, "From") or ""
    domain = from_address.rsplit("@", 1)[-1].strip("> ").lower() if "@" in from_address else ""

    mail = MailMessage(
        gmail_message_id=message_id,
        gmail_thread_id=message.get("threadId"),
        label=settings.gmail_label_target,
        subject=header_value(message, "Subject"),
        from_address=from_address,
        received_at=datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc),
        body_text=body_text or None,
        body_html=body_html or None,
        raw_headers={h["name"]: h["value"] for h in message["payload"].get("headers", []) or []},
    )
    session.add(mail)
    session.flush()

    storage_root = settings.attachment_storage_dir / message_id
    storage_root.mkdir(parents=True, exist_ok=True)
    seen_hashes: set[str] = set()

    for meta in attachment_metas:
        if meta["mime_type"] not in SUPPORTED_MIME:
            logger.info("未対応の添付を無視: %s (%s)", meta["filename"], meta["mime_type"])
            continue

        payload = download_attachment(service, message_id, meta["attachment_id"])
        sha256 = hashlib.sha256(payload).hexdigest()
        # 転送メールでは同じ図面が何度も添付される。同一ファイルは 1 回だけ持つ。
        if sha256 in seen_hashes:
            continue
        seen_hashes.add(sha256)

        path = storage_root / meta["filename"]
        path.write_bytes(payload)

        is_signature = _is_probably_signature(
            session, sha256, domain, meta["mime_type"], len(payload)
        )
        if meta["mime_type"].startswith(IMAGE_MIME_PREFIX) and domain:
            _remember_image(session, sha256, domain)

        session.add(Attachment(
            mail_id=mail.id,
            filename=meta["filename"],
            mime_type=meta["mime_type"],
            size_bytes=len(payload),
            sha256=sha256,
            storage_path=str(path),
            content_id=meta["content_id"],
            is_signature=is_signature,
        ))

    return mail.id


def fetch_new_mails(session: Session, limit: int | None = None) -> list[int]:
    """対象ラベルの未処理メールを取り込み、新規に作られた mail_id を返す。"""
    settings = get_settings()
    service = build_service()
    message_ids = list_message_ids(service, target_query(), limit or settings.gmail_max_results)

    created: list[int] = []
    for message_id in message_ids:
        mail_id = ingest_one(session, service, message_id)
        if mail_id is not None:
            created.append(mail_id)
    return created


def _label_id(service, name: str) -> str | None:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    return next((label["id"] for label in labels if label["name"] == name), None)


def mark_done(service, message_id: str, *, failed: bool = False) -> None:
    """処理済み／エラーのラベルを付ける。パイプライン成功後にだけ呼ぶこと。"""
    settings = get_settings()
    name = settings.gmail_label_error if failed else settings.gmail_label_done
    label_id = _label_id(service, name)
    if label_id is None:
        logger.warning("ラベルが見つかりません: %s（Gmail 側で作成してください）", name)
        return
    service.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]}
    ).execute()
