"""最寄駅・徒歩分数の自動取得（④）。

徒歩分数は「不動産の表示に関する公正競争規約」に合わせて
道路距離 80m につき 1 分・端数切り上げで計算する。
Distance Matrix が返す duration（実所要時間）ではなく distance（道路距離）を
使う点が肝心で、ここを間違えると広告表記として不適合になる。
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import GeoCache

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

STATION_TYPES = ["train_station", "subway_station", "light_rail_station"]
TIMEOUT = 15

CHOME_RE = re.compile(r"^(.*?[0-9０-９]+丁目)")
SPACE_RE = re.compile(r"[\s　]+")

# 特定精度がこれらのときは徒歩分数の誤差が大きい。人に確認させる。
COARSE_ACCURACY = {"APPROXIMATE", "GEOMETRIC_CENTER"}


class GeoError(RuntimeError):
    pass


def _api_key() -> str:
    key = get_settings().google_maps_api_key
    if not key:
        raise GeoError("GOOGLE_MAPS_API_KEY が設定されていません")
    return key


def address_key(address: str) -> str:
    """キャッシュキー。表記ゆれを吸収するため空白と全角数字を潰す。"""
    normalized = SPACE_RE.sub("", address)
    return normalized.translate(str.maketrans("０１２３４５６７８９－", "0123456789-"))


# --------------------------------------------------------------------------

def geocode(address: str) -> dict[str, Any]:
    params = {"address": address, "language": "ja", "region": "jp", "key": _api_key()}
    response = requests.get(GEOCODE_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    body = response.json()

    if body.get("status") == "ZERO_RESULTS":
        raise GeoError(f"住所を特定できませんでした: {address}")
    if body.get("status") != "OK":
        raise GeoError(f"Geocoding 失敗: {body.get('status')} {body.get('error_message', '')}")

    top = body["results"][0]
    location = top["geometry"]["location"]
    return {
        "latitude": location["lat"],
        "longitude": location["lng"],
        "formatted_address": top["formatted_address"],
        "location_type": top["geometry"].get("location_type"),
        "partial_match": bool(top.get("partial_match")),
    }


def nearby_stations(latitude: float, longitude: float, limit: int) -> list[dict[str, Any]]:
    """直線距離の近い順に駅候補を取る。

    ここでは順番付けだけが目的。線路や川を挟むと直線で近い駅が徒歩では遠い、
    が普通に起きるので、最終的な並びは次段の道路距離で付け直す。
    """
    settings = get_settings()
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
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": settings.geo_search_radius_m,
            }
        },
    }
    response = requests.post(PLACES_NEARBY_URL, headers=headers, json=payload, timeout=TIMEOUT)
    response.raise_for_status()

    stations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for place in response.json().get("places", []):
        name = place.get("displayName", {}).get("text", "").replace("駅", "")
        if not name or name in seen:
            continue
        seen.add(name)
        stations.append({
            "station": name,
            "latitude": place["location"]["latitude"],
            "longitude": place["location"]["longitude"],
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
    response = requests.get(DISTANCE_MATRIX_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "OK":
        raise GeoError(f"Distance Matrix 失敗: {body.get('status')}")

    results: list[int | None] = []
    for element in body["rows"][0]["elements"]:
        if element.get("status") == "OK" and "distance" in element:
            results.append(int(element["distance"]["value"]))
        else:
            results.append(None)
    return results


def walk_minutes(distance_m: int) -> int:
    """道路距離 80m につき 1 分、端数切り上げ。1 分未満でも 1 分と表記する。"""
    per_minute = get_settings().geo_walk_meters_per_minute
    return max(1, math.ceil(distance_m / per_minute))


def display_address(formatted: str) -> str:
    """広告表記用に丁目までへ丸める。

    番地・号は元付の許可なく出さない運用が多い。会社によって方針が違うので、
    導入時に必ず確認すること。
    """
    match = CHOME_RE.search(formatted)
    return match.group(1) if match else formatted


# --------------------------------------------------------------------------

def resolve(session: Session, address: str) -> dict[str, Any]:
    """住所 → 緯度経度・最寄駅。同じ住所はキャッシュから返す。"""
    settings = get_settings()
    key = address_key(address)

    cached = session.get(GeoCache, key)
    if cached is not None:
        # 新駅開業・駅名改称があるので永久キャッシュにはしない。
        age = datetime.now(timezone.utc) - cached.fetched_at
        if age < timedelta(days=settings.geo_cache_ttl_days):
            return {
                "latitude": cached.latitude,
                "longitude": cached.longitude,
                "formatted_address": cached.formatted,
                "address_display": cached.address_display or cached.formatted,
                "geocode_accuracy": cached.accuracy,
                "needs_review": cached.accuracy in COARSE_ACCURACY,
                "stations": list(cached.stations),
                "from_cache": True,
            }
        session.delete(cached)
        session.flush()

    geo = geocode(address)
    limit = settings.geo_max_stations
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
            # 沿線名は Places からは確実に取れない。駅名から推測すると
            # 複数路線が乗り入れる駅で必ず外すので、null のまま人に回す。
            "line": None,
            "station": candidate["station"],
            "walk_minutes": walk_minutes(distance),
            "distance_m": distance,
            "bus_minutes": None,
            "source": "geo",
        })
    stations.sort(key=lambda item: item["distance_m"])
    stations = stations[:limit]

    resolved = {
        "latitude": geo["latitude"],
        "longitude": geo["longitude"],
        "formatted_address": geo["formatted_address"],
        "address_display": display_address(geo["formatted_address"]),
        "geocode_accuracy": geo["location_type"],
        "needs_review": geo["partial_match"] or geo["location_type"] in COARSE_ACCURACY,
        "stations": stations,
        "from_cache": False,
    }

    session.merge(GeoCache(
        address_key=key,
        latitude=resolved["latitude"],
        longitude=resolved["longitude"],
        formatted=resolved["formatted_address"],
        accuracy=resolved["geocode_accuracy"],
        address_display=resolved["address_display"],
        stations=stations,
        fetched_at=datetime.now(timezone.utc),
    ))
    return resolved


def enrich(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    """抽出結果に緯度経度・最寄駅を合流させる。

    元付が書いた沿線・駅・徒歩分数は正として扱い、上書きしない。
    業者の分数は物件の正面出入口からの実測であることが多く、Geocoding が
    当てた座標より正確だから。自動取得は足りない分を埋めるためのもの。
    """
    address = (data.get("fields", {}).get("address") or {}).get("value")
    if not address:
        logger.info("所在地が無いため最寄駅の補完をスキップします")
        return data

    try:
        resolved = resolve(session, address)
    except (GeoError, requests.RequestException) as exc:
        logger.warning("最寄駅の取得に失敗: %s", exc)
        data.setdefault("meta", {})["geo_error"] = str(exc)
        return data

    reasons = ["住所の特定精度が粗く、徒歩分数に誤差が出る"] if resolved["needs_review"] else []
    fields = data.setdefault("fields", {})
    for key, value in (
        ("latitude", resolved["latitude"]),
        ("longitude", resolved["longitude"]),
        ("address_display", resolved["address_display"]),
    ):
        fields[key] = {
            "value": value,
            "confidence": 1.0,
            "evidence": f"Geocoding: {resolved['formatted_address']}",
            "needs_review": resolved["needs_review"],
            "review_reasons": reasons,
        }

    extracted = [s for s in data.get("stations", []) if s.get("station")]
    known = {s["station"] for s in extracted}
    limit = get_settings().geo_max_stations
    for station in resolved["stations"]:
        if len(extracted) >= limit:
            break
        if station["station"] not in known:
            extracted.append(station)
    data["stations"] = extracted
    return data
