// <Summary>
// CloudAuthentication tests prove cloud APIs require a login validated by the
// Enterprise API while local mode and health remain available.
// </Summary>
package api

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/config"
)

func TestCloudAuthenticationFailsClosedAndForwardsValidLogin(t *testing.T) {
	enterprise := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/plans/current" || request.Header.Get("Authorization") != "Bearer valid" {
			response.WriteHeader(http.StatusUnauthorized)
			return
		}
		response.WriteHeader(http.StatusOK)
	}))
	defer enterprise.Close()
	server := NewServer(config.Config{StartMode: "cloud", ControlGatewayURL: enterprise.URL}, nil, nil, nil, nil)
	app := NewApp(server.config, server)
	app.Get("/api/v1/health", func(c fiber.Ctx) error { return c.SendStatus(fiber.StatusOK) })
	app.Get("/api/v1/system/deployment", func(c fiber.Ctx) error { return c.SendStatus(fiber.StatusOK) })
	app.Get("/api/v1/private", func(c fiber.Ctx) error { return c.SendStatus(fiber.StatusOK) })

	response, err := app.Test(httptest.NewRequest(http.MethodGet, "/api/v1/health", nil))
	if err != nil || response.StatusCode != fiber.StatusOK {
		t.Fatalf("health status=%d err=%v", response.StatusCode, err)
	}
	response, err = app.Test(httptest.NewRequest(http.MethodGet, "/api/v1/system/deployment", nil))
	if err != nil || response.StatusCode != fiber.StatusOK {
		t.Fatalf("deployment status=%d err=%v", response.StatusCode, err)
	}
	response, err = app.Test(httptest.NewRequest(http.MethodGet, "/api/v1/private", nil))
	if err != nil || response.StatusCode != fiber.StatusUnauthorized {
		t.Fatalf("anonymous status=%d err=%v", response.StatusCode, err)
	}
	request := httptest.NewRequest(http.MethodGet, "/api/v1/private", nil)
	request.Header.Set("Authorization", "Bearer valid")
	response, err = app.Test(request)
	if err != nil || response.StatusCode != fiber.StatusOK {
		t.Fatalf("authenticated status=%d err=%v", response.StatusCode, err)
	}
}

func TestLocalAuthenticationDoesNotRequireEnterprise(t *testing.T) {
	server := NewServer(config.Config{StartMode: "local"}, nil, nil, nil, nil)
	app := NewApp(server.config, server)
	app.Get("/api/v1/private", func(c fiber.Ctx) error { return c.SendStatus(fiber.StatusOK) })
	response, err := app.Test(httptest.NewRequest(http.MethodGet, "/api/v1/private", nil))
	if err != nil || response.StatusCode != fiber.StatusOK {
		t.Fatalf("local status=%d err=%v", response.StatusCode, err)
	}
}
