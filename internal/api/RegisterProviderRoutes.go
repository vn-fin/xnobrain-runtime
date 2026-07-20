// <Summary>
// RegisterProviderRoutes exposes the filtered 9router connection, model, OAuth, and usage surface.
// </Summary>
package api

import (
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/middlewares"
	"github.com/xno/open-lumora/internal/ninerouter"
)

type oauthAttempt struct {
	CodeVerifier string
	State        string
	RedirectURI  string
}

var oauthAttempts sync.Map

var providerNames = map[string]string{
	"claude": "Claude", "codex": "Codex", "antigravity": "Antigravity",
	"openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Gemini",
}

func (s *Server) RegisterProviderRoutes(app *fiber.App) {
	root := app.Group("/agent-gateway/v1/providers")
	root.Get("/", s.listProviders)
	root.Get("", s.listProviders)
	root.Get("/:provider_id/models", s.providerModels)
	root.Get("/:provider_id/models/:model/reasoning", s.providerReasoning)
	root.Post("/:provider_id/test", s.testProvider)
	root.Post("/:provider_id/connect", s.startProviderConnect)
	root.Get("/:provider_id/connect", s.providerConnectStatus)
	root.Put("/:provider_id/connect", middlewares.RateLimit(s.providerConnectionLimit), s.submitProviderConnect)
	root.Patch("/:provider_id/update", s.updateProvider)
	root.Post("/:provider_id/disconnect", s.providerDisconnect)
}

func (s *Server) listProviders(c fiber.Ctx) error {
	connections, routerErr := s.router.ListConnections(c.Context())
	models := []ninerouter.Model{}
	if routerErr == nil {
		models, _ = s.router.ListModels(c.Context())
	}
	result := make([]map[string]any, 0, len(ninerouter.Supported))
	for _, provider := range ninerouter.Supported {
		connection := connectionFor(connections, provider)
		connected := connection.ID != "" && (connection.IsActive == nil || *connection.IsActive)
		mode := "cli"
		if ninerouter.APIKeyProviders[provider] {
			mode = "api-key"
		}
		status := "disconnected"
		if connected {
			status = "connected"
		}
		if connection.LastError != "" {
			status = "error"
		}
		result = append(result, map[string]any{
			"id": provider, "display_name": providerNames[provider], "provider_type": provider,
			"description":     "Credentials are managed by the local 9router runtime.",
			"connection_mode": mode, "connected": connected, "status": status,
			"last_test_status": connection.TestStatus, "default_model": connection.DefaultModel,
			"available_models": modelsForProvider(models, provider),
		})
	}
	return send(c, fiber.StatusOK, result, "providers retrieved successfully")
}

func (s *Server) providerModels(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	models, err := s.router.ListModels(c.Context())
	if err != nil {
		return sendError(c, err)
	}
	items := []map[string]any{{"id": "auto", "reasoning": []string{"low", "medium", "high"}}}
	for _, model := range modelsForProvider(models, provider) {
		items = append(items, map[string]any{"id": model, "reasoning": []string{"low", "medium", "high"}})
	}
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "default_model": "auto", "models": items}, "models retrieved successfully")
}

func (s *Server) providerReasoning(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "model": c.Params("model"), "reasoning": []string{"low", "medium", "high"}}, "reasoning options retrieved successfully")
}

func (s *Server) testProvider(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	connections, err := s.router.ListConnections(c.Context())
	if err != nil {
		return sendError(c, err)
	}
	connection := connectionFor(connections, provider)
	if connection.ID == "" {
		return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "healthy": false, "status": "not_connected", "message": "Provider is not connected"}, "provider tested")
	}
	valid, message, err := s.router.TestConnection(c.Context(), connection.ID)
	if err != nil {
		return sendError(c, err)
	}
	status := "unhealthy"
	if valid {
		status = "healthy"
	}
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "healthy": valid, "status": status, "message": message}, "provider tested")
}

