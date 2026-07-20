// <Summary>
// Package ninerouter provides the credential-safe 9router HTTP adapter used by provider endpoints and Hermes profiles.
// File: Client.go
// </Summary>
package ninerouter

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var (
	Supported       = []string{"claude", "codex", "antigravity", "openai", "anthropic", "gemini"}
	APIKeyProviders = map[string]bool{"openai": true, "anthropic": true, "gemini": true}
	OAuthProviders  = map[string]bool{"claude": true, "codex": true, "antigravity": true}
)

type Client struct {
	baseURL string
	dataDir string
	http    *http.Client
}

type Connection struct {
	ID           string `json:"id"`
	Provider     string `json:"provider"`
	AuthType     string `json:"authType"`
	Name         string `json:"name"`
	IsActive     *bool  `json:"isActive"`
	DefaultModel string `json:"defaultModel"`
	TestStatus   string `json:"testStatus"`
	LastError    string `json:"lastError"`
}

type Model struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	OwnedBy string `json:"owned_by"`
}

func New(baseURL, dataDir string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		dataDir: dataDir,
		http:    &http.Client{Timeout: 30 * time.Second},
	}
}

// APIToken returns the local 9router CLI token used by its OpenAI-compatible API.
func (c *Client) APIToken() (string, error) { return c.cliToken() }

func (c *Client) ListConnections(ctx context.Context) ([]Connection, error) {
	var payload struct {
		Connections []Connection `json:"connections"`
	}
	if err := c.request(ctx, http.MethodGet, "/api/providers", nil, &payload); err != nil {
		return nil, err
	}
	return payload.Connections, nil
}

func (c *Client) CreateAPIKey(ctx context.Context, provider, apiKey, name, defaultModel string) (Connection, error) {
	if !APIKeyProviders[provider] || strings.TrimSpace(apiKey) == "" {
		return Connection{}, fmt.Errorf("provider and api_key are required")
	}
	body := map[string]string{"provider": provider, "apiKey": strings.TrimSpace(apiKey), "name": strings.TrimSpace(name)}
	if body["name"] == "" {
		body["name"] = provider
	}
	if strings.TrimSpace(defaultModel) != "" {
		body["defaultModel"] = strings.TrimSpace(defaultModel)
	}
	var payload struct {
		Connection Connection `json:"connection"`
	}
	if err := c.request(ctx, http.MethodPost, "/api/providers", body, &payload); err != nil {
		return Connection{}, err
	}
	if err := c.EnsureAuto(ctx); err != nil {
		return Connection{}, err
	}
	return payload.Connection, nil
}

func (c *Client) DeleteConnection(ctx context.Context, id string) error {
	if strings.TrimSpace(id) == "" {
		return fmt.Errorf("connection id is required")
	}
	if err := c.request(ctx, http.MethodDelete, "/api/providers/"+url.PathEscape(id), nil, nil); err != nil {
		return err
	}
	return c.EnsureAuto(ctx)
}

func (c *Client) TestConnection(ctx context.Context, id string) (bool, string, error) {
	var payload struct {
		Valid bool   `json:"valid"`
		Error string `json:"error"`
	}
	if err := c.request(ctx, http.MethodPost, "/api/providers/"+url.PathEscape(id)+"/test", map[string]any{}, &payload); err != nil {
		return false, "", err
	}
	return payload.Valid, payload.Error, nil
}

func (c *Client) ListModels(ctx context.Context) ([]Model, error) {
	var payload struct {
		Data []Model `json:"data"`
	}
	if err := c.request(ctx, http.MethodGet, "/v1/models?kind=llm", nil, &payload); err != nil {
		return nil, err
	}
	return payload.Data, nil
}

func (c *Client) OAuth(ctx context.Context, method, provider, action string, query url.Values, body any, output any) error {
	if !OAuthProviders[provider] {
		return fmt.Errorf("unsupported OAuth provider")
	}
	path := "/api/oauth/" + url.PathEscape(provider) + "/" + url.PathEscape(action)
	if len(query) > 0 {
		path += "?" + query.Encode()
	}
	return c.request(ctx, method, path, body, output)
}

