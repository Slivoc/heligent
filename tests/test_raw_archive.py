from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from adsb_ingest.adsblol import DayReleases, GitHubClient, Release, ReleaseAsset
from adsb_ingest.downloader import DownloadResult
from adsb_ingest.raw_archive import ArchiveQueue, ArchiveWorker, _verify_manifest


class ArchiveQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.queue = ArchiveQueue(self.root / "queue.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_latest_priority_preempts_backfill(self) -> None:
        self.queue.enqueue(date(2026, 8, 20))
        self.queue.enqueue(date(2026, 8, 21), reason="LATEST", priority=100)

        claimed = self.queue.claim_next()

        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.utc_date, date(2026, 8, 21))
        self.assertEqual(claimed.reason, "LATEST")

    def test_interrupted_download_is_requeued(self) -> None:
        self.queue.enqueue(date(2026, 8, 20))
        self.assertIsNotNone(self.queue.claim_next())

        self.assertEqual(self.queue.requeue_interrupted(), 1)

        claimed = self.queue.claim_next()
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.attempts, 2)

    def test_completed_date_is_not_queued_again(self) -> None:
        utc_date = date(2026, 8, 20)
        self.queue.enqueue(utc_date)
        self.assertIsNotNone(self.queue.claim_next())
        self.queue.complete(utc_date, self.root / "manifest.json", 10)

        status = self.queue.enqueue(utc_date, reason="LATEST", priority=100)

        self.assertEqual(status, "COMPLETE")
        self.assertIsNone(self.queue.claim_next())

    def test_daily_check_retries_failed_latest_but_not_failed_backfill(self) -> None:
        latest = date(2026, 8, 21)
        backfill = date(2026, 8, 20)
        self.queue.enqueue(latest, reason="LATEST", priority=100)
        self.queue.enqueue(backfill)
        latest_item = self.queue.claim_next()
        assert latest_item is not None
        self.queue.fail(latest_item.utc_date, "temporary failure")
        backfill_item = self.queue.claim_next()
        assert backfill_item is not None
        self.queue.fail(backfill_item.utc_date, "bad historical date")

        self.assertEqual(self.queue.retry_failed_latest(), 1)

        retried = self.queue.claim_next()
        self.assertIsNotNone(retried)
        assert retried is not None
        self.assertEqual(retried.utc_date, latest)
        self.assertIsNone(self.queue.claim_next())

    def test_verification_rotates_from_unverified_to_least_recently_verified(self) -> None:
        first = date(2026, 8, 20)
        second = date(2026, 8, 21)
        for utc_date in (first, second):
            self.queue.enqueue(utc_date)
            self.assertIsNotNone(self.queue.claim_next())
            self.queue.complete(utc_date, self.root / f"{utc_date}.json", 10)

        row = self.queue.next_verification_row()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["utc_date"], first.isoformat())

        self.queue.mark_verified(first)
        row = self.queue.next_verification_row()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["utc_date"], second.isoformat())


class ArchiveWorkerTests(unittest.TestCase):
    def test_download_writes_verifiable_manifest_and_completes_queue(self) -> None:
        payload = b"raw archive fixture"
        digest = hashlib.sha256(payload).hexdigest()
        asset = ReleaseAsset(
            name="v2026.08.20-planes-readsb-prod-0.tar",
            size=len(payload),
            download_url="https://example.invalid/archive.tar",
            digest=None,
        )
        release = Release(
            tag="v2026.08.20-planes-readsb-prod-0",
            html_url="https://example.invalid/release",
            published_at="2026-08-21T02:00:00Z",
            assets=(asset,),
        )
        discovery = DayReleases(
            utc_date=date(2026, 8, 20),
            repository="adsblol/globe_history_2026",
            preferred=release,
            variants=(release,),
        )

        class FakeGitHubClient:
            def __enter__(self) -> "FakeGitHubClient":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def discover_day(self, utc_date: date) -> DayReleases:
                self.utc_date = utc_date
                return discovery

        class FakeDownloader:
            def __enter__(self) -> "FakeDownloader":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def download_release(self, selected: Release, raw_root: Path, *, progress: object) -> DownloadResult:
                release_dir = raw_root / selected.tag
                release_dir.mkdir(parents=True)
                path = release_dir / asset.name
                path.write_bytes(payload)
                progress(asset, asset.size, asset.size)  # type: ignore[operator]
                return DownloadResult((path,), len(payload), 0)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue = ArchiveQueue(root / "queue.sqlite3")
            queue.enqueue(discovery.utc_date)
            item = queue.claim_next()
            self.assertIsNotNone(item)
            assert item is not None

            with (
                patch("adsb_ingest.raw_archive.GitHubClient", FakeGitHubClient),
                patch("adsb_ingest.raw_archive.AssetDownloader", FakeDownloader),
            ):
                result = ArchiveWorker(root, queue, min_free_gib=0).process(item)

            self.assertEqual(result["status"], "COMPLETE")
            row = queue.completed_rows(discovery.utc_date)[0]
            valid, problems = _verify_manifest(row)
            self.assertTrue(valid, problems)
            manifest = json.loads(Path(row["manifest_path"]).read_text())
            self.assertFalse(manifest["source_digests_verified"])
            self.assertEqual(manifest["assets"][0]["sha256"], digest)


class PreferredDateTests(unittest.TestCase):
    def test_extracts_unique_preferred_dates(self) -> None:
        client = GitHubClient()
        preferred = "\n".join(
            [
                "https://github.com/adsblol/globe_history_2026/releases/download/"
                "v2026.08.20-planes-readsb-prod-0/archive.tar.aa",
                "https://github.com/adsblol/globe_history_2026/releases/download/"
                "v2026.08.20-planes-readsb-prod-0/archive.tar.ab",
                "https://github.com/adsblol/globe_history_2026/releases/download/"
                "v2026.08.21-planes-readsb-prod-0/archive.tar",
            ]
        )
        try:
            with patch.object(client, "_text", return_value=preferred):
                result = client.preferred_dates(2026)
        finally:
            client.close()

        self.assertEqual(result, (date(2026, 8, 20), date(2026, 8, 21)))


if __name__ == "__main__":
    unittest.main()
