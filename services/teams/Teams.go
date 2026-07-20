// <Summary>
// Teams owns saved-team validation and bounded Hermes delegation. It reuses the
// existing conversation runtime, enforces owner scope and policy limits under
// races, and returns only final child summaries to the orchestrator.
// </Summary>
package teams

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/xno/open-lumora/internal/edition"
	"github.com/xno/open-lumora/internal/identity"
	"github.com/xno/open-lumora/internal/limits"
	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/repositories"
	"github.com/xno/open-lumora/internal/studio/contract"
	"github.com/xno/open-lumora/services/agents"
)

var safeToolsets = map[string]struct{}{
	"browser": {}, "code_execution": {}, "file": {}, "image_gen": {}, "terminal": {},
	"todo": {}, "tts": {}, "video": {}, "video_gen": {}, "vision": {}, "web": {}, "x_search": {},
}

type DelegationPolicy struct {
	Role             string
	Toolsets         []string
	NoClarification  bool
	FinalSummaryOnly bool
	NoMemoryWrites   bool
}

type Delegator interface {
	Delegate(ctx context.Context, userID string, agentID string, task string, policy DelegationPolicy) (string, error)
}

type AgentOwner interface {
	EnsureOwned(ctx context.Context, userID string, agentID string) error
}

type MemberResult struct {
	AgentID string `json:"agent_id"`
	Role    string `json:"role"`
	Summary string `json:"summary,omitempty"`
	Error   string `json:"error,omitempty"`
}

type RunResult struct {
	TeamID              string         `json:"team_id"`
	MemberResults       []MemberResult `json:"member_results"`
	OrchestratorSummary string         `json:"orchestrator_summary"`
	StartedAt           time.Time      `json:"started_at"`
	CompletedAt         time.Time      `json:"completed_at"`
}

type Teams struct {
	store     contract.TeamStore
	agents    AgentOwner
	delegator Delegator
	policy    edition.Policy
	mu        sync.Mutex
	active    map[string]int
	waiters   map[string]chan struct{}
}

func NewTeams(store contract.TeamStore, owner AgentOwner, delegator Delegator, policy edition.Policy) *Teams {
	return &Teams{store: store, agents: owner, delegator: delegator, policy: policy, active: make(map[string]int), waiters: make(map[string]chan struct{})}
}

func (s *Teams) List(ctx context.Context, userID string) ([]models.Team, error) {
	return s.store.ListTeams(ctx, userID)
}

func (s *Teams) CreateDecision(ctx context.Context, userID string) (limits.Decision, error) {
	current, err := s.store.ListTeams(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	return limits.Check(limits.TeamsCreated, plan.Teams, len(current), 1, nil), nil
}

func (s *Teams) Get(ctx context.Context, userID string, teamID string) (models.Team, error) {
	return s.store.GetTeam(ctx, userID, teamID)
}

func (s *Teams) Create(ctx context.Context, userID string, team models.Team) (models.Team, error) {
	decision, err := s.CreateDecision(ctx, userID)
	if err != nil {
		return models.Team{}, err
	}
	if err := limits.Require(decision); err != nil {
		return models.Team{}, err
	}
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return models.Team{}, err
	}
	team.ID, err = identity.NewID()
	if err != nil {
		return models.Team{}, err
	}
	team.UserID = userID
	now := time.Now().UTC()
	team.CreatedAt, team.UpdatedAt = now, now
	if err := s.validate(ctx, userID, &team, plan); err != nil {
		return models.Team{}, err
	}
	return s.store.CreateTeam(ctx, team)
}

func (s *Teams) Update(ctx context.Context, userID string, teamID string, patch models.Team) (models.Team, error) {
	current, err := s.store.GetTeam(ctx, userID, teamID)
	if err != nil {
		return models.Team{}, err
	}
	patch.ID, patch.UserID, patch.CreatedAt = current.ID, current.UserID, current.CreatedAt
	patch.UpdatedAt = time.Now().UTC()
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return models.Team{}, err
	}
	if err := s.validate(ctx, userID, &patch, plan); err != nil {
		return models.Team{}, err
	}
	return s.store.UpdateTeam(ctx, patch)
}

