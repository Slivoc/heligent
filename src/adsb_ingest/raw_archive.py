from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .adsblol import GitHubClient, Release
from .downloader import AssetDownloader, sha256_file


LOGGER = logging.getLogger("adsb_archive")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path(
    os.environ.get("ADSB_ARCHIVE_ROOT", PROJECT_ROOT / "data" / "raw-archive")
)
LATEST_PRIORITY = 100
BACKFILL_PRIORITY = 0
DEFAULT_MIN_FREE_GIB = 20.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _historical_date(value: str) -> date:
    result = date.fromisoformat(value)
    if result >= _utc_today():
        raise argparse.ArgumentTypeError("date must be a completed UTC day")
    return result


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or suffix == "TiB":
            return f"{size:.1f} {suffix}" if suffix != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


@dataclass(frozen=True)
class QueueItem:
    utc_date: date
    reason: str
    priority: int
    attempts: int


class ArchiveQueue:
    """Small durable queue kept beside the archive on the SSD."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS archive_queue (
                    utc_date TEXT PRIMARY KEY,
                    reason TEXT NOT NULL CHECK (reason IN ('LATEST', 'BACKFILL')),
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED', 'DOWNLOADING', 'COMPLETE', 'FAILED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    release_tag TEXT,
                    release_url TEXT,
                    published_at TEXT,
                    total_bytes INTEGER,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    file_count INTEGER,
                    manifest_path TEXT,
                    status_message TEXT,
                    error_message TEXT,
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    heartbeat_at TEXT,
                    completed_at TEXT,
                    verified_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS archive_queue_status_order
                    ON archive_queue(status, priority DESC, utc_date DESC);
                """
            )

    def enqueue(
        self,
        utc_date: date,
        *,
        reason: str = "BACKFILL",
        priority: int = BACKFILL_PRIORITY,
    ) -> str:
        now = _now()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO archive_queue (
                    utc_date, reason, priority, status, requested_at, updated_at
                ) VALUES (?, ?, ?, 'QUEUED', ?, ?)
                ON CONFLICT(utc_date) DO UPDATE SET
                    reason = CASE
                        WHEN excluded.priority >= archive_queue.priority
                        THEN excluded.reason ELSE archive_queue.reason END,
                    priority = max(archive_queue.priority, excluded.priority),
                    status = CASE
                        WHEN archive_queue.status = 'FAILED' THEN 'QUEUED'
                        ELSE archive_queue.status END,
                    error_message = CASE
                        WHEN archive_queue.status = 'FAILED' THEN NULL
                        ELSE archive_queue.error_message END,
                    requested_at = CASE
                        WHEN archive_queue.status = 'FAILED' THEN excluded.requested_at
                        ELSE archive_queue.requested_at END,
                    updated_at = excluded.updated_at
                """,
                (utc_date.isoformat(), reason, priority, now, now),
            )
            row = connection.execute(
                "SELECT status FROM archive_queue WHERE utc_date = ?",
                (utc_date.isoformat(),),
            ).fetchone()
        return str(row["status"])

    def enqueue_range(self, first: date, last: date) -> int:
        if last < first:
            raise ValueError("range end must be on or after range start")
        count = 0
        cursor = first
        while cursor <= last:
            self.enqueue(cursor)
            count += 1
            cursor += timedelta(days=1)
        return count

    def claim_next(self) -> QueueItem | None:
        now = _now()
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT utc_date, reason, priority, attempts
                FROM archive_queue
                WHERE status = 'QUEUED'
                ORDER BY priority DESC, utc_date DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE archive_queue
                SET status = 'DOWNLOADING', attempts = attempts + 1,
                    started_at = ?, heartbeat_at = ?, completed_at = NULL,
                    error_message = NULL, status_message = 'Discovering preferred release',
                    updated_at = ?
                WHERE utc_date = ?
                """,
                (now, now, now, row["utc_date"]),
            )
            connection.commit()
        return QueueItem(
            utc_date=date.fromisoformat(row["utc_date"]),
            reason=str(row["reason"]),
            priority=int(row["priority"]),
            attempts=int(row["attempts"]) + 1,
        )

    def set_release(self, utc_date: date, release: Release) -> None:
        now = _now()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE archive_queue
                SET release_tag = ?, release_url = ?, published_at = ?,
                    total_bytes = ?, file_count = ?, downloaded_bytes = 0,
                    status_message = ?, heartbeat_at = ?, updated_at = ?
                WHERE utc_date = ? AND status = 'DOWNLOADING'
                """,
                (
                    release.tag,
                    release.html_url,
                    release.published_at,
                    release.total_bytes,
                    len(release.assets),
                    f"Downloading {len(release.assets)} release asset(s)",
                    now,
                    now,
                    utc_date.isoformat(),
                ),
            )

    def progress(self, utc_date: date, downloaded_bytes: int, message: str) -> None:
        now = _now()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE archive_queue
                SET downloaded_bytes = ?, status_message = ?, heartbeat_at = ?,
                    updated_at = ?
                WHERE utc_date = ? AND status = 'DOWNLOADING'
                """,
                (downloaded_bytes, message, now, now, utc_date.isoformat()),
            )

    def complete(self, utc_date: date, manifest_path: Path, total_bytes: int) -> None:
        now = _now()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE archive_queue
                SET status = 'COMPLETE', downloaded_bytes = ?, manifest_path = ?,
                    status_message = 'Archived and verified', error_message = NULL,
                    heartbeat_at = ?, completed_at = ?, verified_at = ?, updated_at = ?
                WHERE utc_date = ? AND status = 'DOWNLOADING'
                """,
                (
                    total_bytes,
                    str(manifest_path),
                    now,
                    now,
                    now,
                    now,
                    utc_date.isoformat(),
                ),
            )

    def fail(self, utc_date: date, message: str) -> None:
        now = _now()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE archive_queue
                SET status = 'FAILED', error_message = ?, status_message = 'Failed',
                    heartbeat_at = ?, updated_at = ?
                WHERE utc_date = ? AND status = 'DOWNLOADING'
                """,
                (message[:10_000], now, now, utc_date.isoformat()),
            )

    def requeue_interrupted(self) -> int:
        now = _now()
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE archive_queue
                SET status = 'QUEUED', status_message = 'Resuming interrupted download',
                    error_message = 'Previous worker stopped; resumable files retained',
                    updated_at = ?
                WHERE status = 'DOWNLOADING'
                """,
                (now,),
            )
        return int(cursor.rowcount)

    def retry(self, utc_date: date | None = None) -> int:
        now = _now()
        parameters: tuple[Any, ...]
        where = "status = 'FAILED'"
        if utc_date is None:
            parameters = (now, now)
        else:
            where += " AND utc_date = ?"
            parameters = (now, now, utc_date.isoformat())
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                f"""
                UPDATE archive_queue
                SET status = 'QUEUED', requested_at = ?, updated_at = ?,
                    error_message = NULL, status_message = 'Queued for retry'
                WHERE {where}
                """,
                parameters,
            )
        return int(cursor.rowcount)

    def retry_failed_latest(self) -> int:
        """Keep a transient failure from leaving a gap in the daily archive."""
        now = _now()
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE archive_queue
                SET status = 'QUEUED', requested_at = ?, updated_at = ?,
                    error_message = NULL, status_message = 'Queued for daily retry'
                WHERE status = 'FAILED' AND reason = 'LATEST'
                """,
                (now, now),
            )
        return int(cursor.rowcount)

    def rows(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection, connection:
            result = connection.execute(
                """
                SELECT * FROM archive_queue
                ORDER BY
                    CASE status
                        WHEN 'DOWNLOADING' THEN 0 WHEN 'QUEUED' THEN 1
                        WHEN 'FAILED' THEN 2 ELSE 3 END,
                    priority DESC, utc_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in result]

    def completed_rows(self, utc_date: date | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM archive_queue WHERE status = 'COMPLETE'"
        parameters: tuple[Any, ...] = ()
        if utc_date is not None:
            query += " AND utc_date = ?"
            parameters = (utc_date.isoformat(),)
        query += " ORDER BY utc_date DESC"
        with closing(self.connect()) as connection, connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def next_verification_row(self) -> dict[str, Any] | None:
        """Return one completed day, rotating from never/least-recently verified."""
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT * FROM archive_queue
                WHERE status = 'COMPLETE'
                ORDER BY
                    CASE WHEN verified_at IS NULL THEN 0 ELSE 1 END,
                    verified_at ASC,
                    utc_date ASC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_verified(self, utc_date: date) -> None:
        now = _now()
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE archive_queue SET verified_at = ?, updated_at = ? WHERE utc_date = ?",
                (now, now, utc_date.isoformat()),
            )


class WorkerBusy(RuntimeError):
    pass


class InsufficientSpaceError(OSError):
    pass


class WorkerLock:
    """Cross-platform, non-blocking process lock for the single download worker."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def __enter__(self) -> "WorkerLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0, os.SEEK_END)
                if self.handle.tell() == 0:
                    self.handle.write(b"\0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            self.handle.close()
            self.handle = None
            raise WorkerBusy("another archive worker is already running") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _remaining_download_bytes(release: Release, releases_root: Path) -> int:
    release_dir = releases_root / release.tag
    remaining = 0
    for asset in release.assets:
        target = release_dir / asset.name
        part = target.with_name(target.name + ".part")
        if target.is_file() and target.stat().st_size == asset.size:
            continue
        partial_size = part.stat().st_size if part.is_file() else 0
        remaining += max(0, asset.size - min(partial_size, asset.size))
    return remaining


def _write_manifest(
    path: Path,
    *,
    utc_date: date,
    repository: str,
    release: Release,
) -> None:
    manifest_assets: list[dict[str, Any]] = []
    source_digests_verified = True
    for asset in release.assets:
        source_digest = asset.digest
        algorithm, separator, expected = (source_digest or "").partition(":")
        if separator == ":" and algorithm.lower() == "sha256":
            archived_sha256 = expected.lower()
        else:
            source_digests_verified = False
            archived_sha256 = sha256_file(path.parent / asset.name)
        manifest_assets.append(
            {
                "name": asset.name,
                "size": asset.size,
                "source_digest": source_digest,
                "sha256": archived_sha256,
            }
        )
    payload = {
        "schema_version": 1,
        "provider": "ADSB.lol",
        "utc_date": utc_date.isoformat(),
        "repository": repository,
        "release_tag": release.tag,
        "release_url": release.html_url,
        "published_at": release.published_at,
        "archived_at": _now(),
        "total_bytes": release.total_bytes,
        "verified": True,
        "source_digests_verified": source_digests_verified,
        "assets": manifest_assets,
    }
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class ArchiveWorker:
    def __init__(
        self,
        root: Path,
        queue: ArchiveQueue,
        *,
        min_free_gib: float = DEFAULT_MIN_FREE_GIB,
    ) -> None:
        if min_free_gib < 0:
            raise ValueError("minimum free space cannot be negative")
        self.root = root
        self.releases_root = root / "releases"
        self.queue = queue
        self.min_free_bytes = round(min_free_gib * 1024**3)

    def process(self, item: QueueItem) -> dict[str, Any]:
        LOGGER.info("Archiving ADSB.lol UTC day %s", item.utc_date)
        last_progress_at = 0.0
        try:
            with GitHubClient() as client:
                discovery = client.discover_day(item.utc_date)
            release = discovery.preferred
            self.queue.set_release(item.utc_date, release)
            self.releases_root.mkdir(parents=True, exist_ok=True)
            remaining = _remaining_download_bytes(release, self.releases_root)
            free = shutil.disk_usage(self.root).free
            required = remaining + self.min_free_bytes
            if free < required:
                raise InsufficientSpaceError(
                    "insufficient SSD space: "
                    f"{_format_bytes(free)} free, {_format_bytes(remaining)} still needed, "
                    f"and {_format_bytes(self.min_free_bytes)} reserved"
                )

            offsets: dict[str, int] = {}
            cursor = 0
            for asset in release.assets:
                offsets[asset.name] = cursor
                cursor += asset.size

            def progress(asset: Any, current: int, total: int) -> None:
                nonlocal last_progress_at
                now = time.monotonic()
                if current == total or now - last_progress_at >= 15:
                    overall = offsets[str(asset.name)] + current
                    message = f"{asset.name}: {_format_bytes(current)} / {_format_bytes(total)}"
                    LOGGER.info(message)
                    self.queue.progress(item.utc_date, overall, message)
                    last_progress_at = now

            with AssetDownloader() as downloader:
                result = downloader.download_release(
                    release,
                    self.releases_root,
                    progress=progress,
                )

            release_dir = self.releases_root / release.tag
            manifest_path = release_dir / "manifest.json"
            _write_manifest(
                manifest_path,
                utc_date=item.utc_date,
                repository=discovery.repository,
                release=release,
            )
            self.queue.complete(item.utc_date, manifest_path, release.total_bytes)
            LOGGER.info(
                "Archived %s (%s downloaded, %s reused)",
                item.utc_date,
                _format_bytes(result.bytes_downloaded),
                _format_bytes(result.bytes_reused),
            )
            return {
                "status": "COMPLETE",
                "utc_date": item.utc_date.isoformat(),
                "release_tag": release.tag,
                "total_bytes": release.total_bytes,
                "downloaded_bytes": result.bytes_downloaded,
                "reused_bytes": result.bytes_reused,
                "manifest": str(manifest_path),
            }
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.queue.fail(item.utc_date, message)
            LOGGER.exception("Archive download failed for %s", item.utc_date)
            raise


def queue_latest(queue: ArchiveQueue) -> tuple[date, str]:
    with GitHubClient() as client:
        latest = client.latest_preferred_date(before=_utc_today())
    queue.retry_failed_latest()
    status = queue.enqueue(latest, reason="LATEST", priority=LATEST_PRIORITY)
    return latest, status


def _run_queue(
    root: Path,
    queue: ArchiveQueue,
    *,
    max_items: int,
    min_free_gib: float,
) -> tuple[int, int]:
    processed = 0
    failed = 0
    worker = ArchiveWorker(root, queue, min_free_gib=min_free_gib)
    with WorkerLock(root / ".archive-worker.lock"):
        recovered = queue.requeue_interrupted()
        if recovered:
            LOGGER.warning("Re-queued %d interrupted download(s)", recovered)
        while max_items == 0 or processed + failed < max_items:
            item = queue.claim_next()
            if item is None:
                break
            try:
                worker.process(item)
                processed += 1
            except InsufficientSpaceError:
                failed += 1
                break
            except Exception:
                failed += 1
    return processed, failed


def _worker_loop(
    root: Path,
    queue: ArchiveQueue,
    *,
    poll_seconds: float,
    min_free_gib: float,
) -> None:
    worker = ArchiveWorker(root, queue, min_free_gib=min_free_gib)
    with WorkerLock(root / ".archive-worker.lock"):
        recovered = queue.requeue_interrupted()
        if recovered:
            LOGGER.warning("Re-queued %d interrupted download(s)", recovered)
        LOGGER.info("Archive worker ready; polling every %.1f seconds", poll_seconds)
        while True:
            item = queue.claim_next()
            if item is None:
                time.sleep(poll_seconds)
                continue
            try:
                worker.process(item)
            except InsufficientSpaceError:
                LOGGER.error(
                    "Archive worker stopped to avoid failing the rest of the queue; "
                    "free SSD space and restart the service"
                )
                return
            except Exception:
                # A failed item is removed from the runnable queue. Continue so
                # one unavailable historical date does not block later work.
                continue


def _verify_manifest(row: dict[str, Any]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    manifest_value = row.get("manifest_path")
    if not manifest_value:
        return False, ["manifest path is missing"]
    manifest_path = Path(str(manifest_value))
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"could not read manifest: {exc}"]
    if payload.get("utc_date") != row["utc_date"]:
        problems.append("manifest UTC date does not match queue record")
    for asset in payload.get("assets", []):
        name = str(asset.get("name", ""))
        if Path(name).name != name:
            problems.append(f"unsafe asset name in manifest: {name!r}")
            continue
        path = manifest_path.parent / name
        expected_size = int(asset.get("size", -1))
        if not path.is_file():
            problems.append(f"missing asset: {name}")
            continue
        if path.stat().st_size != expected_size:
            problems.append(f"wrong size: {name}")
            continue
        expected_sha256 = asset.get("sha256")
        if expected_sha256:
            if sha256_file(path) != str(expected_sha256).lower():
                problems.append(f"SHA-256 mismatch: {name}")
        else:
            # Backward-compatible check for early manifests.
            digest = asset.get("digest")
            if digest:
                algorithm, separator, expected = str(digest).partition(":")
                if separator != ":" or algorithm.lower() != "sha256":
                    problems.append(f"unsupported digest: {name}")
                elif sha256_file(path) != expected.lower():
                    problems.append(f"SHA-256 mismatch: {name}")
            else:
                problems.append(f"SHA-256 missing from manifest: {name}")
    return not problems, problems


def _print_status(queue: ArchiveQueue, *, limit: int, as_json: bool) -> None:
    rows = queue.rows(limit)
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("Archive queue is empty.")
        return
    print(f"{'UTC date':10}  {'status':11}  {'reason':8}  {'size':10}  release / message")
    for row in rows:
        detail = row.get("release_tag") or row.get("status_message") or "-"
        if row.get("error_message"):
            detail = row["error_message"]
        print(
            f"{row['utc_date']:10}  {row['status']:11}  {row['reason']:8}  "
            f"{_format_bytes(row.get('total_bytes')):10}  {detail}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Durable, Pi-friendly ADSB.lol raw archive downloader"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="SSD archive root (or set ADSB_ARCHIVE_ROOT)",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    queue = subparsers.add_parser("queue", help="Queue one or more previous UTC dates")
    queue.add_argument("dates", nargs="+", type=_historical_date, metavar="DATE")

    queue_range = subparsers.add_parser("queue-range", help="Queue an inclusive date range")
    queue_range.add_argument("from_date", type=_historical_date, metavar="FROM")
    queue_range.add_argument("to_date", type=_historical_date, metavar="TO")

    subparsers.add_parser(
        "queue-latest",
        help="Discover and priority-queue the newest available completed UTC day",
    )

    run = subparsers.add_parser("run", help="Process queued downloads and exit")
    run.add_argument(
        "--max-items", type=int, default=0, help="Maximum items; 0 drains the queue"
    )
    run.add_argument("--min-free-gib", type=float, default=DEFAULT_MIN_FREE_GIB)

    worker = subparsers.add_parser("worker", help="Continuously process the queue")
    worker.add_argument("--poll-seconds", type=float, default=30.0)
    worker.add_argument("--min-free-gib", type=float, default=DEFAULT_MIN_FREE_GIB)

    status = subparsers.add_parser("status", help="Show archive and queue status")
    status.add_argument("--limit", type=int, default=100)
    status.add_argument("--json", action="store_true", dest="as_json")

    retry = subparsers.add_parser("retry", help="Retry failed downloads")
    retry.add_argument("date", nargs="?", type=_historical_date)

    verify = subparsers.add_parser("verify", help="Re-hash completed archive assets")
    verify.add_argument("date", nargs="?", type=_historical_date)
    subparsers.add_parser(
        "verify-next",
        help="Re-hash the never or least-recently verified completed day",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    queue = ArchiveQueue(root / "archive-queue.sqlite3")
    try:
        if args.command == "queue":
            results = {
                item.isoformat(): queue.enqueue(item) for item in args.dates
            }
            print(json.dumps({"queued": results}, indent=2))
        elif args.command == "queue-range":
            count = queue.enqueue_range(args.from_date, args.to_date)
            print(
                json.dumps(
                    {
                        "status": "QUEUED",
                        "from": args.from_date.isoformat(),
                        "to": args.to_date.isoformat(),
                        "dates": count,
                    },
                    indent=2,
                )
            )
        elif args.command == "queue-latest":
            latest, status = queue_latest(queue)
            print(
                json.dumps(
                    {"utc_date": latest.isoformat(), "status": status, "priority": "LATEST"},
                    indent=2,
                )
            )
        elif args.command == "run":
            if args.max_items < 0:
                parser.error("--max-items cannot be negative")
            processed, failed = _run_queue(
                root,
                queue,
                max_items=args.max_items,
                min_free_gib=args.min_free_gib,
            )
            print(json.dumps({"completed": processed, "failed": failed}, indent=2))
            if failed:
                raise SystemExit(1)
        elif args.command == "worker":
            if args.poll_seconds <= 0:
                parser.error("--poll-seconds must be positive")
            _worker_loop(
                root,
                queue,
                poll_seconds=args.poll_seconds,
                min_free_gib=args.min_free_gib,
            )
        elif args.command == "status":
            if args.limit <= 0:
                parser.error("--limit must be positive")
            _print_status(queue, limit=args.limit, as_json=args.as_json)
        elif args.command == "retry":
            count = queue.retry(args.date)
            print(json.dumps({"status": "QUEUED", "dates": count}, indent=2))
        elif args.command == "verify":
            rows = queue.completed_rows(args.date)
            if args.date is not None and not rows:
                raise SystemExit(f"No completed archive found for {args.date}")
            failed = 0
            for row in rows:
                ok, problems = _verify_manifest(row)
                print(
                    json.dumps(
                        {
                            "utc_date": row["utc_date"],
                            "status": "VERIFIED" if ok else "FAILED",
                            "problems": problems,
                        }
                    )
                )
                if ok:
                    queue.mark_verified(date.fromisoformat(row["utc_date"]))
                else:
                    failed += 1
            if failed:
                raise SystemExit(1)
        elif args.command == "verify-next":
            row = queue.next_verification_row()
            if row is None:
                print(json.dumps({"status": "EMPTY", "message": "No completed archives"}))
            else:
                ok, problems = _verify_manifest(row)
                print(
                    json.dumps(
                        {
                            "utc_date": row["utc_date"],
                            "status": "VERIFIED" if ok else "FAILED",
                            "problems": problems,
                        }
                    )
                )
                if not ok:
                    raise SystemExit(1)
                queue.mark_verified(date.fromisoformat(row["utc_date"]))
        else:
            parser.error(f"Unknown command {args.command}")
    except KeyboardInterrupt:
        LOGGER.error("Interrupted; partial files will resume on the next worker run")
        raise SystemExit(130)
    except WorkerBusy as exc:
        LOGGER.info("%s", exc)
        print(json.dumps({"status": "WORKER_BUSY", "message": str(exc)}))
    except Exception as exc:
        LOGGER.exception("Command failed")
        raise SystemExit(f"{type(exc).__name__}: {exc}") from exc


if __name__ == "__main__":
    main()
