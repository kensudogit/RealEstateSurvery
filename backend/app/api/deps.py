"""ルート共通の補助。"""

from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

from app.core.config import get_settings
from app.services import repository

logger = logging.getLogger(__name__)


BROKER_PROBE_TIMEOUT = 2.0


def broker_reachable(url: str, timeout: float = BROKER_PROBE_TIMEOUT) -> bool:
    """ブローカーへ TCP で繋がるかだけを短時間で確かめる。

    Celery の再試行設定に任せると、名前解決の失敗などで数十秒待たされる。
    ここで先に判定すれば、押した直後にエラーを返せる。
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (6380 if parsed.scheme == "rediss" else 6379)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def dispatch(session, job, task, *args) -> None:
    """ジョブをワーカーへ投げる。

    ブローカーへ繋がらないとき、ここで捕まえないとリクエストが返らず
    「実行ボタンを押したまま固まる」という一番困る形になる。原因が
    読めるエラーにして即座に返し、ジョブも失敗として記録する。
    """
    redis_url = get_settings().redis_url
    if not broker_reachable(redis_url):
        _fail(session, job, redis_url, "TCP 接続できません")

    try:
        task.delay(*args)
    except Exception as exc:  # kombu / redis の例外は多岐にわたる
        _fail(session, job, redis_url, str(exc))


def _fail(session, job, redis_url: str, cause: str) -> None:
    # 認証情報を含みうるので接続先だけ出す
    target = redis_url.split("@")[-1] if "@" in redis_url else redis_url
    detail = (
        f"ジョブを投入できませんでした。ワーカーのキュー（{target}）へ接続できません。"
        "REDIS_URL の設定と Redis サービスの稼働を確認してください。"
    )
    logger.error("%s: %s", detail, cause)
    repository.finish_job(session, job.id, detail)
    session.commit()
    raise HTTPException(status_code=503, detail=detail)
