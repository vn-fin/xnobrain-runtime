# Conversation ownership context v1

Runtime persists exactly one immutable ownership context for every conversation.
The context is additive on conversation create, list, detail, goal/subgoal, and
run responses. Runtime-owned sidecar records are stored atomically below the agent
profile at `.xnobrain/conversation-contexts/`; owner and payer have no update API.

```json
{
  "schema_version": 1,
  "id": "cctx_example",
  "owner_kind": "organization",
  "organization_id": "org_example",
  "payer_kind": "organization_sponsor",
  "sponsor_grant_id": "sgr_example",
  "membership_revision_at_create": 8,
  "policy_revision_at_create": 3,
  "state": "active"
}
```

`owner_kind` is `personal` or `organization`. Personal ownership requires
`payer_kind=personal` and rejects organization/sponsor fields. Organization
ownership requires `organization_id`; `organization_sponsor` requires a sponsor
grant. An organization-owned conversation may instead use the personal payer.
Owner, organization, payer, sponsor grant, and creation revisions are immutable.
A future trusted authority may change only lifecycle `state` without converting
ownership.

Omitting `ownership_context` on create preserves legacy personal behavior and
writes an explicit Personal binding. Existing Hermes conversations without a
sidecar are durably backfilled as Personal on first list/detail/run/goal access;
Runtime never infers organization ownership from content. The read response omits
private actor/workspace fields.

Non-Personal create is accepted only through the authenticated private facade.
The Runtime gRPC relay derives `x-xnobrain-verified-subject`, tenant, and
organization headers from `VerifiedPrincipal` and authenticates that assertion
with `x-xnobrain-principal-signature`, an HMAC-SHA256 using the existing private
`RUNTIME_INTERNAL_SERVICE_TOKEN`. An ownership snapshot is separately carried as
canonical base64url JSON in `x-xnobrain-verified-conversation-context`, bound to the
principal by `x-xnobrain-conversation-context-signature`. Runtime accepts a body
snapshot only when it exactly equals those signed Control claims. The relay discards
caller copies of all trusted headers. Direct HTTP callers cannot create organization
bindings by supplying those header names. Runtime also requires the claimed
organization to equal the verified principal organization. Control remains
responsible for membership, context ID, payer/grant, and revision authorization
before relaying creation.

Run requests identify the conversation and input only. Runtime loads the persisted
binding and records it on the durable run and `run.started` event. Caller-supplied
`ownership_context`, `work_context_id`, owner, organization, payer, or sponsor
grant fields are rejected before budget/provider dispatch: differing values return
`409 conversation_context_conflict`; even matching duplicates return
`400 conversation_context_not_accepted`. Typed public `ChatRequest` forbids these
extra fields with `422`.
