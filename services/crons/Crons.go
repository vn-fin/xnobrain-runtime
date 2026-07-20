// <Summary>
// Package crons owns cron CRUD, scheduling, and open-source concurrency enforcement.
// File: Crons.go
// Functions:
//   - Cron CRUD and single-concurrency open-source scheduler
//
// </Summary>
package crons

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/rs/zerolog/log"
	"github.com/xno/open-lumora/internal/edition"
	"github.com/xno/open-lumora/internal/identity"
	"github.com/xno/open-lumora/internal/limits"
	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/repositories"
	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	"github.com/xno/open-lumora/internal/studio/contract"
	safetracing "github.com/xno/open-lumora/internal/tracing"
	"github.com/xno/open-lumora/services/agents"
	"github.com/xno/open-lumora/services/conversations"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
)

type Crons struct {
	repository    repositories.Repository
	agents        *agents.Agents
	conversations *conversations.Conversations
	policy        edition.Policy
	running       sync.Map
	concurrency   *Concurrency
	resolutionMu  sync.Mutex
	clock         contract.Clock
}

type systemClock struct{}

func (systemClock) Now() time.Time { return time.Now().UTC() }

func NewCrons(repository repositories.Repository, agentService *agents.Agents, conversationService *conversations.Conversations, policy edition.Policy, clocks ...contract.Clock) *Crons {
	clock := contract.Clock(systemClock{})
	if len(clocks) > 0 && clocks[0] != nil {
		clock = clocks[0]
	}
	return &Crons{repository: repository, agents: agentService, conversations: conversationService, policy: policy, concurrency: NewConcurrency(), clock: clock}
}

func (s *Crons) List(ctx context.Context, userID string) ([]models.CronJob, error) {
	return s.repository.ListCrons(ctx, userID)
}

func (s *Crons) Create(ctx context.Context, userID string, agentID string, name string, prompt string, intervalMinutes int) (models.CronJob, error) {
	if intervalMinutes < 1 {
		return models.CronJob{}, fmt.Errorf("interval_minutes must be positive")
	}
	return s.CreateScheduled(ctx, userID, agentID, name, prompt, "@every "+strconv.Itoa(intervalMinutes)+"m", "Etc/UTC", "local", "notify", 0)
}

func (s *Crons) CreateScheduled(ctx context.Context, userID string, agentID string, name string, prompt string, schedule string, timezone string, mode string, misfirePolicy string, replayLimit int) (models.CronJob, error) {
	if err := s.agents.EnsureOwned(ctx, userID, agentID); err != nil {
		return models.CronJob{}, err
	}
	if strings.TrimSpace(prompt) == "" {
		return models.CronJob{}, fmt.Errorf("prompt is required")
	}
	if timezone = strings.TrimSpace(timezone); timezone == "" {
		timezone = "Etc/UTC"
	}
	canonical, _, err := canonicalSchedule(schedule, timezone)
	if err != nil {
		return models.CronJob{}, err
	}
	if mode = strings.TrimSpace(mode); mode == "" {
		mode = "local"
	}
	if mode != "local" && mode != "managed" {
		return models.CronJob{}, fmt.Errorf("mode must be local or managed")
	}
	if misfirePolicy = strings.TrimSpace(misfirePolicy); misfirePolicy == "" {
		misfirePolicy = "notify"
	}
	if !map[string]bool{"skip": true, "notify": true, "run_latest": true, "ask": true, "replay_bounded": true}[misfirePolicy] {
		return models.CronJob{}, fmt.Errorf("unsupported misfire policy")
	}
	if misfirePolicy == "replay_bounded" && replayLimit < 1 {
		return models.CronJob{}, fmt.Errorf("replay_limit must be positive for bounded replay")
	}
	decision, err := s.CreateDecision(ctx, userID)
	if err != nil {
		return models.CronJob{}, err
	}
	if err := limits.Require(decision); err != nil {
		return models.CronJob{}, err
	}
	id, err := identity.NewID()
	if err != nil {
		return models.CronJob{}, err
	}
	now := s.clock.Now().UTC()
	next, err := nextOccurrence(canonical, timezone, now)
	if err != nil {
		return models.CronJob{}, err
	}
	job := models.CronJob{ID: id, AgentID: agentID, Name: strings.TrimSpace(name), Schedule: canonical, Timezone: timezone, Mode: mode, MisfirePolicy: misfirePolicy, ReplayLimit: replayLimit, Prompt: strings.TrimSpace(prompt), Enabled: true, NextRunAt: &next, LastEvaluatedAt: &now, CreatedAt: now, UpdatedAt: now}
	return s.repository.CreateCron(ctx, userID, job)
}