// EnsureAuto keeps 9router's auto combo synchronized with active providers.
func (c *Client) EnsureAuto(ctx context.Context) error {
	connections, err := c.ListConnections(ctx)
	if err != nil {
		return err
	}
	active := map[string]bool{}
	for _, connection := range connections {
		if connection.IsActive == nil || *connection.IsActive {
			active[connection.Provider] = true
		}
	}
	models, err := c.ListModels(ctx)
	if err != nil {
		return err
	}
	aliases := map[string]string{"claude": "cc", "codex": "cx", "antigravity": "ag", "openai": "openai", "anthropic": "anthropic", "gemini": "gemini"}
	desired := []string{}
	for _, model := range models {
		modelOwner := model.OwnedBy
		if modelOwner == "" && strings.Contains(model.ID, "/") {
			modelOwner = strings.SplitN(model.ID, "/", 2)[0]
		}
		for provider, owner := range aliases {
			if active[provider] && modelOwner == owner && model.ID != "auto" {
				desired = append(desired, model.ID)
			}
		}
	}
	var payload struct {
		Combos []struct {
			ID     string   `json:"id"`
			Name   string   `json:"name"`
			Models []string `json:"models"`
		} `json:"combos"`
	}
	if err := c.request(ctx, http.MethodGet, "/api/combos", nil, &payload); err != nil {
		return err
	}
	var currentID string
	var currentModels []string
	for _, combo := range payload.Combos {
		if combo.Name == "auto" {
			currentID, currentModels = combo.ID, combo.Models
			break
		}
	}
	if len(desired) == 0 {
		if currentID != "" {
			return c.request(ctx, http.MethodDelete, "/api/combos/"+url.PathEscape(currentID), nil, nil)
		}
		return nil
	}
	if currentID == "" {
		return c.request(ctx, http.MethodPost, "/api/combos", map[string]any{"name": "auto", "models": desired}, nil)
	}
	if equalStrings(currentModels, desired) {
		return nil
	}
	return c.request(ctx, http.MethodPut, "/api/combos/"+url.PathEscape(currentID), map[string]any{"models": desired}, nil)
}

func equalStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func (c *Client) request(ctx context.Context, method, path string, body any, output any) error {
	if c.baseURL == "" {
		return fmt.Errorf("9router URL is not configured")
	}
	if !strings.HasPrefix(path, "/api/") && !strings.HasPrefix(path, "/v1/") {
		return fmt.Errorf("invalid 9router path")
	}
	var reader io.Reader
	if body != nil {
		payload, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(payload)
	}
	request, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return err
	}
	request.Header.Set("Accept", "application/json")
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if token, tokenErr := c.cliToken(); tokenErr == nil && token != "" {
		request.Header.Set("x-9r-cli-token", token)
	}
	response, err := c.http.Do(request)
	if err != nil {
		return fmt.Errorf("9router is unavailable: %w", err)
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(response.Body, 2<<20))
	if err != nil {
		return err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var message struct {
			Error   any    `json:"error"`
			Message string `json:"message"`
		}
		_ = json.Unmarshal(payload, &message)
		text := strings.TrimSpace(message.Message)
		if text == "" {
			text = strings.TrimSpace(string(payload))
		}
		if text == "" {
			text = response.Status
		}
		return fmt.Errorf("9router: %s", text)
	}
	if output != nil && len(payload) > 0 {
		if err := json.Unmarshal(payload, output); err != nil {
			return fmt.Errorf("decode 9router response: %w", err)
		}
	}
	return nil
}

func (c *Client) cliToken() (string, error) {
	machine, err := readOrCreate(filepath.Join(c.dataDir, "machine-id"), nativeMachineID())
	if err != nil {
		return "", err
	}
	secretSeed := make([]byte, 32)
	if _, err := rand.Read(secretSeed); err != nil {
		return "", err
	}
	secret, err := readOrCreate(filepath.Join(c.dataDir, "auth", "cli-secret"), hex.EncodeToString(secretSeed))
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256([]byte(machine + "9r-cli-auth" + secret))
	return hex.EncodeToString(sum[:])[:16], nil
}

func nativeMachineID() string {
	for _, path := range []string{"/var/lib/dbus/machine-id", "/etc/machine-id"} {
		if payload, err := os.ReadFile(path); err == nil && strings.TrimSpace(string(payload)) != "" {
			sum := sha256.Sum256([]byte(strings.ToLower(strings.Join(strings.Fields(string(payload)), ""))))
			return hex.EncodeToString(sum[:])
		}
	}
	host, _ := os.Hostname()
	sum := sha256.Sum256([]byte(strings.ToLower(host)))
	return hex.EncodeToString(sum[:])
}

func readOrCreate(path, generated string) (string, error) {
	if payload, err := os.ReadFile(path); err == nil && strings.TrimSpace(string(payload)) != "" {
		return strings.TrimSpace(string(payload)), nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return "", err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if errors.Is(err, os.ErrExist) {
		payload, readErr := os.ReadFile(path)
		return strings.TrimSpace(string(payload)), readErr
	}
	if err != nil {
		return "", err
	}
	if _, err = file.WriteString(generated + "\n"); err != nil {
		_ = file.Close()
		return "", err
	}
	return generated, file.Close()
}
