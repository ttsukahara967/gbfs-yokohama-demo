"""GBFS feed discovery, TTL-aware caching and station normalization."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# station_information rarely changes but some publishers still advertise ttl=60
# for an 8 MB file, so we enforce a longer minimum for it.
MIN_TTL_SECONDS = {"station_information": 3600, "system_information": 3600}
DEFAULT_MIN_TTL = 30


@dataclass(frozen=True)
class Operator:
    id: str
    name: str
    discovery_url: str
    attribution: str
    color: str


@dataclass(frozen=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def parse(cls, value: str) -> "BBox":
        parts = [float(v) for v in value.split(",")]
        if len(parts) != 4:
            raise ValueError("bbox must be min_lon,min_lat,max_lon,max_lat")
        return cls(*parts)

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon


@dataclass
class _CacheEntry:
    body: dict[str, Any]
    expires_at: float


@dataclass
class FeedClient:
    """Fetches one operator's feeds, honoring each file's `ttl`."""

    operator: Operator
    http: httpx.AsyncClient
    _feed_urls: dict[str, str] = field(default_factory=dict)
    _cache: dict[str, _CacheEntry] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def _get_json(self, key: str, url: str) -> dict[str, Any]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._cache.get(key)
            now = time.time()
            if entry and entry.expires_at > now:
                return entry.body
            try:
                resp = await self.http.get(url)
                resp.raise_for_status()
                body = resp.json()
            except (httpx.HTTPError, ValueError):
                # Serve stale data rather than failing the whole map.
                if entry:
                    return entry.body
                raise
            ttl = max(int(body.get("ttl", 0)), MIN_TTL_SECONDS.get(key, DEFAULT_MIN_TTL))
            self._cache[key] = _CacheEntry(body, now + ttl)
            return body

    async def feed(self, name: str) -> dict[str, Any]:
        if not self._feed_urls:
            discovery = await self._get_json("gbfs", self.operator.discovery_url)
            self._feed_urls = discovery_feed_urls(discovery)
        url = self._feed_urls.get(name)
        if url is None:
            raise KeyError(f"{self.operator.id} does not publish {name}")
        return await self._get_json(name, url)

    def last_fetched(self, name: str) -> float | None:
        entry = self._cache.get(name)
        return entry.body.get("last_updated") if entry else None


def discovery_feed_urls(discovery: dict[str, Any]) -> dict[str, str]:
    """Return {feed name: url} from gbfs.json, for both v2 and v3 layouts."""
    data = discovery.get("data", {})
    if "feeds" in data:  # v3: data.feeds
        feeds = data["feeds"]
    else:  # v2: data.<language>.feeds, prefer Japanese
        lang = "ja" if "ja" in data else next(iter(data), None)
        feeds = data[lang]["feeds"] if lang else []
    return {f["name"]: f["url"] for f in feeds}


def localized(value: Any) -> str:
    """v3 uses [{"text", "language"}] for names; v2 uses a plain string."""
    if isinstance(value, list):
        for item in value:
            if item.get("language", "").startswith("ja"):
                return item.get("text", "")
        return value[0].get("text", "") if value else ""
    return value or ""


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify(bikes: int, docks: int, *, renting: bool, returning: bool, installed: bool) -> str:
    if not installed or (not renting and not returning):
        return "offline"
    if bikes == 0 or not renting:
        return "empty"
    if docks == 0 or not returning:
        return "full"
    if bikes <= 2:
        return "few"
    return "ok"


def merge_stations(
    operator: Operator,
    information: dict[str, Any],
    status: dict[str, Any],
    bbox: BBox,
) -> list[dict[str, Any]]:
    """Join station_information and station_status by station_id within bbox."""
    status_by_id = {s["station_id"]: s for s in status["data"]["stations"]}
    stations = []
    for info in information["data"]["stations"]:
        lat, lon = info.get("lat"), info.get("lon")
        if lat is None or lon is None or not bbox.contains(lat, lon):
            continue
        st = status_by_id.get(info["station_id"])
        if st is None:
            continue
        # v3 renamed num_bikes_available to num_vehicles_available.
        bikes = _to_int(st.get("num_vehicles_available", st.get("num_bikes_available"))) or 0
        docks = _to_int(st.get("num_docks_available")) or 0
        renting = bool(st.get("is_renting", True))
        returning = bool(st.get("is_returning", True))
        installed = bool(st.get("is_installed", True))
        stations.append(
            {
                "id": f"{operator.id}:{info['station_id']}",
                "operator": operator.id,
                "name": localized(info.get("name")),
                "address": info.get("address"),
                "lat": lat,
                "lon": lon,
                # HELLO CYCLING publishes capacity as a string in vehicle_capacity.
                "capacity": _to_int(info.get("capacity", info.get("vehicle_capacity"))),
                "bikes": bikes,
                "docks": docks,
                "status": classify(
                    bikes, docks, renting=renting, returning=returning, installed=installed
                ),
                "last_reported": st.get("last_reported"),
                "rental_url": (info.get("rental_uris") or {}).get("web"),
            }
        )
    return stations
