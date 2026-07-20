// <Summary>
// File repository tests prove Community state survives restart, remains atomic
// under concurrent mutation, tolerates invalid profiles, and deletes recoverably.
// </Summary>
package repositories

import (
	"context"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/pkg/studio/contract"
)

func TestFileRepositoryPersistsCommunityStateAcrossRestart(t *testing.T) {
	root := t.TempDir()
	manager, repository := newFileRepository(t, root)
	ctx := context.Background()
	now := time.Date(2026, 7, 20, 1, 2, 3, 0, time.UTC)

	if _, err := manager.Create("a12345", "Research"); err != nil {
		t.Fatal(err)
	}
	agent := models.Agent{ID: "a12345", UserID: "local", Name: "Research", Title: "Research", Description: "durable", Status: "active", CreatedAt: now, UpdatedAt: now}
	if _, err := repository.CreateAgent(ctx, agent); err != nil {
		t.Fatal(err)
	}
	conversation := models.Conversation{ID: "conv-1", AgentID: agent.ID, Title: "First", CreatedAt: now, UpdatedAt: now}
	if _, err := repository.CreateConversation(ctx, "local", conversation); err != nil {
		t.Fatal(err)
	}
	if _, err := repository.AddMessage(ctx, conversation.ID, models.Message{Role: "user", Content: "hello", CreatedAt: now}); err != nil {
		t.Fatal(err)
	}
	job := models.CronJob{ID: "cron-1", AgentID: agent.ID, Name: "Daily", Schedule: "@every 60m", Prompt: "work", Enabled: true, CreatedAt: now, UpdatedAt: now}
	if _, err := repository.CreateCron(ctx, "local", job); err != nil {
		t.Fatal(err)
	}
	period := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	if counter, allowed, err := repository.ReserveUsage(ctx, "local", "cron_runs_monthly", period, 200); err != nil || !allowed || counter.Used != 1 {
		t.Fatalf("reserve usage = %#v, %v, %v", counter, allowed, err)
	}
	if _, err := manager.PersistApproval(agent.ID, "skills_write", "session"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := manager.WriteSkill(agent.ID, "writer", "# Writer\n"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := manager.WriteMemory(agent.ID, "memory", "remember", false); err != nil {
		t.Fatal(err)
	}

	reloadedManager, reloaded := newFileRepository(t, root)
	agents, err := reloaded.ListAgents(ctx, "local")
	if err != nil || len(agents) != 1 || agents[0].Description != "durable" {
		t.Fatalf("agents after restart = %#v, %v", agents, err)
	}
	conversations, err := reloaded.ListConversations(ctx, "local", agent.ID)
	if err != nil || len(conversations) != 1 || conversations[0].Messages != 1 {
		t.Fatalf("conversations after restart = %#v, %v", conversations, err)
	}
	jobs, err := reloaded.ListCrons(ctx, "local")
	if err != nil || len(jobs) != 1 || jobs[0].Prompt != "work" {
		t.Fatalf("crons after restart = %#v, %v", jobs, err)
	}
	counter, err := reloaded.GetUsage(ctx, "local", "cron_runs_monthly", period)
	if err != nil || counter.Used != 1 {
		t.Fatalf("usage after restart = %#v, %v", counter, err)
	}
	config, err := reloadedManager.ReadConfig(agent.ID)
	if err != nil || config.SkillsWriteApproval {
		t.Fatalf("approval after restart = %#v, %v", config, err)
	}
	if _, err := os.Stat(filepath.Join(root, "profiles", agent.ID, "skills", "writer", "SKILL.md")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "profiles", agent.ID, "snapshots")); err != nil {
		t.Fatal(err)
	}
}

func TestFileRepositoryUsageReservationIsAtomicAndDurable(t *testing.T) {
	_, repository := newFileRepository(t, t.TempDir())
	ctx := context.Background()
	period := time.Date(2026, 7, 20, 0, 0, 0, 0, time.UTC)
	const attempts = 40
	const limit = 10
	var wait sync.WaitGroup
	var allowed int
	var resultMu sync.Mutex
	for index := 0; index < attempts; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			_, accepted, err := repository.ReserveUsage(ctx, "local", "cron_runs_daily", period, limit)
			if err != nil {
				t.Errorf("reserve: %v", err)
				return
			}
			if accepted {
				resultMu.Lock()
				allowed++
				resultMu.Unlock()
			}
		}()
	}
	wait.Wait()
	if allowed != limit {
		t.Fatalf("allowed = %d, want %d", allowed, limit)
	}
	counter, err := repository.GetUsage(ctx, "local", "cron_runs_daily", period)
	if err != nil || counter.Used != limit {
		t.Fatalf("counter = %#v, %v", counter, err)
	}
}

