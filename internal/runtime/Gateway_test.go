// <Summary>
// Gateway tests prove that Hermes Runs API events and approval decisions use the same external studio run identifier.
// </Summary>
package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func TestGatewayRunStreamsAndResolvesApproval(t *testing.T) {
	approved := make(chan struct{})
	var approveOnce sync.Once
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer test-token" {
			t.Fatalf("authorization = %q", request.Header.Get("Authorization"))
		}
		switch request.Method + " " + request.URL.Path {
		case "POST /v1/runs":
			writer.WriteHeader(http.StatusAccepted)
			_, _ = writer.Write([]byte(`{"run_id":"hermes-run","status":"started"}`))
		case "GET /v1/runs/hermes-run/events":
			writer.Header().Set("Content-Type", "text/event-stream")
			flusher := writer.(http.Flusher)
			_, _ = fmt.Fprint(writer, "data: {\"event\":\"approval.request\",\"pattern_key\":\"memory_write\",\"allow_permanent\":true}\n\n")
			flusher.Flush()
			select {
			case <-approved:
			case <-request.Context().Done():
				return
			}
			_, _ = fmt.Fprint(writer, "data: {\"event\":\"assistant.delta\",\"delta\":\"done\",\"session_id\":\"session-1\"}\n\n")
			_, _ = fmt.Fprint(writer, "data: {\"event\":\"run.completed\",\"output\":\"done\"}\n\n")
			flusher.Flush()
		case "POST /v1/runs/hermes-run/approval":
			var body map[string]any
			if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			if body["choice"] != "session" {
				t.Fatalf("choice = %#v", body["choice"])
			}
			approveOnce.Do(func() { close(approved) })
			_, _ = writer.Write([]byte(`{"run_id":"hermes-run","choice":"session","resolved":1}`))
		default:
			t.Fatalf("unexpected request %s %s", request.Method, request.URL.Path)
		}
	}))
	t.Cleanup(server.Close)

	gateway := NewRemoteGateway(server.URL, "test-token", time.Second)
	events := make(chan Event, 8)
	result := make(chan Result, 1)
	errors := make(chan error, 1)
	go func() {
		value, err := gateway.Run(context.Background(), Request{RunID: "studio-run", ConversationID: "session-1", Input: "hello"}, func(event Event) { events <- event })
		result <- value
		errors <- err
	}()

	select {
	case event := <-events:
		if event.Type != "approval.request" || event.RunID != "studio-run" || event.PatternKey != "memory_write" {
			t.Fatalf("event = %#v", event)
		}
	case <-time.After(time.Second):
		t.Fatal("approval event was not streamed")
	}
	if err := gateway.ResolveApproval(context.Background(), "studio-run", "session", true); err != nil {
		t.Fatal(err)
	}
	if err := <-errors; err != nil {
		t.Fatal(err)
	}
	got := <-result
	if got.Output != "done" || got.RuntimeSessionID != "session-1" {
		t.Fatalf("result = %#v", got)
	}
}
