"""FastAPI app that aggregates docomo bike share and HELLO CYCLING GBFS feeds."""

from __future__ import annotations

import asyncio
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .gbfs import BBox, FeedClient, Operator, merge_stations

ODPT_BASE = "https://api-public.odpt.org/api/v4/gbfs"

OPERATORS = [
    Operator(
        id="docomo",
        name="ドコモ・バイクシェア",
        discovery_url=f"{ODPT_BASE}/docomo-cycle/gbfs.json",
        attribution="ドコモ・バイクシェア (CC BY 4.0)",
        color="#d4213d",
    ),
    Operator(
        id="hellocycling",
        name="HELLO CYCLING",
        discovery_url=f"{ODPT_BASE}/hellocycling/gbfs.json",
        attribution="OpenStreet株式会社 HELLO CYCLING (CC BY 4.0)",
        color="#f2a900",
    ),
]

# Roughly the city of Yokohama. Override with BBOX=min_lon,min_lat,max_lon,max_lat.
BBOX = BBox.parse(os.environ.get("BBOX", "139.47,35.31,139.72,35.60"))

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

clients: dict[str, FeedClient] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with httpx.AsyncClient(
        timeout=30, headers={"User-Agent": "gbfs-yokohama-demo"}
    ) as http:
        for op in OPERATORS:
            clients[op.id] = FeedClient(op, http)
        yield
        clients.clear()


app = FastAPI(title="GBFS Yokohama demo", lifespan=lifespan)


async def _stations_for(op: Operator) -> list[dict]:
    client = clients[op.id]
    info, status = await asyncio.gather(
        client.feed("station_information"), client.feed("station_status")
    )
    return merge_stations(op, info, status, BBOX)


async def _all_stations() -> tuple[list[dict], list[dict]]:
    results = await asyncio.gather(
        *(_stations_for(op) for op in OPERATORS), return_exceptions=True
    )
    stations, operators = [], []
    for op, result in zip(OPERATORS, results):
        ok = not isinstance(result, Exception)
        if ok:
            stations.extend(result)
        operators.append(
            {
                "id": op.id,
                "name": op.name,
                "color": op.color,
                "attribution": op.attribution,
                "available": ok,
                "error": None if ok else str(result),
                "station_count": len(result) if ok else 0,
                "last_updated": clients[op.id].last_fetched("station_status"),
            }
        )
    return stations, operators


@app.get("/api/stations")
async def stations():
    stations, operators = await _all_stations()
    if not stations and not any(o["available"] for o in operators):
        raise HTTPException(502, "all GBFS feeds are unavailable")
    return {
        "bbox": [BBOX.min_lon, BBOX.min_lat, BBOX.max_lon, BBOX.max_lat],
        "operators": operators,
        "stations": stations,
    }


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.get("/api/nearest")
async def nearest(
    lat: float,
    lon: float,
    want: Literal["rent", "return"] = "rent",
    limit: int = Query(3, ge=1, le=10),
):
    """Nearest stations where you can rent a bike (or return one)."""
    stations, _ = await _all_stations()
    if want == "rent":
        usable = [s for s in stations if s["bikes"] > 0 and s["status"] not in ("offline", "empty")]
    else:
        usable = [s for s in stations if s["docks"] > 0 and s["status"] not in ("offline", "full")]
    for s in usable:
        s["distance_m"] = round(_distance_m(lat, lon, s["lat"], s["lon"]))
    usable.sort(key=lambda s: s["distance_m"])
    return {"want": want, "stations": usable[:limit]}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
