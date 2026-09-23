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

MINDMAP_V2 = """<?xml version="1.0" encoding="UTF-8"?>
<mindmap version="2">
  <node id="root">Root</node>
  <node id="test1" parent="root" order="0">test 1</node>
  <node id="test2" parent="root" order="1">test 2</node>
  <edge id="ref1" source="test2" target="test1" />
  <edge id="ref2" source="test1" target="test2" />
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
    def test_flowchart_root_is_rejected(self) -> None:
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment(_attachment(XML))
        self.assertEqual(error.exception.status, 422)
        self.assertEqual(error.exception.code, "attachment_malformed")

    def test_valid_mindmap_is_accepted(self) -> None:
        result = validate_attachment(_attachment(MINDMAP))
        self.assertEqual(result["node_count"], 3)
        self.assertEqual(result["edge_count"], 0)
        self.assertEqual(result["diagram_kind"], "mindmap")
        merged = merge_into_message("Plan this", result)
        self.assertIn("Plan this", merged)
        self.assertIn("User mind map (hierarchy with optional directed references):", merged)
        self.assertIn("<mindmap>", merged)
        self.assertEqual(
            merge_into_message("  ", result),
            f"User mind map (hierarchy with optional directed references):\n{MINDMAP.strip()}",
        )

    def test_valid_v2_directed_references_are_accepted(self) -> None:
        result = validate_attachment(_attachment(MINDMAP_V2))
        self.assertEqual(result["node_count"], 3)
        self.assertEqual(result["edge_count"], 2)
        merged = merge_into_message("Plan this", result)
        self.assertIn("hierarchy with optional directed references", merged)
        self.assertIn('source="test2" target="test1"', merged)

    def test_v2_reference_validation_and_version_errors(self) -> None:
        malformed = [
            '<mindmap version="2"><node id="n1">A</node><edge id="e1" source="n1" target="n1" /></mindmap>',
            '<mindmap version="2"><node id="n1">A</node><edge id="e1" source="n1" target="missing" /></mindmap>',
            '<mindmap version="2"><node id="n1">A</node><node id="n2" parent="n1">B</node><edge id="e1" source="n1" target="n2" /></mindmap>',
        ]
        for content in malformed:
            with self.subTest(content=content):
                with self.assertRaises(DiagramAttachmentError) as error:
                    validate_attachment(_attachment(content))
                self.assertEqual(error.exception.code, "attachment_malformed")
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment(
                _attachment('<mindmap version="3"><node id="n1">A</node></mindmap>')
            )
        self.assertEqual(error.exception.code, "attachment_unsupported")

    def test_v2_reference_limit_and_duplicate_attributes(self) -> None:
        nodes = ['<node id="n1">Root</node>'] + [
            f'<node id="n{index}" parent="n1">Node {index}</node>' for index in range(2, 15)
        ]
        pairs = [
            (source, target)
            for source in range(2, 15)
            for target in range(2, 15)
            if source != target
        ]
        edges = [
            f'<edge id="r{index}" source="n{source}" target="n{target}" />'
            for index, (source, target) in enumerate(pairs[:80], start=1)
        ]
        accepted = validate_attachment(
            _attachment(f'<mindmap version="2">{"".join(nodes + edges)}</mindmap>')
        )
        self.assertEqual(accepted["edge_count"], 80)
        source, target = pairs[80]
        too_many = edges + [f'<edge id="r81" source="n{source}" target="n{target}" />']
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment(
                _attachment(f'<mindmap version="2">{"".join(nodes + too_many)}</mindmap>')
            )
        self.assertEqual(error.exception.code, "attachment_too_large")

        duplicates = [
            '<mindmap version="2" version="2"><node id="n1">A</node></mindmap>',
            '<mindmap><node id="n1" id="n2">A</node></mindmap>',
        ]
        for content in duplicates:
            with self.subTest(content=content):
                with self.assertRaises(DiagramAttachmentError) as duplicate_error:
                    validate_attachment(_attachment(content))
                self.assertEqual(duplicate_error.exception.code, "attachment_malformed")

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
            validate_attachment(
                {
                    "kind": "image",
                    "filename": "sketch.xml",
                    "mime_type": "image/png",
                    "content": XML,
                }
            )
        self.assertEqual(error.exception.code, "attachment_unsupported")

    def test_mindmap_tree_errors_are_malformed(self) -> None:
        cases = [
            '<mindmap><node id="n1">A</node><node id="n2">B</node></mindmap>',
            '<mindmap><node id="n1">A</node><node id="n2" parent="n3">B</node></mindmap>',
            '<mindmap><node id="n1" parent="n1">A</node></mindmap>',
            (
                '<mindmap><node id="n1" parent="n2">A</node>'
                '<node id="n2" parent="n1">B</node></mindmap>'
            ),
            (
                '<mindmap><node id="n1">Root</node>'
                '<node id="n2" parent="n3">A</node>'
                '<node id="n3" parent="n2">B</node></mindmap>'
            ),
            '<mindmap><node id="n1">Root</node><node id="n2" parent="n2">Loop</node></mindmap>',
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

    def test_cyclic_mindmap_is_rejected_before_merge(self) -> None:
        cycles = [
            '<mindmap><node id="n1" parent="n1">A</node></mindmap>',
            (
                '<mindmap><node id="n1" parent="n2">A</node>'
                '<node id="n2" parent="n1">B</node></mindmap>'
            ),
            (
                '<mindmap><node id="n1">Root</node>'
                '<node id="n2" parent="n3">A</node>'
                '<node id="n3" parent="n2">B</node></mindmap>'
            ),
        ]
        for content in cycles:
            with self.subTest(content=content):
                with self.assertRaises(DiagramAttachmentError) as error:
                    validate_attachment(_attachment(content))
                self.assertEqual(error.exception.status, 422)
                self.assertEqual(error.exception.code, "attachment_malformed")
        valid = '<mindmap><node id="n1">Root</node><node id="n2" parent="n1">Child</node></mindmap>'
        accepted = validate_attachment(_attachment(valid))
        merged = merge_into_message("Plan this", accepted)
        self.assertIn("Plan this", merged)
        self.assertIn("User mind map (hierarchy with optional directed references):", merged)
        self.assertIn("<mindmap>", merged)

    def test_mindmap_size_limit(self) -> None:
        nodes = ['<node id="n1">x</node>'] + [
            f'<node id="n{index}" parent="n1">x</node>' for index in range(2, 42)
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
        first = validate_attachment({**_attachment(MINDMAP), "filename": "mindmap.xml"})
        second = validate_attachment({**_attachment(MINDMAP), "filename": "mindmap-2.xml"})
        self.assertEqual(first["filename"], "mindmap.xml")
        self.assertEqual(second["filename"], "mindmap-2.xml")
        merged = merge_attachments("Use both", [first, second])
        self.assertIn("Use both", merged)
        self.assertEqual(
            merged.count("User mind map (hierarchy with optional directed references):"), 2
        )
        self.assertIn("<mindmap>", merged)
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachments([_attachment(MINDMAP), _attachment(MINDMAP)])
        self.assertEqual(error.exception.code, "attachment_malformed")
        with self.assertRaises(DiagramAttachmentError) as error:
            validate_attachment({**_attachment(MINDMAP), "filename": "diagram.xml"})
        self.assertEqual(error.exception.code, "attachment_unsupported")
