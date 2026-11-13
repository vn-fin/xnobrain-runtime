#!/usr/bin/env python3
"""XNOBrain desktop node: relays MCP requests from Control to local servers.

Stdlib only, Python 3.10+. Commands: pair, enable, disable, status, run.
"""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

API = "/xnobrain/api/control/v1/devices/relay"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
BODY_CAP = 8 * 1024 * 1024
REPLY_WAIT = 110
FORWARD_HEADERS = ("Content-Type", "Accept", "Mcp-Session-Id", "Mcp-Protocol-Version")

RECIPES = {
    "figma": {"url": "http://127.0.0.1:3845/mcp"},
    "paper": {"argv": ["paper", "mcp"], "env": []},
    "obsidian": {
        "argv": ["npx", "-y", "obsidian-mcp-server@3.7.2"],
        "env": ["OBSIDIAN_API_KEY"],
        "optional_env": ["OBSIDIAN_BASE_URL"],
    },
    "apple-apps": {"argv": ["npx", "-y", "apple-mcp@1.0.0"], "env": []},
}


class NodeError(Exception):
    pass


def config_path() -> Path:
    override = os.environ.get("XNOBRAIN_NODE_CONFIG")
    return Path(override) if override else Path.home() / ".config/xnobrain-node/config.json"