func (s *Teams) Delete(ctx context.Context, userID string, teamID string) error {
	return s.store.DeleteTeam(ctx, userID, teamID)
}

func (s *Teams) Run(ctx context.Context, userID string, teamID string, task string, depth int) (RunResult, error) {
	startedAt := time.Now().UTC()
	team, err := s.store.GetTeam(ctx, userID, teamID)
	if err != nil {
		return RunResult{}, err
	}
	if !team.Enabled {
		return RunResult{}, fmt.Errorf("team is disabled")
	}
	if strings.TrimSpace(task) == "" {
		return RunResult{}, fmt.Errorf("task is required")
	}
	if depth < 1 {
		depth = 1
	}
	plan, err := s.plan(ctx, userID)
	if err != nil {
		return RunResult{}, err
	}
	maximumDepth := minPositive(plan.DelegationDepth, team.MaxDepth)
	if err := limits.Require(limits.Check(limits.DelegationDepth, maximumDepth, depth-1, 1, nil)); err != nil {
		return RunResult{}, err
	}
	members := enabledMembers(team.Members)
	if len(members) == 0 {
		return RunResult{}, fmt.Errorf("team has no enabled delegated workers")
	}
	workerLimit := minPositive(plan.DelegatedWorkers, team.MaxParallel)
	if workerLimit == 0 {
		return RunResult{}, limits.Require(limits.Check(limits.DelegatedWorkers, 0, 0, 1, nil))
	}
	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	results := make([]MemberResult, len(members))
	var wait sync.WaitGroup
	var firstErr error
	var errorMu sync.Mutex
	for index, member := range members {
		index, member := index, member
		wait.Add(1)
		go func() {
			defer wait.Done()
			if err := s.acquire(runCtx, userID, workerLimit); err != nil {
				results[index] = MemberResult{AgentID: member.AgentID, Role: member.Role, Error: err.Error()}
				return
			}
			defer s.release(userID)
			policy := DelegationPolicy{Role: member.Role, Toolsets: append([]string(nil), member.AllowedTools...), NoClarification: true, FinalSummaryOnly: true, NoMemoryWrites: true}
			summary, delegateErr := s.delegator.Delegate(runCtx, userID, member.AgentID, task, policy)
			results[index] = MemberResult{AgentID: member.AgentID, Role: member.Role, Summary: summary}
			if delegateErr != nil {
				results[index].Error = delegateErr.Error()
				errorMu.Lock()
				if firstErr == nil {
					firstErr = delegateErr
					cancel()
				}
				errorMu.Unlock()
			}
		}()
	}
	wait.Wait()
	if firstErr != nil {
		return RunResult{TeamID: team.ID, MemberResults: results, StartedAt: startedAt, CompletedAt: time.Now().UTC()}, firstErr
	}
	orchestratorInput := synthesisPrompt(task, results)
	orchestratorSummary, err := s.delegator.Delegate(ctx, userID, team.OrchestratorID, orchestratorInput, DelegationPolicy{Role: "orchestrator", Toolsets: []string{"todo"}, NoClarification: true, FinalSummaryOnly: true, NoMemoryWrites: true})
	result := RunResult{TeamID: team.ID, MemberResults: results, OrchestratorSummary: orchestratorSummary, StartedAt: startedAt, CompletedAt: time.Now().UTC()}
	return result, err
}

