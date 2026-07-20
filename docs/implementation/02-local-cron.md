# OSS-02: local cron, quotas, and missed-run recovery

Priority P1 after quota contract fields are frozen. Own `services/crons`, cron persistence helpers, local notifications, and focused tests. Integrate routes only through the integration owner.

## Plan limits

Free target: four definitions, one concurrent execution, ten actual runs per UTC day, two hundred actual runs per UTC month. Pro defaults: fifty definitions, five concurrent, 250/day, 5,000/month. Enterprise is policy-defined; numeric `-1` means unlimited and `0` means unavailable.

Extend `pkg/edition.Limits` with `CronRunsPerDay`. Add a `CronRunsDaily` resource, UTC day window, middleware decision, status output, environment migration, and tests. Change the shipped Free monthly default from 100 to 200 in the same change; never land documentation/config/code with different values.

## Execution transaction

Before Hermes starts, atomically reserve daily and monthly counters as one operation, then acquire concurrency. If either quota fails, neither counter changes. If concurrency cannot start a manual request, return `429`; scheduled work remains queued without reservation. Define whether an execution attempt is committed at Hermes start or released for pre-start infrastructure failure, and test it.

Local file counters need one lock covering both periods. Enterprise PostgreSQL uses a transaction. The shared service depends on a quota interface and does not know the storage backend.

## Scheduling modes

Every job has `mode=local|managed`. The local ticker evaluates only local jobs. Managed jobs execute only from a valid device command, preventing double scheduling when cloud-connected.

Store an IANA timezone plus canonical schedule. Persist occurrence times in UTC and calculate daylight-saving behavior from the named timezone. A job has `misfire_policy=skip|notify|run_latest|ask|replay_bounded`; default `notify`. Bounded replay has an explicit positive maximum.

On startup, compare `last_evaluated_at` with now, aggregate missed occurrences, and write one notification per reconnect/startup batch. Do not create hundreds of notification files. Missed occurrences consume no run quota. `run_latest` or a user-approved replay reserves quota only when dispatched.

## Notification contract

A notification records offline range, total missed, per-job count/first/last occurrence, allowed actions, created time, and resolution. Actions are dismiss, open jobs, run latest per job, or bounded replay. Resolution is idempotent.

## Acceptance criteria

- Parallel tests prove daily and monthly totals never exceed policy.
- UTC boundary, month boundary, leap day, timezone, and DST tests use a fake clock.
- Restart recovery produces a compact notification and no automatic replay by default.
- Local and managed copies of one job cannot both execute.
- A duplicate managed command returns the first receipt without another Hermes call.
- Disabled, paused, expired, and deleted jobs never reserve quota.
