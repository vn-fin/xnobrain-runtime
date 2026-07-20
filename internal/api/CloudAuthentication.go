// <Summary>
// CloudAuthentication makes cloud Studio fail closed through the Enterprise
// account boundary while leaving static UI and health delivery public.
// </Summary>
package api

import (
	"net/http"
	"strings"
	"time"

	"github.com/gofiber/fiber/v3"
)

func (s *Server) cloudAuthentication(c fiber.Ctx) error {
	if c.Method() == fiber.MethodOptions || !cloudProtectedPath(c.Path()) {
		return c.Next()
	}
	authorization := strings.TrimSpace(c.Get(fiber.HeaderAuthorization))
	if authorization == "" {
		return sendError(c, fiber.NewError(fiber.StatusUnauthorized, "cloud mode requires login"))
	}
	endpoint := strings.TrimRight(strings.TrimSpace(s.config.ControlGatewayURL), "/")
	if endpoint == "" {
		return sendError(c, fiber.NewError(fiber.StatusServiceUnavailable, "cloud authentication gateway is not configured"))
	}
	request, err := http.NewRequestWithContext(c.Context(), http.MethodGet, endpoint+"/api/v1/plans/current", nil)
	if err != nil {
		return sendError(c, fiber.NewError(fiber.StatusServiceUnavailable, "cloud authentication is unavailable"))
	}
	request.Header.Set(fiber.HeaderAuthorization, authorization)
	request.Header.Set(fiber.HeaderAccept, "application/json")
	response, err := (&http.Client{Timeout: 3 * time.Second}).Do(request)
	if err != nil {
		return sendError(c, fiber.NewError(fiber.StatusServiceUnavailable, "cloud authentication is unavailable"))
	}
	defer response.Body.Close()
	if response.StatusCode != fiber.StatusOK {
		return sendError(c, fiber.NewError(fiber.StatusUnauthorized, "cloud login is invalid or expired"))
	}
	return c.Next()
}

func cloudProtectedPath(path string) bool {
	if path == "/api/v1/health" || path == "/agent-gateway/v1/ping" {
		return false
	}
	for _, prefix := range []string{"/api/", "/agent-gateway/", "/conversations/", "/sandboxes/", "/internal/"} {
		if strings.HasPrefix(path, prefix) {
			return true
		}
	}
	return false
}
