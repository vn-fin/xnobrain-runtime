"""Bounded request-scoped flowchart and mind map XML validation.

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
MAX_ATTACHMENTS = 8
ALLOWED_FILENAME = "sketch.xml"
FILENAME_PATTERN = re.compile(r"^(?:sketch|flowchart|mindmap)(?:-[1-9]\d*)?\.xml$")
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
MINDMAP_PROMPT_LABEL = "User mind map (tree):"
_LAYOUT_ATTRS = frozenset({"x", "y", "w", "h"})


class DiagramAttachmentError(Exception):
    """Safe, stable diagram transport failure."""

    def __init__(self, message: str, *, status: int, code: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _malformed() -> DiagramAttachmentError:
    return DiagramAttachmentError(
        "diagram XML is malformed",
        status=422,
        code="attachment_malformed",
    )


def _too_large() -> DiagramAttachmentError:
    return DiagramAttachmentError(
        "diagram exceeds the size limit",
        status=413,
        code="attachment_too_large",
    )


def _validate_ids_and_labels(nodes: list[ElementTree.Element]) -> set[str]:
    ids: set[str] = set()
    for node in nodes:
        ident = str(node.get("id") or "")
        if not ID_PATTERN.fullmatch(ident) or ident in ids:
            raise _malformed()
        ids.add(ident)
        if len((node.text or "").strip()) > MAX_LABEL:
            raise _too_large()
    return ids


def _validate_flowchart(root: ElementTree.Element) -> tuple[int, int]:
    nodes = list(root.findall("node"))
    edges = list(root.findall("edge"))
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise _too_large()
    ids = _validate_ids_and_labels(nodes)
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
            raise _malformed()
    return len(nodes), len(edges)


def _validate_mindmap(root: ElementTree.Element) -> tuple[int, int]:
    if any(child.tag != "node" for child in list(root)):
        raise _malformed()
    nodes = list(root.findall("node"))
    if len(nodes) > MAX_NODES:
        raise _too_large()
    ids = _validate_ids_and_labels(nodes)
    parents: dict[str, str | None] = {}
    for node in nodes:
        ident = str(node.get("id") or "")
        if _LAYOUT_ATTRS.intersection(node.attrib):
            raise _malformed()
        collapsed = node.get("collapsed")
        if collapsed is not None and collapsed != "true":
            raise _malformed()
        order = node.get("order")
        if order is not None and not re.fullmatch(r"[0-9]+", order):
            raise _malformed()
        parent = node.get("parent")
        if parent is None:
            parents[ident] = None
            continue
        if parent not in ids or parent == ident:
            raise _malformed()
        parents[ident] = parent
    roots = [ident for ident, parent in parents.items() if parent is None]
    if len(roots) != 1:
        raise _malformed()
    children: dict[str, list[str]] = {ident: [] for ident in ids}
    for ident, parent in parents.items():
        if parent is not None:
            children[parent].append(ident)
    seen: set[str] = set()
    stack = [roots[0]]
    while stack:
        ident = stack.pop()
        if ident in seen:
            raise _malformed()
        seen.add(ident)
        stack.extend(children[ident])
    if seen != ids:
        raise _malformed()
    return len(nodes), 0


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
    if FILENAME_PATTERN.fullmatch(filename) is None:
        raise DiagramAttachmentError(
            "diagram attachment is unsupported",
            status=422,
            code="attachment_unsupported",
        )
    if not isinstance(content, str) or not content.strip():
        raise _malformed()
    if "\x00" in content or len(content) > MAX_XML_CHARS:
        raise _too_large()
    if re.search(r"<!DOCTYPE", content, re.I) or re.search(r"<!ENTITY", content, re.I):
        raise _malformed()
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise _malformed() from exc
    if root.tag == "flowchart":
        node_count, edge_count = _validate_flowchart(root)
        diagram_kind = "flowchart"
    elif root.tag == "mindmap":
        node_count, edge_count = _validate_mindmap(root)
        diagram_kind = "mindmap"
    else:
        raise _malformed()
    return {
        "kind": "diagram",
        "filename": filename,
        "mime_type": "application/xml",
        "content": content,
        "node_count": node_count,
        "edge_count": edge_count,
        "diagram_kind": diagram_kind,
    }


def _is_mindmap_xml(xml: str) -> bool:
    try:
        return ElementTree.fromstring(xml).tag == "mindmap"
    except ElementTree.ParseError:
        return False


def merge_into_message(text: str, attachment: Mapping[str, Any] | None) -> str:
    """Adapter translation: XML is model text, never mixed into the UI input field."""
    return merge_attachments(text, [attachment] if attachment is not None else [])


def merge_attachments(text: str, attachments: list[Mapping[str, Any]]) -> str:
    prompt = (text or "").strip()
    for attachment in attachments:
        prompt = _append_attachment(prompt, attachment)
    return prompt


def validate_attachments(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _malformed()
    if len(value) > MAX_ATTACHMENTS:
        raise _too_large()
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in value:
        parsed = validate_attachment(item)
        if parsed is None:
            raise _malformed()
        name = str(parsed["filename"])
        if name in seen:
            raise _malformed()
        seen.add(name)
        result.append(parsed)
    return result


def _append_attachment(prompt: str, attachment: Mapping[str, Any]) -> str:
    xml = str(attachment.get("content") or "").strip()
    if not xml:
        return prompt
    if attachment.get("diagram_kind") == "mindmap" or _is_mindmap_xml(xml):
        labeled = f"{MINDMAP_PROMPT_LABEL}\n{xml}"
        return f"{prompt}\n\n{labeled}" if prompt else labeled
    return f"{prompt}\n\n{xml}" if prompt else xml
