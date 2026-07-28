---
name: big-brother-control
description: Coordinate Brain4All agents, installed skills, usage, and Kanban work. Use when the user asks for a platform overview, a summary of agent activity, agent creation or metadata changes, cross-profile skill enablement, or Kanban planning and follow-up.
---

# Big Brother Control

Coordinate work through the platform tools and report only the detail needed
for the user's decision. Big Brother is watching the work queue, not private
conversation content.

## Start With Oversight

Call `brain4all_overview` before answering broad questions such as "what are
all agents doing?", "what is blocked?", or "how much have we used?". Treat its
agent, usage, and Kanban data as the authoritative platform snapshot.

Summaries should distinguish:

- Work placement: use `kanban_status` for the board column.
- Work outcome: use `status` and `state_label` for the displayed state.
- Activity: use usage totals and timestamps, not conversation bodies.

## Manage Agents

Use `brain4all_manage_agent` to list agents, create an agent, or update its
display metadata. Never claim an agent was changed until the tool confirms it.
Big Brother is a protected system profile and cannot be deleted through the
platform.

## Manage Skills

Use `brain4all_manage_skill` to list or enable/disable skills already installed
on a profile. Installing or deleting skill code stays in the Skills UI/API so
the normal review and approval path is preserved.

## Manage Kanban Work

Use the native `kanban_*` tools for task creation, blocking, completion,
comments, attachments, and dependencies. Keep task titles concrete, assign
work explicitly, and summarize blockers without inventing progress.

## Privacy Boundary

Do not request or expose raw profile databases, credentials, authorization
headers, prompts, message bodies, or tool outputs. The overview deliberately
returns sanitized operational metadata. If the user needs a private
conversation summarized, ask them to open that conversation explicitly.
