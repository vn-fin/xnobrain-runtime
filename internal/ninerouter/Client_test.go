// <Summary>
// Client tests lock the 9Router transport contract so empty successful bodies
// cannot be mistaken for disconnected providers or empty model catalogs.
// </Summary>
package ninerouter

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClientRejectsEmptySuccessfulResponse(t *testing.T) {
	router := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(router.Close)

	client := New(router.URL, t.TempDir())
	_, err := client.ListConnections(context.Background())
	if err == nil || !strings.Contains(err.Error(), "empty response") {
		t.Fatalf("ListConnections error = %v", err)
	}
}

func TestClientDecodesProviderAndOAuthContracts(t *testing.T) {
	router := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		switch request.URL.Path {
		case "/api/providers":
			_, _ = writer.Write([]byte(`{"connections":[{"id":"codex-1","provider":"codex"}]}`))
		case "/api/oauth/codex/authorize":
			_, _ = writer.Write([]byte(`{"authUrl":"https://auth.example/authorize","state":"state","codeVerifier":"verifier"}`))
		default:
			writer.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(router.Close)

	client := New(router.URL, t.TempDir())
	connections, err := client.ListConnections(context.Background())
	if err != nil || len(connections) != 1 || connections[0].Provider != "codex" {
		t.Fatalf("connections = %#v, %v", connections, err)
	}
	var auth struct {
		AuthURL string `json:"authUrl"`
	}
	if err := client.OAuth(context.Background(), http.MethodGet, "codex", "authorize", nil, nil, &auth); err != nil {
		t.Fatal(err)
	}
	if auth.AuthURL != "https://auth.example/authorize" {
		t.Fatalf("auth URL = %q", auth.AuthURL)
	}
}
