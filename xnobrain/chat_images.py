"""Bounded, workspace-authorized image snapshots and native chat media."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .services.base import ServiceError

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}


def read_image(root: Path, relative: str) -> tuple[bytes, str, str]:
    """Read through directory descriptors: symlinks and traversal are forbidden."""
    if (
        not isinstance(relative, str)
        or len(relative) > 1024
        or "\\" in relative
        or any(ord(char) < 32 for char in relative)
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ServiceError("Invalid image path", status=422, code="invalid_chat_image_path")
    descriptors = []
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        parts = relative.split("/")
        for part in parts[:-1]:
            descriptor = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            descriptors.append(descriptor)
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        if info.st_size > MAX_IMAGE_BYTES:
            raise ServiceError("Image exceeds 5 MiB", status=413, code="chat_image_too_large")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise ServiceError(
            "Image is unavailable. Upload it again.", status=422, code="chat_image_unavailable"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if len(data) > MAX_IMAGE_BYTES:
        raise ServiceError("Image exceeds 5 MiB", status=413, code="chat_image_too_large")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in FORMATS or image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("unsupported dimensions or format")
            extension, mime = FORMATS[image.format]
            image.verify()
        with Image.open(io.BytesIO(data)) as decoded:
            decoded.load()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ServiceError(
            "Use a valid PNG, JPEG or WebP image (up to 40 MP).",
            status=422,
            code="unsupported_chat_image",
        ) from exc
    digest = hashlib.sha256(data).hexdigest()
    if relative.startswith(".chat-images/") and relative != f".chat-images/{digest}.{extension}":
        raise ServiceError(
            "Saved image has changed. Upload it again.", status=409, code="chat_image_changed"
        )
    return data, extension, mime


def snapshot_images(root: Path, paths: list[str] | None) -> list[str]:
    """Freeze admitted bytes under content identities, using workspace retention."""
    if not paths:
        return []
    if len(paths) > MAX_IMAGES or len(set(paths)) != len(paths):
        raise ServiceError(
            "Attach up to four unique images", status=422, code="invalid_chat_images"
        )
    images = [read_image(root, path) for path in paths]
    result = []
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir(".chat-images", mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        directory = os.open(
            ".chat-images", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        try:
            for data, extension, _mime in images:
                name = f"{hashlib.sha256(data).hexdigest()}.{extension}"
                relative = f".chat-images/{name}"
                temporary = f".pending-{uuid.uuid4().hex}"
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.link(
                            temporary,
                            name,
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        read_image(root, relative)
                finally:
                    os.unlink(temporary, dir_fd=directory)
                result.append(relative)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ServiceError(
            "Could not save the image snapshot. Retry the attachment.",
            status=503,
            code="chat_image_storage_unavailable",
        ) from exc
    finally:
        os.close(root_fd)
    return result


def image_message(root: Path, text: str, paths: list[str]) -> str | list[dict[str, Any]]:
    """Produce native media parts; never rely on the model finding a filename."""
    if not paths:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    for path in paths:
        data, _extension, mime = read_image(root, path)
        url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def display_message(row: dict[str, Any]) -> dict[str, Any]:
    """Keep pixel payloads out of history DTOs; expose immutable retry references."""
    if row.get("role") not in {"user", "tool"}:
        return row
    content = row.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return row
    if not isinstance(content, list):
        return row
    texts, paths = [], []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            texts.append(str(part.get("text") or ""))
        if part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            for extension, mime in FORMATS.values():
                prefix = f"data:{mime};base64,"
                if url.startswith(prefix):
                    try:
                        data = base64.b64decode(url[len(prefix) :], validate=True)
                    except ValueError:
                        continue
                    paths.append(f".chat-images/{hashlib.sha256(data).hexdigest()}.{extension}")
    return {
        **row,
        "content": "\n".join(texts),
        **({"image_paths": paths} if row.get("role") == "user" else {}),
    }
