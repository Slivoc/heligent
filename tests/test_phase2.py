from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from adsb_ingest.airports import Airport, AirportIndex
from adsb_ingest.archive import ConcatenatedFile, TracePayload, iter_trace_payloads
from adsb_ingest.adsblol import ReleaseAsset
from adsb_ingest.downloader import AssetDownloader
from adsb_ingest.summarize import summarize_trace


def trace_row(
    seconds: float,
    latitude: float,
    longitude: float,
    altitude: int | str,
    speed: float,
    *,
    flight: str | None = None,
) -> list[object]:
    detail = {"flight": flight} if flight else None
    return [
        seconds,
        latitude,
        longitude,
        altitude,
        speed,
        90.0,
        0,
        0,
        detail,
        "adsb_icao",
        None,
        None,
        None,
        None,
    ]


class AirportIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.airport = Airport(
            ident="TEST",
            airport_type="small_airport",
            name="Test Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code="EGTT",
            iata_code=None,
            local_code=None,
        )
        self.index = AirportIndex([self.airport])

    def test_matches_ground_location_near_airport(self) -> None:
        result = self.index.match(51.001, -1.001)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.airport.ident, "TEST")
        self.assertLess(result.distance_nm, 0.1)

    def test_does_not_match_outside_type_radius(self) -> None:
        self.assertIsNone(self.index.match(51.1, -1.0))

    def test_embedded_heliport_does_not_steal_major_airport_ground_traffic(self) -> None:
        large = Airport(
            ident="LARGE",
            airport_type="large_airport",
            name="Large Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=True,
            gps_code="EGLG",
            iata_code="LRG",
            local_code=None,
        )
        embedded_heliport = Airport(
            ident="HEL1",
            airport_type="heliport",
            name="Embedded Helipad",
            latitude_deg=51.005,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code=None,
            iata_code=None,
            local_code=None,
        )
        result = AirportIndex([large, embedded_heliport]).match(51.005, -1.0)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.airport.ident, "LARGE")


