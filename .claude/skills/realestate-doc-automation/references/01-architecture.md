# 01. アーキテクチャとデータモデル

## 全体構成

```text
Browser (Next.js / React / TypeScript)
   |  REST + SSE（ジョブ進捗）
   v
FastAPI ---- Celery worker ---- Redis
   |              |
   |              +-- Gmail API / Claude API / Google Maps / Sheets API
   v
PostgreSQL        オブジェクトストレージ（添付・生成物）
```

同期 API で①〜⑤を全部やらない。1通あたり添付の解析だけで数十秒かかることがあり、
HTTP リクエストの中で回すとタイムアウトする。API は「ジョブを作って ID を返す」
までにして、実処理はワーカーに投げる。

ローカル開発は Docker Compose で `frontend` / `backend` / `worker` / `postgres` /
`redis` を分離する。外部 API の認証情報はバックエンドとワーカーだけが持ち、
フロントエンドには一切渡さない。

## ジョブの状態遷移

```text
queued → fetching → extracting → geocoding → syncing → rendering → done
                         |            |          |          |
                         +------------+----------+----------+--> failed
                                                                   |
                                                            （再実行で queued へ）
```

各段は `job_steps` に1行ずつ記録し、失敗した段から再開できるようにする。
「メール取得からやり直し」しかできないと、Sheets のレート制限で落ちただけの
ジョブが Claude API を再度呼ぶことになり、無駄に課金される。

## テーブル定義

```sql
-- ① 取り込んだメール
CREATE TABLE mail_messages (
    id                BIGSERIAL PRIMARY KEY,
    gmail_message_id  TEXT NOT NULL UNIQUE,   -- 冪等性の要。ここで重複を止める
    gmail_thread_id   TEXT,
    label             TEXT NOT NULL,
    subject           TEXT,
    from_address      TEXT,
    received_at       TIMESTAMPTZ NOT NULL,
    body_text         TEXT,
    body_html         TEXT,
    raw_headers       JSONB,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 添付ファイル
CREATE TABLE attachments (
    id             BIGSERIAL PRIMARY KEY,
    mail_id        BIGINT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    mime_type      TEXT NOT NULL,
    size_bytes     BIGINT NOT NULL,
    sha256         TEXT NOT NULL,             -- 同一ファイルの再抽出を避ける
    storage_path   TEXT NOT NULL,
    content_id     TEXT,                      -- 本文埋め込み画像の Content-ID
    UNIQUE (mail_id, sha256)
);

-- ② 抽出結果
CREATE TABLE properties (
    id                 BIGSERIAL PRIMARY KEY,
    mail_id            BIGINT NOT NULL REFERENCES mail_messages(id),
    -- 検索・並び替え・集計に使う確定値。JSONB だけにすると一覧画面が作れない
    property_name      TEXT,
    property_type      TEXT,
    deal_type          TEXT,
    address            TEXT,
    price              BIGINT,
    monthly_rent       INTEGER,
    exclusive_area_sqm NUMERIC(10, 2),
    built_year_month   CHAR(7),
    latitude           DOUBLE PRECISION,
    longitude          DOUBLE PRECISION,
    -- 確信度・根拠・要確認フラグを含む全項目。カラムだけにすると根拠が消える
    fields             JSONB NOT NULL,
    stations           JSONB NOT NULL DEFAULT '[]',
    images             JSONB NOT NULL DEFAULT '[]',
    review_status      TEXT NOT NULL DEFAULT '要確認',
    extraction_model   TEXT,
    prompt_version     TEXT,                  -- 精度比較のために必ず残す
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mail_id)
);
CREATE INDEX ON properties USING GIN (fields jsonb_path_ops);
CREATE INDEX ON properties (review_status, created_at DESC);

-- 人が直した履歴。精度改善の教師データになる
CREATE TABLE property_revisions (
    id           BIGSERIAL PRIMARY KEY,
    property_id  BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    field_key    TEXT NOT NULL,
    old_value    JSONB,
    new_value    JSONB,
    edited_by    TEXT NOT NULL,
    edited_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason       TEXT
);

-- 実行単位
CREATE TABLE jobs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,              -- 'ingest' | 'reprocess' | 'render'
    status        TEXT NOT NULL DEFAULT 'queued',
    triggered_by  TEXT NOT NULL,              -- 'ui' | 'schedule' | 'api'
    params        JSONB NOT NULL DEFAULT '{}',
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    error         TEXT
);

CREATE TABLE job_steps (
    id          BIGSERIAL PRIMARY KEY,
    job_id      BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    mail_id     BIGINT REFERENCES mail_messages(id),
    step        TEXT NOT NULL,                -- fetch/extract/geocode/sync/render
    status      TEXT NOT NULL,
    detail      JSONB,
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

-- ⑤ 生成物
CREATE TABLE generated_documents (
    id            BIGSERIAL PRIMARY KEY,
    property_id   BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    template_key  TEXT NOT NULL,              -- 'mysoku' | 'summary'
    storage_path  TEXT NOT NULL,
    data_hash     TEXT NOT NULL,              -- 生成時のデータのハッシュ
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`data_hash` があると「データが変わっていないのに再生成した」を検出できる。
物件が 100 件あるときの一括再生成で効く。

## オーケストレーション

```python
# app/services/pipeline.py
def process_mail(mail_id: int, job_id: int) -> None:
    with step(job_id, mail_id, "extract"):
        result = extraction.run(mail_id)          # ②
    with step(job_id, mail_id, "geocode"):
        result = geo.enrich(result)               # ④
    with step(job_id, mail_id, "persist"):
        property_id = repository.upsert(mail_id, result)
    with step(job_id, mail_id, "sync"):
        sheets.upsert_row(property_id)            # ③
    with step(job_id, mail_id, "render"):
        for template_key in ("mysoku", "summary"):
            pptx.render(property_id, template_key)  # ⑤
```

`step()` は `job_steps` への記録と例外の捕捉を兼ねるコンテキストマネージャにする。
1件の失敗で全体を止めない。1通ごとに独立して成否を記録し、最後にまとめて報告する。

## 認証情報の扱い

- サービスアカウント JSON・OAuth トークンはリポジトリに置かない。Docker Secret
  かクラウドの Secret Manager をマウントする。
- Google Maps の API キーはサーバ専用キーにし、コンソールで API 種別を
  Geocoding / Places / Distance Matrix に制限する。フロントエンドに渡さない。
- Gmail のスコープは `gmail.readonly` と、処理済みラベル付与に必要な
  `gmail.modify` まで。`gmail.send` は要らない。
