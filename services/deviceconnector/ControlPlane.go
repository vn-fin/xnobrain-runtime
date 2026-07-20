// <Summary>
// ControlPlane defines the bounded outbound device protocol and its HTTPS
// implementation. The default client honors standard proxy environment values.
// File: ControlPlane.go
// Types: ControlPlane, HTTPControlPlane
// </Summary>
package deviceconnector

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/xno/open-lumora/internal/device"
	"github.com/xno/open-lumora/internal/studio/contract"
)

type RegistrationChallenge struct {
	Challenge string `json:"challenge"`
}

type RegistrationRequest struct {
	PublicKey string `json:"public_key"`
	Challenge string `json:"challenge"`
	Proof     string `json:"proof"`
	Version   string `json:"version"`
}

type RefreshRequest struct {
	DeviceID     string `json:"device_id"`
	RefreshToken string `json:"refresh_token"`
	Proof        string `json:"proof"`
}

type PollRequest struct {
	DeviceID string `json:"device_id"`
	Cursor   string `json:"cursor,omitempty"`
}

type PollResponse struct {
	Cursor        string                  `json:"cursor"`
	Commands      []device.Command        `json:"commands"`
	Notifications []contract.Notification `json:"notifications"`
	Revoked       bool                    `json:"revoked"`
	Claimed       bool                    `json:"claimed"`
}

type ControlPlane interface {
	Challenge(ctx context.Context, publicKey string) (RegistrationChallenge, error)
	Register(ctx context.Context, request RegistrationRequest) (device.Registration, error)
	Refresh(ctx context.Context, request RefreshRequest) (device.Registration, error)
	Poll(ctx context.Context, token string, request PollRequest) (PollResponse, error)
	Acknowledge(ctx context.Context, token string, receipt device.Receipt) error
}

type HTTPControlPlane struct {
	endpoint string
	client   *http.Client
}

func NewHTTPControlPlane(endpoint string, timeout time.Duration) (*HTTPControlPlane, error) {
	endpoint = strings.TrimRight(strings.TrimSpace(endpoint), "/")
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Hostname() == "" {
		return nil, fmt.Errorf("invalid device cloud endpoint")
	}
	if parsed.Scheme != "https" && !(parsed.Scheme == "http" && isLoopback(parsed.Hostname())) {
		return nil, fmt.Errorf("device cloud endpoint must use HTTPS")
	}
	if timeout <= 0 {
		timeout = 35 * time.Second
	}
	return &HTTPControlPlane{endpoint: endpoint, client: &http.Client{Timeout: timeout, Transport: &http.Transport{Proxy: http.ProxyFromEnvironment, DialContext: (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext, ForceAttemptHTTP2: true, MaxIdleConns: 4, MaxIdleConnsPerHost: 2, IdleConnTimeout: 90 * time.Second}}}, nil
}

func (h *HTTPControlPlane) Challenge(ctx context.Context, publicKey string) (RegistrationChallenge, error) {
	var response RegistrationChallenge
	err := h.request(ctx, http.MethodPost, "/v1/devices/registration/challenge", "", map[string]string{"public_key": publicKey}, &response)
	return response, err
}

func (h *HTTPControlPlane) Register(ctx context.Context, request RegistrationRequest) (device.Registration, error) {
	var response device.Registration
	err := h.request(ctx, http.MethodPost, "/v1/devices/register", "", request, &response)
	return response, err
}

func (h *HTTPControlPlane) Refresh(ctx context.Context, request RefreshRequest) (device.Registration, error) {
	var response device.Registration
	err := h.request(ctx, http.MethodPost, "/v1/devices/token/refresh", "", request, &response)
	return response, err
}

func (h *HTTPControlPlane) Poll(ctx context.Context, token string, request PollRequest) (PollResponse, error) {
	var response PollResponse
	err := h.request(ctx, http.MethodPost, "/v1/devices/commands/poll", token, request, &response)
	return response, err
}

func (h *HTTPControlPlane) Acknowledge(ctx context.Context, token string, receipt device.Receipt) error {
	return h.request(ctx, http.MethodPost, "/v1/devices/commands/status", token, receipt, nil)
}

func (h *HTTPControlPlane) request(ctx context.Context, method string, path string, token string, request any, response any) error {
	payload, err := json.Marshal(request)
	if err != nil {
		return err
	}
	httpRequest, err := http.NewRequestWithContext(ctx, method, h.endpoint+path, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	if token != "" {
		httpRequest.Header.Set("Authorization", "Bearer "+token)
	}
	httpResponse, err := h.client.Do(httpRequest)
	if err != nil {
		return err
	}
	defer httpResponse.Body.Close()
	if httpResponse.StatusCode < 200 || httpResponse.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(httpResponse.Body, 64*1024))
		return fmt.Errorf("control plane returned status %d", httpResponse.StatusCode)
	}
	if response == nil {
		_, _ = io.Copy(io.Discard, io.LimitReader(httpResponse.Body, 64*1024))
		return nil
	}
	decoder := json.NewDecoder(io.LimitReader(httpResponse.Body, 2*1024*1024))
	if err := decoder.Decode(response); err != nil {
		return fmt.Errorf("decode control-plane response: %w", err)
	}
	return nil
}

func isLoopback(host string) bool {
	return strings.EqualFold(host, "localhost") || net.ParseIP(host) != nil && net.ParseIP(host).IsLoopback()
}
