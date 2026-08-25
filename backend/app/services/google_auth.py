"""Google API の認証情報の解決。

マネージド環境では認証情報をファイルとして置けないことが多い。
環境変数には JSON の中身をそのまま入れられるようにし、ファイルパスでも
中身でも同じように受け付ける。

個人の Gmail（@gmail.com）ではサービスアカウントは使えない。
ドメイン全体の委任は Google Workspace の管理者権限が前提のため、
個人アカウントは OAuth 一択になる。その場合はローカルで
tools/gmail_authorize.py を実行してトークンを作り、中身を
GOOGLE_OAUTH_TOKEN_JSON に入れる。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class GoogleAuthError(RuntimeError):
    pass


def _as_json(value: str | Path | None) -> dict[str, Any] | None:
    """パスでも JSON の中身でも受け取れるようにする。

    Railway のような環境では変数に JSON をそのまま貼るしかないが、
    ローカルの docker compose ではファイルをマウントする方が扱いやすい。
    どちらでも同じ設定名で通るようにしておくと、環境ごとに分岐しなくて済む。
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GoogleAuthError(
                f"認証情報の JSON を解釈できません: {exc}。"
                "改行や引用符が途中で切れていないか確認してください"
            ) from exc

    path = Path(text)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoogleAuthError(f"{path} の JSON を解釈できません: {exc}") from exc


def _token_path() -> Path | None:
    """OAuth トークンをファイルで持っている場合の保存先。

    更新されたトークンを書き戻すために使う。変数で渡されている場合は
    書き戻せないので None。
    """
    configured = get_settings().google_oauth_token_path
    if configured is None:
        return None
    text = str(configured).strip()
    return Path(text) if text and not text.startswith("{") else None


def load_credentials(scopes: list[str]):
    """サービスアカウントか OAuth のどちらかで認証情報を作る。

    サービスアカウントを優先する。無人で動かせるため、Workspace が
    使える環境ではそちらの方が運用が楽になる。
    """
    settings = get_settings()

    service_account_info = _as_json(settings.google_service_account_json)
    if service_account_info:
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=scopes
        )
        subject = settings.google_impersonate_subject
        if subject:
            # Gmail はユーザーの受信箱を読むので、委任先の指定が要る。
            credentials = credentials.with_subject(subject)
        return credentials

    token_info = _as_json(settings.google_oauth_token_json or settings.google_oauth_token_path)
    if token_info:
        credentials = Credentials.from_authorized_user_info(token_info, scopes)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            path = _token_path()
            if path is not None:
                path.write_text(credentials.to_json(), encoding="utf-8")
            else:
                # 変数で渡されている場合は書き戻せない。アクセストークンは
                # メモリ上で更新されるので動作に支障はないが、
                # リフレッシュトークンが失効したら再取得が要ることを残す。
                logger.info("OAuth トークンを更新しました（保存先が無いため書き戻しません）")
        return credentials

    raise GoogleAuthError(
        "Google の認証情報がありません。個人の Gmail なら "
        "tools/gmail_authorize.py でトークンを作り、その中身を "
        "GOOGLE_OAUTH_TOKEN_JSON に設定してください。"
        "Workspace ならサービスアカウントの JSON を "
        "GOOGLE_SERVICE_ACCOUNT_JSON に設定します。"
    )
