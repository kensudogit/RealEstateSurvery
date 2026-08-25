#!/usr/bin/env python3
"""住所から緯度経度・最寄駅・徒歩分数を求める（パイプライン④）。

    python scripts/nearest_station.py --address "東京都新宿区西新宿2-8-1"
    python scripts/nearest_station.py --data out.json --out out.json   # 抽出結果に追記

徒歩分数は「不動産の表示に関する公正競争規約」に合わせて
**道路距離 80m につき 1 分・端数切り上げ** で計算する。
Distance Matrix が返す duration（実所要時間）ではなく distance（道路距離）を
使う点が肝心で、ここを間違えると広告表記として不適合になる。

必要な API（Google Maps Platform で有効化しておく）:
  * Geocoding API      住所 → 緯度経度
  * Places API (New)   周辺の駅を距離順に取得
  * Distance Matrix API 徒歩の道路距離
    （後継の Routes API を使う場合は routes.googleapis.com の
      distanceMatrix/v2:computeRouteMatrix に差し替える。取得する値は同じく距離）

依存: requests
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

STATION_TYPES = ["train_station", "subway_station", "light_rail_station"]
METERS_PER_MINUTE = int(os.environ.get("GEO_WALK_METERS_PER_MINUTE", "80"))
MAX_STATIONS = int(os.environ.get("GEO_MAX_STATIONS", "3"))
SEARCH_RADIUS_M = float(os.environ.get("GEO_SEARCH_RADIUS_M", "2000"))
TIMEOUT = 15

# Windows の既定コンソール（cp932）で日本語や記号が化けないようにする。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class GeoError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        raise GeoError("GOOGLE_MAPS_API_KEY が設定されていません")
    return key


# --------------------------------------------------------------------------

def geocode(address: str) -> dict[str, Any]:
    """住所 → 緯度経度と正規化住所。

    location_type は精度の目安になる。ROOFTOP なら建物単位、
    APPROXIMATE なら町丁目レベルまでしか特定できていないので、
    そのまま徒歩分数を出すと誤差が大きい。呼び出し側で要確認にする。
    """
    params = {"address": address, "language": "ja", "region": "jp", "key": _api_key()}
    res = requests.get(GEOCODE_URL, params=params, timeout=TIMEOUT)
    res.raise_for_status()
    body = res.json()

    if body.get("status") == "ZERO_RESULTS":
        raise GeoError(f"住所を特定できませんでした: {address}")
    if body.get("status") != "OK":
        raise GeoError(f"Geocoding 失敗: {body.get('status')} {body.get('error_message', '')}")

    top = body["results"][0]
    loc = top["geometry"]["location"]
    return {
        "latitude": loc["lat"],
        "longitude": loc["lng"],
        "formatted_address": top["formatted_address"],
        "location_type": top["geometry"].get("location_type"),
        "partial_match": bool(top.get("partial_match")),
    }


def nearby_stations(lat: float, lng: float, limit: int) -> list[dict[str, Any]]:
    """直線距離の近い順に駅候補を取る。ここでは順番付けだけが目的で、
    実際の分数は次段の道路距離で決める。"""
    headers = {
        "X-Goog-Api-Key": _api_key(),
        "X-Goog-FieldMask": "places.displayName,places.location,places.primaryType",
        "Content-Type": "application/json",
    }
    payload = {
        "includedTypes": STATION_TYPES,
        "maxResultCount": max(limit * 3, 5),  # 同一駅の出入口が複数返るため多めに取る
        "rankPreference": "DISTANCE",
        "languageCode": "ja",
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lng},
                       "radius": SEARCH_RADIUS_M}
        },
    }
    res = requests.post(PLACES_NEARBY_URL, headers=headers, json=payload, timeout=TIMEOUT)
    res.raise_for_status()
    places = res.json().get("places", [])

    stations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for place in places:
        name = place.get("displayName", {}).get("text", "").replace("駅", "")
        if not name or name in seen:
            continue
        seen.add(name)
        stations.append({
            "station": name,
            "latitude": place["location"]["latitude"],
            "longitude": place["location"]["longitude"],
            "primary_type": place.get("primaryType"),
        })
        if len(stations) >= limit:
            break
    return stations


def walking_distances(origin: tuple[float, float],
                      destinations: list[tuple[float, float]]) -> list[int | None]:
    """徒歩の道路距離（m）。取得できなかった要素は None。"""
    if not destinations:
        return []
    params = {
        "origins": f"{origin[0]},{origin[1]}",
        "destinations": "|".join(f"{lat},{lng}" for lat, lng in destinations),
        "mode": "walking",
        "language": "ja",
        "key": _api_key(),
    }
    res = requests.get(DISTANCE_MATRIX_URL, params=params, timeout=TIMEOUT)
    res.raise_for_status()
    body = res.json()
    if body.get("status") != "OK":
        raise GeoError(f"Distance Matrix 失敗: {body.get('status')}")

    out: list[int | None] = []
    for element in body["rows"][0]["elements"]:
        if element.get("status") == "OK" and "distance" in element:
            out.append(int(element["distance"]["value"]))
        else:
            out.append(None)
    return out


def walk_minutes(distance_m: int) -> int:
    """道路距離 80m につき 1 分、端数切り上げ。1分未満でも 1 分と表記する。"""
    return max(1, math.ceil(distance_m / METERS_PER_MINUTE))


def display_address(formatted: str) -> str:
    """広告表記用に丁目までへ丸める。番地・号は元付の許可なく出さない運用が多い。"""
    match = re.search(r"^(.*?[0-9０-９]+丁目)", formatted)
    return match.group(1) if match else formatted


# --------------------------------------------------------------------------

def resolve(address: str, limit: int = MAX_STATIONS) -> dict[str, Any]:
    geo = geocode(address)
    candidates = nearby_stations(geo["latitude"], geo["longitude"], limit)
    distances = walking_distances(
        (geo["latitude"], geo["longitude"]),
        [(c["latitude"], c["longitude"]) for c in candidates],
    )

    stations: list[dict[str, Any]] = []
    for candidate, distance in zip(candidates, distances):
        if distance is None:
            continue
        stations.append({
            # 沿線名は Places からは確実に取れない。抽出値がある場合はそちらを
            # 優先し、無ければ null のまま要確認に回す（駅名から沿線を推測すると
            # 複数路線が乗り入れる駅で必ず間違える）。
            "line": None,
            "station": candidate["station"],
            "walk_minutes": walk_minutes(distance),
            "distance_m": distance,
            "bus_minutes": None,
            "source": "geo",
        })
    stations.sort(key=lambda s: s["distance_m"])

    return {
        "latitude": geo["latitude"],
        "longitude": geo["longitude"],
        "formatted_address": geo["formatted_address"],
        "address_display": display_address(geo["formatted_address"]),
        "geocode_accuracy": geo["location_type"],
        # 町丁目レベルまでしか当たっていない住所で出した徒歩分数は誤差が大きい。
        # 資料に載せる前に人が確認できるようフラグを立てておく。
        "needs_review": geo["partial_match"] or geo["location_type"] == "APPROXIMATE",
        "stations": stations[:limit],
    }


def merge_into(data: dict, resolved: dict) -> dict:
    """抽出結果 JSON に geo 情報を合流させる。

    抽出済みの沿線・駅は元付が書いた正の情報なので上書きしない。
    足りない分だけ geo の結果で埋める。
    """
    fields = data.setdefault("fields", {})
    for key, value in (("latitude", resolved["latitude"]),
                       ("longitude", resolved["longitude"]),
                       ("address_display", resolved["address_display"])):
        fields[key] = {"value": value, "confidence": 1.0,
                       "evidence": f"Geocoding: {resolved['formatted_address']}",
                       "needs_review": resolved["needs_review"],
                       "review_reasons": ["住所の特定精度が粗い"] if resolved["needs_review"] else []}

    extracted = [s for s in data.get("stations", []) if s.get("station")]
    known = {s["station"] for s in extracted}
    for station in resolved["stations"]:
        if len(extracted) >= MAX_STATIONS:
            break
        if station["station"] not in known:
            extracted.append(station)
    data["stations"] = extracted
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="住所から最寄駅と徒歩分数を求める")
    parser.add_argument("--address", help="住所文字列")
    parser.add_argument("--data", type=Path, help="extract_property.py の出力 JSON")
    parser.add_argument("--out", type=Path, help="出力先。--data 指定時は合流結果を書く")
    parser.add_argument("--limit", type=int, default=MAX_STATIONS)
    args = parser.parse_args()

    data = None
    address = args.address
    if args.data:
        data = json.loads(args.data.read_text(encoding="utf-8"))
        address = address or (data.get("fields", {}).get("address") or {}).get("value")
    if not address:
        raise SystemExit("--address か、address を含む --data が必要です")

    try:
        resolved = resolve(address, args.limit)
    except GeoError as exc:
        print(f"最寄駅の取得に失敗しました: {exc}", file=sys.stderr)
        return 1

    payload = merge_into(data, resolved) if data else resolved
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{args.out} に出力しました", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
