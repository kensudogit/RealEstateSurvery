#!/usr/bin/env python3
"""メール本文・添付PDF・画像から物件情報を抽出する（パイプライン②）。

    python scripts/extract_property.py \
        --fields assets/property_fields.json \
        --body body.txt \
        --file mysoku.pdf --file gaikan.jpg \
        --out out.json

出力は項目ごとの envelope 形式:
    {"price": {"value": 48000000, "confidence": 0.93,
               "evidence": "販売価格 4,800万円", "needs_review": false}, ...}

設計上の要点:
  * 抽出スキーマは assets/property_fields.json から生成する。項目定義を
    二重管理しないため。項目を足すときは JSON だけを直せばよい。
  * needs_review はモデルに決めさせず、ここで計算する。閾値も必須項目も
    運用中に変わるため、判定はコード側の責務に置く。
  * 値が読み取れないときは null を返させる。推測値を入れる方が空欄より
    危険なので、プロンプトでも強く指示している。

依存: anthropic>=1.0
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import anthropic

DEFAULT_MODEL = os.environ.get("EXTRACTION_MODEL", "claude-opus-5")
DEFAULT_EFFORT = os.environ.get("EXTRACTION_EFFORT", "high")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Windows の既定コンソール（cp932）で日本語や記号が化けないようにする。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp"}

# 和暦の元年（西暦）。「平成5年」→ 1988 + 5 = 1993。
ERA_BASE = {"令和": 2018, "R": 2018, "平成": 1988, "H": 1988,
            "昭和": 1925, "S": 1925, "大正": 1911, "T": 1911, "明治": 1867, "M": 1867}


# --------------------------------------------------------------------------
# スキーマ生成
# --------------------------------------------------------------------------

def _value_schema(field: dict) -> dict:
    """項目定義から value の JSON Schema を作る。必ず null を許す。"""
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


def build_schema(defs: dict, file_names: list[str]) -> dict:
    """property_fields.json から抽出用 JSON Schema を組み立てる。"""
    props: dict[str, Any] = {}
    for field in defs["fields"]:
        if field.get("derived"):
            continue  # 緯度経度など、抽出ではなく後段で埋める項目
        props[field["key"]] = _envelope_schema(field)

    stations = defs["repeated_fields"]["stations"]
    props["stations"] = {
        "type": "array",
        "description": "原文に書かれている交通アクセス。書かれていなければ空配列。"
                       "住所から推測してはいけない（後段で自動補完する）",
        "maxItems": stations["max_items"],
        "items": {
            "type": "object",
            "properties": {
                "line": {"type": ["string", "null"], "description": "沿線名"},
                "station": {"type": ["string", "null"], "description": "駅名（「駅」は付けない）"},
                "walk_minutes": {"type": ["integer", "null"]},
                "bus_minutes": {"type": ["integer", "null"]},
            },
            "required": ["line", "station", "walk_minutes", "bus_minutes"],
            "additionalProperties": False,
        },
    }

    images = defs["repeated_fields"]["images"]
    roles = next(f["enum"] for f in images["item_fields"] if f["key"] == "role")
    props["images"] = {
        "type": "array",
        "description": "渡された画像ファイルの用途分類。渡していないファイル名を作らない",
        "maxItems": images["max_items"],
        "items": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "enum": file_names or ["__none__"]},
                "role": {"type": "string", "enum": roles},
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
     換算した場合は evidence に元の表記（例: 「45.5坪」）を残す。
   - 利回りは % の数値のみ。「7.2％」→ 7.2。
4. 築年月は西暦 YYYY-MM。和暦（平成5年3月、H5.3、S60年築）は換算する。
   月が不明なら年だけ確実にして "1993-01" のような捏造をせず null にする。
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


# --------------------------------------------------------------------------
# 入力ブロック組み立て
# --------------------------------------------------------------------------

def build_content_blocks(body_text: str | None, files: list[Path]) -> list[dict]:
    """PDF・画像・本文を content blocks に変換する。

    document / image ブロックはテキストより前に置く。後ろに置くと
    「この文書について」という参照がモデル側で外れやすい。
    """
    blocks: list[dict] = []
    for path in files:
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            blocks.append({
                "type": "document",
                "title": path.name,
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            })
        elif suffix in IMAGE_MIME:
            blocks.append({"type": "text", "text": f"[次の画像のファイル名: {path.name}]"})
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": IMAGE_MIME[suffix], "data": data},
            })
        else:
            mime, _ = mimetypes.guess_type(path.name)
            raise SystemExit(f"未対応のファイル形式です: {path.name} ({mime})")

    instruction = "上記の資料から物件情報を抽出してください。"
    if body_text:
        instruction = (
            "以下はメール本文です。添付資料と合わせて物件情報を抽出してください。\n"
            "本文と添付で値が食い違う場合は、添付の販売図面・概要書を優先し、\n"
            "食い違ったこと自体を該当項目の evidence に書いてください。\n\n"
            "--- メール本文 ---\n" + body_text + "\n--- ここまで ---"
        )
    blocks.append({"type": "text", "text": instruction})
    return blocks


# --------------------------------------------------------------------------
# 正規化
# --------------------------------------------------------------------------

def normalize_wareki(text: str) -> str | None:
    """「平成5年3月」「H5.3」→ "1993-03"。判別できなければ None。"""
    if not text:
        return None
    m = re.search(r"(令和|平成|昭和|大正|明治|[RHSTM])\s*(\d{1,2}|元)\s*年\s*(\d{1,2})?\s*月?", text)
    if not m:
        return None
    era, year_raw, month = m.group(1), m.group(2), m.group(3)
    year = 1 if year_raw == "元" else int(year_raw)
    seireki = ERA_BASE[era] + year
    return f"{seireki:04d}-{int(month):02d}" if month else None


def to_halfwidth(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def normalize_fields(fields: dict, defs: dict) -> dict:
    """モデル出力を DB に入れられる形へ整える。"""
    by_key = {f["key"]: f for f in defs["fields"]}

    for key, env in fields.items():
        if not isinstance(env, dict) or "value" not in env:
            continue
        value, spec = env["value"], by_key.get(key, {})
        if value is None:
            continue

        if spec.get("type") == "date_ym" and isinstance(value, str):
            if not re.fullmatch(r"\d{4}-\d{2}", value):
                converted = normalize_wareki(value)
                env["value"] = converted
                if converted is None:
                    env["confidence"] = 0.0

        if key == "floor_plan" and isinstance(value, str):
            env["value"] = to_halfwidth(value).upper().replace(" ", "")

        if spec.get("type") in ("integer", "number") and isinstance(value, str):
            digits = re.sub(r"[^\d.\-]", "", to_halfwidth(value))
            env["value"] = (int(float(digits)) if spec["type"] == "integer" else float(digits)) if digits else None

    return fields


# --------------------------------------------------------------------------
# 検算と要確認判定
# --------------------------------------------------------------------------

def _val(fields: dict, key: str):
    env = fields.get(key)
    return env.get("value") if isinstance(env, dict) else None


def cross_check(fields: dict) -> dict[str, str]:
    """項目をまたいだ整合性チェック。{項目key: 理由} を返す。

    単発の項目だけ見ていると、単位の取り違えや桁ずれは confidence が高い
    まま通り抜ける。ここで捕まえるのが最後の砦になる。
    """
    issues: dict[str, str] = {}
    deal = _val(fields, "deal_type")
    price = _val(fields, "price")
    rent = _val(fields, "monthly_rent")

    if deal == "売買" and price is None:
        issues["price"] = "売買物件だが価格が取れていない"
    if deal == "賃貸" and rent is None:
        issues["monthly_rent"] = "賃貸物件だが賃料が取れていない"
    if deal == "売買" and isinstance(price, int) and 0 < price < 1_000_000:
        issues["price"] = f"価格 {price:,} 円は売買として低すぎる。万円→円の換算漏れの疑い"
    if isinstance(rent, int) and rent > 5_000_000:
        issues["monthly_rent"] = f"賃料 {rent:,} 円/月は高すぎる。年額を入れた疑い"

    yield_ = _val(fields, "gross_yield")
    if isinstance(yield_, (int, float)) and not (1.0 <= yield_ <= 30.0):
        issues["gross_yield"] = f"表面利回り {yield_}% が想定レンジ外"

    income = _val(fields, "annual_income_full")
    if all(isinstance(v, (int, float)) for v in (price, income, yield_)) and price:
        calculated = income / price * 100
        if abs(calculated - yield_) > 0.5:
            issues["gross_yield"] = (
                f"記載利回り {yield_}% と 年収÷価格 {calculated:.2f}% が一致しない"
            )

    ym = _val(fields, "built_year_month")
    if isinstance(ym, str) and re.fullmatch(r"\d{4}-\d{2}", ym):
        year = int(ym[:4])
        if not (1900 <= year <= date.today().year + 3):
            issues["built_year_month"] = f"築年 {year} が不自然"

    for key in ("land_area_sqm", "building_area_sqm", "exclusive_area_sqm"):
        area = _val(fields, key)
        if isinstance(area, (int, float)) and not (1.0 <= area <= 100_000.0):
            issues[key] = f"面積 {area}㎡ が想定レンジ外。坪との取り違えの疑い"

    exclusive = _val(fields, "exclusive_area_sqm")
    plan = _val(fields, "floor_plan")
    if isinstance(exclusive, (int, float)) and isinstance(plan, str):
        rooms = re.match(r"(\d+)", plan)
        if rooms and int(rooms.group(1)) >= 3 and exclusive < 40:
            issues["exclusive_area_sqm"] = f"{plan} に対して専有面積 {exclusive}㎡ は狭すぎる"

    return issues


def apply_review_flags(fields: dict, defs: dict) -> dict:
    """needs_review を確定させる。モデルではなくここが判定の主体。"""
    threshold = float(os.environ.get("EXTRACTION_CONFIDENCE_THRESHOLD",
                                     defs.get("review_confidence_threshold", 0.75)))
    required = {f["key"] for f in defs["fields"] if f.get("required")}
    issues = cross_check(fields)

    for key, env in fields.items():
        if not isinstance(env, dict) or "value" not in env:
            continue
        reasons: list[str] = []
        if env["value"] is None:
            reasons.append("必須項目が取れていない" if key in required else "値なし")
        elif float(env.get("confidence") or 0.0) < threshold:
            reasons.append(f"確信度 {env.get('confidence')} が閾値 {threshold} 未満")
        if key in issues:
            reasons.append(issues[key])

        # 任意項目の単なる「値なし」は要確認にしない。全部が要確認になると
        # フラグが意味を失い、人は見なくなる。
        flag = bool(reasons) and not (reasons == ["値なし"])
        env["needs_review"] = flag
        env["review_reasons"] = reasons if flag else []

    return fields


def summarize(fields: dict, defs: dict) -> dict:
    labels = {f["key"]: f["label"] for f in defs["fields"]}
    flagged = [labels.get(k, k) for k, v in fields.items()
               if isinstance(v, dict) and v.get("needs_review")]
    return {
        "review_status": "要確認" if flagged else "自動確定",
        "review_fields": "、".join(flagged),
        "review_count": len(flagged),
    }


# --------------------------------------------------------------------------
# API 呼び出し
# --------------------------------------------------------------------------

def extract(body_text: str | None, files: list[Path], defs: dict,
            model: str, effort: str, use_fallback: bool) -> dict:
    schema = build_schema(defs, [p.name for p in files])
    client = anthropic.Anthropic()

    # システムプロンプトは毎回同じなのでキャッシュする。添付が大きいときほど
    # 効くが、1024トークンに満たないと黙ってキャッシュされない点に注意。
    system = [{"type": "text", "text": SYSTEM_PROMPT,
               "cache_control": {"type": "ephemeral"}}]

    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": build_content_blocks(body_text, files)}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": schema}},
    )

    # 添付が大きいと入力が長くなるので streaming を使う（HTTP タイムアウト回避）。
    if use_fallback:
        with client.beta.messages.stream(
            betas=[FALLBACK_BETA], fallbacks="default", **kwargs
        ) as stream:
            message = stream.get_final_message()
    else:
        with client.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()

    if message.stop_reason == "refusal":
        detail = getattr(message, "stop_details", None)
        raise SystemExit(f"抽出が拒否されました: {detail}")

    text = next(b.text for b in message.content if b.type == "text")
    return json.loads(text)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="物件情報を抽出して envelope 形式の JSON を出力する")
    parser.add_argument("--fields", type=Path,
                        default=Path(__file__).resolve().parent.parent / "assets" / "property_fields.json")
    parser.add_argument("--body", type=Path, help="メール本文のテキストファイル")
    parser.add_argument("--file", type=Path, action="append", default=[],
                        help="添付 PDF / 画像。複数指定可")
    parser.add_argument("--out", type=Path, help="出力先。省略時は標準出力")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT,
                        choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--no-fallback", action="store_true",
                        help="拒否時のサーバサイドフォールバックを無効にする")
    args = parser.parse_args()

    defs = json.loads(args.fields.read_text(encoding="utf-8"))
    body = args.body.read_text(encoding="utf-8") if args.body else None
    files = [p for p in args.file]
    for path in files:
        if not path.exists():
            raise SystemExit(f"ファイルが見つかりません: {path}")
    if not body and not files:
        raise SystemExit("--body か --file のどちらかは必要です")

    raw = extract(body, files, defs, args.model, args.effort, not args.no_fallback)

    stations = raw.pop("stations", [])
    images = raw.pop("images", [])
    fields = apply_review_flags(normalize_fields(raw, defs), defs)

    result = {
        "fields": fields,
        "stations": [{**s, "source": "extracted"} for s in stations],
        "images": images,
        "meta": summarize(fields, defs),
    }

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
        print(f"{args.out} に出力しました（要確認 {result['meta']['review_count']} 項目）",
              file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
