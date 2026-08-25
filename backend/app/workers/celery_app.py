"""Celery アプリ。

1 通あたり添付の解析だけで数十秒かかることがあるため、HTTP リクエストの
中では回さない。API はジョブを作って ID を返すまでにして、実処理はここへ投げる。
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

broker = settings.broker_url
celery_app = Celery("realestate", broker=broker, backend=broker)
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
    # ブローカーへ繋がらないとき、既定では publish が延々とリトライして
    # API のリクエストが返らなくなる。「実行ボタンを押したまま固まる」という
    # 一番困る壊れ方をするので、短時間で諦めて呼び出し側にエラーを返す。
    broker_transport_options={
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
        "retry_on_timeout": False,
    },
    broker_connection_retry_on_startup=True,
    task_publish_retry_policy={
        "max_retries": 1,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 1,
    },
)

celery_app.autodiscover_tasks(["app.workers"])

from app.workers import tasks  # noqa: E402,F401  タスク登録のため
