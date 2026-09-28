---
name: agent-maker
description: Design a complete specialist agent through an interview and automatic blueprint acceptance. Use only when the user explicitly selects Agent maker or asks to make an agent.
---

# Agent maker

Interview before creation. Establish the job, audience, inputs, outputs, boundaries, model/cost limits, tools, work context, memory policy, and acceptance tests.

Produce a complete reviewable blueprint containing:

- SOUL and AGENTS instructions;
- authorized model slot and tools/MCP requirements;
- context-isolated memory policy and explicitly approved seed sources;
- pinned skills, references, scripts, and a workspace input/work/output layout;
- synthetic certification cases, including refusal of unauthorized cross-context access.

When the user asks to create an agent, approval is automatic once requirements are complete. Use `agent_maker_inspect` for schemas and existing blueprints, `agent_maker_prepare` to save the complete specification, and `agent_maker_build` to accept the exact revision, scaffold a paused child, run its synthetic certification cases, and activate only on success. Do not ask for a manual approval click or a separate activation decision. These tools inherit the verified identity and work context of this chat; never pass identity headers or choose another owner.

Report the resulting blueprint ID, target profile ID and actual status. If certification fails, stop and report the failed cases; retry on the same agent only when requested, with a new certification key. Reuse the same key after a transport failure or unknown result. Each test uses a separate chat session in the same child agent. Do not create a new agent for each test.

Use synthetic data and the agreed budget; automatic certification is capped at USD 1 per attempt (default USD 0.10). Scheduling, marketplace publication, copying credentials/history/private memory, and changing protected-write approvals remain separate actions.

## Runtime destination and completion

Use `agent_maker_build` for the Runtime blueprint lifecycle and its
server-reserved target ID. Let Runtime select the destination from its resolved
profiles root (`HERMES_PROFILES_ROOT`); do not ask the user to choose a directory
already determined by the platform, hardcode a deployment path, or place the
child under the creator's working directory.

Verify that Runtime inventory and agent detail both return the scaffolded ID
using an available scoped tool or authenticated client. A native CLI listing,
shell alias, `--help` output, or messaging gateway status is not proof of Runtime
visibility or activation. If verification is unavailable, report the scaffold
state and the missing evidence separately. Do not claim chat readiness before
child certification and activation succeed.

If approval or scaffold returns `trusted_subject_required`, 401, or 403, report
the blocked lifecycle step. Do not substitute `agent profile create`, clone the
creator profile, copy credentials, or manufacture trusted identity headers.
