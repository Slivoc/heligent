from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from .airports import AirportIndex, haversine_nm
from .archive import TracePayload


ADDRESS_RE = re.compile(r"^(?:~)?[0-9a-f]{6}$")


@dataclass(frozen=True)
class ActivityConfig:
    max_continuous_gap_seconds: float = 120.0
    airport_ground_gap_seconds: float = 5 * 60.0
    active_speed_knots: float = 5.0
    airport_endpoint_max_agl_ft: float = 1_500.0
    airport_endpoint_max_speed_knots: float = 200.0
    airport_movement_exit_margin_nm: float = 1.0
    airport_movement_exit_multiplier: float = 1.5
    max_plausible_speed_knots: float = 1_500.0
    calculate_distance: bool = False

    def __post_init__(self) -> None:
        positive = (
            self.max_continuous_gap_seconds,
            self.airport_ground_gap_seconds,
            self.airport_endpoint_max_agl_ft,
            self.airport_endpoint_max_speed_knots,
            self.airport_movement_exit_margin_nm,
            self.airport_movement_exit_multiplier,
            self.max_plausible_speed_knots,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Activity gaps and maximum speed must be positive")
        if self.active_speed_knots < 0:
            raise ValueError("Active speed threshold cannot be negative")


@dataclass(frozen=True)
class AircraftDaySummary:
    utc_date: date
    address: str
    address_kind: str
    registration: str | None
    type_code: str | None
    type_description: str | None
    owner_operator: str | None
    manufacture_year: int | None
    db_flags: int | None
    trace_format_version: str | None
    callsigns: tuple[str, ...]
    callsign_last_seen: str | None
    position_source_types: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime
    observation_count: int
    position_count: int
    ground_observation_count: int
    min_altitude_ft: int | None
    max_altitude_ft: int | None
    max_ground_speed_knots: float | None
    first_latitude: float | None
    first_longitude: float | None
    last_latitude: float | None
    last_longitude: float | None
    time_observed_seconds: int
    active_time_seconds: int
    airborne_time_seconds: int
    ground_active_time_seconds: int
    distinct_airports: int
    airport_presence_count: int
    estimated_distance_nm: float | None
    source_trace_gzip_bytes: int
    source_trace_json_bytes: int


@dataclass(frozen=True)
class AirportPresenceSummary:
    utc_date: date
    airport_ident: str
    address: str
    is_primary_airport: bool
    first_seen_at: datetime
    last_seen_at: datetime
    presence_count: int
    ground_observation_count: int
    ground_time_seconds: int
    ground_active_time_seconds: int
    closest_distance_nm: float
    inferred_endpoint_count: int
    arrival_count: int
    departure_count: int
    link_method: str


@dataclass
class _AirportAccumulator:
    first_seen_seconds: float
    last_seen_seconds: float
    presence_count: int = 1
    ground_observation_count: int = 0
    ground_time_seconds: float = 0.0
    ground_active_time_seconds: float = 0.0
    closest_distance_nm: float = math.inf
    last_speed_knots: float | None = None
    inferred_endpoint_count: int = 0
    arrival_count: int = 0
    departure_count: int = 0


@dataclass(frozen=True)
class _Point:
    observed_seconds: float
    latitude: float | None
    longitude: float | None
    is_ground: bool
    is_airborne: bool
    altitude_ft: float | None
    ground_speed_knots: float | None


def _endpoint_airport_match(
    point: _Point,
    airport_index: AirportIndex,
    config: ActivityConfig,
):
    if point.latitude is None or point.longitude is None:
        return None
    match = airport_index.match(point.latitude, point.longitude)
    if match is None:
        return None
    if point.is_ground:
        return match
    if point.altitude_ft is None:
        return None
    elevation_ft = match.airport.elevation_ft or 0
    if point.altitude_ft - elevation_ft > config.airport_endpoint_max_agl_ft:
        return None
    if (
        point.ground_speed_knots is not None
        and point.ground_speed_knots > config.airport_endpoint_max_speed_knots
    ):
        return None
    return match


def _track_moves_outside_airport(
    points: list[_Point],
    *,
    airport_index: AirportIndex,
    airport_ident: str,
    config: ActivityConfig,
) -> bool:
    airport = airport_index.airports[airport_ident]
    match_radius_nm = airport_index.radii_nm[airport.airport_type]
    exit_radius_nm = max(
        match_radius_nm + config.airport_movement_exit_margin_nm,
        match_radius_nm * config.airport_movement_exit_multiplier,
    )
    return any(
        point.latitude is not None
        and point.longitude is not None
        and haversine_nm(
            point.latitude,
            point.longitude,
            airport.latitude_deg,
            airport.longitude_deg,
        )
        >= exit_radius_nm
        for point in points
    )


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _manufacture_year(value: Any) -> int | None:
    year = _optional_int(value)
    return year if year is not None and 1900 <= year <= 2200 else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def summarize_trace(
    trace_payload: TracePayload,
    utc_date: date,
    airport_index: AirportIndex,
    *,
    config: ActivityConfig | None = None,
) -> tuple[AircraftDaySummary, tuple[AirportPresenceSummary, ...]]:
    config = config or ActivityConfig()
    payload = trace_payload.payload
    address = str(payload.get("icao") or trace_payload.address_from_filename).lower()
    if not ADDRESS_RE.fullmatch(address):
        raise ValueError(f"Invalid aircraft address {address!r} in {trace_payload.archive_name}")
    if address != trace_payload.address_from_filename.lower():
        raise ValueError(
            f"Address mismatch in {trace_payload.archive_name}: filename has "
            f"{trace_payload.address_from_filename}, JSON has {address}"
        )

    base_timestamp = _number(payload.get("timestamp"))
    records = payload.get("trace")
    if base_timestamp is None or not isinstance(records, list) or not records:
        raise ValueError(f"Trace {trace_payload.archive_name} has no timestamped observations")

    callsigns: set[str] = set()
    callsign_last_seen: str | None = None
    source_types: set[str] = set()
    observation_count = 0
    position_count = 0
    ground_observation_count = 0
    min_altitude: float | None = None
    max_altitude: float | None = None
    max_ground_speed: float | None = None
    first_seconds: float | None = None
    last_seconds: float | None = None
    first_position: tuple[float, float] | None = None
    last_position: tuple[float, float] | None = None
    active_seconds = 0.0
    airborne_seconds = 0.0
    ground_active_seconds = 0.0
    estimated_distance = 0.0
    distance_segments = 0
    previous: _Point | None = None
    track_points: list[_Point] = []
    ground_airport_by_observed: dict[float, str] = {}
    current_airport_ident: str | None = None
    airports: dict[str, _AirportAccumulator] = {}

    for row in records:
        if not isinstance(row, list) or not row:
            continue
        offset = _number(row[0])
        if offset is None:
            continue
        observed = base_timestamp + offset
        latitude = _number(row[1]) if len(row) > 1 else None
        longitude = _number(row[2]) if len(row) > 2 else None
        if latitude is not None and not -90 <= latitude <= 90:
            latitude = None
        if longitude is not None and not -180 <= longitude <= 180:
            longitude = None

        altitude_value = row[3] if len(row) > 3 else None
        is_ground = altitude_value == "ground"
        numeric_altitude = _number(altitude_value)
        is_airborne = numeric_altitude is not None and not is_ground
        speed = _number(row[4]) if len(row) > 4 else None
        observation_count += 1
        if latitude is not None and longitude is not None:
            position_count += 1
            if first_position is None:
                first_position = (latitude, longitude)
            last_position = (latitude, longitude)
        if is_ground:
            ground_observation_count += 1
        if numeric_altitude is not None:
            min_altitude = (
                numeric_altitude if min_altitude is None else min(min_altitude, numeric_altitude)
            )
            max_altitude = (
                numeric_altitude if max_altitude is None else max(max_altitude, numeric_altitude)
            )
        if speed is not None:
            max_ground_speed = speed if max_ground_speed is None else max(max_ground_speed, speed)

        first_seconds = observed if first_seconds is None else min(first_seconds, observed)
        last_seconds = observed if last_seconds is None else max(last_seconds, observed)

        detail = row[8] if len(row) > 8 else None
        if isinstance(detail, dict):
            callsign = _optional_text(detail.get("flight"))
            if callsign:
                callsigns.add(callsign)
                callsign_last_seen = callsign
        if len(row) > 9 and row[9] is not None:
            source_types.add(str(row[9]))

        point = _Point(
            observed,
            latitude,
            longitude,
            is_ground,
            is_airborne,
            numeric_altitude,
            speed,
        )
        if latitude is not None and longitude is not None:
            track_points.append(point)
        if previous is not None:
            delta = observed - previous.observed_seconds
            if 0 < delta <= config.max_continuous_gap_seconds:
                moving = (
                    previous.is_airborne
                    or point.is_airborne
                    or (previous.ground_speed_knots or 0) >= config.active_speed_knots
                    or (point.ground_speed_knots or 0) >= config.active_speed_knots
                )
                if moving:
                    active_seconds += delta
                if previous.is_airborne or point.is_airborne:
                    airborne_seconds += delta
                if (
                    previous.is_ground
                    and point.is_ground
                    and (
                        (previous.ground_speed_knots or 0) >= config.active_speed_knots
                        or (point.ground_speed_knots or 0) >= config.active_speed_knots
                    )
                ):
                    ground_active_seconds += delta
                if (
                    config.calculate_distance
                    and previous.latitude is not None
                    and previous.longitude is not None
                    and point.latitude is not None
                    and point.longitude is not None
                ):
                    distance = haversine_nm(
                        previous.latitude,
                        previous.longitude,
                        point.latitude,
                        point.longitude,
                    )
                    implied_speed = distance / (delta / 3600)
                    if implied_speed <= config.max_plausible_speed_knots:
                        estimated_distance += distance
                        distance_segments += 1

        if is_ground and latitude is not None and longitude is not None:
            match = airport_index.match(
                latitude,
                longitude,
                preferred_ident=current_airport_ident,
            )
            if match:
                ident = match.airport.ident
                accumulator = airports.get(ident)
                if accumulator is None:
                    accumulator = _AirportAccumulator(
                        first_seen_seconds=observed,
                        last_seen_seconds=observed,
                        ground_observation_count=1,
                        closest_distance_nm=match.distance_nm,
                        last_speed_knots=speed,
                    )
                    airports[ident] = accumulator
                else:
                    delta = observed - accumulator.last_seen_seconds
                    continuous_same_airport = current_airport_ident == ident
                    if (
                        continuous_same_airport
                        and 0 < delta <= config.airport_ground_gap_seconds
                    ):
                        accumulator.ground_time_seconds += delta
                        if (
                            (accumulator.last_speed_knots or 0) >= config.active_speed_knots
                            or (speed or 0) >= config.active_speed_knots
                        ):
                            accumulator.ground_active_time_seconds += delta
                    accumulator.last_seen_seconds = max(accumulator.last_seen_seconds, observed)
                    accumulator.first_seen_seconds = min(accumulator.first_seen_seconds, observed)
                    accumulator.ground_observation_count += 1
                    accumulator.closest_distance_nm = min(
                        accumulator.closest_distance_nm, match.distance_nm
                    )
                    accumulator.last_speed_knots = speed
                current_airport_ident = ident
                ground_airport_by_observed[observed] = ident
            else:
                current_airport_ident = None
        elif not is_ground:
            current_airport_ident = None

        previous = point

    if first_seconds is None or last_seconds is None:
        raise ValueError(f"Trace {trace_payload.archive_name} has no usable observations")

    endpoint_evidence: set[tuple[str, float]] = set()
    endpoint_candidates = (
        (track_points[0], "departure") if track_points else None,
        (track_points[-1], "arrival") if track_points else None,
    )
    for candidate in endpoint_candidates:
        if candidate is None:
            continue
        endpoint, direction = candidate
        if endpoint.is_ground:
            ident = ground_airport_by_observed.get(endpoint.observed_seconds)
            if ident is None:
                continue
            accumulator = airports[ident]
            endpoint_distance_nm = haversine_nm(
                endpoint.latitude,
                endpoint.longitude,
                airport_index.airports[ident].latitude_deg,
                airport_index.airports[ident].longitude_deg,
            )
        else:
            match = _endpoint_airport_match(endpoint, airport_index, config)
            if match is None:
                continue
            ident = match.airport.ident
            endpoint_distance_nm = match.distance_nm
            accumulator = airports.get(ident)
            if accumulator is None:
                accumulator = _AirportAccumulator(
                    first_seen_seconds=endpoint.observed_seconds,
                    last_seen_seconds=endpoint.observed_seconds,
                    closest_distance_nm=endpoint_distance_nm,
                    last_speed_knots=endpoint.ground_speed_knots,
                )
                airports[ident] = accumulator

        accumulator.first_seen_seconds = min(
            accumulator.first_seen_seconds, endpoint.observed_seconds
        )
        accumulator.last_seen_seconds = max(
            accumulator.last_seen_seconds, endpoint.observed_seconds
        )
        accumulator.closest_distance_nm = min(
            accumulator.closest_distance_nm, endpoint_distance_nm
        )
        evidence_key = (ident, endpoint.observed_seconds)
        if not endpoint.is_ground and evidence_key not in endpoint_evidence:
            accumulator.inferred_endpoint_count += 1
            endpoint_evidence.add(evidence_key)

        other_points = track_points[1:] if direction == "departure" else track_points[:-1]
        if _track_moves_outside_airport(
            other_points,
            airport_index=airport_index,
            airport_ident=ident,
            config=config,
        ):
            if direction == "departure":
                accumulator.departure_count = 1
            else:
                accumulator.arrival_count = 1

    primary_ident: str | None = None
    if airports:
        primary_ident = max(
            airports,
            key=lambda ident: (
                airports[ident].ground_observation_count > 0,
                airports[ident].ground_time_seconds,
                airports[ident].arrival_count + airports[ident].departure_count,
                airports[ident].ground_observation_count,
                airports[ident].inferred_endpoint_count,
                -airports[ident].closest_distance_nm,
                ident,
            ),
        )

    presences = tuple(
        AirportPresenceSummary(
            utc_date=utc_date,
            airport_ident=ident,
            address=address,
            is_primary_airport=ident == primary_ident,
            first_seen_at=_timestamp(item.first_seen_seconds),
            last_seen_at=_timestamp(item.last_seen_seconds),
            presence_count=item.presence_count,
            ground_observation_count=item.ground_observation_count,
            ground_time_seconds=round(item.ground_time_seconds),
            ground_active_time_seconds=round(item.ground_active_time_seconds),
            closest_distance_nm=item.closest_distance_nm,
            inferred_endpoint_count=item.inferred_endpoint_count,
            arrival_count=item.arrival_count,
            departure_count=item.departure_count,
            link_method=(
                "GROUND_AND_ENDPOINT"
                if item.ground_observation_count and item.inferred_endpoint_count
                else "GROUND"
                if item.ground_observation_count
                else "INFERRED_ENDPOINT"
            ),
        )
        for ident, item in sorted(airports.items())
    )

    summary = AircraftDaySummary(
        utc_date=utc_date,
        address=address,
        address_kind="NON_ICAO" if address.startswith("~") else "ICAO",
        registration=_optional_text(payload.get("r")),
        type_code=_optional_text(payload.get("t")),
        type_description=_optional_text(payload.get("desc")),
        owner_operator=_optional_text(payload.get("ownOp")),
        manufacture_year=_manufacture_year(payload.get("year")),
        db_flags=_optional_int(payload.get("dbFlags")),
        trace_format_version=_optional_text(payload.get("version")),
        callsigns=tuple(sorted(callsigns)),
        callsign_last_seen=callsign_last_seen,
        position_source_types=tuple(sorted(source_types)),
        first_seen_at=_timestamp(first_seconds),
        last_seen_at=_timestamp(last_seconds),
        observation_count=observation_count,
        position_count=position_count,
        ground_observation_count=ground_observation_count,
        min_altitude_ft=round(min_altitude) if min_altitude is not None else None,
        max_altitude_ft=round(max_altitude) if max_altitude is not None else None,
        max_ground_speed_knots=max_ground_speed,
        first_latitude=first_position[0] if first_position else None,
        first_longitude=first_position[1] if first_position else None,
        last_latitude=last_position[0] if last_position else None,
        last_longitude=last_position[1] if last_position else None,
        time_observed_seconds=round(max(0.0, last_seconds - first_seconds)),
        active_time_seconds=round(active_seconds),
        airborne_time_seconds=round(airborne_seconds),
        ground_active_time_seconds=round(ground_active_seconds),
        distinct_airports=len(presences),
        airport_presence_count=sum(item.presence_count for item in presences),
        estimated_distance_nm=estimated_distance if distance_segments else None,
        source_trace_gzip_bytes=trace_payload.gzip_bytes,
        source_trace_json_bytes=trace_payload.json_bytes,
    )
    return summary, presences
