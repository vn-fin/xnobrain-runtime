// <Summary>
// RateLimit tests verify HTTP 429 envelopes and standard quota headers.
// </Summary>
package middlewares

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/limits"
)

func TestRateLimitRejectsExceededRequest(t *testing.T) {
	app := fiber.New()
	app.Post("/agents", RateLimit(func(fiber.Ctx) (limits.Decision, error) {
		return limits.Check(limits.AgentsCreated, 4, 4, 1, nil), nil
	}), func(c fiber.Ctx) error { return c.SendStatus(http.StatusCreated) })
	response, err := app.Test(httptest.NewRequest(http.MethodPost, "/agents", nil))
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("status = %d", response.StatusCode)
	}
	if response.Header.Get("RateLimit-Limit") != "4" || response.Header.Get("RateLimit-Remaining") != "0" {
		t.Fatalf("headers = %#v", response.Header)
	}
}

func TestRateLimitReportsCapacityAfterAcceptedRequest(t *testing.T) {
	app := fiber.New()
	app.Post("/agents", RateLimit(func(fiber.Ctx) (limits.Decision, error) {
		return limits.Check(limits.AgentsCreated, 1, 0, 1, nil), nil
	}), func(c fiber.Ctx) error { return c.SendStatus(http.StatusCreated) })
	response, err := app.Test(httptest.NewRequest(http.MethodPost, "/agents", nil))
	if err != nil {
		t.Fatal(err)
	}
	if response.Header.Get("RateLimit-Remaining") != "0" {
		t.Fatalf("remaining = %q", response.Header.Get("RateLimit-Remaining"))
	}
}
