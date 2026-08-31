from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from adsb_ingest.adsblol import Release, ReleaseAsset
from adsb_ingest.archive_api import create_archive_api
from adsb_ingest.archive_api_client import ArchiveApiClient, ArchiveApiError
from adsb_ingest.raw_archive import ArchiveQueue, _write_manifest


class ArchiveApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.utc_date = date(2026, 8, 20)
        self.payload = b"verified archive bytes"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.asset = ReleaseAsset(
            name="v2026.08.20-planes-readsb-prod-0.tar",
            size=len(self.payload),
            download_url="https://example.invalid/source",
            digest=f"sha256:{self.digest}",
        )
        self.release = Release(
            tag="v2026.08.20-planes-readsb-prod-0",
            html_url="https://example.invalid/release",
            published_at="2026-08-21T02:00:00Z",
            assets=(self.asset,),
        )
        release_dir = self.root / "releases" / self.release.tag
        release_dir.mkdir(parents=True)
        (release_dir / self.asset.name).write_bytes(self.payload)
        manifest_path = release_dir / "manifest.json"
        _write_manifest(
            manifest_path,
            utc_date=self.utc_date,
            repository="adsblol/globe_history_2026",
            release=self.release,
        )
        queue = ArchiveQueue(self.root / "archive-queue.sqlite3")
        queue.enqueue(self.utc_date)
        self.assertIsNotNone(queue.claim_next())
        queue.set_release(self.utc_date, self.release)
        queue.complete(self.utc_date, manifest_path, len(self.payload))
        app = create_archive_api(self.root, token="secret-token")
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer secret-token"}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_requires_token_and_returns_completed_manifest(self) -> None:
        unauthorized = self.client.get(f"/v1/days/{self.utc_date}")
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.get(
            f"/v1/days/{self.utc_date}", headers=self.headers
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["release_tag"], self.release.tag)
        self.assertEqual(response.json["assets"][0]["sha256"], self.digest)
        self.assertEqual(
            response.json["assets"][0]["download_path"],
            f"/v1/releases/{self.release.tag}/assets/{self.asset.name}",
        )

    def test_asset_endpoint_honors_range_requests(self) -> None:
        response = self.client.get(
            f"/v1/releases/{self.release.tag}/assets/{self.asset.name}",
            headers={**self.headers, "Range": "bytes=2-7"},
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, self.payload[2:8])
        self.assertEqual(response.headers["X-Content-SHA256"], self.digest)
        response.close()

    def test_unarchived_day_returns_not_found(self) -> None:
        response = self.client.get("/v1/days/2026-08-19", headers=self.headers)
        self.assertEqual(response.status_code, 404)


class ArchiveApiClientTests(unittest.TestCase):
    def test_builds_verified_release_from_api_manifest(self) -> None:
        digest = "a" * 64
        payload = {
            "utc_date": "2026-08-20",
            "repository": "adsblol/globe_history_2026",
            "release_tag": "v2026.08.20-planes-readsb-prod-0",
            "release_url": "https://github.example/release",
            "published_at": "2026-08-21T02:00:00Z",
            "assets": [
                {
                    "name": "part.tar",
                    "size": 10,
                    "sha256": digest,
                    "download_path": (
                        "/v1/releases/v2026.08.20-planes-readsb-prod-0/"
                        "assets/part.tar"
                    ),
                }
            ],
        }

        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return payload

        class FakeSession:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

            def get(self, url: str, *, timeout: float):
                self.url = url
                self.timeout = timeout
                return FakeResponse()

            def close(self) -> None:
                return None

        with patch("adsb_ingest.archive_api_client.requests.Session", FakeSession):
            with ArchiveApiClient("https://pi.example.ts.net", "token") as client:
                discovery = client.discover_day(date(2026, 8, 20))

        asset = discovery.preferred.assets[0]
        self.assertEqual(asset.digest, f"sha256:{digest}")
        self.assertEqual(
            asset.download_url,
            "https://pi.example.ts.net/v1/releases/"
            "v2026.08.20-planes-readsb-prod-0/assets/part.tar",
        )

    def test_missing_day_has_clear_error(self) -> None:
        class MissingResponse:
            status_code = 404

        class FakeSession:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

            def get(self, url: str, *, timeout: float):
                return MissingResponse()

            def close(self) -> None:
                return None

        with patch("adsb_ingest.archive_api_client.requests.Session", FakeSession):
            client = ArchiveApiClient("https://pi.example.ts.net", "token")
            with self.assertRaisesRegex(ArchiveApiError, "does not have"):
                client.discover_day(date(2026, 8, 19))
            client.close()


if __name__ == "__main__":
    unittest.main()
