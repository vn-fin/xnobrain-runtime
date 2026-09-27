---
name: agent-maker
description: Design a complete specialist agent through an interview and explicit blueprint review. Use only when the user explicitly selects Agent maker or asks to make an agent.
---

# Agent maker

Interview before creation. Establish the job, audience, inputs, outputs, boundaries, model/cost limits, tools, work context, memory policy, and acceptance tests.

Produce a complete reviewable blueprint containing:

- SOUL and AGENTS instructions;
- authorized model slot and tools/MCP requirements;
- context-isolated memory policy and explicitly approved seed sources;
- pinned skills, references, scripts, and a workspace input/work/output layout;
- synthetic certification cases, including refusal of unauthorized cross-context access.

Do not scaffold, activate, schedule, publish, copy credentials/history/private memory, or disable protected-instruction approvals merely because the blueprint was shown. Ask the human to approve the immutable blueprint and permission diff first. A new child must begin paused, certify through its own execution path, and require a separate activation decision. Marketplace publication is always separate.

## Runtime destination and completion

After approval, use the Runtime blueprint scaffold operation and its
server-reserved target ID. Let Runtime select the destination from its resolved
profiles root (`HERMES_PROFILES_ROOT`); do not ask the user to choose a directory
already determined by the platform, hardcode a deployment path, or place the
child under the creator's working directory.

Verify that Runtime inventory and agent detail both return the scaffolded ID
using an available scoped tool or authenticated client. A native CLI listing,
shell alias, `--help` output, or messaging gateway status is not proof of Runtime
visibility or activation. If verification is unavailable, report the scaffold
state and the missing evidence separately. Do not claim chat readiness before
child certification and the separate activation decision succeed.

If approval or scaffold returns `trusted_subject_required`, 401, or 403, report
the blocked lifecycle step. Do not substitute `agent profile create`, clone the
creator profile, copy credentials, or manufacture trusted identity headers.
