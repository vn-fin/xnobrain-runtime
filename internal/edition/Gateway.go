// <Summary>
// Gateway resolves authenticated extension entitlements from the private Open
// Lumora gateway while preserving unrestricted OSS access when it is offline.
// </Summary>
package edition

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Gateway struct {
	endpoint string
	client   *http.Client
	fallback Policy
}

func NewGateway(endpoint string, fallback Policy) (*Gateway, error) {
	parsed, err := url.Parse(strings.TrimSpace(endpoint))
	if err != nil || parsed.Host == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
		return nil, fmt.Errorf("control gateway requires a valid HTTP(S) URL without embedded credentials")
	}
	if fallback == nil {
		return nil, fmt.Errorf("control gateway fallback policy is required")
	}
	return &Gateway{endpoint: strings.TrimRight(parsed.String(), "/"), client: &http.Client{Timeout: 2 * time.Second}, fallback: fallback}, nil
}

func (g *Gateway) Name() string { return "gateway-with-unrestricted-oss-fallback" }

func (g *Gateway) Principal(ctx context.Context, authorization string) (Principal, error) {
	return g.fallback.Principal(ctx, authorization)
}

func (g *Gateway) Limits(ctx context.Context, principal Principal) (Limits, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, g.endpoint+"/api/v1/limits/current", nil)
	if err != nil {
		return g.fallback.Limits(ctx, principal)
	}
	response, err := g.client.Do(request)
	if err != nil {
		return g.fallback.Limits(ctx, principal)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return g.fallback.Limits(ctx, principal)
	}
	var envelope struct {
		Success bool `json:"success"`
		Data    struct {
			Limits Limits `json:"limits"`
		} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&envelope); err != nil || !envelope.Success || envelope.Data.Limits.Agents == 0 {
		return g.fallback.Limits(ctx, principal)
	}
	return envelope.Data.Limits, nil
}
