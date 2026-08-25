#!/usr/bin/env python3
"""外部 API を呼ばずに、画面を触れる状態を作る。

    python tools/seed_sample.py            # 空のときだけ投入
    python tools/seed_sample.py --force    # 既にあっても投入する
    python tools/seed_sample.py --reset    # サンプルを消してから投入する

Gmail・Claude・Maps・Sheets を一切呼ばずに、抽出済みの物件が並んだ状態を
作る。要確認フラグの見え方、根拠の表示、原本プレビュー、修正 → 再生成の
導線を、認証情報を揃える前に確認できる。

添付には tools/make_sample_mysoku.py が生成した販売図面画像を使う。
レビュー画面の「原本」ボタンから実際に図面が開くので、根拠の突き合わせが
どう見えるかまで確認できる。

投入するデータは、実務で来るものの幅を意図的にばらしてある。

  * 万円→円の換算漏れ（確信度は高いまま。検算だけが捕まえる）
  * 記載利回りと 年収÷価格 の不一致
  * 情報がほとんど無い案件（必須項目が埋まらない）
  * 何も問題のない案件（自動確定になることの確認）
  * 賃貸と事務所（売買・区分以外の表示確認）
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.db import session_scope  # noqa: E402
from app.models import Attachment, MailMessage, Property  # noqa: E402
from app.services import repository  # noqa: E402
from app.services.extraction.normalize import apply_review_flags, summarize  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# サンプルであることが一目で分かる接頭辞。--reset の対象もこれで絞る。
SAMPLE_PREFIX = "sample-"

# 販売図面のサンプル。tools/make_sample_mysoku.py が生成したもの。
GOLDEN = BACKEND / "tests" / "golden"


def env(value, confidence: float = 0.95, evidence: str = "図面より"):
    return {"value": value, "confidence": confidence, "evidence": evidence}


def station(line: str | None, name: str, minutes: int, distance: int | None = None,
            source: str = "extracted"):
    return {"line": line, "station": name, "walk_minutes": minutes,
            "distance_m": distance, "bus_minutes": None, "source": source}


SAMPLES = [
    {
        "id": "0001",
        "subject": "【売買】グランドメゾン白金台 502号室のご紹介",
        "from": "yamada@example-fudosan.co.jp",
        "body": "お世話になっております。サンプル不動産の山田です。\n"
                "表題の物件をご案内いたします。図面を添付いたします。",
        "attachment": GOLDEN / "case_001" / "attachments" / "mysoku_shirokanedai.jpg",
        "fields": {
            "property_name": env("グランドメゾン白金台", 0.98, "グランドメゾン白金台"),
            "deal_type": env("売買", 1.0, "中古マンション 販売図面"),
            "property_type": env("区分マンション", 0.95, "中古マンション"),
            "address": env("東京都港区白金台3丁目12-8", 0.93, "東京都港区白金台3丁目12-8"),
            # 「9,800万円」を万円のまま読んだケース。確信度は高いままなので、
            # 検算が無ければ要確認が立たずに通り抜ける。
            "price": env(9800, 0.96, "販売価格 9,800万円"),
            "exclusive_area_sqm": env(78.42, 0.96, "専有面積 78.42㎡（23.72坪）"),
            "balcony_area_sqm": env(9.8, 0.94, "バルコニー 9.80㎡"),
            "floor_plan": env("2LDK+S", 0.93, "２ＬＤＫ＋Ｓ"),
            "built_year_month": env("1993-03", 0.88, "平成5年3月築"),
            "structure": env("RC", 0.92, "鉄筋コンクリート造"),
            "floors_total": env(8, 0.9, "地上8階建"),
            "floor_of_unit": env("5", 0.94, "5階／8階建"),
            "units_total": env(42, 0.9, "42戸"),
            "management_fee": env(18500, 0.92, "管理費 18,500円／月"),
            "repair_reserve": env(12300, 0.92, "修繕積立金 12,300円／月"),
            "land_rights": env("所有権", 0.95, "土地権利 所有権"),
            "current_status": env("空室", 0.93, "空室（即入居可）"),
            "delivery_time": env("相談", 0.9, "引渡 相談"),
            "transaction_type": env("専任媒介", 0.9, "取引態様 専任媒介"),
            "source_company": env("株式会社サンプル不動産", 0.9, "株式会社サンプル不動産"),
            "source_contact": env("山田 太郎", 0.9, "担当：山田 太郎"),
            "source_phone": env("03-1234-5678", 0.95, "TEL 03-1234-5678"),
            "remarks": env("ペット飼育可（管理規約による）。管理人日勤。"
                           "オートロック・宅配ボックス・エレベーター2基。", 0.85, "備考"),
        },
        "stations": [
            station("東京メトロ南北線", "白金台", 6),
            station("都営浅草線", "高輪台", 11),
        ],
    },
    {
        "id": "0002",
        "subject": "収益一棟　世田谷区経堂　利回り7.2％",
        "from": "suzuki@example-toushi.co.jp",
        "body": "収益物件のご紹介です。詳細は添付の概要書をご覧ください。",
        "attachment": GOLDEN / "case_002" / "attachments" / "gaiyo_kyodo.jpg",
        "fields": {
            "property_name": env("サンライズ経堂", 0.95, "サンライズ経堂"),
            "deal_type": env("売買", 1.0, "収益物件"),
            "property_type": env("一棟アパート", 0.92, "木造スレート葺2階建 8戸"),
            "address": env("東京都世田谷区経堂1丁目2-3", 0.92, "東京都世田谷区経堂1丁目2-3"),
            "price": env(120_000_000, 0.95, "価格 1億2,000万円"),
            # 記載は 7.2% だが 8,640,000 ÷ 120,000,000 = 7.2%。整合している。
            # ここは検算が誤検知しないことの確認用。
            "gross_yield": env(7.2, 0.94, "表面利回り 7.20％"),
            "annual_income_full": env(8_640_000, 0.93, "満室想定年収 8,640,000円"),
            "annual_income_current": env(7_920_000, 0.9, "現況年収 7,920,000円"),
            "occupancy_rate": env(91.7, 0.88, "入居率 91.7％"),
            "land_area_sqm": env(215.3, 0.9, "土地面積 ２１５．３０㎡"),
            "building_area_sqm": env(308.4, 0.9, "建物面積 ３０８．４０㎡"),
            "structure": env("木造", 0.93, "木造スレート葺"),
            "built_year_month": env("2005-08", 0.9, "平成17年8月"),
            "floors_total": env(2, 0.9, "2階建"),
            "units_total": env(8, 0.92, "総戸数 8戸（1K×8）"),
            "use_district": env("第一種低層住居専用地域", 0.88, "第一種低層住居専用地域"),
            "building_coverage_ratio": env(50.0, 0.9, "建ぺい率 50％"),
            "floor_area_ratio": env(100.0, 0.9, "容積率 100％"),
            "road_frontage": env("南側 公道 幅員6.0m", 0.85, "接道 南側 公道 幅員6.0m"),
            "current_status": env("賃貸中", 0.92, "現況 賃貸中"),
            "transaction_type": env("一般媒介", 0.9, "取引態様 一般媒介"),
            "source_company": env("例示投資リアルティ株式会社", 0.9, "署名"),
            "source_contact": env("鈴木 花子", 0.9, "担当：鈴木 花子"),
        },
        "stations": [station("小田急小田原線", "経堂", 9)],
    },
    {
        "id": "0003",
        "subject": "物件情報のご案内",
        "from": "tanaka@example-estate.jp",
        "body": "添付をご確認ください。詳細は追ってご連絡いたします。",
        "attachment": GOLDEN / "case_003" / "attachments" / "annai_nerima.jpg",
        # 情報が薄い案件。埋めにいかず null のままになっているかを見る。
        "fields": {
            "property_name": env(None, 0.0, None),
            "deal_type": env("売買", 0.55, "価格 相談"),
            "property_type": env("土地", 0.9, "種別 土地"),
            "address": env("東京都練馬区", 0.5, "東京都練馬区（詳細は別途）"),
            "price": env(None, 0.0, None),
            "land_area_sqm": env(None, 0.0, None),
            "source_company": env("エグザンプル・エステート", 0.85, "エグザンプル・エステート"),
            "source_contact": env("田中", 0.8, "田中"),
            "source_email": env("tanaka@example-estate.jp", 0.9, "tanaka@example-estate.jp"),
        },
        "stations": [],
    },
    {
        "id": "0004",
        "subject": "【賃貸】パークコート三田 1203号室",
        "from": "info@example-chintai.co.jp",
        "body": "賃貸物件のご紹介です。即入居可能です。",
        "attachment": None,
        # 問題のない案件。自動確定になることの確認用。
        "fields": {
            "property_name": env("パークコート三田", 0.97, "パークコート三田"),
            "deal_type": env("賃貸", 1.0, "賃貸"),
            "property_type": env("区分マンション", 0.95, "マンション"),
            "address": env("東京都港区三田2丁目7-1", 0.95, "東京都港区三田2丁目7-1"),
            "monthly_rent": env(285_000, 0.96, "賃料 285,000円"),
            "management_fee": env(15000, 0.94, "管理費 15,000円"),
            "deposit": env("2ヶ月", 0.9, "敷金 2ヶ月"),
            "key_money": env("1ヶ月", 0.9, "礼金 1ヶ月"),
            "exclusive_area_sqm": env(64.8, 0.95, "専有面積 64.80㎡"),
            "floor_plan": env("1LDK", 0.96, "1LDK"),
            "built_year_month": env("2018-11", 0.94, "2018年11月"),
            "structure": env("RC", 0.93, "RC造"),
            "floors_total": env(14, 0.92, "14階建"),
            "floor_of_unit": env("12", 0.95, "1203号室"),
            "current_status": env("空室", 0.93, "即入居可"),
            "transaction_type": env("専属専任媒介", 0.9, "専属専任媒介"),
            "source_company": env("例示賃貸株式会社", 0.9, "署名"),
        },
        "stations": [
            station("都営浅草線", "三田", 5),
            station("東京メトロ南北線", "白金高輪", 8),
        ],
    },
    {
        "id": "0005",
        "subject": "恵比寿ガーデンハイツ　値下げのご案内",
        "from": "sales@example-fudosan.co.jp",
        "body": "先日ご案内した物件が価格改定となりましたのでご連絡いたします。",
        "attachment": None,
        "fields": {
            "property_name": env("恵比寿ガーデンハイツ", 0.97, "恵比寿ガーデンハイツ"),
            "deal_type": env("売買", 1.0, "売買"),
            "property_type": env("区分マンション", 0.94, "マンション"),
            "address": env("東京都渋谷区恵比寿南1丁目5-2", 0.94, "渋谷区恵比寿南1丁目5-2"),
            "price": env(72_800_000, 0.96, "価格 7,280万円"),
            "exclusive_area_sqm": env(55.2, 0.95, "専有面積 55.20㎡"),
            "floor_plan": env("2LDK", 0.95, "2LDK"),
            "built_year_month": env("2007-02", 0.93, "2007年2月"),
            "structure": env("SRC", 0.9, "SRC造"),
            "floors_total": env(11, 0.9, "11階建"),
            "floor_of_unit": env("7", 0.92, "7階"),
            "units_total": env(64, 0.88, "総戸数 64戸"),
            "management_fee": env(14200, 0.9, "管理費 14,200円"),
            "repair_reserve": env(9800, 0.9, "修繕積立金 9,800円"),
            "current_status": env("居住中", 0.9, "現況 居住中"),
            "delivery_time": env("2026年10月末", 0.85, "引渡 2026年10月末"),
            "transaction_type": env("専任媒介", 0.9, "専任媒介"),
            "source_company": env("株式会社サンプル不動産", 0.9, "署名"),
        },
        "stations": [station("JR山手線", "恵比寿", 6)],
    },
    {
        "id": "0006",
        "subject": "麻布十番　事務所ビル一棟　売却情報",
        "from": "office@example-toushi.co.jp",
        "body": "事務所ビルの売却情報です。築年は資料が不鮮明で読み取れませんでした。",
        "attachment": None,
        "fields": {
            "property_name": env("麻布十番スクエアビル", 0.92, "麻布十番スクエアビル"),
            "deal_type": env("売買", 1.0, "売却情報"),
            "property_type": env("事務所", 0.9, "事務所ビル"),
            "address": env("東京都港区麻布十番2丁目3-9", 0.9, "港区麻布十番2丁目3-9"),
            "price": env(480_000_000, 0.93, "価格 4億8,000万円"),
            "gross_yield": env(4.8, 0.88, "表面利回り 4.8％"),
            "annual_income_full": env(23_040_000, 0.85, "満室想定 23,040,000円"),
            "land_area_sqm": env(162.4, 0.88, "土地 162.40㎡"),
            "building_area_sqm": env(612.5, 0.88, "建物 612.50㎡"),
            "structure": env("SRC", 0.85, "SRC造"),
            # 読み取れなかった項目。必須ではないので要確認にはならないが、
            # 画面上は「―」で表示される。
            "built_year_month": env(None, 0.0, None),
            "floors_total": env(6, 0.85, "6階建"),
            "current_status": env("賃貸中", 0.88, "賃貸中"),
            "transaction_type": env("売主", 0.8, "取引態様 売主"),
            "source_company": env("例示投資リアルティ株式会社", 0.88, "署名"),
        },
        "stations": [station("東京メトロ南北線", "麻布十番", 4)],
    },
]


def _clear(session) -> int:
    """サンプルだけ消す。実データが入っていても巻き込まない。"""
    mail_ids = list(session.scalars(
        select(MailMessage.id).where(MailMessage.gmail_message_id.like(f"{SAMPLE_PREFIX}%"))
    ))
    if not mail_ids:
        return 0
    session.execute(delete(Property).where(Property.mail_id.in_(mail_ids)))
    session.execute(delete(Attachment).where(Attachment.mail_id.in_(mail_ids)))
    session.execute(delete(MailMessage).where(MailMessage.id.in_(mail_ids)))
    session.flush()
    return len(mail_ids)


def seed(force: bool = False, reset: bool = False) -> int:
    with session_scope() as session:
        if reset:
            removed = _clear(session)
            if removed:
                print(f"既存のサンプル {removed} 件を削除しました")

        existing = session.scalar(select(func.count()).select_from(Property)) or 0
        if existing and not (force or reset):
            print(f"既に {existing} 件あるため投入しません（--force で上書き）")
            return 0

        created = 0
        for index, sample in enumerate(SAMPLES):
            message_id = f"{SAMPLE_PREFIX}{sample['id']}"
            mail = session.scalar(
                select(MailMessage).where(MailMessage.gmail_message_id == message_id)
            )
            if mail is None:
                mail = MailMessage(
                    gmail_message_id=message_id,
                    label="物件情報",
                    subject=sample["subject"],
                    from_address=sample["from"],
                    received_at=datetime.now(timezone.utc) - timedelta(hours=index * 3),
                    body_text=sample["body"],
                )
                session.add(mail)
                session.flush()

            attachment = sample.get("attachment")
            if attachment and attachment.exists():
                payload = attachment.read_bytes()
                digest = hashlib.sha256(payload).hexdigest()
                known = session.scalar(
                    select(Attachment).where(
                        Attachment.mail_id == mail.id, Attachment.sha256 == digest
                    )
                )
                if known is None:
                    session.add(Attachment(
                        mail_id=mail.id, filename=attachment.name,
                        mime_type="image/jpeg", size_bytes=len(payload),
                        sha256=digest, storage_path=str(attachment),
                    ))
            elif attachment:
                print(f"  添付が見つかりません（スキップ）: {attachment}")

            fields = apply_review_flags(dict(sample["fields"]))
            repository.upsert_property(session, mail.id, {
                "fields": fields,
                "stations": sample["stations"],
                "images": [],
                "meta": {**summarize(fields), "extraction_model": "(サンプル)",
                         "prompt_version": "seed"},
            })
            flagged = summarize(fields)
            mark = "要確認" if flagged["review_count"] else "自動確定"
            print(f"  {sample['subject'][:34]:36} {mark} "
                  f"({flagged['review_count']} 項目)")
            created += 1

        print(f"サンプル {created} 件を投入しました")
        return created


def main() -> int:
    parser = argparse.ArgumentParser(description="サンプルデータを投入する")
    parser.add_argument("--force", action="store_true", help="既にデータがあっても投入する")
    parser.add_argument("--reset", action="store_true", help="サンプルを削除してから投入する")
    args = parser.parse_args()
    seed(force=args.force, reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
