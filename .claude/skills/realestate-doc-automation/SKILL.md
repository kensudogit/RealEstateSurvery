---
name: realestate-doc-automation
description: 不動産物件情報の自動転記・物件資料の自動生成システムを設計・実装・テストするときに使用する。Gmail のラベル付きメールから本文・添付 PDF・画像を取得し、LLM／AI-OCR で物件情報を抽出して Google スプレッドシートへ自動転記し、最寄駅・徒歩分数を自動補完し、PowerPoint テンプレートへ差し込んで物件資料（マイソク／概要書）を生成する一連のパイプラインを扱う。「物件情報の転記」「マイソク自動生成」「物件資料の自動作成」「元付業者からのメールを取り込みたい」「PDF/画像から間取り・価格・所在地を抜き出したい」「最寄駅と徒歩分数を自動で入れたい」「PowerPoint テンプレートに差し込みたい」「Gmail のラベルからバッチ処理したい」といった話題が出たら、明示的に「不動産」と言われていなくても必ずこの Skill を参照する。Python(FastAPI) + Next.js/React/TypeScript + PostgreSQL 構成の規約・データモデル・抽出スキーマ・実行スクリプト・完了条件を含む。物件情報とは無関係な一般的な OCR や汎用スクレイピングだけの依頼には使用しない。
version: 1.0.0
---

# 不動産物件情報 自動転記・資料生成 Skill

## 1. この Skill が扱う範囲

元付業者から届く物件メールを、人手のコピペなしで「一覧表」と「物件資料」に変換するパイプラインを構築する。

```text
① Gmail（指定ラベル）からメール本文・添付を取得
        ↓
② 本文 / 添付PDF / 画像 から LLM・AI-OCR で物件情報を抽出
        ↓
③ Google スプレッドシートへ自動転記（1物件 = 1行）
        ↓
④ 所在地から最寄駅・徒歩分数を自動補完
        ↓
⑤ PowerPoint テンプレート（2種）へ情報と画像を差し込み、物件資料を生成
```

抽出できなかった項目は **推測で埋めない**。空欄のまま `要確認` フラグを立てて出力し、人が確認して直す。これがこのシステムの一番重要な設計方針で、理由は 2.1 に書いてある。

## 2. 先に理解しておくべき設計判断

実装に入る前にこの 3 つを押さえる。ここを外すと後から作り直しになる。

### 2.1 フィールドは「値」ではなく「値＋確信度＋根拠」で持つ

不動産資料の誤記は営業事故に直結する。価格を 1 桁間違えた資料が客先に出るくらいなら、空欄のまま「要確認」で出た方がはるかにマシ。だから抽出結果は生の値ではなく、項目ごとに次の封筒（envelope）で持つ。

```json
{ "price": { "value": 48000000, "confidence": 0.93, "evidence": "販売価格 4,800万円", "needs_review": false } }
```

- `value` … 取れなければ `null`。ゼロや空文字で埋めない（「0円」と「不明」は別物）。
- `confidence` … モデルの自己申告。0.0–1.0。
- `evidence` … 元の文書に書いてあった文字列そのまま。後から人が検算できる。
- `needs_review` … サーバ側で計算する（モデルには決めさせない）。`value is None` / `confidence < 閾値` / 必須項目の欠落 / 検算エラーのいずれかで `true`。

`needs_review` をモデルに出力させないのは、閾値や必須項目の定義を運用中に変えたくなるから。モデル出力は素材、判定はコード側の責務。

### 2.2 メールは何度でも再処理される前提で冪等にする

Gmail の同期は重複と再送が普通に起きる。`gmail_message_id` を一意キーにして、取り込み・抽出・転記・生成の各段を「同じ入力なら同じ結果に上書き」できるようにする。スプレッドシートも「毎回 append」ではなく「キー一致行があれば更新、なければ追記」で書く。追記オンリーにすると、再実行のたびに同じ物件が増えていく。

### 2.3 人手レビューを工程に組み込む

このシステムのゴールは「全自動」ではなく「確認だけで済む状態」。UI は実行ボタンだけでなく、`要確認` 項目を一覧して直せる画面を必ず持つ。修正した値は DB に戻し、資料を再生成できるようにする。修正ログは後の精度改善（プロンプト調整）の教師データにもなる。

