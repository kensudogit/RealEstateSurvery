"""正規化と検算のテスト。

ここで守りたいのは「誤った値が要確認フラグ無しで通り抜けないこと」。
取得率より、捏造率と見逃し率を優先する（取れない項目は人が入れれば済むが、
間違った値が黙って通ると営業事故になる）。
"""

from __future__ import annotations

import pytest

from app.services.extraction.normalize import (
    apply_review_flags, cross_check, normalize_fields, normalize_wareki, summarize,
)


class TestWareki:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("平成5年3月", "1993-03"),
            ("平成5年3月築", "1993-03"),
            ("H5.3", None),          # 区切りが年月でないものは拾わない
            ("昭和60年12月", "1985-12"),
            ("令和元年5月", "2019-05"),
            ("令和6年1月", "2024-01"),
            ("平成5年", None),        # 月が不明なら捏造せず None
            ("2010年4月", None),      # 和暦ではない
        ],
    )
    def test_convert(self, text, expected):
        assert normalize_wareki(text) == expected


class TestNormalize:
    def test_wareki_is_converted(self, envelope):
        fields = normalize_fields({"built_year_month": envelope("平成5年3月")})
        assert fields["built_year_month"]["value"] == "1993-03"

    def test_unconvertible_date_loses_confidence(self, envelope):
        fields = normalize_fields({"built_year_month": envelope("築浅")})
        assert fields["built_year_month"]["value"] is None
        assert fields["built_year_month"]["confidence"] == 0.0

    def test_floor_plan_is_halfwidth_upper(self, envelope):
        fields = normalize_fields({"floor_plan": envelope("２ＬＤＫ")})
        assert fields["floor_plan"]["value"] == "2LDK"

    def test_numeric_string_is_coerced(self, envelope):
        fields = normalize_fields({"price": envelope("48,000,000")})
        assert fields["price"]["value"] == 48_000_000

    def test_null_stays_null(self, envelope):
        """読めなかった項目を 0 や空文字で埋めない。「0円」と「不明」は別物。"""
        fields = normalize_fields({"price": envelope(None, confidence=0.0, evidence=None)})
        assert fields["price"]["value"] is None


class TestCrossCheck:
    def test_missing_price_for_sale(self, envelope):
        issues = cross_check({
            "deal_type": envelope("売買"),
            "price": envelope(None),
        })
        assert "price" in issues

    def test_man_yen_conversion_missed(self, envelope):
        """「4,800万円」を 4800 と読んだケース。confidence は高いままなので
        検算が無いと通り抜ける。"""
        issues = cross_check({"deal_type": envelope("売買"), "price": envelope(4800)})
        assert "換算漏れ" in issues["price"]

    def test_annual_rent_in_monthly_field(self, envelope):
        issues = cross_check({"deal_type": envelope("賃貸"), "monthly_rent": envelope(9_600_000)})
        assert "monthly_rent" in issues

    def test_yield_inconsistent_with_income(self, envelope):
        issues = cross_check({
            "price": envelope(100_000_000),
            "annual_income_full": envelope(6_000_000),   # 実際は 6.0%
            "gross_yield": envelope(7.2),                # 記載は 7.2%
        })
        assert "gross_yield" in issues

    def test_yield_consistent_passes(self, envelope):
        issues = cross_check({
            "price": envelope(100_000_000),
            "annual_income_full": envelope(7_200_000),
            "gross_yield": envelope(7.2),
        })
        assert "gross_yield" not in issues

    def test_tsubo_mistaken_for_sqm(self, envelope):
        issues = cross_check({"land_area_sqm": envelope(250_000.0)})
        assert "land_area_sqm" in issues

    def test_table_row_misalignment(self, envelope):
        """3LDK で専有 25㎡ は表の行列ずれの典型。"""
        issues = cross_check({
            "floor_plan": envelope("3LDK"),
            "exclusive_area_sqm": envelope(25.0),
        })
        assert "exclusive_area_sqm" in issues

    def test_impossible_built_year(self, envelope):
        issues = cross_check({"built_year_month": envelope("1850-01")})
        assert "built_year_month" in issues


class TestReviewFlags:
    def test_required_missing_is_flagged(self, envelope):
        fields = apply_review_flags({"property_name": envelope(None, confidence=0.0)})
        assert fields["property_name"]["needs_review"] is True

    def test_low_confidence_is_flagged(self, envelope):
        fields = apply_review_flags({"address": envelope("東京都渋谷区", confidence=0.4)})
        assert fields["address"]["needs_review"] is True

    def test_optional_missing_is_not_flagged(self, envelope):
        """任意項目の単なる「値なし」でフラグを立てると、全項目が要確認になって
        フラグが情報を持たなくなる。"""
        fields = apply_review_flags({"parking": envelope(None, confidence=0.0)})
        assert fields["parking"]["needs_review"] is False

    def test_cross_check_failure_is_flagged_even_when_confident(self, envelope):
        """検算に引っかかった項目は、確信度が高くても要確認にする。
        これが「見逃し」を防ぐ最後の砦。"""
        fields = apply_review_flags({
            "deal_type": envelope("売買", confidence=1.0),
            "price": envelope(4800, confidence=0.99),
        })
        assert fields["price"]["needs_review"] is True

    def test_summary_lists_japanese_labels(self, envelope):
        fields = apply_review_flags({
            "property_name": envelope(None, confidence=0.0),
            "address": envelope("東京都渋谷区", confidence=1.0),
        })
        summary = summarize(fields)
        assert summary["review_status"] == "要確認"
        assert "物件名" in summary["review_fields"]
        assert summary["review_count"] == 1
