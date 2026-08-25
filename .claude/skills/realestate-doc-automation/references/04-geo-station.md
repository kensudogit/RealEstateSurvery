# 04. 最寄駅・徒歩分数の自動取得（④）

実装は `scripts/nearest_station.py`。

## 徒歩分数の計算規則

「不動産の表示に関する公正競争規約」施行規則により、

> 徒歩による所要時間は、**道路距離 80 メートルにつき 1 分間**を要するものとして
> 算出した数値を表示する。1 分未満の端数は**切り上げる**。

つまり `ceil(道路距離m / 80)`。**実際の所要時間ではない。**
Distance Matrix / Routes API は `duration`（所要時間）と `distance`（距離）の
両方を返すが、使うのは **`distance` の方**。ここを取り違えると、信号待ちや
勾配が加味された値になって広告表記として不適合になる。

坂道・歩道橋・踏切の待ち時間は考慮しない規約なので、API の徒歩ルート距離を
そのまま使ってよい。

## 処理の流れ

```text
住所文字列
  → Geocoding API           緯度経度・正規化住所・特定精度
  → Places API (New)        半径2km の駅を距離順に（出入口の重複を除く）
  → Distance Matrix API     各駅までの徒歩ルート距離（m）
  → ceil(距離 / 80)         徒歩分数
```

Places の「距離順」は直線距離なので、順位付けにだけ使う。線路や川を挟むと
直線で近い駅が徒歩では遠い、が普通に起きるため、最終的な並びは
道路距離で付け直す。

## 抽出値を上書きしない

元付業者が書いた沿線・駅・徒歩分数がある場合は、**それを正**とする。
API 由来の値で上書きしない。理由は 2 つ。

1. 業者が書いた分数は物件の正面出入口からの実測であることが多く、
   Geocoding が当てた座標より正確。
2. 広告表記は元付の記載に揃える商慣習があり、勝手に変えるとトラブルになる。

自動取得は「書かれていない分を埋める」ためのもの。埋めたものは
`source: "geo"` を付けて区別できるようにしておく。

## 沿線名は推測しない

Places API は駅名は返すが、沿線名を確実には返さない。「渋谷」には
JR・東急・東京メトロが乗り入れており、駅名から沿線を推測すると必ず外す。

選択肢は 3 つ。

| 方法 | 内容 |
|---|---|
| null のまま要確認（既定） | 人が入れる。件数が少ないなら一番安全 |
| 国土数値情報の鉄道データを取り込む | 駅名＋座標→路線名のマスタを PostgreSQL に持つ。無料・オフライン。表記ゆれの正規化が要る |
| Places の詳細を引く | `places.displayName` の別名や `addressComponents` から拾えることがあるが、網羅性が低い |

件数が増えてきたら 2 番目に移行する。国土数値情報の「鉄道（N02）」に
路線名・事業者名・駅座標が入っており、駅座標との最近傍マッチで沿線が引ける。

## Geocoding の精度を見る

`geometry.location_type` を必ず記録する。

| 値 | 意味 | 扱い |
|---|---|---|
| `ROOFTOP` | 建物単位で特定 | そのまま使える |
| `RANGE_INTERPOLATED` | 番地の範囲から補間 | ほぼ使える |
| `GEOMETRIC_CENTER` | 街区・道路の中心 | 誤差 100m 程度。要確認 |
| `APPROXIMATE` | 町丁目レベル | 徒歩分数が数分ずれる。必ず要確認 |

`partial_match: true` も同様。「東京都渋谷区渋谷1-1」のように号が無い住所は
`APPROXIMATE` になりやすく、その状態で出した徒歩分数を資料に載せると
後で修正が入る。フラグを立てて人に回す。

## キャッシュ

住所は繰り返し同じものが来る（同じマンションの別部屋など）。
正規化住所をキーにしたキャッシュテーブルを持つと、API 課金が大きく減る。

```sql
CREATE TABLE geo_cache (
    address_key   TEXT PRIMARY KEY,        -- 正規化・空白除去した住所
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    formatted     TEXT NOT NULL,
    accuracy      TEXT,
    stations      JSONB NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

TTL は 90 日程度。新駅開業・駅名改称があるので永久キャッシュにはしない。

## 広告表記用の住所

物件資料に載せる所在地は、番地・号を伏せて「丁目まで」にする運用が多い
（元付の許可なく詳細住所を出さない商慣習）。`display_address()` が
`^(.*?[0-9０-９]+丁目)` で丸めている。丁目が無い地域はそのまま返す。
この方針は会社によって違うので、導入時に必ず確認する。

## API の選択

Distance Matrix API と Places API の旧版は、後継 API（Routes API /
Places API (New)）への移行が進んでいる。`scripts/nearest_station.py` は
Places は新版、距離は Distance Matrix を使っている。Routes API に
揃える場合は次に差し替える。取得する値（距離）は同じ。

```text
POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix
  X-Goog-FieldMask: originIndex,destinationIndex,distanceMeters,condition
  body: { origins:[...], destinations:[...], travelMode: "WALK" }
```

コンソールでは使う API だけを有効化し、API キーに「API の制限」を掛ける。
キーが漏れたときの被害が桁で変わる。
