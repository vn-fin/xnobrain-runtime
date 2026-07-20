// <Summary>
// Cron service tests cover total-job and monthly-run limits outside the HTTP middleware path.
// </Summary>
package crons

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/limits"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	"github.com/xno/open-lumora/pkg/edition"
	"github.com/xno/open-lumora/services/agents"
	"github.com/xno/open-lumora/services/conversations"
)

type cronTestClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *cronTestClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *cronTestClock) Set(now time.Time) {
	c.mu.Lock()
	c.now = now
	c.mu.Unlock()
}

type cronTestRuntime struct{}

func (cronTestRuntime) Run(_ context.Context, _ runtimeadapter.Request, _ func(runtimeadapter.Event)) (runtimeadapter.Result, error) {
	return runtimeadapter.Result{Output: "done"}, nil
}

type countingCronRuntime struct {
	mu    sync.Mutex
	calls int
}

func (r *countingCronRuntime) Run(_ context.Context, _ runtimeadapter.Request, _ func(runtimeadapter.Event)) (runtimeadapter.Result, error) {
	r.mu.Lock()
	r.calls++
	r.mu.Unlock()
	return runtimeadapter.Result{Output: "done"}, nil
}

func (*countingCronRuntime) ResolveApproval(context.Context, string, string, bool) error { return nil }

func TestManagedCronExecutesOnlyThroughManagedCommandPath(t *testing.T) {
	repository := repositories.NewMemory()
	profiles, _ := profile.NewManager(t.TempDir())
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, _ := agentService.Create(context.Background(), "local", "Managed", "")
	runtime := &countingCronRuntime{}
	service := NewCrons(repository, agentService, conversations.NewConversations(repository, agentService, runtime), policy)
	job, _ := service.Create(context.Background(), "local", agent.ID, "Cloud", "run", 60)
	job.Mode = "managed"
	due := time.Now().Add(-time.Minute)
	job.NextRunAt = &due
	job, _ = repository.UpdateCron(context.Background(), "local", job)

	service.runDue(context.Background(), "local")
	if runtime.calls != 0 {
		t.Fatal("local scheduler executed a managed job")
	}
	if err := service.RunNow(context.Background(), "local", job.ID); err == nil {
		t.Fatal("manual endpoint executed a managed job without a device command")
	}
	if err := service.RunManaged(context.Background(), "local", agent.ID, job.ID); err != nil {
		t.Fatal(err)
	}
	if runtime.calls != 1 {
		t.Fatalf("managed command calls = %d", runtime.calls)
	}
	if err := service.RunManaged(context.Background(), "local", "b12345", job.ID); err == nil {
		t.Fatal("wrong-agent command executed managed cron")
	}
}

func TestCronDailyAndMonthlyReservationsAreAtomicAcrossUTCWindows(t *testing.T) {
	repository := repositories.NewMemory()
	profiles, err := profile.NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	clock := &cronTestClock{now: time.Date(2026, 7, 20, 23, 59, 0, 0, time.UTC)}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 1, CronRunsPerMonth: 2, ProviderConnectionsPerType: 1}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, err := agentService.Create(context.Background(), "local", "Cron agent", "")
	if err != nil {
		t.Fatal(err)
	}
	conversationService := conversations.NewConversations(repository, agentService, cronTestRuntime{})
	service := NewCrons(repository, agentService, conversationService, policy, clock)
	job, err := service.Create(context.Background(), "local", agent.ID, "Daily", "run", 60)
	if err != nil {
		t.Fatal(err)
	}
	if err := service.RunNow(context.Background(), "local", job.ID); err != nil {
		t.Fatal(err)
	}
	if err := service.RunNow(context.Background(), "local", job.ID); err == nil {
		t.Fatal("second run in one UTC day was accepted")
	} else if exceeded, ok := limits.AsExceeded(err); !ok || exceeded.Decision.Resource != limits.CronRunsDaily {
		t.Fatalf("daily error = %v", err)
	}
	clock.Set(time.Date(2026, 7, 21, 0, 1, 0, 0, time.UTC))
	if err := service.RunNow(context.Background(), "local", job.ID); err != nil {
		t.Fatal(err)
	}
	clock.Set(time.Date(2026, 7, 22, 0, 1, 0, 0, time.UTC))
	if err := service.RunNow(context.Background(), "local", job.ID); err == nil {
		t.Fatal("third run in one UTC month was accepted")
	} else if exceeded, ok := limits.AsExceeded(err); !ok || exceeded.Decision.Resource != limits.CronRunsMonthly {
		t.Fatalf("monthly error = %v", err)
	}
	dayStart, _ := limits.DayWindow(clock.Now())
	daily, err := repository.GetUsage(context.Background(), "local", string(limits.CronRunsDaily), dayStart)
	if err != nil || daily.Used != 0 {
		t.Fatalf("denied monthly run partially used daily quota = %#v, %v", daily, err)
	}
}

type blockingCronRuntime struct {
	started chan struct{}
	release chan struct{}
}

