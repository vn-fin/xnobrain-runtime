// <Summary>
// Team API tests prove stable envelopes and rate-limit middleware on saved-team
// creation without depending on a live Hermes runtime.
// </Summary>
package api

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/xno/open-lumora/internal/config"
	"github.com/xno/open-lumora/internal/repositories"
	"github.com/xno/open-lumora/pkg/edition"
	"github.com/xno/open-lumora/services/teams"
)

type apiTeamOwner struct{}

func (apiTeamOwner) EnsureOwned(context.Context, string, string) error { return nil }

type apiTeamDelegator struct{}

func (apiTeamDelegator) Delegate(context.Context, string, string, string, teams.DelegationPolicy) (string, error) {
	return "summary", nil
}

func TestTeamRoutesEnforcePlanLimit(t *testing.T) {
	policy := edition.OpenSource{TeamLimit: 1, AgentsPerTeam: 2, DelegatedWorkers: 1, DelegationDepth: 1}
	service := teams.NewTeams(repositories.NewMemory(), apiTeamOwner{}, apiTeamDelegator{}, policy)
	server := NewServer(config.Config{}, policy, nil, nil, nil)
	server.SetTeams(service)
	app := NewApp(config.Config{}, server)
	server.RegisterTeamRoutes(app)
	payload := []byte(`{"name":"Research","orchestrator_id":"agent1","members":[{"agent_id":"agent2","role":"researcher","allowed_tools":["web"],"enabled":true}],"enabled":true}`)
	first, err := app.Test(httptest.NewRequest(http.MethodPost, "/api/v1/teams/", bytes.NewReader(payload)))
	if err != nil {
		t.Fatal(err)
	}
	if first.StatusCode != http.StatusCreated {
		t.Fatalf("first team status = %d", first.StatusCode)
	}
	second, err := app.Test(httptest.NewRequest(http.MethodPost, "/api/v1/teams/", bytes.NewReader(payload)))
	if err != nil {
		t.Fatal(err)
	}
	if second.StatusCode != http.StatusTooManyRequests || second.Header.Get("RateLimit-Limit") != "1" {
		t.Fatalf("second team status = %d, headers = %v", second.StatusCode, second.Header)
	}
	listed, err := app.Test(httptest.NewRequest(http.MethodGet, "/api/v1/teams/", nil))
	if err != nil || listed.StatusCode != http.StatusOK {
		t.Fatalf("list status = %d, error = %v", listed.StatusCode, err)
	}
}
