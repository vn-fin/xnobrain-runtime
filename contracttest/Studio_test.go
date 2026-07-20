// <Summary>
// The nested compatibility module proves the enterprise-supported composition
// can import Open Lumora internals deliberately and owns a clean lifecycle.
// </Summary>
package contracttest

import (
	"context"
	"net"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/edition"
	"github.com/xno/open-lumora/internal/studio"
	"github.com/xno/open-lumora/internal/studio/contract"
)

type repository struct{ contract.Repository }

type runtime struct{}

func (runtime) Run(_ context.Context, request contract.Request, _ func(contract.Event)) (contract.Result, error) {
	return contract.Result{Output: request.Input, RuntimeSessionID: request.ConversationID}, nil
}

func (runtime) ResolveApproval(context.Context, string, string, bool) error { return nil }

type connector struct {
	started chan struct{}
	closed  atomic.Int32
}

func (c *connector) Start(ctx context.Context) error {
	close(c.started)
	<-ctx.Done()
	return ctx.Err()
}

func (c *connector) Close() error {
	c.closed.Add(1)
	return nil
}

func TestExternalModuleBuildsApplicationAndOwnsLifecycle(t *testing.T) {
	dataDir := t.TempDir()
	frontendDir := t.TempDir()
	policy := edition.OpenSource{AgentLimit: 4, CronJobLimit: 4, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200, ProviderConnectionsPerType: 1}
	device := &connector{started: make(chan struct{})}
	var runtimeCloses atomic.Int32
	application, err := studio.New(studio.Options{
		Config: studio.Config{Edition: "compatibility", DataDir: dataDir, FrontendDir: frontendDir, CORSAllowedOrigins: []string{"http://localhost"}},
		Policy: policy, Repository: repository{}, Runtime: runtime{}, Connector: device,
		RuntimeClose: func() error { runtimeCloses.Add(1); return nil },
	})
	if err != nil {
		t.Fatal(err)
	}
	response, err := application.App.Test(httptest.NewRequest("GET", "/api/v1/health", nil))
	if err != nil || response.StatusCode != 200 {
		t.Fatalf("health = %v, %v", response, err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- application.Serve(ctx, listener) }()
	select {
	case <-device.started:
	case <-time.After(time.Second):
		t.Fatal("connector did not start")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("serve stop: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("application did not stop")
	}
	if err := application.Close(); err != nil {
		t.Fatal(err)
	}
	if err := application.Close(); err != nil {
		t.Fatal(err)
	}
	if device.closed.Load() != 1 || runtimeCloses.Load() != 1 {
		t.Fatalf("close counts = connector %d runtime %d", device.closed.Load(), runtimeCloses.Load())
	}
}

func TestMissingDependenciesReturnNamedConstructionErrors(t *testing.T) {
	config := studio.Config{DataDir: t.TempDir(), FrontendDir: t.TempDir()}
	if _, err := studio.New(studio.Options{Config: config}); err == nil || !strings.Contains(err.Error(), "policy") {
		t.Fatalf("missing policy error = %v", err)
	}
	policy := edition.OpenSource{}
	if _, err := studio.New(studio.Options{Config: config, Policy: policy}); err == nil || !strings.Contains(err.Error(), "repository") {
		t.Fatalf("missing repository error = %v", err)
	}
	if _, err := studio.New(studio.Options{Config: config, Policy: policy, Repository: repository{}}); err == nil || !strings.Contains(err.Error(), "runtime") {
		t.Fatalf("missing runtime error = %v", err)
	}
}
