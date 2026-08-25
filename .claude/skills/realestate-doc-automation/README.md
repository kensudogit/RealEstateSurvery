# realestate-doc-automation

不動産物件情報の自動転記・資料生成システムを作るための Claude Code Skill。

Gmail の指定ラベル → AI 抽出 → Google スプレッドシート転記 → 最寄駅補完 →
PowerPoint 資料生成、までの一連のパイプラインの設計規約・データ定義・
実行スクリプトが入っている。

## 使い方

`C:\devlop\RealEstate` を作業ディレクトリにして Claude Code を起動すると
自動で読み込まれる。「物件メールから資料を自動生成したい」のような依頼で
発火する。手動で呼ぶ場合は `/realestate-doc-automation`。

## 中身

```text
SKILL.md          設計方針・全体フロー・実装順序・完了条件
assets/
  property_fields.json      抽出項目の唯一の定義（ここを直せば全体に反映される）
  column_map.example.yaml   スプレッドシート列マッピングの例
  env.example               必要な環境変数
scripts/
  extract_property.py       本文+PDF+画像 → 物件情報 JSON
  nearest_station.py        住所 → 最寄駅・徒歩分数
  pptx_fill.py              テンプレート + JSON → 物件資料 pptx
  requirements.txt
references/
  01-architecture.md        構成・DB スキーマ・ジョブ状態遷移
  02-gmail-ingest.md        Gmail 取得・添付・冪等性
  03-extraction.md          Claude API での抽出・要確認判定・検算
  04-geo-station.md         最寄駅と徒歩分数（公正競争規約準拠）
  05-sheets-sync.md         スプレッドシート upsert
  06-pptx-render.md         PowerPoint 差し込み・画像配置
  07-frontend-ui.md         実行 UI とレビュー画面
  08-testing-and-tuning.md  ゴールデンセット・精度指標・運用
```

## スクリプトを単体で試す

```bash
pip install -r scripts/requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...
python scripts/extract_property.py --body body.txt --file mysoku.pdf --out out.json

export GOOGLE_MAPS_API_KEY=AIza...
python scripts/nearest_station.py --data out.json --out out.json

python scripts/pptx_fill.py --template templates/mysoku_a4.pptx \
    --data out.json --images-dir ./attachments --out 資料.pptx
```

## 設計の要点

抽出できなかった項目は推測で埋めず、空欄のまま `要確認` フラグを立てる。
不動産資料の誤記は営業事故に直結するため、間違った値より空欄の方が安全という
判断に全体が従っている。詳細は SKILL.md の 2 章。
