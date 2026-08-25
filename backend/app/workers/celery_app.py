"""Celery アプリ。

1 通あたり添付の解析だけで数十秒かかることがあるため、HTTP リクエストの
中では回さない。API はジョブを作って ID を返すまでにして、実処理はここへ投げる。
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("realestate", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Tokyo",
    enable_utc=True,
    # 外部 API のレート制限に当てないため、1 ワーカーの同時実行は絞る。
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_time_limit=1800,
    task_soft_time_limit=1500,
)

celery_app.autodiscover_tasks(["app.workers"])

from app.workers import tasks  # noqa: E402,F401  タスク登録のため
