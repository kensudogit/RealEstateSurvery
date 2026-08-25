"""Google スプレッドシートへの転記（③）。

「毎回 append」にすると、再実行のたびに同じ物件が増える。メール取得は
必ず再実行されるので、これは事故ではなく仕様上の必然。必ず upsert にする。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import yaml
from googleapiclient.discovery import build
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.services.google_auth import GoogleAuthError, load_credentials

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
REVIEW_HIGHLIGHT = {"red": 1.0, "green": 0.95, "blue": 0.8}


class SheetsError(RuntimeError):
    pass


@lru_cache
def load_column_map() -> dict[str, Any]:
    """列マッピング。列の追加・並べ替えは業務側の都合で頻繁に起きるので、
    コードにハードコードせず設定ファイルから読む。"""
    path = get_settings().sheets_column_map
    if not path.exists():
        raise SheetsError(f"列マッピングが見つかりません: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_service():
    try:
        return build("sheets", "v4", credentials=load_credentials(SCOPES),
                     cache_discovery=False)
    except GoogleAuthError as exc:
        raise SheetsError(str(exc)) from exc


_retry = retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)


# --------------------------------------------------------------------------
# 値の取り出し
# --------------------------------------------------------------------------

def resolve_field(data: dict[str, Any], path: str) -> tuple[Any, bool]:
    """列定義の field 表記から値と needs_review を引く。

    - "price"                → fields.price
    - "stations.0.station"   → stations[0]["station"]
    - "meta.review_status"   → meta.review_status
    """
    if path.startswith("meta."):
        return (data.get("meta") or {}).get(path[5:]), False

    if path.startswith("stations."):
        _, index, key = path.split(".", 2)
        stations = data.get("stations") or []
        position = int(index)
        if position >= len(stations):
            return None, False
        return stations[position].get(key), False

    envelope = (data.get("fields") or {}).get(path)
    if not isinstance(envelope, dict):
        return None, False
    return envelope.get("value"), bool(envelope.get("needs_review"))


def to_cell_values(data: dict[str, Any], columns: list[dict],
                   null_placeholder: str) -> tuple[list[Any], list[int]]:
    """1 行分のセル値と、要確認セルの列インデックスを返す。

    数値は文字列にせず数値のまま渡す。"4,800万円" のような文字列を入れると
    シート上で並べ替え・集計ができなくなる。
    """
    values: list[Any] = []
    review_columns: list[int] = []

    for index, column in enumerate(columns):
        value, needs_review = resolve_field(data, column["field"])
        # null を空文字にすると「未取得」と「もともと空欄」が区別できない。
        values.append(null_placeholder if value is None else value)
        if needs_review:
            review_columns.append(index)

    return values, review_columns


# --------------------------------------------------------------------------
# 読み書き
# --------------------------------------------------------------------------

@_retry
def _sheet_id(service, spreadsheet_id: str, worksheet: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == worksheet:
            return sheet["properties"]["sheetId"]
    raise SheetsError(f"シートが見つかりません: {worksheet}")


@_retry
def _read_header(service, config: dict) -> list[str]:
    row = config["header_row"]
    response = service.spreadsheets().values().get(
        spreadsheetId=config["spreadsheet_id"],
        range=f"{config['worksheet']}!{row}:{row}",
    ).execute()
    return (response.get("values") or [[]])[0]


def verify_header(service, config: dict) -> None:
    """設定にある列がシートに無ければエラーにする。

    黙って列を足すと、共有シートに知らない列が生えて業務側が混乱する。
    人が手で足した列は設定に無くても触らない。
    """
    header = _read_header(service, config)
    missing = [c["header"] for c in config["columns"] if c["header"] not in header]
    if missing:
        raise SheetsError(
            f"シートに存在しない列が設定されています: {', '.join(missing)}。"
            "シート側に列を追加するか、column_map.yaml を直してください"
        )


@_retry
def find_row_index(service, config: dict, key_value: str) -> int | None:
    """キー列を全件読んで行番号（1 始まり）を返す。見つからなければ None。"""
    header = _read_header(service, config)
    key_column = config["key_column"]
    if key_column not in header:
        raise SheetsError(f"キー列 {key_column} がシートにありません")

    column_letter = _column_letter(header.index(key_column))
    response = service.spreadsheets().values().get(
        spreadsheetId=config["spreadsheet_id"],
        range=f"{config['worksheet']}!{column_letter}:{column_letter}",
    ).execute()
    values = [row[0] if row else "" for row in response.get("values", [])]

    for offset, value in enumerate(values):
        if value == key_value:
            return offset + 1
    return None


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


@_retry
def upsert_row(service, data: dict[str, Any]) -> int:
    """キー一致行があれば更新、無ければ追記。行番号を返す。"""
    settings = get_settings()
    config = dict(load_column_map())
    config.setdefault("spreadsheet_id", settings.sheets_spreadsheet_id)
    config.setdefault("worksheet", settings.sheets_worksheet_name)
    config.setdefault("header_row", settings.sheets_header_row)

    rendering = config.get("rendering") or {}
    null_placeholder = rendering.get("null_placeholder", "―")
    columns = config["columns"]

    key_field = next(c["field"] for c in columns if c["header"] == config["key_column"])
    key_value, _ = resolve_field(data, key_field)
    if key_value is None:
        raise SheetsError(f"キー列の値がありません: {key_field}")

    values, review_columns = to_cell_values(data, columns, null_placeholder)
    existing = find_row_index(service, config, str(key_value))

    if existing is None:
        response = service.spreadsheets().values().append(
            spreadsheetId=config["spreadsheet_id"],
            range=f"{config['worksheet']}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()
        updated_range = response["updates"]["updatedRange"]
        row_number = int(updated_range.split("!")[1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    else:
        row_number = existing
        service.spreadsheets().values().update(
            spreadsheetId=config["spreadsheet_id"],
            range=f"{config['worksheet']}!A{row_number}",
            valueInputOption="USER_ENTERED",
            body={"values": [values]},
        ).execute()

    _highlight_review_cells(service, config, data, row_number, review_columns)
    return row_number


def _highlight_review_cells(service, config: dict, data: dict,
                            row_number: int, review_columns: list[int]) -> None:
    """要確認セルに色を付け、根拠をセルのメモに入れる。

    シートを開いた瞬間にどこを見ればいいか分かる。メモに evidence を
    入れておくと、カーソルを合わせるだけで確認が済む。
    """
    if not review_columns:
        return

    sheet_id = _sheet_id(service, config["spreadsheet_id"], config["worksheet"])
    columns = config["columns"]
    requests_body = []

    for index in review_columns:
        envelope = (data.get("fields") or {}).get(columns[index]["field"]) or {}
        note_lines = []
        if envelope.get("evidence"):
            note_lines.append(f"根拠: {envelope['evidence']}")
        if envelope.get("confidence") is not None:
            note_lines.append(f"確信度: {envelope['confidence']}")
        note_lines.extend(envelope.get("review_reasons") or [])

        requests_body.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_number - 1, "endRowIndex": row_number,
                    "startColumnIndex": index, "endColumnIndex": index + 1,
                },
                "cell": {
                    "userEnteredFormat": {"backgroundColor": REVIEW_HIGHLIGHT},
                    "note": "\n".join(note_lines) or None,
                },
                "fields": "userEnteredFormat.backgroundColor,note",
            }
        })

    # 書式とメモは 1 回の batchUpdate にまとめる。セルごとに呼ぶと
    # 30 物件ほどでレート制限（60 リクエスト/分）に当たる。
    service.spreadsheets().batchUpdate(
        spreadsheetId=config["spreadsheet_id"], body={"requests": requests_body}
    ).execute()


def sync(data: dict[str, Any]) -> int:
    service = build_service()
    config = load_column_map()
    verify_header(service, {**config,
                            "spreadsheet_id": config.get("spreadsheet_id")
                            or get_settings().sheets_spreadsheet_id})
    return upsert_row(service, data)
