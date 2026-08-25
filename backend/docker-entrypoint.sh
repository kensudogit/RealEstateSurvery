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

echo "[entrypoint] uvicorn を起動します"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}"