## 3. 技術スタック

| 層 | 採用 |
|---|---|
| Backend | Python 3.12+ / FastAPI |
| 抽出 | Anthropic Claude API（`claude-opus-5`）。PDF・画像をそのまま入力できるので、別の OCR エンジンは原則不要 |
| Frontend | Next.js (App Router) + React + TypeScript |
| DB | PostgreSQL 16+（SQLAlchemy 2.x / Alembic） |
| ジョブ | Celery + Redis、または小規模なら FastAPI BackgroundTasks |
| 外部 API | Gmail API / Google Sheets API / Google Maps Platform |
| 資料生成 | python-pptx + Pillow |
| Container | Docker Compose |

## 4. ディレクトリ構成

```text
backend/
  app/
    api/routes/         # jobs, properties, documents, review
    core/               # config, logging, security
    models/             # SQLAlchemy models
    schemas/            # Pydantic schemas
    services/
      gmail/            # ①取得
      extraction/       # ②抽出（Claude API）
      geo/              # ④最寄駅・徒歩分数
      sheets/           # ③転記
      pptx/             # ⑤資料生成
      pipeline.py       # ①→⑤ のオーケストレーション
    workers/            # Celery tasks
  migrations/
  tests/
    golden/             # 正解データ付きサンプル（精度評価用）
frontend/
  app/                  # 実行ボタン / ジョブ一覧 / 要確認レビュー画面
  components/
  lib/api/
templates/
  mysoku_a4.pptx        # テンプレート1（マイソク）
  summary_a4.pptx       # テンプレート2（物件概要書）
```

## 5. パイプライン各段

各段の実装詳細は `references/` にある。着手する段のファイルだけ読めばよい。

| 段 | 内容 | 参照 |
|---|---|---|
| 全体 | アーキテクチャ・DB スキーマ・ジョブ状態遷移 | `references/01-architecture.md` |
| ① | Gmail ラベル取得・添付保存・冪等性・認証 | `references/02-gmail-ingest.md` |
| ② | Claude API での抽出、PDF/画像の渡し方、プロンプト、正規化・検算 | `references/03-extraction.md` |
| ④ | Google Maps での最寄駅・徒歩分数（規約に沿った分数計算） | `references/04-geo-station.md` |
| ③ | スプレッドシート列マッピングと upsert | `references/05-sheets-sync.md` |
| ⑤ | PowerPoint テンプレート差し込み・画像リサイズ配置 | `references/06-pptx-render.md` |
| UI | 実行ボタン・ジョブ監視・要確認レビュー画面 | `references/07-frontend-ui.md` |
| 品質 | ゴールデンセット・項目別精度・プロンプト調整・運用手順 | `references/08-testing-and-tuning.md` |

順番は ④ が ② の後、③ と ⑤ が ④ の後。③ と ⑤ は互いに独立なので並行してよい。

## 6. 同梱物

`assets/` は「正」となる定義。コード側でこれを読み込んで使い、定義をコードに二重に書かない。

| ファイル | 用途 |
|---|---|
| `assets/property_fields.json` | 抽出項目の唯一の定義。日本語ラベル・型・単位・必須フラグ・シート列・PPTX プレースホルダを持つ。抽出用 JSON Schema はここから生成する |
| `assets/column_map.example.yaml` | スプレッドシートのシート名・ヘッダ行・列順の設定例 |
| `assets/env.example` | 必要な環境変数一式 |

`scripts/` は単体で動く実装。まずこれを CLI で回して手元で結果を確認し、それから `services/` に組み込む形にすると立ち上がりが速い。

| スクリプト | 用途 |
|---|---|
| `scripts/extract_property.py` | 本文テキスト＋PDF＋画像 → 物件情報 JSON（envelope 形式）。`python scripts/extract_property.py --body body.txt --file a.pdf --file b.jpg --out out.json` |
| `scripts/nearest_station.py` | 住所 → 緯度経度・最寄駅・徒歩分数。`python scripts/nearest_station.py --address "東京都新宿区西新宿2-8-1"` |
| `scripts/pptx_fill.py` | テンプレート＋JSON＋画像 → 物件資料 pptx。`python scripts/pptx_fill.py --template templates/mysoku_a4.pptx --data out.json --images-dir ./img --out result.pptx` |

