"""Publish the versioned Foxglove extension and organization layout together."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_BASE = "https://api.foxglove.dev/v1"
DEFAULT_LAYOUT_ID = "lay_0eaTLQSSPmExnWfB"
DEFAULT_LAYOUT_NAME = "Quest Unified Teleop"


class FoxgloveApiError(RuntimeError):
    """An HTTP failure from the Foxglove API without credential disclosure."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Foxglove API returned HTTP {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class ExtensionManifest:
    publisher: str
    name: str
    version: str
    display_name: str


@dataclass(frozen=True)
class PublishResult:
    extension_id: str
    extension_version: str
    layout_id: str
    layout_updated_at: str


class FoxgloveApi:
    def __init__(self, api_key: str, *, base_url: str = API_BASE) -> None:
        if not api_key:
            raise ValueError("FOXGLOVE_API_KEY is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> Any:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": content_type,
                "User-Agent": "quest-trajectory-recorder/foxglove-publisher",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8", errors="replace").strip()
            raise FoxgloveApiError(error.code, payload or error.reason) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"Foxglove API connection failed: {error.reason}"
            ) from error
        return json.loads(payload) if payload else None

    def upload_extension(self, extension: Path) -> dict[str, Any]:
        return self._request(
            "POST",
            "/extension-upload",
            body=extension.read_bytes(),
            content_type="application/octet-stream",
        )

    def list_extensions(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/extensions")
        if not isinstance(result, list):
            raise TypeError("Foxglove extensions response was not a list")
        return result

    def update_layout(
        self,
        layout_id: str,
        *,
        name: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps(
            {"name": name, "permission": "ORG_WRITE", "data": data},
            separators=(",", ":"),
        ).encode()
        return self._request("PATCH", f"/layouts/{layout_id}", body=body)

    def get_layout(self, layout_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"includeData": "true"})
        return self._request("GET", f"/layouts/{layout_id}?{query}")


def read_extension_manifest(extension: Path) -> ExtensionManifest:
    if extension.suffix != ".foxe":
        raise ValueError(f"extension must be a .foxe archive: {extension}")
    try:
        with zipfile.ZipFile(extension) as archive:
            raw = json.loads(archive.read("package.json"))
    except (
        FileNotFoundError,
        KeyError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(f"invalid Foxglove extension archive: {extension}") from error

    required = {
        key: raw.get(key) for key in ("publisher", "name", "version", "displayName")
    }
    missing = [
        key
        for key, value in required.items()
        if not isinstance(value, str) or not value
    ]
    if missing:
        raise ValueError(f"extension manifest is missing: {', '.join(missing)}")
    return ExtensionManifest(
        publisher=required["publisher"],
        name=required["name"],
        version=required["version"],
        display_name=required["displayName"],
    )


def _matching_extension(
    extensions: list[dict[str, Any]], manifest: ExtensionManifest
) -> dict[str, Any] | None:
    for extension in extensions:
        if (
            str(extension.get("publisher", "")).casefold()
            == manifest.publisher.casefold()
            and str(extension.get("name", "")).casefold() == manifest.name.casefold()
        ):
            return extension
    return None


def publish_assets(
    api: FoxgloveApi,
    *,
    extension: Path,
    layout: Path,
    layout_id: str = DEFAULT_LAYOUT_ID,
    layout_name: str = DEFAULT_LAYOUT_NAME,
) -> PublishResult:
    manifest = read_extension_manifest(extension)
    extension_sha256 = hashlib.sha256(extension.read_bytes()).hexdigest()
    layout_data = json.loads(layout.read_text(encoding="utf-8"))
    if not isinstance(layout_data, dict):
        raise TypeError("Foxglove layout must contain a JSON object")

    try:
        upload = api.upload_extension(extension)
        extension_id = str(upload["id"])
    except FoxgloveApiError as error:
        if error.status != 409:
            raise
        installed = _matching_extension(api.list_extensions(), manifest)
        if (
            installed is None
            or installed.get("activeVersion") != manifest.version
            or installed.get("sha256Sum") != extension_sha256
        ):
            raise RuntimeError(
                "this extension version already exists with different contents; "
                "bump package.json version"
            ) from error
        extension_id = str(installed["id"])

    updated = api.update_layout(layout_id, name=layout_name, data=layout_data)

    installed = _matching_extension(api.list_extensions(), manifest)
    if (
        installed is None
        or installed.get("activeVersion") != manifest.version
        or installed.get("sha256Sum") != extension_sha256
    ):
        raise RuntimeError("published Foxglove extension version did not become active")
    remote_layout = api.get_layout(layout_id)
    if remote_layout.get("data") != layout_data:
        raise RuntimeError(
            "organization layout verification did not match the repository export"
        )

    return PublishResult(
        extension_id=extension_id,
        extension_version=manifest.version,
        layout_id=str(updated.get("id", layout_id)),
        layout_updated_at=str(updated.get("updatedAt", "")),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish a .foxe extension and the Quest organization layout atomically."
    )
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument(
        "--layout",
        type=Path,
        default=Path("foxglove/quest_teleop.foxglove-layout.json"),
    )
    parser.add_argument("--layout-id", default=DEFAULT_LAYOUT_ID)
    parser.add_argument("--layout-name", default=DEFAULT_LAYOUT_NAME)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.environ.get("FOXGLOVE_API_KEY", "")
    if not api_key:
        print("FOXGLOVE_API_KEY is required", file=sys.stderr)
        return 2
    try:
        result = publish_assets(
            FoxgloveApi(api_key),
            extension=args.extension,
            layout=args.layout,
            layout_id=args.layout_id,
            layout_name=args.layout_name,
        )
    except (
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(
        f"Published extension {result.extension_id} v{result.extension_version} "
        f"and layout {result.layout_id} ({result.layout_updated_at})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
