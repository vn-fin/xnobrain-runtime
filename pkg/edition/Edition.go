// <Summary>
// Package edition defines the public open-source and enterprise policy boundary.
// File: Edition.go
// Types: Policy, Limits, Principal, OpenSource
// </Summary>
package edition

import "context"

const Unlimited = -1

type Principal struct {
	UserID   string
	TenantID string
	PlanID   string
	Roles    []string
}

type Limits struct {
	Sandboxes                  int   `json:"sandboxes"`
	Agents                     int   `json:"agents"`
	CronJobs                   int   `json:"cron_jobs"`
	CronParallelRuns           int   `json:"cron_parallel_runs"`
	CronRunsPerDay             int   `json:"cron_runs_per_day"`
	CronRunsPerMonth           int   `json:"cron_runs_per_month"`
	ProviderConnectionsPerType int   `json:"provider_connections_per_type"`
	ContainerHardwareUpgrades  bool  `json:"container_hardware_upgrades"`
	ManagedTelemetry           bool  `json:"managed_telemetry"`
	Teams                      int   `json:"teams"`
	AgentsPerTeam              int   `json:"agents_per_team"`
	DelegatedWorkers           int   `json:"delegated_workers"`
	DelegationDepth            int   `json:"delegation_depth"`
	ImportBytes                int64 `json:"import_bytes"`
}

type Policy interface {
	Name() string
	Principal(ctx context.Context, authorization string) (Principal, error)
	Limits(ctx context.Context, principal Principal) (Limits, error)
}

type OpenSource struct {
	AgentLimit                 int
	CronJobLimit               int
	CronConcurrency            int
	CronRunsPerDay             int
	CronRunsPerMonth           int
	ProviderConnectionsPerType int
	TeamLimit                  int
	AgentsPerTeam              int
	DelegatedWorkers           int
	DelegationDepth            int
}

func (o OpenSource) Name() string { return "opensource" }
func (o OpenSource) Principal(_ context.Context, _ string) (Principal, error) {
	return Principal{UserID: "local", TenantID: "local", PlanID: "opensource", Roles: []string{"owner"}}, nil
}
func (o OpenSource) Limits(_ context.Context, _ Principal) (Limits, error) {
	return Limits{
		Sandboxes:                  1,
		Agents:                     o.AgentLimit,
		CronJobs:                   o.CronJobLimit,
		CronParallelRuns:           o.CronConcurrency,
		CronRunsPerDay:             o.CronRunsPerDay,
		CronRunsPerMonth:           o.CronRunsPerMonth,
		ProviderConnectionsPerType: o.ProviderConnectionsPerType,
		ContainerHardwareUpgrades:  false,
		ManagedTelemetry:           false,
		Teams:                      o.TeamLimit,
		AgentsPerTeam:              o.AgentsPerTeam,
		DelegatedWorkers:           o.DelegatedWorkers,
		DelegationDepth:            o.DelegationDepth,
		ImportBytes:                -1,
	}, nil
}
