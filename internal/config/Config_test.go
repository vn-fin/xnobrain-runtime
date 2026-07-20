// <Summary>
// Config tests lock local as the default startup mode and derive private runtime
// routes from the single enterprise gateway URL.
// </Summary>
package config

import "testing"

func TestLoadDerivesGatewayRuntimeRoutes(t *testing.T) {
	t.Setenv("START_MODE", "local")
	t.Setenv("CONTROL_GATEWAY_URL", "http://gateway:3100/")
	t.Setenv("HERMES_RUNTIME_URL", "")
	t.Setenv("NINE_ROUTER_URL", "")
	t.Setenv("DATA_DIR", t.TempDir())
	t.Setenv("FRONTEND_DIR", t.TempDir())
	config, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if config.StartMode != "local" || config.HermesRuntimeURL != "http://gateway:3100/runtime" || config.NineRouterURL != "http://gateway:3100/nine-router" {
		t.Fatalf("unexpected gateway config: %+v", config)
	}
}

func TestLoadRejectsUnknownStartMode(t *testing.T) {
	t.Setenv("START_MODE", "desktop")
	if _, err := Load(); err == nil {
		t.Fatal("expected invalid START_MODE to fail")
	}
}
