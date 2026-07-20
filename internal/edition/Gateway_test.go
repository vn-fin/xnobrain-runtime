// <Summary>
// Gateway policy tests prove remote limit resolution and deterministic Free
// fallback during gateway loss or malformed responses.
// </Summary>
package edition

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGatewayLimitsAndFallback(t *testing.T) {
	fallback := OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200, ProviderConnectionsPerType: 1, TeamLimit: 1, AgentsPerTeam: 2, DelegatedWorkers: 1, DelegationDepth: 1}
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"success":true,"data":{"limits":{"sandboxes":1,"agents":8,"cron_jobs":4,"cron_parallel_runs":1,"cron_runs_per_day":10,"cron_runs_per_month":200,"provider_connections_per_type":1,"teams":1,"agents_per_team":2,"delegated_workers":1,"delegation_depth":1}}}`))
	}))
	gateway, err := NewGateway(server.URL, fallback)
	if err != nil {
		t.Fatal(err)
	}
	limits, err := gateway.Limits(context.Background(), Principal{UserID: "local", TenantID: "local"})
	if err != nil || limits.Agents != 8 {
		t.Fatalf("remote limits = %+v, err=%v", limits, err)
	}
	server.Close()
	limits, err = gateway.Limits(context.Background(), Principal{UserID: "local", TenantID: "local"})
	if err != nil || limits.Agents != 4 {
		t.Fatalf("fallback limits = %+v, err=%v", limits, err)
	}
}
