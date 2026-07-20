// <Summary>
// Conversation service tests prove persistent approvals, streaming lifecycle, and profile reload behavior.
// </Summary>
package conversations

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	"github.com/xno/open-lumora/internal/edition"
	agentservice "github.com/xno/open-lumora/services/agents"
)

type approvalTestRuntime struct{}

func (approvalTestRuntime) Run(_ context.Context, request runtimeadapter.Request, emit func(runtimeadapter.Event)) (runtimeadapter.Result, error) {
	emit(runtimeadapter.Event{Type: "assistant.delta", RunID: request.RunID, Text: "done"})
	return runtimeadapter.Result{Output: "done"}, nil
}

func (approvalTestRuntime) ResolveApproval(context.Context, string, string, bool) error {
	return fmt.Errorf("preflight approval must not be sent to CLI runtime")
}

func TestAllowSessionPersistsBothWritePermissionsBeforeRuntimeContinues(t *testing.T) {
	dataDir := t.TempDir()
	profiles, err := profile.NewManager(dataDir)
	if err != nil {
		t.Fatal(err)
	}
	repository := repositories.NewMemory()
	agents := agentservice.NewAgents(repository, profiles, edition.OpenSource{AgentLimit: 4, CronConcurrency: 1})
	agent, err := agents.Create(context.Background(), "local", "Writer", "")
	if err != nil {
		t.Fatal(err)
	}
	service := NewConversations(repository, agents, approvalTestRuntime{})
	conversation, err := service.Create(context.Background(), "local", agent.ID, "approval")
	if err != nil {
		t.Fatal(err)
	}
	events := make(chan runtimeadapter.Event, 8)
	done := make(chan error, 1)
	go func() {
		done <- service.Stream(context.Background(), "local", agent.ID, conversation.ID, "remember this", func(event runtimeadapter.Event) { events <- event })
	}()

	var approval runtimeadapter.Event
	deadline := time.After(2 * time.Second)
	for approval.Type != "approval.request" {
		select {
		case approval = <-events:
		case <-deadline:
			t.Fatal("approval event was not emitted")
		}
	}
	if approval.Type != "approval.request" || len(approval.PatternKeys) != 2 {
		t.Fatalf("unexpected approval event: %#v", approval)
	}
	if err := service.ResolveApproval(context.Background(), "local", agent.ID, approval.RunID, "session", true, ""); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("runtime did not continue after approval")
	}

	path, _ := profiles.ProfilePath(agent.ID)
	payload, err := os.ReadFile(filepath.Join(path, "config.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	configText := string(payload)
	for _, expected := range []string{"memory_write: allow", "skills_write: allow"} {
		if !strings.Contains(configText, expected) {
			t.Fatalf("%q missing from config:\n%s", expected, configText)
		}
	}
	reloaded, err := profile.NewManager(dataDir)
	if err != nil {
		t.Fatal(err)
	}
	config, err := reloaded.ReadConfig(agent.ID)
	if err != nil {
		t.Fatal(err)
	}
	if config.MemoryWriteApproval || config.SkillsWriteApproval {
		t.Fatalf("session approval did not survive reload: %#v", config)
	}
}
