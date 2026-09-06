from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Iterator

from .adsblol import GitHubClient
from .archive_api_client import ArchiveApiClient
from .airports import (
    AirportIndex,
    ensure_airports_csv,
    load_airport_catalog,
)
from .archive import iter_trace_payloads
from .downloader import AssetDownloader
from .postgres import PostgresStore
from .summarize import ActivityConfig, DERIVATION_VERSION, TraceSummary, summarize_trace


LOGGER = logging.getLogger("adsb_ingest")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = PROJECT_ROOT / "schema" / "phase2.sql"
MAX_INVALID_AIRCRAFT_TRACES = 10
RAW_SOURCES = frozenset({"DIRECT", "PI"})


def _database_url(value: str | None) -> str:
    result = value or os.getenv("DATABASE_URL")
    if not result:
        raise SystemExit("A PostgreSQL URL is required via --database-url or DATABASE_URL")
    return result


def _delete_verified_raw_files(raw_root: Path, paths: tuple[Path, ...]) -> int:
    root = raw_root.resolve()
    deleted_bytes = 0
    parents: set[Path] = set()
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"Refusing to delete symlinked raw path: {path}")
        resolved = path.resolve(strict=True)
        if root != resolved.parent and root not in resolved.parents:
            raise ValueError(f"Refusing to delete raw file outside {root}: {resolved}")
        if not resolved.is_file():
            raise ValueError(f"Refusing to delete unexpected raw path: {resolved}")
        deleted_bytes += resolved.stat().st_size
        parents.add(resolved.parent)
        resolved.unlink()
    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        if parent != root and not any(parent.iterdir()):
            parent.rmdir()
    return deleted_bytes


