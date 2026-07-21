// <Summary>
// EnterpriseFeatures tests authenticated plan capability proxying and proves
// anonymous local users cannot accidentally enable paid UI features.
// </Summary>
package api

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/config"
)

func TestEnterpriseFeaturesRequiresLoginAndProxiesCapabilities(t *testing.T) {
	gateway := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer valid" {
			response.WriteHeader(http.StatusUnauthorized)
			return
		}
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write([]byte(`{"success":true,"data":{"plan_id":"free","capabilities":{"managed_telemetry":true},"hardware_class":"default","telemetry_retention_days":7}}`))
	}))
	defer gateway.Close()
	server := NewServer(config.Config{StartMode: "local", ControlGatewayURL: gateway.URL}, nil, nil, nil, nil)
	app := NewApp(server.config, server)
	app.Get("/api/v1/enterprise/features", server.EnterpriseFeatures)

	response, err := app.Test(httptest.NewRequest(http.MethodGet, "/api/v1/enterprise/features", nil))
	if err != nil || response.StatusCode != fiber.StatusUnauthorized {
		t.Fatalf("anonymous status=%d err=%v", response.StatusCode, err)
	}
	request := httptest.NewRequest(http.MethodGet, "/api/v1/enterprise/features", nil)
	request.Header.Set("Authorization", "Bearer valid")
	response, err = app.Test(request)
	if err != nil || response.StatusCode != fiber.StatusOK {
		t.Fatalf("authenticated status=%d err=%v", response.StatusCode, err)
	}
}
