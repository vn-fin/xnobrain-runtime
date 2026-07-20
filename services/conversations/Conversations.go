// <Summary>
// Package conversations owns chat history, streamed Hermes runs, and approval orchestration.
// File: Conversations.go
// Functions:
//   - Conversation CRUD and streamed runtime run orchestration
//
// </Summary>
package conversations

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/xno/open-lumora/internal/identity"
	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	safetracing "github.com/xno/open-lumora/internal/tracing"
	"github.com/xno/open-lumora/services/agents"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
)

type Run struct {
	ID                string
	AgentID           string
	ConversationID    string
	PendingSubsystem  string
	PendingSubsystems []string
	Approval          chan string
	Preflight         bool
	Cancel            context.CancelFunc
}

type Conversations struct {
	repository repositories.Repository
	agents     *agents.Agents
	runtime    runtimeadapter.Runtime
	mu         sync.RWMutex
	runs       map[string]*Run
}

func NewConversations(repository repositories.Repository, agentService *agents.Agents, runtime runtimeadapter.Runtime) *Conversations {
	return &Conversations{repository: repository, agents: agentService, runtime: runtime, runs: make(map[string]*Run)}
}

func (s *Conversations) List(ctx context.Context, userID string, agentID string) ([]models.Conversation, error) {
	if err := s.agents.EnsureOwned(ctx, userID, agentID); err != nil {
		return nil, err
	}
	return s.repository.ListConversations(ctx, userID, agentID)
}

func (s *Conversations) Create(ctx context.Context, userID string, agentID string, title string) (models.Conversation, error) {
	if err := s.agents.EnsureOwned(ctx, userID, agentID); err != nil {
		return models.Conversation{}, err
	}
	conversationID, err := identity.NewID()
	if err != nil {
		return models.Conversation{}, err
	}
	title = strings.TrimSpace(title)
	if title == "" {
		title = "New conversation"
	}
	now := time.Now().UTC()
	return s.repository.CreateConversation(ctx, userID, models.Conversation{ID: conversationID, AgentID: agentID, Title: title, CreatedAt: now, UpdatedAt: now})
}

func (s *Conversations) Get(ctx context.Context, userID string, agentID string, conversationID string) (models.Conversation, error) {
	return s.repository.GetConversation(ctx, userID, agentID, conversationID)
}

func (s *Conversations) Rename(ctx context.Context, userID string, agentID string, conversationID string, title string) (models.Conversation, error) {
	conversation, err := s.Get(ctx, userID, agentID, conversationID)
	if err != nil {
		return models.Conversation{}, err
	}
	if title = strings.TrimSpace(title); title == "" {
		return models.Conversation{}, fmt.Errorf("title is required")
	}
	conversation.Title = title
	conversation.UpdatedAt = time.Now().UTC()
	return s.repository.UpdateConversation(ctx, userID, conversation)
}

func (s *Conversations) Delete(ctx context.Context, userID string, agentID string, conversationID string) error {
	return s.repository.DeleteConversation(ctx, userID, agentID, conversationID)
}

func (s *Conversations) Messages(ctx context.Context, userID string, agentID string, conversationID string) ([]models.Message, error) {
	if _, err := s.Get(ctx, userID, agentID, conversationID); err != nil {
		return nil, err
	}
	return s.repository.ListMessages(ctx, conversationID)
}

func (s *Conversations) Stream(ctx context.Context, userID string, agentID string, conversationID string, input string, emit func(runtimeadapter.Event)) error {
	return s.stream(ctx, userID, agentID, conversationID, input, true, nil, emit)
}

// StreamAutomated runs a cron turn without waiting for an interactive profile
// approval. Hermes stages gated memory/skill writes for later review.
func (s *Conversations) StreamAutomated(ctx context.Context, userID string, agentID string, conversationID string, input string, emit func(runtimeadapter.Event)) error {
	return s.stream(ctx, userID, agentID, conversationID, input, false, nil, emit)
}