func (s *Crons) CreateDecision(ctx context.Context, userID string) (limits.Decision, error) {
	jobs, err := s.repository.ListCrons(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	return limits.Check(limits.CronJobsCreated, plan.CronJobs, len(jobs), 1, nil), nil
}

func (s *Crons) MonthlyDecision(ctx context.Context, userID string) (limits.Decision, error) {
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	periodStart, resetAt := limits.MonthWindow(s.clock.Now())
	counter, err := s.repository.GetUsage(ctx, userID, string(limits.CronRunsMonthly), periodStart)
	if err != nil {
		return limits.Decision{}, err
	}
	return limits.Check(limits.CronRunsMonthly, plan.CronRunsPerMonth, counter.Used, 1, &resetAt), nil
}

func (s *Crons) DailyDecision(ctx context.Context, userID string) (limits.Decision, error) {
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	periodStart, resetAt := limits.DayWindow(s.clock.Now())
	counter, err := s.repository.GetUsage(ctx, userID, string(limits.CronRunsDaily), periodStart)
	if err != nil {
		return limits.Decision{}, err
	}
	return limits.Check(limits.CronRunsDaily, plan.CronRunsPerDay, counter.Used, 1, &resetAt), nil
}

func (s *Crons) ParallelDecision(ctx context.Context, userID string) (limits.Decision, error) {
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	return limits.Check(limits.CronParallelRuns, plan.CronParallelRuns, s.concurrency.Usage(userID), 1, nil), nil
}

func (s *Crons) SetEnabled(ctx context.Context, userID string, cronID string, enabled bool) (models.CronJob, error) {
	jobs, err := s.List(ctx, userID)
	if err != nil {
		return models.CronJob{}, err
	}
	for _, job := range jobs {
		if job.ID == cronID {
			job.Enabled = enabled
			job.UpdatedAt = s.clock.Now().UTC()
			return s.repository.UpdateCron(ctx, userID, job)
		}
	}
	return models.CronJob{}, repositories.ErrNotFound
}
func (s *Crons) Delete(ctx context.Context, userID string, cronID string) error {
	return s.repository.DeleteCron(ctx, userID, cronID)
}

func (s *Crons) RunNow(ctx context.Context, userID string, cronID string) error {
	jobs, err := s.List(ctx, userID)
	if err != nil {
		return err
	}
	for _, job := range jobs {
		if job.ID == cronID {
			if !job.Enabled {
				return fmt.Errorf("cron job is paused")
			}
			if cronMode(job) != "local" {
				return fmt.Errorf("managed cron jobs require a validated device command")
			}
			return s.execute(ctx, userID, job, true)
		}
	}
	return repositories.ErrNotFound
}

func (s *Crons) RunManaged(ctx context.Context, userID string, agentID string, cronID string) error {
	jobs, err := s.List(ctx, userID)
	if err != nil {
		return err
	}
	for _, job := range jobs {
		if job.ID != cronID || job.AgentID != agentID {
			continue
		}
		if !job.Enabled {
			return fmt.Errorf("cron job is paused")
		}
		if cronMode(job) != "managed" {
			return fmt.Errorf("cron job is not managed")
		}
		return s.execute(ctx, userID, job, false)
	}
	return repositories.ErrNotFound
}

func (s *Crons) Run(ctx context.Context) {
	if err := s.RecoverMissed(ctx, "local"); err != nil {
		log.Error().Err(err).Msg("recover missed cron occurrences")
	}
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.runDue(ctx, "local")
		}
	}
}

func (s *Crons) runDue(ctx context.Context, userID string) {
	jobs, err := s.List(ctx, userID)
	if err != nil {
		return
	}
	now := s.clock.Now().UTC()
	for _, job := range jobs {
		if job.Enabled && cronMode(job) == "local" && job.NextRunAt != nil && !job.NextRunAt.After(now) {
			job := job
			go func() { _ = s.execute(ctx, userID, job, false) }()
		}
	}
}

