// <Summary>
// API rate-limit tests prove provider replacement remains possible while additive accounts obey plan limits.
// </Summary>
package api

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/xno/open-lumora/internal/config"
	"github.com/xno/open-lumora/pkg/edition"
)

func TestProviderConnectionLimitAllowsReplaceButRejectsAdditiveAccount(t *testing.T) {
	router := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/providers" {
			t.Fatalf("unexpected 9router path %s", request.URL.Path)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"connections":[{"id":"codex-1","provider":"codex","isActive":true}]}`))
	}))
	t.Cleanup(router.Close)
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200, ProviderConnectionsPerType: 1}
	server := NewServer(config.Config{NineRouterURL: router.URL, NineRouterDataDir: t.TempDir()}, policy, nil, nil, nil)
	app := NewApp(config.Config{}, server)
	server.RegisterProviderRoutes(app)

	additive, err := app.Test(httptest.NewRequest(http.MethodPut, "/agent-gateway/v1/providers/codex/connect?replace=false", nil))
	if err != nil {
		t.Fatal(err)
	}
	if additive.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("additive status = %d", additive.StatusCode)
	}
	replacement, err := app.Test(httptest.NewRequest(http.MethodPut, "/agent-gateway/v1/providers/codex/connect", nil))
	if err != nil {
		t.Fatal(err)
	}
	if replacement.StatusCode == http.StatusTooManyRequests {
		t.Fatal("provider replacement was rate limited")
	}
}
