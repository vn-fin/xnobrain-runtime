// <Summary>
// Redaction enforces the telemetry allowlist at the final span-export boundary,
// hashes local identifiers, removes event payloads, and strips status messages.
// File: Redaction.go
// Functions: HashID, NewSafeExporter
// </Summary>
package tracing

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

type safeExporter struct {
	next sdktrace.SpanExporter
}

type safeSpan struct {
	sdktrace.ReadOnlySpan
}

func HashID(value string) string {
	hash := sha256.Sum256([]byte(value))
	return hex.EncodeToString(hash[:8])
}

func NewSafeExporter(next sdktrace.SpanExporter) sdktrace.SpanExporter {
	return &safeExporter{next: next}
}

func (e *safeExporter) ExportSpans(ctx context.Context, spans []sdktrace.ReadOnlySpan) error {
	redacted := make([]sdktrace.ReadOnlySpan, 0, len(spans))
	for _, span := range spans {
		redacted = append(redacted, safeSpan{ReadOnlySpan: span})
	}
	return e.next.ExportSpans(ctx, redacted)
}

func (e *safeExporter) Shutdown(ctx context.Context) error { return e.next.Shutdown(ctx) }

func (s safeSpan) Name() string {
	name := s.ReadOnlySpan.Name()
	if strings.Contains(name, "/") && !strings.Contains(name, ":") {
		return "HTTP request"
	}
	return name
}

func (s safeSpan) Attributes() []attribute.KeyValue {
	return safeAttributes(s.ReadOnlySpan.Attributes())
}

func (s safeSpan) Events() []sdktrace.Event {
	events := s.ReadOnlySpan.Events()
	result := make([]sdktrace.Event, 0, len(events))
	for _, event := range events {
		name := strings.ToLower(strings.TrimSpace(event.Name))
		if !strings.HasPrefix(name, "gen_ai.stream.") && !strings.HasPrefix(name, "lumora.approval.") && !strings.HasPrefix(name, "lumora.scheduler.") {
			continue
		}
		event.Attributes = safeAttributes(event.Attributes)
		result = append(result, event)
	}
	return result
}

func (s safeSpan) Links() []sdktrace.Link {
	links := s.ReadOnlySpan.Links()
	result := make([]sdktrace.Link, 0, len(links))
	for _, link := range links {
		link.Attributes = safeAttributes(link.Attributes)
		result = append(result, link)
	}
	return result
}

func (s safeSpan) Status() sdktrace.Status {
	status := s.ReadOnlySpan.Status()
	status.Description = ""
	return status
}

func (s safeSpan) Resource() *resource.Resource {
	current := s.ReadOnlySpan.Resource()
	if current == nil {
		return resource.Empty()
	}
	allowed := make([]attribute.KeyValue, 0)
	for _, item := range current.Attributes() {
		key := string(item.Key)
		if key == "service.name" || key == "service.namespace" || key == "service.version" || strings.HasPrefix(key, "telemetry.sdk.") {
			allowed = append(allowed, item)
		}
	}
	return resource.NewSchemaless(allowed...)
}

func safeAttributes(attributes []attribute.KeyValue) []attribute.KeyValue {
	result := make([]attribute.KeyValue, 0, len(attributes))
	for _, item := range attributes {
		key := string(item.Key)
		if allowedAttribute(key) {
			result = append(result, item)
		}
	}
	return result
}

func allowedAttribute(key string) bool {
	if key == "http.request.method" || key == "http.route" || key == "http.response.status_code" || key == "http.status_code" || key == "run.interactive" || key == "run.status" || key == "runtime.class" || key == "tool.category" || key == "error.code" || key == "error.type" || key == "agent.name" || key == "team.name" || key == "skill.name" || key == "tool.name" || key == "lumora.node.kind" || key == "lumora.node.label" || key == "lumora.response.chars" || key == "lumora.stream.time_to_first_token_ms" || key == "lumora.stream.duration_ms" || key == "lumora.stream.event_count" || key == "lumora.approval.decision" || key == "lumora.agent.ref" || key == "lumora.conversation.ref" || key == "lumora.session.ref" || key == "gen_ai.prompt.length" || key == "gen_ai.provider.name" || key == "gen_ai.request.model" || key == "gen_ai.response.model" || key == "gen_ai.usage.input_tokens" || key == "gen_ai.usage.output_tokens" || key == "gen_ai.usage.cached_tokens" || key == "gen_ai.usage.cache_read_tokens" || key == "gen_ai.usage.cache_write_tokens" || key == "gen_ai.usage.reasoning_tokens" || key == "gen_ai.usage.cost_usd" {
		return true
	}
	for _, prefix := range []string{"limits.", "quota."} {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	return strings.HasSuffix(key, ".id_hash")
}