func TestFileRepositoryUsageGroupIsOneDurableMutation(t *testing.T) {
	root := t.TempDir()
	_, repository := newFileRepository(t, root)
	ctx := context.Background()
	day := time.Date(2026, 7, 20, 0, 0, 0, 0, time.UTC)
	month := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	reservations := []contract.UsageReservation{
		{Resource: "cron_runs_daily", PeriodStart: day, Limit: 10},
		{Resource: "cron_runs_monthly", PeriodStart: month, Limit: 1},
	}
	if counters, allowed, err := repository.ReserveUsageGroup(ctx, "local", reservations); err != nil || !allowed || len(counters) != 2 {
		t.Fatalf("first group = %#v, %v, %v", counters, allowed, err)
	}
	_, allowed, err := repository.ReserveUsageGroup(ctx, "local", reservations)
	if err != nil || allowed {
		t.Fatalf("second group = %v, %v", allowed, err)
	}
	daily, err := repository.GetUsage(ctx, "local", "cron_runs_daily", day)
	if err != nil || daily.Used != 1 {
		t.Fatalf("daily after denied group = %#v, %v", daily, err)
	}
	usageFiles, err := filepath.Glob(filepath.Join(root, "usage", "*", "counters.json"))
	if err != nil || len(usageFiles) != 1 {
		t.Fatalf("usage aggregate files = %v, %v", usageFiles, err)
	}
}

func TestFileRepositoryToleratesInvalidProfileAndReportsDiagnostic(t *testing.T) {
	root := t.TempDir()
	manager, repository := newFileRepository(t, root)
	ctx := context.Background()
	createStoredAgent(t, manager, repository, "a12345", "Valid")
	if _, err := manager.Create("b12345", "Broken"); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "profiles", "b12345", "config.yaml"), []byte("model: [\n"), 0o640); err != nil {
		t.Fatal(err)
	}

	reloadedManager, reloaded := newFileRepository(t, root)
	agents, err := reloaded.ListAgents(ctx, "local")
	if err != nil || len(agents) != 1 || agents[0].ID != "a12345" {
		t.Fatalf("valid agents = %#v, %v", agents, err)
	}
	if len(reloadedManager.Diagnostics()) == 0 || len(reloaded.Diagnostics()) == 0 {
		t.Fatal("invalid profile diagnostic was not retained")
	}
}

func TestFileRepositorySoftDeleteMovesProfileToTrash(t *testing.T) {
	root := t.TempDir()
	manager, repository := newFileRepository(t, root)
	createStoredAgent(t, manager, repository, "a12345", "Recoverable")
	if err := repository.DeleteAgent(context.Background(), "local", "a12345"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "profiles", "a12345")); !os.IsNotExist(err) {
		t.Fatalf("active profile still exists: %v", err)
	}
	entries, err := os.ReadDir(filepath.Join(root, "trash", "profiles"))
	if err != nil || len(entries) != 1 {
		t.Fatalf("trash entries = %v, %v", entries, err)
	}
	if _, err := os.Stat(filepath.Join(root, "trash", "profiles", entries[0].Name(), "config.yaml")); err != nil {
		t.Fatal(err)
	}
}

func TestFileRepositoryIgnoresInterruptedTemporaryWrite(t *testing.T) {
	root := t.TempDir()
	manager, repository := newFileRepository(t, root)
	createStoredAgent(t, manager, repository, "a12345", "Stable")
	path := filepath.Join(root, "profiles", "a12345", ".open-lumora-write-interrupted")
	if err := os.WriteFile(path, []byte("invalid"), 0o640); err != nil {
		t.Fatal(err)
	}
	_, reloaded := newFileRepository(t, root)
	agent, err := reloaded.GetAgent(context.Background(), "local", "a12345")
	if err != nil || agent.Name != "Stable" {
		t.Fatalf("agent after interrupted write = %#v, %v", agent, err)
	}
}

func newFileRepository(t *testing.T, root string) (*profile.Manager, *File) {
	t.Helper()
	manager, err := profile.NewManager(root)
	if err != nil {
		t.Fatal(err)
	}
	repository, err := NewFile(manager)
	if err != nil {
		t.Fatal(err)
	}
	return manager, repository
}

func createStoredAgent(t *testing.T, manager *profile.Manager, repository *File, id string, name string) {
	t.Helper()
	if _, err := manager.Create(id, name); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	if _, err := repository.CreateAgent(context.Background(), models.Agent{ID: id, UserID: "local", Name: name, Title: name, Status: "active", CreatedAt: now, UpdatedAt: now}); err != nil {
		t.Fatal(err)
	}
}
