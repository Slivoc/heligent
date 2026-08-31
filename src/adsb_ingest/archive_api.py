from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_file
from waitress import serve

from .raw_archive import ArchiveQueue, DEFAULT_ROOT


LOGGER = logging.getLogger("adsb_archive_api")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _safe_manifest(root: Path, row: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    value = row.get("manifest_path")
    if not value:
        raise FileNotFoundError("Archive manifest is missing")
    manifest_path = Path(str(value)).resolve(strict=True)
    releases_root = (root / "releases").resolve(strict=True)
    if releases_root not in manifest_path.parents:
        raise ValueError("Archive manifest lies outside the releases directory")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("utc_date") != row["utc_date"]:
        raise ValueError("Archive manifest does not match its queue record")
    if not payload.get("verified"):
        raise ValueError("Archive manifest is not marked verified")
    return manifest_path, payload


def create_archive_api(
    root: Path,
    *,
    token: str | None,
    allow_unauthenticated: bool = False,
) -> Flask:
    archive_root = root.resolve()
    queue = ArchiveQueue(archive_root / "archive-queue.sqlite3")
    expected_token = (token or "").strip()
    if not expected_token and not allow_unauthenticated:
        raise RuntimeError(
            "ADSB_ARCHIVE_API_TOKEN is required unless --allow-unauthenticated is set"
        )

    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=16 * 1024, JSON_SORT_KEYS=False)

    @app.before_request
    def require_bearer_token() -> Response | None:
        if request.endpoint == "health" or allow_unauthenticated:
            return None
        supplied = request.headers.get("Authorization", "")
        prefix = "Bearer "
        candidate = supplied[len(prefix) :] if supplied.startswith(prefix) else ""
        if not hmac.compare_digest(candidate, expected_token):
            response = jsonify({"error": "Valid archive API bearer token required"})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        return None

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.errorhandler(404)
    def not_found(exc: Exception) -> tuple[dict[str, str], int]:
        return {"error": "Archive resource not found"}, 404

    @app.errorhandler(ValueError)
    @app.errorhandler(json.JSONDecodeError)
    def invalid_archive(exc: Exception) -> tuple[dict[str, str], int]:
        LOGGER.error("Invalid retained archive: %s", exc)
        return {"error": "Retained archive failed manifest validation"}, 500

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/days/<utc_date>")
    def day_manifest(utc_date: str) -> tuple[dict[str, Any], int] | dict[str, Any]:
        try:
            parsed_date = date.fromisoformat(utc_date)
        except ValueError:
            return {"error": "Date must use YYYY-MM-DD format"}, 400
        rows = queue.completed_rows(parsed_date)
        if not rows:
            return {"error": f"No completed archive for {parsed_date}"}, 404
        manifest_path, payload = _safe_manifest(archive_root, rows[0])
        tag = str(payload.get("release_tag", ""))
        if not tag or Path(tag).name != tag or manifest_path.parent.name != tag:
            raise ValueError("Manifest release tag is unsafe or inconsistent")
        assets: list[dict[str, Any]] = []
        for item in payload.get("assets", []):
            if not isinstance(item, dict):
                raise ValueError("Manifest contains invalid asset metadata")
            name = str(item.get("name", ""))
            digest = str(item.get("sha256", ""))
            if (
                not name
                or Path(name).name != name
                or SHA256_RE.fullmatch(digest) is None
            ):
                raise ValueError("Manifest contains an unsafe asset")
            asset_path = (manifest_path.parent / name).resolve(strict=True)
            if asset_path.parent != manifest_path.parent:
                raise ValueError("Manifest asset lies outside its release directory")
            size = int(item.get("size", -1))
            if size < 0 or asset_path.stat().st_size != size:
                raise ValueError("Manifest asset size does not match disk")
            assets.append(
                {
                    "name": name,
                    "size": size,
                    "sha256": digest,
                    "download_path": (
                        f"/v1/releases/{quote(tag, safe='')}/assets/"
                        f"{quote(name, safe='')}"
                    ),
                }
            )
        if not assets:
            raise ValueError("Manifest contains no assets")
        return {
            "utc_date": parsed_date.isoformat(),
            "repository": payload.get("repository"),
            "release_tag": tag,
            "release_url": payload.get("release_url"),
            "published_at": payload.get("published_at"),
            "total_bytes": payload.get("total_bytes"),
            "assets": assets,
        }

    @app.get("/v1/releases/<tag>/assets/<name>")
    def download_asset(tag: str, name: str) -> Response:
        if Path(tag).name != tag or Path(name).name != name:
            return jsonify({"error": "Unsafe archive path"}), 400
        matching_rows = [
            row for row in queue.completed_rows() if row.get("release_tag") == tag
        ]
        if not matching_rows:
            return jsonify({"error": "Completed release not found"}), 404
        manifest_path, payload = _safe_manifest(archive_root, matching_rows[0])
        asset = next(
            (
                item
                for item in payload.get("assets", [])
                if isinstance(item, dict) and item.get("name") == name
            ),
            None,
        )
        if asset is None:
            return jsonify({"error": "Release asset not found"}), 404
        path = (manifest_path.parent / name).resolve(strict=True)
        if path.parent != manifest_path.parent or path.stat().st_size != int(asset["size"]):
            raise ValueError("Release asset failed path or size validation")
        response = send_file(
            path,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=name,
            conditional=True,
            etag=False,
            max_age=0,
        )
        response.headers["X-Content-SHA256"] = str(asset["sha256"])
        return response

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Heligent raw archive API")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5090)
    parser.add_argument("--allow-unauthenticated", action="store_true")
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_archive_api(
        args.root,
        token=os.getenv("ADSB_ARCHIVE_API_TOKEN"),
        allow_unauthenticated=args.allow_unauthenticated,
    )
    LOGGER.info("Archive API listening on http://%s:%s", args.host, args.port)
    serve(app, host=args.host, port=args.port, threads=4)


if __name__ == "__main__":
    main()