func (s *Server) startProviderConnect(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	if ninerouter.APIKeyProviders[provider] {
		return send(c, fiber.StatusOK, apiKeyConnectInfo(provider), "provider connection ready")
	}
	redirectURI := "http://localhost:20128/callback"
	if provider == "codex" {
		redirectURI = "http://localhost:1455/auth/callback"
	}
	var auth struct {
		AuthURL      string `json:"authUrl"`
		CodeVerifier string `json:"codeVerifier"`
		State        string `json:"state"`
	}
	if err := s.router.OAuth(c.Context(), http.MethodGet, provider, "authorize", url.Values{"redirect_uri": {redirectURI}}, nil, &auth); err != nil {
		return sendError(c, err)
	}
	if strings.TrimSpace(auth.AuthURL) == "" {
		return sendError(c, fmt.Errorf("9router did not return an authorization URL"))
	}
	oauthAttempts.Store(provider, oauthAttempt{CodeVerifier: auth.CodeVerifier, State: auth.State, RedirectURI: redirectURI})
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "provider_type": provider, "connection_mode": "cli", "required_client_action": "submit_response", "login_url": auth.AuthURL, "verification_url": auth.AuthURL, "instructions": "Authorize in the browser, then paste the complete callback URL.", "text_label": "Callback URL", "status": "waiting_for_user"}, "provider connection started")
}

func (s *Server) providerConnectStatus(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	connections, routerErr := s.router.ListConnections(c.Context())
	if routerErr != nil {
		return sendError(c, routerErr)
	}
	connection := connectionFor(connections, provider)
	models, _ := s.router.ListModels(c.Context())
	mode := "cli"
	if ninerouter.APIKeyProviders[provider] {
		mode = "api-key"
	}
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "connection_mode": mode, "connected": connection.ID != "", "status": map[bool]string{true: "connected", false: "disconnected"}[connection.ID != ""], "default_model": connection.DefaultModel, "available_models": modelsForProvider(models, provider)}, "provider status retrieved")
}

func (s *Server) submitProviderConnect(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		Text         string `json:"text"`
		ResponseText string `json:"response_text"`
		Token        string `json:"token"`
		APIKey       string `json:"api_key"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	value := firstText(request.Text, request.ResponseText, request.Token, request.APIKey)
	if ninerouter.APIKeyProviders[provider] {
		_, err := s.replaceAPIKeyConnection(c, provider, value, "", "")
		if err != nil {
			return sendError(c, err)
		}
		return send(c, fiber.StatusOK, apiKeyConnectInfo(provider), "provider connected")
	}
	attemptValue, ok := oauthAttempts.Load(provider)
	if !ok {
		return sendError(c, fmt.Errorf("provider connection has not been started"))
	}
	attempt := attemptValue.(oauthAttempt)
	code, state, err := parseOAuthCallback(value)
	if err != nil {
		return sendError(c, err)
	}
	if state != "" && attempt.State != "" && state != attempt.State {
		return sendError(c, fmt.Errorf("provider callback state does not match"))
	}
	if state == "" {
		state = attempt.State
	}
	var exchange struct {
		Success    bool                  `json:"success"`
		Connection ninerouter.Connection `json:"connection"`
	}
	body := map[string]string{"code": code, "redirectUri": attempt.RedirectURI, "codeVerifier": attempt.CodeVerifier, "state": state}
	if err := s.router.OAuth(c.Context(), http.MethodPost, provider, "exchange", nil, body, &exchange); err != nil {
		return sendError(c, err)
	}
	if !exchange.Success {
		return sendError(c, fmt.Errorf("provider authorization was not accepted"))
	}
	if err := s.router.EnsureAuto(c.Context()); err != nil {
		return sendError(c, err)
	}
	oauthAttempts.Delete(provider)
	if shouldReplaceProviderConnection(c) {
		_ = s.deleteOtherConnections(c, provider, exchange.Connection.ID)
	}
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "connection_mode": "cli", "connected": true, "status": "connected"}, "provider connected")
}

func (s *Server) updateProvider(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	if !ninerouter.APIKeyProviders[provider] {
		return sendError(c, fmt.Errorf("OAuth provider credentials must be updated through connect"))
	}
	var request struct {
		APIKey       string `json:"api_key"`
		DisplayName  string `json:"display_name"`
		DefaultModel string `json:"default_model"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	connection, err := s.replaceAPIKeyConnection(c, provider, request.APIKey, request.DisplayName, request.DefaultModel)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "connected": true, "status": "connected", "default_model": connection.DefaultModel}, "provider updated")
}