def run_ingestion(args: argparse.Namespace) -> dict[str, object]:
    if args.batch_size <= 0 or args.heartbeat_every <= 0:
        raise ValueError("Batch size and heartbeat interval must be positive")
    store = PostgresStore(_database_url(args.database_url))
    existing = store.get_dataset(args.utc_date)
    if existing and str(existing["status"]) == "PROCESSED" and not args.reprocess:
        LOGGER.info("%s is already processed; use --reprocess to replace it", args.utc_date)
        return {"status": "SKIPPED_ALREADY_PROCESSED", "dataset": existing}

    raw_source = str(getattr(args, "raw_source", "DIRECT")).upper()
    if raw_source not in RAW_SOURCES:
        raise ValueError(f"Unsupported raw download source: {raw_source}")
    download_headers: dict[str, str] | None = None
    if raw_source == "PI":
        archive_api_url = getattr(args, "archive_api_url", None) or os.getenv(
            "ADSB_ARCHIVE_API_URL"
        )
        archive_api_token = getattr(args, "archive_api_token", None) or os.getenv(
            "ADSB_ARCHIVE_API_TOKEN"
        )
        if not archive_api_url or not archive_api_token:
            raise ValueError(
                "Raspberry Pi source requires ADSB_ARCHIVE_API_URL and "
                "ADSB_ARCHIVE_API_TOKEN"
            )
        with ArchiveApiClient(archive_api_url, archive_api_token) as archive_client:
            discovery = archive_client.discover_day(args.utc_date)
            download_headers = archive_client.download_headers
    else:
        with GitHubClient() as github_client:
            discovery = github_client.discover_day(args.utc_date)
    dataset_day_id = store.upsert_dataset(discovery)
    store.reconcile_interrupted(dataset_day_id)

    airport_csv = (
        args.airport_csv
        if args.airport_csv
        else ensure_airports_csv(args.cache_dir, refresh=args.refresh_airports)
    )
    catalog = load_airport_catalog(airport_csv)
    airport_count = store.upsert_airports(catalog)
    airport_index = AirportIndex(catalog.airports)
    LOGGER.info("Loaded %s airports from %s", f"{airport_count:,}", airport_csv)

    raw_root = args.raw_dir.resolve()
    download_job_id = store.start_job(dataset_day_id, "DOWNLOAD")
    store.set_downloading(dataset_day_id)
    last_progress_at = 0.0

    def progress(asset: object, current: int, total: int) -> None:
        nonlocal last_progress_at
        now = time.monotonic()
        if current == total or now - last_progress_at >= 15:
            name = getattr(asset, "name", "asset")
            message = f"{name}: {current:,} / {total:,} bytes"
            LOGGER.info(message)
            store.heartbeat(download_job_id, message)
            last_progress_at = now

    download_started = time.monotonic()
    try:
        with AssetDownloader(headers=download_headers) as downloader:
            download = downloader.download_release(
                discovery.preferred,
                raw_root,
                progress=progress,
            )
        store.finish_download(
            dataset_day_id,
            download_job_id,
            raw_bytes=discovery.preferred.total_bytes,
            raw_file_count=len(download.paths),
            downloaded_bytes_this_run=download.bytes_downloaded,
            duration_ms=round((time.monotonic() - download_started) * 1000),
        )
    except Exception as exc:
        store.fail_job(
            dataset_day_id,
            download_job_id,
            stage="DOWNLOAD",
            message=f"{type(exc).__name__}: {exc}",
        )
        raise

    process_job_id = store.start_job(
        dataset_day_id, "REPROCESS" if args.reprocess else "PROCESS"
    )
    store.set_processing(dataset_day_id)
    config = ActivityConfig(
        max_continuous_gap_seconds=args.max_continuous_gap_seconds,
        airport_ground_gap_seconds=args.airport_ground_gap_seconds,
        active_speed_knots=args.active_speed_knots,
        airport_contact_max_agl_ft=args.airport_contact_max_agl_ft,
        airport_contact_max_speed_knots=args.airport_contact_max_speed_knots,
        flight_discontinuity_gap_seconds=args.flight_discontinuity_gap_seconds,
        flight_endpoint_link_seconds=args.flight_endpoint_link_seconds,
        flight_boundary_interpolation_seconds=args.flight_boundary_interpolation_seconds,
        calculate_distance=args.calculate_distance,
    )
    invalid_trace_names: list[str] = []
    watched_tails: set[str] = set()

    def quarantine_invalid_trace(member_name: str, error: ValueError) -> None:
        invalid_trace_names.append(member_name)
        LOGGER.warning("Quarantining malformed source trace: %s", error)
        if len(invalid_trace_names) > MAX_INVALID_AIRCRAFT_TRACES:
            raise RuntimeError(
                "Source archive contains more than "
                f"{MAX_INVALID_AIRCRAFT_TRACES} malformed aircraft traces; refusing "
                "to ingest a potentially damaged day"
            ) from error

    def summaries() -> Iterator[TraceSummary]:
        for trace in iter_trace_payloads(
            download.paths,
            on_invalid=quarantine_invalid_trace,
        ):
            yield summarize_trace(
                trace,
                args.utc_date,
                airport_index,
                config=config,
                retain_track=str(trace.payload.get('r') or '').strip().upper().replace('-', '').replace(' ', '') in watched_tails,
            )

    try:
        # Snapshot once per day: no per-aircraft queries or changes mid-file.
        with store.connect() as connection:
            watched_tails.update(row[0] for row in connection.execute('''
                SELECT w.registration FROM maintenance_watch w
                JOIN maintenance_watchlist l ON l.id=w.watchlist_id
                WHERE w.active AND l.scope_key='internal'
            '''))
        LOGGER.info('Retaining sampled paths for %d active watched registrations', len(watched_tails))
        metrics = store.load_summaries(
            dataset_day_id,
            process_job_id,
            summaries(),
            batch_size=args.batch_size,
            heartbeat_every=args.heartbeat_every,
            derivation_version=DERIVATION_VERSION,
            derivation_config={**asdict(config), 'track_scope': 'ACTIVE_WATCHLIST',
                               'track_registrations': sorted(watched_tails)},
        )
    except Exception as exc:
        store.fail_job(
            dataset_day_id,
            process_job_id,
            stage="PROCESSING",
            message=f"{type(exc).__name__}: {exc}",
        )
        raise

    analytics_refresh_error: str | None = None
    try:
        from .analytics import AnalyticsStore

        AnalyticsStore(store).refresh_after_ingestion()
    except Exception as exc:
        analytics_refresh_error = f"{type(exc).__name__}: {exc}"
        LOGGER.exception(
            "Derived rows committed but semantic cache refresh failed; "
            "heligent-migrate or the next successful ingestion can retry it"
        )

    raw_deleted_bytes = 0
    if not args.keep_raw:
        try:
            raw_deleted_bytes = _delete_verified_raw_files(raw_root, download.paths)
            store.mark_raw_deleted(dataset_day_id, raw_deleted_bytes)
            LOGGER.info("Deleted %s verified raw bytes", f"{raw_deleted_bytes:,}")
        except Exception as exc:
            store.mark_raw_delete_error(dataset_day_id, f"{type(exc).__name__}: {exc}")
            LOGGER.exception("Processing succeeded but raw deletion failed")

    return {
        "status": "PROCESSED",
        "utc_date": args.utc_date.isoformat(),
        "dataset_day_id": dataset_day_id,
        "release_tag": discovery.preferred.tag,
        "raw_source": raw_source,
        "airport_reference_rows": airport_count,
        "downloaded_bytes_this_run": download.bytes_downloaded,
        "reused_bytes": download.bytes_reused,
        "raw_deleted_bytes": raw_deleted_bytes,
        "invalid_aircraft_trace_count": len(invalid_trace_names),
        "analytics_refresh_error": analytics_refresh_error,
        **metrics,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-day ADSB.lol ingestion into airport/tail activity summaries"
    )
    parser.add_argument("--database-url", help="PostgreSQL connection URL")
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init-db", help="Create the Phase 2 PostgreSQL schema")
    init.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)

    ingest = subparsers.add_parser("ingest-day", help="Download and ingest one UTC day")
    ingest.add_argument("--date", required=True, type=date.fromisoformat, dest="utc_date")
    ingest.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data" / "raw")
    ingest.add_argument(
        "--cache-dir", type=Path, default=PROJECT_ROOT / "data" / "reference"
    )
    ingest.add_argument("--airport-csv", type=Path)
    ingest.add_argument("--refresh-airports", action="store_true")
    ingest.add_argument("--keep-raw", action="store_true")
    ingest.add_argument("--reprocess", action="store_true")
    ingest.add_argument(
        "--raw-source",
        choices=("direct", "pi"),
        default="direct",
        help="Fetch from ADSB.lol directly or from the configured Pi archive API",
    )
    ingest.add_argument(
        "--archive-api-url",
        help="Pi archive API base URL (or ADSB_ARCHIVE_API_URL)",
    )
    ingest.add_argument(
        "--archive-api-token",
        help="Pi archive API bearer token (or ADSB_ARCHIVE_API_TOKEN)",
    )
    ingest.add_argument("--batch-size", type=int, default=1_000)
    ingest.add_argument("--heartbeat-every", type=int, default=5_000)
    ingest.add_argument("--max-continuous-gap-seconds", type=float, default=120.0)
    ingest.add_argument("--airport-ground-gap-seconds", type=float, default=300.0)
    ingest.add_argument("--active-speed-knots", type=float, default=5.0)
    ingest.add_argument("--airport-contact-max-agl-ft", type=float, default=500.0)
    ingest.add_argument("--airport-contact-max-speed-knots", type=float, default=100.0)
    ingest.add_argument("--flight-discontinuity-gap-seconds", type=float, default=1_800.0)
    ingest.add_argument("--flight-endpoint-link-seconds", type=float, default=1_800.0)
    ingest.add_argument("--flight-boundary-interpolation-seconds", type=float, default=900.0)
    ingest.add_argument(
        "--calculate-distance",
        action="store_true",
        help="Calculate point-to-point distance (off by default; journey-oriented and CPU-heavy)",
    )

    status = subparsers.add_parser("status", help="Show ingestion status for one date")
    status.add_argument("--date", required=True, type=date.fromisoformat, dest="utc_date")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        store = PostgresStore(_database_url(args.database_url))
        if args.command == "init-db":
            store.apply_schema(args.schema)
            applied_migrations: list[str] = []
            if args.schema.resolve() == DEFAULT_SCHEMA.resolve():
                from .migrate import migrate

                applied_migrations = migrate(store.dsn)
            print(
                json.dumps(
                    {
                        "status": "SCHEMA_CREATED",
                        "schema": str(args.schema),
                        "applied_migrations": applied_migrations,
                    }
                )
            )
        elif args.command == "status":
            print(json.dumps(store.get_dataset(args.utc_date), indent=2, default=str))
        elif args.command == "ingest-day":
            print(json.dumps(run_ingestion(args), indent=2, default=str))
        else:
            parser.error(f"Unknown command {args.command}")
    except KeyboardInterrupt:
        LOGGER.error("Interrupted; the next run will reconcile the unfinished job")
        raise SystemExit(130)
    except Exception as exc:
        LOGGER.exception("Command failed")
        raise SystemExit(f"{type(exc).__name__}: {exc}") from exc


if __name__ == "__main__":
    main()