func (s *Crons) execute(ctx context.Context, userID string, job models.CronJob, manual bool) error {
	ctx, span := otel.Tracer("open-lumora/services/crons").Start(ctx, "Crons.Execute")
	defer span.End()
	span.SetAttributes(attribute.String("cron.id_hash", safetracing.HashID(job.ID)), attribute.String("agent.id_hash", safetracing.HashID(job.AgentID)))
	if _, loaded := s.running.LoadOrStore(job.ID, true); loaded {
		return fmt.Errorf("cron job is already running")
	}
	defer s.running.Delete(job.ID)
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return err
	}
	var release func()
	if manual {
		release, err = s.concurrency.TryAcquire(userID, plan.CronParallelRuns)
	} else {
		release, err = s.concurrency.Acquire(ctx, userID, plan.CronParallelRuns)
	}
	if err != nil {
		decision := limits.Check(limits.CronParallelRuns, plan.CronParallelRuns, s.concurrency.Usage(userID), 1, nil)
		return &limits.ExceededError{Decision: decision}
	}
	defer release()
	dailyStart, dailyReset := limits.DayWindow(s.clock.Now())
	monthlyStart, monthlyReset := limits.MonthWindow(s.clock.Now())
	reservations := []contract.UsageReservation{
		{Resource: string(limits.CronRunsDaily), PeriodStart: dailyStart, Limit: plan.CronRunsPerDay},
		{Resource: string(limits.CronRunsMonthly), PeriodStart: monthlyStart, Limit: plan.CronRunsPerMonth},
	}
	counters, allowed, err := s.repository.ReserveUsageGroup(ctx, userID, reservations)
	if err != nil {
		return err
	}
	if !allowed {
		decisions := []limits.Decision{
			limits.Check(limits.CronRunsDaily, plan.CronRunsPerDay, counterUsed(counters, 0), 1, &dailyReset),
			limits.Check(limits.CronRunsMonthly, plan.CronRunsPerMonth, counterUsed(counters, 1), 1, &monthlyReset),
		}
		for _, decision := range decisions {
			if !decision.Allowed {
				if !manual {
					job.NextRunAt = decision.ResetAt
					job.UpdatedAt = s.clock.Now().UTC()
					_, _ = s.repository.UpdateCron(ctx, userID, job)
				}
				return &limits.ExceededError{Decision: decision}
			}
		}
		return fmt.Errorf("cron usage reservation rejected")
	}
	conversations, err := s.conversations.List(ctx, userID, job.AgentID)
	if err != nil {
		return err
	}
	var conversation models.Conversation
	if len(conversations) == 0 {
		conversation, err = s.conversations.Create(ctx, userID, job.AgentID, "Scheduled: "+job.Name)
	} else {
		conversation = conversations[0]
	}
	if err != nil {
		return err
	}
	err = s.conversations.StreamAutomated(ctx, userID, job.AgentID, conversation.ID, job.Prompt, func(_ runtimeadapter.Event) {})
	// The adapter below keeps this file independent from presentation concerns.
	now := s.clock.Now().UTC()
	job.LastRunAt = &now
	job.LastEvaluatedAt = &now
	next, scheduleErr := nextOccurrence(job.Schedule, job.Timezone, now)
	if scheduleErr != nil {
		return scheduleErr
	}
	job.NextRunAt = &next
	job.UpdatedAt = now
	_, updateErr := s.repository.UpdateCron(ctx, userID, job)
	if err != nil {
		log.Error().Str("error_code", "runtime_failed").Str("cron_id_hash", safetracing.HashID(job.ID)).Str("agent_id_hash", safetracing.HashID(job.AgentID)).Msg("cron run failed")
		return err
	}
	log.Info().Str("cron_id_hash", safetracing.HashID(job.ID)).Str("agent_id_hash", safetracing.HashID(job.AgentID)).Time("next_run_at", next).Msg("cron run completed")
	return updateErr
}

func (s *Crons) RecoverMissed(ctx context.Context, userID string) error {
	store, ok := s.repository.(contract.NotificationStore)
	if !ok {
		return nil
	}
	jobs, err := s.List(ctx, userID)
	if err != nil {
		return err
	}
	now := s.clock.Now().UTC()
	summaries := make([]models.MissedCronSummary, 0)
	updates := make([]models.CronJob, 0)
	var offlineFrom time.Time
	for _, job := range jobs {
		if !job.Enabled || cronMode(job) != "local" || job.NextRunAt == nil || job.NextRunAt.After(now) {
			continue
		}
		count, first, last, occurrenceErr := missedOccurrenceWindow(job.Schedule, job.Timezone, job.NextRunAt.UTC(), now)
		if occurrenceErr != nil {
			return occurrenceErr
		}
		if misfirePolicy(job) != "skip" {
			summaries = append(summaries, models.MissedCronSummary{CronID: job.ID, CronName: job.Name, Count: count, FirstOccurrence: first, LastOccurrence: last})
		}
		if job.LastEvaluatedAt != nil && (offlineFrom.IsZero() || job.LastEvaluatedAt.Before(offlineFrom)) {
			offlineFrom = job.LastEvaluatedAt.UTC()
		} else if job.LastEvaluatedAt == nil && (offlineFrom.IsZero() || job.CreatedAt.Before(offlineFrom)) {
			offlineFrom = job.CreatedAt.UTC()
		}
		next, occurrenceErr := nextOccurrence(job.Schedule, job.Timezone, last)
		if occurrenceErr != nil {
			return occurrenceErr
		}
		job.NextRunAt = &next
		job.LastEvaluatedAt = &now
		job.UpdatedAt = now
		updates = append(updates, job)
	}
	if len(updates) == 0 {
		return nil
	}
	if len(summaries) > 0 {
		id, err := identity.NewID()
		if err != nil {
			return err
		}
		notification := models.Notification{
			ID: id, DedupKey: missedDedupKey(summaries), Type: "cron.missed", OfflineFrom: offlineFrom, OfflineTo: now,
			Jobs: summaries, Actions: []string{"dismiss", "open_jobs", "run_latest", "replay_bounded"}, CreatedAt: now,
		}
		for _, summary := range summaries {
			notification.TotalMissed += summary.Count
		}
		if _, err := store.CreateNotification(ctx, userID, notification); err != nil {
			return err
		}
	}
	for _, job := range updates {
		if _, err := s.repository.UpdateCron(ctx, userID, job); err != nil {
			return err
		}
	}
	return nil
}