class SummaryTests(unittest.TestCase):
    def test_derives_tail_hours_and_airport_presence_without_positions(self) -> None:
        airport = Airport(
            ident="TEST",
            airport_type="small_airport",
            name="Test Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code="EGTT",
            iata_code=None,
            local_code=None,
        )
        payload = {
            "icao": "abcdef",
            "r": "G-TEST",
            "t": "H145",
            "desc": "AIRBUS HELICOPTERS H145",
            "version": "readsb test",
            "timestamp": 1787184000.0,
            "trace": [
                trace_row(0, 51.0000, -1.0000, "ground", 0, flight="TEST1"),
                trace_row(30, 51.0001, -1.0001, "ground", 10, flight="TEST1"),
                trace_row(60, 51.0100, -1.0000, 500, 100, flight="TEST1"),
                trace_row(90, 51.0200, -1.0000, 1000, 120, flight="TEST2"),
                trace_row(400, 51.0001, -1.0001, "ground", 0),
                trace_row(430, 51.0000, -1.0000, "ground", 5),
            ],
        }
        trace = TracePayload("trace.json", "abcdef", 100, 500, payload)
        summary, presences = summarize_trace(
            trace, date(2026, 8, 20), AirportIndex([airport])
        )

        self.assertEqual(summary.address, "abcdef")
        self.assertEqual(summary.type_code, "H145")
        self.assertEqual(summary.callsigns, ("TEST1", "TEST2"))
        self.assertEqual(summary.callsign_last_seen, "TEST2")
        self.assertEqual(summary.observation_count, 6)
        self.assertEqual(summary.active_time_seconds, 120)
        self.assertEqual(summary.airborne_time_seconds, 60)
        self.assertEqual(summary.ground_active_time_seconds, 60)
        self.assertEqual(summary.distinct_airports, 1)
        self.assertEqual(summary.airport_presence_count, 1)
        self.assertIsNone(summary.estimated_distance_nm)
        self.assertEqual(len(presences), 1)
        self.assertEqual(presences[0].presence_count, 1)
        self.assertEqual(presences[0].ground_time_seconds, 60)
        self.assertTrue(presences[0].is_primary_airport)

    def test_infers_arrival_and_departure_from_low_slow_airport_endpoints(self) -> None:
        airport = Airport(
            ident="TEST",
            airport_type="small_airport",
            name="Test Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code="EGTT",
            iata_code=None,
            local_code=None,
        )
        payload = {
            "icao": "abcdef",
            "r": "G-TEST",
            "t": "H145",
            "timestamp": 1787184000.0,
            "trace": [
                trace_row(0, 51.001, -1.0, 250, 40),
                trace_row(300, 51.10, -1.0, 2_000, 120),
                trace_row(600, 51.001, -1.0, 300, 35),
            ],
        }
        trace = TracePayload("trace.json", "abcdef", 100, 500, payload)

        summary, presences = summarize_trace(
            trace, date(2026, 8, 20), AirportIndex([airport])
        )

        self.assertEqual(summary.distinct_airports, 1)
        self.assertEqual(len(presences), 1)
        self.assertEqual(presences[0].ground_observation_count, 0)
        self.assertEqual(presences[0].inferred_endpoint_count, 2)
        self.assertEqual(presences[0].departure_count, 1)
        self.assertEqual(presences[0].arrival_count, 1)
        self.assertEqual(presences[0].link_method, "INFERRED_ENDPOINT")

    def test_does_not_infer_high_or_fast_endpoint_as_airport_activity(self) -> None:
        airport = Airport(
            ident="TEST",
            airport_type="small_airport",
            name="Test Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code="EGTT",
            iata_code=None,
            local_code=None,
        )
        payload = {
            "icao": "abcdef",
            "timestamp": 1787184000.0,
            "trace": [
                trace_row(0, 51.001, -1.0, 3_000, 220),
                trace_row(300, 51.10, -1.0, 3_500, 220),
            ],
        }
        trace = TracePayload("trace.json", "abcdef", 100, 500, payload)

        summary, presences = summarize_trace(
            trace, date(2026, 8, 20), AirportIndex([airport])
        )

        self.assertEqual(summary.distinct_airports, 0)
        self.assertEqual(presences, ())

    def test_ground_endpoint_movement_uses_continuous_ground_airport_match(self) -> None:
        first_airport = Airport(
            ident="FIRST",
            airport_type="small_airport",
            name="First Airport",
            latitude_deg=51.0,
            longitude_deg=-1.0,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code=None,
            iata_code=None,
            local_code=None,
        )
        neighbour = Airport(
            ident="NEAR",
            airport_type="small_airport",
            name="Neighbour Airport",
            latitude_deg=51.0,
            longitude_deg=-0.99,
            elevation_ft=100,
            continent="EU",
            iso_country="GB",
            iso_region="GB-ENG",
            municipality="Test",
            scheduled_service=False,
            gps_code=None,
            iata_code=None,
            local_code=None,
        )
        payload = {
            "icao": "abcdef",
            "timestamp": 1787184000.0,
            "trace": [
                trace_row(0, 51.0, -1.0, "ground", 0),
                trace_row(300, 51.10, -1.0, 2_000, 120),
                trace_row(590, 51.0, -1.0, "ground", 5),
                trace_row(600, 51.0, -0.994, "ground", 0),
            ],
        }
        trace = TracePayload("trace.json", "abcdef", 100, 500, payload)

        _, presences = summarize_trace(
            trace,
            date(2026, 8, 20),
            AirportIndex([first_airport, neighbour]),
        )

        self.assertEqual([item.airport_ident for item in presences], ["FIRST"])
        self.assertEqual(presences[0].arrival_count, 1)
        self.assertEqual(presences[0].departure_count, 1)
        self.assertEqual(presences[0].link_method, "GROUND")


