from __future__ import annotations

import hashlib
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .adsblol import DayReleases
from .airports import AirportCatalog
from .summarize import DERIVATION_VERSION, TraceSummary


AIRCRAFT_STAGE_COLUMNS = (
    "address",
    "address_kind",
    "registration",
    "callsign_last_seen",
    "type_code",
    "type_description",
    "owner_operator",
    "manufacture_year",
    "db_flags",
    "first_seen_date",
    "last_seen_date",
)

AIRCRAFT_DAY_STAGE_COLUMNS = (
    "dataset_day_id",
    "utc_date",
    "address",
    "registration",
    "type_code",
    "type_description",
    "owner_operator",
    "manufacture_year",
    "db_flags",
    "trace_format_version",
    "callsigns",
    "position_source_types",
    "first_seen_at",
    "last_seen_at",
    "observation_count",
    "position_count",
    "ground_observation_count",
    "min_altitude_ft",
    "max_altitude_ft",
    "max_ground_speed_knots",
    "first_latitude",
    "first_longitude",
    "last_latitude",
    "last_longitude",
    "time_observed_seconds",
    "active_time_seconds",
    "airborne_time_seconds",
    "ground_active_time_seconds",
    "distinct_airports",
    "airport_presence_count",
    "estimated_distance_nm",
    "source_trace_gzip_bytes",
    "source_trace_json_bytes",
)

AIRPORT_DAY_STAGE_COLUMNS = (
    "dataset_day_id",
    "utc_date",
    "airport_ident",
    "address",
    "is_primary_airport",
    "first_seen_at",
    "last_seen_at",
    "presence_count",
    "ground_observation_count",
    "ground_time_seconds",
    "ground_active_time_seconds",
    "closest_distance_nm",
    "inferred_endpoint_count",
    "arrival_count",
    "departure_count",
    "link_method",
)

FLIGHT_SEGMENT_STAGE_COLUMNS = (
    "dataset_day_id",
    "utc_date",
    "address",
    "segment_sequence",
    "first_airborne_at",
    "last_airborne_at",
    "takeoff_at",
    "landing_at",
    "origin_airport_ident",
    "destination_airport_ident",
    "origin_evidence",
    "destination_evidence",
    "observation_count",
    "observed_airborne_seconds",
    "elapsed_airborne_seconds",
    "unobserved_seconds",
    "estimated_distance_nm",
    "max_altitude_ft",
    "max_ground_speed_knots",
    "callsigns",
    "starts_before_window",
    "ends_after_window",
    "confidence",
    "quality_flags",
    "track",
)

AIRPORT_VISIT_STAGE_COLUMNS = (
    "dataset_day_id",
    "utc_date",
    "address",
    "visit_sequence",
    "airport_ident",
    "first_evidence_at",
    "last_evidence_at",
    "arrived_at",
    "departed_at",
    "ground_observation_count",
    "proximity_observation_count",
    "ground_time_seconds",
    "ground_active_time_seconds",
    "closest_distance_nm",
    "arrival_evidence",
    "departure_evidence",
    "open_at_start",
    "open_at_end",
    "confidence",
    "quality_flags",
)


