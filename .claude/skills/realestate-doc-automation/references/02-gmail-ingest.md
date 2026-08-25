# 02. Gmail からの取得（①）

## 認証方式の選択

| 方式 | 向いているケース | 注意 |
|---|---|---|
| サービスアカウント＋ドメイン全体の委任 | Google Workspace で運用、共有の受信箱を定期巡回する | 管理コンソールでスコープの委任設定が必要。無人運転できる |
| OAuth ユーザー同意 | 個人 Gmail、小規模、担当者本人のメールボックス | リフレッシュトークンの失効に備えた再同意フローが要る |

必要なスコープはこの 2 つだけ。

```text
https://www.googleapis.com/auth/gmail.readonly   # 取得
https://www.googleapis.com/auth/gmail.modify     # 処理済みラベルの付与
```

## ラベル設計

3 つ用意すると運用が回る。

- `物件情報` … 取り込み対象。担当者がフィルタで自動付与する
- `物件情報/処理済` … 取り込み成功後に付ける
- `物件情報/エラー` … 失敗したもの。人が見て再実行する

取得クエリは「対象ラベルが付いていて、処理済みでもエラーでもないもの」。

```python
query = f"label:{TARGET} -label:{DONE} -label:{ERROR}"
```

`historyId` による差分同期もあるが、初期実装では上のクエリで十分。
ラベルを外して再取り込みできる方が、運用中の手戻りに強い。

## 取得の実装

```python
# app/services/gmail/client.py
from googleapiclient.discovery import build

def fetch_target_messages(service, query: str, max_results: int) -> list[dict]:
    """対象メールの ID 一覧。ページングを忘れると 100 通で止まる。"""
    messages, page_token = [], None
    while True:
        res = service.users().messages().list(
            userId="me", q=query, maxResults=min(max_results, 500),
            pageToken=page_token,
        ).execute()
        messages.extend(res.get("messages", []))
        page_token = res.get("nextPageToken")
        if not page_token or len(messages) >= max_results:
            break
    return messages[:max_results]


def fetch_message(service, message_id: str) -> dict:
    return service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
```

## 本文と添付の取り出し

MIME ツリーは再帰的に辿る。`multipart/alternative` の中に
`multipart/related` があり、その中に inline 画像がぶら下がる、という構造が普通。

```python
import base64

def walk_parts(part):
    yield part
    for child in part.get("parts", []) or []:
        yield from walk_parts(child)


def extract_body_and_attachments(service, message: dict):
    body_text, body_html, attachments = "", "", []

    for part in walk_parts(message["payload"]):
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        filename = part.get("filename") or ""
        headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}

        if mime == "text/plain" and not filename and body.get("data"):
            body_text += _decode(body["data"])
        elif mime == "text/html" and not filename and body.get("data"):
            body_html += _decode(body["data"])
        elif body.get("attachmentId"):
            # 本文中の inline 画像は filename が空のことがある。
            # Content-ID から名前を作らないと、後で画像を参照できなくなる。
            content_id = (headers.get("content-id") or "").strip("<>")
            if not filename:
                ext = mime.split("/")[-1].replace("jpeg", "jpg")
                filename = f"inline_{content_id or body['attachmentId'][:8]}.{ext}"
            attachments.append({
                "filename": filename, "mime_type": mime,
                "attachment_id": body["attachmentId"], "content_id": content_id,
            })

    return body_text, body_html, attachments


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
```

本文が HTML しか無いメールも多い。その場合は `body_html` からタグを落として
テキスト化する（`selectolax` か `beautifulsoup4`）。ただし表がレイアウトに
使われていることがあるので、`<tr>` / `<td>` は改行・タブに置き換えてから
落とすと、物件概要の表構造が残って抽出精度が上がる。

## 添付の保存と重複排除

```python
def download_attachment(service, message_id: str, attachment_id: str) -> bytes:
    res = service.users().messages().attachments().get(
        userId="me", messageId=message_id, id=attachment_id
    ).execute()
    return base64.urlsafe_b64decode(res["data"])
```

保存時に `sha256` を取り、`(mail_id, sha256)` の UNIQUE で重複を弾く。
転送メールでは同じ図面が何度も添付されるため、これが無いと同じ PDF を
何度も Claude API に投げることになる。

**署名画像・バナーを除外する。** 会社ロゴや「環境のため印刷は控えて」の
バナーが毎回添付として来る。次の条件で機械的に落とす。

- サイズが 20KB 未満の画像
- 幅か高さが 200px 未満
- 同じ `sha256` が過去 N 通で繰り返し出現している（差出人ごとの署名画像）

最後の条件が一番効く。`attachments` の `sha256` を差出人ドメインごとに
集計し、出現回数が閾値を超えたハッシュを「署名画像」として登録しておく。

## 冪等性と後始末

```python
def ingest_one(service, message_id: str) -> int | None:
    if repository.mail_exists(message_id):
        return None                       # 既に取り込み済み。何もしない
    message = fetch_message(service, message_id)
    mail_id = repository.save_mail(message)
    for attachment in ...:
        repository.save_attachment(mail_id, attachment)
    return mail_id
```

処理済みラベルは**パイプライン全体が成功してから**付ける。抽出の途中で
落ちたのに処理済みが付くと、そのメールは二度と拾われない。

```python
service.users().messages().modify(
    userId="me", id=message_id,
    body={"addLabelIds": [DONE_LABEL_ID], "removeLabelIds": []},
).execute()
```

## レート制限

Gmail API は 1 ユーザーあたり 250 quota units/秒。`messages.get` が 5 units、
`attachments.get` が 5 units なので、添付の多いメールを並列で回すとすぐ当たる。
指数バックオフ付きのリトライ（`googleapiclient` の `num_retries` ではなく
`tenacity` で 429/5xx を明示的に拾う）と、ワーカーの同時実行数を 2〜4 に
抑えるのが実際的。
