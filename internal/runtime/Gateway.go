// <Summary>
// Gateway starts or connects to a Hermes API server for each agent profile and uses the Runs API for streaming, stop, and approval control.
// </Summary>
package runtime

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/rs/zerolog/log"
	safetracing "github.com/xno/open-lumora/internal/tracing"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
)

type gatewayEndpoint struct {
	baseURL string
	token   string
	process *os.Process
}

type gatewayRun struct {
	endpoint *gatewayEndpoint
	runID    string
}

type Gateway struct {
	launcher    string
	timeout     time.Duration
	routerToken string
	remote      *gatewayEndpoint
	client      *http.Client
	mu          sync.Mutex
	profiles    map[string]*gatewayEndpoint
	runs        map[string]gatewayRun
}

func NewGateway(launcher string, timeout time.Duration, routerToken string) *Gateway {
	return &Gateway{launcher: launcher, timeout: timeout, routerToken: routerToken, client: &http.Client{}, profiles: make(map[string]*gatewayEndpoint), runs: make(map[string]gatewayRun)}
}

func NewRemoteGateway(baseURL string, token string, timeout time.Duration) *Gateway {
	gateway := NewGateway("", timeout, "")
	gateway.remote = &gatewayEndpoint{baseURL: strings.TrimRight(baseURL, "/"), token: token}
	return gateway
}

func (g *Gateway) Run(ctx context.Context, request Request, emit func(Event)) (Result, error) {
	runContext, cancel := context.WithTimeout(ctx, g.timeout)
	defer cancel()
	endpoint, err := g.endpoint(runContext, request)
	if err != nil {
		return Result{}, err
	}
	startBody := map[string]any{"input": request.Input, "session_id": request.ConversationID}
	if request.RuntimeSessionID != "" {
		startBody["session_id"] = request.RuntimeSessionID
	}
	if request.Model != "" && request.Model != "auto" {
		startBody["model"] = request.Model
	}
	if len(request.Toolsets) > 0 {
		startBody["toolsets"] = request.Toolsets
	}
	var started struct {
		RunID  string `json:"run_id"`
		Status string `json:"status"`
	}
	if err := g.doJSON(runContext, endpoint, http.MethodPost, "/v1/runs", startBody, &started); err != nil {
		return Result{}, fmt.Errorf("start Hermes run: %w", err)
	}
	if strings.TrimSpace(started.RunID) == "" {
		return Result{}, fmt.Errorf("start Hermes run: response did not include run_id")
	}
	g.mu.Lock()
	g.runs[request.RunID] = gatewayRun{endpoint: endpoint, runID: started.RunID}
	g.mu.Unlock()
	defer func() {
		g.mu.Lock()
		delete(g.runs, request.RunID)
		g.mu.Unlock()
	}()

	streamPath := "/v1/runs/" + url.PathEscape(started.RunID) + "/events"
	req, err := http.NewRequestWithContext(runContext, http.MethodGet, endpoint.baseURL+streamPath, nil)
	if err != nil {
		return Result{}, err
	}
	g.authorize(req, endpoint)
	req.Header.Set("Accept", "text/event-stream")
	resp, err := g.client.Do(req)
	if err != nil {
		return Result{}, fmt.Errorf("stream Hermes run: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return Result{}, responseError(resp)
	}

	var output strings.Builder
	var runError error
	runtimeSessionID := request.RuntimeSessionID
	err = readSSE(resp.Body, func(eventName string, payload map[string]any) {
		eventType := stringValue(payload["event"])
		if eventType == "" {
			eventType = eventName
		}
		if eventType == "message.delta" || eventType == "assistant.delta" {
			output.WriteString(firstString(payload, "delta", "text", "content"))
		}
		if eventType == "run.completed" && output.Len() == 0 {
			output.WriteString(firstString(payload, "output", "content", "text"))
		}
		if eventType == "run.failed" || eventType == "error" || eventType == "run.cancelled" {
			message := firstString(payload, "error", "message", "text")
			if message == "" {
				message = "Hermes run " + eventType
			}
			runError = fmt.Errorf("%s", message)
		}
		if sessionID := firstString(payload, "session_id", "conversation_id"); sessionID != "" {
			runtimeSessionID = sessionID
		}
		emit(Event{
			Type: eventType, RunID: request.RunID,
			Text:           firstString(payload, "delta", "output", "content", "text", "message", "error"),
			PatternKey:     firstString(payload, "pattern_key"),
			PatternKeys:    stringSlice(payload["pattern_keys"]),
			Description:    firstString(payload, "description"),
			Command:        firstString(payload, "command"),
			AllowPermanent: boolValue(payload["allow_permanent"]),
			Payload:        payload,
		})
	})
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(runContext.Err(), context.Canceled) || errors.Is(runContext.Err(), context.DeadlineExceeded) {
			_ = g.stop(context.Background(), endpoint, started.RunID)
		}
		return Result{}, err
	}
	if runError != nil {
		return Result{}, runError
	}
	return Result{Output: strings.TrimSpace(output.String()), RuntimeSessionID: runtimeSessionID}, nil
}

func (g *Gateway) ResolveApproval(ctx context.Context, externalRunID string, choice string, resolveAll bool) error {
	g.mu.Lock()
	run, ok := g.runs[externalRunID]
	g.mu.Unlock()
	if !ok {
		return fmt.Errorf("run not found")
	}
	return g.doJSON(ctx, run.endpoint, http.MethodPost, "/v1/runs/"+url.PathEscape(run.runID)+"/approval", map[string]any{"choice": choice, "resolve_all": resolveAll}, nil)
}

func (g *Gateway) Close() {
	g.mu.Lock()
	defer g.mu.Unlock()
	for profile, endpoint := range g.profiles {
		if endpoint.process != nil {
			_ = endpoint.process.Kill()
		}
		delete(g.profiles, profile)
	}
}

