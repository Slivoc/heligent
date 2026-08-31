from __future__ import annotations

import gzip
import io
import re
import tarfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import msgspec


LOCAL_TRACE_RE = re.compile(
    r"(?:^|/)traces/[0-9a-f]{2}/trace_full_(?P<address>[^/]+)\.json$"
)
JSON_DECODER = msgspec.json.Decoder()
InvalidTraceCallback = Callable[[str, ValueError], None]


class ConcatenatedFile(io.RawIOBase):
    """Read ordered split archive parts as one non-seekable byte stream."""

    def __init__(self, paths: Sequence[Path]) -> None:
        super().__init__()
        if not paths:
            raise ValueError("At least one split archive path is required")
        self.paths = tuple(paths)
        self._index = 0
        self._handle = self.paths[0].open("rb")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed archive")
        if self._index >= len(self.paths):
            return 0
        view = memoryview(buffer)
        written = 0
        while written < len(view):
            count = self._handle.readinto(view[written:])
            if count:
                written += count
                continue
            self._handle.close()
            self._index += 1
            if self._index >= len(self.paths):
                break
            self._handle = self.paths[self._index].open("rb")
        return written

    def close(self) -> None:
        if not self.closed and not self._handle.closed:
            self._handle.close()
        super().close()


@dataclass(frozen=True)
class TracePayload:
    archive_name: str
    address_from_filename: str
    gzip_bytes: int
    json_bytes: int
    payload: dict[str, Any]


def iter_trace_payloads(
    paths: Sequence[Path],
    *,
    on_invalid: InvalidTraceCallback | None = None,
) -> Iterator[TracePayload]:
    with ConcatenatedFile(paths) as raw:
        with io.BufferedReader(raw, buffer_size=1024 * 1024) as buffered:
            with tarfile.open(fileobj=buffered, mode="r|") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    match = LOCAL_TRACE_RE.search(member.name.removeprefix("./"))
                    if not match:
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise RuntimeError(f"Could not read tar member {member.name}")
                    compressed = extracted.read()
                    try:
                        raw_json = gzip.decompress(compressed)
                        payload = JSON_DECODER.decode(raw_json)
                    except (
                        EOFError,
                        OSError,
                        UnicodeDecodeError,
                        msgspec.DecodeError,
                        zlib.error,
                    ) as exc:
                        invalid = ValueError(
                            f"Invalid aircraft trace {member.name}: {exc}"
                        )
                        if on_invalid is None:
                            raise invalid from exc
                        on_invalid(member.name, invalid)
                        continue
                    if not isinstance(payload, dict):
                        raise ValueError(f"Aircraft trace {member.name} is not a JSON object")
                    yield TracePayload(
                        archive_name=member.name,
                        address_from_filename=match.group("address"),
                        gzip_bytes=member.size,
                        json_bytes=len(raw_json),
                        payload=payload,
                    )
