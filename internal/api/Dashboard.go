// <Summary>
// Dashboard proxies aggregate-only observability reads through Studio to the
// private enterprise gateway. ClickHouse is never exposed to the browser.
// </Summary>
package api

import (
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gofiber/fiber/v3"
)

func (s *Server) DashboardOverview(c fiber.Ctx) error {
	return s.proxyDashboard(c, "/api/v1/dashboard/overview")
}

func (s *Server) DashboardDependencies(c fiber.Ctx) error {
	return s.proxyDashboard(c, "/api/v1/dashboard/dependencies")
}

func (s *Server) proxyDashboard(c fiber.Ctx, path string) error {
	if strings.TrimSpace(s.config.ControlGatewayURL) == "" {
		return sendError(c, fmt.Errorf("enterprise gateway is not configured"))
	}
	target := strings.TrimRight(s.config.ControlGatewayURL, "/") + path
	if query := string(c.Request().URI().QueryString()); query != "" {
		target += "?" + query
	}
	request, err := http.NewRequestWithContext(c.Context(), http.MethodGet, target, nil)
	if err != nil {
		return sendError(c, err)
	}
	request.Header.Set("Accept", "application/json")
	if authorization := strings.TrimSpace(c.Get("Authorization")); authorization != "" {
		request.Header.Set("Authorization", authorization)
	}
	client := &http.Client{Timeout: 15 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		return c.Status(fiber.StatusBadGateway).JSON(envelope{Success: false, Message: "observability gateway is unavailable", StatusCode: fiber.StatusBadGateway})
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return sendError(c, err)
	}
	c.Set(fiber.HeaderContentType, "application/json; charset=utf-8")
	return c.Status(response.StatusCode).Send(body)
}
