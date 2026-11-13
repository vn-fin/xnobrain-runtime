# XNOBrain desktop node

Runs MCP servers on your own computer and relays requests from XNOBrain to
them. One stdlib-only Python 3.10+ file; no install step.

Prerequisites

- Node.js 24 or newer for `obsidian` and `apple-apps`.
- `figma`: the Figma desktop MCP server enabled (listens on `127.0.0.1:3845`).
- `obsidian`: the Local REST API plugin with the non-encrypted HTTP server on;
  pass its key as `OBSIDIAN_API_KEY`.
- `apple-apps`: macOS only.
- `paper`: the `paper` command on your PATH (or pass `--command`).

Usage

```
python3 xnobrain_node.py pair --url https://your-xnobrain.example --token <64 hex from Settings > Devices>
python3 xnobrain_node.py enable figma
python3 xnobrain_node.py enable obsidian --env OBSIDIAN_API_KEY=... [--env OBSIDIAN_BASE_URL=...]
python3 xnobrain_node.py status
python3 xnobrain_node.py run      # keep running while you use the integrations
python3 xnobrain_node.py disable figma
```

`enable` shows what will run and asks for confirmation (`--yes` skips it). The
config lives in `~/.config/xnobrain-node/config.json` (mode 0600) and keeps the
device token and local keys; the node never prints them. Stdio server logs go
to `~/.config/xnobrain-node/<id>.log`.

Tests: `python3 -m unittest test_xnobrain_node.py`
