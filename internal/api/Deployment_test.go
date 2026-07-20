// <Summary>
// Deployment API tests keep local/cloud mode reporting non-secret and stable for
// the personal Settings view.
// </Summary>
package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/config"
)

func TestDeploymentSummary(t *testing.T) {
	server := NewServer(config.Config{StartMode: "local", ControlGatewayURL: "http://gateway:3100", HermesRuntimeURL: "http://gateway:3100/runtime"}, nil, nil, nil, nil)
	app := fiber.New()
	app.Get("/api/v1/system/deployment", server.Deployment)
	response, err := app.Test(httptest.NewRequest(http.MethodGet, "/api/v1/system/deployment", nil))
	if err != nil {
		t.Fatal(err)
	}
	var body struct {
		Data struct {
			Mode              string `json:"mode"`
			GatewayConfigured bool   `json:"gateway_configured"`
			RuntimeTransport  string `json:"runtime_transport"`
		} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body.Data.Mode != "local" || !body.Data.GatewayConfigured || body.Data.RuntimeTransport != "gateway-runtime" {
		t.Fatalf("unexpected deployment summary: %+v", body.Data)
	}
}
