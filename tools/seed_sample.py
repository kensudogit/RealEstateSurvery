#!/usr/bin/env python3
"""API キー無しで UI を触るためのサンプルデータ投入。

    docker compose exec backend python /app/../tools/seed_sample.py
    # ローカルなら backend/ から: python ../tools/seed_sample.py

Gmail・Claude・Maps・Sheets を一切呼ばずに、抽出済みの物件が 3 件ある状態を
作る。要確認フラグの見え方、根拠の表示、修正 → 再生成の導線を、
認証情報を揃える前に確認できる。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.db import session_scope  # noqa: E402
from app.models import Attachment, MailMessage  # noqa: E402
from app.services import repository  # noqa: E402
from app.services.extraction.normalize import apply_review_flags, summarize  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def envelope(value, confidence=0.95, evidence="原文より"):
    return {"value": value, "confidence": confidence, "evidence": evidence}


SAMPLES = [
    {
        "message_id": "sample-0001",
        "subject": "【売買】渋谷アーバンレジデンス 502号室のご紹介",
        "from": "sales@example-fudosan.co.jp",
        "body": "お世話になっております。表題の物件をご案内いたします。",
        "fields": {
            "property_name": envelope("渋谷アーバンレジデンス", 0.98, "渋谷アーバンレジデンス"),
            "deal_type": envelope("売買", 1.0, "売買物件のご紹介"),
            "property_type": envelope("区分マンション", 0.94, "マンション"),
            "address": envelope("東京都渋谷区渋谷2-1-1", 0.92, "渋谷区渋谷2-1-1"),
            # 「4,800万円」の換算を誤ったケース。確信度は高いままなので、
            # 検算が無いと要確認が立たずに通り抜ける。
            "price": envelope(4800, 0.97, "販売価格 4,800万円"),
            "exclusive_area_sqm": envelope(62.5, 0.96, "専有面積 62.50㎡"),
            "floor_plan": envelope("2LDK", 0.97, "2LDK"),
            "built_year_month": envelope("1993-03", 0.88, "平成5年3月築"),
            "structure": envelope("RC", 0.9, "RC造"),
            "floors_total": envelope(12, 0.9, "地上12階建"),
            "floor_of_unit": envelope("5", 0.95, "502号室"),
            "management_fee": envelope(12000, 0.9, "管理費 12,000円"),
            "repair_reserve": envelope(8500, 0.9, "修繕積立金 8,500円"),
            "current_status": envelope("空室", 0.9, "現況 空室"),
            "transaction_type": envelope("専任媒介", 0.85, "専任media"),
            "source_company": envelope("株式会社サンプル不動産", 0.9, "署名より"),
            "remarks": envelope("ペット可（規約による）。管理人日勤。", 0.8, "備考欄"),
        },
        "stations": [
            {"line": "JR山手線", "station": "渋谷", "walk_minutes": 7,
             "distance_m": 520, "bus_minutes": None, "source": "extracted"},
        ],
    },
    {
        "message_id": "sample-0002",
        "subject": "収益一棟 　世田谷区　利回り7.2％",
        "from": "info@example-toushi.co.jp",
        "body": "収益物件のご紹介です。満室想定年収600万円、価格1億円。",
        "fields": {
            "property_name": envelope("サンライズ経堂", 0.9, "サンライズ経堂"),
            "deal_type": envelope("売買", 1.0, "売買"),
            "property_type": envelope("一棟アパート", 0.9, "一棟アパート"),
            "address": envelope("東京都世田谷区経堂1-2-3", 0.85, "世田谷区経堂1-2-3"),
            "price": envelope(100_000_000, 0.95, "価格 1億円"),
            "annual_income_full": envelope(6_000_000, 0.9, "満室想定年収600万円"),
            # 記載 7.2% に対し 年収÷価格 は 6.0%。検算で捕まる。
            "gross_yield": envelope(7.2, 0.93, "利回り7.2％"),
            "land_area_sqm": envelope(180.5, 0.9, "土地 180.50㎡"),
            "building_area_sqm": envelope(240.0, 0.9, "建物 240.00㎡"),
            "built_year_month": envelope("2005-08", 0.9, "平成17年8月"),
            "structure": envelope("木造", 0.85, "木造"),
            "units_total": envelope(8, 0.9, "総戸数8戸"),
            "current_status": envelope("賃貸中", 0.9, "満室稼働中"),
            "source_company": envelope("例示投資リアルティ株式会社", 0.85, "署名より"),
        },
        "stations": [],
    },
    {
        "message_id": "sample-0003",
        "subject": "物件情報のご案内",
        "from": "tanaka@example-estate.jp",
        "body": "添付をご確認ください。",
        # 情報が薄いケース。埋めにいかず null のままにできているかを見る。
        "fields": {
            "property_name": envelope(None, 0.0, None),
            "deal_type": envelope("売買", 0.6, "件名より推測"),
            "property_type": envelope(None, 0.0, None),
            "address": envelope("東京都練馬区", 0.5, "練馬区"),
            "price": envelope(None, 0.0, None),
        },
        "stations": [],
    },
]


def main() -> int:
    templates = Path(__file__).resolve().parent.parent / "templates"
    sample_file = templates / "mysoku_a4.pptx"  # 原本プレビューの動作確認用

    with session_scope() as session:
        for index, sample in enumerate(SAMPLES):
            existing = session.query(MailMessage).filter_by(
                gmail_message_id=sample["message_id"]
            ).one_or_none()
            if existing is not None:
                print(f"既にあるためスキップ: {sample['message_id']}")
                continue

            mail = MailMessage(
                gmail_message_id=sample["message_id"],
                label="物件情報",
                subject=sample["subject"],
                from_address=sample["from"],
                received_at=datetime.now(timezone.utc) - timedelta(hours=index),
                body_text=sample["body"],
            )
            session.add(mail)
            session.flush()

            if sample_file.exists():
                session.add(Attachment(
                    mail_id=mail.id, filename=sample_file.name,
                    mime_type="application/vnd.openxmlformats-officedocument."
                              "presentationml.presentation",
                    size_bytes=sample_file.stat().st_size,
                    sha256=f"sample{index:04d}", storage_path=str(sample_file),
                ))

            fields = apply_review_flags(dict(sample["fields"]))
            repository.upsert_property(session, mail.id, {
                "fields": fields,
                "stations": sample["stations"],
                "images": [],
                "meta": {**summarize(fields), "extraction_model": "(サンプル)",
                         "prompt_version": "seed"},
            })
            flagged = summarize(fields)
            print(f"投入: {sample['subject']} -> {flagged['review_status']} "
                  f"({flagged['review_count']} 項目)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
