"""Bounded request-scoped flowchart XML validation.

User diagram text is never logged. The decoder rejects DTDs/entities, caps
size and graph cardinality, and keeps a typed attachment out of ``input``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from xml.etree import ElementTree

from .feature_flags import FEATURE_COMPOSER_SKETCH
from .feature_flags import enabled as feature_enabled

MAX_XML_CHARS = 64_000
MAX_NODES = 40
MAX_EDGES = 60
MAX_LABEL = 200
ALLOWED_FILENAME = "sketch.xml"
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")


class DiagramAttachmentError(Exception):
    """Safe, stable diagram transport failure."""

    def __init__(self, message: str, *, status: int, code: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def validate_attachment(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not feature_enabled(FEATURE_COMPOSER_SKETCH):
        raise DiagramAttachmentError(
            "composer sketches are disabled",
            status=404,
            code="feature_disabled",
        )
    if not isinstance(value, Mapping):
        raise DiagramAttachmentError(
            "diagram attachment is malformed",
            status=422,
            code="attachment_malformed",
        )
    kind = value.get("kind")
    mime = value.get("mime_type")
    filename = str(value.get("filename") or "").strip()
    content = value.get("content")
    if kind != "diagram" or mime not in {"application/xml", "text/xml"}:
        raise DiagramAttachmentError(
            "diagram attachment is unsupported",
            status=422,
            code="attachment_unsupported",
        )
    if filename != ALLOWED_FILENAME:
        raise DiagramAttachmentError(
            "diagram attachment is unsupported",
            status=422,
            code="attachment_unsupported",
        )
    if not isinstance(content, str) or not content.strip():
        raise DiagramAttachmentError(
            "diagram XML is malformed",
            status=422,
            code="attachment_malformed",
        )
    if "\x00" in content or len(content) > MAX_XML_CHARS:
        raise DiagramAttachmentError(
            "diagram exceeds the size limit",
            status=413,
            code="attachment_too_large",
        )
    if re.search(r"<!DOCTYPE", content, re.I) or re.search(r"<!ENTITY", content, re.I):
        raise DiagramAttachmentError(
            "diagram XML is malformed",
            status=422,
            code="attachment_malformed",
        )
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise DiagramAttachmentError(
            "diagram XML is malformed",
            status=422,
            code="attachment_malformed",
        ) from exc
    if root.tag != "flowchart":
        raise DiagramAttachmentError(
            "diagram XML is malformed",
            status=422,
            code="attachment_malformed",
        )
    nodes = list(root.findall("node"))
    edges = list(root.findall("edge"))
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise DiagramAttachmentError(
            "diagram exceeds the size limit",
            status=413,
            code="attachment_too_large",
        )
    ids: set[str] = set()
    for node in nodes:
        ident = str(node.get("id") or "")
        if not ID_PATTERN.fullmatch(ident) or ident in ids:
            raise DiagramAttachmentError(
                "diagram XML is malformed",
                status=422,
                code="attachment_malformed",
            )
        ids.add(ident)
        if len((node.text or "").strip()) > MAX_LABEL:
            raise DiagramAttachmentError(
                "diagram exceeds the size limit",
                status=413,
                code="attachment_too_large",
            )
    for edge in edges:
        ident = str(edge.get("id") or "")
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if (
            not ID_PATTERN.fullmatch(ident)
            or source not in ids
            or target not in ids
            or source == target
        ):
            raise DiagramAttachmentError(
                "diagram XML is malformed",
                status=422,
                code="attachment_malformed",
            )
    return {
        "kind": "diagram",
        "filename": ALLOWED_FILENAME,
        "mime_type": "application/xml",
        "content": content,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def merge_into_message(text: str, attachment: Mapping[str, Any] | None) -> str:
    """Adapter translation: XML is model text, never mixed into the UI input field."""
    prompt = (text or "").strip()
    if attachment is None:
        return prompt
    xml = str(attachment.get("content") or "").strip()
    if prompt and xml:
        return f"{prompt}\n\n{xml}"
    return prompt or xml