func (r *blockingCronRuntime) Run(_ context.Context, _ runtimeadapter.Request, _ func(runtimeadapter.Event)) (runtimeadapter.Result, error) {
	close(r.started)
	<-r.release
	return runtimeadapter.Result{Output: "done"}, nil
}

func (*blockingCronRuntime) ResolveApproval(context.Context, string, string, bool) error { return nil }

func TestManualCronReturnsImmediatelyWhenParallelCapacityIsFull(t *testing.T) {
	repository := repositories.NewMemory()
	profiles, err := profile.NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200, ProviderConnectionsPerType: 1}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, err := agentService.Create(context.Background(), "local", "Cron agent", "")
	if err != nil {
		t.Fatal(err)
	}
	runtime := &blockingCronRuntime{started: make(chan struct{}), release: make(chan struct{})}
	service := NewCrons(repository, agentService, conversations.NewConversations(repository, agentService, runtime), policy)
	first, _ := service.Create(context.Background(), "local", agent.ID, "First", "run", 60)
	second, _ := service.Create(context.Background(), "local", agent.ID, "Second", "run", 60)
	done := make(chan error, 1)
	go func() { done <- service.RunNow(context.Background(), "local", first.ID) }()
	select {
	case <-runtime.started:
	case <-time.After(time.Second):
		t.Fatal("first run did not start")
	}
	started := time.Now()
	err = service.RunNow(context.Background(), "local", second.ID)
	if exceeded, ok := limits.AsExceeded(err); !ok || exceeded.Decision.Resource != limits.CronParallelRuns {
		t.Fatalf("parallel error = %v", err)
	}
	if time.Since(started) > 100*time.Millisecond {
		t.Fatalf("parallel rejection blocked for %s", time.Since(started))
	}
	close(runtime.release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestRestartRecoveryCreatesOneCompactIdempotentNotification(t *testing.T) {
	repository := repositories.NewMemory()
	profiles, err := profile.NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 7, 20, 12, 0, 0, 0, time.UTC)
	clock := &cronTestClock{now: now}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200, ProviderConnectionsPerType: 1}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, err := agentService.Create(context.Background(), "local", "Cron agent", "")
	if err != nil {
		t.Fatal(err)
	}
	service := NewCrons(repository, agentService, conversations.NewConversations(repository, agentService, cronTestRuntime{}), policy, clock)
	first, _ := service.Create(context.Background(), "local", agent.ID, "First", "run", 60)
	second, _ := service.Create(context.Background(), "local", agent.ID, "Second", "run", 30)
	offlineFrom := now.Add(-3 * time.Hour)
	first.NextRunAt = pointerTime(now.Add(-2 * time.Hour))
	first.LastEvaluatedAt = &offlineFrom
	second.NextRunAt = pointerTime(now.Add(-time.Hour))
	second.LastEvaluatedAt = &offlineFrom
	_, _ = repository.UpdateCron(context.Background(), "local", first)
	_, _ = repository.UpdateCron(context.Background(), "local", second)

	if err := service.RecoverMissed(context.Background(), "local"); err != nil {
		t.Fatal(err)
	}
	notifications, err := service.ListNotifications(context.Background(), "local")
	if err != nil || len(notifications) != 1 {
		t.Fatalf("notifications = %#v, %v", notifications, err)
	}
	if notifications[0].TotalMissed != 6 || len(notifications[0].Jobs) != 2 {
		t.Fatalf("compact summary = %#v", notifications[0])
	}
	jobs, err := service.List(context.Background(), "local")
	if err != nil {
		t.Fatal(err)
	}
	for _, job := range jobs {
		if job.NextRunAt == nil || !job.NextRunAt.After(now) {
			t.Fatalf("job was not advanced after recovery: %#v", job)
		}
	}
	if err := service.RecoverMissed(context.Background(), "local"); err != nil {
		t.Fatal(err)
	}
	notifications, _ = service.ListNotifications(context.Background(), "local")
	if len(notifications) != 1 {
		t.Fatalf("duplicate recovery notification count = %d", len(notifications))
	}
	resolved, err := service.ResolveNotification(context.Background(), "local", notifications[0].ID, "dismiss")
	if err != nil || resolved.Resolution == nil || resolved.Resolution.Action != "dismiss" {
		t.Fatalf("resolution = %#v, %v", resolved, err)
	}
	again, err := service.ResolveNotification(context.Background(), "local", notifications[0].ID, "open_jobs")
	if err != nil || again.Resolution.Action != "dismiss" {
		t.Fatalf("idempotent resolution = %#v, %v", again, err)
	}
}

