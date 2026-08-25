"""スプレッドシート転記のテスト（API を叩かない部分）。"""

from __future__ import annotations

import pytest

from app.services.sheets import _column_letter, resolve_field, to_cell_values


@pytest.fixture
def payload():
    return {
        "fields": {
            "property_name": {"value": "渋谷ハイツ", "confidence": 1.0,
                              "evidence": "渋谷ハイツ", "needs_review": False},
            "price": {"value": 48_000_000, "confidence": 0.6,
                      "evidence": "4,800万円", "needs_review": True},
            "floor_plan": {"value": None, "confidence": 0.0,
                           "evidence": None, "needs_review": False},
        },
        "stations": [
            {"line": "JR山手線", "station": "渋谷", "walk_minutes": 7},
        ],
        "meta": {"gmail_message_id": "abc123", "review_status": "要確認"},
    }


class TestResolveField:
    def test_plain_field(self, payload):
        assert resolve_field(payload, "property_name") == ("渋谷ハイツ", False)

    def test_review_flag_is_returned(self, payload):
        assert resolve_field(payload, "price") == (48_000_000, True)

    def test_station_index(self, payload):
        assert resolve_field(payload, "stations.0.walk_minutes") == (7, False)

    def test_missing_station_index(self, payload):
        assert resolve_field(payload, "stations.2.station") == (None, False)

    def test_meta(self, payload):
        assert resolve_field(payload, "meta.gmail_message_id") == ("abc123", False)

    def test_unknown_field(self, payload):
        assert resolve_field(payload, "nonexistent") == (None, False)


class TestCellValues:
    def test_null_uses_placeholder(self, payload):
        """空文字にすると「AI が取れなかった」のか「もともと空欄」なのかが
        シートを見た人に分からない。"""
        columns = [{"header": "間取り", "field": "floor_plan"}]
        values, review = to_cell_values(payload, columns, "―")
        assert values == ["―"]
        assert review == []

    def test_numbers_stay_numeric(self, payload):
        """文字列にするとシート上で並べ替え・集計ができなくなる。"""
        columns = [{"header": "価格(円)", "field": "price"}]
        values, _ = to_cell_values(payload, columns, "―")
        assert values == [48_000_000]
        assert isinstance(values[0], int)

    def test_review_columns_are_reported(self, payload):
        columns = [
            {"header": "物件名", "field": "property_name"},
            {"header": "価格(円)", "field": "price"},
            {"header": "間取り", "field": "floor_plan"},
        ]
        _, review = to_cell_values(payload, columns, "―")
        assert review == [1]


@pytest.mark.parametrize("index,letter", [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (51, "AZ")])
def test_column_letter(index, letter):
    assert _column_letter(index) == letter


def test_column_map_is_loadable():
    """列マッピングは設定ファイルから読む。列の並べ替えでデプロイが要らない。"""
    from app.services.sheets import load_column_map

    config = load_column_map()
    assert config["key_column"]
    assert any(c["header"] == config["key_column"] for c in config["columns"])
