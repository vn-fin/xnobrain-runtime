from __future__ import annotations

import unittest
from unittest.mock import patch

from xnobrain.diagram_attachment import (
    DiagramAttachmentError,
    merge_attachments,
    merge_into_message,
    validate_attachment,
    validate_attachments,
)

XML = """<?xml version="1.0" encoding="UTF-8"?>
<flowchart>
  <node id="n1" x="40" y="80">Start</node>
  <node id="n2" x="240" y="80">Done</node>
  <edge id="e1" from="n1" to="n2"/>
</flowchart>
"""

MINDMAP = """<?xml version="1.0" encoding="UTF-8"?>
<mindmap>
  <node id="n1">Root</node>
  <node id="n2" parent="n1" order="0">Child</node>
  <node id="n3" parent="n1" order="1" collapsed="true">Sibling</node>
</mindmap>
"""


def _attachment(content: str) -> dict[str, str]:
    return {
        "kind": "diagram",
        "filename": "sketch.xml",
        "mime_type": "application/xml",
        "content": content,
    }


class DiagramAttachmentTests(unittest.TestCase):
    def test_valid_flowchart_is_accepted(self) -> None:
        result = validate_attachment(_attachment(XML))
        self.assertEqual(result["node_count"], 2)
        self.assertEqual(result["edge_count"], 1)
        self.assertEqual(result["diagram_kind"], "flowchart")
        self.assertEqual(merge_into_message("Fix this", result), f"Fix this\n\n{XML.strip()}")
        self.assertEqual(merge_into_message("  ", result), XML.strip())

    def test_valid_mindmap_is_accepted(self) -> None:
        result = validate_attachment(_attachment(MINDMAP))
        self.assertEqual(result["node_count"], 3)
        self.assertEqual(result["edge_count"], 0)
        self.assertEqual(result["diagram_kind"], "mindmap")
        merged = merge_into_message("Plan this", result)
        self.assertIn("Plan this", merged)
        self.assertIn("User mind map (tree):", merged)
        self.assertIn("<mindmap>", merged)
        self.assertEqual(
            merge_into_message("  ", result),
            f"User mind map (tree):\n{MINDMAP.strip()}",
        )

    def test_text_only_requests_skip_attachment(self) -> None:
        self.assertIsNone(validate_attachment(None))
        self.assertEqual(merge_into_message("hello", None), "hello")

    def test_dtd_and_unknown_kind_are_rejected(self) -> None:
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment(
                _attachment(
                    '<!DOCTYPE flowchart [<!ENTITY x SYSTEM "file:///etc/passwd">]><flowchart/>'
                )
            )
        self.assertEqual(error.exception.code, "attachment_malformed")
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment({"kind": "image", "filename": "sketch.xml", "mime_type": "image/png", "content": XML})
        self.assertEqual(error.exception.code, "attachment_unsupported")

    def test_mindmap_tree_errors_are_malformed(self) -> None:
        cases = [
            '<mindmap><node id="n1">A</node><node id="n2">B</node></mindmap>',
            '<mindmap><node id="n1">A</node><node id="n2" parent="n3">B</node></mindmap>',
            '<mindmap><node id="n1" parent="n2">A</node><node id="n2" parent="n1">B</node></mindmap>',
            '<mindmap><node id="n1" x="10">A</node></mindmap>',
            '<mindmap><node id="n1">A</node><edge id="e1" from="n1" to="n1"/></mindmap>',
            '<mindmap><node id="n1" collapsed="false">A</node></mindmap>',
        ]
        for content in cases:
            with self.subTest(content=content):
                with self.assertRaises(DiagramAttachmentError) as error:
                    validate_attachment(_attachment(content))
                self.assertEqual(error.exception.code, "attachment_malformed")
                self.assertEqual(error.exception.status, 422)

    def test_mindmap_size_limit(self) -> None:
        nodes = ['<node id="n1">x</node>'] + [
            f'<node id="n{index}" parent="n1">x</node>'
            for index in range(2, 42)
        ]
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment(_attachment(f"<mindmap>{''.join(nodes)}</mindmap>"))
        self.assertEqual(error.exception.code, "attachment_too_large")
        self.assertEqual(error.exception.status, 413)

    def test_disabled_flag_hides_the_capability(self) -> None:
        with patch("xnobrain.diagram_attachment.feature_enabled", return_value=False):
            with self.assertRaises(DiagramAttachmentError) as error:
                validate_attachment(_attachment(XML))
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(error.exception.code, "feature_disabled")
        with patch("xnobrain.diagram_attachment.feature_enabled", return_value=False):
            with self.assertRaises(DiagramAttachmentError) as error:
                validate_attachment(_attachment(MINDMAP))
        self.assertEqual(error.exception.code, "feature_disabled")

    def test_numbered_filename_and_multiple_attachments(self) -> None:
        flowchart = validate_attachment({**_attachment(XML), "filename": "flowchart.xml"})
        mindmap = validate_attachment({**_attachment(MINDMAP), "filename": "mindmap-2.xml"})
        self.assertEqual(flowchart["filename"], "flowchart.xml")
        self.assertEqual(mindmap["filename"], "mindmap-2.xml")
        merged = merge_attachments("Use both", [flowchart, mindmap])
        self.assertIn("Use both", merged)
        self.assertIn("<flowchart>", merged)
        self.assertIn("User mind map (tree):", merged)
        self.assertIn("<mindmap>", merged)
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachments([_attachment(XML), _attachment(MINDMAP)])
        self.assertEqual(error.exception.code, "attachment_malformed")
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment({**_attachment(XML), "filename": "diagram.xml"})
        self.assertEqual(error.exception.code, "attachment_unsupported")
