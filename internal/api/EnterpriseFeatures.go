// <Summary>
// EnterpriseFeatures proxies the signed-in tenant's plan capabilities without
// exposing the private Gateway container or allowing it to restrict OSS use.
// </Summary>
package api

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gofiber/fiber/v3"
)

type enterprisePlan struct {
	PlanID                 string          `json:"plan_id"`
	Capabilities           map[string]bool `json:"capabilities"`
	HardwareClass          string          `json:"hardware_class"`
	TelemetryRetentionDays int             `json:"telemetry_retention_days"`
}

func (s *Server) EnterpriseFeatures(c fiber.Ctx) error {
	authorization := strings.TrimSpace(c.Get(fiber.HeaderAuthorization))
	if authorization == "" {
		return sendError(c, fiber.NewError(fiber.StatusUnauthorized, "enterprise features require login"))
	}
	endpoint := strings.TrimRight(strings.TrimSpace(s.config.ControlGatewayURL), "/")
	if endpoint == "" {
		return sendError(c, fiber.NewError(fiber.StatusServiceUnavailable, "enterprise gateway is not configured"))
	}
	request, err := http.NewRequestWithContext(c.Context(), http.MethodGet, endpoint+"/api/v1/plans/current", nil)
	if err != nil {
		return sendError(c, fmt.Errorf("create enterprise feature request: %w", err))
	}
	request.Header.Set(fiber.HeaderAuthorization, authorization)
	request.Header.Set(fiber.HeaderAccept, "application/json")
	response, err := (&http.Client{Timeout: 3 * time.Second}).Do(request)
	if err != nil {
		return sendError(c, fiber.NewError(fiber.StatusServiceUnavailable, "enterprise gateway is unavailable"))
	}
	defer response.Body.Close()
	if response.StatusCode == fiber.StatusUnauthorized || response.StatusCode == fiber.StatusForbidden {
		return sendError(c, fiber.NewError(fiber.StatusUnauthorized, "login is invalid or expired"))
	}
	if response.StatusCode != fiber.StatusOK {
		return sendError(c, fiber.NewError(fiber.StatusBadGateway, "enterprise gateway rejected the feature request"))
	}
	var payload struct {
		Data enterprisePlan `json:"data"`
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 1024*1024))
	if err := decoder.Decode(&payload); err != nil {
		return sendError(c, fiber.NewError(fiber.StatusBadGateway, "enterprise gateway returned an invalid plan"))
	}
	if payload.Data.Capabilities == nil {
		payload.Data.Capabilities = map[string]bool{}
	}
	return send(c, fiber.StatusOK, payload.Data, "enterprise features resolved")
}
