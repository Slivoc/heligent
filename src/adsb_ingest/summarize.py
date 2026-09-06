from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterator

from .airports import AirportIndex, AirportMatch, haversine_nm
from .archive import TracePayload


ADDRESS_RE = re.compile(r"^(?:~)?[0-9a-f]{6}$")
DERIVATION_VERSION = "flight-visits-v2-tracks"


@dataclass(frozen=True)
class ActivityConfig:
    max_continuous_gap_seconds: float = 120.0
    airport_ground_gap_seconds: float = 5 * 60.0
    active_speed_knots: float = 5.0
    airport_endpoint_max_agl_ft: float = 1_500.0
    airport_endpoint_max_speed_knots: float = 200.0
    airport_contact_max_agl_ft: float = 500.0
    airport_contact_max_speed_knots: float = 100.0
    airport_movement_exit_margin_nm: float = 1.0
    airport_movement_exit_multiplier: float = 1.5
    flight_discontinuity_gap_seconds: float = 30 * 60.0
    flight_endpoint_link_seconds: float = 30 * 60.0
    flight_boundary_interpolation_seconds: float = 15 * 60.0
    max_plausible_speed_knots: float = 1_500.0
    calculate_distance: bool = False

    def __post_init__(self) -> None:
        positive = (
            self.max_continuous_gap_seconds,
            self.airport_ground_gap_seconds,
            self.airport_endpoint_max_agl_ft,
            self.airport_endpoint_max_speed_knots,
            self.airport_contact_max_agl_ft,
            self.airport_contact_max_speed_knots,
            self.airport_movement_exit_margin_nm,
            self.airport_movement_exit_multiplier,
            self.flight_discontinuity_gap_seconds,
            self.flight_endpoint_link_seconds,
            self.flight_boundary_interpolation_seconds,
            self.max_plausible_speed_knots,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Activity gaps and maximum speed must be positive")
        if self.active_speed_knots < 0:
            raise ValueError("Active speed threshold cannot be negative")
        if self.flight_discontinuity_gap_seconds <= self.max_continuous_gap_seconds:
            raise ValueError("Flight discontinuity gap must exceed the continuous-time gap")


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


@dataclass(frozen=True)
class AirportVisitSummary:
    utc_date: date
    address: str
    visit_sequence: int
    airport_ident: str
    first_evidence_at: datetime
    last_evidence_at: datetime
    arrived_at: datetime | None
    departed_at: datetime | None
    ground_observation_count: int
    proximity_observation_count: int
    ground_time_seconds: int
    ground_active_time_seconds: int
    closest_distance_nm: float
    arrival_evidence: str | None
    departure_evidence: str | None
    open_at_start: bool
    open_at_end: bool
    confidence: str
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class FlightSegmentSummary:
    utc_date: date
    address: str
    segment_sequence: int
    first_airborne_at: datetime
    last_airborne_at: datetime
    takeoff_at: datetime
    landing_at: datetime
    origin_airport_ident: str | None
    destination_airport_ident: str | None
    origin_evidence: str | None
    destination_evidence: str | None
    observation_count: int
    observed_airborne_seconds: int
    elapsed_airborne_seconds: int
    unobserved_seconds: int
    estimated_distance_nm: float | None
    max_altitude_ft: int | None
    max_ground_speed_knots: float | None
    callsigns: tuple[str, ...]
    starts_before_window: bool
    ends_after_window: bool
    confidence: str
    quality_flags: tuple[str, ...]
    track: dict[str, Any] | None = None


@dataclass(frozen=True)
class TraceSummary:
    aircraft_day: AircraftDaySummary
    airport_presences: tuple[AirportPresenceSummary, ...]
    flight_segments: tuple[FlightSegmentSummary, ...]
    airport_visits: tuple[AirportVisitSummary, ...]

    # Keep the original two-value unpacking contract for small scripts and tests.
    def __iter__(self) -> Iterator[object]:
        yield self.aircraft_day
        yield self.airport_presences

    def __getitem__(self, index: int) -> object:
        return (self.aircraft_day, self.airport_presences)[index]

    def __len__(self) -> int:
        return 2


@dataclass(frozen=True)
class _Point:
    observed_seconds: float
    latitude: float | None
    longitude: float | None
    is_ground: bool
    is_airborne: bool
    altitude_ft: float | None
    ground_speed_knots: float | None
    callsign: str | None


@dataclass(frozen=True)
class _AirportContact:
    point_index: int
    airport_ident: str
    distance_nm: float
    evidence: str


def compact_track(points: list[_Point], config: ActivityConfig) -> dict[str, Any]:
    """Keep observed coordinates, not invented airport connectors or gap interpolation.

    At most one sample per 15s plus each continuous run's endpoints. Break on
    missing fixes, reception gaps, implausible jumps and the antimeridian.
    Cap output per flight; an explicit truncation flag prevents silent coverage claims.
    """
    lines: list[list[list[float | int | None]]] = []
    line: list[list[float | int | None]] = []
    previous = None
    count = 0
    truncated = False

    def sample(p):
        return [round(p.observed_seconds, 3), round(p.latitude, 5),
                round(p.longitude, 5), round(p.altitude_ft) if p.altitude_ft is not None else None]

    def finish():
        nonlocal line, count, truncated
        if line:
            if previous is not None and line[-1][0] != round(previous.observed_seconds, 3):
                line.append(sample(previous))
            available = max(0, 2048 - count)
            if len(line) > available:
                truncated = True
            if available:
                lines.append(line[:available])
                count += len(lines[-1])
        line = []

    for point in points:
        if point.latitude is None or point.longitude is None:
            finish()
            previous = None
            continue
        if previous is not None:
            gap = point.observed_seconds - previous.observed_seconds
            jump = haversine_nm(previous.latitude, previous.longitude, point.latitude, point.longitude)
            if (gap <= 0 or gap > config.max_continuous_gap_seconds
                    or abs(point.longitude - previous.longitude) > 180
                    or jump * 3600 / max(gap, 0.001) > config.max_plausible_speed_knots):
                finish()
        if not line or point.observed_seconds - line[-1][0] >= 15:
            line.append(sample(point))
        previous = point
    finish()
    return {'version': 'observed-track-v1', 'segments': lines,
            'input_count': len(points), 'retained_count': count, 'truncated': truncated,
            'sample_interval_seconds': 15, 'gap_seconds': config.max_continuous_gap_seconds}


@dataclass
class _VisitCandidate:
    airport_ident: str
    contacts: list[_AirportContact]
    entry_point_index: int | None = None
    exit_point_index: int | None = None


def _endpoint_airport_match(
    point: _Point,
    airport_index: AirportIndex,
    config: ActivityConfig,
    *,
    preferred_ident: str | None = None,
) -> AirportMatch | None:
    if point.latitude is None or point.longitude is None:
        return None
    match = airport_index.match(
        point.latitude,
        point.longitude,
        preferred_ident=preferred_ident,
    )
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


def _airport_contact_match(
    point: _Point,
    airport_index: AirportIndex,
    config: ActivityConfig,
    *,
    preferred_ident: str | None,
    broad_endpoint: bool,
) -> tuple[AirportMatch, str] | None:
    if point.latitude is None or point.longitude is None:
        return None
    if point.is_ground:
        match = airport_index.match(
            point.latitude,
            point.longitude,
            preferred_ident=preferred_ident,
        )
        return (match, "GROUND") if match is not None else None
    if point.altitude_ft is None:
        return None
    if (
        point.ground_speed_knots is not None
        and point.ground_speed_knots > config.airport_contact_max_speed_knots
        and not broad_endpoint
    ):
        return None
    match = airport_index.match(
        point.latitude,
        point.longitude,
        preferred_ident=preferred_ident,
    )
    if match is None:
        return None
    elevation_ft = match.airport.elevation_ft or 0
    max_agl = (
        config.airport_endpoint_max_agl_ft
        if broad_endpoint
        else config.airport_contact_max_agl_ft
    )
    max_speed = (
        config.airport_endpoint_max_speed_knots
        if broad_endpoint
        else config.airport_contact_max_speed_knots
    )
    if point.altitude_ft - elevation_ft > max_agl:
        return None
    if point.ground_speed_knots is not None and point.ground_speed_knots > max_speed:
        return None
    return match, "INFERRED_ENDPOINT" if broad_endpoint else "LOW_SLOW_PROXIMITY"


def _airport_exit_radius_nm(
    airport_index: AirportIndex,
    airport_ident: str,
    config: ActivityConfig,
) -> float:
    airport = airport_index.airports[airport_ident]
    match_radius_nm = airport_index.radii_nm[airport.airport_type]
    return max(
        match_radius_nm + config.airport_movement_exit_margin_nm,
        match_radius_nm * config.airport_movement_exit_multiplier,
    )


def _point_is_outside_airport(
    point: _Point,
    *,
    airport_index: AirportIndex,
    airport_ident: str,
    config: ActivityConfig,
) -> bool:
    if point.latitude is None or point.longitude is None:
        return False
    airport = airport_index.airports[airport_ident]
    return haversine_nm(
        point.latitude,
        point.longitude,
        airport.latitude_deg,
        airport.longitude_deg,
    ) >= _airport_exit_radius_nm(airport_index, airport_ident, config)


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


def _midpoint_timestamp(
    first: _Point,
    second: _Point,
    *,
    maximum_gap_seconds: float,
    fallback_seconds: float,
) -> float:
    delta = second.observed_seconds - first.observed_seconds
    if 0 < delta <= maximum_gap_seconds:
        return first.observed_seconds + delta / 2
    return fallback_seconds


def _find_prior_outside_point(
    points: list[_Point],
    point_index: int,
    airport_ident: str,
    airport_index: AirportIndex,
    config: ActivityConfig,
) -> int | None:
    for candidate_index in range(point_index - 1, -1, -1):
        candidate = points[candidate_index]
        if candidate.latitude is None or candidate.longitude is None:
            continue
        if _point_is_outside_airport(
            candidate,
            airport_index=airport_index,
            airport_ident=airport_ident,
            config=config,
        ):
            return candidate_index
    return None


def _build_airport_visits(
    points: list[_Point],
    *,
    utc_date: date,
    address: str,
    airport_index: AirportIndex,
    config: ActivityConfig,
) -> tuple[tuple[AirportVisitSummary, ...], dict[int, tuple[int, _AirportContact]]]:
    positioned = [
        index
        for index, point in enumerate(points)
        if point.latitude is not None and point.longitude is not None
    ]
    endpoint_indices = {positioned[0], positioned[-1]} if positioned else set()
    candidates: list[_VisitCandidate] = []
    current: _VisitCandidate | None = None

    def close_current(exit_point_index: int | None) -> None:
        nonlocal current
        if current is None:
            return
        current.exit_point_index = exit_point_index
        candidates.append(current)
        current = None

    for point_index, point in enumerate(points):
        if point.latitude is None or point.longitude is None:
            continue
        preferred_ident = current.airport_ident if current is not None else None
        contact_result = _airport_contact_match(
            point,
            airport_index,
            config,
            preferred_ident=preferred_ident,
            broad_endpoint=point_index in endpoint_indices,
        )
        contact = None
        if contact_result is not None:
            match, evidence = contact_result
            contact = _AirportContact(
                point_index=point_index,
                airport_ident=match.airport.ident,
                distance_nm=match.distance_nm,
                evidence=evidence,
            )

        if current is not None and contact is not None:
            if contact.airport_ident == current.airport_ident:
                current.contacts.append(contact)
                continue
            if not _point_is_outside_airport(
                point,
                airport_index=airport_index,
                airport_ident=current.airport_ident,
                config=config,
            ):
                # Airport footprints can overlap. Continuity wins until the track
                # genuinely exits the current airport's hysteresis radius.
                continue

        if current is not None and _point_is_outside_airport(
            point,
            airport_index=airport_index,
            airport_ident=current.airport_ident,
            config=config,
        ):
            close_current(point_index)

        if current is None and contact is not None:
            current = _VisitCandidate(
                airport_ident=contact.airport_ident,
                contacts=[contact],
                entry_point_index=_find_prior_outside_point(
                    points,
                    point_index,
                    contact.airport_ident,
                    airport_index,
                    config,
                ),
            )

    close_current(None)

    visits: list[AirportVisitSummary] = []
    contact_map: dict[int, tuple[int, _AirportContact]] = {}
    for sequence, candidate in enumerate(candidates, start=1):
        contacts = candidate.contacts
        first_contact = contacts[0]
        last_contact = contacts[-1]
        first_point = points[first_contact.point_index]
        last_point = points[last_contact.point_index]
        ground_contacts = [
            contact for contact in contacts if points[contact.point_index].is_ground
        ]
        ground_seconds = 0.0
        ground_active_seconds = 0.0
        for previous_contact, contact in zip(contacts, contacts[1:]):
            previous_point = points[previous_contact.point_index]
            point = points[contact.point_index]
            delta = point.observed_seconds - previous_point.observed_seconds
            continuously_grounded = all(
                intermediate.is_ground
                for intermediate in points[
                    previous_contact.point_index : contact.point_index + 1
                ]
            )
            if (
                previous_point.is_ground
                and point.is_ground
                and continuously_grounded
                and 0 < delta <= config.airport_ground_gap_seconds
            ):
                ground_seconds += delta
                if (
                    (previous_point.ground_speed_knots or 0) >= config.active_speed_knots
                    or (point.ground_speed_knots or 0) >= config.active_speed_knots
                ):
                    ground_active_seconds += delta

        entry_point = (
            points[candidate.entry_point_index]
            if candidate.entry_point_index is not None
            else None
        )
        exit_point = (
            points[candidate.exit_point_index]
            if candidate.exit_point_index is not None
            else None
        )
        arrived_seconds = (
            _midpoint_timestamp(
                entry_point,
                first_point,
                maximum_gap_seconds=config.flight_boundary_interpolation_seconds,
                fallback_seconds=first_point.observed_seconds,
            )
            if entry_point is not None
            else None
        )
        departed_seconds = (
            _midpoint_timestamp(
                last_point,
                exit_point,
                maximum_gap_seconds=config.flight_boundary_interpolation_seconds,
                fallback_seconds=last_point.observed_seconds,
            )
            if exit_point is not None
            else None
        )
        quality_flags: list[str] = []
        if entry_point is None:
            quality_flags.append("OPEN_START")
        if exit_point is None:
            quality_flags.append("OPEN_END")
        if not ground_contacts:
            quality_flags.append("NO_GROUND_STATE")
        if len(contacts) == 1:
            quality_flags.append("SINGLE_CONTACT")
        if (
            entry_point is not None
            and first_point.observed_seconds - entry_point.observed_seconds
            > config.max_continuous_gap_seconds
        ) or (
            exit_point is not None
            and exit_point.observed_seconds - last_point.observed_seconds
            > config.max_continuous_gap_seconds
        ):
            quality_flags.append("BOUNDARY_COVERAGE_GAP")
        if len(ground_contacts) >= 2:
            confidence = "HIGH"
        elif ground_contacts or (
            len(contacts) >= 2 and entry_point is not None and exit_point is not None
        ):
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        visit = AirportVisitSummary(
            utc_date=utc_date,
            address=address,
            visit_sequence=sequence,
            airport_ident=candidate.airport_ident,
            first_evidence_at=_timestamp(first_point.observed_seconds),
            last_evidence_at=_timestamp(last_point.observed_seconds),
            arrived_at=_timestamp(arrived_seconds) if arrived_seconds is not None else None,
            departed_at=_timestamp(departed_seconds) if departed_seconds is not None else None,
            ground_observation_count=len(ground_contacts),
            proximity_observation_count=len(contacts),
            ground_time_seconds=round(ground_seconds),
            ground_active_time_seconds=round(ground_active_seconds),
            closest_distance_nm=min(contact.distance_nm for contact in contacts),
            arrival_evidence=(
                "TRACK_ENTRY_" + first_contact.evidence if entry_point is not None else None
            ),
            departure_evidence=(
                "TRACK_EXIT_" + last_contact.evidence if exit_point is not None else None
            ),
            open_at_start=entry_point is None,
            open_at_end=exit_point is None,
            confidence=confidence,
            quality_flags=tuple(quality_flags),
        )
        visits.append(visit)
        for contact in contacts:
            contact_map[contact.point_index] = (sequence - 1, contact)

    return tuple(visits), contact_map


def _build_flight_segments(
    points: list[_Point],
    *,
    utc_date: date,
    address: str,
    airport_visits: tuple[AirportVisitSummary, ...],
    contact_map: dict[int, tuple[int, _AirportContact]],
    config: ActivityConfig,
    retain_track: bool = True,
) -> tuple[FlightSegmentSummary, ...]:
    runs: list[list[int]] = []
    current_run: list[int] = []
    for point_index, point in enumerate(points):
        is_flight_observation = point.is_airborne and point_index not in contact_map
        if not is_flight_observation:
            if current_run:
                runs.append(current_run)
                current_run = []
            continue
        if current_run:
            previous = points[current_run[-1]]
            if (
                point.observed_seconds - previous.observed_seconds
                > config.flight_discontinuity_gap_seconds
            ):
                runs.append(current_run)
                current_run = []
        current_run.append(point_index)
    if current_run:
        runs.append(current_run)

    contact_indices = sorted(contact_map)
    segments: list[FlightSegmentSummary] = []
    for run in runs:
        first_index = run[0]
        last_index = run[-1]
        first_point = points[first_index]
        last_point = points[last_index]
        prior_boundary = points[first_index - 1] if first_index > 0 else None
        next_boundary = points[last_index + 1] if last_index + 1 < len(points) else None
        starts_before_window = (
            prior_boundary is None
            or prior_boundary.is_airborne
            or first_point.observed_seconds - prior_boundary.observed_seconds
            > config.flight_discontinuity_gap_seconds
        )
        ends_after_window = (
            next_boundary is None
            or next_boundary.is_airborne
            or next_boundary.observed_seconds - last_point.observed_seconds
            > config.flight_discontinuity_gap_seconds
        )
        takeoff_seconds = (
            _midpoint_timestamp(
                prior_boundary,
                first_point,
                maximum_gap_seconds=config.flight_boundary_interpolation_seconds,
                fallback_seconds=first_point.observed_seconds,
            )
            if prior_boundary is not None and not starts_before_window
            else first_point.observed_seconds
        )
        landing_seconds = (
            _midpoint_timestamp(
                last_point,
                next_boundary,
                maximum_gap_seconds=config.flight_boundary_interpolation_seconds,
                fallback_seconds=last_point.observed_seconds,
            )
            if next_boundary is not None and not ends_after_window
            else last_point.observed_seconds
        )

        prior_position = bisect_left(contact_indices, first_index)
        next_position = bisect_right(contact_indices, last_index)
        prior_contact_index = (
            contact_indices[prior_position - 1] if prior_position > 0 else None
        )
        next_contact_index = (
            contact_indices[next_position]
            if next_position < len(contact_indices)
            else None
        )
        origin_ident: str | None = None
        destination_ident: str | None = None
        origin_evidence: str | None = None
        destination_evidence: str | None = None
        origin_visit: AirportVisitSummary | None = None
        destination_visit: AirportVisitSummary | None = None
        if prior_contact_index is not None:
            visit_index, contact = contact_map[prior_contact_index]
            if (
                first_point.observed_seconds - points[prior_contact_index].observed_seconds
                <= config.flight_endpoint_link_seconds
            ):
                origin_visit = airport_visits[visit_index]
                origin_ident = contact.airport_ident
                origin_evidence = contact.evidence
        if next_contact_index is not None:
            visit_index, contact = contact_map[next_contact_index]
            if (
                points[next_contact_index].observed_seconds - last_point.observed_seconds
                <= config.flight_endpoint_link_seconds
            ):
                destination_visit = airport_visits[visit_index]
                destination_ident = contact.airport_ident
                destination_evidence = contact.evidence

        observed_seconds = 0.0
        estimated_distance = 0.0
        distance_segments = 0
        has_coverage_gap = False
        for previous_index, point_index in zip(run, run[1:]):
            previous = points[previous_index]
            point = points[point_index]
            delta = point.observed_seconds - previous.observed_seconds
            if 0 < delta <= config.max_continuous_gap_seconds:
                observed_seconds += delta
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
            elif delta > config.max_continuous_gap_seconds:
                has_coverage_gap = True

        boundary_gap = (
            prior_boundary is not None
            and first_point.observed_seconds - prior_boundary.observed_seconds
            > config.max_continuous_gap_seconds
        ) or (
            next_boundary is not None
            and next_boundary.observed_seconds - last_point.observed_seconds
            > config.max_continuous_gap_seconds
        )
        quality_flags: list[str] = []
        if starts_before_window:
            quality_flags.append("OPEN_START")
        if ends_after_window:
            quality_flags.append("OPEN_END")
        if origin_ident is None:
            quality_flags.append("UNKNOWN_ORIGIN")
        elif origin_visit is not None and origin_visit.departed_at is None:
            quality_flags.append("ORIGIN_WITHOUT_TRACK_EXIT")
        if destination_ident is None:
            quality_flags.append("UNKNOWN_DESTINATION")
        elif destination_visit is not None and destination_visit.arrived_at is None:
            quality_flags.append("DESTINATION_WITHOUT_TRACK_ENTRY")
        if len(run) == 1:
            quality_flags.append("SINGLE_AIRBORNE_OBSERVATION")
        if has_coverage_gap or boundary_gap:
            quality_flags.append("COVERAGE_GAP")

        if (
            origin_evidence == "GROUND"
            and destination_evidence == "GROUND"
            and len(run) >= 2
            and not has_coverage_gap
            and not boundary_gap
        ):
            confidence = "HIGH"
        elif (
            (origin_ident is not None or destination_ident is not None)
            and len(run) >= 2
            and not has_coverage_gap
        ):
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        elapsed_seconds = round(max(0.0, landing_seconds - takeoff_seconds))
        observed_rounded = round(observed_seconds)
        altitude_values = [
            points[index].altitude_ft
            for index in run
            if points[index].altitude_ft is not None
        ]
        speed_values = [
            points[index].ground_speed_knots
            for index in run
            if points[index].ground_speed_knots is not None
        ]
        segments.append(
            FlightSegmentSummary(
                utc_date=utc_date,
                address=address,
                segment_sequence=len(segments) + 1,
                first_airborne_at=_timestamp(first_point.observed_seconds),
                last_airborne_at=_timestamp(last_point.observed_seconds),
                takeoff_at=_timestamp(takeoff_seconds),
                landing_at=_timestamp(landing_seconds),
                origin_airport_ident=origin_ident,
                destination_airport_ident=destination_ident,
                origin_evidence=origin_evidence,
                destination_evidence=destination_evidence,
                observation_count=len(run),
                observed_airborne_seconds=observed_rounded,
                elapsed_airborne_seconds=elapsed_seconds,
                unobserved_seconds=max(0, elapsed_seconds - observed_rounded),
                estimated_distance_nm=estimated_distance if distance_segments else None,
                max_altitude_ft=round(max(altitude_values)) if altitude_values else None,
                max_ground_speed_knots=max(speed_values) if speed_values else None,
                callsigns=tuple(
                    sorted({points[index].callsign for index in run if points[index].callsign})
                ),
                starts_before_window=starts_before_window,
                ends_after_window=ends_after_window,
                confidence=confidence,
                quality_flags=tuple(quality_flags),
                track=compact_track([points[index] for index in run], config) if retain_track else None,
            )
        )
    return tuple(segments)


def _aggregate_airport_presences(
    visits: tuple[AirportVisitSummary, ...],
    *,
    utc_date: date,
    address: str,
) -> tuple[AirportPresenceSummary, ...]:
    by_airport: dict[str, list[AirportVisitSummary]] = {}
    for visit in visits:
        by_airport.setdefault(visit.airport_ident, []).append(visit)
    primary_ident: str | None = None
    if by_airport:
        primary_ident = max(
            by_airport,
            key=lambda ident: (
                sum(item.ground_observation_count for item in by_airport[ident]) > 0,
                sum(item.ground_time_seconds for item in by_airport[ident]),
                sum(
                    int(item.arrived_at is not None) + int(item.departed_at is not None)
                    for item in by_airport[ident]
                ),
                sum(item.proximity_observation_count for item in by_airport[ident]),
                -min(item.closest_distance_nm for item in by_airport[ident]),
                ident,
            ),
        )

    presences: list[AirportPresenceSummary] = []
    for ident, airport_visits in sorted(by_airport.items()):
        ground_count = sum(item.ground_observation_count for item in airport_visits)
        inferred_count = sum(
            len({item.first_evidence_at, item.last_evidence_at})
            for item in airport_visits
            if item.ground_observation_count == 0
        )
        presences.append(
            AirportPresenceSummary(
                utc_date=utc_date,
                airport_ident=ident,
                address=address,
                is_primary_airport=ident == primary_ident,
                first_seen_at=min(item.first_evidence_at for item in airport_visits),
                last_seen_at=max(item.last_evidence_at for item in airport_visits),
                presence_count=len(airport_visits),
                ground_observation_count=ground_count,
                ground_time_seconds=sum(item.ground_time_seconds for item in airport_visits),
                ground_active_time_seconds=sum(
                    item.ground_active_time_seconds for item in airport_visits
                ),
                closest_distance_nm=min(
                    item.closest_distance_nm for item in airport_visits
                ),
                inferred_endpoint_count=inferred_count,
                arrival_count=sum(item.arrived_at is not None for item in airport_visits),
                departure_count=sum(item.departed_at is not None for item in airport_visits),
                link_method=(
                    "GROUND_AND_ENDPOINT"
                    if ground_count and inferred_count
                    else "GROUND"
                    if ground_count
                    else "INFERRED_ENDPOINT"
                ),
            )
        )
    return tuple(presences)


def summarize_trace(
    trace_payload: TracePayload,
    utc_date: date,
    airport_index: AirportIndex,
    *,
    config: ActivityConfig | None = None,
    retain_track: bool = True,
) -> TraceSummary:
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
    callsign_sightings: list[tuple[float, str]] = []
    source_types: set[str] = set()
    points: list[_Point] = []
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
        speed = _number(row[4]) if len(row) > 4 else None
        detail = row[8] if len(row) > 8 else None
        callsign = _optional_text(detail.get("flight")) if isinstance(detail, dict) else None
        if callsign:
            callsigns.add(callsign)
            callsign_sightings.append((observed, callsign))
        if len(row) > 9 and row[9] is not None:
            source_types.add(str(row[9]))
        points.append(
            _Point(
                observed_seconds=observed,
                latitude=latitude,
                longitude=longitude,
                is_ground=is_ground,
                is_airborne=numeric_altitude is not None and not is_ground,
                altitude_ft=numeric_altitude,
                ground_speed_knots=speed,
                callsign=callsign,
            )
        )

    if not points:
        raise ValueError(f"Trace {trace_payload.archive_name} has no usable observations")
    points.sort(key=lambda point: point.observed_seconds)
    positioned_points = [
        point for point in points if point.latitude is not None and point.longitude is not None
    ]
    numeric_altitudes = [point.altitude_ft for point in points if point.altitude_ft is not None]
    speeds = [point.ground_speed_knots for point in points if point.ground_speed_knots is not None]
    active_seconds = 0.0
    airborne_seconds = 0.0
    ground_active_seconds = 0.0
    estimated_distance = 0.0
    distance_segments = 0
    for previous, point in zip(points, points[1:]):
        delta = point.observed_seconds - previous.observed_seconds
        if not 0 < delta <= config.max_continuous_gap_seconds:
            continue
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

    visits, contact_map = _build_airport_visits(
        points,
        utc_date=utc_date,
        address=address,
        airport_index=airport_index,
        config=config,
    )
    segments = _build_flight_segments(
        points,
        utc_date=utc_date,
        address=address,
        airport_visits=visits,
        contact_map=contact_map,
        config=config,
        retain_track=retain_track,
    )
    presences = _aggregate_airport_presences(
        visits,
        utc_date=utc_date,
        address=address,
    )
    first_seconds = points[0].observed_seconds
    last_seconds = points[-1].observed_seconds
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
        callsign_last_seen=(max(callsign_sightings)[1] if callsign_sightings else None),
        position_source_types=tuple(sorted(source_types)),
        first_seen_at=_timestamp(first_seconds),
        last_seen_at=_timestamp(last_seconds),
        observation_count=len(points),
        position_count=len(positioned_points),
        ground_observation_count=sum(point.is_ground for point in points),
        min_altitude_ft=round(min(numeric_altitudes)) if numeric_altitudes else None,
        max_altitude_ft=round(max(numeric_altitudes)) if numeric_altitudes else None,
        max_ground_speed_knots=max(speeds) if speeds else None,
        first_latitude=positioned_points[0].latitude if positioned_points else None,
        first_longitude=positioned_points[0].longitude if positioned_points else None,
        last_latitude=positioned_points[-1].latitude if positioned_points else None,
        last_longitude=positioned_points[-1].longitude if positioned_points else None,
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
    return TraceSummary(summary, presences, segments, visits)
