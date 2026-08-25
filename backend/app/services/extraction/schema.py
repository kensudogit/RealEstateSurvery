"""抽出用 JSON Schema の生成と、モデルへ渡すシステムプロンプト。

output_config.format に JSON Schema を渡すと、返ってくる最初の text ブロックが
必ずそのスキーマに適合した JSON になる。パースの防御コードが要らなくなる。
"""

from __future__ import annotations

from typing import Any

from app.services.extraction import fields as field_defs

SYSTEM_PROMPT = """\
あなたは日本の不動産会社で物件資料を読み取る担当者です。元付業者から届いた
メール本文・マイソク（販売図面）・物件概要書・写真から、指定された項目を
そのまま転記します。

守ること:

1. 書かれていない項目は必ず null にする。相場や常識から補わない。
   この出力は顧客に渡す資料になるため、間違った値は空欄よりはるかに有害です。
   「たぶんこうだろう」は null にして、確信度を下げてください。
2. evidence には原文の文字列をそのまま入れる。要約・言い換えをしない。
   後で人間が原文と突き合わせて検算するためのものです。
3. 単位は指定どおりに揃える。
   - 金額は円。「4,800万円」→ 48000000。「4.8億」→ 480000000。
   - 面積は㎡。坪表記は 1坪=3.305785㎡、帖は 1帖=1.62㎡ で換算する。
     換算した場合は evidence に元の表記（例:「45.5坪」）を残す。
   - 利回りは % の数値のみ。「7.2％」→ 7.2。
4. 築年月は西暦 YYYY-MM。和暦（平成5年3月、H5.3、S60年築）は換算する。
   月が不明なら "1993-01" のような捏造をせず null にする。
5. 複数物件が1通に含まれる場合は、最も情報量の多い1件だけを対象にする。
   （複数件の分割は呼び出し側が行う）
6. 図面内の手書きメモ・スタンプ・FAX ヘッダは物件情報ではない。無視する。
7. 表の見出しと値がずれている図面がある。行と列の対応を必ず確認すること。

confidence の目安:
  1.0-0.9  数値・文字列が明記され、項目名も一致している
  0.9-0.7  記載はあるが表記ゆれや単位換算を挟んでいる
  0.7-0.5  複数箇所の記述から組み立てた、または読み取りにくい
  0.5未満  推測が入っている（この場合はむしろ null を検討する）
"""


def _value_schema(field: dict) -> dict:
    """項目定義から value のスキーマを作る。必ず null を許す。

    null を許さないと、読み取れなかった項目にモデルが何かを入れてくる。
    """
    ftype = field.get("type", "string")
    if ftype == "integer":
        return {"type": ["integer", "null"]}
    if ftype == "number":
        return {"type": ["number", "null"]}
    if ftype == "boolean":
        return {"type": ["boolean", "null"]}
    if ftype == "enum":
        return {"type": ["string", "null"], "enum": list(field["enum"]) + [None]}
    if ftype == "date_ym":
        return {"type": ["string", "null"],
                "description": "西暦の YYYY-MM 形式。和暦は換算すること"}
    return {"type": ["string", "null"]}


def _envelope_schema(field: dict) -> dict:
    unit = f"（単位: {field['unit']}）" if field.get("unit") else ""
    note = f" {field['note']}" if field.get("note") else ""
    return {
        "type": "object",
        "description": f"{field['label']}{unit}{note}",
        "properties": {
            "value": _value_schema(field),
            "confidence": {
                "type": "number",
                "description": "0.0-1.0。原文にはっきり書かれていれば高く、"
                               "推測が混じるほど低くする。値が null なら 0.0",
            },
            "evidence": {
                "type": ["string", "null"],
                "description": "根拠となった原文の文字列をそのまま。加工しない。"
                               "値が null なら null",
            },
        },
        "required": ["value", "confidence", "evidence"],
        "additionalProperties": False,
    }


def build_schema(file_names: list[str]) -> dict[str, Any]:
    """config/property_fields.json から抽出用 JSON Schema を組み立てる。"""
    definitions = field_defs.load_definitions()

    props: dict[str, Any] = {}
    for field in definitions["fields"]:
        if field.get("derived"):
            continue  # 緯度経度など、抽出ではなく後段で埋める項目
        props[field["key"]] = _envelope_schema(field)

    props["stations"] = {
        "type": "array",
        "description": "原文に書かれている交通アクセス。書かれていなければ空配列。"
                       "住所から推測してはいけない（後段で自動補完する）",
        "maxItems": field_defs.max_stations(),
        "items": {
            "type": "object",
            "properties": {
                "line": {"type": ["string", "null"], "description": "沿線名"},
                "station": {"type": ["string", "null"],
                            "description": "駅名（「駅」は付けない）"},
                "walk_minutes": {"type": ["integer", "null"]},
                "bus_minutes": {"type": ["integer", "null"]},
            },
            "required": ["line", "station", "walk_minutes", "bus_minutes"],
            "additionalProperties": False,
        },
    }

    props["images"] = {
        "type": "array",
        "description": "渡された画像ファイルの用途分類。渡していないファイル名を作らない",
        "maxItems": field_defs.max_images(),
        "items": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "enum": file_names or ["__none__"]},
                "role": {"type": "string", "enum": field_defs.image_roles()},
                "caption": {"type": ["string", "null"]},
            },
            "required": ["file", "role", "caption"],
            "additionalProperties": False,
        },
    }

    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
        "additionalProperties": False,
    }
