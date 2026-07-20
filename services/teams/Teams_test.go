// <Summary>
// Team tests prove owner and plan validation, race-safe worker limits, cancellation,
// depth enforcement, and exact child safety policy forwarding.
// </Summary>
package teams

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/repositories"
	"github.com/xno/open-lumora/pkg/edition"
)

type fakeOwner struct{ owned map[string]bool }

func (f fakeOwner) EnsureOwned(_ context.Context, _ string, agentID string) error {
	if !f.owned[agentID] {
		return repositories.ErrNotFound
	}
	return nil
}

type recordingDelegator struct {
	mu            sync.Mutex
	active        int
	maximumActive int
	policies      []DelegationPolicy
	delay         time.Duration
	block         bool
}

func (d *recordingDelegator) Delegate(ctx context.Context, _ string, _ string, _ string, policy DelegationPolicy) (string, error) {
	d.mu.Lock()
	d.policies = append(d.policies, policy)
	if policy.Role != "orchestrator" {
		d.active++
		if d.active > d.maximumActive {
			d.maximumActive = d.active
		}
	}
	d.mu.Unlock()
	if d.block {
		<-ctx.Done()
		d.finish(policy)
		return "", ctx.Err()
	}
	select {
	case <-ctx.Done():
		d.finish(policy)
		return "", ctx.Err()
	case <-time.After(d.delay):
	}
	d.finish(policy)
	return "summary", nil
}

func (d *recordingDelegator) finish(policy DelegationPolicy) {
	if policy.Role == "orchestrator" {
		return
	}
	d.mu.Lock()
	d.active--
	d.mu.Unlock()
}

func TestTeamsEnforcesOwnerPlanAndToolPolicies(t *testing.T) {
	service := NewTeams(repositories.NewMemory(), fakeOwner{owned: map[string]bool{"orchestrator": true, "worker": true}}, &recordingDelegator{}, testPolicy())
	team, err := service.Create(context.Background(), "local", models.Team{
		Name: "Research", OrchestratorID: "orchestrator", Enabled: true,
		Members: []models.TeamMember{{AgentID: "worker", Role: "researcher", AllowedTools: []string{"web", "web"}, Enabled: true}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if team.MaxParallel != 1 || team.MaxDepth != 1 || len(team.Members[0].AllowedTools) != 1 {
		t.Fatalf("normalized team = %#v", team)
	}
	if _, err := service.Create(context.Background(), "local", models.Team{Name: "Second", OrchestratorID: "orchestrator"}); err == nil {
		t.Fatal("second free team was accepted")
	}
	store := repositories.NewMemory()
	service = NewTeams(store, fakeOwner{owned: map[string]bool{"orchestrator": true, "worker": true}}, &recordingDelegator{}, testPolicy())
	if _, err := service.Create(context.Background(), "local", models.Team{Name: "Unsafe", OrchestratorID: "orchestrator", Members: []models.TeamMember{{AgentID: "worker", Role: "leaf", AllowedTools: []string{"memory"}}}}); err == nil {
		t.Fatal("memory toolset was accepted")
	}
	service = NewTeams(store, fakeOwner{owned: map[string]bool{"orchestrator": true}}, &recordingDelegator{}, testPolicy())
	if _, err := service.Create(context.Background(), "local", models.Team{Name: "Foreign", OrchestratorID: "orchestrator", Members: []models.TeamMember{{AgentID: "worker", Role: "leaf"}}}); err == nil {
		t.Fatal("foreign member was accepted")
	}
}

func TestTeamsWorkerLimitIsRaceSafeAndPolicyIsForwarded(t *testing.T) {
	delegator := &recordingDelegator{delay: 30 * time.Millisecond}
	service, team := newTeamService(delegator)
	var wait sync.WaitGroup
	errorsFound := make(chan error, 2)
	for run := 0; run < 2; run++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			_, err := service.Run(context.Background(), "local", team.ID, "investigate", 1)
			errorsFound <- err
		}()
	}
	wait.Wait()
	close(errorsFound)
	for err := range errorsFound {
		if err != nil {
			t.Fatal(err)
		}
	}
	delegator.mu.Lock()
	defer delegator.mu.Unlock()
	if delegator.maximumActive != 1 {
		t.Fatalf("maximum active workers = %d", delegator.maximumActive)
	}
	for _, policy := range delegator.policies {
		if !policy.NoClarification || !policy.FinalSummaryOnly || !policy.NoMemoryWrites {
			t.Fatalf("unsafe delegation policy = %#v", policy)
		}
	}
}

func TestTeamsCancellationAndDepth(t *testing.T) {
	delegator := &recordingDelegator{block: true}
	service, team := newTeamService(delegator)
	if _, err := service.Run(context.Background(), "local", team.ID, "nested", 2); err == nil {
		t.Fatal("depth above free plan was accepted")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	_, err := service.Run(ctx, "local", team.ID, "wait", 1)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("cancellation error = %v", err)
	}
}

func newTeamService(delegator Delegator) (*Teams, models.Team) {
	store := repositories.NewMemory()
	service := NewTeams(store, fakeOwner{owned: map[string]bool{"orchestrator": true, "worker": true}}, delegator, testPolicy())
	team, err := service.Create(context.Background(), "local", models.Team{
		Name: "Research", OrchestratorID: "orchestrator", Enabled: true,
		Members: []models.TeamMember{{AgentID: "worker", Role: "researcher", AllowedTools: []string{"web"}, Enabled: true}},
	})
	if err != nil {
		panic(err)
	}
	return service, team
}

func testPolicy() edition.OpenSource {
	return edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200, ProviderConnectionsPerType: 1, TeamLimit: 1, AgentsPerTeam: 2, DelegatedWorkers: 1, DelegationDepth: 1}
}
