"""Synthetic image evidence: no external inference or customer documents."""

import asyncio
import base64
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw
from pydantic import ValidationError

from xnobrain.chat_images import display_message, image_message, read_image, snapshot_images
from xnobrain.integrations.image_tools import image_tool_scope, inspect_image, local_image_source
from xnobrain.models.conversations import ChatRequest
from xnobrain.services.base import ServiceError


class ChatImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "Ảnh scan 01.png"
        image = Image.new("RGB", (180, 80), "white")
        ImageDraw.Draw(image).text((8, 20), "SYNTHETIC-QR7", fill="black")
        image.save(self.source)

    def test_native_pixels_survive_original_replacement_and_history_retry(self):
        paths = snapshot_images(self.root, [self.source.name])
        original = self.source.read_bytes()
        self.source.write_bytes(b"replaced")
        message = image_message(self.root, "Read the marker", paths)
        pixels = base64.b64decode(message[1]["image_url"]["url"].split(",", 1)[1])
        self.assertEqual(pixels, original)
        dto = display_message({"role": "user", "content": json.dumps(message)})
        self.assertEqual(dto["content"], "Read the marker")
        self.assertEqual(dto["image_paths"], paths)
        self.assertNotIn("base64", json.dumps(dto))
        self.assertEqual(image_message(self.root, "Read the marker", dto["image_paths"]), message)
        (self.root / paths[0]).write_bytes(original + b"changed")
        with self.assertRaises(ServiceError) as caught:
            image_message(self.root, "Retry", paths)
        self.assertEqual(caught.exception.code, "chat_image_changed")

    def test_rejects_unsafe_missing_invalid_and_duplicate_paths(self):
        (self.root / "link.png").symlink_to(self.source)
        (self.root / "folder").symlink_to(self.root, target_is_directory=True)
        for path in (
            "../outside.png",
            str(self.source),
            "link.png",
            "folder/Ảnh scan 01.png",
            "missing.png",
            "bad\\path.png",
        ):
            with self.subTest(path=path), self.assertRaises(ServiceError):
                read_image(self.root, path)
        with self.assertRaises(ServiceError):
            snapshot_images(self.root, [self.source.name] * 2)
        self.source.write_bytes(b"not PNG")
        with self.assertRaises(ServiceError):
            read_image(self.root, self.source.name)

    def test_image_only_contract_and_limits(self):
        self.assertEqual(ChatRequest(image_paths=[self.source.name]).input, "")
        with self.assertRaises(ValidationError):
            ChatRequest(image_paths=["a"] * 5)
        with self.assertRaises(ValidationError):
            ChatRequest(input="")
        self.source.write_bytes(b"0" * (5 * 1024 * 1024 + 1))
        with self.assertRaises(ServiceError) as caught:
            read_image(self.root, self.source.name)
        self.assertEqual(caught.exception.code, "chat_image_too_large")

    def test_generated_relative_paths_and_cross_workspace_isolation(self):
        folder = self.root / "run2" / "reference"
        folder.mkdir(parents=True)
        (folder / "page.png").write_bytes(self.source.read_bytes())
        url = local_image_source(self.root, self.root / "run2", "reference/page.png")
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), self.source.read_bytes())
        with self.assertRaises(ServiceError):
            local_image_source(self.root, self.root.parent, "other/page.png")


class NativeImageToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_alias_preserves_pixels_through_real_engine_preprocessing(self):
        from run_agent import AIAgent

        from xnobrain.integrations.image_tools import install_image_tools

        agent = AIAgent.__new__(AIAgent)
        agent.provider = "custom:xnobrain"
        agent.model = "cx/gpt-6-sol"
        agent.run_conversation = lambda **kwargs: kwargs
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}}]
        messages = [{"role": "user", "content": content}]
        with (
            patch("hermes_cli.config.load_config", return_value={}),
            patch("agent.image_routing._lookup_supports_vision", return_value=None) as lookup,
            patch("tools.vision_tools.vision_analyze_tool", new_callable=AsyncMock) as auxiliary,
        ):
            self.assertFalse(agent._model_supports_vision())
            install_image_tools(agent, Path("/tmp"))
            for model in ("cx/gpt-6-sol", "cc/claude-synthetic"):
                agent.model = model
                self.assertIs(agent._prepare_messages_for_non_vision_model(messages), messages)
                self.assertIs(agent._prepare_anthropic_messages_for_api(messages), messages)
                self.assertEqual(
                    agent._tool_result_content_for_active_model(
                        "vision_analyze", {"_multimodal": True, "content": content}
                    ),
                    content,
                )
            lookup.assert_called_with("anthropic", "claude-synthetic", {})
            auxiliary.assert_not_awaited()

    async def test_text_model_preprocessing_requires_explicit_auxiliary(self):
        from run_agent import AIAgent

        from xnobrain.integrations.image_tools import install_image_tools

        agent = AIAgent.__new__(AIAgent)
        agent.provider = "custom:xnobrain"
        agent.model = "test/text"
        agent.run_conversation = lambda **kwargs: kwargs
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}}
                ],
            }
        ]
        with (
            patch("hermes_cli.config.load_config", return_value={}),
            patch("xnobrain.integrations.image_tools._vision_support", return_value=False),
            patch("tools.vision_tools.vision_analyze_tool", new_callable=AsyncMock) as auxiliary,
        ):
            install_image_tools(agent, Path("/tmp"))
            with self.assertRaises(ServiceError) as error:
                agent._prepare_messages_for_non_vision_model(messages)
            self.assertEqual(error.exception.code, "image_model_unsupported")
            auxiliary.assert_not_awaited()

    async def test_native_engine_delivers_pixels_and_partial_page_failures(self):
        # Actual pinned image resolver/encoder, with capability lookup stubbed.
        # This proves media transport, not model recognition or OCR.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "run2" / "reference"
            pages.mkdir(parents=True)
            scanned_pages = []
            for number in range(1, 22):
                image = Image.new("RGB", (180, 80), "white")
                ImageDraw.Draw(image).text((10, 20), f"PAGE-{number:02d}-QR7", fill="black")
                scanned_pages.append(image)
            scanned_pages[0].save(
                root / "synthetic.pdf", save_all=True, append_images=scanned_pages[1:]
            )
            subprocess.run(
                ["pdftoppm", "-r", "72", "-png", str(root / "synthetic.pdf"), str(pages / "page")],
                check=True,
                capture_output=True,
            )
            for number in range(1, 10):
                (pages / f"page-0{number}.png").rename(pages / f"page-{number}.png")
            (pages / "page-11.png").unlink()
            agent = SimpleNamespace(model="cc/synthetic-vision", session_id="synthetic")
            with (
                patch("xnobrain.integrations.image_tools._vision_support", return_value=True),
                patch("tools.terminal_tool.get_session_cwd", return_value=str(root)),
            ):
                results = await asyncio.gather(
                    *[
                        inspect_image(
                            {"image_url": f"run2/reference/page-{number}.png"},
                            (agent, root),
                            task_id=f"page-{number}",
                        )
                        for number in range(1, 22)
                    ]
                )
            self.assertEqual(
                sum(isinstance(result, dict) and result.get("_multimodal") for result in results),
                20,
            )
            self.assertEqual(json.loads(results[10])["code"], "chat_image_unavailable")
            for result in results[:10] + results[11:]:
                self.assertEqual(result["content"][1]["type"], "image_url")
                self.assertNotIn("base64", json.dumps(result["meta"]))

    async def test_explicit_text_model_does_not_call_default_auxiliary(self):
        agent = SimpleNamespace(model="test/text", session_id="text")
        with (
            patch("xnobrain.integrations.image_tools._vision_support", return_value=False),
            patch("hermes_cli.config.load_config", return_value={}),
            patch("tools.vision_tools._handle_vision_analyze", new_callable=AsyncMock) as auxiliary,
        ):
            result = await inspect_image(
                {"image_url": "https://example.com/page.png"}, (agent, Path("/tmp"))
            )
        self.assertEqual(json.loads(result)["code"], "image_model_unsupported")
        auxiliary.assert_not_awaited()

    async def test_alias_lookup_uses_active_model_and_explicit_override(self):
        from xnobrain.integrations.image_tools import _vision_support

        with patch("agent.image_routing._lookup_supports_vision", return_value=True) as lookup:
            self.assertTrue(_vision_support("cc/claude-synthetic", {}))
            lookup.assert_called_with("anthropic", "claude-synthetic", {})
            self.assertTrue(_vision_support("cx/gpt-synthetic", {}))
            lookup.assert_called_with("openai", "gpt-synthetic", {})
            self.assertFalse(
                _vision_support("cc/claude-synthetic", {"model": {"supports_vision": False}})
            )

    async def test_big_brother_artifact_root_and_provider_failure_are_explicit(self):
        with TemporaryDirectory() as directory:
            profile = Path(directory)
            root = profile / "workspace"
            root.mkdir()
            Image.new("RGB", (10, 10)).save(profile / "rendered.png")
            agent = SimpleNamespace(
                model="cc/synthetic",
                session_id="big-brother",
                _xnobrain_image_artifact_roots=[profile],
            )
            with (
                patch("tools.terminal_tool.get_session_cwd", return_value=str(profile)),
                patch("xnobrain.integrations.image_tools._vision_support", return_value=True),
                patch(
                    "tools.vision_tools._vision_analyze_native",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("private upstream payload"),
                ),
            ):
                result = await inspect_image({"image_url": "rendered.png"}, (agent, root))
            self.assertEqual(json.loads(result)["code"], "image_inspection_failed")
            self.assertNotIn("private", result)
            agent._xnobrain_image_artifact_roots = []
            with patch("tools.terminal_tool.get_session_cwd", return_value=str(profile)):
                result = await inspect_image({"image_url": "rendered.png"}, (agent, root))
            self.assertEqual(json.loads(result)["code"], "invalid_chat_image_path")

    async def test_scoped_dispatch_and_cleanup(self):
        from tools.registry import registry

        from xnobrain.integrations import image_tools

        with TemporaryDirectory() as left, TemporaryDirectory() as right:
            a = SimpleNamespace(model="test", session_id="left")
            b = SimpleNamespace(model="test", session_id="right")
            with patch.object(
                image_tools, "inspect_image", new_callable=AsyncMock, return_value="{}"
            ) as inspect:
                with (
                    image_tool_scope(a, left, "left-task"),
                    image_tool_scope(b, right, "right-task"),
                ):
                    handler = registry.get_entry("vision_analyze").handler
                    await handler(
                        {"image_url": "page.png"}, session_id="right", task_id="right-task"
                    )
                    self.assertEqual(inspect.call_args.args[1], (b, Path(right)))
                    await handler({"image_url": "page.png"}, session_id="left", task_id="left-task")
                    self.assertEqual(inspect.call_args.args[1], (a, Path(left)))
                self.assertNotIn("left", image_tools._BINDINGS)
                self.assertNotIn("right", image_tools._BINDINGS)
