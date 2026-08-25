# PowerPoint テンプレート

`mysoku_a4.pptx` と `summary_a4.pptx` は雛形です。営業が普段使っている pptx を
そのまま流用するのが本筋なので、体裁は差し替えてください。差し込みが効くために
必要な約束ごとは 2 つだけです。

## 1. テキストは二重波かっこ

```text
{{property_name}}
所在地  {{address_display}}
交通    {{access_1}}
価格    {{price}}
```

キーは `config/property_fields.json` の `pptx` の値。`{{meta.review_status}}` で
メタ情報も差し込めます。単位（万円・㎡）はコード側で付くので、テンプレートには
書かないでください。テンプレートを差し替えても表記が揺れなくなります。

利用できるキーの一覧は次で確認できます。

```bash
python tools/make_starter_templates.py --out /tmp/x
```

## 2. 画像枠は図形の「名前」

画像を置きたい位置に四角形を置き、その図形の**名前**を次のいずれかにします。
図形の位置とサイズがそのまま画像の枠になります。

| 図形名 | 入る画像 |
|---|---|
| `IMG:exterior` | 外観写真 |
| `IMG:floor_plan` | 間取り図 |
| `IMG:map` | 地図 |
| `IMG:interior` | 室内写真 |

名前の変更は PowerPoint の
**[ホーム] → [選択] → [オブジェクトの選択と表示]** から行います。
テキストではなくオブジェクト名です（ここを間違えるとまったく効きません）。

画像は枠内にアスペクト比を保って収まり（contain）、中央寄せされます。
対応する画像が無い枠は既定では残ります。テンプレート側で「写真準備中」の体裁を
作り込んでいることが多く、消すとレイアウトが崩れるためです。

## 動作確認

```bash
python .claude/skills/realestate-doc-automation/scripts/pptx_fill.py \
    --template templates/mysoku_a4.pptx \
    --data out.json --images-dir ./img --out 確認用.pptx
```

テンプレートに書いたのに定義に無いキーは `[警告] 未定義のプレースホルダ` として
報告され、終了コードが 1 になります。誤字を放置すると `{{proprety_name}}` が
そのまま印刷された資料が客先に出るので、必ず潰してください。

## PDF 化

営業が実際に配るのは PDF であることが多いので、コンテナに LibreOffice と
`fonts-noto-cjk` を入れてあります。フォントが無いと全部豆腐（□）になります。
テンプレートで使っているフォントと同じものを入れないと、改行位置が変わります。
