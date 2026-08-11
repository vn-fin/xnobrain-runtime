# OSS-00: public composition and extension contracts

Priority P0 and blocking cross-repository integration. Own public interfaces and contract tests; do not move business logic into public packages.

## Problem

The enterprise binary must inject policy, PostgreSQL repositories, quota ledger, device/cloud runtime adapters, and event sinks. A sibling module cannot safely build against unstable `internal` packages. The existing `pkg/edition.Policy` is public, but application wiring is not.

## Deliverable

Expose a narrow public builder, preferably `pkg/studio`, that accepts dependencies and returns the Fiber application plus lifecycle hooks. Public interfaces cover principal/plan policy, persistence capabilities, atomic quota reservation, runtime execution, event/audit sink, clock, and optional device connector. Default constructors assemble the Community implementation.

Do not export concrete internal services merely to make composition easy. Keep DTOs versioned and minimal. Use compile-time interface assertions and a compatibility test module that implements every interface as the enterprise repository will.

Configuration is divided into shared runtime config, Community defaults, and injected enterprise config. Secrets are never part of serializable public config. Lifecycle owns start, readiness, graceful stop, and cleanup without hidden goroutines.

## Acceptance criteria

- A tiny external test module builds an application without importing any `internal` package.
- Community `cmd/main.go` uses the same builder as enterprise composition.
- Missing required dependencies fail at construction with named errors.
- Start/stop tests detect leaked goroutines and double close.
- Public API changes require a documented compatibility/version decision.
