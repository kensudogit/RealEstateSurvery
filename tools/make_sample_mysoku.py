#!/usr/bin/env python3
"""検証用のサンプル販売図面（マイソク）を生成する。

    python tools/make_sample_mysoku.py --out backend/tests/golden

実物のマイソクが揃うまでの間、抽出の落とし穴を意図的に踏ませるための
テスト入力を作る。生成されるのは画像（PNG）と正解データ（expected.json）で、
そのままゴールデンセットのケースになる。

仕込んである落とし穴:

  case_001  和暦の築年月 / 坪表記の土地面積 / 万円表記 / FAX ヘッダのノイズ /
            手書き風メモ / 罫線を省略した表
  case_002  収益一棟。年収と利回りが整合する正常系。全角数字・㎡と坪の併記
  case_003  情報がほとんど無いスカスカの案件。モデルが埋めにくるかを見る

最後のケースが一番重要で、情報が薄い資料で捏造するかどうかが、この
システムの安全性を決める。

依存: Pillow
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# A4 横を 150dpi 相当で。実際の FAX スキャンもこの程度の解像度で届く。
WIDTH, HEIGHT = 1754, 1240

WHITE = (255, 255, 255)
INK = (25, 25, 28)
GRAY = (110, 115, 122)
LINE = (190, 195, 200)
RED = (190, 40, 40)
BLUE = (40, 70, 160)

FONT_PATHS = [
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/YuGothM.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = ["C:/Windows/Fonts/meiryob.ttc"] + FONT_PATHS if bold else FONT_PATHS
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit("日本語フォントが見つかりません。Meiryo か Noto Sans CJK を入れてください")


def _text(draw, xy, text, size=22, color=INK, bold=False, anchor=None):
    draw.text(xy, text, font=_font(size, bold), fill=color, anchor=anchor)


def _spec_rows(draw, left, top, rows, *, label_width=180, row_height=44,
               ruled=True, size=21):
    """物件概要の表。

    ruled=False にすると罫線を引かない。実際の販売図面では罫線が省略されて
    いることが多く、これが行と列の対応を誤る最大の原因になる。
    """
    y = top
    for label, value in rows:
        _text(draw, (left, y + 8), label, size=size - 2, color=GRAY)
        _text(draw, (left + label_width, y + 8), value, size=size)
        if ruled:
            draw.line([(left, y + row_height), (left + 780, y + row_height)], fill=LINE)
        y += row_height
    return y


def _fax_header(draw):
    """FAX のヘッダ。物件情報ではないので無視されるべきノイズ。"""
    _text(draw, (40, 18),
          "2026/08/20 14:32  FROM: 03-XXXX-XXXX  サンプル不動産(株)  P.001/001",
          size=17, color=GRAY)
    draw.line([(30, 46), (WIDTH - 30, 46)], fill=LINE)


def _stamp(draw, x, y, lines):
    """朱印風のスタンプ。これも物件情報ではない。"""
    draw.rectangle([x, y, x + 130, y + 130], outline=RED, width=4)
    for index, line in enumerate(lines):
        _text(draw, (x + 65, y + 30 + index * 32), line, size=24, color=RED,
              bold=True, anchor="ma")


def _handwriting(draw, x, y, text, size=28):
    """手書きメモ風。斜めに書けないので色と字形で代用する。"""
    _text(draw, (x, y), text, size=size, color=BLUE)


def _photo_block(image, box, color, caption):
    draw = ImageDraw.Draw(image)
    draw.rectangle(box, fill=color, outline=LINE, width=2)
    _text(draw, ((box[0] + box[2]) // 2, box[3] - 34), caption,
          size=18, color=WHITE, anchor="ma")


# --------------------------------------------------------------------------

def build_case_001(path: Path) -> dict:
    """区分マンション。和暦・坪表記・万円表記・ノイズ入り。"""
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    _fax_header(draw)

    _text(draw, (40, 70), "中古マンション　販売図面", size=24, color=GRAY)
    _text(draw, (40, 110), "グランドメゾン白金台", size=52, bold=True)
    _text(draw, (40, 180), "東京都港区白金台3丁目12-8　502号室", size=24)
    _text(draw, (40, 218), "東京メトロ南北線「白金台」駅　徒歩6分", size=24)
    _text(draw, (40, 252), "都営浅草線「高輪台」駅　徒歩11分", size=24)

    _text(draw, (40, 310), "販売価格", size=24, color=GRAY)
    _text(draw, (190, 296), "9,800万円", size=48, bold=True, color=RED)

    # 罫線を省略した表。行列の対応を誤りやすい。
    _spec_rows(draw, 40, 380, [
        ("専有面積", "78.42㎡（23.72坪）　壁芯"),
        ("バルコニー", "9.80㎡"),
        ("間取り", "２ＬＤＫ＋Ｓ"),
        ("築年月", "平成5年3月築"),
        ("構造・規模", "鉄筋コンクリート造　地上8階建"),
        ("所在階", "5階／8階建"),
        ("総戸数", "42戸"),
        ("管理費", "18,500円／月"),
        ("修繕積立金", "12,300円／月"),
        ("土地権利", "所有権"),
        ("現況", "空室（即入居可）"),
        ("引渡", "相談"),
        ("取引態様", "専任媒介"),
    ], ruled=False)

    _photo_block(image, (900, 80, 1500, 470), (150, 140, 130), "外観")
    _photo_block(image, (900, 500, 1500, 900), (235, 235, 230), "間取図")

    _handwriting(draw, 910, 930, "※ 値下げ交渉可とのこと")
    _stamp(draw, 1560, 90, ["専任", "媒介"])

    _text(draw, (40, 1010), "備考：ペット飼育可（管理規約による）。管理人日勤。", size=20)
    _text(draw, (40, 1044), "　　　オートロック・宅配ボックス・エレベーター2基。", size=20)

    draw.line([(30, 1100), (WIDTH - 30, 1100)], fill=LINE)
    _text(draw, (40, 1120), "株式会社サンプル不動産　城南支店　担当：山田 太郎", size=21)
    _text(draw, (40, 1154), "TEL 03-1234-5678 / yamada@example-fudosan.co.jp", size=20, color=GRAY)
    _text(draw, (40, 1188), "国土交通大臣（3）第XXXXX号", size=18, color=GRAY)

    image.save(path, quality=92)

    return {
        "property_name": "グランドメゾン白金台",
        "deal_type": "売買",
        "property_type": "区分マンション",
        "address": "東京都港区白金台3丁目12-8",
        "price": 98_000_000,
        "exclusive_area_sqm": 78.42,
        "balcony_area_sqm": 9.8,
        "floor_plan": "2LDK+S",
        "built_year_month": "1993-03",
        "structure": "RC",
        "floors_total": 8,
        "floor_of_unit": "5",
        "units_total": 42,
        "management_fee": 18500,
        "repair_reserve": 12300,
        "land_rights": "所有権",
        "current_status": "空室",
        "delivery_time": "相談",
        "transaction_type": "専任媒介",
        "source_company": "株式会社サンプル不動産",
        "source_contact": "山田 太郎",
        "source_phone": "03-1234-5678",
        # 図面に無い項目。埋めにきたら捏造。
        "land_area_sqm": None,
        "building_area_sqm": None,
        "gross_yield": None,
        "monthly_rent": None,
        "use_district": None,
        "road_frontage": None,
    }


def build_case_002(path: Path) -> dict:
    """収益一棟。年収と利回りが整合する正常系。全角数字と単位の混在。"""
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)

    _text(draw, (40, 40), "収益物件　物件概要書", size=24, color=GRAY)
    _text(draw, (40, 82), "サンライズ経堂", size=48, bold=True)
    _text(draw, (40, 150), "所 在 地　東京都世田谷区経堂1丁目2-3", size=24)
    _text(draw, (40, 186), "交　　通　小田急小田原線「経堂」駅　徒歩9分", size=24)

    _text(draw, (40, 250), "価　格", size=24, color=GRAY)
    _text(draw, (160, 238), "1億2,000万円", size=44, bold=True, color=RED)
    _text(draw, (560, 252), "表面利回り　7.20％", size=30, bold=True)

    _spec_rows(draw, 40, 330, [
        ("満室想定年収", "8,640,000円"),
        ("現況年収", "7,920,000円"),
        ("入居率", "91.7％"),
        ("土地面積", "２１５．３０㎡（65.12坪）"),
        ("建物面積", "３０８．４０㎡"),
        ("構　造", "木造スレート葺2階建"),
        ("築年月", "平成17年8月"),
        ("総戸数", "8戸（1K×8）"),
        ("用途地域", "第一種低層住居専用地域"),
        ("建ぺい率／容積率", "50％／100％"),
        ("接　道", "南側 公道 幅員6.0m"),
        ("現　況", "賃貸中"),
        ("取引態様", "一般媒介"),
    ], label_width=230)

    _photo_block(image, (900, 80, 1560, 520), (140, 150, 135), "外観")
    _photo_block(image, (900, 550, 1560, 900), (240, 238, 232), "配置図")

    draw.line([(30, 1080), (WIDTH - 30, 1080)], fill=LINE)
    _text(draw, (40, 1100), "例示投資リアルティ株式会社　担当：鈴木 花子", size=21)
    _text(draw, (40, 1134), "TEL 03-9876-5432 / suzuki@example-toushi.co.jp", size=20, color=GRAY)

    image.save(path, quality=92)

    return {
        "property_name": "サンライズ経堂",
        "deal_type": "売買",
        "property_type": "一棟アパート",
        "address": "東京都世田谷区経堂1丁目2-3",
        "price": 120_000_000,
        "gross_yield": 7.2,
        "annual_income_full": 8_640_000,
        "annual_income_current": 7_920_000,
        "occupancy_rate": 91.7,
        "land_area_sqm": 215.3,
        "building_area_sqm": 308.4,
        "structure": "木造",
        "built_year_month": "2005-08",
        "floors_total": 2,
        "units_total": 8,
        "use_district": "第一種低層住居専用地域",
        "building_coverage_ratio": 50.0,
        "floor_area_ratio": 100.0,
        "current_status": "賃貸中",
        "transaction_type": "一般媒介",
        "source_company": "例示投資リアルティ株式会社",
        "source_contact": "鈴木 花子",
        "source_phone": "03-9876-5432",
        "exclusive_area_sqm": None,
        "floor_plan": None,
        "management_fee": None,
        "repair_reserve": None,
        "delivery_time": None,
    }


def build_case_003(path: Path) -> dict:
    """情報がほとんど無い案件。埋めにこないかを見るための最重要ケース。"""
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)

    _fax_header(draw)
    _text(draw, (40, 90), "物件のご案内", size=34, bold=True)
    _text(draw, (40, 170), "所在地　東京都練馬区（詳細は別途）", size=26)
    _text(draw, (40, 220), "種　別　土地", size=26)
    _text(draw, (40, 270), "価　格　相談", size=26)
    _text(draw, (40, 340), "詳細は追ってご連絡いたします。", size=22, color=GRAY)
    _text(draw, (40, 380), "ご興味ございましたらご一報ください。", size=22, color=GRAY)

    _handwriting(draw, 40, 460, "※ 図面は後日送付")
    _stamp(draw, 1400, 120, ["見本"])

    draw.line([(30, 1080), (WIDTH - 30, 1080)], fill=LINE)
    _text(draw, (40, 1100), "エグザンプル・エステート　田中", size=21)
    _text(draw, (40, 1134), "tanaka@example-estate.jp", size=20, color=GRAY)

    image.save(path, quality=92)

    return {
        "property_type": "土地",
        "address": "東京都練馬区",
        "source_company": "エグザンプル・エステート",
        "source_contact": "田中",
        "source_email": "tanaka@example-estate.jp",
        # 以下はすべて図面に無い。1 つでも埋まっていたら捏造。
        "property_name": None,
        "price": None,
        "land_area_sqm": None,
        "building_area_sqm": None,
        "exclusive_area_sqm": None,
        "floor_plan": None,
        "built_year_month": None,
        "structure": None,
        "floors_total": None,
        "units_total": None,
        "gross_yield": None,
        "monthly_rent": None,
        "management_fee": None,
        "current_status": None,
        "transaction_type": None,
        "use_district": None,
    }


CASES = [
    ("case_001", "白金台マンション（和暦・坪・万円・ノイズ入り）", build_case_001,
     "mysoku_shirokanedai.jpg",
     {"subject": "【売買】グランドメゾン白金台 502号室のご紹介",
      "from": "yamada@example-fudosan.co.jp",
      "body": "お世話になっております。サンプル不動産の山田です。\n"
              "表題の物件をご案内いたします。図面を添付いたしますのでご確認ください。\n"
              "ご不明点がございましたらお気軽にご連絡ください。"}),
    ("case_002", "経堂一棟アパート（収益・全角数字）", build_case_002,
     "gaiyo_kyodo.jpg",
     {"subject": "収益一棟　世田谷区経堂　利回り7.2％",
      "from": "suzuki@example-toushi.co.jp",
      "body": "収益物件のご紹介です。詳細は添付の概要書をご覧ください。"}),
    ("case_003", "情報の薄い案件（捏造検出用）", build_case_003,
     "annai_nerima.jpg",
     {"subject": "物件情報のご案内",
      "from": "tanaka@example-estate.jp",
      "body": "添付をご確認ください。"}),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="検証用のサンプル販売図面を生成する")
    parser.add_argument("--out", type=Path, default=Path("backend/tests/golden"))
    args = parser.parse_args()

    for case_id, title, builder, filename, meta in CASES:
        case_dir = args.out / case_id
        (case_dir / "attachments").mkdir(parents=True, exist_ok=True)

        image_path = case_dir / "attachments" / filename
        expected = builder(image_path)

        (case_dir / "expected.json").write_text(
            json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (case_dir / "meta.json").write_text(
            json.dumps({**meta, "title": title}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (case_dir / "body.txt").write_text(meta["body"], encoding="utf-8")

        graded = len(expected)
        blanks = sum(1 for v in expected.values() if v is None)
        print(f"{case_id}: {title}")
        print(f"  {image_path}")
        print(f"  正解 {graded} 項目（うち「値なしが正解」{blanks} 項目）")

    print("\n次のコマンドで抽出精度を測れます（ANTHROPIC_API_KEY が必要）:")
    print("  cd backend && pytest tests/test_golden_accuracy.py -s --refresh-golden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
