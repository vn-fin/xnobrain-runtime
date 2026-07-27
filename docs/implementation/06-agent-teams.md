# OSS-06: agent teams and delegation policy

Priority P2 after profiles, quotas, and command execution stabilize. Own `services/teams` and delegation adapters. Do not create a second agent runtime.

## Product model

A saved team contains one orchestrator profile, member profile IDs, member role labels, allowed toolsets, shared-workspace policy, and execution policy. Free target: one team, two agents per team, one delegated worker, flat depth. Pro: ten teams, ten agents per team, five delegated workers, maximum depth two. Enterprise is policy-defined with a safety ceiling.

Teams use Hermes delegation for transient child work. Mixture of Agents is a separate multi-model capability and has its own usage policy. Team concurrency and MoA calls must not be conflated.

Core safety is available to every plan: restricted child toolsets, no child clarification, bounded timeout, cancellation propagation, no shared memory writes from leaf workers, and final-summary-only context return. Enterprise adds organization ownership, shared workspace RBAC, approved templates, provider policy, audit, and budget allocation.

## Limits and execution

Validate saved-team count and members on mutation. At run time reserve team concurrency and cloud execution usage before delegation. Self-hosted Free uses local safety counters; cloud uses the enterprise ledger. Nested orchestration requires explicit policy and cycle detection.

The current Phase 1 multi-agent execution contract follows Hermes issue #344:
flat team runs are parallel convoy legs followed by coordinator synthesis, and
structured runs accept a DAG of named steps. Ready steps run concurrently on
different profiles, repeated use of one profile is serialized, dependency
summaries are injected into downstream steps, and cyclic or out-of-team graphs
are rejected before execution.

Saved workflow stages may either inherit the assigned profile's normal Hermes
tool configuration or select an explicit safe toolset allowlist. Stages may
also preload enabled skills from that profile. The communication policy follows
the issue #344 levels: L0 schedules isolated stages, L1 passes dependency
summaries, L2 adds a durable per-run shared scratchpad, and L3 adds a bounded
turn-based review/revision exchange between directly connected stages. The
visual editor must support arbitrary acyclic node dependencies, not only edges
from the Start node.

## Acceptance criteria

- A team cannot reference an agent outside its owner/tenant.
- Parallel child count and depth never exceed policy under races.
- Parent cancellation stops children and releases reservations.
- DAG execution detects cycles and passes upstream results to dependent steps.
- Leaf workers cannot mutate shared memory or bypass assigned toolsets.
- Team export/import remaps every member consistently and disables missing members with a diagnostic.