func TestRecoveryIgnoresManagedAndSkippedJobs(t *testing.T) {
	repository := repositories.NewMemory()
	profiles, _ := profile.NewManager(t.TempDir())
	now := time.Date(2026, 7, 20, 12, 0, 0, 0, time.UTC)
	clock := &cronTestClock{now: now}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, _ := agentService.Create(context.Background(), "local", "Cron agent", "")
	service := NewCrons(repository, agentService, conversations.NewConversations(repository, agentService, cronTestRuntime{}), policy, clock)
	managed, _ := service.Create(context.Background(), "local", agent.ID, "Managed", "run", 60)
	skipped, _ := service.Create(context.Background(), "local", agent.ID, "Skipped", "run", 60)
	managed.Mode = "managed"
	skipped.MisfirePolicy = "skip"
	managed.NextRunAt = pointerTime(now.Add(-time.Hour))
	skipped.NextRunAt = pointerTime(now.Add(-time.Hour))
	_, _ = repository.UpdateCron(context.Background(), "local", managed)
	_, _ = repository.UpdateCron(context.Background(), "local", skipped)
	if err := service.RecoverMissed(context.Background(), "local"); err != nil {
		t.Fatal(err)
	}
	notifications, _ := service.ListNotifications(context.Background(), "local")
	if len(notifications) != 0 {
		t.Fatalf("unexpected notifications = %#v", notifications)
	}
	jobs, _ := service.List(context.Background(), "local")
	for _, job := range jobs {
		if job.ID == managed.ID && !job.NextRunAt.Equal(now.Add(-time.Hour)) {
			t.Fatal("managed job was evaluated by local recovery")
		}
		if job.ID == skipped.ID && !job.NextRunAt.After(now) {
			t.Fatal("skip job was not advanced")
		}
	}
}

func TestMissedRunActionsReserveQuotaOnlyWhenExecutedAndRemainIdempotent(t *testing.T) {
	repository := repositories.NewMemory()
	profiles, _ := profile.NewManager(t.TempDir())
	now := time.Date(2026, 7, 20, 12, 0, 0, 0, time.UTC)
	clock := &cronTestClock{now: now}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, _ := agentService.Create(context.Background(), "local", "Replay", "")
	runtime := &countingCronRuntime{}
	service := NewCrons(repository, agentService, conversations.NewConversations(repository, agentService, runtime), policy, clock)
	job, err := service.CreateScheduled(context.Background(), "local", agent.ID, "Replay", "run", "@every 30m", "Etc/UTC", "local", "replay_bounded", 2)
	if err != nil {
		t.Fatal(err)
	}
	due := now.Add(-90 * time.Minute)
	job.NextRunAt = &due
	job.LastEvaluatedAt = pointerTime(now.Add(-2 * time.Hour))
	_, _ = repository.UpdateCron(context.Background(), "local", job)
	if err := service.RecoverMissed(context.Background(), "local"); err != nil {
		t.Fatal(err)
	}
	dayStart, _ := limits.DayWindow(now)
	before, _ := repository.GetUsage(context.Background(), "local", string(limits.CronRunsDaily), dayStart)
	if before.Used != 0 || runtime.calls != 0 {
		t.Fatal("missed occurrences consumed quota or executed before approval")
	}
	notifications, _ := service.ListNotifications(context.Background(), "local")
	if len(notifications) != 1 || notifications[0].Jobs[0].Count != 4 {
		t.Fatalf("unexpected missed notification: %+v", notifications)
	}
	if _, err := service.ResolveNotification(context.Background(), "local", notifications[0].ID, "replay_bounded"); err != nil {
		t.Fatal(err)
	}
	after, _ := repository.GetUsage(context.Background(), "local", string(limits.CronRunsDaily), dayStart)
	if runtime.calls != 2 || after.Used != 2 {
		t.Fatalf("bounded replay calls=%d usage=%d", runtime.calls, after.Used)
	}
	if _, err := service.ResolveNotification(context.Background(), "local", notifications[0].ID, "run_latest"); err != nil {
		t.Fatal(err)
	}
	if runtime.calls != 2 {
		t.Fatalf("idempotent resolution re-executed %d calls", runtime.calls)
	}
}

func pointerTime(value time.Time) *time.Time                                        { return &value }
func (cronTestRuntime) ResolveApproval(context.Context, string, string, bool) error { return nil }

func TestCronLimitsApplyWithoutHTTPMiddleware(t *testing.T) {
	repository := repositories.NewMemory()
	profiles, err := profile.NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 1, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 1, ProviderConnectionsPerType: 1}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, err := agentService.Create(context.Background(), "local", "Cron agent", "")
	if err != nil {
		t.Fatal(err)
	}
	conversationService := conversations.NewConversations(repository, agentService, cronTestRuntime{})
	service := NewCrons(repository, agentService, conversationService, policy)
	job, err := service.Create(context.Background(), "local", agent.ID, "Daily", "run", 60)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Create(context.Background(), "local", agent.ID, "Second", "run", 60); err == nil {
		t.Fatal("second cron job was accepted")
	}
	if err := service.RunNow(context.Background(), "local", job.ID); err != nil {
		t.Fatal(err)
	}
	err = service.RunNow(context.Background(), "local", job.ID)
	if _, ok := limits.AsExceeded(err); !ok {
		t.Fatalf("second run error = %v", err)
	}
}
