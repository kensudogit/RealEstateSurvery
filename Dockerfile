# Railway など、リポジトリのルートをビルドコンテキストにする環境向け。
#
# backend/Dockerfile はローカルの docker compose 用で、コンテキストが
# backend/ 配下であることを前提にしている。そちらでは config/ と templates/ を
# ボリュームでマウントするが、マネージド環境ではマウントできないので、
# ここでイメージに焼き込む。
FROM python:3.12-slim

# LibreOffice は pptx -> PDF 変換用。日本語フォントを入れないと全部豆腐になる。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY backend/pyproject.toml ./backend/
RUN pip install --no-cache-dir -e "./backend[dev]"

# app/core/config.py の REPO_ROOT が backend/ の 1 つ上を指すので、
# ローカルと同じ相対配置のまま置く。
COPY backend/ ./backend/
COPY config/ ./config/
COPY templates/ ./templates/
COPY tools/ ./tools/

WORKDIR /srv/backend

# 添付と生成物の置き場。永続ボリュームを /data にマウントすること。
# マウントしないとデプロイのたびに消える。
RUN mkdir -p /data/attachments /data/documents

ENV PYTHONUNBUFFERED=1

# $PORT はプラットフォームが渡す。ローカル確認用に 8000 を既定にしておく。
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
