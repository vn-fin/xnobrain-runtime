// <Summary>
// Decision defines the edition-neutral quota result shared by HTTP middleware and service enforcement.
// </Summary>
package limits

import (
	"errors"
	"fmt"
	"time"
)

type Resource string

const (
	AgentsCreated       Resource = "agents_created"
	CronJobsCreated     Resource = "cron_jobs_created"
	CronParallelRuns    Resource = "cron_parallel_runs"
	CronRunsDaily       Resource = "cron_runs_daily"
	CronRunsMonthly     Resource = "cron_runs_monthly"
	ProviderConnections Resource = "provider_connections"
	TeamsCreated        Resource = "teams_created"
	TeamMembers         Resource = "team_members"
	DelegatedWorkers    Resource = "delegated_workers"
	DelegationDepth     Resource = "delegation_depth"
)

type Decision struct {
	Resource  Resource   `json:"resource"`
	Allowed   bool       `json:"allowed"`
	Limit     int        `json:"limit"`
	Used      int        `json:"used"`
	Remaining int        `json:"remaining"`
	ResetAt   *time.Time `json:"reset_at,omitempty"`
}

func Check(resource Resource, limit int, used int, requested int, resetAt *time.Time) Decision {
	if requested < 1 {
		requested = 1
	}
	decision := Decision{Resource: resource, Limit: limit, Used: used, ResetAt: resetAt}
	if limit < 0 {
		decision.Allowed = true
		decision.Remaining = -1
		return decision
	}
	decision.Remaining = max(0, limit-used)
	decision.Allowed = used+requested <= limit
	return decision
}

type ExceededError struct{ Decision Decision }

func (e *ExceededError) Error() string {
	return fmt.Sprintf("%s limit reached (%d/%d)", e.Decision.Resource, e.Decision.Used, e.Decision.Limit)
}

func Require(decision Decision) error {
	if decision.Allowed {
		return nil
	}
	return &ExceededError{Decision: decision}
}

func AsExceeded(err error) (*ExceededError, bool) {
	var target *ExceededError
	ok := errors.As(err, &target)
	return target, ok
}

func MonthWindow(now time.Time) (time.Time, time.Time) {
	now = now.UTC()
	start := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
	return start, start.AddDate(0, 1, 0)
}

func DayWindow(now time.Time) (time.Time, time.Time) {
	now = now.UTC()
	start := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	return start, start.AddDate(0, 0, 1)
}