// StreamDelegated runs an automated child turn with an explicit Hermes toolset
// allowlist. The caller owns delegation depth, concurrency, and prompt policy.
func (s *Conversations) StreamDelegated(ctx context.Context, userID string, agentID string, conversationID string, input string, toolsets []string, emit func(runtimeadapter.Event)) error {
	return s.stream(ctx, userID, agentID, conversationID, input, false, append([]string(nil), toolsets...), emit)
}

func (s *Conversations) stream(ctx context.Context, userID string, agentID string, conversationID string, input string, interactive bool, toolsets []string, emit func(runtimeadapter.Event)) (streamErr error) {
	ctx, span := otel.Tracer("open-lumora/services/conversations").Start(ctx, "Conversations.Stream")
	metrics := newRunTelemetry(agentID)
	defer func() {
		metrics.Close()
		metrics.Apply(span)
		if streamErr != nil {
			span.SetAttributes(attribute.String("run.status", "error"), attribute.String("error.type", "agent_run_failed"))
		} else {
			span.SetAttributes(attribute.String("run.status", "ok"))
		}
		span.End()
	}()
	agentIDHash := safetracing.HashID(agentID)
	span.SetAttributes(attribute.String("agent.id_hash", agentIDHash), attribute.String("conversation.id_hash", safetracing.HashID(conversationID)), attribute.String("lumora.agent.ref", agentID), attribute.String("lumora.conversation.ref", conversationID), attribute.String("lumora.session.ref", conversationID), attribute.Bool("run.interactive", interactive), attribute.String("lumora.node.kind", "agent"), attribute.String("lumora.node.id_hash", agentIDHash), attribute.Int("gen_ai.prompt.length", len([]rune(strings.TrimSpace(input)))))
	input = strings.TrimSpace(input)
	if input == "" {
		return fmt.Errorf("input is required")
	}
	agent, err := s.agents.Get(ctx, userID, agentID)
	if err != nil {
		return err
	}
	metrics.Configure(agent.Name, agent.Config.Provider, agent.Config.Model)
	span.SetAttributes(attribute.String("agent.name", agent.Name), attribute.String("lumora.node.label", agent.Name), attribute.String("gen_ai.provider.name", agent.Config.Provider), attribute.String("gen_ai.request.model", agent.Config.Model))
	conversation, err := s.Get(ctx, userID, agentID, conversationID)
	if err != nil {
		return err
	}
	runID, err := identity.NewID()
	if err != nil {
		return err
	}
	span.SetAttributes(attribute.String("run.id_hash", safetracing.HashID(runID)))
	runCtx, cancel := context.WithCancel(ctx)
	run := &Run{ID: runID, AgentID: agentID, ConversationID: conversationID, Cancel: cancel, Approval: make(chan string, 1)}
	if agent.Config.MemoryWriteApproval {
		run.PendingSubsystems = append(run.PendingSubsystems, "memory_write")
	}
	if agent.Config.SkillsWriteApproval {
		run.PendingSubsystems = append(run.PendingSubsystems, "skills_write")
	}
	if interactive && len(run.PendingSubsystems) > 0 {
		run.PendingSubsystem = run.PendingSubsystems[0]
		run.Preflight = true
	}
	s.mu.Lock()
	s.runs[runID] = run
	s.mu.Unlock()
	defer func() { s.mu.Lock(); delete(s.runs, runID); s.mu.Unlock(); cancel() }()
	emit(runtimeadapter.Event{Type: "run.started", RunID: runID})
	if run.Preflight {
		emit(runtimeadapter.Event{Type: "approval.request", RunID: runID, PatternKey: run.PendingSubsystem, PatternKeys: run.PendingSubsystems, Description: "Allow this agent to write its own memory and skills", Command: "Profile writes are restricted to this agent's profile", AllowPermanent: true})
		var choice string
		select {
		case choice = <-run.Approval:
		case <-runCtx.Done():
			return runCtx.Err()
		}
		emit(runtimeadapter.Event{Type: "approval.responded", RunID: runID, Text: choice})
		if choice == "deny" {
			return fmt.Errorf("profile write permission denied")
		}
		if choice == "once" {
			disable := profile.ConfigPatch{}
			restore := profile.ConfigPatch{}
			if agent.Config.MemoryWriteApproval {
				disable.MemoryWriteApproval = boolPointer(false)
				restore.MemoryWriteApproval = boolPointer(true)
			}
			if agent.Config.SkillsWriteApproval {
				disable.SkillsWriteApproval = boolPointer(false)
				restore.SkillsWriteApproval = boolPointer(true)
			}
			if _, err := s.agents.UpdateConfig(ctx, userID, agentID, disable); err != nil {
				return err
			}
			defer func() { _, _ = s.agents.UpdateConfig(context.Background(), userID, agentID, restore) }()
		}
	}
	traceID := span.SpanContext().TraceID().String()
	_, err = s.repository.AddMessage(ctx, conversationID, models.Message{Role: "user", Content: input, CreatedAt: time.Now().UTC(), Metadata: map[string]any{"run_id": runID, "trace_id": traceID}})
	if err != nil {
		return err
	}
	profilePath, _ := s.agents.Profiles().ProfilePath(agentID)
	result, err := s.runtime.Run(runCtx, runtimeadapter.Request{
		RunID: runID, ProfilePath: profilePath, WorkspacePath: profilePath + "/workspace", ConversationID: conversationID,
		RuntimeSessionID: conversation.RuntimeSessionID, Input: input, Model: agent.Config.Model, Toolsets: toolsets,
	}, func(event runtimeadapter.Event) {
		metrics.Observe(ctx, event)
		if event.RunID == "" {
			event.RunID = runID
		}
		if event.Type == "approval.required" || event.Type == "approval.request" {
			for _, key := range append(event.PatternKeys, event.PatternKey) {
				if key == "memory_write" || key == "skills_write" {
					run.PendingSubsystem = key
					break
				}
			}
		}
		emit(event)
	})
	if err != nil {
		emit(runtimeadapter.Event{Type: "run.failed", RunID: runID, Text: err.Error()})
		return err
	}
	span.SetAttributes(attribute.Int("lumora.response.chars", len([]rune(result.Output))))
	_, err = s.repository.AddMessage(ctx, conversationID, models.Message{Role: "assistant", Content: result.Output, CreatedAt: time.Now().UTC(), Metadata: map[string]any{"run_id": runID, "trace_id": traceID}})
	if err != nil {
		return err
	}
	if result.RuntimeSessionID != "" && result.RuntimeSessionID != conversation.RuntimeSessionID {
		conversation.RuntimeSessionID = result.RuntimeSessionID
		conversation.UpdatedAt = time.Now().UTC()
		if _, err := s.repository.UpdateConversation(ctx, userID, conversation); err != nil {
			return err
		}
	}
	if err := s.agents.Profiles().ReconcileSnapshots(agentID); err != nil {
		return err
	}
	emit(runtimeadapter.Event{Type: "run.completed", RunID: runID, Text: result.Output})
	return nil
}

