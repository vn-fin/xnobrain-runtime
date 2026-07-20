// <Summary>
// Team repository tests prove atomic YAML persistence survives process restart and
// remains owner-scoped.
// </Summary>
package repositories

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/models"
)

func TestFileTeamsPersistAcrossRestart(t *testing.T) {
	root := t.TempDir()
	_, repository := newFileRepository(t, root)
	now := time.Date(2026, 7, 20, 12, 0, 0, 0, time.UTC)
	team := models.Team{ID: "team-1", UserID: "local", Name: "Research", OrchestratorID: "agent-1", Members: []models.TeamMember{{AgentID: "agent-2", Role: "researcher", Enabled: true}}, Enabled: true, CreatedAt: now, UpdatedAt: now}
	if _, err := repository.CreateTeam(context.Background(), team); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "teams", "team-1.yaml")); err != nil {
		t.Fatal(err)
	}
	_, reloaded := newFileRepository(t, root)
	teams, err := reloaded.ListTeams(context.Background(), "local")
	if err != nil || len(teams) != 1 || teams[0].Members[0].AgentID != "agent-2" {
		t.Fatalf("teams after restart = %#v, %v", teams, err)
	}
	if _, err := reloaded.GetTeam(context.Background(), "another-user", team.ID); err != ErrNotFound {
		t.Fatalf("cross-owner read error = %v", err)
	}
}
