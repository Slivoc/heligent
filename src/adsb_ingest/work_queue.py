from __future__ import annotations

import logging
import os
import socket
import threading
from argparse import Namespace
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .ingest import PROJECT_ROOT, run_ingestion
from .postgres import PostgresStore


LOGGER = logging.getLogger(__name__)
PHASE3_MIGRATION = PROJECT_ROOT / "schema" / "phase3.sql"
PHASE13_MIGRATION = PROJECT_ROOT / "schema" / "phase13.sql"
WORKER_ADVISORY_LOCK_ID = 1_145_398_973
MAX_QUEUE_RANGE_DAYS = 31


@dataclass(frozen=True)
class QueueItem:
    id: int
    utc_date: date
    requested_action: str
    keep_raw: bool
    raw_source: str
    attempts: int


class AdminStore(PostgresStore):
    def apply_phase3_migration(self, path: Path = PHASE3_MIGRATION) -> None:
        self.apply_schema_once(path)

    def apply_phase13_migration(self, path: Path = PHASE13_MIGRATION) -> None:
        self.apply_schema_once(path)

    def enqueue_date(
        self,
        utc_date: date,
        *,
        reprocess: bool = False,
        keep_raw: bool = False,
        raw_source: str = "DIRECT",
    ) -> dict[str, Any]:
        raw_source = raw_source.strip().upper()
        if raw_source not in {"DIRECT", "PI"}:
            raise ValueError("Raw source must be DIRECT or PI")
        with self.connect() as connection:
            connection.row_factory = dict_row
            dataset = connection.execute(
                """
                SELECT id, status
                FROM dataset_day
                WHERE utc_date = %s
                  AND source_code = 'ADSB_LOL'
                  AND source_selection = 'PREFERRED'
                FOR UPDATE
                """,
                (utc_date,),
            ).fetchone()
            if dataset and str(dataset["status"]) == "PROCESSED" and not reprocess:
                connection.rollback()
                return {
                    "outcome": "SKIPPED_ALREADY_PROCESSED",
                    "utc_date": utc_date,
                }

            if dataset:
                active_stage = connection.execute(
                    """
                    SELECT 1 FROM ingestion_job
                    WHERE dataset_day_id = %s AND status IN ('QUEUED', 'RUNNING')
                    LIMIT 1
                    """,
                    (dataset["id"],),
                ).fetchone()
                if active_stage:
                    connection.rollback()
                    return {"outcome": "ALREADY_RUNNING", "utc_date": utc_date}

            active_queue = connection.execute(
                """
                SELECT id, status, requested_action
                FROM ingestion_queue
                WHERE utc_date = %s AND status IN ('QUEUED', 'RUNNING')
                ORDER BY id DESC
                LIMIT 1
                """,
                (utc_date,),
            ).fetchone()
            if active_queue:
                connection.rollback()
                return {
                    "outcome": "ALREADY_QUEUED",
                    "utc_date": utc_date,
                    "queue_id": active_queue["id"],
                    "status": str(active_queue["status"]),
                }

            if dataset is None:
                dataset = connection.execute(
                    """
                    INSERT INTO dataset_day (
                        utc_date, source_code, source_selection, status, queued_at
                    )
                    VALUES (%s, 'ADSB_LOL', 'PREFERRED', 'QUEUED', clock_timestamp())
                    RETURNING id, status
                    """,
                    (utc_date,),
                ).fetchone()
            else:
                connection.execute(
                    """
                    UPDATE dataset_day
                    SET status = 'QUEUED', queued_at = clock_timestamp(),
                        error_stage = NULL, error_message = NULL,
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (dataset["id"],),
                )

            action = "REPROCESS" if reprocess else "INGEST"
            queued = connection.execute(
                """
                INSERT INTO ingestion_queue (
                    utc_date, requested_action, keep_raw, raw_source, status
                )
                VALUES (%s, %s, %s, %s, 'QUEUED')
                RETURNING id, utc_date, requested_action, keep_raw, raw_source, status,
                          requested_at
                """,
                (utc_date, action, keep_raw, raw_source),
            ).fetchone()
            connection.commit()
            assert queued is not None
            return {"outcome": "QUEUED", "item": queued}

    def enqueue_range(
        self,
        start_date: date,
        end_date: date,
        *,
        keep_raw: bool = False,
        raw_source: str = "DIRECT",
    ) -> dict[str, Any]:
        span = (end_date - start_date).days + 1
        if span <= 0:
            raise ValueError("The end date must be on or after the start date")
        if span > MAX_QUEUE_RANGE_DAYS:
            raise ValueError(
                f"A single range may contain at most {MAX_QUEUE_RANGE_DAYS} days"
            )
        results: list[dict[str, Any]] = []
        for offset in range(span):
            results.append(
                self.enqueue_date(
                    date.fromordinal(start_date.toordinal() + offset),
                    keep_raw=keep_raw,
                    raw_source=raw_source,
                )
            )
        return {
            "requested_days": span,
            "queued_days": sum(item["outcome"] == "QUEUED" for item in results),
            "skipped_days": sum(item["outcome"] != "QUEUED" for item in results),
            "results": results,
        }

    def retry_date(
        self,
        utc_date: date,
        *,
        keep_raw: bool = False,
        raw_source: str = "DIRECT",
    ) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    d.status,
                    EXISTS (
                        SELECT 1 FROM aircraft_day ad WHERE ad.dataset_day_id = d.id
                    ) AS has_derived_rows
                FROM dataset_day d
                WHERE d.utc_date = %s
                  AND d.source_code = 'ADSB_LOL'
                  AND d.source_selection = 'PREFERRED'
                """,
                (utc_date,),
            ).fetchone()
        if not row:
            return self.enqueue_date(
                utc_date, keep_raw=keep_raw, raw_source=raw_source
            )
        status = str(row[0])
        if status not in {"FAILED_DOWNLOAD", "FAILED_PROCESSING"}:
            raise ValueError(f"{utc_date} is not in a failed state")
        return self.enqueue_date(
            utc_date,
            reprocess=bool(row[1]),
            keep_raw=keep_raw,
            raw_source=raw_source,
        )

    def cancel_queue_item(self, queue_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            connection.row_factory = dict_row
            row = connection.execute(
                """
                UPDATE ingestion_queue
                SET status = 'CANCELLED', finished_at = clock_timestamp(),
                    error_message = 'Cancelled before processing'
                WHERE id = %s AND status = 'QUEUED'
                RETURNING id, utc_date, status
                """,
                (queue_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            connection.execute(
                """
                UPDATE dataset_day d
                SET status = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM aircraft_day ad WHERE ad.dataset_day_id = d.id
                        ) THEN 'PROCESSED'::dataset_status
                        ELSE 'NOT_DOWNLOADED'::dataset_status
                    END,
                    updated_at = clock_timestamp()
                WHERE d.utc_date = %s
                  AND d.source_code = 'ADSB_LOL'
                  AND d.source_selection = 'PREFERRED'
                """,
                (row["utc_date"],),
            )
            connection.commit()
            return row

    def claim_next(self, worker_id: str) -> QueueItem | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                WITH next_item AS (
                    SELECT id
                    FROM ingestion_queue
                    WHERE status = 'QUEUED'
                    ORDER BY requested_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ingestion_queue q
                SET status = 'RUNNING', attempts = attempts + 1,
                    worker_id = %s, started_at = clock_timestamp(),
                    heartbeat_at = clock_timestamp(), finished_at = NULL,
                    error_message = NULL
                FROM next_item
                WHERE q.id = next_item.id
                RETURNING q.id, q.utc_date, q.requested_action, q.keep_raw,
                          q.raw_source, q.attempts
                """,
                (worker_id,),
            ).fetchone()
            connection.commit()
        if not row:
            return None
        return QueueItem(
            id=int(row[0]),
            utc_date=row[1],
            requested_action=str(row[2]),
            keep_raw=bool(row[3]),
            raw_source=str(row[4]),
            attempts=int(row[5]),
        )

    def finish_queue_item(self, item: QueueItem, result: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE ingestion_queue
                SET status = 'SUCCEEDED', result = %s,
                    heartbeat_at = clock_timestamp(), finished_at = clock_timestamp()
                WHERE id = %s AND status = 'RUNNING'
                """,
                (Jsonb(result), item.id),
            )
            connection.commit()

    def fail_queue_item(self, item: QueueItem, message: str) -> None:
        clean_message = message[:10_000]
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE ingestion_queue
                SET status = 'FAILED', error_message = %s,
                    heartbeat_at = clock_timestamp(), finished_at = clock_timestamp()
                WHERE id = %s AND status = 'RUNNING'
                """,
                (clean_message, item.id),
            )
            connection.execute(
                """
                UPDATE dataset_day
                SET status = 'FAILED_DOWNLOAD', error_stage = 'DISCOVERY',
                    error_message = %s, updated_at = clock_timestamp()
                WHERE utc_date = %s
                  AND source_code = 'ADSB_LOL'
                  AND source_selection = 'PREFERRED'
                  AND status = 'QUEUED'
                """,
                (clean_message, item.utc_date),
            )
            connection.commit()

    def reconcile_running_queue(self) -> int:
        with self.connect() as connection:
            rows = connection.execute(
                """
                UPDATE ingestion_queue
                SET status = 'QUEUED', worker_id = NULL, started_at = NULL,
                    heartbeat_at = clock_timestamp(),
                    error_message = 'Web worker restarted; safely queued again'
                WHERE status = 'RUNNING'
                RETURNING utc_date
                """
            ).fetchall()
            if rows:
                connection.execute(
                    """
                    UPDATE dataset_day
                    SET status = 'QUEUED', updated_at = clock_timestamp()
                    WHERE utc_date = ANY(%s)
                      AND source_code = 'ADSB_LOL'
                      AND source_selection = 'PREFERRED'
                    """,
                    ([row[0] for row in rows],),
                )
            connection.commit()
            return len(rows)

    def month_overview(self, year: int, month: int) -> dict[str, Any]:
        first = date(year, month, 1)
        last = date(year, month, monthrange(year, month)[1])
        with self.connect() as connection:
            connection.row_factory = dict_row
            days = connection.execute(
                """
                SELECT
                    d.utc_date, d.status, d.source_release_tag, d.raw_bytes,
                    d.source_aircraft_count, d.source_record_count,
                    d.derived_record_count, d.airport_presence_record_count,
                    d.download_duration_ms, d.processing_duration_ms,
                    d.derived_bytes_estimate, d.raw_deleted_at,
                    d.raw_deleted_bytes, d.error_stage, d.error_message,
                    d.updated_at,
                    q.id AS queue_id, q.status AS queue_status,
                    q.requested_action, q.raw_source, q.requested_at,
                    j.job_type AS active_stage, j.status_message,
                    j.heartbeat_at
                FROM dataset_day d
                LEFT JOIN LATERAL (
                    SELECT iq.id, iq.status, iq.requested_action, iq.raw_source,
                           iq.requested_at
                    FROM ingestion_queue iq
                    WHERE iq.utc_date = d.utc_date
                      AND iq.status IN ('QUEUED', 'RUNNING')
                    ORDER BY iq.id DESC
                    LIMIT 1
                ) q ON true
                LEFT JOIN LATERAL (
                    SELECT ij.job_type, ij.status_message, ij.heartbeat_at
                    FROM ingestion_job ij
                    WHERE ij.dataset_day_id = d.id AND ij.status = 'RUNNING'
                    ORDER BY ij.id DESC
                    LIMIT 1
                ) j ON true
                WHERE d.utc_date BETWEEN %s AND %s
                  AND d.source_code = 'ADSB_LOL'
                  AND d.source_selection = 'PREFERRED'
                ORDER BY d.utc_date
                """,
                (first, last),
            ).fetchall()
            summary = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE EXISTS (
                        SELECT 1 FROM aircraft_day ad WHERE ad.dataset_day_id = dataset_day.id
                    )) AS processed_days,
                    count(*) FILTER (WHERE status IN ('QUEUED','DOWNLOADING','DOWNLOADED','PROCESSING')) AS active_days,
                    count(*) FILTER (WHERE status IN ('FAILED_DOWNLOAD','FAILED_PROCESSING')) AS failed_days,
                    max(utc_date) FILTER (WHERE EXISTS (
                        SELECT 1 FROM aircraft_day ad WHERE ad.dataset_day_id = dataset_day.id
                    )) AS latest_processed_date,
                    COALESCE(sum(derived_record_count) FILTER (WHERE EXISTS (
                        SELECT 1 FROM aircraft_day ad WHERE ad.dataset_day_id = dataset_day.id
                    )), 0)
                        AS aircraft_day_rows
                FROM dataset_day
                WHERE source_code = 'ADSB_LOL' AND source_selection = 'PREFERRED'
                """
            ).fetchone()
            current = connection.execute(
                """
                SELECT
                    q.id, q.utc_date, q.requested_action, q.raw_source, q.status,
                    q.requested_at, q.started_at, q.attempts,
                    d.status AS dataset_status,
                    j.job_type AS active_stage, j.status_message, j.heartbeat_at
                FROM ingestion_queue q
                LEFT JOIN dataset_day d
                  ON d.utc_date = q.utc_date
                 AND d.source_code = 'ADSB_LOL'
                 AND d.source_selection = 'PREFERRED'
                LEFT JOIN LATERAL (
                    SELECT ij.job_type, ij.status_message, ij.heartbeat_at
                    FROM ingestion_job ij
                    WHERE ij.dataset_day_id = d.id AND ij.status = 'RUNNING'
                    ORDER BY ij.id DESC LIMIT 1
                ) j ON true
                WHERE q.status IN ('RUNNING', 'QUEUED')
                ORDER BY (q.status = 'RUNNING') DESC, q.requested_at, q.id
                LIMIT 1
                """
            ).fetchone()
            recent_queue = connection.execute(
                """
                SELECT id, utc_date, requested_action, raw_source, status, attempts,
                       requested_at, started_at, finished_at, error_message
                FROM ingestion_queue
                ORDER BY id DESC
                LIMIT 12
                """
            ).fetchall()
            latest = connection.execute(
                """
                SELECT utc_date, status, raw_bytes, source_aircraft_count,
                       source_record_count, derived_record_count,
                       airport_presence_record_count, download_duration_ms,
                       processing_duration_ms, derived_bytes_estimate,
                       raw_deleted_at, raw_deleted_bytes
                FROM dataset_day
                WHERE status = 'PROCESSED'
                  AND source_code = 'ADSB_LOL'
                  AND source_selection = 'PREFERRED'
                ORDER BY processing_finished_at DESC NULLS LAST, utc_date DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "month": f"{year:04d}-{month:02d}",
            "first_date": first,
            "last_date": last,
            "days": days,
            "summary": summary,
            "current_queue_item": current,
            "recent_queue": recent_queue,
            "latest_ingestion": latest,
        }

    def date_detail(self, utc_date: date) -> dict[str, Any]:
        with self.connect() as connection:
            connection.row_factory = dict_row
            dataset = connection.execute(
                """
                SELECT * FROM dataset_day
                WHERE utc_date = %s
                  AND source_code = 'ADSB_LOL'
                  AND source_selection = 'PREFERRED'
                """,
                (utc_date,),
            ).fetchone()
            jobs: list[dict[str, Any]] = []
            if dataset:
                jobs = connection.execute(
                    """
                    SELECT id, job_type, status, attempt, progress_percent,
                           status_message, queued_at, started_at, heartbeat_at,
                           finished_at, error_message
                    FROM ingestion_job
                    WHERE dataset_day_id = %s
                    ORDER BY id DESC
                    LIMIT 12
                    """,
                    (dataset["id"],),
                ).fetchall()
            queue_history = connection.execute(
                """
                SELECT id, requested_action, keep_raw, raw_source, status, attempts,
                       worker_id, requested_at, started_at, heartbeat_at,
                       finished_at, error_message
                FROM ingestion_queue
                WHERE utc_date = %s
                ORDER BY id DESC
                LIMIT 12
                """,
                (utc_date,),
            ).fetchall()
        return {
            "utc_date": utc_date,
            "dataset": dataset,
            "jobs": jobs,
            "queue_history": queue_history,
        }


Runner = Callable[[Namespace], dict[str, Any]]
AfterSuccess = Callable[[], None]


class SequentialIngestionWorker:
    def __init__(
        self,
        store: AdminStore,
        *,
        runner: Runner = run_ingestion,
        after_success: AfterSuccess | None = None,
        poll_seconds: float = 2.0,
    ) -> None:
        self.store = store
        self.runner = runner
        self.after_success = after_success
        self.poll_seconds = poll_seconds
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="adsb-sequential-ingestion-worker",
            daemon=True,
        )
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _runner_args(self, item: QueueItem) -> Namespace:
        return Namespace(
            database_url=self.store.dsn,
            utc_date=item.utc_date,
            raw_dir=PROJECT_ROOT / "data" / "raw",
            cache_dir=PROJECT_ROOT / "data" / "reference",
            airport_csv=None,
            refresh_airports=False,
            keep_raw=item.keep_raw,
            raw_source=item.raw_source,
            archive_api_url=os.getenv("ADSB_ARCHIVE_API_URL"),
            archive_api_token=os.getenv("ADSB_ARCHIVE_API_TOKEN"),
            reprocess=item.requested_action == "REPROCESS",
            batch_size=1_000,
            heartbeat_every=5_000,
            max_continuous_gap_seconds=120.0,
            airport_ground_gap_seconds=300.0,
            active_speed_knots=5.0,
            calculate_distance=False,
        )

    def _run(self) -> None:
        with self.store.connect(autocommit=True) as lock_connection:
            acquired = bool(
                lock_connection.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    (WORKER_ADVISORY_LOCK_ID,),
                ).fetchone()[0]
            )
            if not acquired:
                LOGGER.info("Another sequential ingestion worker already owns the queue")
                return
            reconciled = self.store.reconcile_running_queue()
            if reconciled:
                LOGGER.warning("Re-queued %s interrupted queue item(s)", reconciled)
            while not self._stop.is_set():
                item = self.store.claim_next(self.worker_id)
                if item is None:
                    self._wake.wait(self.poll_seconds)
                    self._wake.clear()
                    continue
                LOGGER.info("Processing queued date %s (%s)", item.utc_date, item.requested_action)
                try:
                    result = self.runner(self._runner_args(item))
                    if self.after_success:
                        try:
                            self.after_success()
                        except Exception:
                            LOGGER.exception(
                                "Ingestion succeeded but analytics metadata refresh failed"
                            )
                    self.store.finish_queue_item(item, result)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    LOGGER.exception("Queued ingestion failed for %s", item.utc_date)
                    self.store.fail_queue_item(item, message)