func (s *Conversations) Stop(runID string) error {
	s.mu.RLock()
	run := s.runs[runID]
	s.mu.RUnlock()
	if run == nil {
		return fmt.Errorf("run not found")
	}
	run.Cancel()
	return nil
}

func (s *Conversations) ResolveApproval(ctx context.Context, userID string, agentID string, runID string, choice string, resolveAll bool, subsystem string) error {
	s.mu.RLock()
	run := s.runs[runID]
	s.mu.RUnlock()
	if run == nil || run.AgentID != agentID {
		return fmt.Errorf("run not found")
	}
	if subsystem == "" {
		subsystem = run.PendingSubsystem
	}
	if choice == "session" || choice == "always" {
		targets := []string{subsystem}
		if (resolveAll || run.Preflight) && len(run.PendingSubsystems) > 0 {
			targets = run.PendingSubsystems
		}
		for _, target := range targets {
			if target == "memory_write" || target == "skills_write" {
				if _, err := s.agents.PersistApproval(ctx, userID, agentID, target, choice); err != nil {
					return err
				}
			}
		}
	}
	if run.Preflight {
		select {
		case run.Approval <- choice:
			return nil
		default:
			return fmt.Errorf("approval was already resolved")
		}
	}
	return s.runtime.ResolveApproval(ctx, runID, choice, resolveAll)
}

func boolPointer(value bool) *bool { return &value }
