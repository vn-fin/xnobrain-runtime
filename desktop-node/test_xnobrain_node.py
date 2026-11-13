import base64
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import xnobrain_node as xn  # noqa: E402

TOKEN = "ab" * 32
FAKE = r"""
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        open(sys.argv[1], "a").write(m["method"] + "\n"); continue
    if m["method"] == "initialize":
        open(sys.argv[1], "a").write("init\n")
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {"echo": m["method"], "n": m["id"]}}), flush=True)
"""


def serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def cli(*args):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = xn.main(list(args))
    return code, out.getvalue() + err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patch = mock.patch.dict(
            os.environ, {"XNOBRAIN_NODE_CONFIG": f"{self.dir.name}/c/config.json"}
        )
        patch.start()
        self.addCleanup(patch.stop)


class ConfigTests(Base):
    def test_pair_writes_private_config_and_validates(self):
        self.assertEqual(cli("pair", "--url", "https://x.example", "--token", TOKEN)[0], 0)
        path = xn.config_path()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())["url"], "https://x.example")
        for url, token in (
            ("http://x.example", TOKEN),
            ("https://x.example/path", TOKEN),
            ("https://x.example", "short"),
        ):
            self.assertEqual(cli("pair", "--url", url, "--token", token)[0], 1)
        self.assertEqual(cli("pair", "--url", "http://localhost:5173", "--token", TOKEN)[0], 0)

    def test_enable_confirmation_and_obsidian_key(self):
        with mock.patch("builtins.input", return_value="n"):
            self.assertEqual(cli("enable", "figma")[0], 1)
        self.assertFalse(xn.load_config()["servers"])
        code, out = cli("enable", "obsidian", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("OBSIDIAN_API_KEY", out)
        code, out = cli("enable", "obsidian", "--yes", "--env", "OBSIDIAN_API_KEY=sekret")
        self.assertEqual(code, 0)
        self.assertNotIn("sekret", out)
        self.assertTrue(xn.load_config()["servers"]["obsidian"]["enabled"])
        with mock.patch("builtins.input", return_value="y"):
            self.assertEqual(cli("enable", "paper", "--command", "/bin/paper")[0], 0)
        cli("disable", "paper")
        self.assertFalse(xn.load_config()["servers"]["paper"]["enabled"])

    def test_apple_apps_refused_off_macos(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(cli("enable", "apple-apps", "--yes")[0], 1)

    def test_loopback_assertion(self):
        xn.check_loopback("http://127.0.0.1:3845/mcp")
        for bad in ("http://example.com/mcp", "file:///etc/passwd"):
            with self.assertRaises(xn.NodeError):
                xn.check_loopback(bad)
        for recipe in xn.RECIPES.values():
            if "url" in recipe:
                xn.check_loopback(recipe["url"])


class BridgeTests(Base):
    def make(self):
        script = Path(self.dir.name) / "fake.py"
        script.write_text(FAKE)
        self.marks = Path(self.dir.name) / "marks"
        bridge = xn.StdioServer(
            [sys.executable, str(script), str(self.marks)], {}, Path(self.dir.name) / "s.log"
        )
        self.addCleanup(bridge.stop)
        return bridge

    def test_remap_cached_initialize_and_notification(self):
        bridge = self.make()
        call = lambda msg: bridge.handle(json.dumps(msg).encode())  # noqa: E731
        status, body = call({"jsonrpc": "2.0", "id": "abc", "method": "initialize"})
        self.assertEqual(status, 200)
        first = json.loads(body)
        self.assertEqual(first["id"], "abc")
        self.assertEqual(first["result"]["echo"], "initialize")
        status, body = call({"jsonrpc": "2.0", "id": 99, "method": "initialize"})
        self.assertEqual(json.loads(body)["id"], 99)
        self.assertEqual(self.marks.read_text().count("init"), 1)  # replayed, not forwarded
        status, body = call({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        reply = json.loads(body)
        self.assertEqual((reply["id"], reply["result"]["echo"]), (7, "tools/list"))
        self.assertNotEqual(reply["result"]["n"], 7)  # node-side id
        self.assertEqual(
            call({"jsonrpc": "2.0", "method": "notifications/initialized"}), (202, b"")
        )
        status, body = call(
            [{"jsonrpc": "2.0", "id": 1, "method": "a"}, {"jsonrpc": "2.0", "id": 2, "method": "b"}]
        )
        self.assertEqual([r["id"] for r in json.loads(body)], [1, 2])


class UrlTests(Base):
    def test_forwarding_and_get(self):
        seen = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                seen["sid"] = self.headers.get("Mcp-Session-Id")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Mcp-Session-Id", "s1")
                self.end_headers()
                self.wfile.write(b"data: hi\n\n")

            def log_message(self, *a):
                pass

        server, base = serve(H)
        self.addCleanup(server.shutdown)
        cfg = {"servers": {"figma": {"enabled": True}}}
        node = xn.Node(cfg)
        with mock.patch.dict(xn.RECIPES["figma"], {"url": base + "/mcp"}):
            hdr = {"Content-Type": "application/json", "Mcp-Session-Id": "abc", "X-Evil": "1"}
            frame = {
                "server": "figma",
                "method": "POST",
                "headers": hdr,
                "body": base64.b64encode(b"{}").decode(),
            }
            reply = node.handle_frame(frame)
            self.assertEqual(reply["status"], 200)
            self.assertEqual(base64.b64decode(reply["body"]), b"data: hi\n\n")
            self.assertEqual(reply["headers"]["Mcp-Session-Id"], "s1")
            self.assertEqual((seen["body"], seen["sid"]), (b"{}", "abc"))
            self.assertEqual(node.handle_frame({**frame, "method": "GET"})["status"], 405)
        self.assertEqual(node.handle_frame({**frame, "server": "paper"})["status"], 404)
        self.assertEqual(node.handle_frame({**frame, "server": "nope"})["status"], 404)


class RunLoopTests(Base):
    def start(self, status_next):
        state = {"replies": [], "served": False}
        done = threading.Event()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                state["auth"] = self.headers.get("Authorization")
                state["path"] = self.path
                if status_next == 401:
                    self.send_response(401)
                    self.end_headers()
                elif not state["served"]:
                    state["served"] = True
                    body = json.dumps(
                        {
                            "v": 1,
                            "id": "r1",
                            "server": "figma",
                            "method": "POST",
                            "headers": {},
                            "body": "",
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(204)
                    self.end_headers()

            def do_POST(self):
                state["reply_path"] = self.path
                state["replies"].append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(204)
                self.end_headers()
                done.set()

            def log_message(self, *a):
                pass

        server, base = serve(H)
        self.addCleanup(server.shutdown)
        return state, done, {"url": base, "token": TOKEN, "servers": {"figma": {"enabled": True}}}

    def test_frame_round_trip(self):
        state, done, cfg = self.start(200)
        stop = threading.Event()
        thread = threading.Thread(target=xn.run, args=(cfg, stop))
        thread.start()
        self.assertTrue(done.wait(10))
        stop.set()
        thread.join(15)
        self.assertEqual(state["auth"], f"Bearer {TOKEN}")
        self.assertIn("servers=figma", state["path"])
        self.assertTrue(state["reply_path"].endswith("/relay/r1"))
        self.assertEqual(state["replies"][0]["v"], 1)

    def test_401_exits(self):
        _state, _done, cfg = self.start(401)
        err = StringIO()
        with redirect_stderr(err):
            self.assertEqual(xn.run(cfg), 1)
        self.assertIn("pair again", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())


if __name__ == "__main__":
    unittest.main()