func (s *Crons) ListNotifications(ctx context.Context, userID string) ([]models.Notification, error) {
	store, ok := s.repository.(contract.NotificationStore)
	if !ok {
		return []models.Notification{}, nil
	}
	return store.ListNotifications(ctx, userID)
}

func (s *Crons) ResolveNotification(ctx context.Context, userID string, notificationID string, action string) (models.Notification, error) {
	store, ok := s.repository.(contract.NotificationStore)
	if !ok {
		return models.Notification{}, repositories.ErrNotFound
	}
	allowed := map[string]bool{"dismiss": true, "open_jobs": true, "run_latest": true, "replay_bounded": true}
	if !allowed[action] {
		return models.Notification{}, fmt.Errorf("unsupported notification action")
	}
	s.resolutionMu.Lock()
	defer s.resolutionMu.Unlock()
	notifications, err := store.ListNotifications(ctx, userID)
	if err != nil {
		return models.Notification{}, err
	}
	var current models.Notification
	found := false
	for _, notification := range notifications {
		if notification.ID == notificationID {
			current, found = notification, true
			break
		}
	}
	if !found {
		return models.Notification{}, repositories.ErrNotFound
	}
	if current.Resolution != nil {
		return current, nil
	}
	resolved, err := store.ResolveNotification(ctx, userID, notificationID, models.NotificationResolve{Action: action, ResolvedAt: s.clock.Now().UTC()})
	if err != nil || action != "run_latest" && action != "replay_bounded" {
		return resolved, err
	}
	if err := s.executeNotificationAction(ctx, userID, current, action); err != nil {
		return resolved, err
	}
	return resolved, nil
}

func (s *Crons) executeNotificationAction(ctx context.Context, userID string, notification models.Notification, action string) error {
	jobs, err := s.List(ctx, userID)
	if err != nil {
		return err
	}
	byID := make(map[string]models.CronJob, len(jobs))
	for _, job := range jobs {
		byID[job.ID] = job
	}
	for _, missed := range notification.Jobs {
		job, exists := byID[missed.CronID]
		if !exists || !job.Enabled || cronMode(job) != "local" {
			continue
		}
		runs := 1
		if action == "replay_bounded" {
			if misfirePolicy(job) != "replay_bounded" || job.ReplayLimit < 1 {
				continue
			}
			runs = missed.Count
			if runs > job.ReplayLimit {
				runs = job.ReplayLimit
			}
		}
		for run := 0; run < runs; run++ {
			if err := s.execute(ctx, userID, job, true); err != nil {
				return err
			}
		}
	}
	return nil
}

func missedDedupKey(summaries []models.MissedCronSummary) string {
	parts := make([]string, 0, len(summaries))
	for _, summary := range summaries {
		parts = append(parts, summary.CronID+":"+summary.FirstOccurrence.UTC().Format(time.RFC3339Nano)+":"+summary.LastOccurrence.UTC().Format(time.RFC3339Nano)+":"+strconv.Itoa(summary.Count))
	}
	sort.Strings(parts)
	sum := sha256.Sum256([]byte(strings.Join(parts, "|")))
	return hex.EncodeToString(sum[:])
}

func cronMode(job models.CronJob) string {
	if strings.TrimSpace(job.Mode) == "" {
		return "local"
	}
	return job.Mode
}

func misfirePolicy(job models.CronJob) string {
	if strings.TrimSpace(job.MisfirePolicy) == "" {
		return "notify"
	}
	return job.MisfirePolicy
}

func counterUsed(counters []models.UsageCounter, index int) int {
	if index < 0 || index >= len(counters) {
		return 0
	}
	return counters[index].Used
}

func (s *Crons) plan(ctx context.Context, userID string) (edition.Limits, error) {
	return s.policy.Limits(ctx, edition.Principal{UserID: userID, TenantID: userID})
}
