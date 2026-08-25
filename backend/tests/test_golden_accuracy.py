"""ゴールデンセットによる抽出精度の計測。

正解データが tests/golden/case_*/ に無い間は自動でスキップされる。
実際に届いたメール 20〜30 通を、正解付きで固定するところから始める。
これが無いと「プロンプトを直したら良くなった気がする」から先に進めない。

ケースの置き方:

    tests/golden/case_001/
        meta.json          件名・差出人・受信日時
        body.txt           メール本文（無ければ省略可）
        attachments/*.pdf  添付
        expected.json      人が入力した正解（素の値。envelope ではない）
        _cached.json       抽出結果のキャッシュ（自動生成、gitignore 済み）

API 呼び出しは高いので、既定ではキャッシュを使う。プロンプトを変えたら

    pytest tests/test_golden_accuracy.py --refresh-golden

で実際に呼び直す。CI は常にキャッシュを使う。

見る指標は 4 つ。取得率より捏造率と見逃し率を優先する。取れない項目は
人が入れれば済むが、間違った値が黙って通ると営業事故になる。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"
CASES = sorted(p for p in GOLDEN_DIR.glob("case_*") if p.is_dir())

pytestmark = pytest.mark.skipif(
    not CASES, reason="tests/golden/case_* が無いためスキップ（README.md 参照）"
)


def _extract(case: Path, refresh: bool) -> dict:
    cache = case / "_cached.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))

    from app.services.extraction.claude import extract
    from app.services.extraction.normalize import apply_review_flags, normalize_fields

    body_path = case / "body.txt"
    body = body_path.read_text(encoding="utf-8") if body_path.exists() else None
    files = sorted((case / "attachments").glob("*")) if (case / "attachments").exists() else []

    result = extract(body, files)
    raw = dict(result.raw)
    raw.pop("stations", None)
    raw.pop("images", None)
    payload = {"fields": apply_review_flags(normalize_fields(raw))}
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


@pytest.fixture(scope="session")
def refresh(request) -> bool:
    return bool(request.config.getoption("--refresh-golden", default=False))


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_no_unflagged_error(case: Path, refresh: bool):
    """誤った値が要確認フラグ無しで通り抜けていないこと。

    これがこのシステムの安全性そのもの。誤りをゼロにはできないが、
    誤りが確認されずに資料へ流れる経路は塞ぐ。
    """
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    actual = _extract(case, refresh)["fields"]

    missed: list[str] = []
    for key, want in expected.items():
        envelope = actual.get(key) or {}
        got = envelope.get("value")
        if got is None or want is None or got == want:
            continue
        if not envelope.get("needs_review"):
            missed.append(f"{key}: 正解={want!r} 抽出={got!r}")

    assert not missed, "確認フラグ無しの誤りがある:\n  " + "\n  ".join(missed)


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_no_fabrication_when_absent(case: Path, refresh: bool):
    """正解が null の項目に値を作っていないこと。

    情報が薄い資料でモデルが埋めにくるかどうかが安全性を決めるので、
    スカスカの案件をゴールデンセットに必ず入れておく。
    """
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    actual = _extract(case, refresh)["fields"]

    fabricated = [
        f"{key}: 抽出={(actual.get(key) or {}).get('value')!r}"
        for key, want in expected.items()
        if want is None and (actual.get(key) or {}).get("value") is not None
    ]
    assert not fabricated, "正解が空の項目に値が入っている:\n  " + "\n  ".join(fabricated)


def test_report(refresh, capsys):
    """項目別の精度サマリ。数字で語れるようにするための出力。"""
    totals = {"正解": 0, "誤り": 0, "未取得": 0, "正しくnull": 0}
    flagged_errors = 0

    for case in CASES:
        expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
        actual = _extract(case, refresh)["fields"]
        for key, want in expected.items():
            envelope = actual.get(key) or {}
            got = envelope.get("value")
            if want is None:
                totals["正しくnull" if got is None else "誤り"] += 1
            elif got is None:
                totals["未取得"] += 1
            elif got == want:
                totals["正解"] += 1
            else:
                totals["誤り"] += 1
                if envelope.get("needs_review"):
                    flagged_errors += 1

    graded = sum(totals.values())
    errors = totals["誤り"]
    with capsys.disabled():
        print(f"\n--- 抽出精度（{len(CASES)} ケース / {graded} 項目）---")
        for label, count in totals.items():
            print(f"  {label}: {count} ({count / graded:.1%})")
        print(f"  捏造率: {errors / graded:.1%}  ← 最優先で下げる")
        if errors:
            print(f"  見逃し率: {(errors - flagged_errors) / errors:.1%}"
                  f"  ← 誤りのうち要確認が立たなかった割合")
