# 017 — Desktop plugins: server-side only

Status: decided 2026-11-13. The earlier version of this plan proposed a
desktop node (a process on the user's computer, paired with Control, that
relayed MCP requests to local servers). It was built and then reverted. This
file records why and what replaced it.

## Decision

XNOBrain is a web product whose agents run in a server workspace. A catalog
plugin must work from that workspace alone. We do not ask users to install and
keep running a helper program on their own computer.

| Catalog id | Outcome |
| --- | --- |
| `figma` | Installable. Pinned stdio recipe `figma-developer-mcp@0.13.2 --stdio --no-telemetry`, which reads files through the Figma REST API with a personal access token in `FIGMA_API_KEY`. Read-only. |
| `obsidian` | Removed from the catalog. The Local REST API only exists while the Obsidian app runs on the user's machine; Obsidian Sync has no API. |
| `paper` | Removed from the catalog. Its MCP server relays to Paper Desktop with an open file. |
| `apple-apps` | Removed from the catalog. Notes, Messages and Safari need a local Mac with Automation access. |

## Why not the desktop node

- It turns a web product into "install this program and leave it running",
  per user and per computer, which most users will not do.
- Tools disappear whenever the laptop sleeps or goes offline, while agents
  (scheduled runs, channels) keep working without the user.
- It adds a new trust boundary: a server workspace that can make a program on
  the user's computer run things. Pairing, device ownership, approval and kill
  switches all become permanent product surface.
- Control needs a cross-instance device registry before it can run more than
  one instance.
- The official Figma remote MCP (`mcp.figma.com/mcp`) accepts only allowlisted
  OAuth clients, so the desktop app was the only official Figma path; the REST
  API recipe avoids both.

OpenBot ships the same four plugins, but OpenBot is a local-first desktop app:
its agents run on the user's computer and call these servers directly. That
design does not transfer to a server-side workspace.

## Revisit when

- XNOBrain ships its own desktop app (plan 013, Full Managed App track) where
  agents can run on the user's computer. Local plugins then belong to that app,
  not to a relay through Control.
- Apple Calendar, Reminders or Contacts are requested: iCloud CalDAV/CardDAV
  with an app-specific password could serve them server-side as a separate
  catalog entry. No reviewed MCP package exists yet.
- Figma admits third-party OAuth clients to its remote MCP server.
