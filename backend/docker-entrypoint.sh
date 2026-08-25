#!/bin/sh
# API の起動。マネージド環境で「なぜ healthy にならないか」が
# デプロイログだけで分かるようにしてある。
set -e

PORT="${PORT:-8000}"

# 待ち受けアドレス。既定は 0.0.0.0（プラットフォームの公開トラフィックは
# IPv4 で来るため、まずこれを満たす）。
#
# Railway のようにサービス間通信とヘルスチェックを IPv6 の内部ネットワークで
# 行う環境で、ビルドは通るのにヘルスチェックだけ落ちる場合は BIND_HOST=:: を
# 設定する。ただしコンテナの bindv6only が 1 だと :: は IPv4 を受けなくなり、
# 公開側が落ちるので、切り替えたら両方を必ず確認すること。
HOST="${BIND_HOST:-0.0.0.0}"

# 接続先だけ出す。認証情報はログに残さない。
DB_TARGET=$(printf '%s' "${DATABASE_URL:-未設定}" | sed -E 's#^([a-z+]+)://[^@]*@#\1://***@#')
echo "[entrypoint] host=${HOST} port=${PORT}"
echo "[entrypoint] database=${DB_TARGET}"

if [ -z "${DATABASE_URL}" ]; then
    echo "[entrypoint] DATABASE_URL が設定されていません。" >&2
    echo "[entrypoint] Railway なら DATABASE_URL=\${{Postgres.DATABASE_URL}} を設定してください。" >&2
    exit 1
fi

# DB は API より後に起動することがある。少し待つ。
# ここで待たずに alembic へ進むと、起動直後の 1 回で落ちて
# 「ヘルスチェックが通らない」という分かりにくい形になる。
echo "[entrypoint] データベースの起動を待ちます..."
i=0
until python -c "
import sys
from sqlalchemy import create_engine, text
from app.core.config import get_settings
try:
    create_engine(get_settings().database_url, pool_pre_ping=True).connect().execute(text('select 1'))
except Exception as exc:
    print(f'  まだ接続できません: {type(exc).__name__}: {exc}'[:200], file=sys.stderr)
    sys.exit(1)
" 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
        echo "[entrypoint] データベースへ接続できませんでした（60秒）。" >&2
        echo "[entrypoint] DATABASE_URL の指す先が起動しているか、同じ環境にあるか確認してください。" >&2
        exit 1
    fi
    sleep 2
done
echo "[entrypoint] データベースへ接続できました"

echo "[entrypoint] マイグレーションを適用します"
alembic upgrade head

# 添付と生成物の置き場。永続ボリュームは実行時にマウントされ、
# イメージ内に作ったディレクトリを覆い隠すので、ここで作り直す。
ATTACHMENTS="${ATTACHMENT_STORAGE_DIR:-/data/attachments}"
DOCUMENTS="${PPTX_OUTPUT_DIR:-/data/documents}"
mkdir -p "${ATTACHMENTS}" "${DOCUMENTS}"
echo "[entrypoint] 添付=${ATTACHMENTS} 生成物=${DOCUMENTS}"
if [ ! -w "${ATTACHMENTS}" ]; then
    echo "[entrypoint] ${ATTACHMENTS} へ書き込めません。ボリュームのマウント先を確認してください。" >&2
    exit 1
fi

# ワーカーを同じコンテナで動かす。
#
# Railway ではボリュームをサービス間で共有できない（1 サービス 1 ボリューム）。
# ワーカーが添付と資料を書き、API がそれを配信する構成なので、両者を別々の
# サービスにするとどちらかがファイルを読めなくなる。規模が小さいうちは
# 同居させるのが確実で、ボリュームも 1 つで済む。
#
# 件数が増えてスケールさせたくなったら、ファイルの置き場を
# オブジェクトストレージへ移してから分離する。
if [ "${RUN_WORKER_INLINE}" = "true" ]; then
    CONCURRENCY="${WORKER_CONCURRENCY:-2}"
    echo "[entrypoint] ワーカーを同じコンテナで起動します（concurrency=${CONCURRENCY}）"
    celery -A app.workers.celery_app worker --loglevel=info --concurrency="${CONCURRENCY}" &
    WORKER_PID=$!
    # ワーカーが落ちたらコンテナごと終了させる。生きているのに
    # ジョブが一切進まない、という分かりにくい状態を避ける。
    trap 'kill -TERM "${WORKER_PID}" 2>/dev/null' TERM INT
fi

echo "[entrypoint] uvicorn を起動します"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}"
