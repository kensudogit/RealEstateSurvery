# 05. Google スプレッドシートへの転記（③）

## upsert にする

「毎回 append」にすると、再実行のたびに同じ物件が増える。メール取得は
必ず再実行されるので、これは事故ではなく仕様上の必然。

```python
def upsert_row(service, config: dict, row: dict) -> None:
    key_column = config["key_column"]          # 例: "メールID"
    key_value = row[key_column]

    existing = find_row_index(service, config, key_column, key_value)
    values = [to_cell_values(row, config["columns"])]

    if existing is None:
        service.spreadsheets().values().append(
            spreadsheetId=config["spreadsheet_id"],
            range=f"{config['worksheet']}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
    else:
        service.spreadsheets().values().update(
            spreadsheetId=config["spreadsheet_id"],
            range=f"{config['worksheet']}!A{existing}",
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
```

キー列はメール ID にする。物件名や住所をキーにすると、表記ゆれで
別物件と判定されたり、同じマンションの別部屋が同一視されたりする。

## 列マッピングは設定ファイルに置く

列の追加・並べ替えは業務側の都合で頻繁に起きる。コードにハードコードすると
そのたびにデプロイが要る。`assets/column_map.example.yaml` の形で外に出す。

起動時に**ヘッダ行を読んで設定と突き合わせる**。人がシート上で列を入れ替えても
壊れないよう、ヘッダ名から列位置を引く。

```python
def resolve_columns(service, config: dict) -> list[str | None]:
    header = service.spreadsheets().values().get(
        spreadsheetId=config["spreadsheet_id"],
        range=f"{config['worksheet']}!{config['header_row']}:{config['header_row']}",
    ).execute().get("values", [[]])[0]

    # 設定に無いヘッダは触らない（人が手で足した列を壊さないため）
    return [next((c["field"] for c in config["columns"] if c["header"] == h), None)
            for h in header]
```

設定にある列がシートに無い場合は、勝手に足さずエラーにして人に知らせる。
黙って追加すると、共有シートに知らない列が生えて混乱する。

## 値の書き方

`values` に `null` を渡すとセルが空になり、「未取得」と「もともと空欄」の
区別が付かない。`null_placeholder`（既定 `―`）を入れておくと、シートを見た人が
「AI が取れなかった」と分かる。

数値は文字列にせず数値のまま渡し、書式は `spreadsheets.batchUpdate` の
`repeatCell` でセル書式として設定する。`"4,800万円"` のような文字列を入れると
シート上で並べ替え・集計ができなくなる。

## 要確認セルを目立たせる

`needs_review` が立った項目は背景色を変える。人がシートを開いた瞬間に
どこを見ればいいか分かる。

```python
requests = [{
    "repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r + 1,
                  "startColumnIndex": c, "endColumnIndex": c + 1},
        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.8}}},
        "fields": "userEnteredFormat.backgroundColor",
    }
}]
service.spreadsheets().batchUpdate(
    spreadsheetId=spreadsheet_id, body={"requests": requests}
).execute()
```

`evidence` はセルのメモ（note）に入れると、セルにカーソルを合わせるだけで
根拠が読めて確認が速い。

```python
{"repeatCell": {"range": {...},
                "cell": {"note": f"根拠: {evidence}\n確信度: {confidence}"},
                "fields": "note"}}
```

## レート制限

Sheets API は 1 分あたり 60 リクエスト／ユーザー。1 物件ごとに
`append` + `batchUpdate` を呼ぶと、30 物件で当たる。

- 複数物件はまとめて 1 回の `values.batchUpdate` で書く
- 書式・メモの更新も `spreadsheets.batchUpdate` に requests を積んで 1 回にする
- それでも 429 が出たら指数バックオフでリトライする

## 共有と権限

サービスアカウントで書き込む場合、対象スプレッドシートをその
サービスアカウントのメールアドレスに**編集者として共有**する必要がある。
これを忘れて `403` で悩むのが最頻出のつまずき。

シート自体は業務側が自由に触るものなので、システム側は「自分が管理する列」
だけを更新し、人が足した列・行・フィルタ・条件付き書式は壊さない。
