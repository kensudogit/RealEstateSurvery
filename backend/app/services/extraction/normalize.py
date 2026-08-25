"""モデル出力の正規化・検算・要確認判定。

needs_review をモデルに決めさせないのは、閾値も必須項目も運用中に変わるから。
モデル出力は素材で、判定はコード側の責務にしておくと、判定基準を変えるたびに
再抽出（＝再課金）しなくて済む。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from app.services.extraction import fields as field_defs

# 和暦の元年（西暦）。「平成5年」→ 1988 + 5 = 1993。
ERA_BASE = {"令和": 2018, "R": 2018, "平成": 1988, "H": 1988,
            "昭和": 1925, "S": 1925, "大正": 1911, "T": 1911, "明治": 1867, "M": 1867}

WAREKI_RE = re.compile(r"(令和|平成|昭和|大正|明治|[RHSTM])\s*(\d{1,2}|元)\s*年\s*(\d{1,2})?\s*月?")
YM_RE = re.compile(r"\d{4}-\d{2}")


def normalize_wareki(text: str) -> str | None:
    """「平成5年3月」「H5.3」→ "1993-03"。判別できなければ None。"""
    if not text:
        return None
    match = WAREKI_RE.search(text)
    if not match:
        return None
    era, year_raw, month = match.group(1), match.group(2), match.group(3)
    year = 1 if year_raw == "元" else int(year_raw)
    return f"{ERA_BASE[era] + year:04d}-{int(month):02d}" if month else None


def to_halfwidth(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """DB に入れられる形へ整える。"""
    specs = field_defs.field_specs()

    for key, envelope in fields.items():
        if not isinstance(envelope, dict) or "value" not in envelope:
            continue
        value, spec = envelope["value"], specs.get(key, {})
        if value is None:
            continue

        if spec.get("type") == "date_ym" and isinstance(value, str):
            if not YM_RE.fullmatch(value):
                converted = normalize_wareki(value)
                envelope["value"] = converted
                if converted is None:
                    envelope["confidence"] = 0.0

        if key == "floor_plan" and isinstance(value, str):
            envelope["value"] = to_halfwidth(value).upper().replace(" ", "")

        # モデルが文字列で返してきた数値を拾う（"4,800万円" のような残骸）
        if spec.get("type") in ("integer", "number") and isinstance(value, str):
            digits = re.sub(r"[^\d.\-]", "", to_halfwidth(value))
            if digits:
                envelope["value"] = (
                    int(float(digits)) if spec["type"] == "integer" else float(digits)
                )
            else:
                envelope["value"] = None

    return fields


def _value(fields: dict, key: str) -> Any:
    envelope = fields.get(key)
    return envelope.get("value") if isinstance(envelope, dict) else None


def cross_check(fields: dict[str, Any]) -> dict[str, str]:
    """項目をまたいだ整合性チェック。{項目key: 理由} を返す。

    単発の項目だけ見ていると、単位の取り違えや桁ずれは confidence が高いまま
    通り抜ける。ここが最後の砦になる。業務側と相談してルールを足していくほど、
    人のレビュー時間が減る。
    """
    issues: dict[str, str] = {}
    deal = _value(fields, "deal_type")
    price = _value(fields, "price")
    rent = _value(fields, "monthly_rent")

    if deal == "売買" and price is None:
        issues["price"] = "売買物件だが価格が取れていない"
    if deal == "賃貸" and rent is None:
        issues["monthly_rent"] = "賃貸物件だが賃料が取れていない"
    if deal == "売買" and isinstance(price, int) and 0 < price < 1_000_000:
        issues["price"] = f"価格 {price:,} 円は売買として低すぎる。万円→円の換算漏れの疑い"
    if isinstance(rent, int) and rent > 5_000_000:
        issues["monthly_rent"] = f"賃料 {rent:,} 円/月は高すぎる。年額を入れた疑い"

    gross_yield = _value(fields, "gross_yield")
    if isinstance(gross_yield, (int, float)) and not (1.0 <= gross_yield <= 30.0):
        issues["gross_yield"] = f"表面利回り {gross_yield}% が想定レンジ外"

    income = _value(fields, "annual_income_full")
    if all(isinstance(v, (int, float)) for v in (price, income, gross_yield)) and price:
        calculated = income / price * 100
        if abs(calculated - gross_yield) > 0.5:
            issues["gross_yield"] = (
                f"記載利回り {gross_yield}% と 年収÷価格 {calculated:.2f}% が一致しない"
            )

    year_month = _value(fields, "built_year_month")
    if isinstance(year_month, str) and YM_RE.fullmatch(year_month):
        year = int(year_month[:4])
        if not (1900 <= year <= date.today().year + 3):
            issues["built_year_month"] = f"築年 {year} が不自然"

    for key in ("land_area_sqm", "building_area_sqm", "exclusive_area_sqm"):
        area = _value(fields, key)
        if isinstance(area, (int, float)) and not (1.0 <= area <= 100_000.0):
            issues[key] = f"面積 {area}㎡ が想定レンジ外。坪との取り違えの疑い"

    exclusive = _value(fields, "exclusive_area_sqm")
    floor_plan = _value(fields, "floor_plan")
    if isinstance(exclusive, (int, float)) and isinstance(floor_plan, str):
        rooms = re.match(r"(\d+)", floor_plan)
        if rooms and int(rooms.group(1)) >= 3 and exclusive < 40:
            issues["exclusive_area_sqm"] = (
                f"{floor_plan} に対して専有面積 {exclusive}㎡ は狭すぎる"
            )

    return issues


def apply_review_flags(fields: dict[str, Any]) -> dict[str, Any]:
    """needs_review を確定させる。"""
    threshold = field_defs.review_threshold()
    required = field_defs.required_keys()
    issues = cross_check(fields)

    for key, envelope in fields.items():
        if not isinstance(envelope, dict) or "value" not in envelope:
            continue
        reasons: list[str] = []
        if envelope["value"] is None:
            reasons.append("必須項目が取れていない" if key in required else "値なし")
        elif float(envelope.get("confidence") or 0.0) < threshold:
            reasons.append(f"確信度 {envelope.get('confidence')} が閾値 {threshold} 未満")
        if key in issues:
            reasons.append(issues[key])

        # 任意項目の単なる「値なし」は要確認にしない。全項目が要確認になると
        # フラグが情報を持たなくなり、人は見なくなる。
        flag = bool(reasons) and reasons != ["値なし"]
        envelope["needs_review"] = flag
        envelope["review_reasons"] = reasons if flag else []

    return fields


def summarize(fields: dict[str, Any]) -> dict[str, Any]:
    labels = field_defs.labels()
    flagged = [labels.get(key, key) for key, envelope in fields.items()
               if isinstance(envelope, dict) and envelope.get("needs_review")]
    return {
        "review_status": "要確認" if flagged else "自動確定",
        "review_fields": "、".join(flagged),
        "review_count": len(flagged),
    }
