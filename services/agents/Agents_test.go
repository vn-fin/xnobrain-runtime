// <Summary>
// Package services tests open-source limits and persisted agent config behavior.
// File: Agents_test.go
// Tests:
//   - Open-source agent limit
//
// </Summary>
package agents

import (
	"context"
	"testing"

	"github.com/xno/open-lumora/internal/edition"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
)

func TestOpenSourceAgentLimit(t *testing.T) {
	profiles, err := profile.NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	service := NewAgents(repositories.NewMemory(), profiles, edition.OpenSource{AgentLimit: 4, CronConcurrency: 1})
	for index := 0; index < 4; index++ {
		if _, err := service.Create(context.Background(), "local", "Agent", ""); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := service.Create(context.Background(), "local", "Fifth", ""); err == nil {
		t.Fatal("fifth agent was accepted")
	}
}