func (s *Server) providerDisconnect(c fiber.Ctx) error {
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return sendError(c, err)
	}
	connections, err := s.router.ListConnections(c.Context())
	if err != nil {
		return sendError(c, err)
	}
	for _, connection := range connections {
		if connection.Provider == provider {
			if err := s.router.DeleteConnection(c.Context(), connection.ID); err != nil {
				return sendError(c, err)
			}
		}
	}
	oauthAttempts.Delete(provider)
	return send(c, fiber.StatusOK, map[string]any{"provider_id": provider, "connected": false, "status": "disconnected"}, "provider disconnected")
}

func (s *Server) replaceAPIKeyConnection(c fiber.Ctx, provider, key, name, defaultModel string) (ninerouter.Connection, error) {
	connection, err := s.router.CreateAPIKey(c.Context(), provider, key, name, defaultModel)
	if err != nil {
		return ninerouter.Connection{}, err
	}
	if shouldReplaceProviderConnection(c) {
		return connection, s.deleteOtherConnections(c, provider, connection.ID)
	}
	return connection, nil
}

func shouldReplaceProviderConnection(c fiber.Ctx) bool {
	value := strings.TrimSpace(strings.ToLower(c.Query("replace", "true")))
	return value != "false" && value != "0" && value != "no"
}

func (s *Server) deleteOtherConnections(c fiber.Ctx, provider, keep string) error {
	connections, err := s.router.ListConnections(c.Context())
	if err != nil {
		return err
	}
	for _, item := range connections {
		if item.Provider == provider && item.ID != keep {
			if err := s.router.DeleteConnection(c.Context(), item.ID); err != nil {
				return err
			}
		}
	}
	return nil
}

func validProvider(value string) (string, error) {
	value = strings.ToLower(strings.TrimSpace(value))
	for _, item := range ninerouter.Supported {
		if value == item {
			return value, nil
		}
	}
	return "", fmt.Errorf("provider not found")
}
func connectionFor(items []ninerouter.Connection, provider string) ninerouter.Connection {
	for _, item := range items {
		if item.Provider == provider {
			return item
		}
	}
	return ninerouter.Connection{}
}
func apiKeyConnectInfo(provider string) map[string]any {
	return map[string]any{"provider_id": provider, "provider_type": provider, "connection_mode": "api-key", "required_client_action": "submit_text", "instructions": "Enter the provider API key. It is stored by 9router, not Open Lumora.", "text_label": "API key", "status": "waiting_for_user"}
}
func firstText(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}
func parseOAuthCallback(value string) (string, string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", "", fmt.Errorf("callback URL is required")
	}
	if !strings.Contains(value, "://") {
		return value, "", nil
	}
	parsed, err := url.Parse(value)
	if err != nil {
		return "", "", fmt.Errorf("callback URL is invalid")
	}
	if parsed.Query().Get("error") != "" {
		return "", "", fmt.Errorf("provider authorization returned an error")
	}
	code := firstText(parsed.Query().Get("code"), parsed.Query().Get("token"))
	if code == "" {
		return "", "", fmt.Errorf("callback URL does not contain an authorization code")
	}
	return code, strings.TrimSpace(parsed.Query().Get("state")), nil
}
func modelsForProvider(items []ninerouter.Model, provider string) []string {
	aliases := map[string]string{"claude": "cc", "codex": "cx", "antigravity": "ag", "openai": "openai", "anthropic": "anthropic", "gemini": "gemini"}
	result := []string{}
	for _, item := range items {
		owner := item.OwnedBy
		if owner == "" && strings.Contains(item.ID, "/") {
			owner = strings.SplitN(item.ID, "/", 2)[0]
		}
		if owner == aliases[provider] {
			result = append(result, item.ID)
		}
	}
	return result
}
