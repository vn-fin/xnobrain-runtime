#!/usr/bin/env python3
"""Create a PDF from a JSON spec, preserving source URLs as URI annotations."""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+(?:\([^\s)]*\)[^\s)]*)?)\)|(https?://[^\s<>]+)", re.I)
TRAILING_PUNCTUATION = ".,;:!?"


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _clean_url(raw: str) -> tuple[str, str]:
    url = raw
    suffix = ""
    while url and url[-1] in TRAILING_PUNCTUATION:
        suffix = url[-1] + suffix
        url = url[:-1]
    while url.endswith(")") and url.count("(") < url.count(")"):
        suffix = ")" + suffix
        url = url[:-1]
    return url, suffix


def linkify(text: Any) -> str:
    """Escape text and turn Markdown or visible HTTP(S) URLs into PDF links."""
    raw = str(text or "")
    parts: list[str] = []
    offset = 0
    for match in LINK_RE.finditer(raw):
        parts.append(escape(raw[offset:match.start()]))
        label = match.group(1)
        url, suffix = _clean_url(match.group(2) or match.group(3) or "")
        visible = label or url
        parts.append(
            f'<link href="{escape(url, quote=True)}" color="#1155cc">'
            f'<u>{escape(visible)}</u></link>{escape(suffix)}'
        )
        offset = match.end()
    parts.append(escape(raw[offset:]))
    return "".join(parts).replace("\n", "<br/>")


def _text_values(spec: dict[str, Any]) -> Iterable[str]:
    for element in spec.get("elements", []):
        if not isinstance(element, dict):
            continue
        if element.get("type") in {"heading", "paragraph"}:
            yield str(element.get("text") or "")
        if element.get("type") == "table":
            for row in element.get("rows", []):
                for cell in row if isinstance(row, list) else []:
                    if isinstance(cell, str):
                        yield cell


def expected_urls(spec: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for text in _text_values(spec):
        for match in LINK_RE.finditer(text):
            url, _ = _clean_url(match.group(2) or match.group(3) or "")
            if url:
                urls.add(url)
    return urls


def verify_pdf_links(path: str, expected: set[str]) -> None:
    if not expected:
        return
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf is required to verify generated PDF links") from exc
    actual: set[str] = set()
    for page in PdfReader(path).pages:
        for annotation_ref in page.get("/Annots", []):
            annotation = annotation_ref.get_object()
            action = annotation.get("/A")
            uri = action.get("/URI") if action else None
            if uri:
                actual.add(str(uri))
    missing = expected - actual
    if missing:
        raise RuntimeError(f"PDF link verification failed; missing URI annotations: {sorted(missing)}")


def build_pdf(spec: dict[str, Any], out_path: str) -> int:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        print("Missing dependency: install with 'python3 -m pip install reportlab'", file=sys.stderr)
        return 2

    page_size = letter if str(spec.get("page_size", "A4")).lower() == "letter" else A4
    styles = getSampleStyleSheet()
    story = []
    for element in spec.get("elements", []):
        element_type = element.get("type")
        if element_type == "heading":
            level = min(max(int(element.get("level", 1)), 1), 3)
            story.append(Paragraph(linkify(element.get("text", "")), styles[f"Heading{level}"]))
        elif element_type == "paragraph":
            story.append(Paragraph(linkify(element.get("text", "")), styles["BodyText"]))
            story.append(Spacer(1, 6))
        elif element_type == "table":
            rows = element.get("rows", [])
            if not rows:
                continue
            linked_rows = [
                [Paragraph(linkify(cell), styles["BodyText"]) if isinstance(cell, str) else cell for cell in row]
                for row in rows
            ]
            table = Table(linked_rows, repeatRows=1 if element.get("header", True) else 0)
            style = [("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]
            if element.get("header", True):
                style += [("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
            table.setStyle(TableStyle(style))
            story.extend((table, Spacer(1, 10)))
        elif element_type == "image":
            kwargs = {key: float(element[key]) for key in ("width", "height") if element.get(key)}
            image = Image(element["path"], **kwargs)
            if "width" in kwargs and "height" not in kwargs:
                image.drawWidth = kwargs["width"]
                image.drawHeight = kwargs["width"] * image.imageHeight / image.imageWidth
            story.extend((image, Spacer(1, 10)))
        elif element_type == "pagebreak":
            story.append(PageBreak())
        else:
            print(f"Warning: unknown element type {element_type!r}, skipped", file=sys.stderr)

    def draw_page_number(canvas, doc) -> None:
        if spec.get("page_numbers", True):
            canvas.saveState()
            canvas.setFont("Helvetica", 9)
            canvas.drawCentredString(page_size[0] / 2.0, 0.5 * inch, f"Page {doc.page}")
            canvas.restoreState()

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(out_path, pagesize=page_size, title=spec.get("title", ""), author=spec.get("author", ""))
    doc.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)
    try:
        verify_pdf_links(out_path, expected_urls(spec))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps({"output": out_path, "elements": len(spec.get("elements", [])), "links_verified": len(expected_urls(spec))}))
    return 0


def main() -> int:
    _reconfigure_stdio()
    parser = argparse.ArgumentParser(description="Create a linked PDF from a UTF-8 JSON spec.")
    parser.add_argument("spec")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    with open(args.spec, encoding="utf-8") as handle:
        spec = json.load(handle)
    return build_pdf(spec, args.output)


if __name__ == "__main__":
    sys.exit(main())
