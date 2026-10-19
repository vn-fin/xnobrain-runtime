# Private checkpoint response compatibility

The existing `/xnobrain/api/runtime/v1/system/update/checkpoint` and
`/xnobrain/api/runtime/v1/system/update/v2/checkpoint` paths retain their request
models, service-token authentication, maintenance fencing, and JSON envelope.
Their `data` is now a receipt:

```json
{
  "checkpoint_id": "ckp_<sha256>",
  "operation_id": "upd_<operation>",
  "generation": 3,
  "created_at": "2026-10-19T00:00:00Z",
  "manifest": {
    "version": 2,
    "algorithm": "sha256",
    "digest": "<digest>",
    "file_count": 2000,
    "total_bytes": 1000000
  }
}
```

Summary values come from the saved evidence; legacy optional fields are omitted
when absent. File entries, SQLite paths, target, and recovery mode remain in the
full durable checkpoint. HTTP projection does not rewrite checkpoints or
operation journals, including on replay.

Control's two maintained update adapters require only `checkpoint_id` from this
response. Verification and restore proof remain Runtime operations that load
the original manifest locally. A receipt alone is not proof of preservation.

For mixed-version rollout, gateway accepts up to 64 MiB for the two checkpoint
responses, aligned with Runtime's existing journal-read ceiling. Other update
responses retain 256 KiB. It reads at most the limit plus one byte and reports
`runtime_update_response_too_large` rather than decoding a truncated prefix.
Malformed, truncated, trailing, or incompatible JSON reports
`runtime_update_response_invalid`; neither error includes response contents.

Deploy the compatible gateway first. Updating Runtime alone cannot repair a
rollout that checkpoints on an older Runtime before source installation. Retry
or recovery of previously blocked operations remains an explicit operational
action; this change does not clear maintenance, erase evidence, or restart a
deployment automatically.
