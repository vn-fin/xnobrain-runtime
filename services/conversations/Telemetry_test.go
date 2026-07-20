// <Summary>
// Telemetry tests prove Hermes usage is reduced to safe aggregate attributes
// and tool dependency spans without copying prompt or argument payloads.
// </Summary>
package conversations

import (
	"context"
	"testing"

	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func TestRunTelemetryAggregatesUsageAndCreatesToolSpan(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	previous := otel.GetTracerProvider()
	otel.SetTracerProvider(provider)
	t.Cleanup(func() {
		_ = provider.Shutdown(context.Background())
		otel.SetTracerProvider(previous)
	})

	ctx, root := otel.Tracer("test").Start(context.Background(), "agent-run")
	metrics := newRunTelemetry("agent-secret-id")
	metrics.Configure("Researcher", "openai", "gpt-5")
	metrics.Observe(ctx, runtimeadapter.Event{Type: "tool.completed", Payload: map[string]any{
		"tool_name": "browser.search",
		"arguments": map[string]any{"query": "must not be exported"},
		"result":    "Xin chào 🌏",
		"usage":     map[string]any{"input_tokens": float64(120), "output_tokens": float64(30), "cache_read_tokens": float64(20), "cache_write_tokens": float64(5), "cost_usd": 0.012},
	}})
	metrics.Apply(root)
	root.End()

	spans := recorder.Ended()
	if len(spans) != 2 {
		t.Fatalf("ended spans = %d", len(spans))
	}
	rootAttributes := attributeMap(spans[1].Attributes())
	if rootAttributes["gen_ai.usage.input_tokens"] != int64(120) || rootAttributes["gen_ai.usage.output_tokens"] != int64(30) || rootAttributes["gen_ai.usage.cached_tokens"] != int64(25) {
		t.Fatalf("usage attributes = %#v", rootAttributes)
	}
	toolAttributes := attributeMap(spans[0].Attributes())
	if toolAttributes["tool.name"] != "browser.search" || toolAttributes["lumora.node.kind"] != "tool" {
		t.Fatalf("tool attributes = %#v", toolAttributes)
	}
	if toolAttributes["lumora.response.chars"] != int64(10) {
		t.Fatalf("response chars = %#v", toolAttributes["lumora.response.chars"])
	}
	if _, leaked := toolAttributes["arguments"]; leaked {
		t.Fatal("tool arguments were exported")
	}
}

func attributeMap(values []attribute.KeyValue) map[string]any {
	result := make(map[string]any, len(values))
	for _, value := range values {
		result[string(value.Key)] = value.Value.AsInterface()
	}
	return result
}
