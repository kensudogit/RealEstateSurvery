# 08. テスト・精度調整・運用

## ゴールデンセット

実際に届いたメール 20〜30 通を、正解データ付きで固定する。
これが無いと「プロンプトを直したら良くなった気がする」から先に進めない。

```text
tests/golden/
  case_001/
    meta.json          # 件名・差出人・受信日時
    body.txt
    attachments/mysoku.pdf
    expected.json      # 人が入力した正解
  case_002/
  ...
```

`expected.json` は envelope ではなく素の値だけでよい。
確信度は評価対象ではなく、値が合っているかだけを見る。

```json
{
  "property_name": "渋谷ハイツ",
  "price": 48000000,
  "exclusive_area_sqm": 62.5,
  "built_year_month": "1993-03",
  "floor_plan": "2LDK",
  "structure": null
}
```

**ケースは偏らせない。** 実務で来るものを網羅する。

- テキストだけのメール／PDF 添付／画像だけ（FAX スキャン）
- 売買／賃貸／収益一棟／土地
- 表がきれいなもの／罫線が省略されたもの／手書きメモ入り
- 和暦表記／坪表記／万円と円の混在
- 情報がほとんど無いスカスカの案件（← 捏造を検出するための重要ケース）

最後のケースを必ず入れる。情報が薄い資料でモデルが埋めにくるかどうかが、
このシステムの安全性を決める。

## 測る指標

項目ごとに 4 つに分類する。

| 分類 | 意味 |
|---|---|
| 正解 | 正解と一致 |
| 誤り | 値は出たが違う ← **最も有害** |
| 未取得 | null。正解には値がある |
| 正しく null | 正解も null |

見る指標:

- **捏造率** = 誤り件数 ÷ 全項目。**最優先で下げる。** 目標 0%
- **取得率** = 正解 ÷（正解＋誤り＋未取得）
- **要確認率** = needs_review が立った項目の割合。低すぎるとフラグが
  機能しておらず、高すぎると人の手間が減らない。実務では 10〜20% が目安
- **見逃し率** = 誤りのうち needs_review が立たなかったもの。
  **これが 0 に近くないと運用に乗らない。** 誤った値が確認されずに
  資料へ流れる経路そのもの

取得率より捏造率と見逃し率を優先する。取れない項目は人が入れれば済むが、
間違った値が黙って通ると事故になる。

## 評価スクリプト

```python
# tests/test_extraction_accuracy.py
import json, pathlib, pytest

CASES = sorted(pathlib.Path("tests/golden").glob("case_*"))

@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_no_fabrication(case, extraction_result):
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    actual = extraction_result(case)["fields"]

    fabricated = []
    for key, want in expected.items():
        got = actual.get(key, {}).get("value")
        if got is not None and want is not None and got != want:
            if not actual[key].get("needs_review"):
                fabricated.append((key, want, got))

    assert not fabricated, f"確認フラグ無しの誤りがある: {fabricated}"
```

API 呼び出しは高いので、`extraction_result` フィクスチャは
`tests/golden/<case>/_cached.json` にレスポンスをキャッシュし、
`--refresh-golden` を付けたときだけ実際に呼ぶ。CI は常にキャッシュを使い、
プロンプトを変えたときだけ手元でリフレッシュする。

## 精度が出ないときの手順

上から順に試す。下に行くほどコストが高い。

1. **`evidence` を読む。** 何を見て間違えたかが書いてある。原因の 8 割は
   ここで分かる。表の別の行を見ていた／単位を取り違えた／別物件の欄を見た。
2. **プロンプトに具体例を足す。** 抽象的な指示より、
   「4,800万円 → 48000000」のような実例が効く。
3. **項目の説明を直す。** JSON Schema の `description` はモデルが読む。
   `property_fields.json` の `note` に「〜の場合は null」まで書く。
4. **検算ルールを足す。** 直せない誤りは、せめて検出して人に回す。
5. **`effort` を上げる。** `high` → `xhigh`。表の読み取りが絡む案件で効く。
6. **入力の渡し方を変える。** 画像を大きく／ページを分割して個別に呼ぶ。
   PDF 1 ファイルに 10 物件入っている、のようなケースはここで解決する。

プロンプトを変えたら `prompt_version` を上げ、ゴールデンセットの結果を
バージョンごとに残す。「前より良くなった」を数字で言えるようにする。

## 運用開始時の進め方

いきなり全自動にしない。段階を踏む。

1. **並走期間（2 週間程度）** … 従来どおり手入力もしつつ、システムの出力と
   突き合わせる。この期間の差分がそのままゴールデンセットに追加される。
2. **確認前提運用** … システムが入力し、担当者が要確認項目だけ確認する。
   ここが実質的なゴール。
3. **確信度の高い項目だけ自動確定** … 十分な実績が貯まってから。
   物件名・所在地のような外しにくい項目から順に。

## 操作説明の内容

導入時に業務側へ伝えるべきこと。

- ラベルの付け方（フィルタの設定方法を画面つきで）
- 実行ボタンの押し方と、処理にかかる時間の目安
- **要確認マークの意味と、確認せずに資料を出してはいけない理由**
- 直した内容が次回以降の精度改善に使われること
- うまく取れなかったメールの報告先（そのままゴールデンセット候補になる）

3 つ目が一番大事で、ここが伝わっていないと `※要確認` 付きの資料が
そのまま客先に出る。

## 監視

- ジョブの失敗率と、失敗した段の内訳
- 1 通あたりの Claude API トークン数とコスト（`usage` を `job_steps.detail` に残す）
- `cache_read_input_tokens` が 0 でないこと（0 ならキャッシュが効いていない）
- 要確認率の推移。急に跳ねたら、新しい形式のメールが来ている合図
