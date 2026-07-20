// <Summary>
// Package agents owns agent lifecycle, profile config, skills, memory, and approval persistence.
// File: Agents.go
// Functions:
//   - NewAgents(repository repositories.Repository, profiles *profile.Manager, policy edition.Policy) *Agents
//   - Agent CRUD, config, skill, memory, snapshot, and workspace operations
//
// </Summary>
package agents

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/xno/open-lumora/internal/identity"
	"github.com/xno/open-lumora/internal/limits"
	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	safetracing "github.com/xno/open-lumora/internal/tracing"
	"github.com/xno/open-lumora/pkg/edition"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
)

type Agents struct {
	repository repositories.Repository
	profiles   *profile.Manager
	policy     edition.Policy
}

func NewAgents(repository repositories.Repository, profiles *profile.Manager, policy edition.Policy) *Agents {
	return &Agents{repository: repository, profiles: profiles, policy: policy}
}

func (s *Agents) List(ctx context.Context, userID string) ([]models.Agent, error) {
	agents, err := s.repository.ListAgents(ctx, userID)
	if err != nil {
		return nil, err
	}
	for index := range agents {
		if config, configErr := s.profiles.ReadConfig(agents[index].ID); configErr == nil {
			agents[index].Config = config
		}
		if path, pathErr := s.profiles.ProfilePath(agents[index].ID); pathErr == nil {
			agents[index].Metadata = map[string]any{"profile_path": path, "workspace_path": path + "/workspace", "config": agents[index].Config}
		}
	}
	return agents, nil
}

func (s *Agents) Create(ctx context.Context, userID string, name string, description string) (models.Agent, error) {
	ctx, span := otel.Tracer("open-lumora/services/agents").Start(ctx, "Agents.Create")
	defer span.End()
	name = strings.TrimSpace(name)
	if name == "" {
		return models.Agent{}, fmt.Errorf("name is required")
	}
	decision, err := s.CreateDecision(ctx, userID)
	if err != nil {
		return models.Agent{}, err
	}
	if err := limits.Require(decision); err != nil {
		return models.Agent{}, err
	}
	agentID, err := identity.NewID()
	if err != nil {
		return models.Agent{}, err
	}
	config, err := s.profiles.Create(agentID, name)
	if err != nil {
		return models.Agent{}, err
	}
	now := time.Now().UTC()
	agent := models.Agent{ID: agentID, UserID: userID, Name: name, Title: name, Description: strings.TrimSpace(description), Status: "active", Config: config, CreatedAt: now, UpdatedAt: now}
	created, err := s.repository.CreateAgent(ctx, agent)
	if err != nil {
		return models.Agent{}, err
	}
	path, _ := s.profiles.ProfilePath(agentID)
	span.SetAttributes(attribute.String("agent.id_hash", safetracing.HashID(agentID)))
	created.Metadata = map[string]any{"profile_path": path, "workspace_path": path + "/workspace", "config": created.Config}
	return created, nil
}

func (s *Agents) CreateDecision(ctx context.Context, userID string) (limits.Decision, error) {
	count, err := s.repository.CountAgents(ctx, userID)
	if err != nil {
		return limits.Decision{}, err
	}
	plan, err := s.policy.Limits(ctx, edition.Principal{UserID: userID, TenantID: userID})
	if err != nil {
		return limits.Decision{}, err
	}
	return limits.Check(limits.AgentsCreated, plan.Agents, count, 1, nil), nil
}

func (s *Agents) Get(ctx context.Context, userID string, agentID string) (models.Agent, error) {
	agent, err := s.repository.GetAgent(ctx, userID, agentID)
	if err != nil {
		return models.Agent{}, err
	}
	config, err := s.profiles.ReadConfig(agentID)
	if err != nil {
		return models.Agent{}, err
	}
	agent.Config = config
	path, _ := s.profiles.ProfilePath(agentID)
	agent.Metadata = map[string]any{"profile_path": path, "workspace_path": path + "/workspace", "config": agent.Config}
	return agent, nil
}

