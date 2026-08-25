#!/usr/bin/env python3
"""Gmail / スプレッドシートのアクセストークンを作る。

    python tools/gmail_authorize.py --client-secrets ~/Downloads/client_secret.json

ブラウザが開いて Google の同意画面が出る。承認するとトークンが作られ、
デプロイ先の環境変数に貼る内容を表示する。

なぜローカルで実行するのか
--------------------------
OAuth は初回に必ず人の同意が要る。マネージド環境にはブラウザが無いので、
手元で 1 回だけ同意してトークンを作り、その中身を環境変数として渡す。
以降はリフレッシュトークンで自動更新されるため、再実行は不要。

個人の Gmail（@gmail.com）はこの方法しかない。サービスアカウントの
ドメイン全体の委任は Google Workspace の管理者権限が前提のため、
個人アカウントでは設定できない。

事前に必要なもの
----------------
1. Google Cloud Console でプロジェクトを作る
2. Gmail API と Google Sheets API を有効化する
3. OAuth 同意画面を設定し、テストユーザーに自分のアドレスを追加する
4. 認証情報 → OAuth クライアント ID →「デスクトップアプリ」を作成し、
   JSON をダウンロードする（それをこのスクリプトに渡す）

注意
----
同意画面が「テスト」状態のままだと、リフレッシュトークンは 7 日で失効する。
継続運用するなら、同意画面を「本番」に切り替えること（内部利用であれば
審査は不要なことが多い）。

依存: google-auth-oauthlib
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 取り込みに必要な最小限。modify は処理済みラベルを付けるために要る。
# spreadsheets は③の転記用。使わないなら外してよい。
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="OAuth トークンを作る")
    parser.add_argument("--client-secrets", type=Path, required=True,
                        help="OAuth クライアント ID の JSON（デスクトップアプリ）")
    parser.add_argument("--out", type=Path, default=Path("secrets/gmail_token.json"),
                        help="トークンの保存先。既定は secrets/（.gitignore 済み）")
    parser.add_argument("--port", type=int, default=0,
                        help="コールバックの待ち受けポート。0 で自動")
    args = parser.parse_args()

    if not args.client_secrets.exists():
        raise SystemExit(f"クライアント ID の JSON が見つかりません: {args.client_secrets}")

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), SCOPES)
    print("ブラウザが開きます。承認してください…")
    # access_type=offline と prompt=consent を付けないとリフレッシュトークンが
    # 返らないことがある。2 回目以降の同意で特に起きる。
    credentials = flow.run_local_server(
        port=args.port, access_type="offline", prompt="consent"
    )

    if not credentials.refresh_token:
        raise SystemExit(
            "リフレッシュトークンが取得できませんでした。"
            "Google アカウントの「サードパーティ製アプリ」から本アプリのアクセスを"
            "解除してから、もう一度実行してください"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    token = credentials.to_json()
    args.out.write_text(token, encoding="utf-8")

    print()
    print(f"保存しました: {args.out}")
    print()
    print("--- デプロイ先の環境変数に設定する内容 ---")
    print("変数名: GOOGLE_OAUTH_TOKEN_JSON")
    print("値: 次の 1 行をそのまま貼り付けてください")
    print()
    # 改行が混ざると変数として貼りにくいので 1 行に潰す
    print(json.dumps(json.loads(token), ensure_ascii=False, separators=(",", ":")))
    print()
    print("ローカルで使う場合は .env に次を書いても動きます。")
    print(f"GOOGLE_OAUTH_TOKEN_PATH={args.out}")
    print()
    print("※ この出力にはリフレッシュトークンが含まれます。"
          "チャットや issue に貼らないでください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