def _copy_rows(
    connection: Connection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    column_sql = ", ".join(columns)
    with connection.cursor().copy(f"COPY {table} ({column_sql}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def connect(self, *, autocommit: bool = False) -> Connection:
        return psycopg.connect(self.dsn, autocommit=autocommit)

    def apply_schema(self, schema_path: Path) -> None:
        sql = schema_path.read_text(encoding="utf-8")
        with self.connect(autocommit=True) as connection:
            connection.execute(sql)

    def apply_schema_once(self, schema_path: Path) -> bool:
        """Apply a changed migration once, serialized across app processes."""

        sql = schema_path.read_text(encoding="utf-8")
        content_sha256 = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migration_name = schema_path.name
        lock_id = 1_767_046_213
        with self.connect(autocommit=True) as connection:
            connection.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS heligent_schema_migration (
                        migration_name text PRIMARY KEY,
                        content_sha256 char(64) NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
                    )
                    """
                )
                row = connection.execute(
                    """
                    SELECT content_sha256
                    FROM heligent_schema_migration
                    WHERE migration_name = %s
                    """,
                    (migration_name,),
                ).fetchone()
                if row is not None and row[0] == content_sha256:
                    return False
                try:
                    connection.execute(sql)
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute(
                    """
                    INSERT INTO heligent_schema_migration (
                        migration_name, content_sha256, applied_at
                    ) VALUES (%s, %s, clock_timestamp())
                    ON CONFLICT (migration_name) DO UPDATE SET
                        content_sha256 = EXCLUDED.content_sha256,
                        applied_at = EXCLUDED.applied_at
                    """,
                    (migration_name, content_sha256),
                )
                return True
            finally:
                connection.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))

    def get_dataset(self, utc_date: date) -> dict[str, object] | None:
        with self.connect() as connection:
            connection.row_factory = dict_row
            return connection.execute(
                """
                SELECT *
                FROM dataset_day
                WHERE utc_date = %s
                  AND source_code = 'ADSB_LOL'
                  AND source_selection = 'PREFERRED'
                """,
                (utc_date,),
            ).fetchone()

    def upsert_dataset(self, discovery: DayReleases) -> int:
        release = discovery.preferred
        assets = [
            {
                "name": item.name,
                "size": item.size,
                "download_url": item.download_url,
                "digest": item.digest,
            }
            for item in release.assets
        ]
        with self.connect() as connection:
            row = connection.execute(
                """
                INSERT INTO dataset_day (
                    utc_date, source_code, source_selection, source_instance,
                    source_repository, source_release_tag, source_url, source_assets
                )
                VALUES (%s, 'ADSB_LOL', 'PREFERRED', %s, %s, %s, %s, %s)
                ON CONFLICT (utc_date, source_code, source_selection) DO UPDATE SET
                    source_instance = EXCLUDED.source_instance,
                    source_repository = EXCLUDED.source_repository,
                    source_release_tag = EXCLUDED.source_release_tag,
                    source_url = EXCLUDED.source_url,
                    source_assets = EXCLUDED.source_assets,
                    updated_at = clock_timestamp()
                RETURNING id
                """,
                (
                    discovery.utc_date,
                    release.instance,
                    discovery.repository,
                    release.tag,
                    release.html_url,
                    Jsonb(assets),
                ),
            ).fetchone()
            connection.commit()
            assert row is not None
            return int(row[0])

    def reconcile_interrupted(self, dataset_day_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE ingestion_job
                SET status = 'INTERRUPTED',
                    finished_at = clock_timestamp(),
                    error_message = COALESCE(error_message, 'Worker restarted while job was running')
                WHERE dataset_day_id = %s AND status = 'RUNNING'
                """,
                (dataset_day_id,),
            )
            connection.execute(
                """
                UPDATE dataset_day
                SET status = CASE
                        WHEN status = 'DOWNLOADING' THEN 'FAILED_DOWNLOAD'::dataset_status
                        WHEN status = 'PROCESSING' THEN 'FAILED_PROCESSING'::dataset_status
                        ELSE status
                    END,
                    error_stage = CASE
                        WHEN status = 'DOWNLOADING' THEN 'DOWNLOAD'
                        WHEN status = 'PROCESSING' THEN 'PROCESSING'
                        ELSE error_stage
                    END,
                    error_message = CASE
                        WHEN status IN ('DOWNLOADING', 'PROCESSING')
                            THEN 'Worker restarted while operation was running'
                        ELSE error_message
                    END,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (dataset_day_id,),
            )
            connection.commit()

    def start_job(self, dataset_day_id: int, job_type: str) -> int:
        with self.connect() as connection:
            attempt = connection.execute(
                "SELECT COALESCE(max(attempt), 0) + 1 FROM ingestion_job WHERE dataset_day_id = %s",
                (dataset_day_id,),
            ).fetchone()[0]
            row = connection.execute(
                """
                INSERT INTO ingestion_job (
                    dataset_day_id, job_type, status, attempt, started_at, heartbeat_at
                )
                VALUES (%s, %s, 'RUNNING', %s, clock_timestamp(), clock_timestamp())
                RETURNING id
                """,
                (dataset_day_id, job_type, attempt),
            ).fetchone()
            connection.commit()
            assert row is not None
            return int(row[0])

    def set_downloading(self, dataset_day_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE dataset_day
                SET status = 'DOWNLOADING',
                    download_started_at = COALESCE(download_started_at, clock_timestamp()),
                    error_stage = NULL, error_message = NULL, updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (dataset_day_id,),
            )
            connection.commit()

    def finish_download(
        self,
        dataset_day_id: int,
        job_id: int,
        *,
        raw_bytes: int,
        raw_file_count: int,
        downloaded_bytes_this_run: int,
        duration_ms: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE dataset_day
                SET status = 'DOWNLOADED',
                    download_finished_at = CASE
                        WHEN %s > 0 OR download_finished_at IS NULL THEN clock_timestamp()
                        ELSE download_finished_at
                    END,
                    raw_bytes = %s,
                    raw_file_count = %s,
                    download_duration_ms = CASE
                        WHEN %s > 0 OR download_duration_ms IS NULL THEN %s
                        ELSE download_duration_ms
                    END,
                    raw_deleted_at = CASE WHEN %s > 0 THEN NULL ELSE raw_deleted_at END,
                    raw_deleted_bytes = CASE WHEN %s > 0 THEN NULL ELSE raw_deleted_bytes END,
                    raw_delete_error = CASE WHEN %s > 0 THEN NULL ELSE raw_delete_error END,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    downloaded_bytes_this_run,
                    raw_bytes,
                    raw_file_count,
                    downloaded_bytes_this_run,
                    duration_ms,
                    downloaded_bytes_this_run,
                    downloaded_bytes_this_run,
                    downloaded_bytes_this_run,
                    dataset_day_id,
                ),
            )
            connection.execute(
                """
                UPDATE ingestion_job
                SET status = 'SUCCEEDED', progress_percent = 100,
                    status_message = 'Download verified', heartbeat_at = clock_timestamp(),
                    finished_at = clock_timestamp()
                WHERE id = %s
                """,
                (job_id,),
            )
            connection.commit()

    def set_processing(self, dataset_day_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE dataset_day
                SET status = 'PROCESSING', processing_started_at = clock_timestamp(),
                    processing_finished_at = NULL, error_stage = NULL, error_message = NULL,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (dataset_day_id,),
            )
            connection.commit()

    def fail_job(
        self,
        dataset_day_id: int,
        job_id: int,
        *,
        stage: str,
        message: str,
    ) -> None:
        dataset_status = "FAILED_DOWNLOAD" if stage == "DOWNLOAD" else "FAILED_PROCESSING"
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE dataset_day
                SET status = %s, error_stage = %s, error_message = %s,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (dataset_status, stage, message[:10_000], dataset_day_id),
            )
            connection.execute(
                """
                UPDATE ingestion_job
                SET status = 'FAILED', error_message = %s,
                    heartbeat_at = clock_timestamp(), finished_at = clock_timestamp()
                WHERE id = %s
                """,
                (message[:10_000], job_id),
            )
            connection.commit()

    def heartbeat(self, job_id: int, message: str) -> None:
        with self.connect(autocommit=True) as connection:
            connection.execute(
                """
                UPDATE ingestion_job
                SET heartbeat_at = clock_timestamp(), status_message = %s
                WHERE id = %s AND status = 'RUNNING'
                """,
                (message[:1_000], job_id),
            )

    def upsert_airports(self, catalog: AirportCatalog) -> int:
        source_code = "OURAIRPORTS"
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO reference_dataset (
                    code, source_url, sha256, downloaded_at, row_count, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    sha256 = EXCLUDED.sha256,
                    downloaded_at = EXCLUDED.downloaded_at,
                    row_count = EXCLUDED.row_count,
                    metadata = EXCLUDED.metadata
                """,
                (
                    source_code,
                    catalog.source_url,
                    catalog.sha256,
                    catalog.downloaded_at,
                    len(catalog.airports),
                    Jsonb({"license": "Public Domain", "provider": "OurAirports"}),
                ),
            )
            connection.execute(
                "CREATE TEMP TABLE airport_stage (LIKE airport INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            columns = (
                "ident",
                "airport_type",
                "name",
                "latitude_deg",
                "longitude_deg",
                "elevation_ft",
                "continent",
                "iso_country",
                "iso_region",
                "municipality",
                "scheduled_service",
                "gps_code",
                "iata_code",
                "local_code",
                "source_code",
            )
            _copy_rows(
                connection,
                "airport_stage",
                columns,
                (
                    (
                        airport.ident,
                        airport.airport_type,
                        airport.name,
                        airport.latitude_deg,
                        airport.longitude_deg,
                        airport.elevation_ft,
                        airport.continent,
                        airport.iso_country,
                        airport.iso_region,
                        airport.municipality,
                        airport.scheduled_service,
                        airport.gps_code,
                        airport.iata_code,
                        airport.local_code,
                        source_code,
                    )
                    for airport in catalog.airports
                ),
            )
            connection.execute(
                """
                INSERT INTO airport (
                    ident, airport_type, name, latitude_deg, longitude_deg, elevation_ft,
                    continent, iso_country, iso_region, municipality, scheduled_service,
                    gps_code, iata_code, local_code, source_code
                )
                SELECT
                    ident, airport_type, name, latitude_deg, longitude_deg, elevation_ft,
                    continent, iso_country, iso_region, municipality, scheduled_service,
                    gps_code, iata_code, local_code, source_code
                FROM airport_stage
                ON CONFLICT (ident) DO UPDATE SET
                    airport_type = EXCLUDED.airport_type,
                    name = EXCLUDED.name,
                    latitude_deg = EXCLUDED.latitude_deg,
                    longitude_deg = EXCLUDED.longitude_deg,
                    elevation_ft = EXCLUDED.elevation_ft,
                    continent = EXCLUDED.continent,
                    iso_country = EXCLUDED.iso_country,
                    iso_region = EXCLUDED.iso_region,
                    municipality = EXCLUDED.municipality,
                    scheduled_service = EXCLUDED.scheduled_service,
                    gps_code = EXCLUDED.gps_code,
                    iata_code = EXCLUDED.iata_code,
                    local_code = EXCLUDED.local_code,
                    source_code = EXCLUDED.source_code,
                    updated_at = clock_timestamp()
                """
            )
            connection.commit()
        return len(catalog.airports)

    def _relation_bytes(self, connection: Connection) -> int:
        row = connection.execute(
            """
            SELECT
                pg_total_relation_size('aircraft')
              + pg_total_relation_size('aircraft_day')
              + pg_total_relation_size('aircraft_airport_day')
              + pg_total_relation_size('aircraft_flight_segment')
              + pg_total_relation_size('aircraft_airport_visit')
            """
        ).fetchone()
        return int(row[0])

    def load_summaries(
        self,
        dataset_day_id: int,
        job_id: int,
        summaries: Iterator[TraceSummary],
        *,
        batch_size: int = 1_000,
        heartbeat_every: int = 5_000,
        derivation_version: str = DERIVATION_VERSION,
        derivation_config: dict[str, object] | None = None,
    ) -> dict[str, int]:
        started = time.monotonic()
        aircraft_count = 0
        observation_count = 0
        airport_presence_count = 0
        flight_segment_count = 0
        airport_visit_count = 0
        aircraft_rows: list[tuple[object, ...]] = []
        aircraft_day_rows: list[tuple[object, ...]] = []
        airport_day_rows: list[tuple[object, ...]] = []
        flight_segment_rows: list[tuple[object, ...]] = []
        airport_visit_rows: list[tuple[object, ...]] = []
        buffered_track_points = 0

        with self.connect() as connection:
            before_bytes = self._relation_bytes(connection)
            connection.execute("SET LOCAL statement_timeout = 0")
            connection.execute("SET LOCAL lock_timeout = '10s'")
            connection.execute(
                "CREATE TEMP TABLE aircraft_stage (LIKE aircraft INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            connection.execute(
                "CREATE TEMP TABLE aircraft_day_stage (LIKE aircraft_day INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            connection.execute(
                """
                CREATE TEMP TABLE aircraft_airport_day_stage
                (LIKE aircraft_airport_day INCLUDING DEFAULTS) ON COMMIT DROP
                """
            )
            connection.execute(
                """
                CREATE TEMP TABLE aircraft_flight_segment_stage
                (LIKE aircraft_flight_segment INCLUDING DEFAULTS) ON COMMIT DROP
                """
            )
            connection.execute(
                """
                CREATE TEMP TABLE aircraft_airport_visit_stage
                (LIKE aircraft_airport_visit INCLUDING DEFAULTS) ON COMMIT DROP
                """
            )

            def flush() -> None:
                nonlocal buffered_track_points
                buffered_track_points = 0
                if aircraft_rows:
                    _copy_rows(connection, "aircraft_stage", AIRCRAFT_STAGE_COLUMNS, aircraft_rows)
                    _copy_rows(
                        connection,
                        "aircraft_day_stage",
                        AIRCRAFT_DAY_STAGE_COLUMNS,
                        aircraft_day_rows,
                    )
                    aircraft_rows.clear()
                    aircraft_day_rows.clear()
                if airport_day_rows:
                    _copy_rows(
                        connection,
                        "aircraft_airport_day_stage",
                        AIRPORT_DAY_STAGE_COLUMNS,
                        airport_day_rows,
                    )
                    airport_day_rows.clear()
                if flight_segment_rows:
                    _copy_rows(
                        connection,
                        "aircraft_flight_segment_stage",
                        FLIGHT_SEGMENT_STAGE_COLUMNS,
                        flight_segment_rows,
                    )
                    flight_segment_rows.clear()
                if airport_visit_rows:
                    _copy_rows(
                        connection,
                        "aircraft_airport_visit_stage",
                        AIRPORT_VISIT_STAGE_COLUMNS,
                        airport_visit_rows,
                    )
                    airport_visit_rows.clear()

            for trace_summary in summaries:
                summary = trace_summary.aircraft_day
                presences = trace_summary.airport_presences
                segments = trace_summary.flight_segments
                buffered_track_points += sum((s.track or {}).get('retained_count', 0) for s in segments)
                visits = trace_summary.airport_visits
                aircraft_count += 1
                observation_count += summary.observation_count
                airport_presence_count += len(presences)
                flight_segment_count += len(segments)
                airport_visit_count += len(visits)
                aircraft_rows.append(
                    (
                        summary.address,
                        summary.address_kind,
                        summary.registration,
                        summary.callsign_last_seen,
                        summary.type_code,
                        summary.type_description,
                        summary.owner_operator,
                        summary.manufacture_year,
                        summary.db_flags,
                        summary.utc_date,
                        summary.utc_date,
                    )
                )
                aircraft_day_rows.append(
                    (
                        dataset_day_id,
                        summary.utc_date,
                        summary.address,
                        summary.registration,
                        summary.type_code,
                        summary.type_description,
                        summary.owner_operator,
                        summary.manufacture_year,
                        summary.db_flags,
                        summary.trace_format_version,
                        list(summary.callsigns),
                        list(summary.position_source_types),
                        summary.first_seen_at,
                        summary.last_seen_at,
                        summary.observation_count,
                        summary.position_count,
                        summary.ground_observation_count,
                        summary.min_altitude_ft,
                        summary.max_altitude_ft,
                        summary.max_ground_speed_knots,
                        summary.first_latitude,
                        summary.first_longitude,
                        summary.last_latitude,
                        summary.last_longitude,
                        summary.time_observed_seconds,
                        summary.active_time_seconds,
                        summary.airborne_time_seconds,
                        summary.ground_active_time_seconds,
                        summary.distinct_airports,
                        summary.airport_presence_count,
                        summary.estimated_distance_nm,
                        summary.source_trace_gzip_bytes,
                        summary.source_trace_json_bytes,
                    )
                )
                airport_day_rows.extend(
                    (
                        dataset_day_id,
                        item.utc_date,
                        item.airport_ident,
                        item.address,
                        item.is_primary_airport,
                        item.first_seen_at,
                        item.last_seen_at,
                        item.presence_count,
                        item.ground_observation_count,
                        item.ground_time_seconds,
                        item.ground_active_time_seconds,
                        item.closest_distance_nm,
                        item.inferred_endpoint_count,
                        item.arrival_count,
                        item.departure_count,
                        item.link_method,
                    )
                    for item in presences
                )
                flight_segment_rows.extend(
                    (
                        dataset_day_id,
                        item.utc_date,
                        item.address,
                        item.segment_sequence,
                        item.first_airborne_at,
                        item.last_airborne_at,
                        item.takeoff_at,
                        item.landing_at,
                        item.origin_airport_ident,
                        item.destination_airport_ident,
                        item.origin_evidence,
                        item.destination_evidence,
                        item.observation_count,
                        item.observed_airborne_seconds,
                        item.elapsed_airborne_seconds,
                        item.unobserved_seconds,
                        item.estimated_distance_nm,
                        item.max_altitude_ft,
                        item.max_ground_speed_knots,
                        list(item.callsigns),
                        item.starts_before_window,
                        item.ends_after_window,
                        item.confidence,
                        list(item.quality_flags),
                        Jsonb(item.track) if item.track is not None else None,
                    )
                    for item in segments
                )
                airport_visit_rows.extend(
                    (
                        dataset_day_id,
                        item.utc_date,
                        item.address,
                        item.visit_sequence,
                        item.airport_ident,
                        item.first_evidence_at,
                        item.last_evidence_at,
                        item.arrived_at,
                        item.departed_at,
                        item.ground_observation_count,
                        item.proximity_observation_count,
                        item.ground_time_seconds,
                        item.ground_active_time_seconds,
                        item.closest_distance_nm,
                        item.arrival_evidence,
                        item.departure_evidence,
                        item.open_at_start,
                        item.open_at_end,
                        item.confidence,
                        list(item.quality_flags),
                    )
                    for item in visits
                )
                if len(aircraft_rows) >= batch_size or buffered_track_points >= 50000:
                    flush()
                if aircraft_count % heartbeat_every == 0:
                    self.heartbeat(
                        job_id,
                        f"Parsed {aircraft_count:,} aircraft / {observation_count:,} observations",
                    )
            flush()

            # Reprocessing must invalidate the Phase 12 identity/activity cache.
            # The area cache cascades with aircraft_day; this cache references the
            # durable aircraft and dataset rows and would otherwise remain stale.
            connection.execute(
                "DELETE FROM nl_aircraft_activity_cache WHERE dataset_day_id = %s",
                (dataset_day_id,),
            )
            connection.execute(
                "DELETE FROM aircraft_day WHERE dataset_day_id = %s", (dataset_day_id,)
            )
            connection.execute(
                """
                INSERT INTO aircraft (
                    address, address_kind, registration, callsign_last_seen, type_code,
                    type_description, owner_operator, manufacture_year, db_flags,
                    first_seen_date, last_seen_date
                )
                SELECT
                    address, address_kind, registration, callsign_last_seen, type_code,
                    type_description, owner_operator, manufacture_year, db_flags,
                    first_seen_date, last_seen_date
                FROM aircraft_stage
                ON CONFLICT (address) DO UPDATE SET
                    address_kind = EXCLUDED.address_kind,
                    registration = COALESCE(EXCLUDED.registration, aircraft.registration),
                    callsign_last_seen = COALESCE(
                        EXCLUDED.callsign_last_seen, aircraft.callsign_last_seen
                    ),
                    type_code = COALESCE(EXCLUDED.type_code, aircraft.type_code),
                    type_description = COALESCE(
                        EXCLUDED.type_description, aircraft.type_description
                    ),
                    owner_operator = COALESCE(EXCLUDED.owner_operator, aircraft.owner_operator),
                    manufacture_year = COALESCE(
                        EXCLUDED.manufacture_year, aircraft.manufacture_year
                    ),
                    db_flags = COALESCE(EXCLUDED.db_flags, aircraft.db_flags),
                    first_seen_date = LEAST(aircraft.first_seen_date, EXCLUDED.first_seen_date),
                    last_seen_date = GREATEST(aircraft.last_seen_date, EXCLUDED.last_seen_date),
                    updated_at = clock_timestamp()
                """
            )
            connection.execute(
                f"""
                INSERT INTO aircraft_day ({', '.join(AIRCRAFT_DAY_STAGE_COLUMNS)})
                SELECT {', '.join(AIRCRAFT_DAY_STAGE_COLUMNS)} FROM aircraft_day_stage
                """
            )
            connection.execute(
                f"""
                INSERT INTO aircraft_airport_day ({', '.join(AIRPORT_DAY_STAGE_COLUMNS)})
                SELECT {', '.join(AIRPORT_DAY_STAGE_COLUMNS)}
                FROM aircraft_airport_day_stage
                """
            )
            connection.execute(
                f"""
                INSERT INTO aircraft_flight_segment
                    ({', '.join(FLIGHT_SEGMENT_STAGE_COLUMNS)})
                SELECT {', '.join(FLIGHT_SEGMENT_STAGE_COLUMNS)}
                FROM aircraft_flight_segment_stage
                """
            )
            connection.execute(
                f"""
                INSERT INTO aircraft_airport_visit
                    ({', '.join(AIRPORT_VISIT_STAGE_COLUMNS)})
                SELECT {', '.join(AIRPORT_VISIT_STAGE_COLUMNS)}
                FROM aircraft_airport_visit_stage
                """
            )
            after_bytes = self._relation_bytes(connection)
            duration_ms = round((time.monotonic() - started) * 1000)
            connection.execute(
                """
                UPDATE dataset_day
                SET status = 'PROCESSED', processing_finished_at = clock_timestamp(),
                    source_aircraft_count = %s, source_record_count = %s,
                    derived_record_count = %s, airport_presence_record_count = %s,
                    flight_segment_record_count = %s, airport_visit_record_count = %s,
                    derivation_version = %s, derivation_config = %s,
                    processing_duration_ms = %s,
                    derived_bytes_estimate = CASE
                        WHEN (
                            SELECT job_type FROM ingestion_job WHERE id = %s
                        ) = 'REPROCESS'::ingestion_job_type
                          AND derived_bytes_estimate IS NOT NULL
                            THEN derived_bytes_estimate
                        ELSE %s
                    END,
                    error_stage = NULL, error_message = NULL, updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    aircraft_count,
                    observation_count,
                    aircraft_count,
                    airport_presence_count,
                    flight_segment_count,
                    airport_visit_count,
                    derivation_version,
                    Jsonb(derivation_config or {}),
                    duration_ms,
                    job_id,
                    max(0, after_bytes - before_bytes),
                    dataset_day_id,
                ),
            )
            connection.execute(
                """
                UPDATE ingestion_job
                SET status = 'SUCCEEDED', progress_percent = 100,
                    status_message = %s, heartbeat_at = clock_timestamp(),
                    finished_at = clock_timestamp()
                WHERE id = %s
                """,
                (
                    f"Processed {aircraft_count:,} aircraft, "
                    f"{observation_count:,} observations, {flight_segment_count:,} "
                    f"flight segments and {airport_visit_count:,} airport visits",
                    job_id,
                ),
            )
            connection.commit()

        return {
            "aircraft_count": aircraft_count,
            "observation_count": observation_count,
            "airport_presence_count": airport_presence_count,
            "flight_segment_count": flight_segment_count,
            "airport_visit_count": airport_visit_count,
            "processing_duration_ms": duration_ms,
        }

    def mark_raw_deleted(self, dataset_day_id: int, raw_deleted_bytes: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE dataset_day
                SET raw_deleted_at = clock_timestamp(), raw_deleted_bytes = %s,
                    raw_delete_error = NULL, updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (raw_deleted_bytes, dataset_day_id),
            )
            connection.commit()

    def mark_raw_delete_error(self, dataset_day_id: int, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE dataset_day
                SET raw_delete_error = %s, updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (message[:10_000], dataset_day_id),
            )
            connection.commit()
