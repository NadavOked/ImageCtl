"""Re-verify stored image partition files against their manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .images import ImageLibrary, inside, streamed_partitions, valid_image_id

CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _scrub_image(root: Path, manifest: dict) -> dict:
    files = []
    folder = Path(manifest["_dir"])
    for part in streamed_partitions(manifest):
        expected = part["sha256"]
        candidate = folder / part["file"]
        path = inside(candidate, root)
        result = {
            "index": part["index"],
            "file": part["file"],
            "expected_sha256": expected,
        }
        try:
            if not candidate.exists():
                result["state"] = "missing"
            elif path is None:
                result["state"] = "unreadable"
            elif not path.is_file():
                result["state"] = "unreadable"
            else:
                actual = _sha256(path)
                result["actual_sha256"] = actual
                result["state"] = "ok" if actual == expected else "mismatch"
        except OSError as exc:
            result["state"] = "unreadable"
            result["error"] = str(exc)
        files.append(result)
    return {
        "id": manifest["id"],
        "state": "intact" if all(item["state"] == "ok" for item in files) else "drift",
        "files": files,
    }


def scrub_library(root: str | Path, image_id: str | None = None) -> dict:
    """Hash one stored image, or every image when ``image_id`` is omitted."""
    root = Path(root)
    if image_id is not None and not valid_image_id(image_id):
        raise ValueError("invalid image id")
    manifests = ImageLibrary(root).scan()
    if image_id is not None:
        manifest = manifests.get(image_id)
        if manifest is None:
            raise KeyError(image_id)
        selected = [manifest]
    else:
        selected = [manifests[key] for key in sorted(manifests)]
    images = [_scrub_image(root, manifest) for manifest in selected]
    return {"intact": all(image["state"] == "intact" for image in images), "images": images}
