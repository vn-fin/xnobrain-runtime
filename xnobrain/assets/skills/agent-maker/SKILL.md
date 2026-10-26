---
name: agent-maker
description: Design a complete specialist agent through an interview and automatic blueprint acceptance. Use only when the user explicitly selects Agent maker or asks to make an agent.
---

# Agent maker

Use the user's requirements and ask only for missing details needed for creation: job, audience, inputs, outputs, boundaries, model/cost limits, tools, work context, and memory policy. Do not require acceptance tests or certification.

Produce a complete reviewable blueprint containing:

- SOUL and AGENTS instructions;
- authorized model slot and tools/MCP requirements;
- context-isolated memory policy and explicitly approved seed sources;
- pinned skills, references, scripts, and a workspace input/work/output layout.

When the user asks to create an agent, approval is automatic once requirements are complete. Use `agent_maker_inspect` for schemas and existing blueprints, `agent_maker_prepare` to save the complete specification, and `agent_maker_build` to accept the blueprint, scaffold the child, and activate it directly. Do not run certification, create test sessions, or ask for a manual approval click or a separate activation decision. These tools inherit the verified identity and work context of this chat; never pass identity headers or choose another owner.

To update an editable blueprint, call `agent_maker_prepare` with its `blueprint_id`
and the complete updated specification. Omit `expected_revision` to update the
current stored revision without inspecting it first. Supply `expected_revision`
only when an explicit revision precondition is wanted. An identical retry returns
the saved blueprint without advancing its revision again. For `agent_maker_build`,
only `blueprint_id` and `idempotency_key` are required; revision and digest are
optional preconditions. Do not supply test cases or a certification budget.

Call `agent_maker_inspect` without a blueprint ID to get `model_catalog`. Select
`model_slot.alias` from its exact `data[].id` values, preserving the full provider
prefix (for example `cc/…` or `cx/…`). Do not use a remembered model name, strip
the prefix, or substitute a CLI default. If the catalog is unavailable or empty,
report the blocker and retry inspection; do not guess a model. If preparation or
build reports `agent_maker_model_unavailable`, inspect again and prepare an updated
blueprint with an available model before building.

Report the resulting blueprint ID, target profile ID and actual status. Reuse the same key after a transport failure or unknown result. An existing scaffold or failed certification can continue to activation on the same agent without another certification attempt. Creation and activation do not establish that the agent has been tested.

Scheduling, marketplace publication, copying credentials/history/private memory, and changing protected-write approvals remain separate actions.

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
state and the missing evidence separately. Do not claim activation before it
succeeds, or claim certification was run.

If approval or scaffold returns `trusted_subject_required`, 401, or 403, report
the blocked lifecycle step. Do not substitute `agent profile create`, clone the
creator profile, copy credentials, or manufacture trusted identity headers.