## 7. データモデルの要点

詳細な DDL は `references/01-architecture.md`。最低限この形は守る。

- `mail_messages` … `gmail_message_id` UNIQUE。ラベル、件名、差出人、受信日時、本文、処理状態。
- `attachments` … メールに紐づく添付。`sha256` を持ち、同一ファイルの再抽出を避ける。
- `properties` … 1物件1行。確定値カラム（検索・表示用）と `fields JSONB`（envelope 一式）の両方を持つ。JSONB だけにすると検索が地獄になり、カラムだけにすると根拠と確信度が消える。
- `property_revisions` … 人が直した履歴。誰がいつ何を何に変えたか。精度改善の資産になる。
- `jobs` / `job_steps` … 実行単位と各段の状態・エラー。再実行はここを起点にする。
- `generated_documents` … 生成した pptx/pdf のパス、使ったテンプレート、生成時のデータのハッシュ。

## 8. 実装順序

小さく縦に通してから横に広げる。①〜⑤を段ごとに完成させようとすると、最後まで動くものが見えない。

1. `assets/property_fields.json` を業務側と合意する。ここが決まらないと他が全部動かない。実際のメール 10 通を見ながら決める。
2. `scripts/extract_property.py` を手元のサンプル PDF で回し、抽出品質を目視確認する。
3. Gmail 取得（①）を実装し、DB に保存するところまで通す。
4. ②の結果を `properties` に保存し、④で駅を補完する。
5. ③スプレッドシート転記。ここで初めて業務側に見せられる状態になる。
6. ⑤PPTX 生成。テンプレートは 1 種で通してから 2 種目を足す。
7. UI（実行ボタン → 要確認レビュー）。
8. ゴールデンセットで項目別精度を測り、プロンプトを調整する。

## 9. 完了条件（Definition of Done）

- [ ] 指定ラベルのメールを取り込み、同じメールを 2 回流しても行が重複しない
- [ ] 添付 PDF・画像から抽出した各項目に `value` / `confidence` / `evidence` / `needs_review` が揃っている
- [ ] 抽出できなかった項目が空欄＋要確認で出力され、数値が捏造されていない（ゴールデンセットで捏造ゼロを確認）
- [ ] スプレッドシートが upsert で更新され、列マッピングが設定ファイル由来になっている
- [ ] 住所から最寄駅・徒歩分数が入り、徒歩分数が道路距離 80m=1分・端数切り上げで計算されている
- [ ] テンプレート 2 種で pptx が生成され、画像がアスペクト比を保って枠に収まっている
- [ ] UI から実行でき、要確認項目を修正して資料を再生成できる
- [ ] API キー・トークンがリポジトリに入っていない（`.env` と Secret Manager 経由）
- [ ] `tests/golden/` の項目別精度が計測でき、CI で回る

## 10. よくある落とし穴

- **PPTX のプレースホルダが置換されない** … python-pptx ではひとつの段落が複数 run に分割され、`{{price}}` が `{{pri` と `ce}}` に割れていることがある。段落単位でテキストを結合してから置換する。`scripts/pptx_fill.py` はこれを処理済み。
- **徒歩分数を所要時間から出してしまう** … 不動産の表示に関する公正競争規約では「道路距離 80m につき 1 分、端数切り上げ」。Distance Matrix の `duration` ではなく `distance` を使う。
- **面積の単位が混ざる** … 坪・㎡・帖が同じ文書に出る。DB は㎡固定で持ち、表示側で変換する。1坪=3.305785㎡、1帖=1.62㎡（不動産公正取引協議会基準）。
- **和暦の築年月** … 「平成5年3月築」「S60年築」が普通に来る。抽出時に西暦 `YYYY-MM` へ正規化する。
- **Gmail の添付が inline 画像** … 本文中の画像は `filename` が空のことがある。`Content-ID` で拾う。
- **スプレッドシートのレート制限** … 1物件ずつ `append` を呼ぶと 429 になる。バッチでまとめて `values.batchUpdate` する。
- **画像の向き** … スマホ撮影画像は EXIF Orientation で横倒しになる。Pillow の `ImageOps.exif_transpose()` を通してから配置する。
