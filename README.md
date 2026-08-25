# 不動産物件情報 自動転記・資料生成システム

Gmail の指定ラベルに届く物件メールを、AI で読み取ってスプレッドシートへ転記し、
PowerPoint の物件資料まで自動生成する。

```text
① Gmail（指定ラベル） → ② AI/AI-OCR で抽出 → ③ スプレッドシート転記
                                              → ④ 最寄駅・徒歩分数の補完
                                              → ⑤ PowerPoint 資料生成
```

抽出できなかった項目は推測で埋めず、空欄のまま `要確認` フラグを立てて出力する。
設計方針の詳細は `.claude/skills/realestate-doc-automation/SKILL.md`。

## 構成

| ディレクトリ | 内容 |
|---|---|
| `backend/` | FastAPI + Celery。①〜⑤の実処理 |
| `frontend/` | Next.js。実行ボタン・ジョブ監視・要確認レビュー画面 |
| `config/` | `property_fields.json`（抽出項目の唯一の定義）、`column_map.yaml`（シート列） |
| `templates/` | PowerPoint テンプレート（マイソク／物件概要書） |
| `.claude/skills/` | 開発規約・実装ガイド・単体で動く CLI スクリプト |

## 起動

```bash
cp .env.example .env    # API キーを埋める
docker compose up -d
docker compose exec backend alembic upgrade head
```

- API: http://localhost:8000/docs
- UI:  http://localhost:3000

## Railway へのデプロイ

Dockerfile はリポジトリのルートに置いてあり、**ビルドコンテキストは常にルート**。
`backend/` や `frontend/` の中に Dockerfile を置くと、ビルド元によって COPY の
パスが変わって壊れるため、一本化している。

必要なサービスは 4 つ。

| サービス | Config Path | 備考 |
|---|---|---|
| API | `railway.json` | 起動時に `alembic upgrade head` を実行。`/health` でヘルスチェック |
| ワーカー | `railway.worker.json` | 同じイメージ。起動コマンドだけ Celery に差し替え |
| フロントエンド | `railway.frontend.json` | `NEXT_PUBLIC_API_BASE_URL` は**ビルド時**の変数に入れること |
| PostgreSQL / Redis | — | Railway のテンプレートを追加 |

設定する変数。

```
DATABASE_URL   = ${{Postgres.DATABASE_URL}}
REDIS_URL      = ${{Redis.REDIS_URL}}
ANTHROPIC_API_KEY = sk-ant-...
```

`DATABASE_URL` は driver 指定の無い `postgresql://` 形式で配られるが、
アプリ側で `postgresql+psycopg://` へ正規化するのでそのまま渡してよい。

**永続ボリュームを `/data` にマウントすること。** 添付ファイルと生成した
資料の置き場で、マウントしないとデプロイのたびに消える。

Railway の「Suggested Variables」は `.env.example` のプレースホルダを
拾ったもの。`info@example.co.jp` や `1AbC...` は実在しない値なので、
そのまま追加しないこと。項目定義やテンプレートのパスも既定値で解決する。

## 認証情報の準備

| 何 | どこで取る |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| Google サービスアカウント JSON | GCP コンソール。Gmail / Sheets の権限を委任 |
| `GOOGLE_MAPS_API_KEY` | GCP。Geocoding / Places / Distance Matrix を有効化しサーバ専用キーに制限 |

対象のスプレッドシートは、サービスアカウントのメールアドレスに**編集者として共有**する。
これを忘れると 403 になる。

## API キーが揃う前に触ってみる

外部 API を一切呼ばずに、抽出済みの物件が 3 件ある状態を作れます。
要確認フラグの見え方、根拠の表示、修正 → 再生成の導線を先に確認できます。

```bash
docker compose exec backend python /app/tools/seed_sample.py
```

テンプレートの雛形も生成できます（営業が使っている pptx に差し替えるまでの土台）。

```bash
python tools/make_starter_templates.py --out templates/
```

抽出精度を測るためのサンプル販売図面も生成できます。和暦・坪表記・全角数字・
罫線を省略した表・FAX ヘッダなど、日本の販売図面特有の落とし穴を仕込んであります。

```bash
python tools/make_sample_mysoku.py --out backend/tests/golden
```

## 開発

```bash
cd backend && pip install -e ".[dev]" && pytest
cd frontend && npm install && npm run dev
```

`pytest` は外部 API を呼びません。抽出精度の計測は正解データを
`backend/tests/golden/` に置いてから行います（`backend/tests/golden/README.md`）。
