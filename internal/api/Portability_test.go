// <Summary>
// Portability API tests prove the root HTTP surface streams exports and accepts
// the same bundle for inspect, dry-run, and collision-safe atomic apply.
// File: Portability_test.go
// Test: TestPortabilityHTTPRoundTrip
// </Summary>
package api

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/xno/open-lumora/internal/config"
	"github.com/xno/open-lumora/internal/edition"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	"github.com/xno/open-lumora/services/agents"
	"github.com/xno/open-lumora/services/portability"
)

func TestPortabilityHTTPRoundTrip(t *testing.T) {
	dataRoot := t.TempDir()
	profiles, err := profile.NewManager(dataRoot)
	if err != nil {
		t.Fatal(err)
	}
	repository, err := repositories.NewFile(profiles)
	if err != nil {
		t.Fatal(err)
	}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, err := agentService.Create(context.Background(), "local", "Portable", "")
	if err != nil {
		t.Fatal(err)
	}
	server := NewServer(config.Config{DataDir: dataRoot}, policy, agentService, nil, nil)
	server.SetPortability(portability.NewPortability(agentService, profiles))
	app := NewApp(config.Config{}, server)
	server.RegisterPortabilityRoutes(app)

	exportPayload, _ := json.Marshal(map[string]any{"agent_ids": []string{agent.ID}})
	exportRequest := httptest.NewRequest(http.MethodPost, "/api/v1/bundles/export", bytes.NewReader(exportPayload))
	exportRequest.Header.Set("Content-Type", "application/json")
	exportResponse, err := app.Test(exportRequest)
	if err != nil {
		t.Fatal(err)
	}
	if exportResponse.StatusCode != http.StatusOK || exportResponse.Header.Get("Content-Type") != bundleContentType {
		t.Fatalf("export response status=%d content-type=%q", exportResponse.StatusCode, exportResponse.Header.Get("Content-Type"))
	}
	bundle, err := io.ReadAll(exportResponse.Body)
	if err != nil {
		t.Fatal(err)
	}
	_ = exportResponse.Body.Close()
	if len(bundle) == 0 {
		t.Fatal("export response is empty")
	}

	for _, endpoint := range []string{"inspect", "dry-run"} {
		request := httptest.NewRequest(http.MethodPost, "/api/v1/bundles/"+endpoint, bytes.NewReader(bundle))
		request.Header.Set("Content-Type", bundleContentType)
		response, err := app.Test(request)
		if err != nil {
			t.Fatal(err)
		}
		if response.StatusCode != http.StatusOK {
			payload, _ := io.ReadAll(response.Body)
			t.Fatalf("%s status=%d body=%s", endpoint, response.StatusCode, payload)
		}
		_ = response.Body.Close()
	}

	applyRequest := httptest.NewRequest(http.MethodPost, "/api/v1/bundles/apply", bytes.NewReader(bundle))
	applyRequest.Header.Set("Content-Type", bundleContentType)
	applyResponse, err := app.Test(applyRequest)
	if err != nil {
		t.Fatal(err)
	}
	if applyResponse.StatusCode != http.StatusCreated {
		payload, _ := io.ReadAll(applyResponse.Body)
		t.Fatalf("apply status=%d body=%s", applyResponse.StatusCode, payload)
	}
	_ = applyResponse.Body.Close()
	listed, err := agentService.List(context.Background(), "local")
	if err != nil {
		t.Fatal(err)
	}
	if len(listed) != 2 {
		t.Fatalf("collision-safe apply produced %d agents", len(listed))
	}
}

func TestManagedPortabilityEndpointsRequireTokenAndSupportRollback(t *testing.T) {
	dataRoot := t.TempDir()
	profiles, err := profile.NewManager(dataRoot)
	if err != nil {
		t.Fatal(err)
	}
	repository, err := repositories.NewFile(profiles)
	if err != nil {
		t.Fatal(err)
	}
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200}
	agentService := agents.NewAgents(repository, profiles, policy)
	agent, err := agentService.Create(context.Background(), "member-1", "Portable", "")
	if err != nil {
		t.Fatal(err)
	}
	var bundle bytes.Buffer
	service := portability.NewPortability(agentService, profiles, repository)
	if _, err := service.Export(context.Background(), "member-1", portability.ExportOptions{AgentIDs: []string{agent.ID}}, &bundle); err != nil {
		t.Fatal(err)
	}
	cfg := config.Config{DataDir: dataRoot, ManagedWorkerToken: "worker-secret"}
	server := NewServer(cfg, policy, agentService, nil, nil)
	server.SetPortability(service)
	app := NewApp(cfg, server)
	server.RegisterPortabilityRoutes(app)

	unauthorized := httptest.NewRequest(http.MethodPost, "/internal/v1/imports/import-1/stage", bytes.NewReader(bundle.Bytes()))
	unauthorized.Header.Set("Content-Type", bundleContentType)
	unauthorized.Header.Set("X-Lumora-Owner-ID", "member-1")
	response, err := app.Test(unauthorized)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthorized stage status = %d", response.StatusCode)
	}
	_ = response.Body.Close()

	call := func(path string, body []byte) *http.Response {
		request := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(body))
		request.Header.Set("Authorization", "Bearer worker-secret")
		request.Header.Set("X-Lumora-Owner-ID", "member-1")
		request.Header.Set("Content-Type", bundleContentType)
		response, err := app.Test(request)
		if err != nil {
			t.Fatal(err)
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			payload, _ := io.ReadAll(response.Body)
			t.Fatalf("%s status=%d body=%s", path, response.StatusCode, payload)
		}
		return response
	}
	_ = call("/internal/v1/imports/import-1/stage", bundle.Bytes()).Body.Close()
	listed, _ := agentService.List(context.Background(), "member-1")
	if len(listed) != 1 {
		t.Fatalf("stage exposed %d agents", len(listed))
	}
	_ = call("/internal/v1/imports/import-1/commit", nil).Body.Close()
	listed, _ = agentService.List(context.Background(), "member-1")
	if len(listed) != 2 {
		t.Fatalf("commit exposed %d agents", len(listed))
	}
	_ = call("/internal/v1/imports/import-1/rollback", nil).Body.Close()
	listed, _ = agentService.List(context.Background(), "member-1")
	if len(listed) != 1 {
		t.Fatalf("rollback left %d agents", len(listed))
	}
}