class SplitArchiveTests(unittest.TestCase):
    def test_repeated_reads_after_final_part_return_eof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "part.aa"
            second = root / "part.ab"
            first.write_bytes(b"abc")
            second.write_bytes(b"def")
            with ConcatenatedFile([first, second]) as combined:
                self.assertEqual(combined.read(6), b"abcdef")
                self.assertEqual(combined.read(1), b"")
                self.assertEqual(combined.read(1), b"")

    def test_reads_gzip_json_across_arbitrary_split_boundaries(self) -> None:
        payloads = [
            {"icao": "abcdef", "timestamp": 1.0, "trace": [[0] * 14]},
            {"icao": "123456", "timestamp": 1.0, "trace": [[0] * 14]},
        ]
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            for payload in payloads:
                encoded = gzip.compress(json.dumps(payload).encode("utf-8"))
                address = payload["icao"]
                info = tarfile.TarInfo(f"./traces/{address[-2:]}/trace_full_{address}.json")
                info.size = len(encoded)
                archive.addfile(info, io.BytesIO(encoded))
        combined = archive_bytes.getvalue()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundaries = (713, 4_101)
            paths = [root / "part.aa", root / "part.ab", root / "part.ac"]
            paths[0].write_bytes(combined[: boundaries[0]])
            paths[1].write_bytes(combined[boundaries[0] : boundaries[1]])
            paths[2].write_bytes(combined[boundaries[1] :])
            parsed = list(iter_trace_payloads(paths))

        self.assertEqual([item.payload["icao"] for item in parsed], ["abcdef", "123456"])
        self.assertTrue(all(item.json_bytes > 0 for item in parsed))

    def test_invalid_aircraft_gzip_can_be_quarantined_without_hiding_archive_damage(self) -> None:
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            invalid = b"not-a-gzip-stream"
            invalid_info = tarfile.TarInfo("./traces/ef/trace_full_abcdef.json")
            invalid_info.size = len(invalid)
            archive.addfile(invalid_info, io.BytesIO(invalid))

            valid = gzip.compress(json.dumps({"icao": "123456"}).encode("utf-8"))
            valid_info = tarfile.TarInfo("./traces/56/trace_full_123456.json")
            valid_info.size = len(valid)
            archive.addfile(valid_info, io.BytesIO(valid))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.tar"
            path.write_bytes(archive_bytes.getvalue())
            with self.assertRaisesRegex(ValueError, "trace_full_abcdef"):
                list(iter_trace_payloads([path]))

            quarantined: list[str] = []
            parsed = list(
                iter_trace_payloads(
                    [path],
                    on_invalid=lambda name, _error: quarantined.append(name),
                )
            )

        self.assertEqual(quarantined, ["./traces/ef/trace_full_abcdef.json"])
        self.assertEqual([item.payload["icao"] for item in parsed], ["123456"])


class DownloaderTests(unittest.TestCase):
    def test_resumes_part_file_and_verifies_digest(self) -> None:
        content = (b"range-download-fixture-" * 1_000) + b"end"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                start = 0
                range_header = self.headers.get("Range")
                if range_header:
                    start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
                    self.send_response(206)
                    self.send_header(
                        "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                    )
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(content) - start))
                self.end_headers()
                self.wfile.write(content[start:])

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "asset.tar.aa"
                part = target.with_name(target.name + ".part")
                part.write_bytes(content[:1_000])
                asset = ReleaseAsset(
                    name=target.name,
                    size=len(content),
                    download_url=f"http://127.0.0.1:{server.server_port}/asset",
                    digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
                )
                with AssetDownloader(chunk_bytes=257) as downloader:
                    downloaded, reused = downloader.download_asset(asset, target)
                self.assertEqual(target.read_bytes(), content)
                self.assertEqual(reused, 1_000)
                self.assertEqual(downloaded, len(content) - 1_000)
                self.assertFalse(part.exists())
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