def load_config() -> dict:
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"url": "", "token": "", "servers": {}}
    except (OSError, ValueError) as error:
        raise NodeError(f"config unreadable: {error.__class__.__name__}") from error
    cfg.setdefault("servers", {})
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def validate_origin(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    dev = host in LOOPBACK or host.endswith(".localhost") or host == "app.dev.xnobrain"
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise NodeError("url must be an origin such as https://app.example.com")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise NodeError("url must be an origin without path or query")
    if parts.scheme == "http" and not dev:
        raise NodeError("plain http is only allowed for local development hosts")
    return f"{parts.scheme}://{parts.netloc}"


def check_loopback(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in LOOPBACK:
        raise NodeError("recipe URL must be loopback")


def recipe_argv(server_id: str, entry: dict) -> list[str]:
    argv = list(RECIPES[server_id]["argv"])
    if entry.get("command"):
        argv[0] = entry["command"]
    return argv


# ---------------------------------------------------------------- stdio bridge


class StdioServer:
    """One lazily started subprocess speaking newline-delimited JSON-RPC."""

    def __init__(self, argv: list[str], env: dict, log_path: Path):
        self.argv, self.env, self.log_path = argv, env, log_path
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()  # guards start, writes and the pending map
        self.init_lock = threading.Lock()
        self.ids = itertools.count(1)
        self.pending: dict[int, dict] = {}
        self.init_result: dict | None = None

    def _ensure(self) -> subprocess.Popen:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.init_result = None
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "ab") as log:
                    self.proc = subprocess.Popen(
                        self.argv,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=log,
                        env={**os.environ, **self.env},
                    )
                threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
            return self.proc

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            # shortcut: sampling/elicitation from local servers is dropped, forward over the relay if a recipe needs it
            if not isinstance(msg, dict) or "method" in msg:
                continue
            with self.lock:
                slot = self.pending.pop(msg.get("id"), None)
            if slot:
                slot["msg"] = msg
                slot["event"].set()
        with self.lock:  # process ended: fail whoever is waiting
            waiting, self.pending = list(self.pending.values()), {}
        for slot in waiting:
            slot["event"].set()

    def _send(self, msg: dict) -> None:
        proc = self._ensure()
        try:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            proc.stdin.flush()
        except OSError as error:
            raise NodeError("server process is not running") from error

    def _request(self, msg: dict) -> dict:
        """Send one request with a node-side id; return the reply under the caller's id."""
        node_id = next(self.ids)
        slot = {"event": threading.Event(), "msg": None}
        with self.lock:
            self.pending[node_id] = slot
        try:
            self._send({**msg, "id": node_id})
        except NodeError:
            with self.lock:
                self.pending.pop(node_id, None)
            raise
        if not slot["event"].wait(REPLY_WAIT):
            with self.lock:
                self.pending.pop(node_id, None)
            raise TimeoutError
        if slot["msg"] is None:
            raise NodeError("server process exited")
        return {**slot["msg"], "id": msg["id"]}

    def _one(self, msg: dict):
        if "method" not in msg:  # a client reply to a server request: dropped (see _read)
            return None
        if "id" not in msg:
            self._send(msg)
            return None
        if msg["method"] != "initialize":
            return self._request(msg)
        with self.init_lock:
            self._ensure()
            if self.init_result is not None:
                return {"jsonrpc": "2.0", "id": msg["id"], "result": self.init_result}
            reply = self._request(msg)
            if "result" in reply:
                self.init_result = reply["result"]
            return reply

    def handle(self, body: bytes) -> tuple[int, bytes]:
        try:
            payload = json.loads(body)
        except ValueError:
            return 400, b""
        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        if not all(isinstance(m, dict) for m in messages):
            return 400, b""
        try:
            replies = [r for r in (self._one(m) for m in messages) if r is not None]
        except TimeoutError:
            return 504, b""
        except NodeError:
            return 502, b""
        if not replies:
            return 202, b""
        return 200, json.dumps(replies if batch else replies[0]).encode()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# ---------------------------------------------------------------- frame handling


def _reply(status: int, body: bytes = b"", content_type: str = "", session: str = "") -> dict:
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    if session:
        headers["Mcp-Session-Id"] = session
    return {"v": 1, "status": status, "headers": headers, "body": base64.b64encode(body).decode()}


class Node:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.bridges: dict[str, StdioServer] = {}
        self.lock = threading.Lock()

    def enabled_ids(self) -> list[str]:
        return [i for i, s in self.cfg["servers"].items() if i in RECIPES and s.get("enabled")]

    def _bridge(self, server_id: str) -> StdioServer:
        with self.lock:
            if server_id not in self.bridges:
                entry = self.cfg["servers"][server_id]
                self.bridges[server_id] = StdioServer(
                    recipe_argv(server_id, entry),
                    dict(entry.get("env") or {}),
                    config_path().parent / f"{server_id}.log",
                )
            return self.bridges[server_id]

    def handle_frame(self, frame: dict) -> dict:
        server_id = frame.get("server")
        entry = self.cfg["servers"].get(server_id) if isinstance(server_id, str) else None
        if server_id not in RECIPES or not entry or not entry.get("enabled"):
            return _reply(404)
        try:
            body = base64.b64decode(frame.get("body") or "")
        except ValueError:
            return _reply(400)
        method = str(frame.get("method") or "POST").upper()
        if method == "GET":
            return _reply(405)
        if "url" in RECIPES[server_id]:
            return self._forward(
                RECIPES[server_id]["url"], method, frame.get("headers") or {}, body
            )
        status, out = self._bridge(server_id).handle(body)
        return _reply(status, out, "application/json" if out else "")

    @staticmethod
    def _forward(url: str, method: str, headers: dict, body: bytes) -> dict:
        check_loopback(url)
        wanted = {k: v for k, v in headers.items() if k in FORWARD_HEADERS and isinstance(v, str)}
        request = urllib.request.Request(url, data=body or None, method=method, headers=wanted)
        try:
            resp = urllib.request.urlopen(request, timeout=REPLY_WAIT)  # noqa: S310 loopback checked
        except urllib.error.HTTPError as error:
            resp = error
        except (urllib.error.URLError, OSError):
            return _reply(502)
        with resp:
            data = resp.read(BODY_CAP + 1)  # SSE bodies end when the server closes them
            if len(data) > BODY_CAP:
                return _reply(502)
            return _reply(
                resp.status if hasattr(resp, "status") else resp.code,
                data,
                resp.headers.get("Content-Type", ""),
                resp.headers.get("Mcp-Session-Id", ""),
            )

    def close(self) -> None:
        for bridge in self.bridges.values():
            bridge.stop()


# ---------------------------------------------------------------- run loop


def _call(cfg: dict, method: str, path: str, data: bytes | None = None, timeout: float = 35):
    request = urllib.request.Request(
        cfg["url"] + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {cfg['token']}",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 scheme checked at pair


def _answer(node: Node, frame: dict) -> None:
    try:
        reply = node.handle_frame(frame)
    except Exception:  # never let one frame kill the loop
        reply = _reply(502)
    try:
        with _call(
            node.cfg,
            "POST",
            f"{API}/{quote(str(frame.get('id')), safe='')}",
            json.dumps(reply).encode(),
        ):
            pass
    except (urllib.error.URLError, OSError):
        print("could not deliver a reply", file=sys.stderr)


def run(cfg: dict, stop: threading.Event | None = None) -> int:
    stop = stop or threading.Event()
    if not cfg.get("url") or not cfg.get("token"):
        raise NodeError("not paired; run pair first")
    node = Node(cfg)
    delay = 1.0
    try:
        while not stop.is_set():
            query = ",".join(node.enabled_ids())
            try:
                with _call(cfg, "GET", f"{API}/next?servers={quote(query, safe=',-')}") as resp:
                    delay = 1.0
                    if resp.status != 200:
                        continue
                    frame = json.loads(resp.read(BODY_CAP * 2))
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    print("device token rejected, pair again", file=sys.stderr)
                    return 1
                stop.wait(delay)
                delay = min(delay * 2, 30)
                continue
            except (urllib.error.URLError, OSError, ValueError):
                stop.wait(delay)
                delay = min(delay * 2, 30)
                continue
            threading.Thread(target=_answer, args=(node, frame), daemon=True).start()
    finally:
        node.close()
    return 0


# ---------------------------------------------------------------- commands


def cmd_pair(args) -> int:
    if not re.fullmatch(r"[0-9a-f]{64}", args.token):
        raise NodeError("token must be 64 hex characters")
    cfg = load_config()
    cfg.update(url=validate_origin(args.url), token=args.token)
    save_config(cfg)
    print("paired")
    return 0


def cmd_enable(args) -> int:
    recipe = RECIPES.get(args.id)
    if recipe is None:
        raise NodeError(f"unknown server; choose from {', '.join(RECIPES)}")
    if args.id == "apple-apps" and sys.platform != "darwin":
        raise NodeError("apple-apps requires macOS")
    env = {}
    for item in args.env or []:
        key, sep, value = item.partition("=")
        if not sep or key not in [*recipe.get("env", []), *recipe.get("optional_env", [])]:
            raise NodeError(f"unsupported env entry {key!r} for {args.id}")
        env[key] = value
    missing = [k for k in recipe.get("env", []) if not env.get(k)]
    if missing:
        raise NodeError(f"{args.id} requires {', '.join(missing)} (--env {missing[0]}=...)")
    entry = {"enabled": True, "env": env, "command": args.command or ""}
    if "url" in recipe:
        print(f"{args.id}: forwards to {recipe['url']}")
    else:
        print(f"{args.id}: runs {' '.join(recipe_argv(args.id, entry))}")
    if env:
        print(f"environment: {', '.join(env)}")
    if not args.yes and input("Enable? [y/N] ").strip().lower() not in ("y", "yes"):
        print("cancelled")
        return 1
    cfg = load_config()
    cfg["servers"][args.id] = entry
    save_config(cfg)
    print("enabled")
    return 0


def cmd_disable(args) -> int:
    cfg = load_config()
    if args.id in cfg["servers"]:
        cfg["servers"][args.id]["enabled"] = False
        save_config(cfg)
    print("disabled")
    return 0


def cmd_status(_args) -> int:
    cfg = load_config()
    print(f"paired: {'yes, ' + cfg['url'] if cfg.get('token') else 'no'}")
    for server_id in RECIPES:
        entry = cfg["servers"].get(server_id, {})
        print(f"{server_id}: {'enabled' if entry.get('enabled') else 'disabled'}")
    return 0


def cmd_run(_args) -> int:
    return run(load_config())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xnobrain_node")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pair = sub.add_parser("pair")
    pair.add_argument("--url", required=True)
    pair.add_argument("--token", required=True)
    enable = sub.add_parser("enable")
    enable.add_argument("id")
    enable.add_argument("--env", action="append", metavar="KEY=VALUE")
    enable.add_argument("--command")
    enable.add_argument("--yes", action="store_true")
    sub.add_parser("disable").add_argument("id")
    sub.add_parser("status")
    sub.add_parser("run")
    args = parser.parse_args(argv)
    handler = {
        "pair": cmd_pair,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "status": cmd_status,
        "run": cmd_run,
    }[args.cmd]
    try:
        return handler(args)
    except NodeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
