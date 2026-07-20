// <Summary>
// Redaction tests inject secrets into span names, attributes, events, status,
// links, and resources and prove only allowlisted operational data is exported.
// File: Redaction_test.go
// Tests: exporter redaction and secure collector endpoint parsing
// </Summary>
package tracing

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

type captureExporter struct {
	mu      sync.Mutex
	payload string
}

func (e *captureExporter) ExportSpans(_ context.Context, spans []sdktrace.ReadOnlySpan) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	var output strings.Builder
	for _, span := range spans {
		_, _ = fmt.Fprintf(&output, "%s|%v|%v|%v|%v", span.Name(), span.Attributes(), span.Events(), span.Links(), span.Status())
		if span.Resource() != nil {
			_, _ = fmt.Fprintf(&output, "|%v", span.Resource().Attributes())
		}
	}
	e.payload += output.String()
	return nil
}

func (*captureExporter) Shutdown(context.Context) error { return nil }

func TestSafeExporterRemovesContentAndRetainsOperationalAllowlist(t *testing.T) {
	const secret = "TOP-SECRET-prompt-token-file-content"
	capture := &captureExporter{}
	provider := sdktrace.NewTracerProvider(
		sdktrace.WithSyncer(NewSafeExporter(capture)),
		sdktrace.WithResource(resource.NewSchemaless(attribute.String("service.name", "open-lumora"), attribute.String("host.path", secret))),
	)
	_, span := provider.Tracer("test").Start(context.Background(), "GET /agents/"+secret, trace.WithLinks(trace.Link{Attributes: []attribute.KeyValue{attribute.String("tool.arguments", secret), attribute.String("quota.resource", "cron")}}))
	span.SetAttributes(
		attribute.String("prompt", secret),
		attribute.String("http.route", "/agents/:agent_id"),
		attribute.String("agent.id_hash", HashID("a12345")),
	)
	span.AddEvent("response", trace.WithAttributes(attribute.String("content", secret)))
	span.RecordError(errors.New(secret))
	span.SetStatus(codes.Error, secret)
	span.End()
	if err := provider.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	capture.mu.Lock()
	payload := capture.payload
	capture.mu.Unlock()
	if strings.Contains(payload, secret) {
		t.Fatalf("exported telemetry leaked secret: %s", payload)
	}
	for _, expected := range []string{"http.route", "/agents/:agent_id", "agent.id_hash", "quota.resource", "service.name"} {
		if !strings.Contains(payload, expected) {
			t.Fatalf("allowlisted field %q missing from %s", expected, payload)
		}
	}
}

func TestCollectorAddressRequiresTLSExceptLoopback(t *testing.T) {
	if _, _, err := collectorAddress("http://collector.example:4317"); err == nil {
		t.Fatal("remote plaintext collector was accepted")
	}
	if address, insecure, err := collectorAddress("http://127.0.0.1:4317"); err != nil || address != "127.0.0.1:4317" || !insecure {
		t.Fatalf("loopback collector rejected: %q %v %v", address, insecure, err)
	}
	if address, insecure, err := collectorAddress("https://collector.example:4317"); err != nil || address != "collector.example:4317" || insecure {
		t.Fatalf("TLS collector rejected: %q %v %v", address, insecure, err)
	}
	headers := otlpHeaders("authorization=Bearer%20token,x-device=device-1,broken")
	if headers["authorization"] != "Bearer token" || headers["x-device"] != "device-1" || len(headers) != 2 {
		t.Fatalf("unexpected OTLP headers: %#v", headers)
	}
}
