// <Summary>
// Deployment exposes a non-secret summary of local/cloud startup and checks the
// configured Hermes transport for the personal-user settings and runtime UI.
// </Summary>
package api

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/gofiber/fiber/v3"
)

func (s *Server) Deployment(c fiber.Ctx) error {
	transport := "local-cli"
	if s.config.HermesRuntimeURL != "" {
		transport = "gateway-runtime"
	} else if strings.EqualFold(s.config.HermesRuntimeMode, "gateway") {
		transport = "local-gateway-process"
	}
	return send(c, fiber.StatusOK, map[string]any{
		"mode": s.config.StartMode, "gateway_configured": s.config.ControlGatewayURL != "",
		"runtime_transport": transport, "managed_cloud": s.config.DeviceCloudURL != "",
	}, "deployment mode retrieved")
}

func (s *Server) runtimeHealth(ctx context.Context) (int, string, error) {
	if s.config.HermesRuntimeURL == "" {
		return 0, "local-cli", fmt.Errorf("remote Hermes runtime is not configured")
	}
	requestContext, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(requestContext, http.MethodGet, strings.TrimRight(s.config.HermesRuntimeURL, "/")+"/health", nil)
	if err != nil {
		return 0, "gateway-runtime", err
	}
	if token := strings.TrimSpace(s.config.HermesRuntimeToken); token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return 0, "gateway-runtime", err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return response.StatusCode, "gateway-runtime", fmt.Errorf("Hermes runtime health returned HTTP %d", response.StatusCode)
	}
	return response.StatusCode, "gateway-runtime", nil
}