func (s *Agents) UpdateMetadata(ctx context.Context, userID string, agentID string, title *string, description *string) (models.Agent, error) {
	agent, err := s.Get(ctx, userID, agentID)
	if err != nil {
		return models.Agent{}, err
	}
	if title != nil {
		agent.Title = strings.TrimSpace(*title)
		if agent.Title == "" {
			agent.Title = agent.Name
		}
	}
	if description != nil {
		agent.Description = strings.TrimSpace(*description)
	}
	agent.UpdatedAt = time.Now().UTC()
	return s.repository.UpdateAgent(ctx, agent)
}

func (s *Agents) UpdateConfig(ctx context.Context, userID string, agentID string, patch profile.ConfigPatch) (models.AgentConfig, error) {
	agent, err := s.repository.GetAgent(ctx, userID, agentID)
	if err != nil {
		return models.AgentConfig{}, err
	}
	config, err := s.profiles.UpdateConfig(agentID, patch)
	if err != nil {
		return models.AgentConfig{}, err
	}
	agent.Config = config
	agent.UpdatedAt = time.Now().UTC()
	_, err = s.repository.UpdateAgent(ctx, agent)
	return config, err
}

func (s *Agents) Delete(ctx context.Context, userID string, agentID string) error {
	if _, err := s.repository.GetAgent(ctx, userID, agentID); err != nil {
		return err
	}
	return s.repository.DeleteAgent(ctx, userID, agentID)
}

func (s *Agents) EnsureOwned(ctx context.Context, userID string, agentID string) error {
	_, err := s.repository.GetAgent(ctx, userID, agentID)
	return err
}

func (s *Agents) ListSkills(ctx context.Context, userID string, agentID string) ([]models.Skill, error) {
	if err := s.EnsureOwned(ctx, userID, agentID); err != nil {
		return nil, err
	}
	return s.profiles.ListSkills(agentID)
}

func (s *Agents) InstallSkill(ctx context.Context, userID string, agentID string, skillID string, name string, category string, content string) ([]models.Skill, error) {
	if err := s.EnsureOwned(ctx, userID, agentID); err != nil {
		return nil, err
	}
	if strings.TrimSpace(content) != "" {
		if _, _, err := s.profiles.WriteSkill(agentID, skillID, content); err != nil {
			return nil, err
		}
		return s.profiles.ListSkills(agentID)
	}
	return s.profiles.InstallSharedSkill(agentID, skillID, name, category)
}

func (s *Agents) SetSkillEnabled(ctx context.Context, userID string, agentID string, skillID string, enabled bool) ([]models.Skill, error) {
	if err := s.EnsureOwned(ctx, userID, agentID); err != nil {
		return nil, err
	}
	return s.profiles.SetSkillEnabled(agentID, skillID, enabled)
}

func (s *Agents) RemoveSkill(ctx context.Context, userID string, agentID string, skillID string) ([]models.Skill, error) {
	if err := s.EnsureOwned(ctx, userID, agentID); err != nil {
		return nil, err
	}
	return s.profiles.RemoveSkill(agentID, skillID)
}

func (s *Agents) ReadMemory(ctx context.Context, userID string, agentID string) (map[string]string, error) {
	if err := s.EnsureOwned(ctx, userID, agentID); err != nil {
		return nil, err
	}
	return s.profiles.ReadMemory(agentID)
}

func (s *Agents) WriteMemory(ctx context.Context, userID string, agentID string, target string, content string, appendMode bool) (map[string]string, models.Snapshot, error) {
	if err := s.EnsureOwned(ctx, userID, agentID); err != nil {
		return nil, models.Snapshot{}, err
	}
	return s.profiles.WriteMemory(agentID, target, content, appendMode)
}

func (s *Agents) PersistApproval(ctx context.Context, userID string, agentID string, subsystem string, choice string) (models.AgentConfig, error) {
	if err := s.EnsureOwned(ctx, userID, agentID); err != nil {
		return models.AgentConfig{}, err
	}
	config, err := s.profiles.PersistApproval(agentID, subsystem, choice)
	if err != nil {
		return models.AgentConfig{}, err
	}
	agent, err := s.repository.GetAgent(ctx, userID, agentID)
	if err != nil {
		return models.AgentConfig{}, err
	}
	agent.Config = config
	agent.UpdatedAt = time.Now().UTC()
	_, err = s.repository.UpdateAgent(ctx, agent)
	return config, err
}

func (s *Agents) Profiles() *profile.Manager { return s.profiles }

func IsNotFound(err error) bool { return errors.Is(err, repositories.ErrNotFound) }