func (s *Teams) validate(ctx context.Context, userID string, team *models.Team, plan edition.Limits) error {
	team.Name = strings.TrimSpace(team.Name)
	if team.Name == "" {
		return fmt.Errorf("team name is required")
	}
	if team.SharedWorkspace {
		return fmt.Errorf("shared team workspaces require enterprise workspace RBAC")
	}
	if err := s.agents.EnsureOwned(ctx, userID, team.OrchestratorID); err != nil {
		return fmt.Errorf("orchestrator is not owned by this user: %w", err)
	}
	totalAgents := 1 + len(team.Members)
	if err := limits.Require(limits.Check(limits.TeamMembers, plan.AgentsPerTeam, 0, totalAgents, nil)); err != nil {
		return err
	}
	seen := map[string]struct{}{team.OrchestratorID: {}}
	for index := range team.Members {
		member := &team.Members[index]
		member.Role = strings.TrimSpace(member.Role)
		if member.AgentID == "" || member.Role == "" {
			return fmt.Errorf("each team member requires agent_id and role")
		}
		if _, exists := seen[member.AgentID]; exists {
			return fmt.Errorf("team agent %s is duplicated", member.AgentID)
		}
		seen[member.AgentID] = struct{}{}
		if err := s.agents.EnsureOwned(ctx, userID, member.AgentID); err != nil {
			return fmt.Errorf("member %s is not owned by this user: %w", member.AgentID, err)
		}
		if len(member.AllowedTools) == 0 {
			member.AllowedTools = []string{"web"}
		}
		uniqueTools := make(map[string]struct{}, len(member.AllowedTools))
		cleanedTools := make([]string, 0, len(member.AllowedTools))
		for _, tool := range member.AllowedTools {
			tool = strings.TrimSpace(tool)
			if _, allowed := safeToolsets[tool]; !allowed {
				return fmt.Errorf("toolset %q is not allowed for delegated workers", tool)
			}
			if _, exists := uniqueTools[tool]; !exists {
				uniqueTools[tool] = struct{}{}
				cleanedTools = append(cleanedTools, tool)
			}
		}
		sort.Strings(cleanedTools)
		member.AllowedTools = cleanedTools
	}
	if team.MaxParallel < 1 {
		team.MaxParallel = plan.DelegatedWorkers
	}
	if team.MaxParallel > plan.DelegatedWorkers && plan.DelegatedWorkers >= 0 {
		return limits.Require(limits.Check(limits.DelegatedWorkers, plan.DelegatedWorkers, 0, team.MaxParallel, nil))
	}
	if team.MaxDepth < 1 {
		team.MaxDepth = 1
	}
	if team.MaxDepth > plan.DelegationDepth && plan.DelegationDepth >= 0 {
		return limits.Require(limits.Check(limits.DelegationDepth, plan.DelegationDepth, 0, team.MaxDepth, nil))
	}
	return nil
}

func (s *Teams) plan(ctx context.Context, userID string) (edition.Limits, error) {
	return s.policy.Limits(ctx, edition.Principal{UserID: userID, TenantID: userID})
}

func (s *Teams) acquire(ctx context.Context, userID string, limit int) error {
	for {
		s.mu.Lock()
		if limit < 0 || s.active[userID] < limit {
			s.active[userID]++
			s.mu.Unlock()
			return nil
		}
		waiter := s.waiters[userID]
		if waiter == nil {
			waiter = make(chan struct{})
			s.waiters[userID] = waiter
		}
		s.mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-waiter:
		}
	}
}

func (s *Teams) release(userID string) {
	s.mu.Lock()
	if s.active[userID] > 0 {
		s.active[userID]--
	}
	if waiter := s.waiters[userID]; waiter != nil {
		close(waiter)
		delete(s.waiters, userID)
	}
	s.mu.Unlock()
}

func enabledMembers(members []models.TeamMember) []models.TeamMember {
	result := make([]models.TeamMember, 0, len(members))
	for _, member := range members {
		if member.Enabled {
			result = append(result, member)
		}
	}
	return result
}

func synthesisPrompt(task string, results []MemberResult) string {
	var builder strings.Builder
	builder.WriteString("Synthesize the delegated worker summaries into one final answer. Do not ask questions and do not use outside information.\nOriginal task: ")
	builder.WriteString(task)
	for _, result := range results {
		builder.WriteString("\n\nWorker role: ")
		builder.WriteString(result.Role)
		builder.WriteString("\nSummary:\n")
		builder.WriteString(result.Summary)
	}
	return builder.String()
}

func minPositive(left int, right int) int {
	if left < 0 {
		return right
	}
	if right < 0 {
		return left
	}
	if left < right {
		return left
	}
	return right
}

func IsNotFound(err error) bool { return errors.Is(err, repositories.ErrNotFound) }

var _ AgentOwner = (*agents.Agents)(nil)
