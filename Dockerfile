# バックエンド（API / Celery ワーカー共用）。
#
# ビルドコンテキストは必ずリポジトリのルート。Railway のようにルートを
# コンテキストにする環境と、ローカルの docker compose で同じ Dockerfile を使う。
# backend/ や frontend/ の中に Dockerfile を置くと、ビルド元によって
# COPY のパスが変わって壊れるため、ルートに一本化している。
FROM python:3.12-slim AS base

# LibreOffice は pptx -> PDF 変換用。日本語フォントを入れないと全部豆腐になる。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY backend/pyproject.toml ./backend/
RUN pip install --no-cache-dir -e "./backend[dev]"

ENV PYTHONUNBUFFERED=1
# 添付と生成物の置き場。永続ボリュームを /data にマウントすること。
# マウントしないとデプロイのたびに消える。
RUN mkdir -p /data/attachments /data/documents

WORKDIR /srv/backend


# --- ローカル開発用。ソースは compose がマウントするので焼き込まない ---
FROM base AS dev
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# --- デプロイ用 ---
FROM base AS runtime

# app/core/config.py の REPO_ROOT が backend/ の 1 つ上を指すので、
# ローカルと同じ相対配置のまま置く。
COPY backend/ /srv/backend/
COPY config/ /srv/config/
COPY templates/ /srv/templates/
COPY tools/ /srv/tools/
RUN chmod +x /srv/backend/docker-entrypoint.sh

# $PORT はプラットフォームが渡す。ローカル確認用に 8000 を既定にしておく。
ENV PORT=8000
EXPOSE 8000

# DB の待機・マイグレーション・待ち受けアドレスの決定は entrypoint に任せる。
# 起動できなかった理由がデプロイログに出るようにするため。
CMD ["/srv/backend/docker-entrypoint.sh"]
