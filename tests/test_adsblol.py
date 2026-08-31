from __future__ import annotations

import io
import tarfile
import unittest

from adsb_ingest.adsblol import ArchiveError, parse_tar_header
from adsb_ingest.phase1 import _bootstrap_estimates, _percentile


class TarHeaderTests(unittest.TestCase):
    def make_header(self, name: str, content: bytes) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return buffer.getvalue()[:512]

    def test_parses_standard_tar_header(self) -> None:
        block = self.make_header("traces/7f/trace_full_a0107f.json", b"payload")
        entry = parse_tar_header(block, 1024)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.name, "traces/7f/trace_full_a0107f.json")
        self.assertEqual(entry.size, 7)
        self.assertEqual(entry.data_offset, 1536)
        self.assertEqual(entry.next_header_offset, 2048)

    def test_zero_block_ends_archive(self) -> None:
        self.assertIsNone(parse_tar_header(b"\0" * 512, 0))

    def test_rejects_invalid_checksum(self) -> None:
        block = bytearray(self.make_header("one.txt", b"payload"))
        block[0] ^= 1
        with self.assertRaises(ArchiveError):
            parse_tar_header(bytes(block), 0)


class EstimateTests(unittest.TestCase):
    def test_percentile_interpolates(self) -> None:
        self.assertEqual(_percentile([0.0, 10.0], 0.25), 2.5)

    def test_bootstrap_estimate_on_identical_rows(self) -> None:
        result = _bootstrap_estimates(
            footprints=[100] * 20,
            records=[10] * 20,
            uncompressed=[500] * 20,
            trace_region_bytes=10_000,
            repetitions=20,
        )
        self.assertEqual(result["aircraft_files"]["estimate"], 100)
        self.assertEqual(result["trace_records"]["estimate"], 1_000)
        self.assertEqual(result["uncompressed_json_bytes"]["estimate"], 50_000)


if __name__ == "__main__":
    unittest.main()
