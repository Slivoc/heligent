from __future__ import annotations

import csv
import hashlib
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import requests

from .adsblol import USER_AGENT


OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
SUPPORTED_AIRPORT_TYPES = frozenset(
    {
        "large_airport",
        "medium_airport",
        "small_airport",
        "heliport",
        "seaplane_base",
        "balloonport",
    }
)
DEFAULT_RADIUS_NM = {
    "large_airport": 4.0,
    "medium_airport": 3.0,
    "small_airport": 2.0,
    "heliport": 1.2,
    "seaplane_base": 2.0,
    "balloonport": 1.0,
}
AIRPORT_TYPE_BONUS_NM = {
    "large_airport": 2.0,
    "medium_airport": 1.0,
    "small_airport": 0.25,
    "heliport": 0.0,
    "seaplane_base": 0.0,
    "balloonport": 0.0,
}
CONTINUITY_BONUS_NM = 0.25


@dataclass(frozen=True)
class Airport:
    ident: str
    airport_type: str
    name: str
    latitude_deg: float
    longitude_deg: float
    elevation_ft: int | None
    continent: str | None
    iso_country: str | None
    iso_region: str | None
    municipality: str | None
    scheduled_service: bool
    gps_code: str | None
    iata_code: str | None
    local_code: str | None


@dataclass(frozen=True)
class AirportMatch:
    airport: Airport
    distance_nm: float


@dataclass(frozen=True)
class AirportCatalog:
    airports: tuple[Airport, ...]
    source_url: str
    sha256: str
    downloaded_at: datetime


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_nm = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_nm * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_airports_csv(
    cache_dir: Path,
    *,
    url: str = OURAIRPORTS_URL,
    refresh: bool = False,
    timeout: float = 60.0,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "ourairports-airports.csv"
    if target.exists() and target.stat().st_size > 0 and not refresh:
        return target

    part = target.with_suffix(".csv.part")
    with requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        with part.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    if part.stat().st_size == 0:
        raise RuntimeError("OurAirports download was empty")
    os.replace(part, target)
    return target


def load_airport_catalog(
    path: Path,
    *,
    source_url: str = OURAIRPORTS_URL,
) -> AirportCatalog:
    airports: list[Airport] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ident", "type", "name", "latitude_deg", "longitude_deg"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Airport CSV is missing columns: {sorted(missing)}")
        for row in reader:
            if row["type"] not in SUPPORTED_AIRPORT_TYPES:
                continue
            try:
                latitude = float(row["latitude_deg"])
                longitude = float(row["longitude_deg"])
            except (TypeError, ValueError):
                continue
            elevation: int | None
            try:
                elevation = int(float(row.get("elevation_ft") or ""))
            except ValueError:
                elevation = None
            airports.append(
                Airport(
                    ident=row["ident"].strip(),
                    airport_type=row["type"],
                    name=row["name"].strip(),
                    latitude_deg=latitude,
                    longitude_deg=longitude,
                    elevation_ft=elevation,
                    continent=(row.get("continent") or "").strip() or None,
                    iso_country=(row.get("iso_country") or "").strip() or None,
                    iso_region=(row.get("iso_region") or "").strip() or None,
                    municipality=(row.get("municipality") or "").strip() or None,
                    scheduled_service=(row.get("scheduled_service") or "").lower() == "yes",
                    gps_code=(row.get("gps_code") or "").strip() or None,
                    iata_code=(row.get("iata_code") or "").strip() or None,
                    local_code=(row.get("local_code") or "").strip() or None,
                )
            )
    if not airports:
        raise ValueError("Airport CSV produced no supported airports")
    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return AirportCatalog(
        airports=tuple(airports),
        source_url=source_url,
        sha256=_sha256_file(path),
        downloaded_at=modified,
    )


class AirportIndex:
    def __init__(
        self,
        airports: Iterable[Airport],
        *,
        radii_nm: dict[str, float] | None = None,
        cell_degrees: float = 0.1,
    ) -> None:
        self.radii_nm = dict(DEFAULT_RADIUS_NM if radii_nm is None else radii_nm)
        self.cell_degrees = cell_degrees
        self.longitude_cell_count = round(360.0 / cell_degrees)
        self.airports: dict[str, Airport] = {}
        self.grid: dict[tuple[int, int], list[Airport]] = defaultdict(list)
        for airport in airports:
            if airport.airport_type not in self.radii_nm:
                continue
            self.airports[airport.ident] = airport
            self.grid[self._cell(airport.latitude_deg, airport.longitude_deg)].append(airport)
        if not self.airports:
            raise ValueError("Airport index has no matchable airports")
        self.max_radius_nm = max(self.radii_nm.values())

    def _cell(self, latitude: float, longitude: float) -> tuple[int, int]:
        return (
            math.floor((latitude + 90.0) / self.cell_degrees),
            math.floor((longitude + 180.0) / self.cell_degrees),
        )

    def _matches(self, airport: Airport, latitude: float, longitude: float) -> AirportMatch | None:
        radius = self.radii_nm[airport.airport_type]
        if abs(latitude - airport.latitude_deg) * 60 > radius:
            return None
        longitude_delta = abs(longitude - airport.longitude_deg)
        longitude_delta = min(longitude_delta, 360.0 - longitude_delta)
        longitude_nm = longitude_delta * 60 * max(
            math.cos(math.radians(latitude)), 0.01
        )
        if longitude_nm > radius:
            return None
        distance = haversine_nm(
            latitude,
            longitude,
            airport.latitude_deg,
            airport.longitude_deg,
        )
        return AirportMatch(airport, distance) if distance <= radius else None

    def match(
        self,
        latitude: float,
        longitude: float,
        *,
        preferred_ident: str | None = None,
    ) -> AirportMatch | None:
        lat_cell, lon_cell = self._cell(latitude, longitude)
        lat_steps = math.ceil((self.max_radius_nm / 60) / self.cell_degrees)
        longitude_scale = max(math.cos(math.radians(latitude)), 0.05)
        lon_steps = math.ceil(
            (self.max_radius_nm / (60 * longitude_scale)) / self.cell_degrees
        )
        nearest: AirportMatch | None = None
        nearest_score = math.inf
        for lat_offset in range(-lat_steps, lat_steps + 1):
            for lon_offset in range(-lon_steps, lon_steps + 1):
                candidate_lon_cell = (lon_cell + lon_offset) % self.longitude_cell_count
                for airport in self.grid.get((lat_cell + lat_offset, candidate_lon_cell), ()):
                    result = self._matches(airport, latitude, longitude)
                    if not result:
                        continue
                    score = result.distance_nm - AIRPORT_TYPE_BONUS_NM[airport.airport_type]
                    if airport.scheduled_service:
                        score -= 0.25
                    if airport.ident == preferred_ident:
                        score -= CONTINUITY_BONUS_NM
                    if score < nearest_score or (
                        math.isclose(score, nearest_score)
                        and (nearest is None or result.distance_nm < nearest.distance_nm)
                    ):
                        nearest = result
                        nearest_score = score
        return nearest
