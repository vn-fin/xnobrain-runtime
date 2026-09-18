from __future__ import annotations

import unittest
from unittest.mock import patch

from xnobrain.diagram_attachment import (
    DiagramAttachmentError,
    merge_into_message,
    validate_attachment,
)

XML = """<?xml version="1.0" encoding="UTF-8"?>
<flowchart>
  <node id="n1" x="40" y="80">Start</node>
  <node id="n2" x="240" y="80">Done</node>
  <edge id="e1" from="n1" to="n2"/>
</flowchart>
"""


class DiagramAttachmentTests(unittest.TestCase):
    def test_valid_flowchart_is_accepted(self) -> None:
        result = validate_attachment(
            {
                "kind": "diagram",
                "filename": "sketch.xml",
                "mime_type": "application/xml",
                "content": XML,
            }
        )
        self.assertEqual(result["node_count"], 2)
        self.assertEqual(result["edge_count"], 1)
        self.assertEqual(merge_into_message("Fix this", result), f"Fix this\n\n{XML.strip()}")
        self.assertEqual(merge_into_message("  ", result), XML.strip())

    def test_text_only_requests_skip_attachment(self) -> None:
        self.assertIsNone(validate_attachment(None))
        self.assertEqual(merge_into_message("hello", None), "hello")

    def test_dtd_and_unknown_kind_are_rejected(self) -> None:
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment(
                {
                    "kind": "diagram",
                    "filename": "sketch.xml",
                    "mime_type": "application/xml",
                    "content": '<!DOCTYPE flowchart [<!ENTITY x SYSTEM "file:///etc/passwd">]><flowchart/>',
                }
            )
        self.assertEqual(error.exception.code, "attachment_malformed")
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment({"kind": "image", "filename": "sketch.xml", "mime_type": "image/png", "content": XML})
        self.assertEqual(error.exception.code, "attachment_unsupported")

    def test_disabled_flag_hides_the_capability(self) -> None:
        with patch("xnobrain.diagram_attachment.feature_enabled", return_value=False):
            with self.assertRaises(DiagramAttachmentError) as error:
                validate_attachment(
                    {
                        "kind": "diagram",
                        "filename": "sketch.xml",
                        "mime_type": "application/xml",
                        "content": XML,
                    }
                )
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(error.exception.code, "feature_disabled")