func (g *Gateway) endpoint(ctx context.Context, request Request) (*gatewayEndpoint, error) {
	if g.remote != nil {
		return g.remote, nil
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if endpoint := g.profiles[request.ProfilePath]; endpoint != nil {
		return endpoint, nil
	}
	endpoint, err := g.startProfileGateway(ctx, request)
	if err != nil {
		return nil, err
	}
	g.profiles[request.ProfilePath] = endpoint
	return endpoint, nil
}

func (g *Gateway) startProfileGateway(ctx context.Context, request Request) (*gatewayEndpoint, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	port := listener.Addr().(*net.TCPAddr).Port
	_ = listener.Close()
	tokenBytes := make([]byte, 32)
	if _, err := rand.Read(tokenBytes); err != nil {
		return nil, err
	}
	token := hex.EncodeToString(tokenBytes)
	command := exec.Command(g.launcher)
	command.Dir = request.WorkspacePath
	command.Env = profileEnvironment(request.ProfilePath, g.routerToken)
	command.Env = replaceEnvironment(command.Env, "API_SERVER_ENABLED", "true")
	command.Env = replaceEnvironment(command.Env, "API_SERVER_HOST", "127.0.0.1")
	command.Env = replaceEnvironment(command.Env, "API_SERVER_PORT", fmt.Sprint(port))
	command.Env = replaceEnvironment(command.Env, "API_SERVER_KEY", token)
	command.Env = replaceEnvironment(command.Env, "API_SERVER_MODEL_NAME", "default")
	if err := os.MkdirAll(filepath.Join(request.ProfilePath, "logs"), 0o700); err != nil {
		return nil, err
	}
	logFile, err := os.OpenFile(filepath.Join(request.ProfilePath, "logs", "api-server.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	command.Stdout, command.Stderr = logFile, logFile
	if err := command.Start(); err != nil {
		_ = logFile.Close()
		return nil, fmt.Errorf("start %s: %w", g.launcher, err)
	}
	go func() {
		err := command.Wait()
		_ = logFile.Close()
		log.Info().Bool("unexpected", err != nil).Int("pid", command.Process.Pid).Str("profile_id_hash", safetracing.HashID(filepath.Base(request.ProfilePath))).Msg("Hermes profile API server stopped")
	}()
	endpoint := &gatewayEndpoint{baseURL: fmt.Sprintf("http://127.0.0.1:%d", port), token: token, process: command.Process}
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		for _, path := range []string{"/health", "/v1/health"} {
			healthCtx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
			req, _ := http.NewRequestWithContext(healthCtx, http.MethodGet, endpoint.baseURL+path, nil)
			g.authorize(req, endpoint)
			resp, requestErr := g.client.Do(req)
			cancel()
			if requestErr == nil {
				_ = resp.Body.Close()
				if resp.StatusCode >= 200 && resp.StatusCode < 300 {
					log.Info().Int("pid", command.Process.Pid).Int("port", port).Str("profile_id_hash", safetracing.HashID(filepath.Base(request.ProfilePath))).Msg("Hermes profile API server ready")
					return endpoint, nil
				}
			}
		}
		time.Sleep(150 * time.Millisecond)
	}
	_ = command.Process.Kill()
	return nil, fmt.Errorf("Hermes profile API server did not become ready; see %s", filepath.Join(request.ProfilePath, "logs", "api-server.log"))
}

func (g *Gateway) stop(ctx context.Context, endpoint *gatewayEndpoint, runID string) error {
	return g.doJSON(ctx, endpoint, http.MethodPost, "/v1/runs/"+url.PathEscape(runID)+"/stop", nil, nil)
}

func (g *Gateway) doJSON(ctx context.Context, endpoint *gatewayEndpoint, method string, path string, input any, output any) error {
	var body io.Reader
	if input != nil {
		encoded, err := json.Marshal(input)
		if err != nil {
			return err
		}
		body = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, endpoint.baseURL+path, body)
	if err != nil {
		return err
	}
	g.authorize(req, endpoint)
	if input != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := g.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return responseError(resp)
	}
	if output == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(output)
}

func (g *Gateway) authorize(req *http.Request, endpoint *gatewayEndpoint) {
	req.Header.Set("Authorization", "Bearer "+endpoint.token)
	otel.GetTextMapPropagator().Inject(req.Context(), propagation.HeaderCarrier(req.Header))
}

func responseError(resp *http.Response) error {
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 256*1024))
	return fmt.Errorf("Hermes API returned status %d", resp.StatusCode)
}

func readSSE(reader io.Reader, handle func(string, map[string]any)) error {
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024)
	eventName := ""
	data := make([]string, 0, 2)
	flush := func() {
		if len(data) == 0 {
			eventName = ""
			return
		}
		var payload map[string]any
		if json.Unmarshal([]byte(strings.Join(data, "\n")), &payload) == nil {
			handle(eventName, payload)
		}
		eventName = ""
		data = data[:0]
	}
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			flush()
			continue
		}
		if strings.HasPrefix(line, "event:") {
			eventName = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
		} else if strings.HasPrefix(line, "data:") {
			data = append(data, strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	flush()
	return scanner.Err()
}

func firstString(payload map[string]any, keys ...string) string {
	for _, key := range keys {
		if value := stringValue(payload[key]); value != "" {
			return value
		}
	}
	return ""
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}

func stringSlice(value any) []string {
	values, _ := value.([]any)
	result := make([]string, 0, len(values))
	for _, value := range values {
		if text := stringValue(value); text != "" {
			result = append(result, text)
		}
	}
	return result
}

func boolValue(value any) bool {
	parsed, _ := value.(bool)
	return parsed
}
